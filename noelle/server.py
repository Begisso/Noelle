"""aiohttp web server: REST API, WebSocket tag streaming, static web client."""
from __future__ import annotations

import asyncio
import datetime as dt
import io
import json
import logging
import os
import shutil
import subprocess
import sys
import time

from aiohttp import WSMsgType, web

from . import __version__, jsonutil
from .alarms import AlarmManager
from .datalog import DataLogger, Recipes
from .drivers import register_all
from .drivers.local import SysDriver
from .engine import GOOD, TagEngine
from .historian import Historian
from .project import ALIAS_COLUMNS, TAG_COLUMNS, Project, TagDatabase, parse_csv_text, write_csv
from .scripting import ScriptHost
from .security import Security, role_level

log = logging.getLogger("noelle.server")
WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")


class Session:
    """One websocket client. Tag updates are coalesced in ``pending`` and sent
    at most every ``flush_s`` seconds; a single pump task does all sends so a
    slow client never blocks the engine (it just receives fewer, newer frames)."""

    def __init__(self, server: "Server", ws, user, role, token):
        self.server, self.ws, self.user, self.role, self.token = server, ws, user, role, token
        self.pending: dict[int, tuple] = {}
        self.subs: dict[int, object] = {}
        self.outbox: list[str] = []
        self._scheduled = False
        self._pumping = False
        self.closed = False
        self.loop = asyncio.get_running_loop()

    def kick(self):
        if not self._scheduled:
            self._scheduled = True
            self.loop.call_later(self.server.flush_s, self._flush)

    def _flush(self):
        self._scheduled = False
        if self.closed or not self.pending:
            return
        if self._pumping and len(self.outbox) > 2:   # client is slow: keep coalescing
            self.kick()
            return
        d = [[cid, v, q, int(ts * 1000)] for cid, (v, q, ts) in self.pending.items()]
        self.pending.clear()
        self.send({"op": "v", "d": d})

    def send(self, msg: dict):
        if self.closed:
            return
        self.outbox.append(jsonutil.dumps(msg))
        if not self._pumping:
            self._pumping = True
            self.loop.create_task(self._pump())

    async def _pump(self):
        try:
            while self.outbox and not self.closed:
                batch, self.outbox = self.outbox, []
                for m in batch:
                    await self.ws.send_str(m)
        except Exception:
            self.closed = True
        finally:
            self._pumping = False

    def can(self, role: str) -> bool:
        return role_level(self.role) >= role_level(role)


class Server:
    def __init__(self, project_root: str, host: str | None = None, port: int | None = None):
        self.project = Project(project_root)
        cfg = self.project.cfg
        self.host = host or cfg.get("server", {}).get("host", "127.0.0.1")
        self.port = int(port or cfg.get("server", {}).get("port", 8800))
        self.flush_s = float(cfg.get("server", {}).get("flush_ms", 100)) / 1000
        self.engine = TagEngine()
        self.engine.configure_devices(cfg.get("devices", []))
        url = cfg.get("historian", {}).get("url", "")
        hcfg = cfg.get("historian", {})
        self.historian = Historian(url, cache_path=os.path.join(self.project.root, "runtime", "histcache.sqlite"),
                                   concurrency=int(hcfg.get("concurrency", 4))) if url else None
        register_all(self.engine, self.project, self.historian)
        self.tagdb = TagDatabase(self.engine, self.project)
        self.engine.tagdb = self.tagdb
        self.security = Security(self.project)
        self.alarms = AlarmManager(self.engine, self.project)
        self.datalog = DataLogger(self.engine, self.project)
        self.recipes = Recipes(self.engine, self.project)
        self.scripts = ScriptHost(self.engine, self.project, self.alarms, self.historian)
        self.sessions: set[Session] = set()
        self._alarm_pending = False
        self.engine.write_hooks.append(lambda user, ref, old, new, src: self.security.audit(user, "write", ref, old, new, src))
        self.alarms.listeners.append(self._alarms_changed)
        self.scripts.emitters.append(self._script_emit)
        self.project.listeners.append(self._file_changed)
        SysDriver.providers.update({
            "Clients": lambda: len(self.sessions),
            "AlarmsActive": lambda: self.alarms.counts()[0],
            "AlarmsUnacked": lambda: self.alarms.counts()[1],
            "Project": lambda: self.project.cfg.get("name", ""),
        })

    # ------------------------------------------------------------------ lifecycle
    async def on_startup(self, app):
        self.engine.loop = asyncio.get_running_loop()

        def quiet(loop, ctx):   # browsers closing sockets is normal; don't spam tracebacks
            if isinstance(ctx.get("exception"), (ConnectionResetError, ConnectionAbortedError)):
                return
            loop.default_exception_handler(ctx)
        self.engine.loop.set_exception_handler(quiet)
        self.tagdb.load()
        self.alarms.load()
        self.datalog.load()
        self.scripts.load_all()
        app["watch"] = asyncio.get_running_loop().create_task(self.project.watch())

    async def on_cleanup(self, app):
        app["watch"].cancel()
        self.scripts.stop()
        self.datalog.stop_logging()
        for s in list(self.sessions):
            s.closed = True
        await self.engine.stop()
        drv = self.engine.drivers.get("mem")
        if drv:
            await drv.stop()
        if self.historian:
            await self.historian.close()

    def app(self) -> web.Application:
        app = web.Application(client_max_size=64 * 1024 * 1024, middlewares=[self._errors])
        app.on_startup.append(self.on_startup)
        app.on_cleanup.append(self.on_cleanup)
        r = app.router
        r.add_get("/", self.index)
        r.add_get("/favicon.ico", lambda req: web.FileResponse(os.path.join(WEB, "img", "noelle-logo.svg"), headers={"Content-Type": "image/svg+xml"}))
        r.add_get("/ws", self.ws_handler)
        r.add_get("/api/project", self.api_project)
        r.add_put("/api/project/config", self.api_project_config)
        r.add_get("/api/file", self.api_file_get)
        r.add_put("/api/file", self.api_file_put)
        r.add_post("/api/file/delete", self.api_file_delete)
        r.add_post("/api/file/rename", self.api_file_rename)
        r.add_get("/api/file/history", self.api_file_history)
        r.add_get("/api/file/version", self.api_file_version)
        r.add_get("/api/tags", self.api_tags_get)
        r.add_put("/api/tags", self.api_tags_put)
        r.add_get("/api/tags.csv", self.api_tags_csv)
        r.add_post("/api/tags/import", self.api_tags_import)
        r.add_get("/api/aliases", self.api_aliases_get)
        r.add_put("/api/aliases", self.api_aliases_put)
        r.add_get("/api/browse", self.api_browse)
        r.add_post("/api/vscode", self.api_vscode)
        r.add_get("/api/hist/tree", self.api_hist_tree)
        r.add_get("/api/hist/search", self.api_hist_search)
        r.add_get("/api/hist/data", self.api_hist_data)
        r.add_get("/api/hist/export.csv", self.api_hist_export)
        r.add_get("/api/alarms", self.api_alarms)
        r.add_post("/api/alarms/ack", self.api_alarms_ack)
        r.add_get("/api/alarms/history", self.api_alarms_history)
        r.add_get("/api/recipes", self.api_recipes)
        r.add_get("/api/recipes/{name}", self.api_recipe_get)
        r.add_put("/api/recipes/{name}", self.api_recipe_put)
        r.add_post("/api/recipes/{name}/apply", self.api_recipe_apply)
        r.add_post("/api/recipes/{name}/snapshot", self.api_recipe_snapshot)
        r.add_post("/api/login", self.api_login)
        r.add_post("/api/logout", self.api_logout)
        r.add_get("/api/me", self.api_me)
        r.add_get("/api/users", self.api_users)
        r.add_put("/api/users", self.api_users_put)
        r.add_get("/api/status", self.api_status)
        r.add_get("/api/audit", self.api_audit)
        r.add_get("/api/log/query", self.api_log_query)
        r.add_post("/api/import/{kind}", self.api_import)
        r.add_get("/api/fs/list", self.api_fs_list)
        r.add_static("/web", WEB, show_index=False, append_version=False)
        r.add_get("/project/{path:.*}", self.project_static)
        return app

    @web.middleware
    async def _errors(self, request, handler):
        try:
            resp = await handler(request)
        except web.HTTPException:
            raise
        except PermissionError as e:
            return web.json_response({"error": str(e)}, status=403)
        except (FileNotFoundError, KeyError) as e:
            return web.json_response({"error": f"not found: {e}"}, status=404)
        except Exception as e:
            log.exception("API %s", request.path)
            return web.json_response({"error": f"{type(e).__name__}: {e}"}, status=500)
        if request.path.startswith("/web/") and isinstance(resp, web.FileResponse):
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    # ------------------------------------------------------------------ helpers
    def who(self, request):
        return self.security.user_for(request.cookies.get("gf_session"))

    def require(self, request, role):
        user, r = self.who(request)
        if role_level(r) < role_level(role):
            raise PermissionError(f"'{role}' role required (you are {user}/{r})")
        return user

    @staticmethod
    def json(obj, status=200):
        return web.Response(text=jsonutil.dumps(obj), content_type="application/json", status=status)

    # ------------------------------------------------------------------ static
    async def index(self, request):
        resp = web.FileResponse(os.path.join(WEB, "index.html"))
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    async def project_static(self, request):
        rel = request.match_info["path"]
        if not rel.startswith(("images/", "scripts/", "symbols/", "displays/")):
            raise web.HTTPForbidden()
        p = self.project.abspath(rel)
        if not os.path.isfile(p):
            raise web.HTTPNotFound()
        resp = web.FileResponse(p)
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    # ------------------------------------------------------------------ project / files
    async def api_project(self, request):
        user, role = self.who(request)
        p = self.project
        cfg = {k: v for k, v in p.cfg.items() if k not in ("security",)}
        return self.json({
            "version": __version__, "root": p.root, "cfg": cfg, "user": user, "role": role,
            "security": self.security.enabled,
            "displays": p.list_files("displays"), "symbols": p.list_files("symbols"),
            "scripts": p.list_files("scripts", (".py", ".js")),
            "images": p.list_files("images", (".png", ".jpg", ".jpeg", ".gif", ".svg", ".bmp", ".webp")),
            "aliases": p.aliases, "recipes": self.recipes.list(),
            "historian": bool(self.historian),
        })

    async def api_project_config(self, request):
        user = self.require(request, "engineer")
        cfg = await request.json()
        sec = self.project.cfg.get("security")
        self.project.cfg = {**cfg, "security": cfg.get("security", sec)}
        self.project.save_cfg()
        self.engine.configure_devices(self.project.cfg.get("devices", []))
        self.security.audit(user, "save", "project.nll", "", "")
        return self.json({"ok": True})

    async def api_file_get(self, request):
        rel = request.query["path"]
        return web.Response(text=self.project.read_text(rel), content_type="text/plain", charset="utf-8",
                            headers={"Cache-Control": "no-cache"})

    async def api_file_put(self, request):
        user = self.require(request, "engineer")
        rel = request.query["path"]
        text = await request.text()
        if rel.endswith(".nll"):
            json.loads(text)   # validate before writing
        self.project.write_text(rel, text)
        self.security.audit(user, "save", rel, "", f"{len(text)} bytes")
        if rel.startswith("scripts/") and rel.endswith(".py"):
            self.scripts.on_file_changed(rel)
        self.broadcast({"op": "fs", "path": rel, "by": user})
        return self.json({"ok": True})

    async def api_file_delete(self, request):
        user = self.require(request, "engineer")
        rel = (await request.json())["path"]
        self.project.delete(rel)
        self.security.audit(user, "delete", rel, "", "")
        self.broadcast({"op": "fs", "path": rel, "deleted": True})
        return self.json({"ok": True})

    async def api_file_rename(self, request):
        user = self.require(request, "engineer")
        b = await request.json()
        self.project.rename(b["path"], b["to"])
        self.security.audit(user, "rename", b["path"], "", b["to"])
        self.broadcast({"op": "fs", "path": b["to"]})
        return self.json({"ok": True})

    async def api_file_history(self, request):
        return self.json(self.project.history(request.query["path"]))

    async def api_file_version(self, request):
        rel, v = request.query["path"], request.query["v"]
        p = os.path.join(self.project.root, ".history", os.path.dirname(rel), f"{os.path.basename(rel)}.{v}")
        with open(p, encoding="utf-8-sig") as f:
            return web.Response(text=f.read(), content_type="text/plain", charset="utf-8")

    # ------------------------------------------------------------------ tags / aliases
    async def api_tags_get(self, request):
        return self.json({"columns": TAG_COLUMNS, "rows": self.project.tag_rows()})

    async def api_tags_put(self, request):
        user = self.require(request, "engineer")
        rows = (await request.json())["rows"]
        self.project.save_tag_rows(rows)
        self._reload_tags()
        self.security.audit(user, "save", "tags.csv", "", f"{len(rows)} tags")
        return self.json({"ok": True})

    async def api_tags_csv(self, request):
        buf = io.StringIO()
        import csv
        w = csv.DictWriter(buf, fieldnames=TAG_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in self.project.tag_rows():
            w.writerow(r)
        return web.Response(body=("﻿" + buf.getvalue()).encode("utf-8"), content_type="text/csv",
                            headers={"Content-Disposition": "attachment; filename=tags.csv"})

    async def api_tags_import(self, request):
        user = self.require(request, "engineer")
        mode = request.query.get("mode", "merge")
        new = [r for r in parse_csv_text(await request.text()) if r.get("Name")]
        rows = [] if mode == "replace" else self.project.tag_rows()
        idx = {r["Name"]: i for i, r in enumerate(rows)}
        for r in new:
            if r["Name"] in idx:
                rows[idx[r["Name"]]].update(r)
            else:
                idx[r["Name"]] = len(rows)
                rows.append(r)
        self.project.save_tag_rows(rows)
        self._reload_tags()
        self.security.audit(user, "import", "tags.csv", "", f"{len(new)} rows ({mode})")
        return self.json({"ok": True, "imported": len(new), "total": len(rows)})

    def _reload_tags(self):
        self.tagdb.load()
        self.alarms.load()
        self.datalog.load()

    async def api_aliases_get(self, request):
        return self.json({"columns": ALIAS_COLUMNS, "rows": self.project.alias_rows(), "sets": self.project.aliases})

    async def api_aliases_put(self, request):
        user = self.require(request, "engineer")
        rows = (await request.json())["rows"]
        self.project.save_alias_rows(rows)
        self.security.audit(user, "save", "aliases.csv", "", f"{len(rows)} rows")
        self.broadcast({"op": "aliases", "sets": self.project.aliases})
        return self.json({"ok": True})

    # ------------------------------------------------------------------ browse
    async def api_browse(self, request):
        src = request.query.get("src", "")
        path = request.query.get("path", "")
        if not src:
            out = [{"name": "Tag database", "src": "tags", "branch": True},
                   {"name": "Memory (mem:)", "src": "mem", "branch": True},
                   {"name": "System (sys:)", "src": "sys", "branch": True},
                   {"name": "Simulator (sim:)", "src": "sim", "branch": True}]
            if self.historian:
                out.append({"name": "Historian (hist:)", "src": "hist", "branch": True})
            for d in self.project.cfg.get("devices", []):
                out.append({"name": f"{d['name']} ({d.get('type')})", "src": d["name"], "branch": True})
            return self.json(out)
        if src == "tags":
            return self.json(await self.tagdb.browse(path))
        if src == "sim":
            from .drivers.local import SIM_FUNCS
            return self.json([{"name": n, "ref": f"sim:{n}"} for n in SIM_FUNCS])
        if src == "hist":
            if not path:
                return self.json([{"name": g, "path": g, "branch": True} for g in await self.historian.groups()])
            parts = path.split("\x1f")
            if len(parts) == 1:
                return self.json([{"name": s, "path": f"{path}\x1f{s}", "branch": True}
                                  for s in await self.historian.subgroups(parts[0])])
            return self.json([{"name": d, "ref": f"hist:{d}"} for d in await self.historian.descriptions(parts[0], parts[1])])
        if src in ("mem", "sys"):
            tag = self.engine.acquire(f"{src}:__browse__") if src not in self.engine.drivers else None
            drv = self.engine.drivers.get(src)
            res = await drv.browse(path) if drv else []
            if tag:
                self.engine.release(tag)
            return self.json([r for r in res if "__browse__" not in r.get("name", "")])
        # device driver
        cfg = self.project.cfg
        dev = next((d for d in cfg.get("devices", []) if d["name"] == src), None)
        if dev is None and not src.startswith(("opcda:[", "opcua:[")):
            raise KeyError(src)
        drv = self.engine.drivers.get(src)
        if drv is None:
            factory = self.engine.factories[dev["type"] if dev else src.split(":")[0]]
            drv = factory(self.engine, src, dev or {"direct": src[src.index("[") + 1:-1]})
            self.engine.drivers[src] = drv
            await drv.start()
        return self.json(await drv.browse(path))

    async def api_vscode(self, request):
        self.require(request, "engineer")
        rel = (await request.json()).get("path", "")
        target = self.project.abspath(rel) if rel else self.project.root
        code = shutil.which("code") or shutil.which("code.cmd")
        if not code:
            cand = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Microsoft VS Code", "bin", "code.cmd")
            code = cand if os.path.exists(cand) else None
        if not code:
            raise FileNotFoundError("VS Code 'code' command not found on PATH")
        args = [code, "-r", self.project.root] + (["-g", target] if rel else [])
        kw = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
        subprocess.Popen(args, shell=(sys.platform == "win32"), **kw)
        return self.json({"ok": True})

    # ------------------------------------------------------------------ historian
    def _hist(self):
        if not self.historian:
            raise FileNotFoundError("no historian configured (project.nll: historian.url)")
        return self.historian

    async def api_hist_tree(self, request):
        h = self._hist()
        g, s = request.query.get("group"), request.query.get("subgroup")
        if g is None:
            return self.json({"groups": await h.groups()})
        if s is None:
            return self.json({"subgroups": await h.subgroups(g)})
        return self.json({"descriptions": await h.descriptions(g, s)})

    async def api_hist_search(self, request):
        return self.json(await self._hist().search(request.query.get("q", ""), int(request.query.get("limit", 500))))

    async def api_hist_data(self, request):
        q = request.query
        descs = q.getall("d")
        t0, t1 = float(q["t0"]) / 1000, float(q["t1"]) / 1000
        px = min(8000, int(q.get("px", 1500)))
        t = time.perf_counter()
        data = await self._hist().data(descs, t0, t1, px)
        return self.json({"series": data, "ms": round((time.perf_counter() - t) * 1000, 1)})

    async def api_hist_export(self, request):
        q = request.query
        descs = q.getall("d")
        t0, t1 = float(q["t0"]) / 1000, float(q["t1"]) / 1000
        data = await self._hist().data(descs, t0, t1, raw=True)
        sep = q.get("sep", ";")
        lines = [sep.join(["Description", "Time", "Value"])]
        for d, s in data.items():
            for t, v in zip(s["t"], s["v"]):
                ts = dt.datetime.fromtimestamp(t / 1000).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                val = "" if v is None else (str(v).replace(".", ",") if sep == ";" else str(v))
                lines.append(sep.join([f'"{d}"', ts, val]))
        return web.Response(body=("﻿" + "\r\n".join(lines)).encode("utf-8"), content_type="text/csv",
                            headers={"Content-Disposition": "attachment; filename=historian_export.csv"})

    # ------------------------------------------------------------------ alarms
    async def api_alarms(self, request):
        act, unack = self.alarms.counts()
        return self.json({"list": self.alarms.current(), "active": act, "unack": unack})

    async def api_alarms_ack(self, request):
        user = self.require(request, "operator")
        ids = (await request.json()).get("ids")
        return self.json({"acked": self.alarms.ack(ids, user)})

    async def api_alarms_history(self, request):
        day = request.query.get("day", time.strftime("%Y-%m-%d"))
        return self.json(self.alarms.history_rows(day))

    def _alarms_changed(self):
        if self._alarm_pending:
            return
        self._alarm_pending = True

        def send():
            self._alarm_pending = False
            act, unack = self.alarms.counts()
            self.broadcast({"op": "alarms", "list": self.alarms.current(), "active": act, "unack": unack})
        asyncio.get_running_loop().call_later(0.15, send)

    # ------------------------------------------------------------------ recipes
    async def api_recipes(self, request):
        return self.json(self.recipes.list())

    async def api_recipe_get(self, request):
        return self.json(self.recipes.get(request.match_info["name"]))

    async def api_recipe_put(self, request):
        user = self.require(request, "engineer")
        name = request.match_info["name"]
        self.recipes.save(name, (await request.json())["rows"])
        self.security.audit(user, "save", f"recipes/{name}.csv", "", "")
        return self.json({"ok": True})

    async def api_recipe_apply(self, request):
        user = self.require(request, "operator")
        name = request.match_info["name"]
        self.security.audit(user, "recipe-apply", name, "", "")
        return self.json(await self.recipes.apply(name, user))

    async def api_recipe_snapshot(self, request):
        user = self.require(request, "operator")
        name = request.match_info["name"]
        refs = (await request.json()).get("refs") or [r["Tag"] for r in self.recipes.get(name)]
        rows = await self.recipes.snapshot(name, refs)
        self.security.audit(user, "recipe-save", name, "", f"{len(rows)} values")
        return self.json(rows)

    # ------------------------------------------------------------------ security
    async def api_login(self, request):
        b = await request.json()
        token = self.security.login(b.get("user", ""), b.get("password", ""))
        if not token:
            return self.json({"ok": False, "error": "invalid user or password"}, 401)
        resp = self.json({"ok": True, "user": b["user"], "role": self.security.user_for(token)[1]})
        resp.set_cookie("gf_session", token, httponly=True, samesite="Strict")
        return resp

    async def api_logout(self, request):
        self.security.logout(request.cookies.get("gf_session"))
        resp = self.json({"ok": True})
        resp.del_cookie("gf_session")
        return resp

    async def api_me(self, request):
        user, role = self.who(request)
        return self.json({"user": user, "role": role, "security": self.security.enabled})

    async def api_users(self, request):
        self.require(request, "admin")
        return self.json([{"name": u["name"], "role": u.get("role")} for u in self.security.users.values()])

    async def api_users_put(self, request):
        user = self.require(request, "admin")
        b = await request.json()
        if b.get("delete"):
            self.security.delete_user(b["name"])
        else:
            self.security.set_user(b["name"], b.get("role", "viewer"), b.get("password"))
        self.security.audit(user, "user", b["name"], "", b.get("role", "deleted"))
        return self.json({"ok": True})

    async def api_audit(self, request):
        self.require(request, "engineer")
        day = request.query.get("day", time.strftime("%Y-%m-%d"))
        p = os.path.join(self.security.audit_dir, f"{day}.csv")
        import csv
        rows = list(csv.DictReader(open(p, encoding="utf-8-sig"))) if os.path.exists(p) else []
        return self.json(rows[-5000:])

    # ------------------------------------------------------------------ status / logs
    async def api_status(self, request):
        st = self.engine.status()
        st["clients"] = len(self.sessions)
        st["historian"] = self.historian.status() if self.historian else None
        st["scripts"] = {"loaded": [os.path.basename(p) for p in self.scripts.contexts],
                         "functions": sorted(self.scripts.functions), "errors": self.scripts.errors}
        return self.json(st)

    async def api_log_query(self, request):
        q = request.query
        ts, vs = self.datalog.query(q["ref"], float(q["t0"]) / 1000, float(q["t1"]) / 1000)
        return self.json({"t": [round(t * 1000) for t in ts], "v": vs})

    async def api_fs_list(self, request):
        """Server side file browser for the import page (the server runs on the engineer's PC;
        a browser page cannot see full local paths)."""
        self.require(request, "engineer")
        path = request.query.get("path", "").strip()
        exts = tuple(e.strip().lower() for e in request.query.get("ext", "").split(",") if e.strip())
        if not path:
            places = []
            if sys.platform == "win32":
                import string
                for d in string.ascii_uppercase:
                    if os.path.exists(f"{d}:\\"):
                        places.append({"name": f"{d}:\\", "path": f"{d}:\\", "kind": "drive"})
            else:
                places.append({"name": "/", "path": "/", "kind": "drive"})
            home = os.path.expanduser("~")
            for name, p in (("Home", home), ("Desktop", os.path.join(home, "Desktop")), ("Documents", os.path.join(home, "Documents")),
                            ("This project", self.project.root)):
                if os.path.isdir(p):
                    places.append({"name": name, "path": p, "kind": "place"})
            return self.json({"path": "", "parent": None, "dirs": places, "files": []})
        path = os.path.abspath(path)
        if not os.path.isdir(path):
            raise FileNotFoundError(path)
        dirs, files = [], []
        try:
            entries = list(os.scandir(path))
        except PermissionError:
            raise PermissionError(f"no access to {path}")
        for e in sorted(entries, key=lambda e: e.name.lower()):
            if e.name.startswith(("$", ".")):
                continue
            try:
                if e.is_dir():
                    dirs.append({"name": e.name, "path": e.path})
                elif not exts or e.name.lower().endswith(exts):
                    st = e.stat()
                    files.append({"name": e.name, "path": e.path, "size": st.st_size, "mtime": st.st_mtime})
            except OSError:
                continue
            if len(dirs) + len(files) > 5000:
                break
        parent = os.path.dirname(path.rstrip("\/")) if os.path.dirname(path.rstrip("\/")) != path else None
        if parent and not parent.endswith(("\\", "/")) and len(parent) == 2 and parent[1] == ":":
            parent += "\\"
        return self.json({"path": path, "parent": parent or "", "dirs": dirs, "files": files})

    async def api_import(self, request):
        user = self.require(request, "engineer")
        kind = request.match_info["kind"]
        from .importers import run_import
        params = await request.json()
        report = await asyncio.get_running_loop().run_in_executor(None, run_import, kind, self.project, params)
        self.security.audit(user, "import", kind, "", report.get("summary", ""))
        self._reload_tags()
        self.broadcast({"op": "fs", "path": "displays/"})
        return self.json(report)

    # ------------------------------------------------------------------ websocket
    def broadcast(self, msg: dict):
        for s in list(self.sessions):
            s.send(msg)

    def _script_emit(self, kind, *args):
        if kind == "log":
            self.broadcast({"op": "log", "msg": args[0]})
        elif kind == "toast":
            self.broadcast({"op": "toast", "msg": args[0], "level": args[1] if len(args) > 1 else "info"})

    async def _file_changed(self, rel: str):
        if rel in ("tags.csv",):
            self._reload_tags()
        elif rel == "aliases.csv":
            self.project.load_aliases()
            self.broadcast({"op": "aliases", "sets": self.project.aliases})
        elif rel == "project.nll":
            self.project.load()
            self.engine.configure_devices(self.project.cfg.get("devices", []))
        self.scripts.on_file_changed(rel)
        self.broadcast({"op": "fs", "path": rel, "external": True})

    async def ws_handler(self, request):
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=32 * 1024 * 1024, compress=True)
        await ws.prepare(request)
        token = request.cookies.get("gf_session")
        user, role = self.security.user_for(token)
        sess = Session(self, ws, user, role, token)
        self.sessions.add(sess)
        act, unack = self.alarms.counts()
        sess.send({"op": "welcome", "user": user, "role": role, "aliases": self.project.aliases,
                   "security": self.security.enabled, "version": __version__})
        sess.send({"op": "alarms", "list": self.alarms.current(), "active": act, "unack": unack})
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    if msg.type == WSMsgType.ERROR:
                        break
                    continue
                try:
                    m = jsonutil.loads(msg.data)
                    await self._ws_msg(sess, m)
                except Exception as e:
                    log.exception("ws message")
                    sess.send({"op": "error", "error": str(e)})
        finally:
            sess.closed = True
            self.sessions.discard(sess)
            for cid, tag in list(sess.subs.items()):
                tag.subs.pop(sess, None)
                self.engine.release(tag)
            sess.subs.clear()
        return ws

    async def _ws_msg(self, sess: Session, m: dict):
        op = m.get("op")
        eng = self.engine
        if op == "sub":
            meta = []
            for item in m["items"]:
                cid, ref = item[0], item[1]
                flags = item[2] if len(item) > 2 else ""
                old = sess.subs.get(cid)
                if old is not None:
                    old.subs.pop(sess, None)
                    eng.release(old)
                tag = eng.acquire(ref)
                tag.subs[sess] = cid
                sess.subs[cid] = tag
                if "h" in flags:
                    eng.enable_history(tag)
                if tag.value is not None or tag.quality != 8:
                    sess.pending[cid] = (tag.value, tag.quality, tag.ts or time.time())
                if tag.meta or tag.error:
                    meta.append([cid, {**tag.meta, "err": tag.error}])
            if meta:
                sess.send({"op": "m", "d": meta})
            sess.kick()
        elif op == "unsub":
            for cid in m["ids"]:
                tag = sess.subs.pop(cid, None)
                if tag is not None:
                    tag.subs.pop(sess, None)
                    eng.release(tag)
        elif op == "write":
            rid = m.get("rid")
            if not sess.can("operator"):
                sess.send({"op": "res", "rid": rid, "ok": False, "error": "operator role required"})
                return
            try:
                await eng.write(m["ref"], m.get("value"), user=sess.user, source="ui")
                sess.send({"op": "res", "rid": rid, "ok": True})
            except Exception as e:
                sess.send({"op": "res", "rid": rid, "ok": False, "error": f"{e}"})
        elif op == "rpc":
            rid = m.get("rid")
            try:
                res = await self._rpc(sess, m.get("fn"), m.get("args") or {})
                sess.send({"op": "res", "rid": rid, "ok": True, "result": res})
            except Exception as e:
                sess.send({"op": "res", "rid": rid, "ok": False, "error": f"{type(e).__name__}: {e}"})
        elif op == "ping":
            sess.send({"op": "pong", "t": m.get("t")})

    async def _rpc(self, sess: Session, fn: str, a: dict):
        eng = self.engine
        if fn == "history":
            tag = eng.tags.get(a["ref"])
            if tag is None or tag.history is None:
                return {"t": [], "v": []}
            since = time.time() - float(a.get("seconds", 600))
            pts = [(t, v) for t, v in tag.history if t >= since]
            return {"t": [round(t * 1000) for t, _ in pts], "v": [v for _, v in pts]}
        if fn == "info":
            tag = eng.get(a["ref"])
            drv = tag.driver
            return {"ref": tag.ref, "value": tag.value, "quality": tag.quality, "ts": tag.ts, "error": tag.error,
                    "meta": tag.meta, "driver": drv.status() if drv and hasattr(drv, "status") else None}
        if fn == "py":
            if not sess.can("operator"):
                raise PermissionError("operator role required")
            self.security.audit(sess.user, "script-call", a["name"], "", jsonutil.dumps(a.get("args")))
            return await self.scripts.call(a["name"], a.get("args"))
        if fn == "ack":
            if not sess.can("operator"):
                raise PermissionError("operator role required")
            return self.alarms.ack(a.get("ids"), sess.user)
        if fn == "recipe.apply":
            if not sess.can("operator"):
                raise PermissionError("operator role required")
            return await self.recipes.apply(a["name"], sess.user)
        if fn == "status":
            return self.engine.status()
        raise KeyError(f"unknown rpc '{fn}'")


def run(project_root: str, host=None, port=None, open_browser=True, kiosk=False, exit_when_idle=False,
        start_query: str = "", info_file: str | None = None):
    """Run the server until Ctrl+C - or, in app mode (exit_when_idle), until the last
    window has been closed for 20 s."""
    srv = Server(project_root, host, port)
    app = srv.app()
    url = f"http://{'127.0.0.1' if srv.host in ('0.0.0.0', '::') else srv.host}:{srv.port}/"
    os.environ["GF_SERVER_URL"] = url.rstrip("/")   # used by importers (visual identity)
    if kiosk:
        start_query = (start_query + "&" if start_query else "") + "mode=run&kiosk=1"
    full = url + ("?" + start_query if start_query else "")
    print(f"Noelle HMI {__version__}  project: {srv.project.root}")
    print(f"  open {full}")

    async def main():
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, srv.host, srv.port)
        await site.start()
        if info_file:
            with open(info_file, "w", encoding="utf-8") as f:
                json.dump({"pid": os.getpid(), "port": srv.port, "url": url}, f)
        if open_browser:
            asyncio.get_running_loop().call_later(0.2, _open_app_window, full)
        stop = asyncio.Event()
        if exit_when_idle:
            async def idle_watch():
                seen, idle = False, 0.0
                while True:
                    await asyncio.sleep(1.0)
                    if srv.sessions:
                        seen, idle = True, 0.0
                    elif seen or idle > 120:      # never connected within 2 min -> also quit
                        idle += 1.0
                        if (seen and idle >= 20) or idle > 120:
                            stop.set()
                            return
                    else:
                        idle += 1.0
            asyncio.get_running_loop().create_task(idle_watch())
        try:
            await stop.wait()
        finally:
            await runner.cleanup()
            if info_file:
                try:
                    os.remove(info_file)
                except OSError:
                    pass

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass


def _open_app_window(url: str):
    edge = [os.path.join(os.environ.get(k, ""), "Microsoft", "Edge", "Application", "msedge.exe")
            for k in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
    chrome = [os.path.join(os.environ.get(k, ""), "Google", "Chrome", "Application", "chrome.exe")
              for k in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
    for exe in edge + chrome:
        if exe and os.path.exists(exe):
            subprocess.Popen([exe, f"--app={url}", "--window-size=1600,950"])
            return
    import webbrowser
    webbrowser.open(url)

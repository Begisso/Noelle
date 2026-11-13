"""Project: project.nll, tags.csv (tag database), aliases.csv, files, watcher.

Project folder layout::

    project.nll        project settings + devices (JSON)
    tags.csv           tag database (Name, Address, scaling, units, alarms, logging)
    aliases.csv        global alias sets (Set, Alias, Value)
    displays/*.nll     displays (JSON, readable in Notepad / VS Code)
    symbols/*.nll      reusable symbols / faceplates (displays with <#aliases#>)
    scripts/*.py       server-side Python scripts (hot reloaded)
    scripts/*.js       display scripts (ES modules, run in the browser)
    images/            pictures used by displays
    recipes/*.csv      recipes (Tag, Value)
    logs/              data logger, alarm history, audit trail (CSV)
    .history/          automatic backups of every saved file
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import math
import os
import re
import shutil
import time
from typing import Any

from .address import RefError
from .engine import BAD_CONFIG, GOOD, Tag, TagEngine, to_number

log = logging.getLogger("noelle.project")

DEFAULT_PROJECT = {
    "format": "noelle-project",
    "version": 1,
    "name": "New Project",
    "startDisplay": "displays/Main.nll",
    "server": {"host": "127.0.0.1", "port": 8800, "flush_ms": 100},
    "historian": {"url": ""},
    "devices": [],
    "security": {"enabled": False},
    "logger": {"enabled": True},
}

TAG_COLUMNS = ["Name", "Address", "Description", "Units", "Format", "RawLo", "RawHi", "EngLo", "EngHi",
               "Deadband", "ReadOnly", "Default", "Log", "HiHi", "Hi", "Lo", "LoLo", "AlarmDeadband",
               "Digital", "Priority", "Message", "Group"]
ALIAS_COLUMNS = ["Set", "Alias", "Value"]


# --------------------------------------------------------------------------- CSV helpers
def read_csv(path: str) -> list[dict]:
    """Read CSV written by Excel (any locale: ',' or ';' delimiter, BOM, cp1251)."""
    if not os.path.exists(path):
        return []
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "cp1251", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    return parse_csv_text(text)


def parse_csv_text(text: str) -> list[dict]:
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    if not lines:
        return []
    head = lines[0]
    delim = ";" if head.count(";") > head.count(",") else ("\t" if head.count("\t") > head.count(",") else ",")
    rdr = csv.DictReader(io.StringIO("\n".join(lines)), delimiter=delim)
    rows = []
    for r in rdr:
        rows.append({(k or "").strip(): (v or "").strip() for k, v in r.items() if k is not None})
    return rows


def write_csv(path: str, rows: list[dict], columns: list[str]):
    cols = list(columns)
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})
    os.replace(tmp, path)


def num(s, default=None):
    if s is None:
        return default
    if isinstance(s, (int, float)):
        return s
    s = str(s).strip().replace(",", ".")
    if s == "":
        return default
    try:
        return float(s)
    except ValueError:
        return default


def truthy(s) -> bool:
    return str(s).strip().lower() in ("1", "true", "yes", "y", "x", "да")


# --------------------------------------------------------------------------- expressions
_PLACE = re.compile(r"\{\{(.+?)\}\}")
SAFE = {
    "abs": abs, "min": min, "max": max, "round": round, "int": int, "float": float, "bool": bool,
    "str": str, "len": len, "sum": sum, "pow": pow,
    "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan, "log": math.log,
    "log10": math.log10, "exp": math.exp, "floor": math.floor, "ceil": math.ceil, "pi": math.pi,
    "iif": lambda c, a, b: a if c else b,
    "bit": lambda v, n: (int(v or 0) >> int(n)) & 1,
    "scale": lambda v, a, b, c, d: c + (float(v) - a) * (d - c) / ((b - a) or 1),
    "clamp": lambda v, lo, hi: max(lo, min(hi, v)),
}


class Expression:
    """Server side expression: Python syntax with {{ref}} placeholders."""

    def __init__(self, text: str):
        self.text = text
        self.refs: list[str] = []

        def sub(m):
            ref = m.group(1).strip()
            if ref not in self.refs:
                self.refs.append(ref)
            return f"_v[{self.refs.index(ref)}]"

        src = _PLACE.sub(sub, text)
        src = src.replace("&&", " and ").replace("||", " or ")
        src = re.sub(r"!(?!=)", " not ", src)
        self.code = compile(src, f"<expr {text}>", "eval")

    def eval(self, values: list):
        return eval(self.code, {"__builtins__": {}, **SAFE}, {"_v": values})


# --------------------------------------------------------------------------- tag database
class TagDatabase:
    """Pseudo-driver for named tags from tags.csv (scaling, expressions, memory)."""
    kind = "tagdb"

    def __init__(self, engine: TagEngine, project: "Project"):
        self.engine = engine
        self.project = project
        self.rows: dict[str, dict] = {}
        self.tags: set[Tag] = set()
        self.links: dict[Tag, list] = {}     # db tag -> [(source tag, listener)]
        self.memory: dict[str, Any] = {}
        self.connected = True
        self.last_error = ""

    def load(self):
        rows = read_csv(os.path.join(self.project.root, "tags.csv"))
        self.rows = {r["Name"]: r for r in rows if r.get("Name")}
        # re-attach live tags whose definition changed
        for t in list(self.tags):
            self.remove(t)
            try:
                self.add(t)
            except Exception as e:
                t.error = str(e)
                self.engine.update(t, None, BAD_CONFIG)

    def row(self, name):
        return self.rows.get(name)

    def meta_for(self, r: dict) -> dict:
        return {
            "description": r.get("Description", ""), "units": r.get("Units", ""), "format": r.get("Format", ""),
            "readonly": truthy(r.get("ReadOnly", "")), "min": num(r.get("EngLo")), "max": num(r.get("EngHi")),
        }

    def add(self, tag: Tag):
        name = tag.parsed.address
        r = self.rows.get(name)
        if r is None:
            raise RefError(f"unknown tag '{name}' (not in tags.csv)")
        tag.meta = self.meta_for(r)
        tag.deadband = num(r.get("Deadband"), 0.0) or 0.0
        self.tags.add(tag)
        addr = (r.get("Address") or "").strip()
        raw_lo, raw_hi, eng_lo, eng_hi = (num(r.get(k)) for k in ("RawLo", "RawHi", "EngLo", "EngHi"))
        scaled = None not in (raw_lo, raw_hi, eng_lo, eng_hi) and raw_hi != raw_lo
        if not addr:   # memory tag
            if name not in self.memory:
                d = r.get("Default", "")
                self.memory[name] = num(d, d) if d != "" else 0
            tag.item = ("mem", None)
            self.engine.update(tag, self.memory[name], GOOD, force=True)
            return
        if addr.startswith("="):   # expression tag
            expr = Expression(addr[1:])
            srcs = [self.engine.acquire(ref) for ref in expr.refs]

            def recompute(_t=None, _old=None, tag=tag, expr=expr, srcs=srcs):
                if any(s.quality < GOOD for s in srcs):
                    self.engine.update(tag, tag.value, min(s.quality for s in srcs))
                    return
                try:
                    self.engine.update(tag, expr.eval([s.value for s in srcs]), GOOD)
                except Exception as e:
                    tag.error = f"expression: {e}"
                    self.engine.update(tag, None, BAD_CONFIG)

            for s in srcs:
                s.listeners.append(recompute)
            self.links[tag] = [(s, recompute) for s in srcs]
            tag.item = ("expr", expr)
            tag.meta["readonly"] = True
            recompute()
            return
        src = self.engine.acquire(addr)

        def follow(s, _old, tag=tag):
            v = s.value
            if scaled and isinstance(v, (int, float)) and not isinstance(v, bool):
                v = eng_lo + (v - raw_lo) * (eng_hi - eng_lo) / (raw_hi - raw_lo)
                v = round(v, 6)
            tag.error = s.error
            self.engine.update(tag, v, s.quality, s.ts)

        src.listeners.append(follow)
        self.links[tag] = [(src, follow)]
        tag.item = ("link", src, scaled, (raw_lo, raw_hi, eng_lo, eng_hi))
        follow(src, None)

    def remove(self, tag: Tag):
        self.tags.discard(tag)
        for src, fn in self.links.pop(tag, []):
            if fn in src.listeners:
                src.listeners.remove(fn)
            self.engine.release(src)

    async def write(self, tag: Tag, value):
        kind = tag.item[0]
        if kind == "mem":
            name = tag.parsed.address
            self.memory[name] = value
            self.engine.update(tag, value, GOOD)
            return
        if kind == "expr":
            raise PermissionError("expression tags are read-only")
        _, src, scaled, (raw_lo, raw_hi, eng_lo, eng_hi) = tag.item
        if scaled and not isinstance(value, (str, bool)):
            value = raw_lo + (to_number(value) - eng_lo) * (raw_hi - raw_lo) / (eng_hi - eng_lo)
        await self.engine.write(src.ref, value, source=f"tag:{tag.parsed.address}")

    async def browse(self, path=""):
        groups: dict[str, list] = {}
        for name, r in sorted(self.rows.items()):
            groups.setdefault(r.get("Group") or "", []).append(name)
        if not path:
            out = [{"name": g, "path": g, "branch": True} for g in sorted(groups) if g]
            out += [{"name": n, "ref": n, "desc": self.rows[n].get("Description", "")} for n in groups.get("", [])]
            return out
        return [{"name": n, "ref": n, "desc": self.rows[n].get("Description", "")} for n in groups.get(path, [])]

    async def start(self):
        pass

    async def stop(self):
        pass

    def status(self):
        return {"kind": "tagdb", "connected": True, "tags": len(self.tags), "defined": len(self.rows)}


# --------------------------------------------------------------------------- project
class Project:
    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        self.cfg: dict = {}
        self.aliases: dict[str, dict[str, str]] = {}
        self._own_writes: dict[str, float] = {}
        self.listeners = []   # fn(relpath) on external file change
        self.load()

    # ---------------------------------------------------------------- loading
    def load(self):
        if not os.path.exists(os.path.join(self.root, "project.nll")) and os.path.exists(os.path.join(self.root, "project.gfp")):
            migrate_legacy(self.root)
        p = os.path.join(self.root, "project.nll")
        if os.path.exists(p):
            with open(p, encoding="utf-8-sig") as f:
                self.cfg = {**DEFAULT_PROJECT, **json.load(f)}
        else:
            self.cfg = json.loads(json.dumps(DEFAULT_PROJECT))
        self.load_aliases()

    def load_aliases(self):
        sets: dict[str, dict[str, str]] = {}
        for r in read_csv(os.path.join(self.root, "aliases.csv")):
            if r.get("Alias"):
                sets.setdefault(r.get("Set") or "Default", {})[r["Alias"]] = r.get("Value", "")
        self.aliases = sets or {"Default": {}}

    def save_cfg(self):
        self.write_text("project.nll", json.dumps(self.cfg, ensure_ascii=False, indent=2) + "\n")

    # ---------------------------------------------------------------- files
    def abspath(self, rel: str) -> str:
        rel = rel.replace("\\", "/").lstrip("/")
        p = os.path.abspath(os.path.join(self.root, rel))
        if os.path.commonpath([p, self.root]) != self.root:
            raise PermissionError("path outside project")
        return p

    def list_files(self, folder: str, exts=(".nll",)) -> list[dict]:
        base = self.abspath(folder)
        out = []
        if not os.path.isdir(base):
            return out
        for dirpath, _dirs, files in os.walk(base):
            if ".history" in dirpath:
                continue
            for fn in files:
                if fn.lower().endswith(tuple(exts)):
                    full = os.path.join(dirpath, fn)
                    out.append({"path": os.path.relpath(full, self.root).replace("\\", "/"),
                                "name": os.path.splitext(fn)[0], "mtime": os.path.getmtime(full),
                                "size": os.path.getsize(full)})
        return sorted(out, key=lambda x: x["path"].lower())

    def read_text(self, rel: str) -> str:
        with open(self.abspath(rel), encoding="utf-8-sig") as f:
            return f.read()

    def write_text(self, rel: str, text: str, backup: bool = True):
        p = self.abspath(rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if backup and os.path.exists(p):
            self._backup(rel, p)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, p)
        self._own_writes[p] = os.path.getmtime(p)

    def _backup(self, rel, p, keep=20):
        hdir = os.path.join(self.root, ".history", os.path.dirname(rel))
        os.makedirs(hdir, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        base = os.path.basename(p)
        shutil.copy2(p, os.path.join(hdir, f"{base}.{stamp}"))
        olds = sorted(f for f in os.listdir(hdir) if f.startswith(base + "."))
        for f in olds[:-keep]:
            try:
                os.remove(os.path.join(hdir, f))
            except OSError:
                pass

    def history(self, rel: str) -> list[str]:
        hdir = os.path.join(self.root, ".history", os.path.dirname(rel))
        base = os.path.basename(rel)
        if not os.path.isdir(hdir):
            return []
        return sorted((f[len(base) + 1:] for f in os.listdir(hdir) if f.startswith(base + ".")), reverse=True)

    def delete(self, rel: str):
        p = self.abspath(rel)
        self._backup(rel, p)
        os.remove(p)

    def rename(self, rel: str, new_rel: str):
        a, b = self.abspath(rel), self.abspath(new_rel)
        if os.path.exists(b):
            raise FileExistsError(new_rel)
        os.makedirs(os.path.dirname(b), exist_ok=True)
        os.replace(a, b)

    # ---------------------------------------------------------------- tags/aliases CSV
    def tag_rows(self) -> list[dict]:
        return read_csv(os.path.join(self.root, "tags.csv"))

    def save_tag_rows(self, rows: list[dict]):
        p = os.path.join(self.root, "tags.csv")
        if os.path.exists(p):
            self._backup("tags.csv", p)
        write_csv(p, rows, TAG_COLUMNS)
        self._own_writes[p] = os.path.getmtime(p)

    def alias_rows(self) -> list[dict]:
        return read_csv(os.path.join(self.root, "aliases.csv"))

    def save_alias_rows(self, rows: list[dict]):
        p = os.path.join(self.root, "aliases.csv")
        if os.path.exists(p):
            self._backup("aliases.csv", p)
        write_csv(p, rows, ALIAS_COLUMNS)
        self._own_writes[p] = os.path.getmtime(p)
        self.load_aliases()

    # ---------------------------------------------------------------- watcher
    async def watch(self, interval: float = 0.75):
        """Poll project files (cheap: only stat calls) and notify on external edits."""
        watched = ("displays", "symbols", "scripts", "images", "recipes")
        mt: dict[str, float] = {}

        def scan():
            cur = {}
            for f in ("project.nll", "tags.csv", "aliases.csv"):
                p = os.path.join(self.root, f)
                if os.path.exists(p):
                    cur[p] = os.path.getmtime(p)
            for d in watched:
                base = os.path.join(self.root, d)
                for dirpath, _dirs, files in os.walk(base):
                    for fn in files:
                        if not fn.endswith(".tmp"):
                            p = os.path.join(dirpath, fn)
                            try:
                                cur[p] = os.path.getmtime(p)
                            except OSError:
                                pass
            return cur

        mt = scan()
        while True:
            await asyncio.sleep(interval)
            try:
                cur = await asyncio.get_running_loop().run_in_executor(None, scan)
            except Exception:
                continue
            changed = [p for p, m in cur.items() if mt.get(p) != m] + [p for p in mt if p not in cur]
            mt = cur
            for p in changed:
                own = self._own_writes.get(p)
                if own is not None and own == cur.get(p):
                    continue
                rel = os.path.relpath(p, self.root).replace("\\", "/")
                for fn in self.listeners:
                    try:
                        res = fn(rel)
                        if asyncio.iscoroutine(res):
                            await res
                    except Exception:
                        log.exception("watch listener")


LEGACY_EXT = ".gfd"   # displays of GraphForge, the former name of Noelle


def migrate_legacy(root: str):
    """Upgrade a project from the old file names (project.gfp, *.gfd) to Noelle's .nll.
    Files are renamed, references inside them rewritten; originals go to .history/legacy."""
    import re as _re
    backup = os.path.join(root, ".history", "legacy")
    os.makedirs(backup, exist_ok=True)
    renamed = []
    for dirpath, dirs, files in os.walk(root):
        if ".history" in dirpath or os.sep + "runtime" in dirpath:
            continue
        for fn in files:
            if fn.endswith(LEGACY_EXT) or fn == "project.gfp":
                src = os.path.join(dirpath, fn)
                rel = os.path.relpath(src, root)
                dst_dir = os.path.join(backup, os.path.dirname(rel))
                os.makedirs(dst_dir, exist_ok=True)
                shutil.copy2(src, os.path.join(dst_dir, fn))
                dst = src[: -len(".gfp")] + ".nll" if fn == "project.gfp" else src[: -len(LEGACY_EXT)] + ".nll"
                renamed.append(dst)
                os.replace(src, dst)
    fix = lambda t: _re.sub(r"\.gfd\b", ".nll", t).replace("graphforge-display", "noelle-display").replace("graphforge-project", "noelle-project").replace("project.gfp", "project.nll")
    for dirpath, dirs, files in os.walk(root):
        if ".history" in dirpath:
            continue
        for fn in files:
            if fn.endswith((".nll", ".js", ".py", ".json")) and (".vscode" in dirpath or fn.endswith(".nll") or "scripts" in dirpath):
                p = os.path.join(dirpath, fn)
                try:
                    t = open(p, encoding="utf-8-sig").read()
                except (UnicodeDecodeError, OSError):
                    continue
                t2 = fix(t).replace("gfd.schema.json", "nll.schema.json").replace("gfp.schema.json", "nll.schema.json").replace('"*.gfd"', '"*.nll"').replace('"*.gfp"', '"*.nll"')
                if t2 != t:
                    with open(p, "w", encoding="utf-8", newline="\n") as f:
                        f.write(t2)
    try:
        from .demo import install_vscode
        install_vscode(root)
    except Exception:
        pass
    log.info("migrated %d legacy file(s) in %s to .nll", len(renamed), root)
    return renamed


def create_project(root: str, name: str = "New Project") -> Project:
    os.makedirs(root, exist_ok=True)
    for d in ("displays", "symbols", "scripts", "images", "recipes", "logs"):
        os.makedirs(os.path.join(root, d), exist_ok=True)
    p = os.path.join(root, "project.nll")
    if not os.path.exists(p):
        cfg = json.loads(json.dumps(DEFAULT_PROJECT))
        cfg["name"] = name
        with open(p, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    if not os.path.exists(os.path.join(root, "tags.csv")):
        write_csv(os.path.join(root, "tags.csv"), [], TAG_COLUMNS)
    if not os.path.exists(os.path.join(root, "aliases.csv")):
        write_csv(os.path.join(root, "aliases.csv"), [], ALIAS_COLUMNS)
    try:
        from .demo import install_vscode
        install_vscode(root)
    except Exception:
        pass
    return Project(root)

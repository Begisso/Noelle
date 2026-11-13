"""Noelle HMI command line / Noelle.exe entry point.

    Noelle.exe                                   open the last project (first start: Documents\\Noelle\\Demo)
    Noelle.exe D:\\Plant                          open a project folder
    Noelle.exe D:\\Plant\\displays\\Main.nll        open a .nll file (finds its project; also used by double click)
    Noelle.exe D:\\Plant --kiosk                  operator runtime only
    Noelle.exe D:\\Plant --server [--host 0.0.0.0] [--port 8800]   run without window (runtime server)
    Noelle.exe --register | --unregister         associate .nll files with Noelle (current user)
    Noelle.exe new D:\\NewProject [--name N]
    Noelle.exe adduser PROJECT NAME ROLE [PASSWORD]
    Noelle.exe import gwx|tia|simatic-xml|weintek PROJECT ...
    Noelle.exe render PROJECT DISPLAY.nll OUT.png

From source the same works with:  python -m noelle ...
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
import urllib.parse
import urllib.request

FROZEN = getattr(sys, "frozen", False)


def _appdata(*p):
    base = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "Noelle")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, *p)


def _settings():
    try:
        with open(_appdata("settings.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_settings(d):
    try:
        with open(_appdata("settings.json"), "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2)
    except OSError:
        pass


def _message(text, title="Noelle", error=False):
    """Show a message box when running as windowed exe (no console)."""
    if FROZEN and sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, str(text), title, 0x10 if error else 0x40)
    else:
        print(text, file=sys.stderr if error else sys.stdout)


def _setup_logging():
    handlers = []
    if FROZEN or sys.stdout is None:
        handlers.append(logging.FileHandler(_appdata("noelle.log"), encoding="utf-8"))
    if sys.stdout is not None:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                        datefmt="%H:%M:%S", handlers=handlers)
    for noisy in ("asyncua", "aiohttp.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    if sys.stdout is None:                      # windowed exe: keep print() harmless
        sys.stdout = open(os.devnull, "w")
        sys.stderr = open(_appdata("noelle-stderr.log"), "a", encoding="utf-8")


def _default_project():
    st = _settings()
    last = st.get("lastProject")
    if last and os.path.exists(os.path.join(last, "project.nll")):
        return last
    if not FROZEN:
        here = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "projects", "Demo")
        if os.path.exists(here):
            return here
    docs = os.path.join(os.path.expanduser("~"), "Documents", "Noelle", "Demo")
    if not os.path.exists(os.path.join(docs, "project.nll")):
        from .demo import create_demo
        create_demo(docs)
    return docs


def _resolve_target(path):
    """project folder | project.nll | display.nll -> (project_root, display_rel or None)"""
    path = os.path.abspath(path)
    if os.path.isdir(path):
        return path, None
    if path.lower().endswith(".nll"):
        d = os.path.dirname(path)
        while True:
            if os.path.exists(os.path.join(d, "project.nll")):
                if os.path.basename(path).lower() == "project.nll":
                    return d, None
                return d, os.path.relpath(path, d).replace("\\", "/")
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
        raise FileNotFoundError(f"{path} is not inside a Noelle project (no project.nll found above it)")
    raise FileNotFoundError(path)


def _running_instance(root):
    """If this project is already served by another Noelle process, return its URL."""
    info = os.path.join(root, "runtime", "server.json")
    try:
        with open(info, encoding="utf-8") as f:
            d = json.load(f)
        with urllib.request.urlopen(d["url"] + "api/me", timeout=1.5) as r:
            if r.status == 200:
                return d["url"]
    except Exception:
        pass
    return None


def _free_port(start, host="127.0.0.1"):
    for p in range(start, start + 100):
        s = socket.socket()
        try:
            s.bind((host if host not in ("0.0.0.0", "::") else "", p))
            return p
        except OSError:
            continue
        finally:
            s.close()
    return start


def register_filetype(unregister=False):
    """Associate .nll with this executable for the current user (HKCU, no admin rights)."""
    import winreg
    exe = sys.executable if FROZEN else f'"{sys.executable}" -m noelle'
    cmd = f'"{sys.executable}" "%1"' if FROZEN else f'{exe} "%1"'
    icon = f"{sys.executable},0" if FROZEN else ""
    base = r"Software\Classes"
    if unregister:
        for k in (r"Noelle.nll\shell\open\command", r"Noelle.nll\shell\open", r"Noelle.nll\shell", r"Noelle.nll\DefaultIcon", "Noelle.nll", ".nll"):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, base + "\\" + k)
            except OSError:
                pass
        return "removed the .nll association"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + r"\.nll") as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "Noelle.nll")
        winreg.SetValueEx(k, "Content Type", 0, winreg.REG_SZ, "application/json")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + r"\Noelle.nll") as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "Noelle display / project")
    if icon:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + r"\Noelle.nll\DefaultIcon") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, icon)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + r"\Noelle.nll\shell\open\command") as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, cmd)
    try:   # tell Explorer
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)
    except Exception:
        pass
    return f".nll files now open with {sys.executable}"


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    _setup_logging()
    cmd = argv[0] if argv else ""
    if cmd in ("--register", "--unregister"):
        _message(register_filetype(cmd == "--unregister"))
        return 0
    if cmd == "new":
        ap = argparse.ArgumentParser(prog="noelle new")
        ap.add_argument("project")
        ap.add_argument("--name", default=None)
        a = ap.parse_args(argv[1:])
        from .project import create_project
        create_project(a.project, a.name or os.path.basename(os.path.abspath(a.project)))
        print("created", os.path.abspath(a.project))
        return 0
    if cmd == "adduser":
        from .project import Project
        from .security import Security
        if len(argv) < 4:
            print("usage: adduser PROJECT NAME ROLE [PASSWORD]")
            return 2
        sec = Security(Project(argv[1]))
        pw = argv[4] if len(argv) > 4 else None
        if pw is None:
            import getpass
            pw = getpass.getpass("password: ")
        sec.set_user(argv[2], argv[3], pw)
        print("user saved")
        return 0
    if cmd == "import":
        from .importers import cli_import
        return cli_import(argv[1:])
    if cmd == "render":
        from .importers.visual import cli_render
        return cli_render(argv[1:])

    ap = argparse.ArgumentParser(prog="noelle", description="Noelle HMI")
    ap.add_argument("project", nargs="?", default=None, help="project folder or .nll file")
    ap.add_argument("--host", default=None, help="bind address (0.0.0.0 lets other PCs connect)")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--server", action="store_true", help="run without opening a window and keep running")
    ap.add_argument("--kiosk", action="store_true", help="open the operator runtime only")
    a = ap.parse_args(argv)
    try:
        root, display = _resolve_target(a.project) if a.project else (_default_project(), None)
    except Exception as e:
        _message(str(e), error=True)
        return 2
    query = urllib.parse.urlencode({"mode": "edit", "display": display}) if display else ""
    st = _settings()
    st["lastProject"] = root
    _save_settings(st)
    url = _running_instance(root)
    if url and not a.server:           # already open -> just open another window
        from .server import _open_app_window
        _open_app_window(url + ("?" + query if query else "") if not a.kiosk else url + "?mode=run&kiosk=1")
        return 0
    from .project import Project
    port = a.port or Project(root).cfg.get("server", {}).get("port", 8800)
    host = a.host or Project(root).cfg.get("server", {}).get("host", "127.0.0.1")
    if not a.port:
        port = _free_port(int(port), host)
    os.makedirs(os.path.join(root, "runtime"), exist_ok=True)
    from .server import run
    try:
        run(root, host, port, open_browser=not (a.no_browser or a.server), kiosk=a.kiosk,
            exit_when_idle=not (a.server or a.no_browser), start_query=query,
            info_file=os.path.join(root, "runtime", "server.json"))
    except Exception as e:
        logging.exception("startup failed")
        _message(f"Noelle could not start:\n{e}\n\nLog: {_appdata('noelle.log')}", error=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Importers: ICONICS GraphWorX32, Siemens WinCC (TIA Openness / SimaticML), Weintek.

Every importer returns a report dict::

    {"summary": str, "files": [created project paths], "warnings": [str],
     "identity": {...visual/functional identity results...}}
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def run_import(kind: str, project, params: dict) -> dict:
    if kind == "gwx":
        from .gwx import import_gwx
        return import_gwx(project, params)
    if kind == "tia":
        from .tia import import_tia
        return import_tia(project, params)
    if kind == "simatic-xml":
        from .tia import import_simatic_folder
        return import_simatic_folder(project, params)
    if kind == "weintek":
        from .weintek import import_weintek
        return import_weintek(project, params)
    raise ValueError(f"unknown importer '{kind}'")


def cli_import(argv):
    from ..project import Project
    if not argv:
        print(__doc__)
        return 2
    kind = argv[0]
    ap = argparse.ArgumentParser(prog=f"noelle import {kind}")
    ap.add_argument("project")
    if kind == "gwx":
        ap.add_argument("files", nargs="+")
        ap.add_argument("--folder", default="displays/imported")
        ap.add_argument("--mode", default="native", choices=["native", "hybrid"])
        ap.add_argument("--tagmap", default="")
        ap.add_argument("--no-identity", action="store_true")
    elif kind == "tia":
        ap.add_argument("tia_project")
        ap.add_argument("--device", default="")
        ap.add_argument("--version", default="")
        ap.add_argument("--plc", default="")
        ap.add_argument("--folder", default="displays/wincc")
    elif kind == "simatic-xml":
        ap.add_argument("path")
        ap.add_argument("--plc", default="")
        ap.add_argument("--folder", default="displays/wincc")
    elif kind == "weintek":
        ap.add_argument("file")
        ap.add_argument("--devicemap", default="")
        ap.add_argument("--prefix", default="")
    a = ap.parse_args(argv[1:])
    params = vars(a).copy()
    proj = Project(params.pop("project"))
    if kind == "gwx":
        params["files"] = ";".join(params["files"])
        params["snapshots"] = not params.pop("no_identity")
    if kind == "tia":
        params["project"] = params.pop("tia_project")
    rep = run_import(kind, proj, params)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0

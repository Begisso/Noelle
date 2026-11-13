"""Fast JSON helpers: use orjson when installed, fall back to stdlib json."""
from __future__ import annotations

import json
import math
from typing import Any

try:  # orjson is ~5-10x faster for the hot websocket path
    import orjson as _orjson

    _OPTS = _orjson.OPT_NON_STR_KEYS  # NaN/Inf are emitted as null by orjson

    def dumps(obj: Any) -> str:
        try:
            return _orjson.dumps(obj, option=_OPTS, default=_default).decode()
        except TypeError:
            return json.dumps(_sanitize(obj), ensure_ascii=False, default=_default, separators=(",", ":"))

    def loads(s: str | bytes) -> Any:
        return _orjson.loads(s)

except ImportError:  # pragma: no cover
    _orjson = None

    def dumps(obj: Any) -> str:
        return json.dumps(_sanitize(obj), ensure_ascii=False, default=_default, separators=(",", ":"))

    def loads(s: str | bytes) -> Any:
        return json.loads(s)


def _default(o: Any):
    if isinstance(o, (bytes, bytearray)):
        return o.hex()
    if isinstance(o, set):
        return list(o)
    return str(o)


def _sanitize(o: Any):
    """Replace NaN/Inf (invalid JSON) with None."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _sanitize(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_sanitize(v) for v in o]
    return o


def pretty(obj: Any) -> str:
    """Stable, human readable (Notepad / VS Code / git-diff friendly) JSON."""
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"

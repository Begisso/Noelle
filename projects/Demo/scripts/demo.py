# Demo server script. Hot reloaded when you save it (VS Code / Notepad).
# Type hints for VS Code: scripts/hmi.pyi
import time


@hmi.every(1000)
def counter():
    """Count seconds and accumulate pump run hours."""
    hmi.write_nowait("mem:Counter", (hmi.read("mem:Counter") or 0) + 1)
    for p in ("Pump1", "Pump2"):
        if hmi.read(f"{p}.Run"):
            hmi.write_nowait(f"mem:{p}Hours", round((hmi.read(f"mem:{p}Hours") or 0) + 1 / 3600, 5))


@hmi.on_change("Pump1.Fault")
def fault(ref, value, old):
    if value:
        hmi.notify("Pump 1 fault! Pump stopped.", "error")
        hmi.write_nowait("Pump1.Run", 0)


@hmi.function
def reset_counter():
    """Called from the 'Python: reset' button (action type py)."""
    hmi.write_nowait("mem:Counter", 0)
    hmi.notify("counter reset from Python")
    return "ok"

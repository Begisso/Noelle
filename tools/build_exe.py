"""Build dist/Noelle.exe (single file). Run: .venv\Scripts\python tools\build_exe.py"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
subprocess.check_call([sys.executable, os.path.join("tools", "make_icon.py")])
sep = ";" if os.name == "nt" else ":"
args = [
    sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
    "--name", "Noelle", "--icon", os.path.join(ROOT, "build", "noelle.ico"),
    "--distpath", "dist", "--workpath", os.path.join("build", "pyi"), "--specpath", "build",
    "--add-data", f"{os.path.join(ROOT, 'web')}{sep}web",
    "--add-data", f"{os.path.join(ROOT, 'noelle', 'schema')}{sep}noelle/schema",
    "--add-data", f"{os.path.join(ROOT, 'noelle', 'drivers', 'opcda_bridge.ps1')}{sep}noelle/drivers",
    "--add-data", f"{os.path.join(ROOT, 'noelle', 'importers', 'gwx_export.ps1')}{sep}noelle/importers",
    "--add-data", f"{os.path.join(ROOT, 'noelle', 'importers', 'tia_export.ps1')}{sep}noelle/importers",
    "--add-data", f"{os.path.join(ROOT, 'noelle', 'importers', 'tlbimp.ps1')}{sep}noelle/importers",
    "--collect-submodules", "noelle",
    "--hidden-import", "asyncua", "--hidden-import", "orjson",
    "--exclude-module", "snap7", "--exclude-module", "tkinter", "--exclude-module", "tests",
    "--exclude-module", "matplotlib", "--exclude-module", "numpy.testing", "--exclude-module", "IPython",
    "noelle_launcher.py",
]
subprocess.check_call(args)
print("built", os.path.join(ROOT, "dist", "Noelle.exe"))

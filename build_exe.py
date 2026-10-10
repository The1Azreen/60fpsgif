"""Package the desktop app into a standalone Windows .exe with PyInstaller.

    pip install pyinstaller
    python build_exe.py

The result is dist/60fps GIF Maker.exe. FFmpeg still needs to be on PATH. RIFE is
looked up in a tools/ folder next to the .exe (the app's "Install RIFE" button puts it there).
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SEP = ";" if sys.platform == "win32" else ":"

subprocess.run([
    sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
    "--name", "60fps GIF Maker", "--icon", str(ROOT / "icon.ico"),
    "--add-data", f"{ROOT / 'icon.ico'}{SEP}.",
    str(ROOT / "fpsgif_gui.pyw"),
], cwd=ROOT, check=True)
print(f"\nBuilt: {ROOT / 'dist' / '60fps GIF Maker.exe'}")

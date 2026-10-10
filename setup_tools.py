"""Download RIFE (rife-ncnn-vulkan) into ./tools for AI frame interpolation."""

import io
import sys
import urllib.request
import zipfile
from pathlib import Path

VERSION = "20221029"
PLATFORM = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "ubuntu")
URL = (f"https://github.com/nihui/rife-ncnn-vulkan/releases/download/"
       f"{VERSION}/rife-ncnn-vulkan-{VERSION}-{PLATFORM}.zip")
# When packaged as an .exe, keep tools/ next to the exe rather than in the temp unpack dir.
ROOT = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
TOOLS = ROOT / "tools"


def main():
    if list(TOOLS.glob("**/rife-ncnn-vulkan*")):
        print(f"RIFE already installed in {TOOLS}")
        return
    print(f"Downloading {URL}")
    with urllib.request.urlopen(URL) as r:
        data = r.read()
    TOOLS.mkdir(exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(TOOLS)
    if PLATFORM != "windows":
        for exe in TOOLS.glob("**/rife-ncnn-vulkan"):
            exe.chmod(0o755)
    print(f"Installed RIFE to {TOOLS}")


if __name__ == "__main__":
    main()

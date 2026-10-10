"""Download optional AI tools into ./tools.

    python setup_tools.py             RIFE (rife-ncnn-vulkan) for frame interpolation
    python setup_tools.py --matting   RobustVideoMatting model for background removal
                                      (also needs: pip install -r requirements.txt)
"""

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


MATTING_URL = "https://github.com/PeterL1n/RobustVideoMatting/releases/download/v1.0.0/rvm_mobilenetv3_fp32.onnx"
MATTING_MODEL = TOOLS / "rvm_mobilenetv3_fp32.onnx"


def install_matting():
    if MATTING_MODEL.is_file():
        print(f"Background removal model already installed: {MATTING_MODEL}")
        return
    print(f"Downloading {MATTING_URL}")
    TOOLS.mkdir(exist_ok=True)
    part = MATTING_MODEL.with_suffix(".part")
    with urllib.request.urlopen(MATTING_URL) as r, open(part, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    part.replace(MATTING_MODEL)
    print(f"Installed background removal model to {MATTING_MODEL}")


def main():
    if "--matting" in sys.argv[1:]:
        return install_matting()
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

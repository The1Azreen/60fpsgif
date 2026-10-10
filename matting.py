"""AI background removal for video frames with RobustVideoMatting (MobileNetV3, ONNX).

RVM is a video matting model: it carries recurrent state from frame to frame, so the
cutout stays steady instead of flickering like a per-image model would. It runs through
onnxruntime on the CPU (about 15 ms per 480x270 frame), or on the GPU with
onnxruntime-directml / onnxruntime-gpu. numpy and onnxruntime are imported lazily, so the
rest of the tool works without them.
"""

import subprocess
import sys

from fpsgif import NO_WINDOW, TOOLS

MODEL = TOOLS / "rvm_mobilenetv3_fp32.onnx"
MODEL_URL = "https://github.com/PeterL1n/RobustVideoMatting/releases/download/v1.0.0/rvm_mobilenetv3_fp32.onnx"
PROVIDERS = ("DmlExecutionProvider", "CUDAExecutionProvider", "CoreMLExecutionProvider", "CPUExecutionProvider")


def status():
    """(available, message) for the background remover."""
    try:
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
    except ImportError:
        return False, "AI cutout needs numpy and onnxruntime (pip install -r requirements.txt)"
    if not MODEL.is_file():
        return False, "AI cutout model not installed"
    return True, f"AI cutout ready · {device()}"


def device():
    import onnxruntime as ort
    providers = ort.get_available_providers()
    return ("GPU (DirectML)" if "DmlExecutionProvider" in providers else
            "GPU (CUDA)" if "CUDAExecutionProvider" in providers else
            "GPU (CoreML)" if "CoreMLExecutionProvider" in providers else "CPU")


class Matter:
    """Turns RGB frames (uint8 HxWx3, in playback order) into RGBA cutouts."""

    def __init__(self):
        import numpy as np
        import onnxruntime as ort
        self.np = np
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        available = ort.get_available_providers()
        self.session = ort.InferenceSession(str(MODEL), opts, providers=[p for p in PROVIDERS if p in available])
        self.reset()

    def reset(self):
        """Forget the previous frames, e.g. before a new clip or a seek."""
        self.rec = [self.np.zeros((1, 1, 1, 1), self.np.float32)] * 4

    def __call__(self, rgb):
        np = self.np
        h, w = rgb.shape[:2]
        # Analyse at ~256 px on the long side: as accurate as full res at this size, and ~3x faster.
        ratio = np.array([min(1.0, 256 / max(h, w))], np.float32)
        src = rgb.astype(np.float32) / 255
        fgr, pha, *self.rec = self.session.run(None, {
            "src": src.transpose(2, 0, 1)[None], "r1i": self.rec[0], "r2i": self.rec[1],
            "r3i": self.rec[2], "r4i": self.rec[3], "downsample_ratio": ratio})
        alpha = pha[0, 0][..., None]
        # On soft edges use RVM's predicted foreground colour, which removes background colour
        # bleeding into hair and motion blur; keep the exact source colours everywhere else.
        color = np.where(alpha > 0.98, src, fgr[0].transpose(1, 2, 0))
        return (np.concatenate([color, alpha], axis=2) * 255 + 0.5).clip(0, 255).astype(np.uint8)


def read_ppm(stream):
    """Yield frames from FFmpeg's `-f image2pipe -c:v ppm` output as uint8 HxWx3 arrays."""
    import numpy as np
    while True:
        magic = stream.readline()
        if not magic:
            return
        w, h = map(int, stream.readline().split())
        stream.readline()  # maxval, always 255 here
        data = stream.read(w * h * 3)
        if len(data) < w * h * 3:
            return
        yield np.frombuffer(data, np.uint8).reshape(h, w, 3)


def matte_frames(ffmpeg, inputs, vf, fps, out_dir, total, report=None):
    """Decode `inputs` through `vf`, cut out the background and write RGBA PNGs to out_dir."""
    out_dir.mkdir(exist_ok=True)
    matter = Matter()
    reader = subprocess.Popen([ffmpeg, "-hide_banner", "-loglevel", "error", *map(str, inputs), "-vf", vf,
                               "-an", "-f", "image2pipe", "-c:v", "ppm", "-"],
                              stdout=subprocess.PIPE, creationflags=NO_WINDOW)
    writer = None
    n = 0
    try:
        for frame in read_ppm(reader.stdout):
            rgba = matter(frame)
            if writer is None:
                h, w = rgba.shape[:2]
                writer = subprocess.Popen([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo",
                                           "-pix_fmt", "rgba", "-s", f"{w}x{h}", "-framerate", str(fps), "-i", "-",
                                           str(out_dir / "%08d.png")],
                                          stdin=subprocess.PIPE, creationflags=NO_WINDOW)
            writer.stdin.write(rgba.tobytes())
            n += 1
            if report:
                report(n / total if total else 0, f"({n}/{total} frames)")
    finally:
        reader.stdout.close()
        reader.wait()
        if writer:
            writer.stdin.close()
            writer.wait()
    if reader.returncode or not writer or writer.returncode:
        sys.exit("Background removal failed.")
    return n

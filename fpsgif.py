"""Turn a short video into a smooth, high-frame-rate GIF (and/or WebP/APNG/MP4).

Pipeline:
  1. Trim + scale the input with FFmpeg.
  2. Interpolate new in-between frames up to the target frame rate, using
     RIFE (AI, best quality, needs a GPU with Vulkan) or FFmpeg's
     minterpolate filter (CPU fallback).
  3. Encode: GIF via a per-clip optimized palette, WebP/APNG directly, MP4 as
     a silent H.264 "GIF video" (what Imgur serves as .gifv) - by far the smallest.

GIF frame delays are stored in 1/100 s, and browsers slow down delays under
20 ms, so GIF tops out at 50 fps. WebP, APNG and MP4 can do true 60 fps.
"""

import argparse
import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TOOLS = ROOT / "tools"

GIF_MAX_FPS = 50
FORMATS = ("gif", "webp", "apng", "mp4")
DEFAULT_FPS = {"gif": 50, "webp": 60, "apng": 60, "mp4": 60}


def find_exe(name):
    local = list(TOOLS.glob(f"**/{name}.exe")) + list(TOOLS.glob(f"**/{name}"))
    local = [p for p in local if p.is_file()]
    if local:
        return str(local[0])
    return shutil.which(name)


def run(cmd, quiet=True):
    print("  $ " + " ".join(f'"{c}"' if " " in str(c) else str(c) for c in cmd))
    result = subprocess.run(
        [str(c) for c in cmd],
        stdout=subprocess.PIPE if quiet else None,
        stderr=subprocess.PIPE if quiet else None,
        text=True,
    )
    if result.returncode != 0:
        if quiet:
            sys.stderr.write(result.stderr or "")
        sys.exit(f"Command failed: {cmd[0]}")
    return result


def probe(ffprobe, path):
    out = run([
        ffprobe, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=avg_frame_rate,r_frame_rate,width,height,nb_frames:format=duration",
        "-of", "json", path,
    ]).stdout
    info = json.loads(out)
    stream = info["streams"][0]

    def rate(s):
        num, _, den = s.partition("/")
        return float(num) / float(den or 1) if float(den or 1) else 0.0

    fps = rate(stream.get("avg_frame_rate", "0/0")) or rate(stream.get("r_frame_rate", "0/0"))
    return {
        "fps": fps,
        "width": int(stream["width"]),
        "height": int(stream["height"]),
        "duration": float(info.get("format", {}).get("duration", 0) or 0),
    }


def trim_args(args):
    out = []
    if args.start:
        out += ["-ss", str(args.start)]
    if args.duration:
        out += ["-t", str(args.duration)]
    return out


def scale_filter(width):
    # -2 keeps aspect ratio with an even height; lanczos keeps edges sharp.
    return f"scale={width}:-2:flags=lanczos" if width else "null"


def interpolate_rife(rife, ffmpeg, args, src_fps, target_fps, work):
    """Extract frames, run RIFE to hit target_fps exactly, return (pattern, fps)."""
    src_dir = work / "src"
    out_dir = work / f"rife_{target_fps}"
    src_dir.mkdir(exist_ok=True)
    out_dir.mkdir(exist_ok=True)

    if not any(src_dir.iterdir()):
        run([
            ffmpeg, "-hide_banner", "-y", *trim_args(args), "-i", args.input,
            "-vf", scale_filter(args.width), "-fps_mode", "passthrough",
            str(src_dir / "%08d.png"),
        ])

    n_in = len(list(src_dir.glob("*.png")))
    if n_in < 2:
        sys.exit("Not enough frames in the clip to interpolate.")
    n_out = round(n_in * target_fps / src_fps)

    if len(list(out_dir.glob("*.png"))) != n_out:  # reuse frames across formats at the same fps
        model = Path(rife).parent / args.rife_model
        print(f"  RIFE: {n_in} frames @ {src_fps:.3f} fps -> {n_out} frames @ {target_fps} fps")
        run([
            rife, "-i", src_dir, "-o", out_dir, "-n", n_out,
            "-m", model, "-f", "%08d.png", *(["-g", args.gpu] if args.gpu is not None else []),
        ], quiet=not args.verbose)
    return ["-framerate", str(target_fps), "-i", str(out_dir / "%08d.png")], "null"


def interpolate_ffmpeg(args, target_fps):
    """Return (input args, filter) that make FFmpeg interpolate on the fly."""
    vf = (
        f"{scale_filter(args.width)},"
        f"minterpolate=fps={target_fps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1:scd=fdiff"
    )
    return [*trim_args(args), "-i", args.input], vf


def encode(ffmpeg, fmt, inputs, vf, fps, out):
    base = [ffmpeg, "-hide_banner", "-y", *inputs]
    if fmt == "gif":
        # Build one palette tuned to this clip, then dither against it.
        graph = (
            f"[0:v]{vf},fps={fps},split[a][b];"
            "[a]palettegen=max_colors=256:stats_mode=diff[p];"
            "[b][p]paletteuse=dither=sierra2_4a:diff_mode=rectangle"
        )
        run([*base, "-filter_complex", graph, "-loop", "0", out])
    elif fmt == "webp":
        run([*base, "-vf", f"{vf},fps={fps}", "-c:v", "libwebp_anim",
             "-quality", "85", "-compression_level", "6", "-loop", "0", out])
    elif fmt == "apng":
        run([*base, "-vf", f"{vf},fps={fps}", "-c:v", "apng", "-plays", "0", "-f", "apng", out])
    elif fmt == "mp4":
        # Silent, web-friendly H.264 that plays inline like a GIF.
        run([*base, "-vf", f"{vf},fps={fps}", "-an", "-c:v", "libx264", "-preset", "slow",
             "-crf", "23", "-pix_fmt", "yuv420p", "-movflags", "+faststart", out])


def main():
    p = argparse.ArgumentParser(description="Make a smooth high-fps GIF/WebP/APNG/MP4 from a short video.")
    p.add_argument("input", help="input video file")
    p.add_argument("-o", "--output", help="output path (extension picks the format); default: next to input")
    p.add_argument("-f", "--format", choices=(*FORMATS, "all"), default="gif",
                   help="output format (default: gif, or inferred from --output)")
    p.add_argument("--fps", type=int, help="target fps (default: 50 for GIF, 60 for others)")
    p.add_argument("-w", "--width", type=int, default=480, help="output width in px, 0 = keep (default: 480)")
    p.add_argument("-s", "--start", help="start time, e.g. 3 or 00:00:03.5")
    p.add_argument("-t", "--duration", help="clip length in seconds")
    p.add_argument("--engine", choices=("auto", "rife", "ffmpeg"), default="auto",
                   help="interpolation engine (default: RIFE if installed, else FFmpeg)")
    p.add_argument("--rife-model", default="rife-v4.6", help="RIFE model folder (default: rife-v4.6)")
    p.add_argument("--gpu", type=int, help="GPU id for RIFE (-1 = CPU)")
    p.add_argument("--keep-temp", action="store_true", help="keep extracted/interpolated frames")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    ffmpeg, ffprobe = find_exe("ffmpeg"), find_exe("ffprobe")
    if not ffmpeg or not ffprobe:
        sys.exit("FFmpeg/ffprobe not found. Install FFmpeg and add it to PATH.")

    inp = Path(args.input)
    if not inp.is_file():
        sys.exit(f"Input not found: {inp}")

    if args.output and args.format == "gif":
        ext = Path(args.output).suffix.lower().lstrip(".")
        args.format = {"png": "apng"}.get(ext, ext) if ext in (*FORMATS, "png") else "gif"
    formats = FORMATS if args.format == "all" else (args.format,)

    rife = find_exe("rife-ncnn-vulkan") if args.engine in ("auto", "rife") else None
    if args.engine == "rife" and not rife:
        sys.exit("RIFE not found. Run: python setup_tools.py")
    engine = "rife" if rife else "ffmpeg"

    info = probe(ffprobe, inp)
    print(f"Input: {inp.name}  {info['width']}x{info['height']}  {info['fps']:.3f} fps  "
          f"{info['duration']:.2f}s  | engine: {engine}")

    work = Path(tempfile.mkdtemp(prefix="fpsgif_"))
    try:
        for fmt in formats:
            fps = args.fps or DEFAULT_FPS[fmt]
            if fmt == "gif" and fps > GIF_MAX_FPS:
                print(f"  note: GIF can't play reliably above {GIF_MAX_FPS} fps; using {GIF_MAX_FPS}.")
                fps = GIF_MAX_FPS

            if args.output and len(formats) == 1:
                out = Path(args.output)
            else:
                suffix = {"gif": ".gif", "webp": ".webp", "apng": ".png", "mp4": ".mp4"}[fmt]
                out = (Path(args.output).with_suffix("") if args.output else inp.with_suffix("")) \
                    .with_name(f"{(Path(args.output) if args.output else inp).stem}_{fps}fps{suffix}")

            print(f"\n[{fmt.upper()} @ {fps} fps] -> {out}")
            if info["fps"] >= fps - 0.01:
                print("  source already at/above target fps; resampling only")
                inputs, vf = [*trim_args(args), "-i", str(inp)], scale_filter(args.width)
            elif engine == "rife":
                inputs, vf = interpolate_rife(rife, ffmpeg, args, info["fps"], fps, work)
            else:
                inputs, vf = interpolate_ffmpeg(args, fps)

            encode(ffmpeg, fmt, inputs, vf, fps, out)
            size = out.stat().st_size / 1024 / 1024
            print(f"  done: {size:.2f} MB")
    finally:
        if args.keep_temp:
            print(f"\nTemp frames kept in {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()

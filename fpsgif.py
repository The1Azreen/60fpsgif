"""Turn a short video into a smooth, high-frame-rate GIF (and/or WebP/APNG/MP4/AVIF).

Pipeline:
  1. Trim + scale the input with FFmpeg.
  2. Interpolate new in-between frames up to the target frame rate, using
     RIFE (AI, best quality, needs a GPU with Vulkan) or FFmpeg's
     minterpolate filter (CPU fallback).
  3. Encode: GIF via a per-clip optimized palette, WebP/APNG directly, MP4 as
     a silent H.264 "GIF video" (what Imgur serves as .gifv) - by far the smallest,
     AVIF as animated AV1 (small, transparent, and the only format here that can stay HDR).

GIF frame delays are stored in 1/100 s, and browsers slow down delays under
20 ms, so GIF tops out at 50 fps. WebP, APNG, MP4 and AVIF can do true 60 fps.

HDR sources (PQ or HLG) are tone-mapped to SDR, or with --hdr kept as a 10-bit HDR AVIF.
"""

import argparse
import json
import math
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

# When packaged as an .exe, keep tools/ next to the exe rather than in the temp unpack dir.
ROOT = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
TOOLS = ROOT / "tools"

GIF_MAX_FPS = 50
FORMATS = ("gif", "webp", "apng", "mp4", "avif")
DEFAULT_FPS = {"gif": 50, "webp": 60, "apng": 60, "mp4": 60, "avif": 60}
SUFFIX = {"gif": ".gif", "webp": ".webp", "apng": ".png", "mp4": ".mp4", "avif": ".avif"}
HDR_TRANSFERS = {"smpte2084": "PQ", "arib-std-b67": "HLG"}
# HDR -> SDR. Mobius keeps colours in the SDR range untouched and only rolls off the highlights.
TONEMAP = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=mobius:desat=0,"
           "zscale=t=bt709:m=bt709:r=tv,format=yuv420p")
# Keep FFmpeg/RIFE from popping up console windows when run from the windowed GUI.
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def find_exe(name):
    local = list(TOOLS.glob(f"**/{name}.exe")) + list(TOOLS.glob(f"**/{name}"))
    local = [p for p in local if p.is_file()]
    if local:
        return str(local[0])
    return shutil.which(name)


def run(cmd, quiet=True, on_progress=None):
    """Run a command, exiting on failure. For FFmpeg commands, on_progress(seconds) is
    called with the output position as encoding advances."""
    print("  $ " + " ".join(f'"{c}"' if " " in str(c) else str(c) for c in cmd))
    cmd = [str(c) for c in cmd]
    if on_progress is None:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE if quiet else None,
            stderr=subprocess.PIPE if quiet else None,
            text=True,
            encoding="utf-8",  # FFmpeg prints UTF-8; the locale code page can't hold every file name
            errors="replace",
            creationflags=NO_WINDOW,
        )
    else:
        cmd = [cmd[0], "-nostats", "-progress", "pipe:1", *cmd[1:]]
        with tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as err:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err if quiet else None,
                                    text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
            for line in proc.stdout:
                key, _, value = line.strip().partition("=")
                if key == "out_time_us" and value.isdigit():
                    on_progress(int(value) / 1e6)
            proc.wait()
            err.seek(0)
            result = subprocess.CompletedProcess(cmd, proc.returncode, "", err.read())
    if result.returncode != 0:
        if quiet:
            sys.stderr.write(result.stderr or "")
        sys.exit(f"Command failed: {cmd[0]}")
    return result


class Progress:
    """Prints "@@progress <percent> <label>" lines for the GUI (hidden --progress flag).

    The run is split into equal jobs (one per output format); each job into stages.
    """

    def __init__(self, enabled, jobs):
        self.enabled, self.jobs, self.job, self.last = enabled, jobs, 0, None

    def stage(self, lo, hi, label, busy=False):
        """Start a stage spanning [lo, hi] of the current job; return report(fraction, detail).

        busy=True marks a stage whose progress can't be measured, so the GUI animates instead.
        """
        def report(frac, detail=""):
            if not self.enabled:
                return
            pct = 100 * (self.job + lo + (hi - lo) * min(max(frac, 0), 1)) / self.jobs
            kind = "busy" if busy and frac < 1 else "progress"
            line = f"@@{kind} {pct:.1f} {label} {detail}".rstrip()
            if line != self.last:
                self.last = line
                print(line, flush=True)
        report(0)
        return report


def parse_time(value):
    """Seconds from "3", "3.5", "01:02" or "00:01:02.5"."""
    return sum(float(part) * 60 ** i for i, part in enumerate(reversed(str(value).split(":"))))


def clip_seconds(args, info):
    length = max(info["duration"] - (parse_time(args.start) if args.start else 0), 0)
    if args.duration:
        length = min(length, float(args.duration)) if length else float(args.duration)
    return length


def timed(report, seconds):
    """Adapt a stage report to FFmpeg's position-in-seconds callback."""
    return (lambda t: report(t / seconds)) if seconds else None


def probe(ffprobe, path):
    out = run([
        ffprobe, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=avg_frame_rate,r_frame_rate,width,height,nb_frames,color_transfer"
                         ":format=duration",
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
        # The transfer ("smpte2084" = PQ, "arib-std-b67" = HLG) when the video is HDR, else None.
        "hdr": stream.get("color_transfer") if stream.get("color_transfer") in HDR_TRANSFERS else None,
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


def source_filter(width, tonemap):
    """Scale, then tone-map an HDR source down to SDR (scaling first makes the tone-map cheaper)."""
    return f"{scale_filter(width)},{TONEMAP}" if tonemap else scale_filter(width)


def interpolate_rife(rife, ffmpeg, args, src_vf, tag, src_fps, target_fps, work, progress, clip, rife_hi=0.7):
    """Extract frames, run RIFE to hit target_fps exactly, return (pattern, fps).

    RIFE only reads 8-bit images, so an HDR clip kept as HDR goes through it as 8-bit PQ/HLG.
    """
    src_dir = work / f"src{tag}"
    out_dir = work / f"rife_{target_fps}{tag}"
    src_dir.mkdir(exist_ok=True)
    out_dir.mkdir(exist_ok=True)

    report = progress.stage(0, 0.1, "Extracting frames")
    if not any(src_dir.iterdir()):
        run([
            ffmpeg, "-hide_banner", "-y", *trim_args(args), "-i", args.input,
            "-vf", src_vf, "-fps_mode", "passthrough", "-pix_fmt", "rgb24",  # RIFE misreads RGBA
            str(src_dir / "%08d.png"),
        ], on_progress=timed(report, clip))

    n_in = len(list(src_dir.glob("*.png")))
    if n_in < 2:
        sys.exit("Not enough frames in the clip to interpolate.")
    n_out = round(n_in * target_fps / src_fps)

    if len(list(out_dir.glob("*.png"))) != n_out:  # reuse frames across formats at the same fps
        model = Path(rife).parent / args.rife_model
        print(f"  RIFE: {n_in} frames @ {src_fps:.3f} fps -> {n_out} frames @ {target_fps} fps")
        report = progress.stage(0.1, rife_hi, "Interpolating with RIFE")
        done = threading.Event()

        def watch():  # RIFE prints no progress, so count the frames it has written
            while not done.wait(0.5):
                n = sum(1 for _ in out_dir.glob("*.png"))
                report(n / n_out, f"({n}/{n_out} frames)")

        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        try:
            run([
                rife, "-i", src_dir, "-o", out_dir, "-n", n_out,
                "-m", model, "-f", "%08d.png", *(["-g", args.gpu] if args.gpu is not None else []),
            ], quiet=not args.verbose)
        finally:
            done.set()
            watcher.join()
    return ["-framerate", str(target_fps), "-i", str(out_dir / "%08d.png")], "null"


def interpolate_ffmpeg(args, src_vf, target_fps):
    """Return (input args, filter) that make FFmpeg interpolate on the fly."""
    vf = (
        f"{src_vf},"
        f"minterpolate=fps={target_fps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1:scd=fdiff"
    )
    return [*trim_args(args), "-i", args.input], vf


def encode(ffmpeg, fmt, inputs, vf, fps, out, on_progress=None, alpha=False, bg="black", hdr=None):
    """Encode one output. alpha=(width, height) means the input frames carry transparency (background removed).
    hdr = an HDR transfer from probe() makes AVIF a 10-bit HDR image with that transfer."""
    base = [ffmpeg, "-hide_banner", "-y", *inputs]
    if fmt == "gif":
        # Build one palette tuned to this clip, then dither against it.
        graph = (
            f"[0:v]{vf},fps={fps},split[a][b];"
            "[a]palettegen=max_colors=256:stats_mode=diff[p];"
            "[b][p]paletteuse=dither=sierra2_4a:diff_mode=rectangle"
        )
        run([*base, "-filter_complex", graph, "-loop", "0", out], on_progress=on_progress)
    elif fmt == "webp":
        # compression_level 6 is ~25x slower than 4 for ~2% smaller files.
        run([*base, "-vf", f"{vf},fps={fps}", "-c:v", "libwebp_anim", *(["-pix_fmt", "yuva420p"] if alpha else []),
             "-quality", "85", "-compression_level", "4", "-loop", "0", out], on_progress=on_progress)
    elif fmt == "apng":
        run([*base, "-vf", f"{vf},fps={fps}", "-c:v", "apng", "-plays", "0", "-f", "apng", out], on_progress=on_progress)
    elif fmt == "mp4":
        # Silent, web-friendly H.264 that plays inline like a GIF. H.264 has no transparency,
        # so a cutout is laid over a solid background colour.
        if alpha:
            w, h = alpha
            graph = f"color=c={bg}:s={w}x{h}:r={fps}[c];[0:v]{vf},fps={fps}[fg];[c][fg]overlay=shortest=1"
            filters = ["-filter_complex", graph]
        else:
            filters = ["-vf", f"{vf},fps={fps}"]
        run([*base, *filters, "-an", "-c:v", "libx264", "-preset", "slow",
             "-crf", "23", "-pix_fmt", "yuv420p", "-movflags", "+faststart", out],
            on_progress=on_progress)
    elif fmt == "avif":
        # SVT-AV1 for the picture (fast); transparency is a second, greyscale AV1 stream, which
        # only libaom can encode. Frames coming back from RIFE/matting are PNGs tagged as sRGB, so
        # HDR output converts to BT.2020 YUV explicitly and re-tags the frames with the HDR transfer.
        # 10-bit SVT-AV1 AVIFs render as a blank box in Chrome, so HDR uses libaom for the picture too.
        if hdr:
            color = ("scale=out_color_matrix=bt2020:out_range=tv,format=yuv420p10le,"
                     f"setparams=color_primaries=bt2020:color_trc={hdr}:colorspace=bt2020nc:range=tv")
            picture = "libaom-av1"
        else:
            color = "format=yuv420p"
            picture = "libsvtav1"
        if alpha:
            graph = f"[0:v]{vf},fps={fps},format=rgba,split[c][a];[c]{color}[cv];[a]alphaextract,format=gray[av]"
            streams = ["-filter_complex", graph, "-map", "[cv]", "-map", "[av]",
                       "-c:v:0", picture, "-c:v:1", "libaom-av1"]
        else:
            streams = ["-vf", f"{vf},fps={fps},{color}", "-c:v", picture]
        speed = ["-cpu-used", "6", "-row-mt", "1"]  # libaom's speed knobs; SVT-AV1 uses -preset
        if picture == "libsvtav1":
            speed = ["-preset:v:0", "6", *(["-cpu-used:v:1", "6", "-row-mt:v:1", "1"] if alpha else [])]
        run([*base, *streams, "-an", "-crf", "30", *speed, "-loop", "0", "-f", "avif", out],
            on_progress=on_progress)


def main():
    p = argparse.ArgumentParser(description="Make a smooth high-fps GIF/WebP/APNG/MP4/AVIF from a short video.")
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
    p.add_argument("--remove-bg", action="store_true",
                   help="cut out the background with AI (transparent in WebP/APNG/AVIF/GIF; needs matting model)")
    p.add_argument("--hdr", action="store_true",
                   help="keep an HDR source as HDR: outputs a 10-bit HDR AVIF (default: tone-map to SDR)")
    p.add_argument("--bg-color", default="black", help="background colour for MP4 when using --remove-bg")
    p.add_argument("--keep-temp", action="store_true", help="keep extracted/interpolated frames")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--progress", action="store_true", help=argparse.SUPPRESS)  # machine-readable, for the GUI
    args = p.parse_args()
    # File names can hold characters the console's code page can't show (e.g. emoji on cp932);
    # print a placeholder instead of crashing.
    for stream in (sys.stdout, sys.stderr):
        if stream:
            stream.reconfigure(errors="replace")

    ffmpeg, ffprobe = find_exe("ffmpeg"), find_exe("ffprobe")
    if not ffmpeg or not ffprobe:
        sys.exit("FFmpeg/ffprobe not found. Install FFmpeg and add it to PATH.")

    inp = Path(args.input)
    if not inp.is_file():
        sys.exit(f"Input not found: {inp}")

    if args.output and args.format == "gif":
        ext = Path(args.output).suffix.lower().lstrip(".")
        args.format = {"png": "apng"}.get(ext, ext) if ext in (*FORMATS, "png") else "gif"

    if args.remove_bg:
        import matting
        ok, msg = matting.status()
        if not ok:
            sys.exit(f"{msg}. Run: python setup_tools.py --matting")

    rife = find_exe("rife-ncnn-vulkan") if args.engine in ("auto", "rife") else None
    if args.engine == "rife" and not rife:
        sys.exit("RIFE not found. Run: python setup_tools.py")
    engine = "rife" if rife else "ffmpeg"

    info = probe(ffprobe, inp)
    hdr = info["hdr"]
    hdr_note = f"  HDR ({HDR_TRANSFERS[hdr]})" if hdr else ""
    print(f"Input: {inp.name}  {info['width']}x{info['height']}  {info['fps']:.3f} fps  "
          f"{info['duration']:.2f}s{hdr_note}  | engine: {engine}")
    if args.hdr and not hdr:
        print("  note: source isn't HDR, so --hdr has no effect.")
        args.hdr = False
    if args.hdr and args.format not in ("avif", "all"):
        print(f"  note: only AVIF can stay HDR; making an HDR AVIF instead of {args.format.upper()}.")
        args.format = "avif"
        if args.output:
            args.output = str(Path(args.output).with_suffix(".avif"))
    formats = FORMATS if args.format == "all" else (args.format,)

    clip = clip_seconds(args, info)
    progress = Progress(args.progress, len(formats))
    work = Path(tempfile.mkdtemp(prefix="fpsgif_"))
    try:
        for progress.job, fmt in enumerate(formats):
            fps = args.fps or DEFAULT_FPS[fmt]
            keep_hdr = hdr if args.hdr and fmt == "avif" else None
            src_vf = source_filter(args.width, tonemap=hdr and not keep_hdr)
            tag = "_hdr" if keep_hdr else ""  # tone-mapped and HDR frames are cached separately
            if fmt == "gif" and fps > GIF_MAX_FPS:
                print(f"  note: GIF can't play reliably above {GIF_MAX_FPS} fps; using {GIF_MAX_FPS}.")
                fps = GIF_MAX_FPS

            if args.output and len(formats) == 1:
                out = Path(args.output)
            else:
                out = (Path(args.output).with_suffix("") if args.output else inp.with_suffix("")) \
                    .with_name(f"{(Path(args.output) if args.output else inp).stem}_{fps}fps{SUFFIX[fmt]}")

            print(f"\n[{fmt.upper()}{' HDR' if keep_hdr else ''} @ {fps} fps] -> {out}")
            if keep_hdr and (args.remove_bg or (engine == "rife" and info["fps"] < fps - 0.01)):
                print("  note: RIFE and the AI cutout work in 8-bit, so smooth HDR gradients may show banding.")
            interpolated = False
            if info["fps"] >= fps - 0.01:
                print("  source already at/above target fps; resampling only")
                inputs, vf = [*trim_args(args), "-i", str(inp)], src_vf
            elif engine == "rife":
                inputs, vf = interpolate_rife(rife, ffmpeg, args, src_vf, tag, info["fps"], fps, work, progress,
                                              clip, rife_hi=0.5 if args.remove_bg else 0.7)
                interpolated = True
            else:
                inputs, vf = interpolate_ffmpeg(args, src_vf, fps)

            encode_lo = 0.7 if interpolated else 0
            if args.remove_bg:
                # Cut out every final frame (after interpolation: RIFE ignores alpha), once per fps.
                lo, encode_lo = (0.5, 0.8) if interpolated else (0, 0.6)
                report = progress.stage(lo, encode_lo, "Removing background")
                matte_dir = work / f"matte_{fps}{tag}"
                if not (matte_dir / "done").is_file():
                    print("  removing background (AI)")
                    matting.matte_frames(ffmpeg, inputs, f"{vf},fps={fps}", fps, matte_dir, round(clip * fps), report)
                    (matte_dir / "done").touch()
                inputs, vf = ["-framerate", str(fps), "-i", str(matte_dir / "%08d.png")], "null"
                with open(matte_dir / "00000001.png", "rb") as f:
                    cutout_size = struct.unpack(">II", f.read(24)[16:24])  # PNG IHDR width, height

            # libwebp_anim only emits output when it finishes, so FFmpeg reports no progress for it.
            report = progress.stage(encode_lo, 1, f"Encoding {fmt.upper()}", busy=fmt == "webp")
            encode(ffmpeg, fmt, inputs, vf, fps, out, on_progress=timed(report, clip),
                   alpha=cutout_size if args.remove_bg else None, bg=args.bg_color, hdr=keep_hdr)
            report(1)
            size = out.stat().st_size / 1024 / 1024
            print(f"  done: {size:.2f} MB")
    finally:
        if args.keep_temp:
            print(f"\nTemp frames kept in {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()

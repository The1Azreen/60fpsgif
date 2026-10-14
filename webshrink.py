"""Shrink images and audio for the web without visibly losing quality.

  PNG  -> lossless WebP (every pixel identical, transparency kept), usually 20-40% smaller.
  JPEG -> WebP at the JPEG's own quality (read from its quantization tables), usually 25-35% smaller.
  MP3 / FLAC / WAV -> Opus (or AAC in .m4a). FLAC and WAV shrink ~85-90%; MP3 gets a lower bitrate
                      that keeps it sounding the same.

A file whose result wouldn't be smaller is skipped, so nothing ever gets bigger.
Originals are never touched. Everything runs through FFmpeg.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from fpsgif import NO_WINDOW, find_exe

IMAGE_EXTS = {".png", ".jpg", ".jpeg"}
AUDIO_EXTS = {".mp3", ".flac", ".wav"}
LOSSLESS_AUDIO = {"flac", "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le", "alac"}
# Stereo bitrates (kbps) that sound the same as the source for nearly everyone; mono gets half.
AUDIO = {"opus": {"codec": "libopus", "suffix": ".opus", "kbps": 128},
         "m4a": {"codec": "aac", "suffix": ".m4a", "kbps": 192}}
JPEG_QUALITY_CAP = 90  # above this WebP grows fast for no visible gain
DEFAULT_QUALITY = 85   # for JPEGs whose quality can't be read
# The standard JPEG luminance table that IJG/libjpeg scales by quality (sum is order-independent).
STD_LUMA_SUM = sum((16, 11, 10, 16, 24, 40, 51, 61, 12, 12, 14, 19, 26, 58, 60, 55,
                    14, 13, 16, 24, 40, 57, 69, 56, 14, 17, 22, 29, 51, 87, 80, 62,
                    18, 22, 37, 56, 68, 109, 103, 77, 24, 35, 55, 64, 81, 104, 113, 92,
                    49, 64, 78, 87, 103, 121, 120, 101, 72, 92, 95, 98, 112, 100, 103, 99))


def jpeg_quality(path):
    """Estimate the quality (1-100) a JPEG was saved at from its luminance table, or None."""
    data = Path(path).read_bytes()
    i = 2
    while i + 4 <= len(data) and data[i] == 0xFF:
        marker, length = data[i + 1], int.from_bytes(data[i + 2:i + 4], "big")
        if marker == 0xDB:  # DQT: first table is luminance
            precision, table_id = data[i + 4] >> 4, data[i + 4] & 0x0F
            if table_id != 0:
                return None
            size = 2 if precision else 1
            table = [int.from_bytes(data[i + 5 + k * size:i + 5 + (k + 1) * size], "big") for k in range(64)]
            scale = sum(table) * 100 / STD_LUMA_SUM
            quality = 5000 / scale if scale > 100 else (200 - scale) / 2
            return max(1, min(100, round(quality)))
        if marker == 0xDA:  # image data starts; no table found
            return None
        i += 2 + length
    return None


def probe_audio(ffprobe, path):
    out = subprocess.run([ffprobe, "-v", "error", "-select_streams", "a:0",
                          "-show_entries", "stream=codec_name,channels,bit_rate:format=bit_rate",
                          "-of", "json", str(path)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace",
                         creationflags=NO_WINDOW)
    info = json.loads(out.stdout or "{}")
    if not info.get("streams"):
        return None
    stream = info["streams"][0]
    bit_rate = stream.get("bit_rate") or info.get("format", {}).get("bit_rate")
    return {"codec": stream.get("codec_name"), "channels": int(stream.get("channels") or 2),
            "kbps": int(bit_rate) / 1000 if bit_rate else None}


def image_cmd(src, args):
    """Return (FFmpeg encoder args, description)."""
    if src.suffix.lower() == ".png" and not args.lossy_png:
        # bgra keeps libwebp in its lossless RGB path; compression_level 6 = smallest file.
        return ["-c:v", "libwebp", "-lossless", "1", "-compression_level", "6", "-quality", "100",
                "-pix_fmt", "bgra"], "lossless"
    if args.quality:
        quality, why = args.quality, f"q{args.quality}"
    elif src.suffix.lower() == ".png":
        quality, why = 90, "q90"
    else:
        found = jpeg_quality(src)
        quality = min(found, JPEG_QUALITY_CAP) if found else DEFAULT_QUALITY
        why = f"q{quality}, JPEG was q{found}" if found else f"q{quality}"
    return ["-c:v", "libwebp", "-quality", str(quality), "-compression_level", "6",
            "-pix_fmt", "bgra"], why


def audio_cmd(src, args, ffprobe):
    info = probe_audio(ffprobe, src)
    if not info:
        return None, "no audio stream"
    fmt = AUDIO[args.audio]
    if args.bitrate:
        kbps = args.bitrate
    else:
        kbps = fmt["kbps"] if info["channels"] >= 2 else fmt["kbps"] // 2
        if info["codec"] not in LOSSLESS_AUDIO and info["kbps"]:
            # A lossy source can't gain anything from more bits than it had; Opus/AAC need ~3/4 of MP3's.
            kbps = min(kbps, max(round(info["kbps"] * 0.75 / 8) * 8, 48))
    # Keep tags (title, artist...), drop embedded cover art.
    return ["-map", "0:a:0", "-map_metadata", "0", "-c:a", fmt["codec"], "-b:a", f"{kbps}k",
            *(["-movflags", "+faststart"] if args.audio == "m4a" else [])], f"{kbps} kbps"


def collect(paths):
    """Expand folders (recursively) into the image/audio files inside them, as (file, base folder)."""
    for p in map(Path, paths):
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file() and f.suffix.lower() in IMAGE_EXTS | AUDIO_EXTS:
                    yield f, p
        elif p.is_file():
            yield p, p.parent
        else:
            print(f"  not found: {p}")


def human(n):
    return f"{n / 1024 / 1024:.2f} MB" if n >= 1024 * 1024 else f"{n / 1024:.0f} KB"


def main():
    p = argparse.ArgumentParser(description="Convert PNG/JPEG to WebP and MP3/FLAC/WAV to Opus (or AAC) "
                                            "to save space on a website, keeping the same quality.")
    p.add_argument("inputs", nargs="+", help="files and/or folders (folders are searched recursively)")
    p.add_argument("-o", "--out-dir", help="write results here (keeping subfolders); default: next to each file")
    p.add_argument("-q", "--quality", type=int, choices=range(1, 101), metavar="1-100",
                   help="WebP quality for lossy images (default: match each JPEG's own quality)")
    p.add_argument("--lossy-png", action="store_true",
                   help="encode PNGs as lossy WebP (q90) instead of lossless; much smaller for photos")
    p.add_argument("--audio", choices=AUDIO, default="opus",
                   help="audio format: opus (smallest, default) or m4a (AAC, plays everywhere)")
    p.add_argument("-b", "--bitrate", type=int, help="audio bitrate in kbps (default: 128 Opus / 192 AAC stereo)")
    p.add_argument("--keep-larger", action="store_true", help="keep results even when not smaller than the original")
    args = p.parse_args()
    for stream in (sys.stdout, sys.stderr):
        if stream:
            stream.reconfigure(errors="replace")

    ffmpeg, ffprobe = find_exe("ffmpeg"), find_exe("ffprobe")
    if not ffmpeg or not ffprobe:
        sys.exit("FFmpeg/ffprobe not found. Install FFmpeg and add it to PATH.")

    before = after = done = skipped = failed = 0
    written = set()
    for src, base in collect(args.inputs):
        is_image = src.suffix.lower() in IMAGE_EXTS
        if not is_image and src.suffix.lower() not in AUDIO_EXTS:
            print(f"  skip {src.name}: not a PNG, JPEG, MP3, FLAC or WAV")
            skipped += 1
            continue
        suffix = ".webp" if is_image else AUDIO[args.audio]["suffix"]
        out = (Path(args.out_dir) / src.relative_to(base) if args.out_dir else src).with_suffix(suffix)
        if out.resolve() in written:  # e.g. logo.png and logo.jpg in one folder
            print(f"  skip {src.name}: {out.name} was already made from another file")
            skipped += 1
            continue

        enc, how = image_cmd(src, args) if is_image else audio_cmd(src, args, ffprobe)
        if enc is None:
            print(f"  skip {src.name}: {how}")
            skipped += 1
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), *enc, str(out)],
                                capture_output=True, text=True, encoding="utf-8", errors="replace",
                                creationflags=NO_WINDOW)
        if result.returncode != 0 or not out.is_file():
            print(f"  FAILED {src.name}: {result.stderr.strip().splitlines()[-1:] or ['unknown error']}")
            failed += 1
            continue

        old, new = src.stat().st_size, out.stat().st_size
        if new >= old and not args.keep_larger:
            out.unlink()
            print(f"  skip {src.name}: {suffix[1:].upper()} would be larger ({human(new)} vs {human(old)})")
            skipped += 1
            continue
        written.add(out.resolve())
        before, after, done = before + old, after + new, done + 1
        print(f"  {src.name} -> {out.name}  {human(old)} -> {human(new)}  ({(new - old) / old:+.0%}, {how})")

    print(f"\n{done} converted, {skipped} skipped, {failed} failed.", end="")
    if done:
        print(f"  {human(before)} -> {human(after)}  (saved {human(before - after)}, {(before - after) / before:.0%})")
    else:
        print()
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()

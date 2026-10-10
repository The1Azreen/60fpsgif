# 60fpsgif or try to anyways

Turn a short video into a buttery-smooth, high-frame-rate GIF (or WebP / APNG / MP4) using AI frame interpolation.

## Examples

**Before → after.** A 4 s clip dropped to a choppy 15 fps, then rebuilt to 50 fps by RIFE (140 of the 200 frames are AI-generated):

| Before: 15 fps (2.5 MB) | After: 50 fps with RIFE (8.1 MB) |
|---|---|
| ![15 fps original](examples/before_15fps.gif) | ![50 fps interpolated](examples/after_50fps.gif) |

```
python fpsgif.py clip_15fps.mp4 -w 360 -o examples/after_50fps.gif
```

**Same clip, every format.** 4 s at 360 px:

| Format | FPS | Size | File |
|---|---|---|---|
| GIF | 50 | 7.7 MB | [demo.gif](examples/demo.gif) |
| WebP | 60 | 2.2 MB | [demo.webp](examples/demo.webp) |
| MP4 (silent, loops like a GIF) | 60 | **0.25 MB** | [demo.mp4](examples/demo.mp4) |

```
python fpsgif.py video.mp4 -s 0 -t 4 -w 360 -o examples/demo.gif   # or .webp / .mp4
```

## Requirements

- Python 3.9+
- [FFmpeg](https://ffmpeg.org/) (`ffmpeg` and `ffprobe` on PATH)
- Optional but recommended: RIFE for AI interpolation — install with:

```
python setup_tools.py
```

This downloads [rife-ncnn-vulkan](https://github.com/nihui/rife-ncnn-vulkan) into `tools/`. It runs on any Vulkan-capable GPU (NVIDIA / AMD / Intel). Without it, the tool falls back to FFmpeg's `minterpolate` (CPU, more artifacts).

## Usage

```
python fpsgif.py clip.mp4                      # -> clip_50fps.gif
python fpsgif.py clip.mp4 -o smooth.webp       # true 60 fps WebP
python fpsgif.py clip.mp4 -o smooth.mp4        # tiny 60 fps "GIF video" (like Imgur's .gifv)
python fpsgif.py clip.mp4 -f all               # GIF + WebP + APNG + MP4
python fpsgif.py clip.mp4 -s 3 -t 4 -w 360     # 4 s starting at 0:03, 360 px wide
```

| Option | Meaning |
|---|---|
| `-o, --output` | Output file; extension picks the format |
| `-f, --format` | `gif` (default), `webp`, `apng`, `mp4`, or `all` |
| `--fps` | Target fps (default 50 for GIF, 60 for others) |
| `-w, --width` | Output width, `0` = original (default 480) |
| `-s, --start` / `-t, --duration` | Trim the clip |
| `--engine` | `auto`, `rife`, or `ffmpeg` |
| `--rife-model` | RIFE model folder (default `rife-v4.6`; try `rife-anime` for cartoons) |
| `--gpu` | GPU id for RIFE (`-1` = CPU) |

## Why GIFs are capped at 50 fps

GIF stores frame delays in 1/100 s units, so 60 fps (16.67 ms) can't be represented, and browsers slow any delay under 20 ms down to ~100 ms. **50 fps is the highest GIF rate that plays correctly everywhere.** For real 60 fps, use WebP (smaller, widely supported) or APNG (lossless, large).

## Why MP4 is so much smaller

Sites like Imgur don't actually serve GIFs — they convert them to silent, looping MP4 (`.gifv`). A video codec stores only what *changes* between frames, in full color, instead of a whole 256-color image per frame. The full 25 s source clip at 480 px came out at **85 MB as GIF, 20 MB as WebP, and 2.3 MB as MP4**. Use MP4 anywhere that plays video inline (Discord, X, Reddit, `<video autoplay loop muted playsinline>`); use GIF only where a real `.gif` is required.

## Tips

- Keep clips short (< 10 s) and narrow (≤ 480 px) — high-fps GIFs get big fast.
- Fast motion, scene cuts, and on-screen text are the hardest cases for interpolation.

"""Desktop front-end for fpsgif.py.

Double-click this file (or run `python fpsgif_gui.pyw`) to pick a video, set
options, and convert without touching the command line. The conversion runs
fpsgif.py in a subprocess and streams its output into the log pane.
"""

import base64
import ctypes
import math
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import fpsgif
import setup_tools

ROOT = fpsgif.ROOT
FROZEN = getattr(sys, "frozen", False)
ICON = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "icon.ico"

VIDEO_TYPES = [("Videos", "*.mp4 *.mov *.mkv *.webm *.avi *.m4v *.gif *.wmv *.flv"), ("All files", "*.*")]
SUFFIX = {"gif": ".gif", "webp": ".webp", "apng": ".png", "mp4": ".mp4"}
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

# Palette
BG = "#0f1117"
CARD = "#171a23"
BORDER = "#262b38"
FIELD = "#0c0e14"
HOVER = "#2f3546"
TEXT = "#e7e9f0"
MUTED = "#8a90a2"
FAINT = "#555c6e"
ACCENT = "#7c5cff"
ACCENT_HOVER = "#9278ff"
ACCENT_DIM = "#3b2f78"
ACCENT_SOFT = "#231d45"
GOOD = "#3ecf8e"
WARN = "#f5a524"
BAD = "#f2626b"

UI = "Segoe UI"
MONO = "Consolas"
SCALE = 1.0  # set from the screen DPI once Tk starts


def px(n):
    return round(n * SCALE)


def python_exe():
    # pythonw.exe has no console; use python.exe for the child so output is captured normally.
    exe = Path(sys.executable)
    if exe.stem.lower() == "pythonw":
        console = exe.with_name("python.exe")
        if console.is_file():
            return str(console)
    return str(exe)


def child_cmd(task):
    """Command that runs fpsgif.py ("cli") or setup_tools.py ("setup") in a subprocess.

    A packaged .exe has no Python to hand the scripts to, so it re-launches itself with a flag instead.
    """
    if FROZEN:
        return [sys.executable, f"--run-{task}"]
    script = {"cli": fpsgif.__file__, "setup": setup_tools.__file__}[task]
    return [python_exe(), "-u", str(script)]


def run_child(task):
    """Entry point for `<exe> --run-cli ...` / `<exe> --run-setup`."""
    sys.stdout.reconfigure(line_buffering=True)
    sys.argv = [sys.argv[0], *sys.argv[2:]]
    (fpsgif.main if task == "cli" else setup_tools.main)()


def open_path(path):
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def reveal(path):
    path = Path(path)
    if sys.platform == "win32" and path.is_file():
        subprocess.Popen(["explorer", "/select,", str(path)])
    else:
        open_path(str(path if path.is_dir() else path.parent))


def setup_style(root):
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=BG, foreground=TEXT, font=(UI, 10), bordercolor=BORDER,
                    lightcolor=BG, darkcolor=BG, troughcolor=FIELD, focuscolor=ACCENT,
                    selectbackground=ACCENT, selectforeground="white", fieldbackground=FIELD,
                    insertcolor=TEXT)

    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=CARD)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("Title.TLabel", font=(UI, 20, "bold"))
    style.configure("Subtitle.TLabel", foreground=MUTED, font=(UI, 10))
    style.configure("Card.TLabel", background=CARD)
    style.configure("Section.TLabel", background=CARD, font=(UI, 11, "bold"))
    style.configure("Field.TLabel", background=CARD, foreground=MUTED, font=(UI, 9))
    style.configure("Hint.TLabel", background=CARD, foreground=FAINT, font=(UI, 9))
    style.configure("Status.TLabel", foreground=MUTED, font=(UI, 9))

    field = dict(fieldbackground=FIELD, background=FIELD, foreground=TEXT, bordercolor=BORDER,
                 lightcolor=FIELD, darkcolor=FIELD, arrowcolor=MUTED, arrowsize=px(12), insertcolor=TEXT,
                 padding=(px(10), px(6)))
    focus = dict(bordercolor=[("focus", ACCENT)], lightcolor=[("focus", FIELD)])
    for name in ("TEntry", "TCombobox", "TSpinbox"):
        style.configure(name, **field)
        style.map(name, **focus)
    style.map("TCombobox", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", TEXT)],
              selectbackground=[("readonly", FIELD)], selectforeground=[("readonly", TEXT)],
              background=[("active", FIELD)], arrowcolor=[("active", TEXT)], **focus)
    style.map("TSpinbox", background=[("active", FIELD)], arrowcolor=[("active", TEXT)], **focus)
    root.option_add("*TCombobox*Listbox.background", CARD)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", "white")
    root.option_add("*TCombobox*Listbox.font", (UI, 10))
    root.option_add("*TCombobox*Listbox.borderWidth", 0)

    flat = lambda color: dict(background=color, bordercolor=color, lightcolor=color, darkcolor=color)

    def state_map(normal, hover, pressed, disabled):
        return {k: [("disabled", disabled), ("pressed", pressed), ("active", hover), ("!disabled", normal)]
                for k in ("background", "bordercolor", "lightcolor", "darkcolor")}

    style.configure("TButton", **flat(BORDER), foreground=TEXT, padding=(px(14), px(8)), font=(UI, 10))
    style.map("TButton", foreground=[("disabled", FAINT)], **state_map(BORDER, HOVER, HOVER, CARD))
    style.configure("Card.TButton", **flat(BORDER), foreground=TEXT, padding=(px(14), px(6)))
    style.map("Card.TButton", foreground=[("disabled", FAINT)], **state_map(BORDER, HOVER, HOVER, CARD))
    style.configure("Accent.TButton", **flat(ACCENT), foreground="white", padding=(px(26), px(10)),
                    font=(UI, 11, "bold"))
    style.map("Accent.TButton", foreground=[("disabled", "#a99ee0")],
              **state_map(ACCENT, ACCENT_HOVER, ACCENT_DIM, ACCENT_DIM))
    style.configure("Link.TButton", **flat(CARD), foreground=ACCENT, padding=(0, px(2)), font=(UI, 9, "bold"))
    style.map("Link.TButton", foreground=[("active", ACCENT_HOVER)], **state_map(CARD, CARD, CARD, CARD))
    style.configure("Small.TButton", **flat(ACCENT_SOFT), foreground=ACCENT_HOVER, padding=(px(10), px(4)),
                    font=(UI, 9, "bold"))
    style.map("Small.TButton", **state_map(ACCENT_SOFT, ACCENT_DIM, ACCENT_DIM, BG))

    style.configure("Seg.Toolbutton", **flat(FIELD), foreground=MUTED, padding=(px(8), px(7)),
                    anchor="center", font=(UI, 10, "bold"))
    style.map("Seg.Toolbutton",
              foreground=[("selected", "white"), ("active", TEXT)],
              **{k: [("selected", ACCENT), ("active", BORDER), ("!selected", FIELD)]
                 for k in ("background", "bordercolor", "lightcolor", "darkcolor")})

    style.configure("Card.TCheckbutton", background=CARD, foreground=TEXT, indicatorbackground=FIELD,
                    indicatorforeground="white", indicatorrelief="flat", indicatorsize=px(14), indicatormargin=(0, 0, px(8), 0),
                    upperbordercolor=BORDER, lowerbordercolor=BORDER)
    style.map("Card.TCheckbutton", background=[("active", CARD)],
              indicatorbackground=[("selected", ACCENT), ("active", BORDER)])

    style.configure("Accent.Horizontal.TProgressbar", troughcolor=CARD, background=ACCENT,
                    bordercolor=CARD, lightcolor=ACCENT, darkcolor=ACCENT, thickness=px(8))

    style.layout("Dark.Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
        ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
    style.configure("Dark.Vertical.TScrollbar", troughcolor=FIELD, gripcount=0, **flat(BORDER))
    style.map("Dark.Vertical.TScrollbar", background=[("active", HOVER)])


class DropZone(tk.Canvas):
    """Big clickable area for choosing the input video."""

    def __init__(self, parent, command):
        super().__init__(parent, height=px(84), bg=CARD, highlightthickness=0, cursor="hand2")
        self.command = command
        self.title, self.subtitle = "Choose a video", "Click to browse  ·  MP4, MOV, MKV, WebM, AVI…"
        self.has_file = False
        self.hover = False
        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Enter>", lambda e: self.set_hover(True))
        self.bind("<Leave>", lambda e: self.set_hover(False))
        self.bind("<Button-1>", lambda e: self.command())

    def set_hover(self, value):
        self.hover = value
        self.draw()

    def set_text(self, title, subtitle, has_file=True):
        self.title, self.subtitle, self.has_file = title, subtitle, has_file
        self.draw()

    def draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 10:
            return
        outline = ACCENT if self.hover else (ACCENT_DIM if self.has_file else BORDER)
        self.create_rectangle(1, 1, w - 2, h - 2, fill=ACCENT_SOFT if self.hover else FIELD,
                              outline=outline, dash=() if self.has_file else (6, 4), width=1)
        r, cx, cy = px(22), px(50), h // 2
        self.create_oval(cx - r, cy - r, cx + r, cy + r, fill=ACCENT_DIM if self.has_file else ACCENT_SOFT,
                         outline="")
        if self.has_file:
            self.create_polygon(cx - px(7), cy - px(10), cx - px(7), cy + px(10), cx + px(11), cy, fill="white", outline="")
        else:
            self.create_text(cx, cy - 1, text="+", fill=ACCENT_HOVER, font=(UI, 22))
        x, wrap = px(88), w - px(104)
        self.create_text(x, cy, text=self.title, anchor="sw", fill=TEXT, font=(UI, 12, "bold"), width=wrap)
        self.create_text(x, cy + px(3), text=self.subtitle, anchor="nw", fill=MUTED, font=(UI, 9), width=wrap)


def fmt_time(t):
    m, s = divmod(max(t, 0), 60)
    return f"{int(m)}:{s:05.2f}"


class FrameSource:
    """Grabs preview frames and filmstrips with FFmpeg on a background thread.

    Only the newest request of each kind is kept, so scrubbing never queues up stale frames.
    Results land in .results as (kind, args, base64 PNG or None) for the Tk thread to pick up.
    """

    def __init__(self):
        self.cv = threading.Condition()
        self.pending = {}
        self.results = queue.Queue()
        threading.Thread(target=self.loop, daemon=True).start()

    def request(self, kind, *args):
        with self.cv:
            self.pending[kind] = args
            self.cv.notify()

    def loop(self):
        while True:
            with self.cv:
                while not self.pending:
                    self.cv.wait()
                kind = "frame" if "frame" in self.pending else next(iter(self.pending))
                args = self.pending.pop(kind)
            self.results.put((kind, args, self.grab(kind, *args)))

    @staticmethod
    def grab(kind, path, *rest):
        if kind == "frame":
            t, w, h = rest
            pre, vf = ["-ss", f"{t:.3f}"], f"scale={w}:{h}:force_original_aspect_ratio=decrease"
        else:  # filmstrip from keyframes only: fast even for long videos
            duration, n, tw, th = rest
            pre = ["-skip_frame", "nokey"]
            vf = (f"fps={n}/{duration:.3f},scale={tw}:{th}:force_original_aspect_ratio=increase,"
                  f"crop={tw}:{th},tile={n}x1")
        cmd = [fpsgif.find_exe("ffmpeg"), "-hide_banner", "-loglevel", "error", *pre, "-i", path, "-an",
               "-vf", vf, "-frames:v", "1", "-f", "image2pipe", "-c:v", "png", "-"]
        try:
            out = subprocess.run(cmd, capture_output=True, timeout=30, creationflags=NO_WINDOW).stdout
        except Exception:
            out = b""
        return base64.b64encode(out).decode() if out else None


class TrimPanel(ttk.Frame):
    """Frame preview plus a timeline with draggable start/end handles, synced to the trim fields."""

    STRIP_H = 44

    def __init__(self, parent, start_var, duration_var):
        super().__init__(parent, style="Card.TFrame")
        self.start_var, self.duration_var = start_var, duration_var
        self.path, self.duration, self.fps = None, 0.0, 30.0
        self.start = self.end = self.head = 0.0
        self.drag = None
        self.syncing = False
        self.frame_img = self.strip_img = None
        self.strip_key = None
        self.jobs = {}
        self.frames = FrameSource()

        self.preview = tk.Canvas(self, bg=FIELD, height=px(180), highlightthickness=1, highlightbackground=BORDER)
        self.preview.pack(fill="both", expand=True)
        self.timeline = tk.Canvas(self, bg=CARD, height=px(self.STRIP_H + 16), highlightthickness=0,
                                  cursor="hand2", takefocus=1)
        self.timeline.pack(fill="x", pady=(px(10), 0))

        bar = ttk.Frame(self, style="Card.TFrame")
        bar.pack(fill="x", pady=(px(8), 0))
        ttk.Button(bar, text="Set start", style="Card.TButton", command=lambda: self.set_edge("start")).pack(side="left")
        ttk.Button(bar, text="Set end", style="Card.TButton", command=lambda: self.set_edge("end")).pack(
            side="left", padx=(px(8), 0))
        ttk.Button(bar, text="Whole video", style="Card.TButton", command=self.reset).pack(side="left", padx=(px(8), 0))
        self.range_label = tk.Label(bar, bg=CARD, fg=TEXT, font=(UI, 10))
        self.range_label.pack(side="right")
        ttk.Label(self, text="Drag the purple handles to trim  ·  click to scrub  ·  ← / → one frame (Shift: 1 s)",
                  style="Hint.TLabel").pack(anchor="w", pady=(px(6), 0))

        self.preview.bind("<Configure>", lambda e: (self.draw_preview(), self.debounce("frame", 150, self.request_frame)))
        self.timeline.bind("<Configure>", lambda e: (self.draw(), self.debounce("strip", 300, self.request_strip)))
        self.timeline.bind("<ButtonPress-1>", self.press)
        self.timeline.bind("<B1-Motion>", self.motion)
        self.timeline.bind("<ButtonRelease-1>", lambda e: setattr(self, "drag", None))
        for key, step in (("<Left>", -1), ("<Right>", 1)):
            self.timeline.bind(key, lambda e, s=step: self.step(s / self.fps))
            self.timeline.bind(f"<Shift-{key[1:]}", lambda e, s=step: self.step(s))
        for var in (start_var, duration_var):
            var.trace_add("write", lambda *_: self.from_fields())
        self.after(30, self.poll)

    # ----- data -----

    def load(self, path, info):
        self.path, self.duration = path, info["duration"]
        self.fps = info["fps"] or 30.0
        self.start, self.end, self.head = 0.0, self.duration, 0.0
        self.frame_img = self.strip_img = None
        self.strip_key = None
        self.from_fields()  # keep any trim already typed in
        self.head = self.start
        self.draw()
        self.draw_preview()
        self.request_strip()
        self.request_frame()

    def debounce(self, name, ms, fn):
        if name in self.jobs:
            self.after_cancel(self.jobs[name])
        self.jobs[name] = self.after(ms, fn)

    def request_frame(self):
        if not self.path:
            return
        w, h = self.preview.winfo_width() - 2, self.preview.winfo_height() - 2
        if w > 10 and h > 10:
            t = min(self.head, max(self.duration - 1.5 / self.fps, 0))  # seeking past the last frame yields nothing
            self.frames.request("frame", self.path, round(t, 3), w, h)

    def request_strip(self):
        if not self.path:
            return
        x0, x1 = self.track()
        th = px(self.STRIP_H)
        tw = round(th * 16 / 9)
        n = max(1, math.ceil((x1 - x0) / tw))
        key = (self.path, n, tw, th)
        if key != self.strip_key:
            self.strip_key = key
            self.frames.request("strip", self.path, self.duration, n, tw, th)

    def poll(self):
        try:
            while True:
                kind, args, data = self.frames.results.get_nowait()
                if args[0] != self.path:
                    continue
                img = tk.PhotoImage(data=data) if data else None
                if kind == "frame":
                    self.frame_img = img
                    self.draw_preview()
                else:
                    x0, x1 = self.track()
                    if img and img.width() > x1 - x0:  # crop the strip to the track width
                        crop = tk.PhotoImage()
                        crop.tk.call(crop, "copy", img, "-from", 0, 0, x1 - x0, img.height())
                        img = crop
                    self.strip_img = img
                    self.draw()
        except queue.Empty:
            pass
        self.after(30, self.poll)

    # ----- trim fields <-> handles -----

    def min_len(self):
        return min(2 / self.fps, self.duration)

    def to_fields(self):
        self.syncing = True
        self.start_var.set("" if self.start < 0.005 else f"{self.start:.2f}")
        self.duration_var.set("" if self.end > self.duration - 0.005 else f"{self.end - self.start:.2f}")
        self.syncing = False
        self.update_label()

    def from_fields(self):
        if self.syncing or not self.duration:
            return
        try:
            s = fpsgif.parse_time(self.start_var.get().strip()) if self.start_var.get().strip() else 0.0
            d = float(self.duration_var.get()) if self.duration_var.get().strip() else None
        except ValueError:
            return  # half-typed value; wait for the next keystroke
        self.start = min(max(s, 0.0), max(self.duration - self.min_len(), 0.0))
        self.end = self.duration if d is None else min(max(self.start + d, self.start + self.min_len()), self.duration)
        self.draw()

    def update_label(self):
        if self.duration:
            self.range_label.config(text=f"{fmt_time(self.start)}  →  {fmt_time(self.end)}    "
                                         f"{self.end - self.start:.2f} s")

    def set_edge(self, edge):
        if not self.duration:
            return
        if edge == "start":
            self.start = min(self.head, self.end - self.min_len())
        else:
            self.end = max(self.head, self.start + self.min_len())
        self.to_fields()
        self.draw()

    def reset(self):
        if self.duration:
            self.start, self.end = 0.0, self.duration
            self.to_fields()
            self.draw()

    # ----- timeline interaction -----

    def track(self):
        pad = px(10)
        return pad, max(self.timeline.winfo_width() - pad, pad + 1)

    def t2x(self, t):
        x0, x1 = self.track()
        return x0 + (x1 - x0) * (t / self.duration if self.duration else 0)

    def x2t(self, x):
        x0, x1 = self.track()
        return min(max((x - x0) / (x1 - x0), 0), 1) * self.duration

    def press(self, e):
        self.timeline.focus_set()
        if not self.duration:
            return
        hw = px(8)
        ds, de = abs(e.x - (self.t2x(self.start) - hw / 2)), abs(e.x - (self.t2x(self.end) + hw / 2))
        if min(ds, de) <= px(10):
            self.drag = "start" if ds <= de else "end"
        else:
            self.drag = "head"
        self.motion(e)

    def motion(self, e):
        if not self.duration or not self.drag:
            return
        t = self.x2t(e.x)
        if self.drag == "start":
            self.start = self.head = min(t, self.end - self.min_len())
        elif self.drag == "end":
            self.end = self.head = max(t, self.start + self.min_len())
        else:
            self.head = t
        if self.drag != "head":
            self.to_fields()
        self.draw()
        self.draw_preview()
        self.request_frame()

    def step(self, delta):
        if self.duration:
            self.head = min(max(self.head + delta, 0), self.duration)
            self.draw()
            self.draw_preview()
            self.request_frame()

    # ----- drawing -----

    def draw(self):
        c = self.timeline
        c.delete("all")
        x0, x1 = self.track()
        y0, y1 = px(8), px(8 + self.STRIP_H)
        c.create_rectangle(x0, y0, x1, y1, fill=FIELD, outline=BORDER)
        if not self.duration:
            c.create_text((x0 + x1) / 2, (y0 + y1) / 2, text="Timeline appears once a video is chosen",
                          fill=FAINT, font=(UI, 9))
            return
        if self.strip_img:
            c.create_image(x0, y0, image=self.strip_img, anchor="nw")
        sx, ex = self.t2x(self.start), self.t2x(self.end)
        for a, b in ((x0, sx), (ex, x1)):  # dim what's outside the selection
            if b > a:
                c.create_rectangle(a, y0, b, y1, fill="black", stipple="gray75", outline="")
        c.create_rectangle(sx, y0, ex, y1, outline=ACCENT, width=px(2))
        hw = px(8)
        for a, b in ((sx - hw, sx), (ex, ex + hw)):
            c.create_rectangle(a, y0 - px(2), b, y1 + px(2), fill=ACCENT, outline="")
            mid = (a + b) / 2
            c.create_line(mid, y0 + px(14), mid, y1 - px(14), fill="white", width=max(1, px(1)))
        hx = self.t2x(self.head)
        c.create_line(hx, y0 - px(4), hx, y1 + px(4), fill="white", width=px(2))
        c.create_polygon(hx - px(5), y0 - px(8), hx + px(5), y0 - px(8), hx, y0 - px(2), fill="white", outline="")
        self.update_label()

    def draw_preview(self):
        c = self.preview
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if not self.path:
            c.create_text(w / 2, h / 2, text="Choose a video to preview and trim it", fill=FAINT, font=(UI, 11))
            return
        if self.frame_img:
            c.create_image(w / 2, h / 2, image=self.frame_img)
        else:
            c.create_text(w / 2, h / 2, text="Loading preview…", fill=MUTED, font=(UI, 10))
        if self.duration:
            text = c.create_text(px(14), h - px(12), anchor="sw", fill="white", font=(UI, 10, "bold"),
                                 text=f"{fmt_time(self.head)} / {fmt_time(self.duration)}")
            bx0, by0, bx1, by1 = c.bbox(text)
            bg = c.create_rectangle(bx0 - px(8), by0 - px(4), bx1 + px(8), by1 + px(4), fill="#000000",
                                    stipple="gray50", outline="")
            c.tag_lower(bg, text)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        global SCALE
        SCALE = self.winfo_fpixels("1i") / 96
        self.title("60fps GIF Maker")
        self.configure(bg=BG)
        if sys.platform == "win32" and ICON.is_file():
            self.iconbitmap(default=str(ICON))
        w, h = min(px(1240), self.winfo_screenwidth() - px(40)), min(px(860), self.winfo_screenheight() - px(80))
        self.geometry(f"{w}x{h}+{max((self.winfo_screenwidth() - w) // 2, 0)}+{max((self.winfo_screenheight() - h) // 3, 0)}")
        self.minsize(min(px(1000), w), min(px(700), h))
        setup_style(self)
        self.dark_title_bar()

        self.proc = None
        self.cancelled = False
        self.outputs = []
        self.lines = queue.Queue()

        self.input_var = tk.StringVar()
        self.outdir_var = tk.StringVar()
        self.format_var = tk.StringVar(value="gif")
        self.fps_var = tk.StringVar()
        self.width_var = tk.StringVar(value="480")
        self.start_var = tk.StringVar()
        self.duration_var = tk.StringVar()
        self.engine_var = tk.StringVar(value="auto")
        self.model_var = tk.StringVar(value="rife-v4.6")
        self.gpu_var = tk.StringVar()
        self.verbose_var = tk.BooleanVar()
        self.advanced_open = False

        self.build()
        self.refresh_tools()
        self.format_var.trace_add("write", lambda *_: self.update_fps_hint())
        self.update_fps_hint()
        self.after(100, self.drain_log)

    def dark_title_bar(self):
        if sys.platform != "win32":
            return
        try:
            self.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            value = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
        except Exception:
            pass

    # ---------- layout ----------

    def card(self, parent, title=None, right=None):
        outer = tk.Frame(parent, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
        body = ttk.Frame(outer, style="Card.TFrame", padding=(px(18), px(14), px(18), px(16)))
        body.pack(fill="both", expand=True)
        if title:
            head = ttk.Frame(body, style="Card.TFrame")
            head.pack(fill="x", pady=(0, 10))
            ttk.Label(head, text=title, style="Section.TLabel").pack(side="left")
            if right:
                right(head).pack(side="right")
        inner = ttk.Frame(body, style="Card.TFrame")
        inner.pack(fill="both", expand=True)
        return outer, inner

    def field(self, parent, label, widget_factory, row, col, colspan=1):
        box = ttk.Frame(parent, style="Card.TFrame")
        box.grid(row=row, column=col, columnspan=colspan, sticky="ew", padx=(0 if col == 0 else 10, 0), pady=(0, 4))
        ttk.Label(box, text=label, style="Field.TLabel").pack(anchor="w", pady=(0, 3))
        widget = widget_factory(box)
        widget.pack(fill="x")
        return widget

    def build(self):
        root = ttk.Frame(self, padding=(px(22), px(18)))
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=11, uniform="cols")
        root.columnconfigure(1, weight=9, uniform="cols")
        root.rowconfigure(1, weight=1)

        # Header
        header = ttk.Frame(root)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 14))
        ttk.Label(header, text="60fps GIF Maker", style="Title.TLabel").pack(anchor="w")
        ttk.Label(header, text="Turn short clips into buttery-smooth GIF, WebP, APNG or MP4 "
                               "with AI frame interpolation.", style="Subtitle.TLabel").pack(anchor="w")

        # Left column: source + trim. Right column: settings, actions, output.
        left = ttk.Frame(root)
        left.grid(row=1, column=0, sticky="nsew", padx=(0, px(8)))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)
        main = ttk.Frame(root)
        main.grid(row=1, column=1, sticky="nsew", padx=(px(8), 0))
        main.columnconfigure(0, weight=1)

        # Source
        src_card, src = self.card(left, "Source")
        src_card.grid(row=0, column=0, sticky="ew")
        self.drop = DropZone(src, self.pick_input)
        self.drop.pack(fill="x")
        save = ttk.Frame(src, style="Card.TFrame")
        save.pack(fill="x", pady=(14, 0))
        save.columnconfigure(0, weight=1)
        ttk.Label(save, text="Save to", style="Field.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 3))
        ttk.Entry(save, textvariable=self.outdir_var).grid(row=1, column=0, sticky="ew")
        ttk.Button(save, text="Browse", style="Card.TButton", command=self.pick_outdir).grid(
            row=1, column=1, padx=(10, 0))
        ttk.Label(save, text="Leave blank to save next to the input video.", style="Hint.TLabel").grid(
            row=2, column=0, sticky="w", pady=(4, 0))

        # Trim
        trim_card, trim = self.card(left, "Preview & trim")
        trim_card.grid(row=1, column=0, sticky="nsew", pady=(14, 0))
        self.trim = TrimPanel(trim, self.start_var, self.duration_var)
        self.trim.pack(fill="both", expand=True)

        # Settings
        set_card, opts = self.card(main, "Settings")
        set_card.grid(row=0, column=0, sticky="ew")
        for c in range(4):
            opts.columnconfigure(c, weight=1, uniform="opts")

        ttk.Label(opts, text="Format", style="Field.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 3))
        seg = tk.Frame(opts, bg=BORDER, padx=1, pady=1)
        seg.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(0, 12))
        labels = {"gif": "GIF", "webp": "WebP", "apng": "APNG", "mp4": "MP4", "all": "All"}
        for i, (value, text) in enumerate(labels.items()):
            seg.columnconfigure(i, weight=1, uniform="seg")
            ttk.Radiobutton(seg, text=text, value=value, variable=self.format_var,
                            style="Seg.Toolbutton").grid(row=0, column=i, sticky="ew")

        self.field(opts, "Frame rate (fps)",
                   lambda p: ttk.Spinbox(p, textvariable=self.fps_var, from_=1, to=240, width=6), 2, 0)
        self.field(opts, "Width (px)",
                   lambda p: ttk.Spinbox(p, textvariable=self.width_var, from_=0, to=3840, increment=40,
                                         width=6), 2, 1)
        self.field(opts, "Start time",
                   lambda p: ttk.Entry(p, textvariable=self.start_var, width=8), 2, 2)
        self.field(opts, "Duration (s)",
                   lambda p: ttk.Entry(p, textvariable=self.duration_var, width=8), 2, 3)
        self.fps_hint = ttk.Label(opts, style="Hint.TLabel", wraplength=px(460))
        self.fps_hint.grid(row=3, column=0, columnspan=4, sticky="w", pady=(4, 0))

        self.adv_btn = ttk.Button(opts, text="▸  Advanced", style="Link.TButton", command=self.toggle_advanced)
        self.adv_btn.grid(row=4, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self.adv = ttk.Frame(opts, style="Card.TFrame")
        self.adv.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        for c in range(4):
            self.adv.columnconfigure(c, weight=1, uniform="adv")
        self.field(self.adv, "Engine",
                   lambda p: ttk.Combobox(p, textvariable=self.engine_var, state="readonly", width=8,
                                          values=("auto", "rife", "ffmpeg")), 0, 0)
        self.model_box = self.field(self.adv, "RIFE model",
                                    lambda p: ttk.Combobox(p, textvariable=self.model_var, width=12), 0, 1)
        self.field(self.adv, "GPU id", lambda p: ttk.Entry(p, textvariable=self.gpu_var, width=6), 0, 2)
        chk = ttk.Frame(self.adv, style="Card.TFrame")
        chk.grid(row=0, column=3, sticky="sw", padx=(10, 0), pady=(0, 10))
        ttk.Checkbutton(chk, text="Verbose log", variable=self.verbose_var,
                        style="Card.TCheckbutton").pack(anchor="w")
        ttk.Label(self.adv, text="Start accepts seconds or hh:mm:ss.  GPU id: blank = default, -1 = CPU.  "
                                 "Try rife-anime for cartoons.", style="Hint.TLabel", wraplength=px(460)).grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(4, 0))
        self.adv.grid_remove()

        # Tools status
        status = ttk.Frame(main)
        status.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        self.ffmpeg_pill = self.pill(status)
        self.ffmpeg_pill.pack(side="left")
        self.rife_pill = self.pill(status)
        self.rife_pill.pack(side="left", padx=(16, 0))
        self.install_btn = ttk.Button(status, text="Install RIFE", style="Small.TButton", command=self.install_rife)
        self.install_btn.pack(side="right")

        # Actions
        actions = ttk.Frame(main)
        actions.grid(row=2, column=0, sticky="ew", pady=(14, 0))
        self.convert_btn = ttk.Button(actions, text="Convert", style="Accent.TButton", command=self.convert)
        self.convert_btn.pack(side="left")
        self.cancel_btn = ttk.Button(actions, text="Cancel", command=self.cancel, state="disabled")
        self.cancel_btn.pack(side="left", padx=(10, 0))
        self.folder_btn = ttk.Button(actions, text="Show in folder", command=self.show_folder, state="disabled")
        self.folder_btn.pack(side="right")
        self.open_btn = ttk.Button(actions, text="Open result", command=self.open_result, state="disabled")
        self.open_btn.pack(side="right", padx=(0, 10))

        # Log
        log_card, log = self.card(main, "Output", right=lambda p: self.make_state_label(p))
        log_card.grid(row=3, column=0, sticky="nsew", pady=(14, 0))
        main.rowconfigure(3, weight=1)
        prog = ttk.Frame(log, style="Card.TFrame")
        prog.pack(fill="x", pady=(0, 6))
        self.stage_label = tk.Label(prog, text="Waiting to start", bg=CARD, fg=MUTED, font=(UI, 10), anchor="w")
        self.stage_label.pack(side="left", fill="x", expand=True)
        self.pct_label = tk.Label(prog, text="", bg=CARD, fg=ACCENT_HOVER, font=(UI, 11, "bold"))
        self.pct_label.pack(side="right")
        self.progress = ttk.Progressbar(log, mode="determinate", style="Accent.Horizontal.TProgressbar")
        self.progress.pack(fill="x", pady=(0, 12))
        wrap = tk.Frame(log, bg=FIELD, highlightthickness=1, highlightbackground=BORDER)
        wrap.pack(fill="both", expand=True)
        self.log = tk.Text(wrap, height=8, bg=FIELD, fg=MUTED, font=(MONO, 9), relief="flat", bd=0,
                           padx=12, pady=10, wrap="word", state="disabled", insertbackground=TEXT,
                           selectbackground=ACCENT_DIM, highlightthickness=0)
        scroll = ttk.Scrollbar(wrap, orient="vertical", command=self.log.yview, style="Dark.Vertical.TScrollbar")
        self.log.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        self.log.tag_configure("cmd", foreground=FAINT)
        self.log.tag_configure("step", foreground=ACCENT_HOVER, font=(MONO, 9, "bold"))
        self.log.tag_configure("good", foreground=GOOD)
        self.log.tag_configure("bad", foreground=BAD)
        self.log.tag_configure("info", foreground=TEXT)
        self.write_log("Choose a video and press Convert.\n", "cmd")

    def make_state_label(self, parent):
        self.state_label = tk.Label(parent, text="Idle", bg=CARD, fg=MUTED, font=(UI, 9))
        return self.state_label

    def pill(self, parent):
        frame = ttk.Frame(parent)
        frame.dot = tk.Label(frame, text="●", bg=BG, font=(UI, 9))
        frame.dot.pack(side="left")
        frame.text = ttk.Label(frame, style="Status.TLabel")
        frame.text.pack(side="left", padx=(4, 0))
        return frame

    def set_pill(self, pill, ok, text, color=None):
        pill.dot.config(fg=color or (GOOD if ok else BAD))
        pill.text.config(text=text)

    def set_state(self, text, color=MUTED):
        self.state_label.config(text=text, fg=color)

    def toggle_advanced(self):
        self.advanced_open = not self.advanced_open
        if self.advanced_open:
            self.adv.grid()
        else:
            self.adv.grid_remove()
        self.adv_btn.config(text=("▾" if self.advanced_open else "▸") + "  Advanced")

    # ---------- helpers ----------

    def refresh_tools(self):
        ffmpeg = fpsgif.find_exe("ffmpeg") and fpsgif.find_exe("ffprobe")
        rife = fpsgif.find_exe("rife-ncnn-vulkan")
        if rife:
            models = sorted(p.name for p in Path(rife).parent.iterdir() if p.is_dir() and p.name.startswith("rife"))
            self.model_box["values"] = models
            self.install_btn.pack_forget()
        elif not self.install_btn.winfo_ismapped():
            self.install_btn.pack(side="right")
        self.set_pill(self.ffmpeg_pill, ffmpeg, "FFmpeg ready" if ffmpeg else "FFmpeg not found — install it and add to PATH")
        self.set_pill(self.rife_pill, rife, "RIFE AI interpolation" if rife else "RIFE not installed (FFmpeg fallback)",
                      None if rife else WARN)

    def update_fps_hint(self):
        fmt = self.format_var.get()
        if fmt == "all":
            hint = "Leave fps blank for 50 fps GIF and 60 fps for the rest."
        else:
            hint = f"Leave fps blank for {fpsgif.DEFAULT_FPS[fmt]} fps."
            if fmt == "gif":
                hint += "  GIF can't play reliably above 50 fps — use WebP or MP4 for true 60."
        self.fps_hint.config(text=hint)

    def write_log(self, text, tag=None):
        if tag is None:
            s = text.strip()
            tag = ("cmd" if s.startswith("$") else "step" if s.startswith("[") else
                   "good" if s.startswith("done") or s == "Finished." else
                   "bad" if s.startswith(("Failed", "Command failed", "Error", "Traceback")) else "info")
        self.log.config(state="normal")
        self.log.insert("end", text, tag)
        self.log.see("end")
        self.log.config(state="disabled")

    def drain_log(self):
        try:
            while True:
                item = self.lines.get_nowait()
                if item is None:
                    self.finished()
                    continue
                if item.startswith(("@@progress ", "@@busy ")):
                    kind, pct, label = item.rstrip().split(" ", 2)
                    self.show_progress(float(pct), label, busy=kind == "@@busy")
                    continue
                self.write_log(item)
                m = re.search(r"\] -> (.+)$", item.rstrip())
                if m:
                    self.outputs.append(m.group(1))
        except queue.Empty:
            pass
        self.after(100, self.drain_log)

    def show_progress(self, pct, label, busy=False):
        mode = str(self.progress["mode"])
        if busy and mode != "indeterminate":
            self.progress.config(mode="indeterminate")
            self.progress.start(10)
        elif not busy:
            if mode != "determinate":
                self.progress.stop()
                self.progress.config(mode="determinate")
            self.progress.config(value=pct)
        self.stage_label.config(text=label + "…", fg=TEXT)
        self.pct_label.config(text=f"{pct:.0f}%")
        self.title(f"{pct:.0f}% · 60fps GIF Maker")

    def pick_input(self):
        if self.proc:
            return
        path = filedialog.askopenfilename(title="Choose a video", filetypes=VIDEO_TYPES)
        if path:
            self.input_var.set(path)
            self.load_info(path)

    def pick_outdir(self):
        path = filedialog.askdirectory(title="Choose output folder")
        if path:
            self.outdir_var.set(path)

    def load_info(self, path):
        name = Path(path).name
        self.drop.set_text(name, "Reading video info…")
        ffprobe = fpsgif.find_exe("ffprobe")

        def work():
            try:
                info = fpsgif.probe(ffprobe, path)
                text = (f"{info['width']} × {info['height']}   ·   {info['fps']:.2f} fps   ·   "
                        f"{info['duration']:.2f} s      Click to change")
                self.after(0, self.trim.load, path, info)
            except (SystemExit, Exception):
                text = "Could not read video info (is FFmpeg installed?)"
            self.after(0, self.drop.set_text, name, text)

        threading.Thread(target=work, daemon=True).start()

    def output_arg(self, inp):
        """Build the -o path for a custom output folder, mirroring the CLI's own naming."""
        outdir = self.outdir_var.get().strip()
        if not outdir:
            return []
        fmt = self.format_var.get()
        if fmt == "all":
            return ["-o", str(Path(outdir) / f"{inp.stem}.gif")]  # CLI appends _<fps>fps per format
        fps = int(self.fps_var.get() or fpsgif.DEFAULT_FPS[fmt])
        if fmt == "gif":
            fps = min(fps, fpsgif.GIF_MAX_FPS)
        return ["-o", str(Path(outdir) / f"{inp.stem}_{fps}fps{SUFFIX[fmt]}")]

    def build_cmd(self):
        inp = Path(self.input_var.get().strip().strip('"'))
        if not inp.is_file():
            raise ValueError("Choose an input video first.")
        for name, var in (("FPS", self.fps_var), ("Width", self.width_var), ("GPU id", self.gpu_var)):
            v = var.get().strip()
            if v and not re.fullmatch(r"-?\d+", v):
                raise ValueError(f"{name} must be a whole number.")
        if self.outdir_var.get().strip() and not Path(self.outdir_var.get().strip()).is_dir():
            raise ValueError("Output folder does not exist.")

        cmd = [*child_cmd("cli"), "--progress", str(inp), *self.output_arg(inp),
               "-f", self.format_var.get(), "--engine", self.engine_var.get(),
               "-w", self.width_var.get().strip() or "480"]
        if self.fps_var.get().strip():
            cmd += ["--fps", self.fps_var.get().strip()]
        if self.start_var.get().strip():
            cmd += ["-s", self.start_var.get().strip()]
        if self.duration_var.get().strip():
            cmd += ["-t", self.duration_var.get().strip()]
        if self.model_var.get().strip():
            cmd += ["--rife-model", self.model_var.get().strip()]
        if self.gpu_var.get().strip():
            cmd += ["--gpu", self.gpu_var.get().strip()]
        if self.verbose_var.get():
            cmd.append("-v")
        return cmd

    # ---------- running ----------

    def start(self, cmd, label):
        self.outputs = []
        self.convert_btn.config(state="disabled", text="Converting…" if label == "Converting" else "Convert")
        self.install_btn.config(state="disabled")
        self.cancel_btn.config(state="normal")
        self.open_btn.config(state="disabled")
        self.folder_btn.config(state="disabled")
        self.progress.config(mode="indeterminate")
        self.progress.start(10)
        self.stage_label.config(text="Starting…", fg=TEXT)
        self.pct_label.config(text="")
        self.set_state(label + "…", ACCENT_HOVER)
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")

        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
        self.proc = subprocess.Popen(
            cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)

        def pump(proc):
            for line in proc.stdout:
                self.lines.put(line)
            proc.wait()
            self.lines.put(None)

        threading.Thread(target=pump, args=(self.proc,), daemon=True).start()

    def finished(self):
        code = self.proc.returncode if self.proc else None
        cancelled = self.cancelled
        self.proc = None
        self.cancelled = False
        self.progress.stop()
        self.progress.config(mode="determinate", value=100 if code == 0 and not cancelled else 0)
        self.title("60fps GIF Maker")
        self.pct_label.config(text="100%" if code == 0 and not cancelled else "")
        self.stage_label.config(text="Cancelled" if cancelled else "Finished" if code == 0 else "Failed - see log",
                                fg=WARN if cancelled else GOOD if code == 0 else BAD)
        self.convert_btn.config(state="normal", text="Convert")
        self.install_btn.config(state="normal")
        self.cancel_btn.config(state="disabled")
        self.refresh_tools()

        done = [p for p in self.outputs if Path(p).is_file()]
        if done:
            self.open_btn.config(state="normal")
            self.folder_btn.config(state="normal")
        if cancelled:
            self.write_log("\nCancelled.\n", "bad")
            self.set_state("Cancelled", WARN)
        elif code == 0:
            self.write_log("\nFinished.\n", "good")
            self.set_state("Done", GOOD)
        else:
            self.write_log(f"\nFailed (exit code {code}).\n", "bad")
            self.set_state("Failed", BAD)
            messagebox.showerror("Conversion failed", "Something went wrong - see the log for details.")

    def convert(self):
        try:
            cmd = self.build_cmd()
        except ValueError as e:
            messagebox.showwarning("Check your settings", str(e))
            return
        self.start(cmd, "Converting")

    def install_rife(self):
        self.start(child_cmd("setup"), "Installing RIFE")

    def cancel(self):
        if not self.proc:
            return
        self.cancelled = True
        if sys.platform == "win32":
            # Kill the whole tree so ffmpeg / RIFE children stop too.
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.proc.pid)],
                           capture_output=True, creationflags=NO_WINDOW)
        else:
            self.proc.terminate()

    def open_result(self):
        done = [p for p in self.outputs if Path(p).is_file()]
        if done:
            open_path(done[-1])

    def show_folder(self):
        done = [p for p in self.outputs if Path(p).is_file()]
        if done:
            reveal(done[-1])

    def destroy(self):
        if self.proc and messagebox.askyesno("Quit", "A conversion is running. Stop it and quit?"):
            self.cancel()
        elif self.proc:
            return
        super().destroy()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("--run-cli", "--run-setup"):
        sys.exit(run_child(sys.argv[1][6:]))
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # crisp text on high-DPI screens
        except Exception:
            pass
    App().mainloop()

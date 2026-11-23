#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Chords
"""
cz.py - mini-framework per M5Stack CardputerZero (320x170, /dev/fb0 RGB565).

Gestisce tre cose, così le app restano corte:
  * Display : framebuffer Linux (sul device), finestra Tk (sul PC), PNG (test headless)
  * Tastiera: evdev letto "a mano" da /dev/input/event* (nessuna dipendenza),
              più i tasti del terminale se lanci l'app da SSH
  * UI      : barra titolo, barra comandi, liste scorrevoli, prompt di testo, conferme

Scelta del backend (variabile CZ_BACKEND = fb | sim | png):
  - se non indicata: framebuffer se esiste ed è 320x170, altrimenti simulatore Tk.

Diagnostica tastiera:  python3 cz.py --keytest
"""
from __future__ import annotations

import fcntl
import glob
import mmap
import os
import select
import struct
import sys
import time
from dataclasses import dataclass
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

try:
    import numpy as np
except ImportError:  # funziona anche senza, ma più lento
    np = None

W, H = 320, 170
HEADER_H = 18
FOOTER_H = 14
BODY_TOP = HEADER_H
BODY_H = H - HEADER_H - FOOTER_H

# ---------------------------------------------------------------- colori
BG = (12, 14, 20)
FG = (230, 232, 238)
DIM = (130, 136, 150)
ACCENT = (64, 156, 255)
OK = (80, 200, 120)
WARN = (255, 190, 60)
BAD = (240, 80, 80)
BAR = (28, 32, 44)
SEL = (36, 64, 110)

# ---------------------------------------------------------------- font
_FONT_DIRS = [
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/dejavu",
    "/Library/Fonts",
    os.path.expanduser("~/Library/Fonts"),
    "C:/Windows/Fonts",
]
_FONT_FILES = {
    (False, False): ["DejaVuSans.ttf", "Arial.ttf", "arial.ttf"],
    (True, False): ["DejaVuSans-Bold.ttf", "Arial Bold.ttf", "arialbd.ttf"],
    (False, True): ["DejaVuSansMono.ttf", "Menlo.ttc", "consola.ttf"],
    (True, True): ["DejaVuSansMono-Bold.ttf", "Menlo.ttc", "consolab.ttf"],
}
_font_cache: dict = {}


def font(size: int = 12, bold: bool = False, mono: bool = False):
    key = (size, bold, mono)
    if key in _font_cache:
        return _font_cache[key]
    f = None
    for d in _FONT_DIRS:
        for name in _FONT_FILES[(bold, mono)]:
            p = os.path.join(d, name)
            if os.path.exists(p):
                try:
                    f = ImageFont.truetype(p, size)
                    break
                except OSError:
                    pass
        if f:
            break
    if f is None:
        try:
            f = ImageFont.load_default(size=size)  # Pillow >= 10.1
        except TypeError:
            f = ImageFont.load_default()
    _font_cache[key] = f
    return f


def text_w(draw, s, f) -> int:
    b = draw.textbbox((0, 0), s, font=f)
    return b[2] - b[0]


def ellipsize(draw, s, f, maxw) -> str:
    if text_w(draw, s, f) <= maxw:
        return s
    while s and text_w(draw, s + "…", f) > maxw:
        s = s[:-1]
    return s + "…"


def wrap(draw, s, f, maxw) -> list[str]:
    out = []
    for para in s.split("\n"):
        line = ""
        for word in para.split(" "):
            cand = (line + " " + word).strip()
            if text_w(draw, cand, f) <= maxw:
                line = cand
            else:
                if line:
                    out.append(line)
                while text_w(draw, word, f) > maxw and len(word) > 1:  # parola lunghissima
                    cut = len(word)
                    while cut > 1 and text_w(draw, word[:cut], f) > maxw:
                        cut -= 1
                    out.append(word[:cut])
                    word = word[cut:]
                line = word
        out.append(line)
    return out


# ---------------------------------------------------------------- tasti
# codici Linux input (linux/input-event-codes.h)
_KEYNAMES = {
    1: "ESC", 14: "BACKSPACE", 15: "TAB", 28: "ENTER",
    103: "UP", 108: "DOWN", 105: "LEFT", 106: "RIGHT",
    102: "HOME", 107: "END", 104: "PAGEUP", 109: "PAGEDOWN", 111: "DELETE",
    96: "ENTER",  # KP_ENTER
}
_CHARS = {
    2: "1", 3: "2", 4: "3", 5: "4", 6: "5", 7: "6", 8: "7", 9: "8", 10: "9", 11: "0",
    12: "-", 13: "=", 16: "q", 17: "w", 18: "e", 19: "r", 20: "t", 21: "y", 22: "u",
    23: "i", 24: "o", 25: "p", 26: "[", 27: "]", 30: "a", 31: "s", 32: "d", 33: "f",
    34: "g", 35: "h", 36: "j", 37: "k", 38: "l", 39: ";", 40: "'", 41: "`", 43: "\\",
    44: "z", 45: "x", 46: "c", 47: "v", 48: "b", 49: "n", 50: "m", 51: ",", 52: ".",
    53: "/", 57: " ",
}
_SHIFTED = dict(zip("1234567890-=[];'`\\,./", "!@#$%^&*()_+{}:\"~|<>?"))
_SHIFT = {42, 54}
_CTRL = {29, 97}
_ALT = {56, 100}
_CAPS = 58
# frecce "alla Cardputer" (tasti ; . , /) usate solo per navigare, mai nei prompt
_ALT_ARROWS = {";": "UP", ".": "DOWN", ",": "LEFT", "/": "RIGHT"}


def diario(msg: str):
    """Una riga con data e ora nel diario di sessione (se CZ_DIARIO è impostata)."""
    path = os.environ.get("CZ_DIARIO")
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path) and os.path.getsize(path) > 100_000:
            with open(path, "rb") as f:
                f.seek(-50_000, os.SEEK_END)
                tail = f.read()
            with open(path, "wb") as f:
                f.write(tail)
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S.%f}"[:-3]
                    + f" [{os.getpid()} {os.path.basename(sys.argv[0])}] {msg}\n")
    except OSError:
        pass


@dataclass
class Key:
    name: str            # "UP", "ENTER", "ESC", "a", "A", "!", ...
    char: str | None     # carattere stampabile oppure None
    code: int = 0        # codice evdev grezzo (0 se da terminale/simulatore)
    ctrl: bool = False
    alt: bool = False

    @property
    def nav(self) -> str | None:
        """Direzione per la navigazione: frecce vere o ; . , / della Cardputer."""
        if self.name in ("UP", "DOWN", "LEFT", "RIGHT"):
            return self.name
        if self.char in _ALT_ARROWS and not self.ctrl:
            return _ALT_ARROWS[self.char]
        return None

    def is_(self, *names) -> bool:
        return self.name in names or (self.char is not None and self.char.lower() in names)


def _ioc_read(nr: int, size: int) -> int:
    return (2 << 30) | (size << 16) | (ord("E") << 8) | nr


class EvdevInput:
    """Legge tutte le tastiere in /dev/input senza dipendere da python-evdev."""

    FMT = "llHHi"  # struct input_event su Linux 64 bit
    SIZE = struct.calcsize(FMT)

    def __init__(self):
        self.fds: list[int] = []
        self.shift = self.ctrl = self.alt = False
        self.caps = False
        forced = os.environ.get("CZ_INPUT")
        paths = [forced] if forced else sorted(glob.glob("/dev/input/event*"))
        for p in paths:
            try:
                fd = os.open(p, os.O_RDONLY | os.O_NONBLOCK)
            except OSError:
                continue
            if forced or self._is_keyboard(fd):
                self.fds.append(fd)
                if os.environ.get("CZ_GRAB") == "1":
                    try:
                        fcntl.ioctl(fd, 0x40044590, 1)  # EVIOCGRAB
                    except OSError:
                        pass
            else:
                os.close(fd)

    @staticmethod
    def _is_keyboard(fd) -> bool:
        buf = bytearray(96)
        try:
            fcntl.ioctl(fd, _ioc_read(0x20 + 1, len(buf)), buf)  # EVIOCGBIT(EV_KEY)
        except OSError:
            return False

        def has(code):
            return bool(buf[code // 8] & (1 << (code % 8)))
        return has(30) or has(1) or has(28)  # KEY_A / KEY_ESC / KEY_ENTER

    def fileno_list(self):
        return self.fds

    def read(self) -> list[Key]:
        out = []
        for fd in self.fds:
            while True:
                try:
                    data = os.read(fd, self.SIZE * 32)
                except BlockingIOError:
                    break
                except OSError:
                    break
                if not data:
                    break
                for i in range(0, len(data) - self.SIZE + 1, self.SIZE):
                    _, _, typ, code, val = struct.unpack_from(self.FMT, data, i)
                    k = self._event(typ, code, val)
                    if k:
                        out.append(k)
                if len(data) < self.SIZE * 32:
                    break
        return out

    def _event(self, typ, code, val) -> Key | None:
        if typ != 1:  # EV_KEY
            return None
        down = val != 0  # 1 = premuto, 2 = ripetizione
        if code == 1:  # Esc: lo traccia nel diario e non lo fa ripetere
            if val == 1:
                self.esc_since = time.monotonic()
                diario("tasto Esc premuto")
            elif val == 0:
                since = getattr(self, "esc_since", None)
                held = f" dopo {time.monotonic() - since:.2f} s" if since else " (pressione non vista)"
                diario("tasto Esc rilasciato" + held)
                self.esc_since = None
            else:
                # ripetizione automatica di Esc: tenendolo premuto si uscirebbe da più
                # schermate di fila (brano -> elenco -> accordatore -> fuori). Si ignora.
                return None
        if code in _SHIFT:
            self.shift = down
            return None
        if code in _CTRL:
            self.ctrl = down
            return None
        if code in _ALT:
            self.alt = down
            return None
        if code == _CAPS:
            if val == 1:
                self.caps = not self.caps
            return None
        if not down:
            return None
        return key_from_code(code, self.shift, self.caps, self.ctrl, self.alt)

    def close(self):
        for fd in self.fds:
            try:
                os.close(fd)
            except OSError:
                pass
        self.fds = []


def key_from_code(code, shift=False, caps=False, ctrl=False, alt=False) -> Key:
    if code in _KEYNAMES:
        return Key(_KEYNAMES[code], None, code, ctrl, alt)
    ch = _CHARS.get(code)
    if ch is None:
        return Key(f"KEY_{code}", None, code, ctrl, alt)
    if ch.isalpha():
        if shift ^ caps:
            ch = ch.upper()
    elif shift:
        ch = _SHIFTED.get(ch, ch)
    name = "SPACE" if ch == " " else ch
    return Key(name, ch, code, ctrl, alt)


class TtyInput:
    """Tasti dal terminale (comodo se lanci l'app via SSH)."""

    _SEQ = {"\x1b[A": "UP", "\x1b[B": "DOWN", "\x1b[C": "RIGHT", "\x1b[D": "LEFT",
            "\x1bOA": "UP", "\x1bOB": "DOWN", "\x1bOC": "RIGHT", "\x1bOD": "LEFT",
            "\x1b[3~": "DELETE", "\x1b[H": "HOME", "\x1b[F": "END",
            "\x1b[5~": "PAGEUP", "\x1b[6~": "PAGEDOWN"}

    def __init__(self):
        import termios
        import tty
        self.fd = sys.stdin.fileno()
        self._termios = termios
        self.old = termios.tcgetattr(self.fd)
        tty.setcbreak(self.fd)

    def fileno_list(self):
        return [self.fd]

    def read(self) -> list[Key]:
        out = []
        try:
            data = os.read(self.fd, 64).decode("utf-8", "ignore")
        except OSError:
            return out
        i = 0
        while i < len(data):
            matched = False
            for seq, name in self._SEQ.items():
                if data.startswith(seq, i):
                    out.append(Key(name, None))
                    i += len(seq)
                    matched = True
                    break
            if matched:
                continue
            c = data[i]
            i += 1
            if c == "\x1b":
                out.append(Key("ESC", None))
            elif c in ("\r", "\n"):
                out.append(Key("ENTER", None))
            elif c in ("\x7f", "\x08"):
                out.append(Key("BACKSPACE", None))
            elif c == "\t":
                out.append(Key("TAB", None))
            elif c == " ":
                out.append(Key("SPACE", " "))
            elif c.isprintable():
                out.append(Key(c, c))
        return out

    def close(self):
        self._termios.tcsetattr(self.fd, self._termios.TCSADRAIN, self.old)


# ---------------------------------------------------------------- display
def to_rgb565(img: Image.Image) -> bytes:
    img = img.convert("RGB")
    if np is not None:
        a = np.asarray(img, dtype=np.uint16)
        v = ((a[..., 0] >> 3) << 11) | ((a[..., 1] >> 2) << 5) | (a[..., 2] >> 3)
        return v.astype("<u2").tobytes()
    out = bytearray(img.width * img.height * 2)  # fallback lento senza numpy
    i = 0
    for r, g, b in img.getdata():
        v = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
        out[i] = v & 0xFF
        out[i + 1] = v >> 8
        i += 2
    return bytes(out)


def fb_info(path):
    fd = os.open(path, os.O_RDWR)
    var = bytearray(160)
    fcntl.ioctl(fd, 0x4600, var)  # FBIOGET_VSCREENINFO
    xres, yres, _, _, _, _, bpp = struct.unpack_from("7I", var, 0)
    fix = bytearray(80)  # struct fb_fix_screeninfo (64 bit)
    fcntl.ioctl(fd, 0x4602, fix)  # FBIOGET_FSCREENINFO
    smem_len = struct.unpack_from("I", fix, 24)[0]
    line_length = struct.unpack_from("I", fix, 48)[0]
    return fd, xres, yres, bpp, smem_len, line_length


class FBDisplay:
    def __init__(self, path):
        self.fd, self.xres, self.yres, self.bpp, smem, self.stride = fb_info(path)
        if self.bpp not in (16, 32):
            raise RuntimeError(f"framebuffer a {self.bpp} bpp non supportato")
        self.mm = mmap.mmap(self.fd, smem, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE)
        if (self.xres, self.yres) != (W, H):
            print(f"[cz] attenzione: framebuffer {self.xres}x{self.yres}, disegno in alto a sinistra",
                  file=sys.stderr)

    def show(self, img):
        w, h = min(W, self.xres), min(H, self.yres)
        if self.bpp == 16:
            data, bpr = to_rgb565(img.crop((0, 0, w, h))), w * 2
        else:
            data, bpr = img.crop((0, 0, w, h)).convert("RGB").tobytes("raw", "BGRX"), w * 4
        if self.stride == bpr:
            self.mm.seek(0)
            self.mm.write(data)
        else:
            for y in range(h):
                self.mm.seek(y * self.stride)
                self.mm.write(data[y * bpr:(y + 1) * bpr])

    def close(self):
        try:
            self.mm.close()
        finally:
            os.close(self.fd)


class PNGDisplay:
    """Backend di test: salva i frame come PNG, tasti presi da CZ_KEYS."""

    def __init__(self):
        self.dir = os.environ.get("CZ_PNG_DIR", "cz_frames")
        os.makedirs(self.dir, exist_ok=True)
        self.n = 0
        self.last = None

    def show(self, img):
        b = img.tobytes()
        if b == self.last:
            return
        self.last = b
        self.n += 1
        img.save(os.path.join(self.dir, f"frame_{self.n:04d}.png"))
        img.save(os.path.join(self.dir, "last.png"))

    def close(self):
        pass


class ScriptInput:
    """CZ_KEYS="DOWN,DOWN,ENTER,WAIT,a,b,ESC"  (WAIT = un giro senza tasti)."""

    def __init__(self):
        raw = os.environ.get("CZ_KEYS", "ESC")
        self.queue = [k for k in raw.split(",") if k != ""]

    def next(self) -> Key | None:
        if not self.queue:
            raise SystemExit("[cz] tasti di test finiti")
        k = self.queue.pop(0)
        if k == "WAIT":
            return None
        if k == "COMMA":
            return Key(",", ",")
        if len(k) == 1:
            return Key("SPACE" if k == " " else k, k)
        return Key(k, " " if k == "SPACE" else None)


class SimDisplay:
    """Finestra Tk 3x per provare le app sul PC prima che arrivi il device."""

    _MAP = {"Up": "UP", "Down": "DOWN", "Left": "LEFT", "Right": "RIGHT", "Return": "ENTER",
            "KP_Enter": "ENTER", "Escape": "ESC", "BackSpace": "BACKSPACE", "Tab": "TAB",
            "Delete": "DELETE", "Home": "HOME", "End": "END", "Prior": "PAGEUP", "Next": "PAGEDOWN"}

    def __init__(self, title, scale=3):
        import tkinter as tk
        from PIL import ImageTk
        self._ImageTk = ImageTk
        self.scale = scale
        self.root = tk.Tk()
        self.root.title(f"CardputerZero sim - {title}")
        self.root.resizable(False, False)
        self.label = tk.Label(self.root, bg="black")
        self.label.pack()
        self.queue: list[Key] = []
        self.closed = False
        self.root.bind("<Key>", self._on_key)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._photo = None

    def _on_key(self, e):
        ctrl = bool(e.state & 0x4)
        if e.keysym in self._MAP:
            self.queue.append(Key(self._MAP[e.keysym], None, ctrl=ctrl))
        elif e.char == " ":
            self.queue.append(Key("SPACE", " ", ctrl=ctrl))
        elif e.char and e.char.isprintable():
            self.queue.append(Key(e.char, e.char, ctrl=ctrl))

    def _on_close(self):
        self.closed = True
        self.queue.append(Key("ESC", None))

    def show(self, img):
        big = img.resize((W * self.scale, H * self.scale), Image.NEAREST)
        self._photo = self._ImageTk.PhotoImage(big)
        self.label.configure(image=self._photo)
        self.root.update()

    def pump(self) -> list[Key]:
        if not self.closed:
            self.root.update()
        q, self.queue = self.queue, []
        return q

    def close(self):
        if not self.closed:
            self.root.destroy()


# ---------------------------------------------------------------- App
class App:
    def __init__(self, title: str):
        self.title = title
        self.backend = os.environ.get("CZ_BACKEND") or self._auto_backend()
        self.inputs = []
        self.script = None
        self.sim = None
        if self.backend == "fb":
            self.display = FBDisplay(self._fb_path())
            ev = EvdevInput()
            if ev.fds:
                self.inputs.append(ev)
            if sys.stdin.isatty() and os.environ.get("CZ_TTYKEYS", "1") != "0":
                try:
                    self.inputs.append(TtyInput())
                except Exception:
                    pass
            if not self.inputs:
                print("[cz] nessuna tastiera leggibile: serve il gruppo 'input'", file=sys.stderr)
        elif self.backend == "png":
            self.display = PNGDisplay()
            self.script = ScriptInput()
        else:
            self.display = self.sim = SimDisplay(title)

    @staticmethod
    def _fb_path():
        return os.environ.get("APPLAUNCH_LINUX_FBDEV_DEVICE", "/dev/fb0")

    def _auto_backend(self):
        if sys.platform.startswith("linux") and os.path.exists(self._fb_path()):
            try:
                fd, x, y, *_ = fb_info(self._fb_path())
                os.close(fd)
                if (x, y) == (W, H) or not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
                    return "fb"
            except OSError:
                pass
        return "sim"

    # --- disegno
    def canvas(self):
        img = Image.new("RGB", (W, H), BG)
        return img, ImageDraw.Draw(img)

    def show(self, img):
        self.display.show(img)

    # --- input
    def wait(self, timeout: float | None = None) -> Key | None:
        """Aspetta un tasto per al massimo `timeout` secondi (None = per sempre)."""
        pend = getattr(self, "_pending", None)
        if pend:
            self._pending = pend[1:]
            return pend[0]
        if self.script is not None:
            if timeout == 0:  # semplice "c'è un tasto?" durante un lavoro: non consuma lo script
                return None
            return self.script.next()
        if self.sim is not None:
            end = None if timeout is None else time.monotonic() + timeout
            while True:
                q = self.sim.pump()
                if q:
                    self._pending = q[1:]
                    return q[0]
                if end is not None and time.monotonic() >= end:
                    return None
                time.sleep(0.01)
        end = None if timeout is None else time.monotonic() + timeout
        while True:
            fds = [fd for i in self.inputs for fd in i.fileno_list()]
            left = None if end is None else max(0.0, end - time.monotonic())
            if not fds:
                time.sleep(left if left is not None else 0.5)
                if end is not None:
                    return None
                continue
            r, _, _ = select.select(fds, [], [], left)
            keys = []
            for i in self.inputs:
                if any(fd in r for fd in i.fileno_list()):
                    keys.extend(i.read())
            if keys:
                self._pending = keys[1:]
                return keys[0]
            if end is not None and time.monotonic() >= end:
                return None

    def close(self):
        for i in self.inputs:
            i.close()
        # schermo nero all'uscita, così il launcher riparte pulito
        try:
            self.display.show(Image.new("RGB", (W, H), (0, 0, 0)))
        except Exception:
            pass
        self.display.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


# ---------------------------------------------------------------- UI
def battery_percent() -> int | None:
    for d in glob.glob("/sys/class/power_supply/*"):
        try:
            with open(os.path.join(d, "type")) as f:
                if f.read().strip() != "Battery":
                    continue
            with open(os.path.join(d, "capacity")) as f:
                return int(f.read().strip())
        except (OSError, ValueError):
            continue
    return None


def header(draw, title, right=None):
    draw.rectangle([0, 0, W, HEADER_H - 1], fill=BAR)
    draw.text((5, 2), title, font=font(12, bold=True), fill=FG)
    if right is None:
        right = datetime.now().strftime("%H:%M")
        b = battery_percent()
        if b is not None:
            right = f"{b}%  {right}"
    f = font(11)
    draw.text((W - 5 - text_w(draw, right, f), 3), right, font=f, fill=DIM)


def footer(draw, hints: str):
    draw.rectangle([0, H - FOOTER_H, W, H], fill=BAR)
    f = font(10)
    draw.text((5, H - FOOTER_H + 1), ellipsize(draw, hints, f, W - 10), font=f, fill=DIM)


def progress(draw, x, y, w, h, frac, color=ACCENT):
    frac = max(0.0, min(1.0, frac))
    draw.rectangle([x, y, x + w, y + h], outline=DIM)
    if frac > 0:
        draw.rectangle([x + 1, y + 1, x + 1 + int((w - 2) * frac), y + h - 1], fill=color)


class ListView:
    """Lista scorrevole: items = list[str] oppure list[(testo, colore)]."""

    def __init__(self, row_h=17, top=BODY_TOP + 2, height=BODY_H - 4):
        self.sel = 0
        self.first = 0
        self.row_h = row_h
        self.top = top
        self.rows = max(1, height // row_h)

    def key(self, k: Key, n: int) -> bool:
        if n == 0:
            return False
        d = k.nav
        if d == "UP":
            self.sel = (self.sel - 1) % n
        elif d == "DOWN":
            self.sel = (self.sel + 1) % n
        elif d == "LEFT" or k.name == "PAGEUP":
            self.sel = max(0, self.sel - self.rows)
        elif d == "RIGHT" or k.name == "PAGEDOWN":
            self.sel = min(n - 1, self.sel + self.rows)
        else:
            return False
        return True

    def draw(self, draw, items, render=None):
        n = len(items)
        self.sel = max(0, min(self.sel, n - 1)) if n else 0
        if self.sel < self.first:
            self.first = self.sel
        if self.sel >= self.first + self.rows:
            self.first = self.sel - self.rows + 1
        f = font(12)
        for i in range(self.first, min(n, self.first + self.rows)):
            y = self.top + (i - self.first) * self.row_h
            selected = i == self.sel
            if selected:
                draw.rectangle([2, y, W - 8, y + self.row_h - 2], fill=SEL)
            if render:
                render(draw, items[i], 6, y, selected)
            else:
                txt, col = items[i] if isinstance(items[i], tuple) else (items[i], FG)
                draw.text((6, y + 1), ellipsize(draw, txt, f, W - 18), font=f, fill=col)
        if n > self.rows:  # barra di scorrimento
            th = max(8, int(self.rows / n * (self.rows * self.row_h)))
            ty = self.top + int(self.first / max(1, n - self.rows) * (self.rows * self.row_h - th))
            draw.rectangle([W - 5, ty, W - 3, ty + th], fill=DIM)


def message(app: App, title: str, text: str, color=FG, hint="premi un tasto"):
    img, d = app.canvas()
    header(d, title)
    f = font(12)
    y = BODY_TOP + 6
    for line in wrap(d, text, f, W - 14)[:8]:
        d.text((7, y), line, font=f, fill=color)
        y += 16
    footer(d, hint)
    app.show(img)
    return app.wait()


def confirm(app: App, title: str, question: str) -> bool:
    img, d = app.canvas()
    header(d, title)
    f = font(12)
    y = BODY_TOP + 10
    for line in wrap(d, question, f, W - 14)[:6]:
        d.text((7, y), line, font=f, fill=FG)
        y += 16
    footer(d, "Y / Invio = sì    N / Esc = no")
    app.show(img)
    while True:
        k = app.wait()
        if k is None:
            continue
        if k.name == "ENTER" or k.is_("y", "s"):
            return True
        if k.name == "ESC" or k.is_("n"):
            return False


def prompt(app: App, title: str, label: str, initial: str = "", secret: bool = False,
           allowed=None) -> str | None:
    """Campo di testo. Invio conferma, Esc annulla. Ritorna None se annullato."""
    buf = initial
    while True:
        img, d = app.canvas()
        header(d, title)
        f = font(12)
        y = BODY_TOP + 6
        for line in wrap(d, label, f, W - 14)[:3]:
            d.text((7, y), line, font=f, fill=DIM)
            y += 16
        y += 4
        d.rectangle([5, y, W - 6, y + 24], outline=ACCENT)
        shown = "•" * len(buf) if secret else buf
        fm = font(13, mono=True)
        while text_w(d, shown + "_", fm) > W - 20 and shown:
            shown = shown[1:]
        d.text((10, y + 5), shown + "_", font=fm, fill=FG)
        footer(d, "Invio = ok   Esc = annulla   ⌫ = cancella")
        app.show(img)
        k = app.wait()
        if k is None:
            continue
        if k.name == "ENTER":
            return buf
        if k.name == "ESC":
            return None
        if k.name in ("BACKSPACE", "DELETE"):
            buf = buf[:-1]
        elif k.char and (allowed is None or k.char in allowed):
            buf += k.char


# ---------------------------------------------------------------- keytest
def keytest():
    log = []
    with App("Test tastiera") as app:
        while True:
            img, d = app.canvas()
            header(d, "Test tastiera")
            fm = font(11, mono=True)
            for i, line in enumerate(log[-8:]):
                d.text((6, BODY_TOP + 4 + i * 16), line, font=fm, fill=FG)
            footer(d, "premi tasti - Esc due volte per uscire")
            app.show(img)
            k = app.wait()
            if k is None:
                continue
            line = f"code={k.code:<4} name={k.name:<10} char={k.char!r} nav={k.nav}"
            print(line)
            log.append(line)
            if k.name == "ESC" and len(log) > 1 and log[-2].find("name=ESC ") >= 0:
                break


if __name__ == "__main__":
    if "--keytest" in sys.argv:
        keytest()
    else:
        print(__doc__)

#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Chords
"""
Accordatore per chitarra - app per M5Stack CardputerZero (icona propria nel launcher).

Ascolta dal microfono, riconosce la nota (algoritmo YIN), mostra di quanti
cent sei lontano dalla corda e suona un "ding" quando la corda resta accordata
per un attimo. Accordature: Standard, Drop D, mezzo tono sotto, Open G, DADGAD.

Tasti:  1-6 sceglie la corda (1 = Mi cantino, 6 = Mi basso) e passa a MANUALE
        <- / -> (oppure , /  oppure Z / C) corda precedente / successiva
        A    modalità AUTO (riconosce da solo la corda)
        T    cambia accordatura          N  nomi E-A-D / Mi-La-Re
        M    suono di conferma on/off    S  canzoniere
        Esc  esci (se aperto da una canzone: torna alla canzone)

Da terminale:
  python3 tuner.py --selftest       prova l'algoritmo con suoni sintetici (niente microfono)
  python3 tuner.py --list-devices   elenca i dispositivi audio che verrebbero provati
  python3 tuner.py --device plughw:CARD=xxx,DEV=0   forza il microfono

Impostazioni (create al primo avvio): ~/.config/accordatore/impostazioni.json
  a4 (440), tolleranza_cent (3), soglia_db (-50), mic, uscita, rate (44100)
"""
from __future__ import annotations

import argparse
import ctypes
import glob
import io
import json
import math
import os
import signal
import subprocess
import sys
import threading
import time
import wave
from collections import deque

import numpy as np

import cz

# ---------------------------------------------------------------- musica
NOTE_EN = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
NOTE_IT = ["Do", "Do#", "Re", "Re#", "Mi", "Fa", "Fa#", "Sol", "Sol#", "La", "La#", "Si"]

# dalla corda 6 (la più grossa) alla corda 1
TUNINGS = [
    ("Standard", ["E2", "A2", "D3", "G3", "B3", "E4"]),
    ("Drop D", ["D2", "A2", "D3", "G3", "B3", "E4"]),
    ("Mezzo tono sotto", ["D#2", "G#2", "C#3", "F#3", "A#3", "D#4"]),
    ("Open G", ["D2", "G2", "D3", "G3", "B3", "D4"]),
    ("DADGAD", ["D2", "A2", "D3", "G3", "A3", "D4"]),
]


def note_midi(name: str) -> int:
    """'E2' -> 40, 'D#4' -> 63."""
    pitch, octave = name[:-1], int(name[-1])
    return NOTE_EN.index(pitch) + 12 * (octave + 1)


def midi_freq(m: float, a4: float) -> float:
    return a4 * 2.0 ** ((m - 69) / 12.0)


def freq_midi(f: float, a4: float) -> float:
    return 69 + 12 * math.log2(f / a4)


def cents(f: float, ref: float) -> float:
    return 1200.0 * math.log2(f / ref)


def note_parts(m: int, italian: bool) -> tuple[str, str]:
    names = NOTE_IT if italian else NOTE_EN
    return names[m % 12], str(m // 12 - 1)


# ---------------------------------------------------------------- YIN
class Yin:
    """Stima della frequenza fondamentale con YIN (de Cheveigné & Kawahara, 2002).

    Funzione differenza calcolata con la FFT, normalizzazione cumulativa,
    soglia assoluta e interpolazione parabolica del minimo.
    """

    MARGIN = 256  # campioni in più ai bordi, scartati dopo il filtro

    def __init__(self, rate: int, size: int = 4096, fmin: float = 60.0, fmax: float = 700.0,
                 threshold: float = 0.20, max_ap: float = 0.05, band=(40.0, 1000.0, 1600.0)):
        self.rate = rate
        self.size = size
        self.tau_min = max(2, int(rate / fmax))
        self.tau_max = int(math.ceil(rate / fmin))
        if self.tau_max * 2 >= size:
            raise ValueError("finestra troppo corta per la frequenza minima")
        self.w = size - self.tau_max
        self.nfft = 1 << (size + self.w - 1).bit_length()
        self.threshold = threshold
        # oltre questa aperiodicità la stima esiste ma è inaffidabile (corda quasi spenta,
        # rumore): meglio non mostrare nulla che una lancetta che balla di 10 cent
        self.max_ap = max_ap
        self._taus = np.arange(self.tau_max + 1)
        # filtro passa-banda morbido nel dominio della frequenza: toglie ronzio/DC sotto
        # ~40 Hz e il fruscio sopra ~1,6 kHz, dove non c'è niente che serva a trovare la nota
        self.need = size + 2 * self.MARGIN if band else size
        self._mask = None
        if band:
            hp, lp0, lp1 = band
            self._fnfft = 1 << (self.need + 64 - 1).bit_length()
            fr = np.fft.rfftfreq(self._fnfft, 1 / rate)
            m = np.clip((fr - hp * 0.6) / (hp * 0.4), 0, 1)  # rampa 24 -> 40 Hz
            m = 0.5 - 0.5 * np.cos(np.pi * m)
            lo = np.clip((fr - lp0) / (lp1 - lp0), 0, 1)
            m *= 0.5 + 0.5 * np.cos(np.pi * lo)               # da 1 a 0 tra lp0 e lp1
            self._mask = m

    def detect(self, x: np.ndarray) -> tuple[float | None, float]:
        """Ritorna (frequenza Hz oppure None, aperiodicità 0..1: più bassa = più pulita).

        x: almeno self.need campioni (gli ultimi vengono usati).
        """
        x = np.asarray(x[-self.need:], dtype=np.float64)
        x = x - x.mean()
        if self._mask is not None and len(x) == self.need:
            y = np.fft.irfft(np.fft.rfft(x, self._fnfft) * self._mask, self._fnfft)
            x = y[self.MARGIN:self.MARGIN + self.size]
        else:
            x = x[-self.size:]
        tmax, w = self.tau_max, self.w
        cs = np.concatenate(([0.0], np.cumsum(x * x)))
        e0 = cs[w]
        if e0 <= 0:
            return None, 1.0
        etau = cs[self._taus + w] - cs[self._taus]
        spec = np.fft.rfft(x, self.nfft) * np.conj(np.fft.rfft(x[:w], self.nfft))
        r = np.fft.irfft(spec, self.nfft)[:tmax + 1]
        d = np.maximum(e0 + etau - 2.0 * r, 0.0)
        cmnd = np.ones_like(d)
        run = np.cumsum(d[1:])
        cmnd[1:] = d[1:] * self._taus[1:] / np.where(run > 0, run, 1e-12)

        seg = cmnd[self.tau_min:]
        below = np.flatnonzero(seg < self.threshold)
        if below.size == 0:
            return None, float(seg.min())
        tau = int(below[0]) + self.tau_min
        while tau + 1 <= tmax and cmnd[tau + 1] < cmnd[tau]:
            tau += 1  # scendi fino al minimo locale
        if tau >= tmax or cmnd[tau] > self.max_ap:
            return None, float(cmnd[tau])
        a, b, c = cmnd[tau - 1], cmnd[tau], cmnd[tau + 1]
        den = a - 2 * b + c
        shift = 0.5 * (a - c) / den if den > 1e-12 else 0.0
        shift = max(-1.0, min(1.0, shift))
        return self.rate / (tau + shift), float(b)


# ---------------------------------------------------------------- audio
def _die_with_parent():
    """Se il processo Python muore, il kernel uccide anche arecord."""
    try:
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
    except Exception:
        pass


def alsa_cards() -> list[tuple[str, bool, bool, list[int], list[int]]]:
    """[(id scheda, ha cattura, ha riproduzione, dev cattura, dev riproduzione)] da /proc/asound."""
    out = []
    for cd in sorted(glob.glob("/proc/asound/card[0-9]*")):
        try:
            with open(os.path.join(cd, "id")) as f:
                cid = f.read().strip()
        except OSError:
            continue
        cap = sorted(int(os.path.basename(p)[3:-1]) for p in glob.glob(os.path.join(cd, "pcm*c")))
        pla = sorted(int(os.path.basename(p)[3:-1]) for p in glob.glob(os.path.join(cd, "pcm*p")))
        out.append((cid, bool(cap), bool(pla), cap, pla))
    return out


def capture_candidates(pref: str | None) -> list[str]:
    c = [pref] if pref else []
    c.append("default")
    for cid, has_cap, _, devs, _ in alsa_cards():
        if has_cap:
            c.append(f"plughw:CARD={cid},DEV={devs[0]}")
    seen, res = set(), []
    for d in c:
        if d not in seen:
            seen.add(d)
            res.append(d)
    return res


class Mic:
    """Cattura continua con arecord in un thread; tiene gli ultimi secondi in un buffer circolare."""

    def __init__(self, rate: int = 44100, channels: int = 2, seconds: float = 1.0):
        self.rate = rate
        self.ch = channels
        self.cap = int(rate * seconds)
        self.buf = np.zeros((self.cap, channels), dtype=np.int16)
        self.pos = 0
        self.filled = 0
        self.frames = 0
        self.lock = threading.Lock()
        self.proc = None
        self.thread = None
        self.device = None
        self.error = None
        self._stop = False

    def start(self, candidates: list[str], probe_s: float = 1.5) -> bool:
        """Prova i dispositivi in ordine; tiene il primo che manda davvero audio."""
        errors = []
        for dev in candidates:
            if self._open(dev):
                end = time.monotonic() + probe_s
                while time.monotonic() < end and self.frames < 2048 and self.proc.poll() is None:
                    time.sleep(0.05)
                if self.frames >= 2048:
                    self.device = dev
                    self.error = None
                    return True
                errors.append(f"{dev}: {self.error or 'nessun dato'}")
                self.stop()
            else:
                errors.append(f"{dev}: {self.error}")
        self.error = " | ".join(errors) if errors else "nessun dispositivo di cattura"
        return False

    def _open(self, dev: str) -> bool:
        self.error = None
        self._stop = False
        self.frames = self.filled = self.pos = 0
        cmd = ["arecord", "-q", "-D", dev, "-t", "raw", "-f", "S16_LE", "-c", str(self.ch),
               "-r", str(self.rate), "-B", "200000", "-F", "25000"]
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                         stdin=subprocess.DEVNULL, bufsize=0,
                                         preexec_fn=_die_with_parent)
        except OSError as e:
            self.error = "arecord non trovato (sudo apt install alsa-utils)" \
                if isinstance(e, FileNotFoundError) else str(e)
            self.proc = None
            return False
        self.thread = threading.Thread(target=self._reader, daemon=True)
        self.thread.start()
        return True

    def _reader(self):
        proc = self.proc
        fd = proc.stdout.fileno()
        frame_bytes = 2 * self.ch
        rest = b""
        # legge fino alla fine anche quando stiamo chiudendo: se smettessimo, arecord resterebbe
        # bloccato a scrivere nel tubo pieno e non uscirebbe (tenendo occupata la scheda audio)
        while True:
            try:
                data = os.read(fd, 4096 * frame_bytes)
            except OSError:
                break
            if not data:
                break
            if self._stop:
                continue
            data = rest + data
            n = len(data) // frame_bytes
            rest = data[n * frame_bytes:]
            if n:
                block = np.frombuffer(data[:n * frame_bytes], dtype="<i2").reshape(n, self.ch)
                self._write(block)
        if not self._stop:
            try:
                err = proc.stderr.read().decode(errors="replace").strip()
            except Exception:
                err = ""
            self.error = (err.splitlines() or ["arecord si è fermato"])[-1][:120]

    def _write(self, block: np.ndarray):
        k = len(block)
        with self.lock:
            if k >= self.cap:
                block, k = block[-self.cap:], self.cap
            end = self.pos + k
            if end <= self.cap:
                self.buf[self.pos:end] = block
            else:
                first = self.cap - self.pos
                self.buf[self.pos:] = block[:first]
                self.buf[:k - first] = block[first:]
            self.pos = end % self.cap
            self.filled = min(self.cap, self.filled + k)
            self.frames += k

    def latest(self, n: int) -> np.ndarray | None:
        """Ultimi n campioni mono (canale più forte, il microfono può stare a sinistra o a destra)."""
        with self.lock:
            if self.filled < n:
                return None
            start = (self.pos - n) % self.cap
            if start + n <= self.cap:
                a = self.buf[start:start + n].copy()
            else:
                a = np.concatenate((self.buf[start:], self.buf[:self.pos]))
        if self.ch == 1:
            return a[:, 0]
        e = (a.astype(np.float64) ** 2).sum(axis=0)
        return a[:, int(np.argmax(e))]

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        self._stop = True
        p, self.proc = self.proc, None
        if p is None:
            return
        try:
            p.terminate()
            p.wait(timeout=1)
        except Exception:
            try:
                p.kill()
                p.wait(timeout=1)
            except Exception:
                pass
        if self.thread is not None:
            self.thread.join(timeout=1)
        for f in (p.stdout, p.stderr):
            try:
                f.close()
            except Exception:
                pass


def chime_wav(rate: int, volume: float = 0.7) -> bytes:
    """Due note brevi a campana (Si5 -> Mi6), allo stesso rate del microfono."""
    dur = 0.55
    t = np.arange(int(rate * dur)) / rate
    y = np.zeros_like(t)
    for f, start, amp, decay in ((987.77, 0.0, 0.8, 9.0), (1318.51, 0.12, 1.0, 6.0)):
        tt = t - start
        on = tt >= 0
        tt = np.where(on, tt, 0.0)
        env = np.exp(-tt * decay) * (1 - np.exp(-tt * 600)) * on
        y += amp * env * (np.sin(2 * np.pi * f * tt) + 0.25 * np.sin(4 * np.pi * f * tt)
                          + 0.08 * np.sin(6 * np.pi * f * tt))
    y *= volume / max(1e-9, np.abs(y).max())
    pcm = (y * 32767).astype("<i2").tobytes()
    bio = io.BytesIO()
    with wave.open(bio, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return bio.getvalue()


class Speaker:
    """Suona il ding con aplay in un thread, senza bloccare lo schermo."""

    def __init__(self, device: str, rate: int):
        self.device = device
        self.wav = chime_wav(rate)
        self.busy = False
        self.last_end = -1e9
        self.error = None
        # alcuni codec non suonano mentre registrano ("Device or resource busy"):
        # in quel caso il ding si suona fermando un attimo il microfono
        self.exclusive = False
        self.retry = False

    def play(self):
        if self.busy:
            return
        self.busy = True
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            r = subprocess.run(["aplay", "-q", "-D", self.device, "-"], input=self.wav,
                               capture_output=True, timeout=5)
            if r.returncode != 0:
                msg = r.stderr.decode(errors="replace").strip().splitlines()
                last = (msg or ["aplay: errore"])[-1][:120]
                if "busy" in last.lower() and not self.exclusive:
                    self.exclusive = self.retry = True  # riprova subito a microfono fermo
                else:
                    self.error = last
            else:
                self.error = None
        except FileNotFoundError:
            self.error = "aplay non trovato (sudo apt install alsa-utils)"
        except (OSError, subprocess.SubprocessError) as e:
            self.error = str(e)[:120]
        finally:
            self.last_end = time.monotonic()
            self.busy = False

    def play_with_mic_paused(self, mic: "Mic"):
        """Ferma il microfono, suona (bloccante, ~0,6 s), lo riapre sullo stesso dispositivo."""
        dev = mic.device
        mic.stop()
        self.busy = True
        self._run()
        if dev:
            mic.start([dev], probe_s=1.0)

    def muting_mic(self, now: float) -> bool:
        """Mentre suona (e subito dopo) il microfono sentirebbe il ding: lo ignoriamo."""
        return self.busy or now - self.last_end < 0.3


# ---------------------------------------------------------------- logica
class Tuner:
    HOLD = 1.0        # secondi di fila entro tolleranza per il ding (impostazione "tempo_accordata")
    LOST = 0.3        # dopo quanto senza nota la lancetta sparisce
    REARM_LOST = 0.8  # dopo quanto silenzio il ding si riarma

    def __init__(self, a4=440.0, tol=3.0, tuning=0, auto=True, sel=0, hold=None):
        if hold is not None:
            self.HOLD = max(0.2, min(5.0, float(hold)))
        self.a4 = a4
        self.tol = tol
        self.rearm = max(8.0, 2.5 * tol)
        self.tuning = tuning % len(TUNINGS)
        self.auto = auto
        self.sel = sel % 6
        self.hist = deque(maxlen=5)
        self.freq = None
        self.last_valid = -1e9
        self.cents = None
        self.needle = None
        self.target = None
        self.tuned = [False] * 6
        self.in_since = None
        self.out_since = None
        self.armed = True
        self.ding_t = -1e9

    @property
    def strings(self) -> list[int]:
        return [note_midi(n) for n in TUNINGS[self.tuning][1]]

    def string_freq(self, i: int) -> float:
        return midi_freq(self.strings[i], self.a4)

    def _retarget(self):
        self.in_since = self.out_since = None
        self.armed = True

    def set_tuning(self, idx: int):
        self.tuning = idx % len(TUNINGS)
        self.tuned = [False] * 6
        self._retarget()

    def select(self, i: int):
        self.auto = False
        self.sel = i % 6
        self._retarget()

    def set_auto(self):
        self.auto = True
        self._retarget()

    def update(self, t: float, f: float | None, frozen: bool = False) -> bool:
        """Aggiorna lo stato con una nuova stima. Ritorna True quando va suonato il ding."""
        if f is not None and not (math.isfinite(f) and f > 0):
            f = None  # frequenze impossibili: come se non ci fosse nota
        if frozen:  # durante il ding: congela tutto, non contare questo tempo
            if self.freq is not None:
                self.last_valid = t
            if self.in_since is not None:
                self.in_since = t - min(self.HOLD * 0.5, t - self.in_since)
            return False
        if f is not None:
            self.hist.append(f)
            self.freq = float(np.median(self.hist))
            self.last_valid = t
        elif t - self.last_valid > self.LOST:
            self.hist.clear()
            self.freq = None
            self.cents = self.needle = None
        if t - self.last_valid > self.REARM_LOST:
            self.armed = True
            self.in_since = self.out_since = None

        if self.freq is None:
            if not self.auto:
                self.target = self.sel
            return False

        m = freq_midi(self.freq, self.a4)
        if self.auto:
            target = min(range(6), key=lambda i: abs(m - self.strings[i]))
        else:
            target = self.sel
        if target != self.target:
            self.target = target
            self._retarget()
            self.needle = None
        c = cents(self.freq, self.string_freq(target))
        self.cents = c
        if self.needle is None or abs(c - self.needle) > 40:
            self.needle = c
        else:
            self.needle += 0.45 * (c - self.needle)

        ding = False
        if abs(c) <= self.tol:
            self.out_since = None
            if self.in_since is None:
                self.in_since = t
            elif self.armed and t - self.in_since >= self.HOLD:
                self.armed = False
                self.tuned[target] = True
                self.ding_t = t
                ding = True
        else:
            self.in_since = None
            if abs(c) > self.rearm:
                self.armed = True
                if self.out_since is None:
                    self.out_since = t
                elif t - self.out_since >= 0.6:
                    self.tuned[target] = False
            else:
                self.out_since = None
        return ding


# ---------------------------------------------------------------- disegno
OK_FILL = (24, 70, 44)
ORANGE = (255, 150, 60)
METER_X0, METER_X1, METER_Y = 20, 300, 82
EXIT_SONGBOOK = 10  # codice d'uscita: "apri il canzoniere" (lo gestisce chitarra.py)
FROM_SONG = os.environ.get("CHITARRA_DA_CANZONE") == "1"  # aperto col tasto T di una canzone
FOOTER = ("1-6 corda  ←→  A auto  T accord.  M suono  Esc canzone" if FROM_SONG
          else "1-6 corda  ←→  A auto  T accord.  M suono  S canzoni  Esc")
FAR_CENTS = 300        # MANUALE: oltre 3 semitoni niente "tendi/allenta", solo un avviso
AUTO_SURE_CENTS = 100  # AUTO: oltre 1 semitono la corda riconosciuta non è affidabile


def _state_color(c: float | None, tol: float):
    if c is None:
        return cz.DIM
    a = abs(c)
    if a <= tol:
        return cz.OK
    if a <= 15:
        return cz.WARN
    return ORANGE if a <= 50 else cz.BAD


def _tri(d, cx, cy, size, up, color):
    if up:
        d.polygon([(cx, cy - size), (cx - size, cy + size * 0.8), (cx + size, cy + size * 0.8)], fill=color)
    else:
        d.polygon([(cx, cy + size), (cx - size, cy - size * 0.8), (cx + size, cy - size * 0.8)], fill=color)


def _check(d, x, y, s, color, width=2):
    d.line([(x, y + s * 0.55), (x + s * 0.38, y + s), (x + s, y)], fill=color, width=width)


def _centered(d, cx, y, text, f, color):
    d.text((cx - cz.text_w(d, text, f) // 2, y), text, font=f, fill=color)


def draw_screen(d, tu: Tuner, view: dict):
    """Disegna tutto il corpo dello schermo (tra header e footer)."""
    now = view["now"]
    italian = view["italian"]
    name = TUNINGS[tu.tuning][0]
    mode = "AUTO" if tu.auto else "MANUALE"
    right = f"{name} · {mode}" + (" · muto" if view["muted"] else "")
    if view.get("spk_error") and not view["muted"]:
        right += " · ding ✗"
    cz.header(d, "Accordatore", right=right)

    c = tu.cents
    col = _state_color(c, tu.tol)
    tgt = tu.target if tu.target is not None else (None if tu.auto else tu.sel)

    # --- microfono assente: solo il messaggio, niente strumento
    if view.get("error"):
        d.text((8, 24), "Microfono non disponibile", font=cz.font(15, bold=True), fill=cz.BAD)
        y = 46
        for line in cz.wrap(d, view["error"], cz.font(10), cz.W - 16)[:6]:
            d.text((8, y), line, font=cz.font(10), fill=cz.DIM)
            y += 13
        y += 6
        for line in ("Da SSH prova:  arecord -l", "e:  python3 tuner.py --list-devices"):
            d.text((8, y), line, font=cz.font(11), fill=cz.FG)
            y += 15
        cz.footer(d, "Esc esci")
        return

    # --- nota grande e numeri
    fb = cz.font(40, bold=True)
    if tu.freq is not None:
        nm, octv = note_parts(int(round(freq_midi(tu.freq, tu.a4))), italian)
        d.text((10, 18), nm, font=fb, fill=col)
        x = 10 + cz.text_w(d, nm, fb) + 2
        d.text((x, 44), octv, font=cz.font(17, bold=True), fill=col)
        d.text((150, 22), f"{tu.freq:6.1f} Hz", font=cz.font(18, bold=True), fill=cz.FG)
        dev_txt = f"{c:+.1f} cent" if abs(c) < 100 else f"{c / 100:+.1f} semitoni"
        d.text((150, 43), dev_txt, font=cz.font(15, bold=True), fill=col)
    else:
        d.text((14, 18), "–", font=fb, fill=cz.DIM)
        d.text((150, 26), "In ascolto…", font=cz.font(16, bold=True), fill=cz.DIM)
        d.text((150, 46), "suona una corda", font=cz.font(12), fill=cz.DIM)
    if tgt is not None:
        tn, to = note_parts(tu.strings[tgt], italian)
        info = f"{6 - tgt}ª corda · {tn}{to} {tu.string_freq(tgt):.2f} Hz"
    else:
        info = "AUTO: trova la corda da sé"
    if tu.a4 != 440:
        info += f" · La={tu.a4:g}"
    fi = cz.font(10)
    d.text((150, 62), cz.ellipsize(d, info, fi, 302 - 150), font=fi, fill=cz.DIM)

    # --- livello microfono (barretta verticale a destra)
    lvl, gate = view["level_db"], view["gate_db"]
    x0, y0, y1 = 308, 22, 70
    d.rectangle([x0, y0, x0 + 5, y1], outline=cz.BAR, fill=cz.BAR)
    frac = max(0.0, min(1.0, (lvl + 70) / 70))
    h = int((y1 - y0 - 1) * frac)
    # h >= 1: con un livello appena sopra -70 dB veniva 0 e il rettangolo risultava capovolto
    # (Pillow recente lo rifiuta con ValueError: era il crash dopo pochi secondi in una stanza silenziosa)
    if h >= 1:
        d.rectangle([x0 + 1, y1 - h, x0 + 4, y1 - 1], fill=cz.OK if lvl > gate else cz.DIM)
    gy = y1 - int((y1 - y0) * max(0.0, min(1.0, (gate + 70) / 70)))
    d.line([(x0 - 2, gy), (x0 + 7, gy)], fill=cz.WARN)

    # --- strumento a lancetta: -50 ... +50 cent
    span = METER_X1 - METER_X0
    px = span / 100.0
    cx = METER_X0 + span // 2
    d.rounded_rectangle([METER_X0, METER_Y, METER_X1, METER_Y + 10], radius=5, fill=cz.BAR)
    gz = max(2, int(tu.tol * px))
    d.rectangle([cx - gz, METER_Y + 1, cx + gz, METER_Y + 9], fill=OK_FILL)
    for k in range(-50, 51, 10):
        x = cx + int(k * px)
        h = 6 if k in (-50, 0, 50) else 3
        d.line([(x, METER_Y - h - 1), (x, METER_Y - 2)], fill=cz.DIM if k else cz.FG)
    fs = cz.font(9)
    d.text((METER_X0 - 6, METER_Y + 12), "-50", font=fs, fill=cz.DIM)
    _centered(d, cx, METER_Y + 12, "0", fs, cz.DIM)
    d.text((METER_X1 - 12, METER_Y + 12), "+50", font=fs, fill=cz.DIM)
    if tu.needle is not None:
        n = max(-50.0, min(50.0, tu.needle))
        x = cx + int(round(n * px))
        d.rounded_rectangle([x - 3, METER_Y - 7, x + 3, METER_Y + 17], radius=3, fill=col)
        if abs(tu.needle) > 50:  # fuori scala: freccia sul bordo verso cui sta la nota
            if tu.needle < 0:
                d.polygon([(METER_X0 - 4, METER_Y - 1), (METER_X0 - 12, METER_Y + 5),
                           (METER_X0 - 4, METER_Y + 11)], fill=col)
            else:
                d.polygon([(METER_X1 + 4, METER_Y - 1), (METER_X1 + 12, METER_Y + 5),
                           (METER_X1 + 4, METER_Y + 11)], fill=col)

    # --- indicazione: tendi / allenta / accordata
    sy = 106
    fbig = cz.font(14, bold=True)
    if c is None:
        _centered(d, cz.W // 2, sy, "pizzica la corda vicino al microfono", cz.font(11), cz.DIM)
    elif abs(c) <= tu.tol:
        txt = "ACCORDATA"
        w = cz.text_w(d, txt, fbig)
        x = cz.W // 2 - (w + 18) // 2
        if now - tu.ding_t < 1.2:
            d.rounded_rectangle([x - 8, sy - 2, x + w + 26, sy + 17], radius=4, fill=OK_FILL)
        _check(d, x, sy + 2, 12, cz.OK, 3)
        d.text((x + 18, sy - 1), txt, font=fbig, fill=cz.OK)
    else:
        flat = c < 0
        txt = "tendi la corda" if flat else "allenta la corda"
        arrow = True
        if tu.auto and abs(c) > AUTO_SURE_CENTS:
            # in AUTO, a più di un semitono dalla corda più vicina non si sa quale corda sia:
            # un La calante di 2,5 semitoni sembra un Mi crescente. Niente direzione.
            txt, fbig, arrow = "corda incerta: sceglila con i tasti 1-6", cz.font(12, bold=True), False
        elif abs(c) > FAR_CENTS:
            # più di 3 semitoni dalla corda scelta: quasi sempre è la corda sbagliata.
            # Non invitare a tirare: si rischia di spezzarla.
            txt, fbig = "troppo lontana: è la corda giusta?", cz.font(12, bold=True)
        w = cz.text_w(d, txt, fbig)
        if arrow:
            x = cz.W // 2 - (w + 18) // 2
            _tri(d, x + 6, sy + 7, 6, flat, col)
            d.text((x + 18, sy - 1), txt, font=fbig, fill=col)
        else:
            d.text((cz.W // 2 - w // 2, sy), txt, font=fbig, fill=cz.WARN)

    # --- le 6 corde
    bw, gap, by0, by1 = 48, 4, 125, 152
    x = (cz.W - (6 * bw + 5 * gap)) // 2
    fl, fn = cz.font(13, bold=True), cz.font(9)
    for i, m in enumerate(tu.strings):
        r = [x, by0, x + bw, by1]
        is_t = tgt == i
        d.rounded_rectangle(r, radius=4, fill=OK_FILL if tu.tuned[i] else cz.BAR)
        if is_t:
            d.rounded_rectangle(r, radius=4, outline=cz.ACCENT if not tu.auto else col, width=2)
        nm, octv = note_parts(m, italian)
        label = nm + octv
        _centered(d, x + bw // 2, by0 + 9, label, fl, cz.FG if (is_t or tu.tuned[i]) else cz.DIM)
        d.text((x + 4, by0 + 2), str(i + 1), font=fn, fill=cz.DIM)  # tasto da premere
        if tu.tuned[i]:
            _check(d, x + bw - 12, by0 + 4, 7, cz.OK, 2)
        x += bw + gap

    toast = view.get("toast")
    if toast and view.get("now", 0) < toast[1]:
        cz.footer(d, toast[0])
    else:
        cz.footer(d, view.get("footer") or FOOTER)


# ---------------------------------------------------------------- impostazioni
CONF = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                    "accordatore", "impostazioni.json")
DEFAULTS = {"a4": 440.0, "tolleranza_cent": 3.0, "soglia_db": -50.0, "mic": "", "uscita": "",
            "rate": 44100, "accordatura": 0, "auto": True, "corda": 0, "nomi_italiani": False,
            "suono": True, "tempo_accordata": 1.0}


def load_conf() -> dict:
    conf = dict(DEFAULTS)
    try:
        with open(CONF, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            conf.update({k: v for k, v in data.items()
                         if k in DEFAULTS and type(v) is type(DEFAULTS[k])
                         or (k in DEFAULTS and isinstance(DEFAULTS[k], float) and type(v) is int)})
    except (OSError, ValueError):
        pass
    return conf


def save_conf(conf: dict):
    try:
        os.makedirs(os.path.dirname(CONF), exist_ok=True)
        tmp = CONF + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(conf, f, indent=1, ensure_ascii=False)
        os.replace(tmp, CONF)
    except OSError:
        pass


# ---------------------------------------------------------------- programma
def level_db(x: np.ndarray) -> float:
    rms = math.sqrt(float(np.mean(np.square(x.astype(np.float64))))) / 32768.0
    return 20 * math.log10(rms) if rms > 1e-7 else -140.0


def splash(app, text: str):
    img, d = app.canvas()
    cz.header(d, "Accordatore", right="")
    d.text((10, 60), text, font=cz.font(14, bold=True), fill=cz.FG)
    cz.footer(d, "Esc esci")
    app.show(img)


def run(args) -> int:
    conf = load_conf()
    if args.device:
        conf["mic"] = args.device
    if args.out:
        conf["uscita"] = args.out
    for k, v in (("a4", args.a4), ("tolleranza_cent", args.tol), ("soglia_db", args.gate_db),
                 ("rate", args.rate)):
        if v is not None:
            conf[k] = v
    if args.no_sound:
        conf["suono"] = False

    rate = int(conf["rate"])
    tu = Tuner(a4=float(conf["a4"]), tol=float(conf["tolleranza_cent"]),
               tuning=int(conf["accordatura"]), auto=bool(conf["auto"]), sel=int(conf["corda"]),
               hold=float(conf["tempo_accordata"]))
    yin = Yin(rate)
    period = 1 / 15
    view = {"italian": bool(conf["nomi_italiani"]), "muted": not conf["suono"],
            "gate_db": float(conf["soglia_db"]), "level_db": -140.0, "now": 0.0, "error": None}

    def remember():
        # salva solo le scelte fatte coi tasti; le opzioni da riga di comando valgono una volta
        saved = load_conf()
        saved.update(accordatura=tu.tuning, auto=tu.auto, corda=tu.sel,
                     nomi_italiani=view["italian"], suono=not view["muted"])
        save_conf(saved)

    def on_term(*_):
        cz.diario("accordatore: ricevuto SIGTERM")
        sys.exit(0)
    signal.signal(signal.SIGTERM, on_term)
    mic = Mic(rate=rate, channels=2)
    with cz.App("Accordatore") as app:
        try:
            splash(app, "Avvio microfono…")
            if not mic.start(capture_candidates(conf["mic"] or None)):
                view["error"] = mic.error
            dev = mic.device or "default"
            out = conf["uscita"] or (dev if dev.startswith("plughw:") else "default")
            spk = Speaker(out, rate)

            while True:
                t0 = time.monotonic()
                view["now"] = t0
                if not view["error"] and not mic.alive and mic.error:
                    view["error"] = mic.error
                x = mic.latest(yin.need) if not view["error"] else None
                if x is not None:
                    frozen = spk.muting_mic(t0)
                    view["level_db"] = level_db(x[-2048:])
                    f = None
                    if not frozen and view["level_db"] > view["gate_db"]:
                        f, _ = yin.detect(x)
                    if tu.update(t0, f, frozen=frozen) and not view["muted"]:
                        if spk.exclusive:
                            spk.play_with_mic_paused(mic)
                        else:
                            spk.play()
                if spk.retry:  # il primo ding è fallito per "busy": rifallo a microfono fermo
                    spk.retry = False
                    spk.play_with_mic_paused(mic)
                view["spk_error"] = spk.error
                img, d = app.canvas()
                draw_screen(d, tu, view)
                app.show(img)

                deadline = t0 + period
                while True:
                    k = app.wait(max(0.0, deadline - time.monotonic()))
                    if k is None:
                        break
                    if k.name == "ESC" or k.is_("q"):
                        cz.diario(f"accordatore: esce con il tasto {k.name}")
                        return 0
                    nav = k.nav
                    if k.name in ("1", "2", "3", "4", "5", "6"):
                        # per posizione, come i tasti: 1 = casella a sinistra (Mi basso, corda 6)
                        tu.select(int(k.name) - 1)
                    elif nav == "LEFT" or k.is_("z"):
                        tu.select((tu.target if tu.auto and tu.target is not None else tu.sel) - 1)
                    elif nav == "RIGHT" or k.is_("c"):
                        tu.select((tu.target if tu.auto and tu.target is not None else tu.sel) + 1)
                    elif k.is_("a"):
                        tu.set_auto()
                    elif k.is_("t"):
                        tu.set_tuning(tu.tuning + 1)
                    elif k.is_("n"):
                        view["italian"] = not view["italian"]
                    elif k.is_("m"):
                        view["muted"] = not view["muted"]
                        if view["muted"]:
                            view["toast"] = ("Suono disattivato: niente ding", time.monotonic() + 2.0)
                        else:
                            view["toast"] = ("Suono attivo: ding di prova", time.monotonic() + 2.0)
                            if not spk.busy:
                                if spk.exclusive:
                                    spk.play_with_mic_paused(mic)
                                else:
                                    spk.play()
                    elif k.is_("s"):
                        cz.diario("accordatore: apre il canzoniere (S)")
                        return EXIT_SONGBOOK
                    else:
                        continue
                    remember()
                    if time.monotonic() >= deadline:
                        break
        finally:
            mic.stop()


# ---------------------------------------------------------------- prove
def pluck(f0: float, rate: int, dur: float, weak_fundamental: bool = True,
          noise: float = 0.02, seed: int = 0) -> np.ndarray:
    """Corda pizzicata sintetica: armoniche che decadono (le alte più in fretta) + rumore.

    Con weak_fundamental la fondamentale è più debole della 2a armonica,
    come succede con un microfono piccolo sulle corde basse.
    """
    rng = np.random.default_rng(seed)
    t = np.arange(int(rate * dur)) / rate
    y = np.zeros_like(t)
    for n in range(1, 16):
        f = n * f0
        if f > rate / 2.2:
            break
        amp = (0.35 if (n == 1 and weak_fundamental) else 1.0) / n
        y += amp * np.exp(-t * (1.5 + 0.6 * n)) * np.sin(2 * np.pi * f * t + rng.uniform(0, 6.28))
    y /= np.abs(y).max()
    return y + rng.normal(0, noise, len(y))


def selftest(noise: float = 0.02, verbose: bool = True) -> int:
    """Simula quello che fa l'app: finestre ogni ~1/15 s, soglia -50 dBFS, mediana di 5.

    Misura l'errore della stima che finisce sullo schermo (mediana) e di quella grezza.
    """
    # 1) la schermata si deve poter disegnare con qualsiasi livello del microfono
    #    (il 23/11 un livello appena sopra -70 dB mandava in errore la barretta del livello)
    from PIL import Image, ImageDraw
    tu = Tuner()
    bad = 0
    for i, lvl in enumerate(np.arange(-140.0, 10.0, 0.05)):
        tu.update(i / 15, None if i % 3 else 110.0)
        view = {"italian": bool(i % 2), "muted": False, "gate_db": -50.0, "level_db": float(lvl),
                "now": i / 15, "error": None, "spk_error": None}
        try:
            draw_screen(ImageDraw.Draw(Image.new("RGB", (cz.W, cz.H))), tu, view)
        except Exception as e:
            bad += 1
            if bad == 1:
                print(f"schermata: errore con livello {lvl:.2f} dB: {e!r}")
    print(f"Schermata con 3000 livelli del microfono: {'OK' if not bad else f'{bad} errori'}")
    rate = 44100
    yin = Yin(rate)
    hop = rate // 15
    worst_med = worst_raw = total = 0.0
    shown = 0
    if verbose:
        print(f"Prova di YIN su corde sintetiche (fondamentale debole, rumore {noise:g}), 44,1 kHz:")
    names = sorted({n for _, s in TUNINGS for n in s}, key=note_midi)
    for name in names:
        row = []
        for detune in (-40.0, -7.0, 0.0, 2.5, 25.0):
            f0 = midi_freq(note_midi(name), 440.0) * 2 ** (detune / 1200)
            sig = pluck(f0, rate, 1.5, noise=noise, seed=note_midi(name)) * 8000
            hist = deque(maxlen=5)
            for end in range(yin.need, len(sig), hop):
                win = sig[end - yin.need:end]
                if level_db(win[-2048:]) <= -50:
                    hist.clear()
                    continue
                f, _ = yin.detect(win)
                if f is None:
                    continue
                worst_raw = max(worst_raw, abs(cents(f, f0)))
                hist.append(f)
                if len(hist) >= 3:
                    e = cents(float(np.median(hist)), f0)
                    worst_med = max(worst_med, abs(e))
                    row.append(abs(e))
                    shown += 1
                    total += abs(e)
        if verbose:
            print(f"  {name:>3}  {midi_freq(note_midi(name), 440):7.2f} Hz   errore sullo schermo "
                  f"max {max(row):.2f} cent, medio {np.mean(row):.2f} cent ({len(row)} letture)")
    mean = total / shown if shown else 99.0
    ok = shown > 0 and worst_med < 3.0 and mean < 0.5 and bad == 0
    print(f"Errore mostrato: medio {mean:.2f} cent, massimo {worst_med:.2f} cent "
          f"(stime grezze singole: max {worst_raw:.2f} cent)")
    print("RISULTATO:", "OK" if ok else "PROBLEMA")
    return 0 if ok else 1


def list_devices() -> int:
    print("schede audio (/proc/asound):")
    for cid, cap, pla, cd, pd in alsa_cards():
        print(f"  {cid:<16} cattura={'sì' if cap else 'no'} {cd}  riproduzione={'sì' if pla else 'no'} {pd}")
    print("microfoni che verrebbero provati, in ordine:")
    for c in capture_candidates(load_conf()["mic"] or None):
        print("  ", c)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Accordatore per chitarra (M5Stack CardputerZero)")
    ap.add_argument("--device", help="dispositivo ALSA del microfono (es. plughw:CARD=xxx,DEV=0)")
    ap.add_argument("--out", help="dispositivo ALSA per il ding")
    ap.add_argument("--rate", type=int)
    ap.add_argument("--a4", type=float, help="frequenza del La di riferimento (default 440)")
    ap.add_argument("--tol", type=float, help="tolleranza in cent per 'accordata' (default 3)")
    ap.add_argument("--gate-db", type=float, help="soglia del microfono in dBFS (default -50)")
    ap.add_argument("--no-sound", action="store_true", help="niente ding")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--list-devices", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if a.list_devices:
        return list_devices()
    return run(a) or 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Chords
"""
Canzoniere - parte dell'app Accordatore per M5Stack CardputerZero.

Mostra testi con gli accordi sopra, li traspone, scorre da solo mentre suoni
e disegna i diagrammi degli accordi per chitarra.

Le canzoni sono file di testo nella cartella ~/canzoni (crea anche alcuni esempi):
  *.cho *.chopro *.chordpro *.crd *.pro   formato ChordPro: [G]Testo con [C]accordi
  *.txt                                   "accordi sopra il testo" (come nei siti di accordi)
Sottocartelle ammesse. Codifica UTF-8 (va bene anche quella di Windows).

Tasti nell'elenco:   ↑↓ scegli · Invio apri · R ricarica · Esc torna all'accordatore
Tasti nella canzone: ↑↓ riga · ←→ o Spazio pagina · S/G (o + e -) trasponi su/giù
                     A scorrimento automatico, 1-9 velocità · F carattere
                     N nomi accordi: anglosassoni / italiani / come nel file
                     C diagrammi accordi · T accordatore · Esc elenco

Gli accordi possono essere scritti in anglosassone (C, Am, F#7) o in italiano
(Do, Lam, Fa#7, anche SOLm, SIb, Do-, Do7+).

Da terminale:
  python3 songbook.py --check file.cho        mostra come viene letto un file
  python3 songbook.py --converti file.pdf     PDF (o .txt) di accordi -> .txt in ~/canzoni,
                                              accordi in anglosassone (--italiani per lasciarli)
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys
import time
import traceback

import cz
import lingua
from lingua import t

HERE = os.path.dirname(os.path.abspath(__file__))
SONGS_DIR = os.environ.get("CZ_SONGS_DIR") or os.path.expanduser("~/canzoni")
LOG = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
                   "accordatore", "errori.log")


def log_problem(what: str, detail: str):
    """Aggiunge un problema non fatale (file illeggibile, brano che non si apre) a errori.log."""
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')}  songbook.py  {what}\n"
                    f"{detail.rstrip()}\n")
    except OSError:
        pass
CONF = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                    "accordatore", "canzoniere.json")
CHORDPRO_EXT = (".cho", ".chopro", ".chordpro", ".crd", ".pro")
TEXT_EXT = (".txt",)

# ---------------------------------------------------------------- accordi
SHARPS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLATS = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
_PC = {n: i for i, n in enumerate(SHARPS)}
_PC.update({n: i for i, n in enumerate(FLATS)})
_PC.update({"E#": 5, "B#": 0, "Fb": 4, "Cb": 11})

_SUFFIX = r"(?:maj|min|dim|aug|sus|add|m|M|\d|\+|-|#|b|\(|\)|°|ø|Δ|/(?=\d))*"
CHORD_RE = re.compile(r"^([A-G])([#b♯♭]?)(" + _SUFFIX + r")(?:/([A-G])([#b♯♭]?))?$")
# notazione italiana: Do Re Mi Fa Sol La Si (anche tutto maiuscolo: SOLm, SIb, MIB).
# Deve iniziare con la maiuscola, così parole del testo come "la", "mi", "si" non sono accordi.
_IT_ROOT = r"(DO|Do|RE|Re|MI|Mi|FA|Fa|SOL|Sol|LA|La|SI|Si)"
IT_CHORD_RE = re.compile(r"^" + _IT_ROOT + r"([#b♯♭B]?)(" + _SUFFIX + r")(?:/" + _IT_ROOT + r"([#b♯♭B]?))?$")
_IT_PC = {"do": 0, "re": 2, "mi": 4, "fa": 5, "sol": 7, "la": 9, "si": 11}
IT_SHARPS = ["Do", "Do#", "Re", "Re#", "Mi", "Fa", "Fa#", "Sol", "Sol#", "La", "La#", "Si"]
IT_FLATS = ["Do", "Reb", "Re", "Mib", "Mi", "Fa", "Solb", "Sol", "Lab", "La", "Sib", "Si"]
NAME_MODES = ["en", "it", "orig"]  # anglosassoni, italiani, come scritti nel file
NAME_LABEL = {"en": "", "it": "nomi it", "orig": "nomi orig"}
# etichetta di {start_of_chorus} / {chorus} senza testo: tradotta quando si impagina
CHORUS = object()


def _acc(a: str) -> str:
    return {"♯": "#", "♭": "b", "B": "b"}.get(a, a)


def chord_info(s: str):
    """'SIbm7' -> (10, 'm7', None, 'it', 'b', ''). None se non è un accordo."""
    s = s.strip()
    m = CHORD_RE.match(s)
    if m:
        ra, ba = _acc(m.group(2)), _acc(m.group(5) or "")
        root = _PC[m.group(1) + ra]
        bass = _PC[m.group(4) + ba] if m.group(4) else None
        return root, m.group(3), bass, "en", ra, ba
    m = IT_CHORD_RE.match(s)
    if m:
        ra, ba = _acc(m.group(2)), _acc(m.group(5) or "")
        shift = {"#": 1, "b": -1, "": 0}
        root = (_IT_PC[m.group(1).lower()] + shift[ra]) % 12
        bass = None
        if m.group(4):
            bass = (_IT_PC[m.group(4).lower()] + shift[ba]) % 12
        return root, m.group(3), bass, "it", ra, ba
    return None
# cose che possono stare su una riga di accordi senza renderla "testo"
_DECOR_RE = re.compile(r"^(\||\|\||:\||\|:|x\d+|\d+x|-+|N\.?C\.?|\(|\)|%|/|\.\.\.?)$", re.I)


def parse_chord(s: str):
    """'F#m7/C#' -> (6, 'm7', 1); vale anche 'Fa#m7/Do#'. None se non è un accordo."""
    i = chord_info(s)
    return None if i is None else i[:3]


def prefer_flats(root: int, suffix: str) -> bool:
    """Tonalità che si scrivono coi bemolle (Fa, Si♭, Mi♭... e le minori relative)."""
    minor = suffix.startswith("m") and not suffix.startswith("maj")
    if minor:
        return root in (2, 7, 0, 5, 10, 3)   # Dm Gm Cm Fm Bbm Ebm
    return root in (5, 10, 3, 8, 1, 6)       # F Bb Eb Ab Db Gb


def chord_name(root: int, suffix: str, bass, flats: bool) -> str:
    names = FLATS if flats else SHARPS
    s = names[root % 12] + suffix
    if bass is not None:
        s += "/" + names[bass % 12]
    return s


def render_chord(s: str, steps: int, flats: bool, mode: str = "orig") -> str:
    """Traspone di `steps` semitoni e scrive l'accordo nella notazione `mode`.

    Senza trasposizione ogni nota tiene il suo diesis/bemolle originale (SIb -> Bb, non A#).
    """
    info = chord_info(s)
    if info is None:
        return s
    root, suf, bass, style, ra, ba = info
    out = style if mode == "orig" else mode
    if steps == 0 and out == style:
        return s
    if out == "en" and style == "it":
        if suf.startswith("-"):
            suf = "m" + suf[1:]      # notazione italiana Do- = Cm
        suf = suf.replace("7+", "maj7")  # in italiano Do7+ è la settima maggiore
    tab = {("en", False): SHARPS, ("en", True): FLATS, ("it", False): IT_SHARPS, ("it", True): IT_FLATS}
    # con trasposizione: chi era scritto col bemolle resta col bemolle, chi col diesis col
    # diesis; le note naturali seguono la tonalità d'arrivo (`flats`)
    rf = (ra == "b") if (steps == 0 or ra) else flats
    bf = (ba == "b") if (steps == 0 or ba) else flats
    name = tab[(out, rf)][(root + steps) % 12] + suf
    if bass is not None:
        name += "/" + tab[(out, bf)][(bass + steps) % 12]
    return name


def transpose_chord(s: str, steps: int, flats: bool) -> str:
    return render_chord(s, steps, flats, "orig")


def is_chord_line(line: str) -> bool:
    toks = line.split()
    if not toks:
        return False
    chords = 0
    for t in toks:
        if parse_chord(t):
            chords += 1
        elif not _DECOR_RE.match(t):
            return False
    return chords > 0


def transpose_chord_line(line: str, steps: int, flats: bool, mode: str = "orig") -> str:
    """Traspone/rinomina una riga di soli accordi tenendo ogni accordo nella sua colonna."""
    if steps == 0 and mode == "orig":
        return line
    out = ""
    for m in re.finditer(r"\S+", line):
        tok = m.group(0)
        new = render_chord(tok, steps, flats, mode) if parse_chord(tok) else tok
        col = max(m.start(), len(out) + (1 if out else 0))
        out = out.ljust(col) + new
    return out


# ---------------------------------------------------------------- canzone
class Song:
    """Una canzone letta da file, in righe "logiche" indipendenti dalla larghezza."""

    def __init__(self, path: str):
        self.path = path
        self.title = os.path.splitext(os.path.basename(path))[0].replace("_", " ")
        self.artist = ""
        self.capo = ""
        self.key = ""
        # elementi: ("pair", [(col, accordo)], testo, ritornello)  accordi sopra il testo
        #           ("chords", riga, ritornello)                     riga di soli accordi
        #           ("text", riga, ritornello) | ("comment", riga) | ("section", riga)
        #           ("tab", riga) | ("blank",)
        self.items: list[tuple] = []
        self.chords: list[str] = []  # in ordine di apparizione, senza doppioni
        self.warnings: list[str] = []
        text = _read_text(path)
        if path.lower().endswith(TEXT_EXT):
            self._parse_plain(text)
        else:
            self._parse_chordpro(text)

    # --- comuni
    def _add_chord(self, c: str):
        if c not in self.chords and parse_chord(c):
            self.chords.append(c)

    def uses_flats(self) -> bool:
        accs = [chord_info(c)[4] for c in self.chords if chord_info(c)]
        return accs.count("b") > accs.count("#")

    # --- ChordPro
    def _parse_chordpro(self, text: str):
        chorus = False
        tab = False
        for raw in text.splitlines():
            line = raw.rstrip()
            st = line.strip()
            dm = re.match(r"^\{\s*([^:}]+?)\s*(?::\s*(.*?))?\s*\}$", st)
            if dm:
                name, val = dm.group(1).lower(), (dm.group(2) or "").strip()
                if name in ("title", "t"):
                    self.title = val or self.title
                elif name in ("subtitle", "st", "artist", "composer"):
                    self.artist = self.artist or val
                elif name == "capo":
                    self.capo = val
                elif name == "key":
                    self.key = val
                elif name in ("comment", "c", "comment_italic", "ci", "comment_box", "cb", "highlight"):
                    self.items.append(("comment", val))
                elif name in ("start_of_chorus", "soc"):
                    chorus = True
                    self.items.append(("section", val or CHORUS))
                elif name in ("end_of_chorus", "eoc"):
                    chorus = False
                elif name in ("start_of_verse", "sov", "start_of_bridge", "sob"):
                    if val:
                        self.items.append(("section", val))
                elif name in ("start_of_tab", "sot"):
                    tab = True
                elif name in ("end_of_tab", "eot"):
                    tab = False
                elif name == "chorus":
                    self.items.append(("comment", val or CHORUS))
                continue
            if tab:
                self.items.append(("tab", line))
                continue
            if st.startswith("#"):
                continue
            if not st:
                self.items.append(("blank",))
                continue
            if "[" not in line:
                self.items.append(("text", line, chorus))
                continue
            chords, lyric = [], ""
            pos = 0
            for m in re.finditer(r"\[([^\]]*)\]", line):
                lyric += line[pos:m.start()]
                chords.append((len(lyric), m.group(1).strip()))
                self._add_chord(m.group(1).strip())
                pos = m.end()
            lyric += line[pos:]
            if not lyric.strip():
                # riga di soli accordi: "[G] [C] [D]"
                self.items.append(("chords", "  ".join(c for _, c in chords), chorus))
            else:
                self.items.append(("pair", chords, lyric, chorus))

    # --- testo con accordi sopra (stile siti di accordi)
    def _parse_plain(self, text: str):
        lines = [ln.rstrip().replace("\t", "    ") for ln in text.splitlines()]
        i = 0
        while i < len(lines):
            line = lines[i]
            st = line.strip()
            if not st:
                self.items.append(("blank",))
            elif re.match(r"^\[[^\]]+\]$", st) and not parse_chord(st[1:-1]):
                self.items.append(("section", st[1:-1]))
            elif re.match(r"^[eBGDAE|]\|?[-0-9hpb/\\|~x ]*\|", st) or re.match(r"^[eBGDAEbgda]\s*\|[-0-9]", st):
                self.items.append(("tab", line))
            elif is_chord_line(line):
                for t in line.split():
                    self._add_chord(t)
                nxt = lines[i + 1] if i + 1 < len(lines) else ""
                if nxt.strip() and not is_chord_line(nxt) and not re.match(r"^\[[^\]]+\]$", nxt.strip()):
                    chords = [(m.start(), m.group(0)) for m in re.finditer(r"\S+", line)]
                    self.items.append(("pair", chords, nxt, False))
                    i += 1
                else:
                    self.items.append(("chords", line, False))
            else:
                self.items.append(("text", line, False))
            i += 1


def _read_text(path: str) -> str:
    with open(path, "rb") as f:
        data = f.read()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


# ---------------------------------------------------------------- impaginazione
def chord_row(chords: list[tuple[int, str]]) -> tuple[str, list[int]]:
    """Costruisce la riga degli accordi; ritorna (riga, colonne effettive)."""
    row, cols = "", []
    for col, name in chords:
        c = max(col, len(row) + (1 if row else 0))
        row = row.ljust(c) + name
        cols.append(c)
    return row, cols


def layout(song: Song, steps: int, ncols: int, mode: str = "en") -> list[tuple]:
    """Righe visibili: (tipo, testo, ritornello). Tipi: chord, lyric, text, comment,
    section, tab, title, info, blank."""
    flats_base = song.uses_flats()
    first = parse_chord(song.chords[0]) if song.chords else None
    if steps and first:
        flats = prefer_flats((first[0] + steps) % 12, first[1])
    else:
        flats = flats_base

    rows: list[tuple] = [("title", song.title, False)]
    info = " · ".join(x for x in (song.artist,
                                  f"capo {song.capo}" if song.capo else "",
                                  f"tonalità {render_chord(song.key, steps, flats, mode)}" if song.key else "")
                      if x)
    if info:
        rows.append(("info", info, False))
    rows.append(("blank", "", False))

    def wrap_plain(kind, s, ch):
        s = s.rstrip()
        while len(s) > ncols:
            cut = s.rfind(" ", 0, ncols + 1)
            if cut <= ncols // 3:
                cut = ncols
            rows.append((kind, s[:cut].rstrip(), ch))
            s = "  " + s[cut:].lstrip()
        rows.append((kind, s, ch))

    for it in song.items:
        k = it[0]
        if k == "blank":
            if rows and rows[-1][0] != "blank":
                rows.append(("blank", "", False))
        elif k == "comment":
            wrap_plain("comment", "» " + (t("Ritornello") if it[1] is CHORUS else it[1]), False)
        elif k == "section":
            rows.append(("section", t("Ritornello") if it[1] is CHORUS else it[1], False))
        elif k == "tab":
            rows.append(("tab", it[1][:ncols], False))
        elif k == "text":
            wrap_plain("text", it[1], it[2])
        elif k == "chords":
            line = transpose_chord_line(it[1], steps, flats, mode)
            wrap_plain("chord", line, it[2])
        elif k == "pair":
            chords = [(c, render_chord(n, steps, flats, mode)) for c, n in it[1]]
            crow, _ = chord_row(chords)
            lyric = it[2].rstrip()
            width = max(len(crow), len(lyric))
            crow, lyric = crow.ljust(width), lyric.ljust(width)
            first_piece = True
            while crow.strip() or lyric.strip():
                limit = ncols if first_piece else ncols - 2
                if len(lyric.rstrip()) <= limit and len(crow.rstrip()) <= limit:
                    cut = max(len(lyric), len(crow))
                else:
                    cut = lyric.rfind(" ", 0, limit + 1)
                    if cut <= limit // 3:
                        cut = limit
                    while 0 < cut < len(crow) and crow[cut - 1] != " " and crow[cut] != " ":
                        cut -= 1  # non spezzare il nome di un accordo
                    if cut <= 0:
                        cut = limit
                pre = "" if first_piece else "  "
                c_part, l_part = crow[:cut].rstrip(), lyric[:cut].rstrip()
                if c_part:
                    rows.append(("chord", pre + c_part, it[3]))
                if l_part:
                    rows.append(("lyric", pre + l_part, it[3]))
                crow, lyric = crow[cut:], lyric[cut:]
                if not first_piece or c_part or l_part:
                    first_piece = False
                # salta lo spazio dove si è andati a capo
                while crow[:1] == " " and lyric[:1] == " ":
                    crow, lyric = crow[1:], lyric[1:]
    while rows and rows[-1][0] == "blank":
        rows.pop()
    return rows


# ---------------------------------------------------------------- diagrammi
# corde dalla 6a (Mi basso) alla 1a; -1 = non suonare, 0 = vuota
TUNING = [4, 9, 2, 7, 11, 4]  # E A D G B E (classi di altezza)

QUALITY = {  # suffisso normalizzato -> intervalli
    "": {0, 4, 7}, "m": {0, 3, 7}, "7": {0, 4, 7, 10}, "maj7": {0, 4, 7, 11},
    "m7": {0, 3, 7, 10}, "sus2": {0, 2, 7}, "sus4": {0, 5, 7}, "dim": {0, 3, 6},
    "dim7": {0, 3, 6, 9}, "aug": {0, 4, 8}, "6": {0, 4, 7, 9}, "m6": {0, 3, 7, 9},
    "9": {0, 4, 7, 10, 2}, "m7b5": {0, 3, 6, 10}, "7sus4": {0, 5, 7, 10}, "5": {0, 7},
    "add9": {0, 2, 4, 7},
}
ALIASES = {
    "maj": "", "M": "", "min": "m", "-": "m", "M7": "maj7", "Δ": "maj7", "Δ7": "maj7",
    "ma7": "maj7", "j7": "maj7", "min7": "m7", "-7": "m7", "sus": "sus4", "°": "dim",
    "o": "dim", "°7": "dim7", "o7": "dim7", "+": "aug", "aug5": "aug", "+5": "aug",
    "ø": "m7b5", "ø7": "m7b5", "m7-5": "m7b5", "m7(b5)": "m7b5", "7sus": "7sus4",
    "add2": "add9", "7+": "maj7", "2": "sus2", "4": "sus4", "min6": "m6", "-6": "m6",
}
# accordi "aperti" comuni (diteggiature classiche)
OPEN = {
    ("C", ""): [-1, 3, 2, 0, 1, 0], ("C", "7"): [-1, 3, 2, 3, 1, 0],
    ("C", "maj7"): [-1, 3, 2, 0, 0, 0], ("C", "add9"): [-1, 3, 2, 0, 3, 0],
    ("D", ""): [-1, -1, 0, 2, 3, 2], ("D", "m"): [-1, -1, 0, 2, 3, 1],
    ("D", "7"): [-1, -1, 0, 2, 1, 2], ("D", "m7"): [-1, -1, 0, 2, 1, 1],
    ("D", "maj7"): [-1, -1, 0, 2, 2, 2], ("D", "sus2"): [-1, -1, 0, 2, 3, 0],
    ("D", "sus4"): [-1, -1, 0, 2, 3, 3],
    ("E", ""): [0, 2, 2, 1, 0, 0], ("E", "m"): [0, 2, 2, 0, 0, 0],
    ("E", "7"): [0, 2, 0, 1, 0, 0], ("E", "m7"): [0, 2, 2, 0, 3, 0],
    ("E", "maj7"): [0, 2, 1, 1, 0, 0], ("E", "sus4"): [0, 2, 2, 2, 0, 0],
    ("F", "maj7"): [-1, -1, 3, 2, 1, 0],
    ("G", ""): [3, 2, 0, 0, 0, 3], ("G", "7"): [3, 2, 0, 0, 0, 1],
    ("G", "maj7"): [3, 2, 0, 0, 0, 2],
    ("A", ""): [-1, 0, 2, 2, 2, 0], ("A", "m"): [-1, 0, 2, 2, 1, 0],
    ("A", "7"): [-1, 0, 2, 0, 2, 0], ("A", "m7"): [-1, 0, 2, 0, 1, 0],
    ("A", "maj7"): [-1, 0, 2, 1, 2, 0], ("A", "sus2"): [-1, 0, 2, 2, 0, 0],
    ("A", "sus4"): [-1, 0, 2, 2, 3, 0],
    ("B", "7"): [-1, 2, 1, 2, 0, 2],
}
# forme mobili: (corda della fondamentale 6 o 5, scarti dal tasto della fondamentale)
X = None
SHAPES = {
    "": [(6, [0, 2, 2, 1, 0, 0]), (5, [X, 0, 2, 2, 2, 0])],
    "m": [(6, [0, 2, 2, 0, 0, 0]), (5, [X, 0, 2, 2, 1, 0])],
    "7": [(6, [0, 2, 0, 1, 0, 0]), (5, [X, 0, 2, 0, 2, 0])],
    "m7": [(6, [0, 2, 0, 0, 0, 0]), (5, [X, 0, 2, 0, 1, 0])],
    "maj7": [(5, [X, 0, 2, 1, 2, 0]), (6, [0, X, 1, 1, 0, X])],
    "sus4": [(6, [0, 2, 2, 2, 0, 0]), (5, [X, 0, 2, 2, 3, 0])],
    "sus2": [(5, [X, 0, 2, 2, 0, 0]), (6, [0, 2, 4, 4, 0, 0])],
    "7sus4": [(5, [X, 0, 2, 0, 3, 0]), (6, [0, 2, 0, 2, 0, 0])],
    "6": [(5, [X, 0, 2, 2, 2, 2]), (6, [0, 2, 2, 1, 2, 0])],
    "m6": [(5, [X, 0, 2, 2, 1, 2]), (6, [0, 2, 2, 0, 2, 0])],
    "9": [(5, [X, 0, -1, 0, 0, 0]), (6, [0, 2, 0, 1, 0, 2])],
    "dim": [(5, [X, 0, 1, 2, 1, X]), (6, [0, 1, 2, 0, X, X])],
    "dim7": [(5, [X, 0, 1, -1, 1, X]), (6, [0, 1, 2, 0, 2, 0])],
    "aug": [(5, [X, 0, -1, -2, -2, X]), (6, [0, X, 2, 1, 1, 0])],
    "m7b5": [(5, [X, 0, 1, 0, 1, X]), (6, [0, X, 0, 0, -1, X])],
    "5": [(6, [0, 2, 2, X, X, X]), (5, [X, 0, 2, 2, X, X])],
    "add9": [(5, [X, 0, 2, 4, 2, X]), (6, [0, 2, 4, 1, 0, 0])],
}


def normalize_quality(suffix: str) -> tuple[str, bool]:
    """Ritorna (qualità nota, semplificato?)."""
    s = suffix.replace("(", "").replace(")", "") if suffix not in ALIASES else suffix
    if s in QUALITY:
        return s, False
    if s in ALIASES:
        return ALIASES[s], False
    if suffix in ALIASES:
        return ALIASES[suffix], False
    # estensioni non gestite (m9, 13, 7#9...): mostra l'accordo di base più vicino
    minor = s.startswith(("m", "min", "-")) and not s.startswith(("maj", "M"))
    if "add" in s or "sus" in s:
        return ("m" if minor else ""), True
    if s.startswith("maj") or s.startswith("M"):
        return "maj7", True
    if minor:
        return ("m7" if "7" in s or "9" in s or "11" in s else "m"), True
    if any(d in s for d in ("7", "9", "11", "13")):
        return "7", True
    return "", True


def fingering(chord: str):
    """Diteggiatura [6a..1a] per un accordo, più un flag "semplificato". None se illeggibile."""
    p = parse_chord(chord)
    if p is None:
        return None
    root, suffix, _bass = p
    q, simplified = normalize_quality(suffix)
    open_name = SHARPS[root]
    if (open_name, q) in OPEN:
        return OPEN[(open_name, q)], simplified
    best = None
    for string, offs in SHAPES.get(q, []):
        r = (root - TUNING[0 if string == 6 else 1]) % 12
        while r + min(o for o in offs if o is not None) < 0:
            r += 12
        frets = [-1 if o is None else r + o for o in offs]
        hi = max(f for f in frets if f >= 0)
        score = hi + (0 if string == 6 else 0.5)
        if best is None or score < best[0]:
            best = (score, frets)
    if best is None:
        return None
    return best[1], simplified


def sounding_pcs(frets) -> set[int]:
    return {(TUNING[i] + f) % 12 for i, f in enumerate(frets) if f >= 0}


def draw_diagram(d, x, y, w, chord: str):
    """Diagramma di un accordo in un riquadro largo w, alto ~118 px."""
    res = fingering(chord)
    fn = cz.font(13, bold=True)
    name = ("≈" if res and res[1] else "") + chord
    d.text((x + w // 2 - cz.text_w(d, name, fn) // 2, y), name, font=fn, fill=cz.FG)
    if res is None:
        d.text((x + 4, y + 40), t("n.d."), font=cz.font(11), fill=cz.DIM)
        return
    frets = res[0]
    sp = (w - 16) / 5          # distanza tra le corde
    gx0 = x + 8
    gy0 = y + 32
    fh = 19                    # altezza di un tasto
    nfr = 4
    played = [f for f in frets if f > 0]
    base = 1
    if played and max(played) > nfr:
        base = min(played)
    if played:
        nfr = max(nfr, max(played) - base + 1)
    fh = min(fh, 80 // nfr)
    # griglia
    for i in range(6):
        sx = gx0 + i * sp
        d.line([(sx, gy0), (sx, gy0 + nfr * fh)], fill=cz.DIM)
    for j in range(nfr + 1):
        d.line([(gx0, gy0 + j * fh), (gx0 + 5 * sp, gy0 + j * fh)], fill=cz.DIM)
    if base == 1:
        d.rectangle([gx0, gy0 - 2, gx0 + 5 * sp, gy0], fill=cz.FG)  # capotasto
    else:
        lab = f"{base}° tasto"
        f9 = cz.font(9)
        d.text((gx0 + 5 * sp / 2 - cz.text_w(d, lab, f9) / 2, gy0 + nfr * fh + 2), lab, font=f9, fill=cz.WARN)
    # barré: stesso tasto più basso su almeno 3 corde
    if played:
        low = min(played)
        idx = [i for i, f in enumerate(frets) if f == low]
        # barré vero: il basso è premuto a quel tasto e ci sono 3+ corde, oppure le due
        # corde esterne della forma "La" (5a e 1a). Il La aperto non è un barré.
        bass_i = next(i for i, f in enumerate(frets) if f >= 0)
        if low >= 1 and frets[bass_i] == low and (len(idx) >= 3 or (len(idx) == 2 and idx[-1] - idx[0] >= 4)):
            cy = gy0 + (low - base + 0.5) * fh
            d.rounded_rectangle([gx0 + idx[0] * sp - 4, cy - 4, gx0 + idx[-1] * sp + 4, cy + 4],
                                radius=4, fill=cz.ACCENT)
    for i, f in enumerate(frets):
        sx = gx0 + i * sp
        if f < 0:
            d.line([(sx - 3, gy0 - 11), (sx + 3, gy0 - 5)], fill=cz.BAD, width=2)
            d.line([(sx - 3, gy0 - 5), (sx + 3, gy0 - 11)], fill=cz.BAD, width=2)
        elif f == 0:
            d.ellipse([sx - 3, gy0 - 11, sx + 3, gy0 - 5], outline=cz.FG)
        else:
            cy = gy0 + (f - base + 0.5) * fh
            d.ellipse([sx - 4, cy - 4, sx + 4, cy + 4], fill=cz.ACCENT)


# ---------------------------------------------------------------- impostazioni
def load_conf() -> dict:
    try:
        with open(CONF, encoding="utf-8") as f:
            c = json.load(f)
            if isinstance(c, dict) and isinstance(c.get("canzoni", {}), dict):
                return c
    except (OSError, ValueError):
        pass
    return {}


def save_conf(conf: dict):
    try:
        os.makedirs(os.path.dirname(CONF), exist_ok=True)
        tmp = CONF + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(conf, f, indent=1, ensure_ascii=False)
        os.replace(tmp, CONF)
    except (OSError, ValueError):
        pass


# ---------------------------------------------------------------- esempi (pubblico dominio)
EXAMPLES = {
    "Amazing Grace.cho": """\
{title: Amazing Grace}
{subtitle: John Newton, 1779 - pubblico dominio}
{key: G}
{comment: 3/4, lenta. Arrangiamento semplice di esempio}

[G]Amazing [G7]grace! How [C]sweet the [G]sound
That [G]saved a [Em]wretch like [D]me! [D7]
I [G]once was [G7]lost, but [C]now am [G]found;
Was [G]blind, but [D]now I [G]see.

[G]'Twas grace that [G7]taught my [C]heart to [G]fear,
And [G]grace my [Em]fears re[D]lieved; [D7]
How [G]precious [G7]did that [C]grace ap[G]pear
The [G]hour I [D]first be[G]lieved!
""",
    "Greensleeves.txt": """\
Tradizionale inglese, XVI secolo - pubblico dominio

[Strofa]
Am       G         Am    E
Alas, my love, you do me wrong,
Am         G   E
To cast me off discourteously.
Am         G         Am       E
For I have loved you well and long,
Am            E    Am
Delighting in your company.

[Ritornello]
C            G       Am E
Greensleeves was all my joy
C            G      E
Greensleeves was my delight,
C            G      Am       E
Greensleeves was my heart of gold,
Am             E    Am
And who but my lady Greensleeves.
""",
    "Quel mazzolin di fiori.cho": """\
{title: Quel mazzolin di fiori}
{subtitle: Canto popolare - pubblico dominio}
{key: C}

[C]Quel mazzolin di [G]fiori
che vien dalla mon[C]tagna
e guarda ben che non si [F]ba[C]gna
che lo [G]voglio rega[C]lar.

[C]Lo voglio rega[G]lare
perché l'è un bel maz[C]zetto
lo voglio dare al mio mo[F]ret[C]to
questa [G]sera quando [C]vien.
""",
    "Esercizio arpeggio.cho": """\
{title: Esercizio: arpeggio Am - C - G - E}
{subtitle: esempio di intavolatura}
{comment: Pollice sui bassi, poi indice-medio-anulare}

{start_of_tab}
   Am        C         G         E
e|-------0-|-------0-|-------3-|-------0-|
B|-----1---|-----1---|-----0---|-----0---|
G|---2-----|---0-----|---0-----|---1-----|
D|---------|---------|---------|---------|
A|-0-------|-3-------|---------|---------|
E|---------|---------|-3-------|-0-------|
{end_of_tab}

[Am]Ripeti quattro volte, [C]poi cambia [G]accordo [E]
""",
}


def ensure_songs_dir() -> bool:
    """Crea ~/canzoni con gli esempi la prima volta. True se ha creato qualcosa."""
    if os.path.isdir(SONGS_DIR):
        return False
    try:
        os.makedirs(SONGS_DIR, exist_ok=True)
        for name, text in EXAMPLES.items():
            with open(os.path.join(SONGS_DIR, name), "w", encoding="utf-8") as f:
                f.write(text)
        return True
    except OSError:
        return False


SKIPPED: list[str] = []


def find_songs() -> list[Song]:
    SKIPPED.clear()
    paths = []
    for ext in CHORDPRO_EXT + TEXT_EXT:
        paths += glob.glob(os.path.join(SONGS_DIR, "**", "*" + ext), recursive=True)
        paths += glob.glob(os.path.join(SONGS_DIR, "**", "*" + ext.upper()), recursive=True)
    songs = []
    for p in sorted(set(paths), key=lambda s: s.lower()):
        try:
            songs.append(Song(p))
        except Exception:
            SKIPPED.append(p)
            log_problem(f"file non letto: {p}", traceback.format_exc())
    songs.sort(key=lambda s: s.title.lower())
    return songs


# ---------------------------------------------------------------- schermate
FONT_SIZES = [10, 12, 14]
STYLE = {  # tipo riga -> (grassetto, colore)
    "chord": (True, cz.ACCENT), "lyric": (False, cz.FG), "text": (False, cz.FG),
    "comment": (False, cz.WARN), "section": (True, cz.OK), "tab": (False, cz.FG),
    "title": (True, cz.FG), "info": (False, cz.DIM), "blank": (False, cz.FG),
}


class Viewer:
    def __init__(self, song: Song, conf: dict):
        self.song = song
        self.conf = conf
        sc = conf.setdefault("canzoni", {}).get(song.path, {})
        self.steps = int(sc.get("trasposizione", 0))
        self.mode = conf.get("nomi_accordi", "en")
        if self.mode not in NAME_MODES:
            self.mode = "en"
        self.font_i = int(conf.get("carattere", 1)) % len(FONT_SIZES)
        self.speed = int(conf.get("velocita", 3))
        self.auto = False
        self.top = 0.0          # prima riga visibile (frazionaria per lo scorrimento fluido)
        self.rows: list[tuple] = []
        self._key = None
        self.page = None        # None = testo, int = pagina dei diagrammi

    def metrics(self, d):
        size = FONT_SIZES[self.font_i]
        f = cz.font(size, mono=True)
        cw = max(1, cz.text_w(d, "M" * 10, f) // 10)
        ncols = (cz.W - 10) // cw
        rh = size + 3
        nrows = cz.BODY_H // rh
        return size, ncols, rh, nrows

    def ensure_layout(self, d):
        size, ncols, rh, nrows = self.metrics(d)
        key = (self.steps, ncols, self.mode, lingua.current())
        if key != self._key:
            self.rows = layout(self.song, self.steps, ncols, self.mode)
            self._key = key
        return size, ncols, rh, nrows

    def max_top(self, nrows):
        return max(0, len(self.rows) - nrows)

    def remember(self):
        songs = self.conf.setdefault("canzoni", {})
        if self.steps:
            songs[self.song.path] = {"trasposizione": self.steps}
        else:
            songs.pop(self.song.path, None)
        self.conf["carattere"] = self.font_i
        self.conf["velocita"] = self.speed
        self.conf["nomi_accordi"] = self.mode
        save_conf(self.conf)

    def header_right(self) -> str:
        bits = []
        if self.song.capo:
            bits.append(f"capo {self.song.capo}")
        if self.steps:
            bits.append(f"{self.steps:+d} st")
        if self.auto:
            bits.append(f"auto {self.speed}")
        if NAME_LABEL.get(self.mode):
            bits.append(t(NAME_LABEL[self.mode]))
        return " · ".join(bits)

    def draw_text(self, app):
        img, d = app.canvas()
        size, ncols, rh, nrows = self.ensure_layout(d)
        self.top = max(0.0, min(self.top, self.max_top(nrows)))
        first = int(self.top)
        off = int((self.top - first) * rh)
        y0 = cz.BODY_TOP + 1 - off
        for i in range(first, min(len(self.rows), first + nrows + 2)):
            kind, text, chorus = self.rows[i]
            y = y0 + (i - first) * rh
            if y > cz.H - cz.FOOTER_H - 2:
                break
            bold, color = STYLE.get(kind, (False, cz.FG))
            if kind == "title":
                f = cz.font(size + 2, bold=True)
            elif kind in ("info", "section"):
                f = cz.font(size, bold=bold)
            else:
                f = cz.font(size, bold=bold, mono=True)
            x = 6
            if chorus:
                d.line([(2, y), (2, y + rh - 1)], fill=cz.OK, width=2)
            if kind in ("title", "info", "section"):
                text = cz.ellipsize(d, text, f, cz.W - 12)
            d.text((x, y), text, font=f, fill=color)
        # ricopri ciò che sborda sotto l'header (scorrimento fluido)
        d.rectangle([0, 0, cz.W, cz.HEADER_H - 1], fill=cz.BAR)
        cz.header(d, cz.ellipsize(d, self.song.title, cz.font(12, bold=True), 170),
                  right=self.header_right())
        if len(self.rows) > nrows:  # barra di scorrimento
            track = cz.BODY_H - 4
            th = max(8, int(track * nrows / len(self.rows)))
            ty = cz.BODY_TOP + 2 + int((track - th) * self.top / max(1, self.max_top(nrows)))
            d.rectangle([cz.W - 3, ty, cz.W - 1, ty + th], fill=cz.DIM)
        cz.footer(d, t("↑↓␣ scorri  S/G trasponi  A auto  C accordi  T accorda  Esc"))
        app.show(img)

    def draw_chords(self, app):
        img, d = app.canvas()
        flats = self.song.uses_flats()
        first = parse_chord(self.song.chords[0]) if self.song.chords else None
        if self.steps and first:
            flats = prefer_flats((first[0] + self.steps) % 12, first[1])
        names = []
        for c in self.song.chords:
            nm = render_chord(c, self.steps, flats, self.mode)
            if nm not in names:
                names.append(nm)
        per = 5
        pages = max(1, (len(names) + per - 1) // per)
        self.page = max(0, min(self.page or 0, pages - 1))
        cz.header(d, t("Accordi"), right=f"{self.page + 1}/{pages}" + (f" · {self.steps:+d} st" if self.steps else ""))
        if not names:
            d.text((8, 50), t("Nessun accordo in questa canzone."), font=cz.font(12), fill=cz.DIM)
        bw = (cz.W - 4) // per
        for i, n in enumerate(names[self.page * per:(self.page + 1) * per]):
            draw_diagram(d, 2 + i * bw, cz.BODY_TOP + 3, bw, n)
        cz.footer(d, t("←→ pagina   ≈ = versione semplificata   C/Esc torna al testo"))
        app.show(img)

    def draw(self, app):
        if self.page is not None:
            self.draw_chords(app)
        else:
            self.draw_text(app)

    def handle(self, k, app) -> str | None:
        """Ritorna "back" per tornare all'elenco, "tuner" per l'accordatore."""
        img, d = app.canvas()
        _, _, _, nrows = self.ensure_layout(d)
        nav = k.nav
        if self.page is not None:  # schermata diagrammi
            if k.name == "ESC" or k.is_("c"):
                self.page = None
            elif nav == "RIGHT" or k.name == "SPACE" or nav == "DOWN":
                self.page += 1
            elif nav == "LEFT" or nav == "UP":
                self.page = max(0, self.page - 1)
            return None
        if k.name == "ESC":
            self.remember()
            return "back"
        if nav == "DOWN":
            self.top = min(self.max_top(nrows), int(self.top) + 1)
        elif nav == "UP":
            self.top = max(0, int(self.top) - 1)
        elif nav == "RIGHT" or k.name in ("SPACE", "PAGEDOWN"):
            self.top = min(self.max_top(nrows), int(self.top) + nrows - 1)
        elif nav == "LEFT" or k.name == "PAGEUP":
            self.top = max(0, int(self.top) - nrows + 1)
        elif k.name == "HOME":
            self.top = 0
        elif k.is_("s") or k.char in ("+", "="):
            self.steps = _wrap_steps(self.steps + 1)
            self.remember()
        elif k.is_("g") or k.char in ("-", "_"):
            self.steps = _wrap_steps(self.steps - 1)
            self.remember()
        elif k.is_("0") and not self.auto:
            self.steps = 0
            self.remember()
        elif k.is_("a"):
            self.auto = not self.auto
        elif k.char and k.char.isdigit() and k.char != "0":
            self.speed = int(k.char)
            self.auto = True
            self.remember()
        elif k.is_("n"):
            self.mode = NAME_MODES[(NAME_MODES.index(self.mode) + 1) % len(NAME_MODES)]
            self._key = None
            self.remember()
        elif k.is_("f"):
            self.font_i = (self.font_i + 1) % len(FONT_SIZES)
            self._key = None
            self.remember()
        elif k.is_("c"):
            self.page = 0
            self.auto = False
        elif k.is_("l"):
            lingua.toggle()
        elif k.is_("t"):
            self.remember()
            return "tuner"
        return None

    def tick(self, dt: float, app):
        if not self.auto or self.page is not None:
            return
        img, d = app.canvas()
        _, _, _, nrows = self.ensure_layout(d)
        self.top += dt * self.speed * 0.12  # righe al secondo: 0,12 x velocità
        if self.top >= self.max_top(nrows):
            self.top = self.max_top(nrows)
            self.auto = False


def _wrap_steps(n: int) -> int:
    """Trasposizione tenuta tra -6 e +6 semitoni (oltre è la stessa tonalità all'ottava)."""
    n = ((n + 6) % 12) - 6
    return n


def draw_list(app, lv, songs, note: str = ""):
    img, d = app.canvas()
    cz.header(d, t("Canzoniere"), right=t("{n} brani", n=len(songs)))
    if not songs:
        f = cz.font(11)
        y = cz.BODY_TOP + 6
        msg = t("Nessuna canzone in {dir}. Copiaci file .cho (ChordPro) o .txt con "
                "gli accordi sopra il testo, poi premi R.", dir=SONGS_DIR)
        for line in cz.wrap(d, msg, f, cz.W - 14):
            d.text((7, y), line, font=f, fill=cz.FG)
            y += 15
    else:
        def render(dr, s, x, y, sel):
            # righe da 26 px (5 per schermata): titolo (pixel 0-15) + sottotitolo (13-24)
            # stanno dentro l'evidenziazione (0-24) e non sbordano nella riga sotto
            dr.text((x, y - 1), cz.ellipsize(dr, s.title, cz.font(13, bold=True), cz.W - 20),
                    font=cz.font(13, bold=True), fill=cz.FG)
            sub = s.artist or os.path.relpath(s.path, SONGS_DIR)
            dr.text((x, y + 12), cz.ellipsize(dr, sub, cz.font(10), cz.W - 20),
                    font=cz.font(10), fill=cz.FG if sel else cz.DIM)
        lv.draw(d, songs, render)
    cz.footer(d, note or t("↑↓ scegli   ⏎ apri   R ricarica   L English   Esc accordatore"))
    app.show(img)


def run_tuner():
    """Accordatore "sopra" la canzone: all'uscita si torna esattamente qui."""
    tuner = os.path.join(HERE, "tuner.py")
    if os.path.exists(tuner):
        env = dict(os.environ, CHITARRA_DA_CANZONE="1")
        import tempfile
        with tempfile.TemporaryFile() as err:
            rc = subprocess.run([sys.executable, "-X", "faulthandler", tuner], env=env,
                                stderr=err).returncode
            if rc not in (0, 10):
                err.seek(0)
                log_problem(f"accordatore aperto dalla canzone, codice {rc}",
                            err.read().decode("utf-8", errors="replace") or "(nessun messaggio)")


def drain(app, seconds=0.3):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.wait(0.05)


def main() -> int:
    import signal

    def on_term(*_):
        cz.diario("canzoniere: ricevuto SIGTERM")
        sys.exit(0)
    signal.signal(signal.SIGTERM, on_term)
    created = ensure_songs_dir()
    conf = load_conf()
    songs = find_songs()
    lv = cz.ListView(row_h=26)
    viewer: Viewer | None = None
    note = t("Creata {dir} con alcuni esempi", dir=SONGS_DIR) if created else ""
    if SKIPPED:
        note = t("{n} file non letti: vedi errori.log", n=len(SKIPPED))
    first = True
    while True:
        action = None
        with cz.App(t("Canzoniere")) as app:
            if not first:
                drain(app)
            first = False
            last = time.monotonic()
            while action is None:
                if viewer is None:
                    draw_list(app, lv, songs, note)
                    k = app.wait()
                    note = ""
                    if k is None:
                        continue
                    if k.name == "ESC":
                        cz.diario("canzoniere: esce con Esc dall'elenco")
                        return 0
                    if lv.key(k, len(songs)):
                        continue
                    if k.is_("r"):
                        songs = find_songs()
                        lv.sel = min(lv.sel, max(0, len(songs) - 1))
                        note = t("{n} brani in {dir}", n=len(songs), dir=SONGS_DIR)
                        if SKIPPED:
                            note += t(", {n} non letti (errori.log)", n=len(SKIPPED))
                    elif k.is_("l"):
                        note = lingua.toggle()
                    elif k.name == "ENTER" and songs:
                        viewer = Viewer(songs[lv.sel], conf)
                        last = time.monotonic()
                    continue
                try:
                    viewer.draw(app)
                    k = app.wait(0.08 if viewer.auto else None)
                    now = time.monotonic()
                    viewer.tick(now - last, app)
                    last = now
                    if k is None:
                        continue
                    r = viewer.handle(k, app)
                except Exception:
                    # un brano problematico non deve chiudere il canzoniere
                    log_problem(f"errore nel brano {viewer.song.path}", traceback.format_exc())
                    note = t("Errore in \"{titolo}\": vedi errori.log", titolo=viewer.song.title)
                    viewer = None
                    continue
                if r == "back":
                    cz.diario(f"canzoniere: Esc, dal brano all'elenco ({viewer.song.title})")
                    viewer = None
                elif r == "tuner":
                    action = "tuner"
        if action == "tuner":  # schermo e tastiera liberi: lancia l'accordatore e torna qui
            run_tuner()


# ---------------------------------------------------------------- convertitore
_JUNK_RE = re.compile(r"(https?://|www\.|\.(com|it|net|org)\b|^\s*pag(ina|\.)?\s*\d+|^\s*\d+\s*(/|di)\s*\d+\s*$|^\s*\d+\s*$)", re.I)


def _pdf_lines(path: str) -> list[str]:
    """Testo del PDF riga per riga. Gli accordi vengono agganciati alla lettera esatta
    su cui stanno nel PDF (coordinate di ogni parola), non stimati a colonne."""
    import html
    try:
        out = subprocess.run(["pdftotext", "-bbox", "-enc", "UTF-8", path, "-"],
                             capture_output=True, text=True, check=True).stdout
    except FileNotFoundError:
        raise SystemExit("Manca pdftotext: sudo apt install poppler-utils")
    except subprocess.CalledProcessError as e:
        raise SystemExit(f"pdftotext non riesce a leggere il PDF: {e.stderr.strip()[:200]}")
    word_re = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>')
    pages = []
    for page in re.split(r"<page\b", out)[1:]:
        words = [(float(a), float(b), float(c), float(d), html.unescape(t))
                 for a, b, c, d, t in word_re.findall(page)]
        # righe: parole con lo stesso centro verticale
        words.sort(key=lambda w: ((w[1] + w[3]) / 2, w[0]))
        lines: list[list] = []
        for w in words:
            yc, h = (w[1] + w[3]) / 2, w[3] - w[1]
            if lines and abs(lines[-1][0] - yc) < h * 0.45:
                lines[-1][1].append(w)
            else:
                lines.append([yc, [w]])
        for ln in lines:
            ln[1].sort(key=lambda w: w[0])
        pages.append(lines)

    def height(ws):
        hs = sorted(w[3] - w[1] for w in ws)
        return hs[len(hs) // 2]

    def is_chords(ws):
        return is_chord_line(" ".join(w[4] for w in ws))

    # il corpo della canzone ha la dimensione di carattere delle righe di accordi:
    # dopo l'inizio, le righe con un carattere diverso sono del sito (note, avvisi, link),
    # tranne autori/copyright che conviene tenere con la canzone
    ch = [height(ws) for lines in pages for _, ws in lines if is_chords(ws)]
    body_h = sorted(ch)[len(ch) // 2] if ch else None
    credit_re = re.compile(r"^(credits|autor[ei]|testo|musica|copyright|©)\b", re.I)
    section_re = re.compile(r"^\[?(intro|strofa|rit|ritornello|strumentale|bridge|ponte|coda|finale|"
                            r"verse|chorus|solo|outro|pre-?chorus|special|interludio)\b", re.I)
    started = False
    for lines in pages:
        kept, cont = [], False
        for y, ws in lines:
            if is_chords(ws):
                started = True
            same = body_h is None or abs(height(ws) - body_h) <= 0.12 * body_h
            text = " ".join(w[4] for w in ws)
            credit = bool(credit_re.match(text))
            section = len(ws) <= 3 and (bool(section_re.match(text)) or text.endswith(":"))
            short_cont = cont and len(ws) <= 3  # es. "S.R.L" che continua il copyright
            if not started or same or credit or section or short_cont:
                kept.append([y, ws])
            cont = credit and started and not same
        pages[pages.index(lines)] = kept

    result: list[str] = []
    for lines in pages:
        if not lines:
            continue
        # larghezza media di un carattere e passo tra righe, per spazi e righe vuote
        cws = [(w[2] - w[0]) / len(w[4]) for ln in lines for w in ln[1] if len(w[4]) > 2]
        cw = sorted(cws)[len(cws) // 2] if cws else 5.0
        gaps = [b[0] - a[0] for a, b in zip(lines, lines[1:])]
        step = sorted(gaps)[len(gaps) // 2] if gaps else 12.0
        left = min(w[0] for ln in lines for w in ln[1])

        i = 0
        prev_y = None
        while i < len(lines):
            y, ws = lines[i]
            if prev_y is not None and y - prev_y > step * 1.6:
                result.append("")
            prev_y = y
            if is_chords(ws):
                nxt = lines[i + 1] if i + 1 < len(lines) else None
                if nxt and not is_chords(nxt[1]) and nxt[0] - y < step * 1.3:
                    # testo sotto: posizione x di ogni carattere della riga di testo
                    lyric, xs = "", []
                    for w in nxt[1]:
                        if lyric:
                            lyric += " "
                            xs.append(w[0] - cw / 2)
                        n = max(1, len(w[4]))
                        for k in range(len(w[4])):
                            xs.append(w[0] + (w[2] - w[0]) * k / n)
                        lyric += w[4]
                    chords = []
                    for w in ws:
                        x = w[0]
                        if xs and x > xs[-1] + cw:
                            col = len(lyric) + round((x - xs[-1]) / cw) - 1
                        else:
                            col = min(range(len(xs)), key=lambda k: abs(xs[k] - x)) if xs else 0
                        chords.append((col, w[4]))
                    row, _ = chord_row(chords)
                    result.append(row)
                    result.append(lyric)
                    prev_y = nxt[0]
                    i += 2
                    continue
                row = ""
                for w in ws:  # riga di soli accordi: rispetta le distanze
                    col = max(round((w[0] - left) / cw), len(row) + (1 if row else 0))
                    row = row.ljust(col) + w[4]
                result.append(row)
            else:
                result.append(" ".join(w[4] for w in ws))
            i += 1
        result.append("")
    return result


def _clean_title(path: str) -> str:
    t = os.path.splitext(os.path.basename(path))[0]
    t = re.sub(r"^[0-9a-f]{8}-", "", t)           # prefisso aggiunto da alcuni upload
    t = t.replace("_", " ")
    t = re.sub(r"\s*\baccordi\b.*$", "", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip(" -")
    if t.isupper():
        t = t.capitalize()
    return t or "Canzone"


def convert(path: str, out: str | None = None, mode: str = "en") -> dict:
    """PDF o .txt con accordi -> .txt pulito per il canzoniere. Ritorna un riepilogo."""
    if path.lower().endswith(".pdf"):
        lines = _pdf_lines(path)
    else:
        lines = _read_text(path).replace("\t", "    ").splitlines()
    names: dict[str, str] = {}
    res: list[str] = []
    n_chord = n_junk = 0
    for ln in lines:
        ln = ln.rstrip().replace("\f", "")
        if ln and _JUNK_RE.search(ln) and not is_chord_line(ln):
            n_junk += 1
            continue
        if is_chord_line(ln):
            n_chord += 1
            new = transpose_chord_line(ln, 0, False, mode)
            for tok in ln.split():
                if parse_chord(tok):
                    names[tok] = render_chord(tok, 0, False, mode)
            ln = new
        if not ln and res and not res[-1]:
            continue  # al massimo una riga vuota di fila
        res.append(ln)
    while res and not res[0]:
        res.pop(0)
    while res and not res[-1]:
        res.pop()
    if out is None:
        os.makedirs(SONGS_DIR, exist_ok=True)
        base = os.path.join(SONGS_DIR, _clean_title(path))
        out, k = base + ".txt", 2
        while os.path.exists(out):
            out, k = f"{base} ({k}).txt", k + 1
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(res) + "\n")
    return {"file": out, "righe": len(res), "righe_accordi": n_chord, "scartate": n_junk,
            "accordi": names}


def convert_cli(args: list[str]) -> int:
    mode = "it" if "--italiani" in args else "en"
    out = None
    if "--uscita" in args:
        i = args.index("--uscita")
        out = args[i + 1]
        args = args[:i] + args[i + 2:]
    files = [a for a in args if not a.startswith("--")]
    if not files:
        print("Uso: converti-canzone FILE.pdf [altri file...] [--uscita nome.txt] [--italiani]")
        return 2
    for p in files:
        r = convert(p, out if len(files) == 1 else None, mode)
        conv = sorted({f"{a}->{b}" for a, b in r["accordi"].items() if a != b})
        print(f"{os.path.basename(p)}\n  salvato in: {r['file']}\n  {r['righe']} righe, "
              f"{r['righe_accordi']} di accordi, {r['scartate']} scartate (link/numeri di pagina)")
        if conv:
            print("  accordi convertiti:", ", ".join(conv))
    print("Nel canzoniere premi R per ricaricare l'elenco.")
    return 0


def check(path: str) -> int:
    s = Song(path)
    print(f"titolo: {s.title}\nartista: {s.artist}\ncapo: {s.capo}  tonalità: {s.key}")
    print("accordi:", " ".join(s.chords) or "-")
    for c in s.chords:
        res = fingering(c)
        print(f"  {c:<8} {'n.d.' if res is None else ' '.join('x' if f < 0 else str(f) for f in res[0])}"
              f"{'  (semplificato)' if res and res[1] else ''}")
    print("--- impaginato a 46 colonne:")
    for kind, text, _ in layout(s, 0, 46):
        print(f"{kind:>8} | {text}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--check":
        sys.exit(check(sys.argv[2]))
    if len(sys.argv) >= 2 and sys.argv[1] == "--converti":
        sys.exit(convert_cli(sys.argv[2:]))
    sys.exit(main())

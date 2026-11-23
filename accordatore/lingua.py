#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Chords
"""
Lingua dell'interfaccia: italiano (predefinito) o inglese.

Si cambia con il tasto L nell'accordatore o nel canzoniere, oppure scrivendo
"lingua": "en" (o "it") in ~/.config/accordatore/impostazioni.json.
La variabile d'ambiente ACCORDATORE_LINGUA=en|it, se presente, ha la precedenza (utile nei test).

Nel codice i testi restano in italiano e passano da t():
    t("tendi la corda")                    -> "tighten the string" se la lingua è l'inglese
    t("{n} brani", n=12)                   -> "12 songs"
Un testo senza traduzione resta in italiano.
"""
from __future__ import annotations

import json
import os

LINGUE = ("it", "en")
CONF = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                    "accordatore", "impostazioni.json")

EN = {
    # ---- accordatore
    "Accordatore": "Tuner",
    "Mezzo tono sotto": "Half step down",
    "MANUALE": "MANUAL",
    " · muto": " · muted",
    "Microfono non disponibile": "Microphone not available",
    "Da SSH prova:  arecord -l": "Over SSH try:  arecord -l",
    "e:  python3 tuner.py --list-devices": "and:  python3 tuner.py --list-devices",
    "Esc esci": "Esc quit",
    "{v} semitoni": "{v} semitones",
    "In ascolto…": "Listening…",
    "suona una corda": "play a string",
    "{n}ª corda · {nota} {hz} Hz": "string {n} · {nota} {hz} Hz",
    "AUTO: trova la corda da sé": "AUTO: finds the string itself",
    " · La={v}": " · A={v}",
    "pizzica la corda vicino al microfono": "pluck the string near the microphone",
    "ACCORDATA": "IN TUNE",
    "tendi la corda": "tighten the string",
    "allenta la corda": "loosen the string",
    "corda incerta: sceglila con i tasti 1-6": "unsure string: pick it with keys 1-6",
    "troppo lontana: è la corda giusta?": "too far off: right string?",
    "1-6 corda  ←→  A auto  T accord.  M suono  Esc canzone":
        "1-6 string  ←→  A auto  T tuning  M sound  Esc song",
    "1-6 corda  ←→  A auto  T accord.  M suono  S canzoni  Esc":
        "1-6 string  ←→  A auto  T tuning  M sound  S songs  Esc",
    "Suono disattivato: niente ding": "Sound off: no chime",
    "Suono attivo: ding di prova": "Sound on: test chime",
    "Avvio microfono…": "Starting microphone…",
    "nessun dato": "no data",
    "nessun dispositivo di cattura": "no capture device",
    "arecord non trovato (sudo apt install alsa-utils)": "arecord not found (sudo apt install alsa-utils)",
    "arecord si è fermato": "arecord stopped",
    # ---- cambio lingua
    "Lingua: italiano": "Lingua: italiano",
    "Language: English": "Language: English",
    "L = English": "L = italiano",
    # ---- canzoniere
    "Canzoniere": "Songbook",
    "{n} brani": "{n} songs",
    "nomi it": "it names",
    "nomi orig": "orig names",
    "Ritornello": "Chorus",
    "n.d.": "n/a",
    "↑↓␣ scorri  S/G trasponi  A auto  C accordi  T accorda  Esc":
        "↑↓␣ scroll  S/G transpose  A auto  C chords  T tuner  Esc",
    "Accordi": "Chords",
    "Nessun accordo in questa canzone.": "No chords in this song.",
    "←→ pagina   ≈ = versione semplificata   C/Esc torna al testo":
        "←→ page   ≈ = simplified   C/Esc back to lyrics",
    "Nessuna canzone in {dir}. Copiaci file .cho (ChordPro) o .txt con gli accordi sopra il testo, poi premi R.":
        "No songs in {dir}. Copy .cho (ChordPro) files or .txt files with chords above the lyrics there, "
        "then press R.",
    "↑↓ scegli   ⏎ apri   R ricarica   L English   Esc accordatore":
        "↑↓ select   ⏎ open   R reload   L italiano   Esc tuner",
    "Creata {dir} con alcuni esempi": "Created {dir} with some examples",
    "{n} file non letti: vedi errori.log": "{n} files not read: see errori.log",
    "{n} brani in {dir}": "{n} songs in {dir}",
    ", {n} non letti (errori.log)": ", {n} not read (errori.log)",
    "Errore in \"{titolo}\": vedi errori.log": "Error in \"{titolo}\": see errori.log",
    # ---- schermata d'errore (chitarra.py)
    "L'accordatore": "The tuner",
    "Il canzoniere": "The songbook",
    "Errore": "Error",
    "segnale {n}": "signal {n}",
    "Chiuso dal sistema ({nome}).": "Closed by the system ({nome}).",
    "Possibile memoria esaurita.": "Possibly out of memory.",
    "Uscito con codice {rc}.": "Exited with code {rc}.",
    "{file}, riga {riga} ({funz})": "{file}, line {riga} ({funz})",
    "{chi} si è chiuso per un errore.": "{chi} closed because of an error.",
    "Dettagli in ~/.cache/accordatore/errori.log": "Details in ~/.cache/accordatore/errori.log",
    "un tasto qualsiasi: continua": "any key: continue",
}

_lang: str | None = None


def _read_conf() -> dict:
    try:
        with open(CONF, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def reload() -> str:
    """Rilegge la scelta (serve a un processo che resta aperto mentre un altro la cambia)."""
    global _lang
    env = os.environ.get("ACCORDATORE_LINGUA", "").strip().lower()
    if env in LINGUE:
        _lang = env
    else:
        v = _read_conf().get("lingua")
        _lang = v if v in LINGUE else "it"
    return _lang


def current() -> str:
    return _lang if _lang is not None else reload()


def save(code: str) -> str:
    """Imposta la lingua e la salva in impostazioni.json, lasciando com'è il resto del file."""
    global _lang
    _lang = code if code in LINGUE else "it"
    data = _read_conf()
    data["lingua"] = _lang
    try:
        os.makedirs(os.path.dirname(CONF), exist_ok=True)
        tmp = CONF + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, ensure_ascii=False)
        os.replace(tmp, CONF)
    except OSError:
        pass
    return _lang


def toggle() -> str:
    """Italiano <-> inglese. Ritorna il messaggio da mostrare, già nella nuova lingua."""
    save("en" if current() == "it" else "it")
    return "Language: English" if current() == "en" else "Lingua: italiano"


def t(s: str, **kw) -> str:
    if current() == "en":
        s = EN.get(s, s)
    return s.format(**kw) if kw else s

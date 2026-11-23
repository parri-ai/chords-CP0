#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Chords
"""
Accordatore + Canzoniere per M5Stack CardputerZero: è il programma che parte dall'icona.

Apre l'accordatore. Dall'accordatore, S apre il canzoniere; dal canzoniere,
Esc riporta all'accordatore; Esc dall'accordatore chiude l'app.
Ogni schermata è un programma a sé (tuner.py, songbook.py): questo file li alterna
senza annidarli, così la memoria resta quella di un programma solo.

Se una delle due parti si chiude per un errore, invece di sparire in silenzio:
- l'errore completo viene aggiunto a ~/.cache/accordatore/errori.log
- sullo schermo compare un riepilogo (che errore e in che punto del codice)
- dal canzoniere si torna all'accordatore; dall'accordatore si esce.
"""
import os
import re
import signal
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
EXIT_SONGBOOK = 10  # come in tuner.py
CACHE = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "accordatore")
LOG = os.path.join(CACHE, "errori.log")
# diario di sessione: avvii, uscite, tasto Esc, segnali. Lo scrivono anche tuner.py e songbook.py (via cz.diario)
os.environ.setdefault("CZ_DIARIO", os.path.join(CACHE, "sessione.log"))
sys.path.insert(0, HERE)
import cz  # noqa: E402
NAMES = {"tuner.py": "L'accordatore", "songbook.py": "Il canzoniere"}


def log_error(script: str, rc: int, err: str, secs: float):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        if os.path.exists(LOG) and os.path.getsize(LOG) > 200_000:  # tiene il file piccolo
            with open(LOG, "rb") as f:
                f.seek(-100_000, os.SEEK_END)
                tail = f.read()
            with open(LOG, "wb") as f:
                f.write(tail)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')}  {script}  "
                    f"codice {rc}  dopo {secs:.1f} s\n")
            f.write(err.rstrip() + "\n" if err.strip() else "(nessun messaggio di errore)\n")
    except OSError:
        pass


def summary(rc: int, err: str) -> list[str]:
    """Due o tre righe leggibili sullo schermo: che errore e dove."""
    lines = [ln.rstrip() for ln in err.splitlines() if ln.strip()]
    if rc < 0:
        try:
            name = signal.Signals(-rc).name
        except ValueError:
            name = f"segnale {-rc}"
        out = [f"Chiuso dal sistema ({name})."]
        if -rc == signal.SIGKILL:
            out.append("Possibile memoria esaurita.")
    else:
        last = next((ln.strip() for ln in reversed(lines)
                     if not ln.startswith(" ") and not ln.startswith("Traceback")), "")
        out = [last or f"Uscito con codice {rc}."]
    where = [m for m in (re.search(r'File "(.+?)", line (\d+), in (\S+)', ln) for ln in lines) if m]
    if where:
        m = where[-1]
        out.append(f"{os.path.basename(m.group(1))}, riga {m.group(2)} ({m.group(3)})")
    return out


def show_error(script: str, rc: int, err: str):
    """Schermata d'errore; se non si riesce a disegnarla, resta comunque il file di log."""
    try:
        with cz.App("Errore") as app:
            for _ in range(8):  # ~0,4 s: scarta i tasti premuti prima del crash
                app.wait(0.05)
            img, d = app.canvas()
            cz.header(d, "Errore", right=script)
            f, fb = cz.font(11), cz.font(11, bold=True)
            y = cz.BODY_TOP + 4
            paras = [(f"{NAMES.get(script, script)} si è chiuso per un errore.", fb, cz.WARN)]
            paras += [(s, f, cz.FG) for s in summary(rc, err)]
            paras += [("Dettagli in ~/.cache/accordatore/errori.log", f, cz.DIM)]
            for text, ft, col in paras:
                for line in cz.wrap(d, text, ft, cz.W - 14):
                    if y > cz.H - cz.FOOTER_H - 14:
                        break
                    d.text((7, y), line, font=ft, fill=col)
                    y += 14
                y += 3
            cz.footer(d, "un tasto qualsiasi: continua")
            app.show(img)
            end = time.monotonic() + 60
            while time.monotonic() < end and app.wait(0.5) is None:
                pass
    except (Exception, SystemExit):
        pass


def run(script: str) -> int:
    t0 = time.monotonic()
    cz.diario(f"avvio {script}")
    with tempfile.TemporaryFile() as err:
        try:
            # -X faulthandler: anche un crash "duro" dell'interprete lascia la traccia nel log
            rc = subprocess.run([sys.executable, "-X", "faulthandler", os.path.join(HERE, script)],
                                stderr=err).returncode
        except KeyboardInterrupt:
            return 0
        err.seek(0)
        text = err.read().decode("utf-8", errors="replace")
    if text and sys.stderr.isatty():
        sys.stderr.write(text)
    secs = time.monotonic() - t0
    if rc < 0:
        how = f"chiuso dal segnale {signal.Signals(-rc).name if -rc in signal.Signals._value2member_map_ else -rc}"
    else:
        how = {0: "uscito normalmente", EXIT_SONGBOOK: "chiede il canzoniere"}.get(rc, f"uscito con errore (codice {rc})")
    cz.diario(f"{script} {how} dopo {secs:.1f} s")
    if rc not in (0, EXIT_SONGBOOK):
        log_error(script, rc, text, time.monotonic() - t0)
        show_error(script, rc, text)
    return rc


def on_signal(signum, _frame):
    # SIGTERM arriva di solito dal launcher (APPLaunch) quando Esc resta premuto ~3 s:
    # manda il segnale a tutto il gruppo dell'app, quindi anche al programma in corso.
    cz.diario(f"ricevuto {signal.Signals(signum).name}: l'app viene chiusa dall'esterno")
    sys.exit(128 + signum)


def main() -> int:
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, on_signal)
    try:
        user = os.getlogin()
    except OSError:
        user = "?"
    cz.diario(f"=== app avviata (uid {os.getuid()}, utente {user}, HOME {os.environ.get('HOME', '?')})")
    while True:
        rc = run("tuner.py")
        if rc != EXIT_SONGBOOK:
            cz.diario("=== app chiusa")
            return 0 if rc == 0 else 1
        run("songbook.py")  # all'uscita dal canzoniere (anche per errore) si torna all'accordatore


if __name__ == "__main__":
    sys.exit(main())

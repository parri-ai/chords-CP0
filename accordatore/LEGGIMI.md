# Accordatore — manuale

App per M5Stack CardputerZero, con la sua icona nel launcher (APPLaunch). Contiene due parti:

- **Accordatore**: si apre subito. Ascolta la chitarra dal microfono e mostra nota, scarto in cent e se tendere o allentare la corda. Quando la corda è intonata suona un "ding".
- **Canzoniere**: si apre con `S`. Mostra i testi con gli accordi sopra. Puoi trasporre, far scorrere il testo da solo e vedere i diagrammi degli accordi. Con `T` apri l'accordatore; quando esci torni alla stessa canzone.

## Installazione

Serve un CardputerZero con il sistema e il launcher originali. Comandi da lanciare nel terminale del device o via SSH.

1. Scarica il repository sul device, in uno dei due modi:
   - con git (se manca: `sudo apt install git`):
     ```
     cd ~/Downloads
     git clone https://github.com/parri-ai/chords-CP0.git
     cd chords-CP0/accordatore
     ```
   - come zip (pulsante *Code → Download ZIP* su GitHub, poi copialo in `~/Downloads`):
     ```
     cd ~/Downloads
     unzip -o chords-CP0-main.zip
     cd chords-CP0-main/accordatore
     ```
2. Installa: `sudo sh installa.sh`. Lo script:
   - installa i pacchetti necessari con apt (`python3-numpy`, `python3-pil`, `alsa-utils`, `poppler-utils`, font DejaVu);
   - copia l'app in `/usr/share/APPLaunch/apps/accordatore`;
   - crea l'icona nel launcher e il comando `converti-canzone`;
   - aggiunge il tuo utente ai gruppi `audio`, `video` e `input`.
3. Se alla fine compare "IMPORTANTE: … riavvia", lancia `sudo reboot`.
4. Prova senza chitarra: `python3 /usr/share/APPLaunch/apps/accordatore/tuner.py --selftest`. Deve finire con `RISULTATO: OK`.

L'icona compare nel launcher entro pochi secondi; se non c'è, riavvia il device.

## Accordatore — tasti

| Tasto | Cosa fa |
|---|---|
| `1`…`6` | sceglie la corda per posizione, come sulla tastiera: 1 = casella a sinistra (Mi basso, 6ª corda), 6 = casella a destra (Mi cantino, 1ª corda). Passa in modalità **MANUALE** |
| `←` `→` (`fn`+`Z` / `fn`+`C`), oppure `Z` `C` | corda precedente / successiva |
| `A` | modalità **AUTO**: riconosce da solo la corda |
| `T` | accordatura: Standard, Drop D, mezzo tono sotto, Open G, DADGAD |
| `N` | nomi delle note: E A D… ↔ Mi La Re… |
| `M` | suono on/off. In basso compare la conferma; quando lo riattivi suona un ding di prova |
| `S` | apre il canzoniere |
| `Esc` | chiude l'app. Se l'accordatore era stato aperto da una canzone, torna alla canzone |

- In AUTO, se una corda è lontana più di un semitono, l'app scrive "corda incerta" e non indica il verso, perché non può sapere quale corda stai suonando. Scegli la corda con `1`-`6`.
- In MANUALE, oltre i 3 semitoni di scarto chiede "è la corda giusta?" invece di dirti di tendere, per non rischiare di spezzarla.
- La barretta a destra è il livello del microfono: il suono deve superare il trattino giallo.
- Il ding arriva quando la corda resta intonata per un secondo (vedi `tempo_accordata` più sotto).

## Canzoniere

Le canzoni stanno in `~/canzoni`. La prima volta la cartella viene creata con quattro esempi di pubblico dominio. Aggiungi i tuoi file, poi premi `R` nell'elenco per ricaricare. Le sottocartelle vanno bene.

Nel repository, la cartella `canzoni/` contiene altri 10 brani di pubblico dominio con accordi presi da fonti a licenza libera. Per installarli (senza `sudo`), dalla cartella `accordatore` del repository:
```
mkdir -p ~/canzoni && cp -r ../canzoni/folk-spiritual ../canzoni/natale ~/canzoni/
```
Le loro licenze sono spiegate in `canzoni/LEGGIMI.md`.

Formati accettati (codifica UTF-8; va bene anche quella di Windows):
- **`.txt` copiato da un sito di accordi**: lo incolli così com'è. Righe di accordi, intestazioni tipo `[Ritornello]` e intavolature (`e|---0---|`) vengono riconosciute da sole.
- **ChordPro** (`.cho`, `.chopro`, `.chordpro`, `.pro`, `.crd`): accordi tra quadre dentro il testo, `[G]Prima [C]riga`, più direttive come `{title: …}`, `{artist: …}`, `{capo: 2}`, `{key: G}`, `{start_of_chorus}`…`{end_of_chorus}`, `{comment: …}`, `{start_of_tab}`…`{end_of_tab}`. Le righe che iniziano con `#` non vengono mostrate: servono per note e fonti.

Gli accordi possono essere scritti in **anglosassone** (C, Am, F#7) o in **italiano** (Do, Lam, Fa#7, anche maiuscolo come SOLm o SIb, Do- per il minore, Do7+ per la settima maggiore). Con il tasto `N` scegli come mostrarli.

Per vedere come viene letto un file: `python3 /usr/share/APPLaunch/apps/accordatore/songbook.py --check ~/canzoni/nome.cho`

| Tasto | Cosa fa |
|---|---|
| `↑` `↓` | una riga |
| `←` `→` o `Spazio` | una pagina |
| `S` / `G` (oppure `+` / `-`) | trasponi **s**u / **g**iù di un semitono; `0` torna all'originale |
| `A` | scorrimento automatico on/off |
| `1`…`9` | velocità dello scorrimento (e lo avvia) |
| `N` | nomi degli accordi: anglosassoni (C, Am) → italiani (Do, Lam) → come scritti nel file |
| `C` | diagrammi degli accordi, già trasposti. `≈` indica una versione semplificata, per esempio Cm9 → Cm7 |
| `F` | dimensione del testo (3 misure). La più piccola serve per le intavolature larghe |
| `T` | accordatore. Con `Esc` torni alla canzone |
| `Esc` | dalla canzone all'elenco; dall'elenco all'accordatore |

La trasposizione viene ricordata canzone per canzone. Dimensione del testo, velocità e notazione valgono per tutte.

### Da PDF di accordi a .txt: `converti-canzone`

Se hai una canzone in PDF, per esempio stampata da un sito di accordi, mettila sul device e lancia:
```
converti-canzone ~/Downloads/nome-del-file.pdf
```
Cosa fa:
- estrae il testo e mette ogni accordo **sulla lettera su cui sta nel PDF**: usa le coordinate di ogni parola, non stima a colonne;
- converte gli accordi italiani in anglosassone: SOLm → Gm, SIb → Bb, Do7+ → Cmaj7…;
- toglie link, numeri di pagina e avvisi del sito in fondo, ma tiene autori e copyright;
- salva il `.txt` in `~/canzoni`. Il nome viene dal file PDF, ripulito; se esiste già aggiunge "(2)".

Alla fine stampa quante righe di accordi ha trovato e quali accordi ha convertito. Nel canzoniere premi `R` per vedere la canzone.

Opzioni: `--italiani` lascia gli accordi come sono nel PDF; `--uscita "Titolo.txt"` sceglie il nome del file. Funziona anche con un `.txt` già pronto, per convertire gli accordi italiani: `converti-canzone canzone.txt`.

Limiti:
- il PDF deve contenere testo vero, come quelli stampati da una pagina web. Una foto o una scansione non funziona;
- dentro una parola la posizione è calcolata in proporzione, quindi qualche accordo può risultare spostato di una lettera;
- per controllare il risultato: `python3 /usr/share/APPLaunch/apps/accordatore/songbook.py --check ~/canzoni/Titolo.txt`.

Le canzoni che converti restano sul tuo device. Testi e accordi presi dai siti sono di solito protetti da diritto d'autore: non ridistribuirli senza permesso.

## Impostazioni

File: `~/.config/accordatore/impostazioni.json` (viene creato dall'app). Le voci utili:

| Voce | Predefinito | Cosa fa |
|---|---|---|
| `a4` | `440.0` | frequenza del La di riferimento |
| `tolleranza_cent` | `3.0` | entro quanti cent la corda è "accordata" |
| `tempo_accordata` | `1.0` | per quanti secondi la corda deve restare intonata prima del ding (da 0,2 a 5) |
| `soglia_db` | `-50.0` | livello minimo del microfono; abbassalo (es. `-58`) se il suono non viene preso |
| `mic` | `""` | dispositivo ALSA del microfono; vuoto = scelta automatica |
| `uscita` | `""` | dispositivo ALSA per il ding; vuoto = scelta automatica |

## Se qualcosa non va

- **"Microfono non disponibile".** Lancia `arecord -l` e `python3 /usr/share/APPLaunch/apps/accordatore/tuner.py --list-devices`, poi prova `tuner.py --device plughw:CARD=<nome>,DEV=0`. Se funziona, scrivi `"mic": "plughw:CARD=<nome>,DEV=0"` nelle impostazioni.
- **Livello del microfono basso.** Avvicinati. Se non basta, usa `alsamixer` (F4 = cattura) oppure abbassa `soglia_db`.
- **Niente ding.** Controlla il volume (`fn`+`S` / `fn`+`D`) e che il suono non sia spento con `M`. Se in alto compare `ding ✗`, guarda `aplay -l` e imposta `uscita`.
- **Esc va premuto e lasciato subito.** Se resta premuto per circa 3 secondi è il launcher a chiudere l'app (prima SIGTERM, poi SIGKILL dopo altri 3 secondi). In quel caso non compare nessuna schermata d'errore.
- **L'app si chiude da sola.** Un errore non fa sparire l'app in silenzio: compare una schermata "Errore" con il tipo di errore e il punto del codice. Il testo completo finisce in `~/.cache/accordatore/errori.log`. Se l'errore è nel canzoniere si torna all'accordatore; un brano che dà problemi riporta all'elenco, e un file illeggibile viene saltato (avviso in basso nell'elenco).
- **Diario di sessione.** `~/.cache/accordatore/sessione.log` registra avvii, uscite (con il tasto usato), le pressioni di Esc con la durata e i segnali ricevuti: `tail -n 30 ~/.cache/accordatore/sessione.log`
- **Log del launcher.** `journalctl --user -u APPLaunch.service -b --no-pager | grep -E "ESC timeout|grace timeout|External app exited" | tail`. La riga `ESC timeout: SIGTERM` vuol dire che l'app è stata chiusa perché Esc è rimasto premuto.
- **Passaggio lento tra accordatore e canzoniere.** Ogni passaggio avvia un altro programma Python, quindi ci vuole qualche istante.

Se apri una segnalazione su GitHub, allega `errori.log` e le ultime righe di `sessione.log`.

## Togliere l'app

Dalla cartella `accordatore` del repository:
```
sudo sh disinstalla.sh
```
Canzoni (`~/canzoni`) e impostazioni (`~/.config/accordatore`) restano.

## Cosa è stato verificato

**Su un CardputerZero (un solo esemplare):** installazione, accordatura di una chitarra vera con il ding, canzoniere con elenco e brani, passaggio tra le due parti.

**Al computer, con prove automatiche:**
- accordatore: precisione su corde sintetiche rumorose (errore medio 0,16 cent, massimo 2); logica del ding; disegno della schermata con 3000 livelli di microfono diversi; catena `arecord`/`aplay`, compreso il ripiego quando la scheda audio non permette di registrare e suonare insieme;
- canzoniere: 204 diagrammi di accordi; trasposizione con scelta di diesis/bemolle; 23 casi di conversione dalla notazione italiana, più parole italiane che non vanno scambiate per accordi; impaginazione da 10 a 60 colonne; giro completo con tastiera simulata su 20 brani;
- `converti-canzone` su un PDF di prova con carattere proporzionale: 26 accordi su 28 sulla lettera esatta, 2 spostati di una.

**Non verificato:** altri esemplari o revisioni del CardputerZero, schede audio diverse da quella del device provato, e quali tasti fisici danno `+` e `-` (per questo ci sono anche `S` e `G`).

## File

| File | Cosa contiene |
|---|---|
| `chitarra.py` | avvio dell'app: lancia l'accordatore e mostra gli errori a schermo |
| `tuner.py` | accordatore (rilevamento dell'altezza con l'algoritmo YIN) |
| `songbook.py` | canzoniere e `converti-canzone` |
| `cz.py` | mini-framework per schermo (framebuffer 320×170), tastiera (evdev) e interfaccia |
| `installa.sh`, `disinstalla.sh` | installazione e rimozione |

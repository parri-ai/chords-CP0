# Canzoni extra — fonti e licenze

**Questa cartella NON è coperta dalla licenza MIT del resto del repository.** Ogni brano ha la sua licenza, scritta anche nelle prime righe del file (quelle che iniziano con `#`, che il canzoniere non mostra).

Tutti i testi e le melodie sono di pubblico dominio: brani tradizionali oppure con autori morti da più di 70 anni. Le licenze riguardano la trascrizione con gli accordi.

## Installazione

Dalla cartella `accordatore` del repository, senza `sudo`:
```
mkdir -p ~/canzoni && cp -r ../canzoni/folk-spiritual ../canzoni/natale ~/canzoni/
```
Poi, nell'elenco del canzoniere, premi `R`.

## CC BY-SA 4.0 — accordi da Wikibooks (de.wikibooks.org)

Licenza: [Creative Commons Attribuzione - Condividi allo stesso modo 4.0](https://creativecommons.org/licenses/by-sa/4.0/deed.it). L'elenco degli autori di ogni pagina è nella sua cronologia su Wikibooks.

| Brano | Fonte |
|---|---|
| Danny Boy | <https://de.wikibooks.org/wiki/Liederbuch/_Danny_Boy> |
| Oh My Darling Clementine | <https://de.wikibooks.org/wiki/Liederbuch/_Oh_My_Darling_Clementine> |
| When the Saints Go Marching In | <https://de.wikibooks.org/wiki/Gitarre:_Oh_When_The_Saints_durch_alle_Tonarten> (sezione "in G") |
| Swing Low, Sweet Chariot | <https://de.wikibooks.org/wiki/Liederbuch/_Swing_low,_sweet_chariot> |
| Drunken Sailor | <https://de.wikibooks.org/wiki/Liederbuch/_What_shall_we_do_with_the_drunken_sailor> |
| Auld Lang Syne | accordi: <https://de.wikibooks.org/wiki/Liederbuch/_Auld_Lang_Syne>; testo: [Wikisource, ed. 1887](https://en.wikisource.org/wiki/The_Poetical_Works_of_Robert_Burns/Auld_Lang_Syne) |

Modifiche rispetto alle fonti: conversione in formato ChordPro, righe divise per lo schermo da 320×170, note e sottotitoli in inglese, notazione tedesca H7 resa come B7, correzione di un refuso ("Haeve" → "Heave"), tolti gli accordi blues opzionali nell'ultima strofa di Swing Low. Dove serviva, sono state integrate strofe di pubblico dominio (Danny Boy: testo del 1913). I dettagli sono nelle righe `#` di ogni file. Queste versioni modificate sono distribuite con la stessa licenza CC BY-SA 4.0.

## MIT — accordi da cardinote/christmas-chords-lyrics

Fonte: <https://github.com/cardinote/christmas-chords-lyrics>, licenza MIT, Copyright (c) 2020 Cardinote Inc. Il testo completo della licenza è in [`LICENZA-cardinote-MIT.txt`](LICENZA-cardinote-MIT.txt).

| Brano | File di origine |
|---|---|
| Jingle Bells | `src/Jingle-Bells.txt` |
| Silent Night | `src/Silent-Night.txt` (testo inglese di J. F. Young, 1859) |
| We Wish You a Merry Christmas | `src/We-Wish-You-a-Merry-Christmas.txt` |
| Deck the Halls | `src/Deck-the-Halls.txt` |

Modifiche: conversione in formato ChordPro, righe divise per lo schermo.

## I quattro esempi dentro l'app

Amazing Grace, Greensleeves, Quel mazzolin di fiori e Arpeggio exercise vengono creati dall'app in `~/canzoni` al primo avvio. Sono arrangiamenti semplici scritti per questo progetto su testi di pubblico dominio, e seguono la licenza MIT del repository.

## Segnalazioni

Se trovi un errore in un accordo o nella licenza di un brano, apri una segnalazione su GitHub.

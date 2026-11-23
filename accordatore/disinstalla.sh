#!/bin/sh
# Toglie l'app Accordatore dal launcher. Le impostazioni in ~/.config/accordatore restano.
set -e
[ "$(id -u)" -eq 0 ] || { echo "Uso: sudo sh disinstalla.sh"; exit 1; }
PREFIX=/usr/share/APPLaunch
rm -rf "$PREFIX/apps/accordatore"
rm -f "$PREFIX/bin/accordatore" "$PREFIX/applications/accordatore.desktop" "$PREFIX/share/images/accordatore.png"
rm -f /usr/local/bin/converti-canzone
# il launcher ricontrolla da solo la cartella delle app ogni pochi secondi
echo "Accordatore rimosso."

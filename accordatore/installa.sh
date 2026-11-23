#!/bin/sh
# Installa l'app "Accordatore" (accordatore + canzoniere) con la sua icona nel launcher.
#
# Uso, dentro questa cartella:    sudo sh installa.sh
#
# Copia l'app in /usr/share/APPLaunch/apps/accordatore, crea l'icona nel launcher,
# installa le dipendenze (apt) e il comando converti-canzone.
set -e
[ "$(id -u)" -eq 0 ] || { echo "Uso: sudo sh installa.sh"; exit 1; }
SRC="$(cd "$(dirname "$0")" && pwd)"
PREFIX=/usr/share/APPLaunch
DEST="$PREFIX/apps/accordatore"
UTENTE="${SUDO_USER:-pi}"
FILES="chitarra.py tuner.py songbook.py cz.py lingua.py"

for f in $FILES accordatore.png; do
    [ -f "$SRC/$f" ] || { echo "ERRORE: manca $f accanto a installa.sh"; exit 1; }
done

echo "== dipendenze"
apt-get install -y python3-numpy python3-pil fonts-dejavu-core alsa-utils poppler-utils \
    || echo "ATTENZIONE: apt non riuscito (rete?). Se mancano numpy o alsa-utils l'accordatore non parte."

echo "== file"
mkdir -p "$DEST" "$PREFIX/bin" "$PREFIX/applications" "$PREFIX/share/images"
for f in $FILES; do
    install -m 0644 "$SRC/$f" "$DEST/$f"
done
install -m 0644 "$SRC/accordatore.png" "$PREFIX/share/images/accordatore.png"
cat > "$PREFIX/bin/accordatore" <<EOF
#!/bin/sh
exec python3 $DEST/chitarra.py "\$@"
EOF
chmod 0755 "$PREFIX/bin/accordatore"
cat > "$PREFIX/applications/accordatore.desktop" <<EOF
[Desktop Entry]
Name=Accordatore
Exec=$PREFIX/bin/accordatore
Terminal=false
Sysplause=true
Icon=share/images/accordatore.png
Type=Application
EOF
echo "   + icona 'Accordatore' nel launcher"
cat > /usr/local/bin/converti-canzone <<EOF
#!/bin/sh
# PDF o .txt di accordi -> .txt in ~/canzoni, accordi in anglosassone (--italiani per lasciarli)
exec python3 $DEST/songbook.py --converti "\$@"
EOF
chmod 0755 /usr/local/bin/converti-canzone
echo "   + comando converti-canzone"

echo "== permessi: $UTENTE nei gruppi audio (microfono), video (schermo), input (tastiera)"
NUOVO_AUDIO=0
if getent group audio >/dev/null && ! id -nG "$UTENTE" 2>/dev/null | tr ' ' '\n' | grep -qx audio; then
    NUOVO_AUDIO=1
fi
for g in audio video input; do
    getent group "$g" >/dev/null && usermod -aG "$g" "$UTENTE" || true
done

echo "(il launcher mostra la nuova icona entro pochi secondi; se non compare, riavvia il device)"
echo
echo "Fatto: nel launcher c'è l'icona 'Accordatore' (S = canzoniere, canzoni in ~/canzoni)."
echo "Prova veloce, senza chitarra:  python3 $DEST/tuner.py --selftest"
if [ $NUOVO_AUDIO = 1 ]; then
    echo "IMPORTANTE: $UTENTE è appena entrato nel gruppo audio, riavvia una volta: sudo reboot"
fi

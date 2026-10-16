#!/usr/bin/env bash
# =============================================================================
#  Suricate-tracker - installation du client sur Linux (par utilisateur, sans root)
#
#  Après l'installation, la commande « suricate-tracker » fonctionne depuis N'IMPORTE
#  quel dossier : plus besoin d'être dans le dossier du projet.
#
#  Le plus simple : le tableau de bord (bouton « 🐧 Linux » du client) affiche une commande
#  toute prête, à coller dans un terminal :
#      curl -fsSL https://mon-serveur.com/linux/install.sh | bash -s -- --server https://mon-serveur.com --code XXXX-XXXX-XXXX --autostart
#  (le script télécharge alors client.py depuis votre serveur).
#
#  Installer depuis un dossier local :
#      ./install-linux.sh --server https://mon-serveur.com --code XXXX-XXXX-XXXX --autostart
#      ./install-linux.sh --server https://mon-serveur.com --token LE_JETON
#      ./install-linux.sh                       # installe seulement, configurez ensuite
#
#  Options :
#      --server URL     adresse du serveur
#      --code CODE      code d'activation (bouton « Windows »/installation du tableau de bord)
#      --token JETON    jeton du client (alternative au code)
#      --autostart      démarre le client automatiquement (systemd utilisateur, sinon autostart graphique)
#      --no-open        ne pas ouvrir le navigateur (position via routeurs voisins puis IP)
#      --uninstall      désinstalle tout (programme, service, configuration) ; aussi : suricate-tracker --remove-all
#      -h, --help       cette aide
#
#  Emplacements :
#      programme       ~/.local/share/suricate-tracker/
#      commande        ~/.local/bin/suricate-tracker
#      configuration   ~/.config/suricate-tracker/config.json   (droits 600)
#      service         ~/.config/systemd/user/suricate-tracker.service
# =============================================================================
set -euo pipefail

APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/suricate-tracker"
BIN_DIR="$HOME/.local/bin"
LAUNCHER="$BIN_DIR/suricate-tracker"
CONF_DIR="${XDG_CONFIG_HOME:-$HOME/.config}"
UNIT_DIR="$CONF_DIR/systemd/user"
UNIT_FILE="$UNIT_DIR/suricate-tracker.service"
DESKTOP_FILE="$CONF_DIR/autostart/suricate-tracker.desktop"
SELF="${BASH_SOURCE[0]:-}"          # vide quand le script arrive par « curl | bash »
SRC_DIR=""
[ -n "$SELF" ] && [ -f "$SELF" ] && SRC_DIR="$(cd "$(dirname "$SELF")" && pwd)"

SERVER="" CODE="" TOKEN="" AUTOSTART=0 NO_OPEN=0 UNINSTALL=0

say()  { printf '%s\n' "$*"; }
fail() { printf '❌ %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

usage() { if [ -f "$SELF" ]; then sed -n '2,32p' "$SELF" | sed 's/^# \{0,1\}//'; else say "Voir l'en-tête de install-linux.sh (options : --server --code --token --autostart --no-open --uninstall)."; fi; }

# fetch URL DESTINATION : télécharge avec curl, wget ou python3.
fetch() {
  if have curl; then curl -fsSL "$1" -o "$2"
  elif have wget; then wget -q "$1" -O "$2"
  else python3 -c 'import sys,urllib.request; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])' "$1" "$2"
  fi
}

while [ $# -gt 0 ]; do
  case "$1" in
    --server)    [ $# -ge 2 ] || fail "--server attend une valeur"; SERVER="$2"; shift 2 ;;
    --code)      [ $# -ge 2 ] || fail "--code attend une valeur";   CODE="$2";   shift 2 ;;
    --token)     [ $# -ge 2 ] || fail "--token attend une valeur";  TOKEN="$2";  shift 2 ;;
    --autostart) AUTOSTART=1; shift ;;
    --no-open)   NO_OPEN=1;   shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help)   usage; exit 0 ;;
    *) fail "Option inconnue : $1 (voir --help)" ;;
  esac
done

[ "$(id -u)" -ne 0 ] || fail "Lancez ce script avec votre utilisateur normal (pas root, pas sudo) : l'installation est par utilisateur."

# ---------------------------------------------------------------- désinstallation
if [ "$UNINSTALL" -eq 1 ]; then
  say "🗑  Désinstallation de Suricate-tracker..."
  if have systemctl; then
    systemctl --user disable --now suricate-tracker 2>/dev/null || true
  fi
  rm -f "$UNIT_FILE" "$DESKTOP_FILE"
  have systemctl && systemctl --user daemon-reload 2>/dev/null || true
  if [ -x "$LAUNCHER" ]; then "$LAUNCHER" --uninstall --silent 2>/dev/null || true; fi
  pkill -u "$(id -u)" -f "$APP_DIR/client.py" 2>/dev/null || true
  rm -f "$LAUNCHER"
  rm -rf "$APP_DIR" "$CONF_DIR/suricate-tracker"
  say "✅ Désinstallé (programme, service, configuration)."
  say "   Pensez à supprimer le client dans le tableau de bord (🗑) si vous ne l'utilisez plus."
  exit 0
fi

# ---------------------------------------------------------------- vérifications
have python3 || fail "python3 est introuvable. Installez-le (ex. : sudo apt install python3 python3-venv)."
python3 - <<'PY' || fail "Python 3.8 ou plus récent est requis."
import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)
PY
if [ -n "$CODE" ] && [ -n "$TOKEN" ]; then fail "Donnez --code OU --token, pas les deux."; fi
if { [ -n "$CODE" ] || [ -n "$TOKEN" ]; } && [ -z "$SERVER" ]; then fail "--server est requis avec --code / --token."; fi

# ---------------------------------------------------------------- copie du programme
say "📦 Installation dans $APP_DIR"
mkdir -p "$APP_DIR" "$BIN_DIR"
SERVER="${SERVER%/}"
if [ -n "$SRC_DIR" ] && [ -f "$SRC_DIR/client.py" ]; then
  install -m 755 "$SRC_DIR/client.py" "$APP_DIR/client.py"
  if [ -d "$SRC_DIR/assets" ]; then
    rm -rf "$APP_DIR/assets"
    cp -r "$SRC_DIR/assets" "$APP_DIR/assets"
  fi
  [ -f "$SRC_DIR/install-linux.sh" ] && [ "$SRC_DIR/install-linux.sh" != "$APP_DIR/install-linux.sh" ] \
    && install -m 755 "$SRC_DIR/install-linux.sh" "$APP_DIR/install-linux.sh"
else
  [ -n "$SERVER" ] || fail "client.py est introuvable à côté du script : ajoutez --server https://mon-serveur.com pour le télécharger."
  say "⬇  Téléchargement du client depuis $SERVER"
  fetch "$SERVER/linux/client.py" "$APP_DIR/client.py.new" \
    || { rm -f "$APP_DIR/client.py.new"; fail "Téléchargement impossible depuis $SERVER (adresse correcte ? serveur à jour ?)."; }
  head -c 64 "$APP_DIR/client.py.new" | grep -q python || { rm -f "$APP_DIR/client.py.new"; fail "Le serveur n'a pas renvoyé client.py."; }
  chmod 755 "$APP_DIR/client.py.new"; mv -f "$APP_DIR/client.py.new" "$APP_DIR/client.py"
  mkdir -p "$APP_DIR/assets"
  fetch "$SERVER/linux/assets/background.mp4" "$APP_DIR/assets/background.mp4" 2>/dev/null || rm -f "$APP_DIR/assets/background.mp4"   # facultatif
  if fetch "$SERVER/linux/install.sh" "$APP_DIR/install-linux.sh.new" 2>/dev/null; then
    chmod 755 "$APP_DIR/install-linux.sh.new"; mv -f "$APP_DIR/install-linux.sh.new" "$APP_DIR/install-linux.sh"
  else rm -f "$APP_DIR/install-linux.sh.new"; fi
fi

# ---------------------------------------------------------------- Python + requests
PY="$APP_DIR/venv/bin/python"
if [ ! -x "$PY" ]; then
  # --system-site-packages : réutilise le paquet « requests » du système s'il existe déjà.
  if python3 -m venv --system-site-packages "$APP_DIR/venv" 2>/dev/null; then
    :
  else
    rm -rf "$APP_DIR/venv"
    PY=""
  fi
fi

if [ -n "$PY" ] && [ -x "$PY" ]; then
  if ! "$PY" -c "import requests" 2>/dev/null; then
    say "⬇  Installation de la dépendance « requests »..."
    "$PY" -m pip install --quiet --disable-pip-version-check requests \
      || fail "Impossible d'installer « requests » (réseau ?)."
  fi
else
  # Pas de venv disponible (ex. python3-venv absent) : on utilise le Python du système.
  PY="$(command -v python3)"
  if ! "$PY" -c "import requests" 2>/dev/null; then
    say "⬇  Installation de « requests » pour l'utilisateur..."
    python3 -m pip install --user --quiet --disable-pip-version-check requests 2>/dev/null \
      || python3 -m pip install --user --quiet --disable-pip-version-check --break-system-packages requests 2>/dev/null \
      || fail "Impossible d'installer « requests ». Essayez : sudo apt install python3-requests  (ou python3-venv, puis relancez)."
  fi
fi

# ---------------------------------------------------------------- commande « suricate-tracker »
cat > "$LAUNCHER" <<EOF
#!/bin/sh
# Généré par install-linux.sh : lance le client Suricate-tracker depuis n'importe quel dossier.
if [ "\${1:-}" = "--remove-all" ] && [ -f "$APP_DIR/install-linux.sh" ]; then exec bash "$APP_DIR/install-linux.sh" --uninstall; fi
exec "$PY" "$APP_DIR/client.py" "\$@"
EOF
chmod 755 "$LAUNCHER"

# ~/.local/bin dans le PATH ?
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *)
    LINE='export PATH="$HOME/.local/bin:$PATH"   # Suricate-tracker'
    for rc in "$HOME/.profile" "$HOME/.bashrc" "$HOME/.zshrc"; do
      if [ -f "$rc" ] && ! grep -qF '# Suricate-tracker' "$rc"; then printf '\n%s\n' "$LINE" >> "$rc"; fi
    done
    [ -f "$HOME/.profile" ] || printf '%s\n' "$LINE" > "$HOME/.profile"
    export PATH="$BIN_DIR:$PATH"
    say "ℹ️  $BIN_DIR ajouté au PATH (~/.profile, ~/.bashrc, ~/.zshrc). Ouvrez un nouveau terminal pour en profiter."
    ;;
esac

# ---------------------------------------------------------------- configuration
OPEN_FLAG=()
[ "$NO_OPEN" -eq 1 ] && OPEN_FLAG=(--no-open)

if [ -n "$CODE" ]; then
  say "🔑 Enregistrement avec le code d'activation..."
  "$LAUNCHER" --enroll "$CODE" --server "$SERVER" --silent "${OPEN_FLAG[@]}" \
    || fail "Enregistrement refusé (code invalide, expiré ou déjà utilisé ? serveur injoignable ?). Générez un nouveau code et relancez."
elif [ -n "$TOKEN" ]; then
  say "🔑 Enregistrement du jeton..."
  WT_SERVER="$SERVER" WT_TOKEN="$TOKEN" WT_NO_OPEN="$NO_OPEN" "$PY" -I -c '
import os, sys
sys.path.insert(0, sys.argv[1])
import client
client.save_config(client.normalize_server(os.environ["WT_SERVER"]), os.environ["WT_TOKEN"], os.environ["WT_NO_OPEN"] == "1")
' "$APP_DIR" || fail "Impossible d'enregistrer la configuration."
fi

# ---------------------------------------------------------------- démarrage automatique
if [ "$AUTOSTART" -eq 1 ]; then
  if [ ! -f "$CONF_DIR/suricate-tracker/config.json" ]; then
    say "⚠️  Pas encore de configuration : le démarrage automatique est ignoré. Relancez avec --server et --code (ou --token)."
  elif have systemctl && systemctl --user show-environment >/dev/null 2>&1; then
    mkdir -p "$UNIT_DIR"
    cat > "$UNIT_FILE" <<EOF
[Unit]
Description=Suricate-tracker client
After=network-online.target

[Service]
ExecStart="$LAUNCHER" --no-open
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable --now suricate-tracker
    say "✅ Service systemd utilisateur activé (démarre à l'ouverture de session)."
    say "   Pour qu'il démarre dès le boot, sans ouvrir de session :  sudo loginctl enable-linger \"$USER\""
    say "   Journal en direct :  journalctl --user -u suricate-tracker -f"
  else
    mkdir -p "$(dirname "$DESKTOP_FILE")"
    cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Suricate-tracker
Exec="$LAUNCHER"$([ "$NO_OPEN" -eq 1 ] && printf ' --no-open')
Terminal=false
EOF
    say "✅ Démarrage automatique ajouté à l'ouverture de session graphique ($DESKTOP_FILE)."
    say "   Il prendra effet à la prochaine ouverture de session."
  fi
fi

# ---------------------------------------------------------------- fin
say ""
say "✅ Installé. Depuis n'importe quel dossier :"
say "     suricate-tracker                lancer le client"
say "     suricate-tracker --setup        changer serveur / jeton"
say "     suricate-tracker --once --debug test rapide (un seul envoi)"
say "     suricate-tracker --remove-all   tout supprimer (programme, service, configuration)"
if [ ! -f "$CONF_DIR/suricate-tracker/config.json" ]; then
  say ""
  say "ℹ️  Il reste à configurer :  suricate-tracker --server https://mon-serveur.com --token LE_JETON --save"
fi

#!/usr/bin/env bash
# Déploiement de airmon sur un Raspberry Pi (Debian).
# À lancer SUR le Pi, depuis le dossier du dépôt cloné.
#   git clone <repo> ~/airmon-src && cd ~/airmon-src && ./install.sh
set -euo pipefail

APP_DIR=/opt/airmon
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
USER_NAME="${SUDO_USER:-$USER}"

echo ">> Paquets système (i2c-tools, python venv)"
sudo apt-get update -qq
sudo apt-get install -y i2c-tools python3-venv python3-full

echo ">> Accès I2C pour $USER_NAME"
sudo usermod -aG i2c "$USER_NAME" || true

echo ">> Installation dans $APP_DIR"
sudo mkdir -p "$APP_DIR"
sudo chown -R "$USER_NAME:$USER_NAME" "$APP_DIR"
cp -r "$SRC_DIR/collector.py" "$SRC_DIR/webapp.py" "$SRC_DIR/static" "$APP_DIR/"
mkdir -p "$APP_DIR/data"

echo ">> Environnement Python"
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet -r "$SRC_DIR/requirements.txt"

echo ">> Services systemd"
sudo cp "$SRC_DIR/systemd/airmon-collector.service" "$SRC_DIR/systemd/airmon-web.service" /etc/systemd/system/
sudo sed -i "s/^User=.*/User=$USER_NAME/" /etc/systemd/system/airmon-collector.service /etc/systemd/system/airmon-web.service
sudo systemctl daemon-reload
sudo systemctl enable --now airmon-collector.service airmon-web.service

echo ">> Terminé. Dashboard : http://$(hostname -I | awk '{print $1}'):8080"
echo "   (une reconnexion peut être nécessaire pour l'accès I2C si le groupe vient d'être ajouté)"

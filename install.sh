#!/usr/bin/env bash
# Déploiement de airmon sur un Raspberry Pi (Debian).
# À lancer SUR le Pi, depuis le dossier du dépôt cloné.
#   git clone <repo> ~/airmon-src && cd ~/airmon-src && ./install.sh
set -euo pipefail

APP_DIR=/opt/airmon
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
USER_NAME="${SUDO_USER:-$USER}"

echo ">> Paquets système (i2c-tools, python venv, écran e-ink : SPI/GPIO/PIL + polices)"
sudo apt-get update -qq
sudo apt-get install -y i2c-tools python3-venv python3-full fonts-dejavu-core \
    python3-pil python3-spidev python3-gpiozero python3-lgpio

echo ">> Accès I2C / SPI / GPIO pour $USER_NAME"
sudo usermod -aG i2c "$USER_NAME" || true
sudo usermod -aG spi "$USER_NAME" || true    # écran e-ink : accès /dev/spidev*
sudo usermod -aG gpio "$USER_NAME" || true   # écran e-ink : accès /dev/gpiochip* (gpiozero/lgpio)

echo ">> Activation SPI (écran e-ink Waveshare)"
sudo raspi-config nonint do_spi 0 || echo "   (raspi-config indisponible : activer SPI manuellement)"

echo ">> Installation dans $APP_DIR"
sudo mkdir -p "$APP_DIR"
sudo chown -R "$USER_NAME:$USER_NAME" "$APP_DIR"
cp -r "$SRC_DIR/collector.py" "$SRC_DIR/webapp.py" "$SRC_DIR/weather.py" \
      "$SRC_DIR/eink_display.py" "$SRC_DIR/static" "$SRC_DIR/eink" "$APP_DIR/"
mkdir -p "$APP_DIR/data"

echo ">> Environnement Python (--system-site-packages : accès aux libs apt SPI/GPIO/PIL)"
rm -rf "$APP_DIR/venv"
python3 -m venv --system-site-packages "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet -r "$SRC_DIR/requirements.txt"

echo ">> Services systemd"
sudo cp "$SRC_DIR/systemd/airmon-collector.service" "$SRC_DIR/systemd/airmon-web.service" \
        "$SRC_DIR/systemd/airmon-weather.service" "$SRC_DIR/systemd/airmon-eink.service" \
        /etc/systemd/system/
sudo sed -i "s/^User=.*/User=$USER_NAME/" \
    /etc/systemd/system/airmon-collector.service \
    /etc/systemd/system/airmon-web.service \
    /etc/systemd/system/airmon-weather.service \
    /etc/systemd/system/airmon-eink.service
sudo systemctl daemon-reload
sudo systemctl enable --now airmon-collector.service airmon-web.service \
    airmon-weather.service airmon-eink.service

echo ">> Terminé. Dashboard : http://$(hostname -I | awk '{print $1}'):8080"
echo "   (une reconnexion peut être nécessaire pour l'accès I2C si le groupe vient d'être ajouté)"
echo "   (si SPI vient d'être activé, un redémarrage est nécessaire pour l'écran e-ink : sudo reboot)"

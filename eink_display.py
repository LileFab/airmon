#!/usr/bin/env python3
"""Affichage des dernières mesures sur l'écran e-ink Waveshare 2.9" (296x128).

Dalle WFT0290CZ10 (noir/blanc, 296x128, contrôleur "busy = bas") sur l'Universal
e-Paper Driver HAT, reliée au Pi par le câble 9 broches (broches par défaut
Waveshare, disjointes de l'I2C du BME680). Pilote : epd2in9d.

Lit directement la dernière ligne de data/airmon.db (comme webapp.py::api_latest,
sans passer par le serveur web) et l'affiche : température, humidité, indice de
qualité d'air + horodatage.
"""

import logging
import os
import sqlite3
import sys
import time

from PIL import Image, ImageDraw, ImageFont

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "airmon.db")

# Pilote Waveshare vendorisé dans eink/waveshare_epd/ (aucune dépendance réseau).
sys.path.append(os.path.join(BASE_DIR, "eink"))
from waveshare_epd import epd2in9d  # noqa: E402

# Dimensions en paysage (la dalle est native 128x296 portrait, on tourne).
SCREEN_W = epd2in9d.EPD_HEIGHT  # 296
SCREEN_H = epd2in9d.EPD_WIDTH   # 128

INTERVAL = 120  # secondes entre deux rafraîchissements

# Polices DejaVu (paquet fonts-dejavu-core, installé par install.sh).
FONT_DIR = "/usr/share/fonts/truetype/dejavu"
FONT_REGULAR = os.path.join(FONT_DIR, "DejaVuSans.ttf")
FONT_BOLD = os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("eink")


def load_font(path, size):
    """Charge une police TrueType, avec repli sur la police PIL par défaut."""
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        logger.warning("police %s introuvable, repli sur la police par défaut", path)
        return ImageFont.load_default()


def read_latest():
    """Dernière mesure sous forme de dict, ou None si la base est vide/absente."""
    try:
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        try:
            row = con.execute(
                "SELECT ts, temperature, humidity, pressure, gas_resistance, air_quality "
                "FROM measurements ORDER BY ts DESC LIMIT 1"
            ).fetchone()
        finally:
            con.close()
    except sqlite3.Error as exc:
        logger.warning("lecture SQLite impossible : %s", exc)
        return None
    return dict(row) if row is not None else None


def air_quality_label(score):
    """Libellé qualitatif de l'indice 0-100 (plus haut = meilleur)."""
    if score is None:
        return ""
    if score >= 80:
        return "Excellent"
    if score >= 60:
        return "Bon"
    if score >= 40:
        return "Moyen"
    if score >= 20:
        return "Mediocre"
    return "Mauvais"


def fmt(value, unit, digits=1):
    return f"{value:.{digits}f}{unit}" if value is not None else "--"


def render(latest):
    """Construit l'image 1-bit paysage (296x128) à afficher.

    Disposition : barre d'en-tête inversée (blanc sur noir) avec l'heure,
    température "hero" à gauche, humidité + qualité d'air empilées à droite.
    """
    f_bar = load_font(FONT_BOLD, 15)
    f_tiny = load_font(FONT_REGULAR, 12)
    f_temp = load_font(FONT_BOLD, 44)
    f_unit = load_font(FONT_BOLD, 20)
    f_row = load_font(FONT_BOLD, 22)
    f_word = load_font(FONT_REGULAR, 14)

    img = Image.new("1", (SCREEN_W, SCREEN_H), 255)  # 255 = blanc
    draw = ImageDraw.Draw(img)

    # En-tête inversé : rectangle noir plein, texte blanc.
    # Date du jour à gauche, heure de la dernière mesure à droite.
    draw.rectangle((0, 0, SCREEN_W, 24), fill=0)
    draw.text((6, 4), time.strftime("%d/%m/%Y"), font=f_bar, fill=255)

    if latest is None:
        draw.text((8, 56), "En attente de mesures...", font=f_row, fill=0)
        return img

    stamp = time.strftime("%H:%M", time.localtime(latest["ts"]))
    draw.text((SCREEN_W - draw.textlength(stamp, font=f_bar) - 6, 4), stamp, font=f_bar, fill=255)

    divider = 176

    # Température "hero" à gauche.
    draw.text((8, 30), "TEMPERATURE", font=f_tiny, fill=0)
    tval = fmt(latest["temperature"], "", 1)
    draw.text((6, 46), tval, font=f_temp, fill=0)
    draw.text((6 + draw.textlength(tval, font=f_temp) + 4, 66), "C", font=f_unit, fill=0)

    draw.line((divider, 30, divider, 120), fill=0)

    # Colonne droite : humidité (haut) + qualité d'air (bas).
    draw.text((divider + 12, 30), "HUMIDITE", font=f_tiny, fill=0)
    draw.text((divider + 10, 44), fmt(latest["humidity"], " %", 0), font=f_row, fill=0)

    aq = latest["air_quality"]
    draw.text((divider + 12, 78), "QUALITE AIR", font=f_tiny, fill=0)
    if aq is None:
        draw.text((divider + 10, 94), "chauffe...", font=f_word, fill=0)
    else:
        draw.text((divider + 10, 92), f"{aq:.0f}", font=f_row, fill=0)
        aw = draw.textlength(f"{aq:.0f}", font=f_row)
        draw.text((divider + 10 + aw + 8, 98), air_quality_label(aq), font=f_word, fill=0)

    return img


def refresh(epd):
    """Un cycle : réveil de la dalle -> dessin -> affichage -> veille."""
    latest = read_latest()
    img = render(latest)
    if epd.init() != 0:
        raise RuntimeError("epd.init() a echoue")
    epd.display(epd.getbuffer(img))
    epd.sleep()  # remet le panneau en veille basse consommation entre deux MAJ
    logger.info("ecran rafraichi (%s)", "sans donnee" if latest is None else "ok")


def main():
    epd = epd2in9d.EPD()
    next_tick = time.monotonic()
    while True:
        try:
            refresh(epd)
        except Exception as exc:  # on ne veut jamais faire planter le service
            logger.error("echec du rafraichissement : %s", exc)
            try:
                epd.sleep()
            except Exception:
                pass

        next_tick += INTERVAL
        sleep_for = next_tick - time.monotonic()
        if sleep_for < 0:
            next_tick = time.monotonic()
            sleep_for = INTERVAL
        time.sleep(sleep_for)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        try:
            epd2in9d.epdconfig.module_exit(cleanup=True)
        except Exception:
            pass
        sys.exit(0)

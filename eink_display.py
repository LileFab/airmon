#!/usr/bin/env python3
"""Affichage e-ink Waveshare 2.9" (296x128) — plusieurs écrans cyclables.

Dalle WFT0290CZ10 (noir/blanc, 296x128, contrôleur "busy = bas") sur l'Universal
e-Paper Driver HAT, reliée au Pi par le câble 9 broches. Pilote : epd2in9d.

Un bouton poussoir (GPIO26 -> GND) fait défiler les écrans. Chaque écran est une
fonction de rendu enregistrée dans VIEWS — pour en ajouter un, écrire une
fonction `render_xxx() -> Image` et l'ajouter à VIEWS.

Écrans actuels :
- Air intérieur : dernière mesure du BME680 (SQLite), comme webapp.py::api_latest.
- Météo actuelle : conditions à Lyon via Open-Meteo (gratuit, sans clé API).
"""

import json
import logging
import os
import sqlite3
import sys
import threading
import time
import urllib.request
from math import cos, sin, radians

from PIL import Image, ImageDraw, ImageFont

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "airmon.db")

# Pilote Waveshare vendorisé dans eink/waveshare_epd/ (aucune dépendance réseau).
sys.path.append(os.path.join(BASE_DIR, "eink"))
from waveshare_epd import epd2in9d  # noqa: E402

# Dimensions en paysage (la dalle est native 128x296 portrait, on tourne).
SCREEN_W = epd2in9d.EPD_HEIGHT  # 296
SCREEN_H = epd2in9d.EPD_WIDTH   # 128

INTERVAL = 120       # s : rafraîchissement périodique de la vue courante
BUTTON_PIN = 26      # bouton poussoir vers GND (broches physiques 37 + 39)
WEATHER_TTL = 900    # s : intervalle entre deux appels météo (15 min)

# Localisation de l'écran météo (éditable).
LAT, LON, CITY = 45.748, 4.85, "Lyon"

# Polices DejaVu (paquet fonts-dejavu-core, installé par install.sh).
FONT_DIR = "/usr/share/fonts/truetype/dejavu"
FONT_REGULAR = os.path.join(FONT_DIR, "DejaVuSans.ttf")
FONT_BOLD = os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("eink")

_font_cache = {}
_font_warned = False


def font(size, bold=False):
    """Police TrueType mise en cache, repli sur la police PIL par défaut."""
    global _font_warned
    key = (size, bold)
    if key not in _font_cache:
        try:
            _font_cache[key] = ImageFont.truetype(FONT_BOLD if bold else FONT_REGULAR, size)
        except OSError:
            if not _font_warned:
                logger.warning("polices DejaVu introuvables, repli sur la police par défaut")
                _font_warned = True
            _font_cache[key] = ImageFont.load_default()
    return _font_cache[key]


def fmt(value, unit="", digits=1):
    return f"{value:.{digits}f}{unit}" if value is not None else "--"


def new_canvas():
    """Image 1-bit blanche (296x128) + son contexte de dessin."""
    img = Image.new("1", (SCREEN_W, SCREEN_H), 255)
    return img, ImageDraw.Draw(img)


def draw_header(draw, left, right=""):
    """Barre d'en-tête inversée (rectangle noir, texte blanc) commune aux vues."""
    f = font(15, bold=True)
    draw.rectangle((0, 0, SCREEN_W, 24), fill=0)
    draw.text((6, 4), left, font=f, fill=255)
    if right:
        draw.text((SCREEN_W - draw.textlength(right, font=f) - 6, 4), right, font=f, fill=255)


# ----------------------------------------------------------------------------
# Vue « Air intérieur » (BME680 via SQLite)
# ----------------------------------------------------------------------------
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


def render_air():
    """Écran mesures intérieures : température hero + humidité + qualité d'air."""
    latest = read_latest()
    img, draw = new_canvas()
    stamp = time.strftime("%H:%M", time.localtime(latest["ts"])) if latest else ""
    draw_header(draw, time.strftime("%d/%m/%Y"), stamp)

    if latest is None:
        draw.text((8, 56), "En attente de mesures...", font=font(22, True), fill=0)
        return img

    divider = 176
    draw.text((8, 30), "TEMPERATURE", font=font(12), fill=0)
    tval = fmt(latest["temperature"], "", 1)
    draw.text((6, 46), tval, font=font(44, True), fill=0)
    draw.text((6 + draw.textlength(tval, font=font(44, True)) + 4, 66), "C", font=font(20, True), fill=0)

    draw.line((divider, 30, divider, 120), fill=0)

    draw.text((divider + 12, 30), "HUMIDITE", font=font(12), fill=0)
    draw.text((divider + 10, 44), fmt(latest["humidity"], " %", 0), font=font(22, True), fill=0)

    aq = latest["air_quality"]
    draw.text((divider + 12, 78), "QUALITE AIR", font=font(12), fill=0)
    if aq is None:
        draw.text((divider + 10, 94), "chauffe...", font=font(14), fill=0)
    else:
        draw.text((divider + 10, 92), f"{aq:.0f}", font=font(22, True), fill=0)
        aw = draw.textlength(f"{aq:.0f}", font=font(22, True))
        draw.text((divider + 10 + aw + 8, 98), air_quality_label(aq), font=font(14), fill=0)

    return img


# ----------------------------------------------------------------------------
# Vue « Météo » (Open-Meteo)
# ----------------------------------------------------------------------------
# code météo WMO -> (libellé FR concis, catégorie d'icône)
WMO_CODES = {
    0: ("Ciel clair", "clear"),
    1: ("Peu nuageux", "partly"), 2: ("Nuageux", "partly"), 3: ("Couvert", "cloud"),
    45: ("Brouillard", "fog"), 48: ("Brouillard", "fog"),
    51: ("Bruine", "rain"), 53: ("Bruine", "rain"), 55: ("Bruine", "rain"),
    56: ("Bruine gel.", "rain"), 57: ("Bruine gel.", "rain"),
    61: ("Pluie", "rain"), 63: ("Pluie", "rain"), 65: ("Pluie forte", "rain"),
    66: ("Pluie gel.", "rain"), 67: ("Pluie gel.", "rain"),
    71: ("Neige", "snow"), 73: ("Neige", "snow"), 75: ("Neige forte", "snow"), 77: ("Neige", "snow"),
    80: ("Averses", "rain"), 81: ("Averses", "rain"), 82: ("Averses fortes", "rain"),
    85: ("Neige", "snow"), 86: ("Neige", "snow"),
    95: ("Orage", "storm"), 96: ("Orage grele", "storm"), 99: ("Orage grele", "storm"),
}

# Cache météo, alimenté par le thread weather_updater().
_weather = {"data": None}
_weather_lock = threading.Lock()


def fetch_weather():
    """Interroge Open-Meteo (timeout 5 s) et renvoie un dict de valeurs."""
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={LAT}&longitude={LON}"
        "&current=temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m"
        "&daily=temperature_2m_max,temperature_2m_min&timezone=auto&forecast_days=1"
    )
    with urllib.request.urlopen(url, timeout=5) as resp:
        payload = json.load(resp)
    cur = payload.get("current", {})
    daily = payload.get("daily", {})
    first = lambda k: (daily.get(k) or [None])[0]
    return {
        "temp": cur.get("temperature_2m"),
        "feels": cur.get("apparent_temperature"),
        "hum": cur.get("relative_humidity_2m"),
        "wind": cur.get("wind_speed_10m"),
        "code": cur.get("weather_code"),
        "tmax": first("temperature_2m_max"),
        "tmin": first("temperature_2m_min"),
        "fetched": time.time(),
    }


def weather_updater():
    """Thread démon : rafraîchit le cache météo toutes les WEATHER_TTL secondes."""
    while True:
        try:
            data = fetch_weather()
            with _weather_lock:
                _weather["data"] = data
            logger.info("meteo mise a jour (%s C, code %s)", data["temp"], data["code"])
        except Exception as exc:  # réseau/HTTP/JSON : on garde le cache précédent
            logger.warning("meteo indisponible : %s", exc)
        time.sleep(WEATHER_TTL)


def draw_weather_icon(draw, cx, cy, category):
    """Dessine une icône météo N&B (~48px) centrée sur (cx, cy)."""
    def sun(sx, sy, r):
        draw.ellipse((sx - r, sy - r, sx + r, sy + r), fill=0)
        for ang in range(0, 360, 45):
            dx, dy = cos(radians(ang)), sin(radians(ang))
            draw.line((sx + dx * (r + 3), sy + dy * (r + 3),
                       sx + dx * (r + 9), sy + dy * (r + 9)), fill=0, width=3)

    def cloud(x, y):
        # nuage plein d'environ 46 de large, 26 de haut, coin haut-gauche (x, y)
        draw.ellipse((x, y + 8, x + 22, y + 26), fill=0)
        draw.ellipse((x + 10, y, x + 34, y + 22), fill=0)
        draw.ellipse((x + 24, y + 8, x + 46, y + 26), fill=0)
        draw.rectangle((x + 8, y + 16, x + 40, y + 26), fill=0)

    if category == "clear":
        sun(cx, cy, 15)
    elif category == "partly":
        sun(cx - 10, cy - 10, 10)
        cloud(cx - 14, cy - 2)
    elif category == "cloud":
        cloud(cx - 23, cy - 13)
    elif category == "fog":
        cloud(cx - 23, cy - 18)
        for i in range(3):
            draw.line((cx - 20, cy + 14 + i * 6, cx + 20, cy + 14 + i * 6), fill=0, width=2)
    elif category == "rain":
        cloud(cx - 23, cy - 20)
        for i in range(3):
            x0 = cx - 16 + i * 16
            draw.line((x0, cy + 10, x0 - 5, cy + 22), fill=0, width=3)
    elif category == "snow":
        cloud(cx - 23, cy - 20)
        for i in range(3):
            x0 = cx - 16 + i * 16
            draw.ellipse((x0 - 2, cy + 12, x0 + 2, cy + 16), fill=0)
    elif category == "storm":
        cloud(cx - 23, cy - 20)
        draw.polygon([(cx - 2, cy + 8), (cx - 10, cy + 22), (cx - 2, cy + 22),
                      (cx - 8, cy + 34), (cx + 8, cy + 18), (cx, cy + 18)], fill=0)
    else:
        cloud(cx - 23, cy - 13)


def render_weather():
    """Écran météo : icône + température actuelle + détails du jour."""
    with _weather_lock:
        data = _weather["data"]

    img, draw = new_canvas()
    upd = time.strftime("%H:%M", time.localtime(data["fetched"])) if data else ""
    draw_header(draw, f"Meteo - {CITY}", upd)

    if data is None:
        draw.text((8, 56), "Meteo indisponible", font=font(22, True), fill=0)
        return img

    label, category = WMO_CODES.get(data["code"], ("---", "cloud"))
    divider = 170

    # Gauche : icône + température actuelle + libellé condition.
    draw_weather_icon(draw, 36, 58, category)
    tval = fmt(data["temp"], "", 0)
    draw.text((68, 34), tval, font=font(40, True), fill=0)
    draw.text((68 + draw.textlength(tval, font=font(40, True)) + 3, 50), "C", font=font(18, True), fill=0)
    draw.text((10, 96), label, font=font(15, True), fill=0)

    draw.line((divider, 30, divider, 120), fill=0)

    # Droite : ressenti, humidité, vent, min/max du jour.
    x = divider + 10
    rows = [
        f"Ressenti {fmt(data['feels'], '', 0)}",
        f"Humidite {fmt(data['hum'], ' %', 0)}",
        f"Vent {fmt(data['wind'], ' km/h', 0)}",
        f"Min {fmt(data['tmin'], '', 0)} Max {fmt(data['tmax'], '', 0)}",
    ]
    for i, text in enumerate(rows):
        draw.text((x, 32 + i * 23), text, font=font(14), fill=0)

    return img


# ----------------------------------------------------------------------------
# Registre des écrans : pour en ajouter un, écrire render_xxx() puis l'ajouter ici.
# ----------------------------------------------------------------------------
VIEWS = [
    ("Air", render_air),
    ("Meteo", render_weather),
]


def show(epd, image):
    """Envoie une image déjà rendue sur la dalle puis la remet en veille."""
    if epd.init() != 0:
        raise RuntimeError("epd.init() a echoue")
    epd.display(epd.getbuffer(image))
    epd.sleep()


def main():
    epd = epd2in9d.EPD()
    state = {"index": 0}
    wake = threading.Event()

    # Bouton : avance l'index et réveille la boucle (optionnel — dégradation propre).
    try:
        from gpiozero import Button
        button = Button(BUTTON_PIN, pull_up=True, bounce_time=0.1)

        def on_press():
            state["index"] = (state["index"] + 1) % len(VIEWS)
            wake.set()

        button.when_pressed = on_press
        logger.info("bouton actif sur GPIO%d (%d ecrans)", BUTTON_PIN, len(VIEWS))
    except Exception as exc:
        logger.warning("bouton indisponible (%s) — cyclage manuel desactive", exc)

    # Rafraîchisseur météo en tâche de fond.
    threading.Thread(target=weather_updater, daemon=True).start()

    while True:
        name, render_fn = VIEWS[state["index"]]
        try:
            show(epd, render_fn())
            logger.info("ecran '%s' affiche", name)
        except Exception as exc:  # on ne veut jamais faire planter le service
            logger.error("echec affichage '%s' : %s", name, exc)
            try:
                epd.sleep()
            except Exception:
                pass

        wake.wait(timeout=INTERVAL)  # réveil par le bouton ou après INTERVAL
        wake.clear()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        try:
            epd2in9d.epdconfig.module_exit(cleanup=True)
        except Exception:
            pass
        sys.exit(0)

#!/usr/bin/env python3
"""Service météo : température extérieure (Open-Meteo) -> SQLite.

Récupère la température extérieure horaire pour un lieu fixe (Lyon) via l'API
Open-Meteo (gratuite, sans clé), la stocke dans la table `outdoor_temperature`
et l'entretient. Au démarrage : backfill de l'historique depuis la première
mesure intérieure, pour aligner la courbe extérieure sur les données du BME680.

Aucune dépendance externe (urllib / sqlite3, stdlib). Service systemd airmon-weather.
"""

import json
import logging
import math
import os
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "airmon.db")

# Localisation de la météo (doit correspondre à eink_display.py ; factorisable plus tard).
LAT, LON, CITY = 45.748, 4.85, "Lyon"

UPDATE_INTERVAL = 1800     # s : rafraîchissement en régime permanent (30 min)
RECENT_PAST_DAYS = 2       # fenêtre de rattrapage récent
MAX_PAST_DAYS = 92         # cap de l'API forecast Open-Meteo
HTTP_TIMEOUT = 8           # s

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("weather")


def connect():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.execute("PRAGMA busy_timeout=5000")  # coexistence avec le collecteur (WAL)
    return con


def ensure_table(con):
    con.execute(
        "CREATE TABLE IF NOT EXISTS outdoor_temperature ("
        "  ts INTEGER PRIMARY KEY,"   # epoch secondes UTC, aligné à l'heure
        "  temperature REAL"
        ")"
    )
    con.commit()


def fetch_hourly(past_days):
    """Renvoie [(ts_utc, temp)] horaire sur les `past_days` derniers jours + aujourd'hui."""
    past_days = max(1, min(MAX_PAST_DAYS, int(past_days)))
    params = urllib.parse.urlencode({
        "latitude": LAT,
        "longitude": LON,
        "hourly": "temperature_2m",
        "past_days": past_days,
        "forecast_days": 1,
        "timezone": "UTC",
    })
    url = "https://api.open-meteo.com/v1/forecast?" + params
    with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT) as resp:
        payload = json.load(resp)
    hourly = payload.get("hourly", {})
    times = hourly.get("time", [])
    temps = hourly.get("temperature_2m", [])
    out = []
    for t, temp in zip(times, temps):
        if temp is None:
            continue
        ts = int(datetime.strptime(t, "%Y-%m-%dT%H:%M")
                 .replace(tzinfo=timezone.utc).timestamp())
        out.append((ts, float(temp)))
    return out


def upsert(con, rows):
    con.executemany(
        "INSERT OR REPLACE INTO outdoor_temperature (ts, temperature) VALUES (?, ?)",
        rows,
    )
    con.commit()
    return len(rows)


def backfill_past_days(con):
    """Nb de jours à remonter pour couvrir depuis la 1re mesure intérieure (cap 92 j)."""
    row = con.execute("SELECT MIN(ts) FROM measurements").fetchone()
    start = row[0] if row and row[0] is not None else int(time.time()) - 86400
    days = math.ceil((time.time() - start) / 86400) + 1
    return max(1, min(MAX_PAST_DAYS, days))


def update(con, past_days):
    rows = fetch_hourly(past_days)
    n = upsert(con, rows) if rows else 0
    logger.info("meteo %s : %d points horaires (past_days=%d)", CITY, n, past_days)


def main():
    con = connect()
    ensure_table(con)

    # 1) Backfill de l'historique au démarrage (best effort, avec réessais).
    while True:
        try:
            update(con, backfill_past_days(con))
            break
        except Exception as exc:  # réseau/HTTP/JSON : on réessaie sans planter
            logger.warning("backfill météo échoué (%s), nouvel essai dans 60 s", exc)
            time.sleep(60)

    # 2) Entretien : rattrapage récent périodique.
    while True:
        time.sleep(UPDATE_INTERVAL)
        try:
            update(con, RECENT_PAST_DAYS)
        except Exception as exc:
            logger.warning("mise à jour météo indisponible : %s", exc)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)

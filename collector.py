#!/usr/bin/env python3
"""Lecture du BME680 toutes les 30 s -> SQLite.

- Mesure : température, humidité, pression, résistance de gaz.
- Indice qualité d'air 0-100 (méthode Pimoroni : humidité 25 % + gaz 75 %),
  calculé une fois la ligne de base du gaz établie (phase de chauffe ~5 min).
"""

import json
import os
import sqlite3
import statistics
import sys
import time

import bme680

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "airmon.db")
BASELINE_PATH = os.path.join(DATA_DIR, "gas_baseline.json")

INTERVAL = 30                 # secondes entre deux mesures
BURN_IN_SAMPLES = 10          # nb de lectures stables pour établir la baseline (~5 min)
BASELINE_MAX_AGE = 7 * 86400  # on refait la chauffe si la baseline est plus vieille que ça
HUM_BASELINE = 40.0           # humidité "idéale" en %
HUM_WEIGHTING = 0.25          # part de l'humidité dans l'indice (gaz = 0.75)
TEMP_OFFSET = -2.5            # correction °C (recalibré 2026-07-26 ; ancien -1.6)


def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("""
        CREATE TABLE IF NOT EXISTS measurements (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            ts             INTEGER NOT NULL,   -- epoch secondes UTC
            temperature    REAL,
            humidity       REAL,
            pressure       REAL,
            gas_resistance REAL,
            air_quality    REAL                -- indice 0-100, NULL pendant la chauffe
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_measurements_ts ON measurements(ts)")
    con.commit()
    return con


def init_sensor():
    try:
        sensor = bme680.BME680(bme680.I2C_ADDR_SECONDARY)  # 0x77
    except (RuntimeError, IOError):
        sensor = bme680.BME680(bme680.I2C_ADDR_PRIMARY)    # 0x76 (repli)
    sensor.set_humidity_oversample(bme680.OS_2X)
    sensor.set_pressure_oversample(bme680.OS_4X)
    sensor.set_temperature_oversample(bme680.OS_8X)
    sensor.set_filter(bme680.FILTER_SIZE_3)
    sensor.set_gas_status(bme680.ENABLE_GAS_MEAS)
    sensor.set_gas_heater_temperature(320)
    sensor.set_gas_heater_duration(150)
    sensor.select_gas_heater_profile(0)
    return sensor


def load_baseline():
    try:
        with open(BASELINE_PATH) as f:
            data = json.load(f)
        if time.time() - data.get("saved_at", 0) < BASELINE_MAX_AGE:
            return float(data["gas_baseline"])
    except (OSError, ValueError, KeyError):
        pass
    return None


def save_baseline(value):
    tmp = BASELINE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"gas_baseline": value, "saved_at": time.time()}, f)
    os.replace(tmp, BASELINE_PATH)


def air_quality_score(gas, humidity, gas_baseline):
    """Indice 0-100 : humidité (25 %) + gaz (75 %). Plus haut = meilleur."""
    hum_offset = humidity - HUM_BASELINE
    if hum_offset > 0:
        hum_score = (100 - HUM_BASELINE - hum_offset) / (100 - HUM_BASELINE) * (HUM_WEIGHTING * 100)
    else:
        hum_score = (HUM_BASELINE + hum_offset) / HUM_BASELINE * (HUM_WEIGHTING * 100)

    gas_offset = gas_baseline - gas
    if gas_offset > 0:
        gas_score = (gas / gas_baseline) * (100 - (HUM_WEIGHTING * 100))
    else:
        gas_score = 100 - (HUM_WEIGHTING * 100)

    return round(max(0.0, min(100.0, hum_score + gas_score)), 1)


def read_sensor(sensor):
    """Retourne (temp, hum, press, gas|None). gas None si le chauffage n'est pas stable."""
    if not sensor.get_sensor_data():
        return None
    temp = sensor.data.temperature + TEMP_OFFSET
    hum = sensor.data.humidity
    press = sensor.data.pressure
    gas = sensor.data.gas_resistance if sensor.data.heat_stable else None
    return temp, hum, press, gas


def store(con, temp, hum, press, gas, aq):
    con.execute(
        "INSERT INTO measurements (ts, temperature, humidity, pressure, gas_resistance, air_quality) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (int(time.time()), temp, hum, press, gas, aq),
    )
    con.commit()


def main():
    con = init_db()
    sensor = init_sensor()
    gas_baseline = load_baseline()
    burn_in = []

    if gas_baseline is not None:
        print(f"[collector] baseline gaz chargée : {gas_baseline:.0f} Ohms", flush=True)
    else:
        print("[collector] phase de chauffe : établissement de la baseline gaz...", flush=True)

    # Amorçage : le premier get_sensor_data() peut être vide, on laisse le capteur démarrer.
    read_sensor(sensor)
    time.sleep(1)

    next_tick = time.monotonic()
    while True:
        reading = read_sensor(sensor)
        if reading is not None:
            temp, hum, press, gas = reading

            # Constitution de la baseline tant qu'on ne l'a pas.
            if gas_baseline is None and gas is not None:
                burn_in.append(gas)
                if len(burn_in) >= BURN_IN_SAMPLES:
                    gas_baseline = statistics.median(burn_in)
                    save_baseline(gas_baseline)
                    print(f"[collector] baseline gaz établie : {gas_baseline:.0f} Ohms", flush=True)

            aq = None
            if gas_baseline is not None and gas is not None:
                aq = air_quality_score(gas, hum, gas_baseline)

            store(con, round(temp, 2), round(hum, 2), round(press, 2),
                  round(gas, 1) if gas is not None else None, aq)
        else:
            print("[collector] lecture capteur indisponible, on réessaie", flush=True)

        next_tick += INTERVAL
        sleep_for = next_tick - time.monotonic()
        if sleep_for < 0:            # on a pris du retard, on se recale
            next_tick = time.monotonic()
            sleep_for = INTERVAL
        time.sleep(sleep_for)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)

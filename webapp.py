#!/usr/bin/env python3
"""Serveur web du dashboard qualité de l'air (port 8080).

Sert le dashboard statique et une API JSON qui lit la base SQLite remplie
par collector.py. Les grandes plages temporelles sont sous-échantillonnées
(moyenne par intervalle) pour garder des réponses légères.
"""

import os
import sqlite3
import time

from flask import Flask, jsonify, request, send_from_directory
from flask_swagger_ui import get_swaggerui_blueprint
from waitress import serve

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "airmon.db")
STATIC_DIR = os.path.join(BASE_DIR, "static")
PORT = 8080

# plage -> (durée en secondes | None pour "tout", nb de points visés)
RANGES = {
    "1h":  (3600,        180),
    "6h":  (6 * 3600,    360),
    "24h": (24 * 3600,   480),
    "7d":  (7 * 86400,   672),
    "30d": (30 * 86400,  720),
    "all": (None,        1000),
}

app = Flask(__name__, static_folder=None)

# --- Documentation OpenAPI / Swagger UI (assets embarqués, aucun CDN) ---
SWAGGER_URL = "/api/docs"
OPENAPI_URL = "/openapi.json"

_MEASUREMENT_PROPS = {
    "t":              {"type": "integer", "format": "int64", "description": "Horodatage epoch en millisecondes (UTC)."},
    "temperature":    {"type": "number", "nullable": True, "description": "Température en °C (calibrée)."},
    "humidity":       {"type": "number", "nullable": True, "description": "Humidité relative en %."},
    "pressure":       {"type": "number", "nullable": True, "description": "Pression en hPa."},
    "gas_resistance": {"type": "number", "nullable": True, "description": "Résistance de gaz en Ohms (null pendant la chauffe)."},
    "air_quality":    {"type": "number", "nullable": True, "description": "Indice qualité d'air 0-100, plus haut = meilleur (null pendant la chauffe)."},
}

OPENAPI_SPEC = {
    "openapi": "3.0.3",
    "info": {
        "title": "API Qualité de l'air — BME680",
        "version": "1.0.0",
        "description": "Mesures température / humidité / pression / résistance de gaz / indice qualité d'air "
                       "collectées toutes les 30 s sur un Raspberry Pi et stockées en SQLite.",
    },
    "servers": [{"url": "/", "description": "Ce serveur (même hôte que la doc)"}],
    "paths": {
        "/api/data": {
            "get": {
                "summary": "Série temporelle des mesures",
                "description": "Renvoie les mesures sur une plage donnée, sous-échantillonnées "
                               "(moyenne par intervalle) pour rester légères.",
                "parameters": [{
                    "name": "range",
                    "in": "query",
                    "required": False,
                    "description": "Plage temporelle.",
                    "schema": {"type": "string", "enum": ["1h", "6h", "24h", "7d", "30d", "all"], "default": "24h"},
                }],
                "responses": {"200": {
                    "description": "Points de mesure.",
                    "content": {"application/json": {"schema": {
                        "type": "object",
                        "properties": {
                            "range": {"type": "string", "example": "24h"},
                            "points": {"type": "array", "items": {"$ref": "#/components/schemas/Measurement"}},
                        },
                    }}},
                }},
            }
        },
        "/api/system": {
            "get": {
                "summary": "État du Raspberry Pi",
                "description": "Température CPU du Pi et uptime, indépendants des mesures du capteur.",
                "responses": {"200": {
                    "description": "État système.",
                    "content": {"application/json": {"schema": {
                        "type": "object",
                        "properties": {
                            "cpu_temp": {"type": "number", "nullable": True, "description": "Température CPU du Pi en °C."},
                            "uptime_seconds": {"type": "number", "nullable": True, "description": "Uptime du Pi en secondes."},
                        },
                    }}},
                }},
            }
        },
        "/api/latest": {
            "get": {
                "summary": "Dernière mesure + total",
                "description": "Renvoie la mesure la plus récente et le nombre total de mesures en base.",
                "responses": {"200": {
                    "description": "Dernière mesure.",
                    "content": {"application/json": {"schema": {
                        "type": "object",
                        "properties": {
                            "latest": {"oneOf": [
                                {"$ref": "#/components/schemas/Measurement"},
                                {"type": "null"},
                            ]},
                            "count": {"type": "integer", "description": "Nombre total de mesures."},
                        },
                    }}},
                }},
            }
        },
    },
    "components": {"schemas": {"Measurement": {"type": "object", "properties": _MEASUREMENT_PROPS}}},
}

swaggerui_bp = get_swaggerui_blueprint(
    SWAGGER_URL, OPENAPI_URL,
    config={"app_name": "API Qualité de l'air — BME680", "validatorUrl": None},
)
app.register_blueprint(swaggerui_bp, url_prefix=SWAGGER_URL)


@app.route(OPENAPI_URL)
def openapi_spec():
    return jsonify(OPENAPI_SPEC)


THERMAL_ZONE = "/sys/class/thermal/thermal_zone0/temp"


def read_cpu_temp():
    try:
        with open(THERMAL_ZONE) as f:
            return round(int(f.read().strip()) / 1000, 1)
    except (OSError, ValueError):
        return None


def read_uptime_seconds():
    try:
        with open("/proc/uptime") as f:
            return round(float(f.read().split()[0]), 0)
    except (OSError, ValueError, IndexError):
        return None


@app.route("/api/system")
def api_system():
    return jsonify({
        "cpu_temp": read_cpu_temp(),
        "uptime_seconds": read_uptime_seconds(),
    })


def query_db(range_key):
    since, target_points = RANGES.get(range_key, RANGES["24h"])
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        if since is None:
            row = con.execute("SELECT MIN(ts) AS a, MAX(ts) AS b FROM measurements").fetchone()
            if row is None or row["a"] is None:
                return []
            start, end = row["a"], row["b"]
        else:
            end = int(time.time())
            start = end - since

        span = max(1, end - start)
        bucket = max(1, span // target_points)  # taille d'intervalle en secondes

        # Moyenne par intervalle : réduit le volume tout en gardant l'allure.
        rows = con.execute(
            """
            SELECT
                (ts / :bucket) * :bucket           AS b,
                AVG(temperature)                    AS temperature,
                AVG(humidity)                       AS humidity,
                AVG(pressure)                       AS pressure,
                AVG(gas_resistance)                 AS gas_resistance,
                AVG(air_quality)                    AS air_quality
            FROM measurements
            WHERE ts >= :start
            GROUP BY b
            ORDER BY b
            """,
            {"bucket": bucket, "start": start},
        ).fetchall()
    finally:
        con.close()

    def r(v, n):
        return round(v, n) if v is not None else None

    return [
        {
            "t": row["b"] * 1000,  # epoch ms pour JS
            "temperature": r(row["temperature"], 2),
            "humidity": r(row["humidity"], 2),
            "pressure": r(row["pressure"], 2),
            "gas_resistance": r(row["gas_resistance"], 0),
            "air_quality": r(row["air_quality"], 1),
        }
        for row in rows
    ]


@app.route("/api/data")
def api_data():
    range_key = request.args.get("range", "24h")
    if range_key not in RANGES:
        range_key = "24h"
    return jsonify({"range": range_key, "points": query_db(range_key)})


@app.route("/api/latest")
def api_latest():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT ts, temperature, humidity, pressure, gas_resistance, air_quality "
            "FROM measurements ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        count = con.execute("SELECT COUNT(*) AS c FROM measurements").fetchone()["c"]
    finally:
        con.close()
    if row is None:
        return jsonify({"latest": None, "count": 0})
    return jsonify({
        "latest": {
            "t": row["ts"] * 1000,
            "temperature": row["temperature"],
            "humidity": row["humidity"],
            "pressure": row["pressure"],
            "gas_resistance": row["gas_resistance"],
            "air_quality": row["air_quality"],
        },
        "count": count,
    })


@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/static/<path:path>")
def static_files(path):
    return send_from_directory(STATIC_DIR, path)


if __name__ == "__main__":
    print(f"[web] dashboard sur http://0.0.0.0:{PORT}", flush=True)
    serve(app, host="0.0.0.0", port=PORT)

# airmon — Surveillance de la qualité de l'air (Raspberry Pi + BME680)

Collecte **température, humidité, pression, résistance de gaz** et un **indice de qualité
d'air 0-100** depuis un capteur **BME680** (I2C) sur un Raspberry Pi, stocke les mesures
dans **SQLite** (une toutes les 30 s) et les affiche via un **dashboard web** avec
graphes d'évolution dans le temps.

![dashboard](docs/dashboard.png)

## Fonctionnalités

- Mesure toutes les 30 s → base SQLite (mode WAL).
- Indice qualité d'air 0-100 (méthode Pimoroni : humidité 25 % + gaz 75 %) avec phase de
  chauffe au démarrage pour établir la ligne de base du gaz (persistée entre redémarrages).
- Dashboard responsive (thème clair/sombre) : tuiles en direct + 5 graphes, sélecteur de
  plage 1 h / 6 h / 24 h / 7 j / 30 j / Tout. Chart.js servi en local (aucun CDN).
- API JSON documentée en **OpenAPI / Swagger UI** (`/api/docs`).
- Deux services **systemd** : redémarrage automatique et au boot.

## Architecture

| Composant | Rôle |
|---|---|
| `collector.py` | Lit le BME680 toutes les 30 s → écrit dans `data/airmon.db`. |
| `webapp.py` | Serveur Flask (via waitress) : dashboard + API JSON + Swagger, port **8080**. |
| `weather.py` | Température extérieure (Open-Meteo, Lyon) → table `outdoor_temperature`. Backfill au démarrage puis MAJ toutes les 30 min. |
| `eink_display.py` | Écrans e-ink cyclables (bouton) : air intérieur + météo actuelle. |
| `static/` | Dashboard (HTML/CSS/JS) + Chart.js vendored. |
| `eink/` | Pilote Waveshare `epd2in9d` vendored (aucune dépendance réseau). |
| `systemd/` | Unités `airmon-collector`, `airmon-web`, `airmon-weather` et `airmon-eink`. |

## Prérequis matériel

- Raspberry Pi sous Debian, **I2C activé** (`sudo raspi-config` → Interface Options → I2C).
- BME680 câblé en I2C (SDA/SCL). Adresse par défaut **0x77** (repli 0x76 géré).
- Vérifier la détection : `sudo i2cdetect -y 1`.

### Écran e-ink (optionnel)

- **Dalle Waveshare 2.9″ WFT0290CZ10** (296 × 128, noir/blanc, contrôleur *busy = bas*)
  sur l'**Universal e-Paper Driver HAT**. Pilote **`epd2in9d`**.
- **Non stacké sur le header** (le BME680 occupe l'I2C) : relié par le **câble 9 broches**,
  sur des broches disjointes de l'I2C. **SPI activé** (`install.sh` le fait ; reboot requis).
- Switches du Driver HAT : **Display Config = A (3R)**, **Interface Config = B (4-line SPI)**.
- Câblage (fil e-Paper → broche physique Pi) :

  | VCC | GND | DIN | CLK | CS | DC | RST | BUSY | PWR |
  |---|---|---|---|---|---|---|---|---|
  | 17 | 20 | 19 | 23 | 24 | 22 | 11 | 18 | 12 |

  (BCM : DIN=GPIO10, CLK=GPIO11, CS=GPIO8, DC=GPIO25, RST=GPIO17, BUSY=GPIO24, PWR=GPIO18 —
  aucun conflit avec l'I2C du BME680 sur GPIO2/GPIO3.)
- Rafraîchissement toutes les **2 min** (~3,5 s par rafraîchissement complet).
  Vérifier : `ls /dev/spidev*` doit lister `spidev0.0`.
- ⚠️ La **nappe FPC** dalle↔Driver HAT doit être insérée contacts dans le bon sens et
  loquet verrouillé, sinon écran muet.

#### Écrans cyclables + bouton

`eink_display.py` affiche plusieurs **écrans** qu'on fait défiler avec un **bouton
poussoir** :

- **Air intérieur** : température / humidité / indice qualité d'air (BME680).
- **Météo actuelle** : conditions à Lyon via **Open-Meteo** (gratuit, **sans clé API**,
  `urllib` stdlib). Localisation en dur (`LAT/LON/CITY` en tête de `eink_display.py`).
  Rafraîchie en tâche de fond toutes les 15 min ; si le réseau tombe, l'écran affiche
  « Meteo indisponible » et le reste continue de fonctionner.

**Bouton** : poussoir momentané entre **GPIO26 (broche physique 37)** et **GND (broche
39)** — pull-up interne, aucun résistor. Chaque appui passe à l'écran suivant (~4 s, le
temps d'un rafraîchissement). Sans bouton câblé, le service tourne quand même sur le
premier écran.

**Ajouter un écran** : écrire une fonction `render_xxx() -> PIL.Image` (296×128) dans
`eink_display.py` — en réutilisant `draw_header()` — puis l'ajouter à la liste `VIEWS`.
Rien d'autre à toucher.

## Installation (sur le Pi)

```bash
git clone <URL_DU_REPO> ~/airmon-src
cd ~/airmon-src
./install.sh
```

Le script installe les dépendances, crée un venv dans `/opt/airmon`, copie l'appli et
active les services. Dashboard ensuite sur `http://<ip-du-pi>:8080`.

## API

| Endpoint | Description |
|---|---|
| `GET /api/data?range=1h\|6h\|24h\|7d\|30d\|all` | Série temporelle sous-échantillonnée. |
| `GET /api/outdoor?range=…` | Température extérieure horaire (Open-Meteo). |
| `GET /api/latest` | Dernière mesure + total. |
| `GET /api/docs` | Swagger UI. |
| `GET /openapi.json` | Spécification OpenAPI 3.0. |

## Calibration de la température

Le BME680 lit quelques degrés au-dessus de l'ambiant (auto-échauffement de la puce, et
chaleur du Pi s'il est proche). L'écart est constant → corrigé par la constante
`TEMP_OFFSET` en haut de `collector.py`. Comparer à un thermomètre de référence, ajuster,
puis `sudo systemctl restart airmon-collector`.

## Exploitation

```bash
sudo systemctl status airmon-collector airmon-web airmon-eink
journalctl -u airmon-collector -f      # logs du collecteur
journalctl -u airmon-eink -f           # logs de l'écran e-ink
sudo systemctl restart airmon-collector
```

La base est dans `/opt/airmon/data/airmon.db`. Pour repartir de zéro : arrêter le
collecteur, vider la table `measurements`, supprimer `data/gas_baseline.json`, redémarrer.

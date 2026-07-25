import csv
import logging
import os
import sqlite3
import time
from pathlib import Path

import requests

from app.db import DB_PATH

logger = logging.getLogger("rarebirdalert.aircraft_db")

METADATA_URL = "https://opensky-network.org/datasets/metadata/aircraftDatabase.csv"
CACHE_PATH = Path(os.environ.get("RAREBIRDALERT_AIRCRAFT_DB_CACHE", "/app/data/aircraft-db.csv"))
REFRESH_INTERVAL_SECONDS = 7 * 24 * 3600

# (source CSV column, AircraftMetadata column) - source has more columns than
# we care about (serial numbers, engines, ...), we only keep what matcher.py
# and the UI need.
_COLUMN_MAP = [
    ("icao24", "icao24"),
    ("registration", "registration"),
    ("manufacturername", "manufacturer"),
    ("model", "model"),
    ("typecode", "typecode"),
    ("operator", "operator"),
    ("icaoaircrafttype", "icao_aircraft_type"),
    ("categorydescription", "category_description"),
]


def _download(dest: Path) -> bool:
    tmp = dest.with_suffix(".tmp")
    try:
        with requests.get(METADATA_URL, stream=True, timeout=180) as resp:
            resp.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    f.write(chunk)
    except (requests.RequestException, OSError) as exc:
        logger.error("Aircraft database download failed: %s", exc)
        tmp.unlink(missing_ok=True)
        return False
    tmp.replace(dest)
    return True


def _load_into_db(csv_path: Path) -> int:
    # Bulk upsert of a few hundred thousand rows via the ORM would be far too
    # slow - go through sqlite3 directly. init_db() has already created the
    # aircraftmetadata table (from the AircraftMetadata SQLModel) by the time
    # this runs, with columns matching _COLUMN_MAP's destination names.
    conn = sqlite3.connect(DB_PATH, timeout=30)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        batch = []
        count = 0
        with open(csv_path, newline="", encoding="utf-8", errors="replace") as f:
            # The upstream CSV lower-cases inconsistently across exports;
            # normalize header names before mapping.
            reader = csv.DictReader(f)
            reader.fieldnames = [(name or "").strip().lower() for name in reader.fieldnames or []]
            for row in reader:
                icao24 = (row.get("icao24") or "").strip().lower()
                if not icao24:
                    continue
                values = [icao24]
                for src, _dst in _COLUMN_MAP[1:]:
                    value = (row.get(src) or "").strip()
                    values.append(value.upper() if src == "typecode" else value or None)
                batch.append(tuple(values))
                count += 1
                if len(batch) >= 5000:
                    # Commit per batch (not once at the end) so this long-running
                    # import never holds a single multi-hundred-thousand-row
                    # transaction open, which would starve concurrent readers.
                    conn.executemany(
                        "INSERT OR REPLACE INTO aircraftmetadata VALUES (?,?,?,?,?,?,?,?)", batch
                    )
                    conn.commit()
                    batch = []
        if batch:
            conn.executemany("INSERT OR REPLACE INTO aircraftmetadata VALUES (?,?,?,?,?,?,?,?)", batch)
            conn.commit()
        return count
    finally:
        conn.close()


def refresh_aircraft_db(force: bool = False) -> None:
    """Downloads and (re-)loads the aircraft metadata cache if it's missing
    or older than REFRESH_INTERVAL_SECONDS. Safe to call on every startup."""
    if not force and CACHE_PATH.exists():
        age = time.time() - CACHE_PATH.stat().st_mtime
        if age < REFRESH_INTERVAL_SECONDS:
            logger.info("Aircraft metadata cache is up to date (age %.1fh)", age / 3600)
            return

    logger.info("Refreshing aircraft metadata database from OpenSky (this can take a minute)...")
    os.makedirs(CACHE_PATH.parent, exist_ok=True)
    if not _download(CACHE_PATH):
        if not CACHE_PATH.exists():
            logger.warning("No aircraft metadata cache available yet - sightings will lack type info")
        return

    count = _load_into_db(CACHE_PATH)
    logger.info("Loaded %d aircraft metadata rows", count)


def lookup(icao24: str) -> dict | None:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM aircraftmetadata WHERE icao24 = ?", (icao24.strip().lower(),)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

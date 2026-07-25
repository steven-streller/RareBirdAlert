import csv
import time

from app import aircraft_db

SAMPLE_HEADER = [
    "icao24",
    "registration",
    "manufacturericao",
    "manufacturername",
    "model",
    "typecode",
    "operator",
    "icaoaircrafttype",
    "categoryDescription",
]


def _write_sample_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(SAMPLE_HEADER)
        writer.writerows(rows)


def test_load_into_db_inserts_and_skips_blank_icao24(tmp_path, test_engine):
    csv_path = tmp_path / "sample.csv"
    _write_sample_csv(
        csv_path,
        [
            ["3c6444", "31+00", "EUROFIGHTER", "Eurofighter", "Typhoon", "eufi", "Luftwaffe", "L2J", "Military"],
            ["", "N12345", "CESSNA", "Cessna", "150", "C150", "", "L1P", ""],
        ],
    )

    count = aircraft_db._load_into_db(csv_path)

    assert count == 1
    entry = aircraft_db.lookup("3c6444")
    assert entry is not None
    assert entry["registration"] == "31+00"
    assert entry["typecode"] == "EUFI"  # normalized to uppercase
    assert entry["operator"] == "Luftwaffe"


def test_lookup_returns_none_for_unknown_icao24(test_engine):
    assert aircraft_db.lookup("ffffff") is None


def test_refresh_skips_download_when_cache_is_fresh(tmp_path, monkeypatch):
    cache_path = tmp_path / "aircraft-db.csv"
    cache_path.write_text("icao24\n")
    monkeypatch.setattr(aircraft_db, "CACHE_PATH", cache_path)

    called = []
    monkeypatch.setattr(aircraft_db, "_download", lambda dest: called.append(dest) or True)

    aircraft_db.refresh_aircraft_db()

    assert called == []


def test_refresh_downloads_when_cache_is_stale(tmp_path, monkeypatch):
    cache_path = tmp_path / "aircraft-db.csv"
    cache_path.write_text("icao24\n")
    old_time = time.time() - aircraft_db.REFRESH_INTERVAL_SECONDS - 10
    import os

    os.utime(cache_path, (old_time, old_time))
    monkeypatch.setattr(aircraft_db, "CACHE_PATH", cache_path)

    called = []
    monkeypatch.setattr(aircraft_db, "_download", lambda dest: called.append(dest) or True)
    monkeypatch.setattr(aircraft_db, "_load_into_db", lambda path: 0)

    aircraft_db.refresh_aircraft_db()

    assert called == [cache_path]


def test_refresh_downloads_when_cache_is_missing(tmp_path, monkeypatch):
    cache_path = tmp_path / "does-not-exist.csv"
    monkeypatch.setattr(aircraft_db, "CACHE_PATH", cache_path)

    called = []
    monkeypatch.setattr(aircraft_db, "_download", lambda dest: called.append(dest) or True)
    monkeypatch.setattr(aircraft_db, "_load_into_db", lambda path: 0)

    aircraft_db.refresh_aircraft_db()

    assert called == [cache_path]

import csv
import os
import time

import requests

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


def test_refresh_skips_load_when_download_fails_and_no_cache_exists(tmp_path, monkeypatch):
    cache_path = tmp_path / "does-not-exist.csv"
    monkeypatch.setattr(aircraft_db, "CACHE_PATH", cache_path)
    monkeypatch.setattr(aircraft_db, "_download", lambda dest: False)

    called = []
    monkeypatch.setattr(aircraft_db, "_load_into_db", lambda path: called.append(path) or 0)

    aircraft_db.refresh_aircraft_db()

    assert called == []


class _FakeStreamResponse:
    def __init__(self, chunks, status_code=200):
        self._chunks = chunks
        self.status_code = status_code

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=None):
        return iter(self._chunks)


def test_download_success_writes_dest_and_removes_tmp_file(tmp_path, monkeypatch):
    dest = tmp_path / "aircraft-db.csv"
    monkeypatch.setattr(
        aircraft_db.requests, "get", lambda url, stream=True, timeout=None: _FakeStreamResponse([b"a,b\n", b"1,2\n"])
    )

    assert aircraft_db._download(dest) is True
    assert dest.read_bytes() == b"a,b\n1,2\n"
    assert not dest.with_suffix(".tmp").exists()


def test_download_failure_cleans_up_tmp_file_and_leaves_no_dest(tmp_path, monkeypatch):
    def raise_exc(url, stream=True, timeout=None):
        raise requests.ConnectionError("boom")

    monkeypatch.setattr(aircraft_db.requests, "get", raise_exc)

    dest = tmp_path / "aircraft-db.csv"
    assert aircraft_db._download(dest) is False
    assert not dest.exists()
    assert not dest.with_suffix(".tmp").exists()


def test_download_http_error_status_returns_false(tmp_path, monkeypatch):
    monkeypatch.setattr(
        aircraft_db.requests, "get", lambda url, stream=True, timeout=None: _FakeStreamResponse([], status_code=503)
    )

    dest = tmp_path / "aircraft-db.csv"
    assert aircraft_db._download(dest) is False
    assert not dest.exists()


def test_load_into_db_commits_in_batches_over_5000_rows(tmp_path, test_engine):
    csv_path = tmp_path / "big.csv"
    rows = [[f"{i:06x}", f"REG{i}", "", "", "", "TYPE", "", "", ""] for i in range(5001)]
    _write_sample_csv(csv_path, rows)

    count = aircraft_db._load_into_db(csv_path)

    assert count == 5001
    assert aircraft_db.lookup(f"{0:06x}") is not None  # from the first, committed-mid-loop batch
    assert aircraft_db.lookup(f"{5000:06x}") is not None  # from the final, tail-commit batch

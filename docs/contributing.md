# Mitwirken

## Ablauf

`main` ist geschützt: jede Änderung läuft über einen Branch + Pull Request,
direkte Pushes werden abgelehnt (auch für Repo-Admins). Zum Mergen müssen
zwei Status-Checks grün sein:

- `lint-and-test` – `ruff check .` + `pytest` (mit Coverage-Gate, siehe [Tests](#tests))
- `docker-build` – das Image muss bauen

Es ist kein Review-Approval erforderlich, damit auch Solo-Änderungen ohne
zweite Person gemergt werden können – die CI-Checks sind das eigentliche Gate.

Zusätzlich läuft bei jedem Push/PR ein Trivy-Scan des gebauten Images
(innerhalb von `docker-build`) sowie CodeQL auf dem Python-Code (eigener
`codeql`-Workflow, zusätzlich wöchentlich geplant) – beide sind nicht
merge-blockierend, Funde landen im "Security"-Reiter des Repos.

```bash
git checkout -b feature/mein-feature
# Änderungen ...
git push -u origin feature/mein-feature
gh pr create
```

## Lokal einrichten

Siehe [Entwicklung](setup/development.md) für venv, Tests, Lint und
Projektstruktur.

```bash
pytest              # muss durchlaufen
ruff check .         # muss sauber sein
```

Ruff-Regeln, die absichtlich ignoriert werden (siehe `pyproject.toml`):
`UP007`/`UP045` (Stil-Präferenz `Optional[X]` statt `X | None` für
SQLModel-Felder) und `B008` (der übliche FastAPI-`Depends(...)`-Default, den
Ruffs Bugbear-Regel fälschlich als Problem meldet).

## Tests

Neue Routen/Logik sollten nach Möglichkeit mit abgedeckt werden –
`tests/conftest.py` stellt dafür zwei Fixtures bereit:

- `test_engine`: frische SQLite-Datei pro Test, in alle Module verdrahtet, die
  `engine` importieren (inkl. `app.aircraft_db.DB_PATH`, das den Pfad separat
  importiert)
- `client`: ein `TestClient` über die echte App, **ohne** das Startup-Event
  auszulösen (kein echter Scheduler, kein Netzwerkzugriff im Test)

```python
def test_my_new_route(client):
    register(client, "alice@example.com")
    resp = client.get("/my-route")
    assert resp.status_code == 200
```

Tests, die OpenSky- oder Netzwerk-Aufrufe berühren, mocken diese immer
(siehe `tests/test_opensky_client.py`) – der Testlauf darf nie vom Internet
abhängen.

### E2E-Tests

`e2e/` enthält Playwright-Browsertests für den Golden Path (registrieren,
Flughafen über die Live-Suche hinzufügen, Ruhezeiten speichern) - bewusst
getrennt von `tests/`, weil sie einen echten Browser und einen echten
laufenden Server brauchen, statt der In-Process-ASGI-`TestClient`s. Sie
fangen Dinge, die die restliche Suite grundsätzlich nicht sehen kann - z. B.
ein rein visuelles CSS-Problem (weiße statt dunkle Eingabefelder), das sich
in keinem HTTP-Status-Code oder HTML-String-Vergleich zeigt.

Lokal ausführen:

```bash
pip install -r requirements-e2e.txt
playwright install chromium
pytest e2e/ -v
```

`e2e/conftest.py` startet dafür die echte App als Subprozess (eigene
SQLite-Datei, `DISABLE_SCHEDULER=true` - sonst würde jeder Testlauf die
~500.000-Zeilen-Flugzeugdatenbank herunterladen und echte Datenquellen
abfragen, sobald ein Flughafen beobachtet wird). Läuft als eigener
`e2e`-Job in CI, getrennt von `lint-and-test` - `pytest` ohne Pfadangabe
(wie in `lint-and-test`) sammelt wegen `testpaths = ["tests"]` in
`pyproject.toml` ohnehin nie etwas aus `e2e/` ein.

### Coverage

CI läuft mit `pytest --cov=app --cov-report=term-missing` und schlägt fehl,
wenn die Gesamt-Coverage unter 80 % fällt (`fail_under` in `pyproject.toml`).
Lokal genauso mit `--cov=app --cov-report=term-missing` prüfen, um zu sehen,
welche Zeilen eine neue Änderung noch nicht abdeckt. Bewusst nicht auf 100 %
gesetzt: ein paar Zeilen (z. B. der echte Scheduler-Start beim App-Boot) sind
nur mit unverhältnismäßigem Aufwand oder Testflakiness sauber zu testen –
80 % ist die Schwelle, die reale Lücken auffängt, ohne dazu zu verleiten,
Tests nur für die Zahl zu schreiben.

## Abhängigkeiten

Dependabot hält `requirements*.txt` und GitHub Actions aktuell und öffnet
wöchentlich PRs. Minor/Patch-Updates mergen automatisch, sobald die Checks
grün sind (`.github/workflows/dependabot-automerge.yml`); Major-Updates
bleiben zur manuellen Prüfung offen.

## Neuen Benachrichtigungskanal hinzufügen

Siehe [Architektur](architecture.md#benachrichtigungskanale-appnotificationspy) –
ein Eintrag im `CHANNELS`-Dict in `app/notifications.py` reicht, die
Einstellungen-Seite rendert die Formularfelder automatisch daraus.

## Eine eingebaute Watchlist-Kategorie ergänzen oder korrigieren

Die kuratierte Startliste in `CATEGORIES` (`app/db.py`) erhebt bewusst keinen
Anspruch auf Vollständigkeit oder hundertprozentige Genauigkeit – Korrekturen
und neue Kategorien (per PR) sind willkommen, siehe [Watchlist](watchlist.md)
für das Muster-Format.

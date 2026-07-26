"""Aggregation queries for the personal stats page (app/main.py's /stats
route) - pulled out of main.py the same way app/matcher.py holds the pure
matching logic, so the route handler stays a thin session/template glue.
"""

from datetime import datetime

from sqlalchemy import func
from sqlmodel import Session, select

from app.models import Airport, AirportWatch, Sighting, SightingMatch

TOP_N = 8
MONTHS_BACK = 12


def compute_stats(session: Session, user_id: int) -> dict:
    """Stats over sightings at the user's *watched* airports - same scope as
    the dashboard feed (app/main.py::dashboard), not narrowed to whichever
    categories/watchlist entries the user currently has notifications
    enabled for. Shows "what showed up for you", not "what you were paged
    about".
    """
    watch_airport_ids = session.exec(
        select(AirportWatch.airport_id).where(AirportWatch.user_id == user_id)
    ).all()
    if not watch_airport_ids:
        return {
            "total": 0,
            "first_seen": None,
            "last_seen": None,
            "top_types": [],
            "top_airports": [],
            "top_labels": [],
            "monthly": [],
        }

    total = session.exec(
        select(func.count()).select_from(Sighting).where(Sighting.airport_id.in_(watch_airport_ids))
    ).one()

    first_seen, last_seen = session.exec(
        select(func.min(Sighting.landed_at), func.max(Sighting.landed_at)).where(
            Sighting.airport_id.in_(watch_airport_ids)
        )
    ).one()

    top_types = [
        {"label": typecode, "count": count}
        for typecode, count in session.exec(
            select(Sighting.typecode, func.count().label("count"))
            .where(Sighting.airport_id.in_(watch_airport_ids), Sighting.typecode.is_not(None))
            .group_by(Sighting.typecode)
            .order_by(func.count().desc())
            .limit(TOP_N)
        ).all()
    ]

    airport_counts = session.exec(
        select(Sighting.airport_id, func.count().label("count"))
        .where(Sighting.airport_id.in_(watch_airport_ids))
        .group_by(Sighting.airport_id)
        .order_by(func.count().desc())
        .limit(TOP_N)
    ).all()
    airports = {a.id: a for a in session.exec(select(Airport).where(Airport.id.in_(watch_airport_ids)))}
    top_airports = [
        {"label": airports[airport_id].icao, "name": airports[airport_id].name, "count": count}
        for airport_id, count in airport_counts
        if airport_id in airports
    ]

    top_labels = [
        {"label": label, "count": count}
        for label, count in session.exec(
            select(SightingMatch.label, func.count().label("count"))
            .join(Sighting, Sighting.id == SightingMatch.sighting_id)
            .where(Sighting.airport_id.in_(watch_airport_ids))
            .group_by(SightingMatch.label)
            .order_by(func.count().desc())
            .limit(TOP_N)
        ).all()
    ]

    # SQLite-specific (strftime) - fine here, the app only ever runs against
    # SQLite (see app/db.py's WAL setup).
    month_key = func.strftime("%Y-%m", Sighting.landed_at)
    monthly_counts = dict(
        session.exec(
            select(month_key, func.count())
            .where(Sighting.airport_id.in_(watch_airport_ids))
            .group_by(month_key)
        ).all()
    )
    monthly = _last_n_months(MONTHS_BACK, monthly_counts)

    return {
        "total": total,
        "first_seen": first_seen,
        "last_seen": last_seen,
        "top_types": top_types,
        "top_airports": top_airports,
        "top_labels": top_labels,
        "monthly": monthly,
    }


def _last_n_months(n: int, counts_by_month: dict[str, int]) -> list[dict]:
    """Always returns exactly `n` entries, oldest first, filling in zero for
    months with no sightings - so the chart's x-axis doesn't silently skip
    quiet months."""
    now = datetime.utcnow()
    months = []
    for i in range(n - 1, -1, -1):
        total_month_index = now.year * 12 + (now.month - 1) - i
        year, month = divmod(total_month_index, 12)
        key = f"{year:04d}-{month + 1:02d}"
        months.append({"month": key, "count": counts_by_month.get(key, 0)})
    return months

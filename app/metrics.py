from prometheus_client import Counter, Histogram

sightings_total = Counter(
    "rarebirdalert_sightings_total",
    "Number of Sighting rows created (a landing matched a category or watchlist entry).",
)

notifications_sent_total = Counter(
    "rarebirdalert_notifications_sent_total",
    "Notification send attempts, by channel and result.",
    ["channel", "result"],
)

poll_duration_seconds = Histogram(
    "rarebirdalert_poll_duration_seconds",
    "Time spent fetching and processing aircraft states for one poll_job run.",
)

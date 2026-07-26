from sqlmodel import Session, SQLModel, create_engine

from app import metrics
from app.db import set_setting, set_user_setting
from app.models import PushSubscription, User
from app.notifications import CHANNELS, _channel_config, enabled_channels, notify_all


def make_session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_channel_registry_keys_match_declared_fields():
    for channel in CHANNELS.values():
        assert channel["keys"] == [field[0] for field in channel["fields"]]
        assert callable(channel["send"])


def test_enabled_channels_empty_by_default():
    session = make_session()
    assert enabled_channels(session, user_id=1) == []


def test_enabled_channels_respects_setting():
    session = make_session()
    set_user_setting(session, 1, "ntfy_enabled", "true")
    set_user_setting(session, 1, "ntfy_server_url", "https://ntfy.sh")
    set_user_setting(session, 1, "ntfy_topic", "test-topic")
    assert enabled_channels(session, user_id=1) == ["ntfy"]


def test_notify_all_reports_false_for_unconfigured_enabled_channel():
    session = make_session()
    set_user_setting(session, 1, "discord_enabled", "true")
    # discord_webhook_url intentionally left blank -> must fail without a network call
    results = notify_all(session, 1, "Titel", "Nachricht")
    assert results == {"discord": False}


def test_notify_all_increments_the_notifications_sent_metric_with_result_label():
    # Counters are process-wide singletons shared across the whole test
    # session, so assert on the delta rather than an absolute value.
    before = metrics.notifications_sent_total.labels(channel="discord", result="fail")._value.get()

    session = make_session()
    set_user_setting(session, 1, "discord_enabled", "true")
    notify_all(session, 1, "Titel", "Nachricht")  # webhook URL blank -> fails without a network call

    after = metrics.notifications_sent_total.labels(channel="discord", result="fail")._value.get()
    assert after == before + 1


def test_enabled_channels_respects_webpush_setting():
    session = make_session()
    set_user_setting(session, 1, "webpush_enabled", "true")
    assert enabled_channels(session, user_id=1) == ["webpush"]


def test_channel_config_for_webpush_includes_subscriptions_and_vapid_key():
    session = make_session()
    session.add(User(id=1, email="alice@example.com", password_hash="x"))
    session.add(PushSubscription(user_id=1, endpoint="https://push.example/abc", p256dh="p", auth="a"))
    session.commit()
    set_setting(session, "vapid_private_key_pem", "fake-pem")

    cfg = _channel_config(session, 1, "webpush")

    assert len(cfg["subscriptions"]) == 1
    assert cfg["subscriptions"][0].endpoint == "https://push.example/abc"
    assert cfg["vapid_private_key_pem"] == "fake-pem"
    assert cfg["user_email"] == "alice@example.com"
    assert cfg["session"] is session


def test_user_settings_are_isolated_between_users():
    session = make_session()
    set_user_setting(session, 1, "ntfy_enabled", "true")
    set_user_setting(session, 1, "ntfy_server_url", "https://ntfy.sh")
    set_user_setting(session, 1, "ntfy_topic", "user-1-topic")

    # user 2 never configured anything - must not see user 1's channel or settings
    assert enabled_channels(session, user_id=2) == []
    assert enabled_channels(session, user_id=1) == ["ntfy"]

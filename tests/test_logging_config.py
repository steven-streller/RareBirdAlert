import json
import logging
import sys

import pytest

from app.logging_config import JSONFormatter, _resolve_log_level, configure_logging


def _make_record(msg="hello", level=logging.INFO, exc_info=None, extra=None):
    record = logging.LogRecord(
        name="rarebirdalert.test",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=exc_info,
    )
    for key, value in (extra or {}).items():
        setattr(record, key, value)
    return record


def test_json_formatter_includes_core_fields():
    payload = json.loads(JSONFormatter().format(_make_record("hello world")))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "rarebirdalert.test"
    assert payload["message"] == "hello world"
    assert "timestamp" in payload


def test_json_formatter_surfaces_extra_fields():
    record = _make_record("sighting", extra={"sighting_id": 42, "airport": "EDDF"})
    payload = json.loads(JSONFormatter().format(record))

    assert payload["sighting_id"] == 42
    assert payload["airport"] == "EDDF"


def test_json_formatter_includes_exception_info():
    try:
        raise ValueError("boom")
    except ValueError:
        record = _make_record("failed", exc_info=sys.exc_info())

    payload = json.loads(JSONFormatter().format(record))
    assert "ValueError: boom" in payload["exception"]


def test_json_formatter_falls_back_to_str_for_unserializable_extra_values():
    class Thing:
        def __str__(self):
            return "a-thing"

    record = _make_record("obj", extra={"thing": Thing()})
    payload = json.loads(JSONFormatter().format(record))

    assert payload["thing"] == "a-thing"


def test_json_formatter_output_is_a_single_line():
    record = _make_record("multi\nline\nmessage")
    formatted = JSONFormatter().format(record)

    assert "\n" not in formatted
    assert json.loads(formatted)["message"] == "multi\nline\nmessage"


@pytest.fixture
def restore_logging_state():
    """configure_logging() mutates process-wide logging state (the root
    logger's handlers, three uvicorn loggers) - save and restore it so these
    tests don't leak configuration into whatever runs after them.
    """
    root = logging.getLogger()
    saved_root_handlers = list(root.handlers)
    saved_root_level = root.level
    saved_uvicorn = {
        name: (list(logging.getLogger(name).handlers), logging.getLogger(name).propagate)
        for name in ("uvicorn", "uvicorn.access", "uvicorn.error")
    }
    yield
    root.handlers = saved_root_handlers
    root.setLevel(saved_root_level)
    for name, (handlers, propagate) in saved_uvicorn.items():
        logger = logging.getLogger(name)
        logger.handlers = handlers
        logger.propagate = propagate


def test_configure_logging_uses_json_formatter_when_requested(monkeypatch, restore_logging_state):
    monkeypatch.setenv("LOG_FORMAT", "json")
    configure_logging()

    root = logging.getLogger()
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, JSONFormatter)


def test_configure_logging_defaults_to_plain_text(monkeypatch, restore_logging_state):
    monkeypatch.delenv("LOG_FORMAT", raising=False)
    configure_logging()

    root = logging.getLogger()
    assert len(root.handlers) == 1
    assert not isinstance(root.handlers[0].formatter, JSONFormatter)


def test_configure_logging_defers_uvicorn_loggers_to_the_root_handler(monkeypatch, restore_logging_state):
    monkeypatch.setenv("LOG_FORMAT", "json")
    configure_logging()

    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(name)
        assert logger.handlers == []
        assert logger.propagate is True


def test_resolve_log_level_accepts_known_level_names():
    assert _resolve_log_level("DEBUG") == logging.DEBUG
    assert _resolve_log_level("info") == logging.INFO
    assert _resolve_log_level("Warning") == logging.WARNING
    assert _resolve_log_level("ERROR") == logging.ERROR
    assert _resolve_log_level("CRITICAL") == logging.CRITICAL


def test_resolve_log_level_falls_back_to_info_for_unknown_or_empty_values():
    assert _resolve_log_level("not-a-real-level") == logging.INFO
    assert _resolve_log_level("") == logging.INFO
    assert _resolve_log_level(None) == logging.INFO


def test_configure_logging_applies_the_requested_level(monkeypatch, restore_logging_state):
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    configure_logging()

    assert logging.getLogger().level == logging.DEBUG


def test_configure_logging_defaults_to_info_level(monkeypatch, restore_logging_state):
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    configure_logging()

    assert logging.getLogger().level == logging.INFO


def test_configure_logging_applies_the_level_to_uvicorn_loggers_too(monkeypatch, restore_logging_state):
    # Regression test: uvicorn sets an explicit level on its own loggers
    # before app.main is even imported - an explicit level on a logger wins
    # over the root logger's regardless of propagation, so LOG_LEVEL=WARNING
    # would otherwise silently leave uvicorn's own INFO lines showing
    # through. Caught by a live container run, not by reasoning alone.
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    configure_logging()

    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        assert logging.getLogger(name).level == logging.WARNING

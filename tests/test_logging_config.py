"""Tests for the JSON logging + correlation ID plumbing."""
from __future__ import annotations

import json
import logging
import os
from io import StringIO

import pytest

from app import logging_config


def _capture_logs(use_json: bool = True) -> tuple[StringIO, logging.Logger]:
    """Reset root handlers, install a capture stream, and return it."""
    os.environ["LOG_FORMAT"] = "json" if use_json else "text"
    logging_config.configure_logging(level="DEBUG")
    # Replace the stdout handler with one writing into a StringIO
    stream = StringIO()
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(stream)
    handler.addFilter(logging_config._ContextFilter())
    handler.setFormatter(logging_config.JsonFormatter() if use_json else logging.Formatter("%(message)s"))
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    return stream, root


def test_json_format_emits_valid_json():
    stream, root = _capture_logs(use_json=True)
    logging.getLogger("test").info("hello world")
    line = stream.getvalue().strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["msg"] == "hello world"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test"


def test_correlation_id_in_log_record():
    stream, _ = _capture_logs(use_json=True)
    cid = logging_config.set_correlation_id("abc123")
    assert cid == "abc123"
    logging.getLogger("trace").info("traced")
    payload = json.loads(stream.getvalue().strip().splitlines()[-1])
    assert payload["correlation_id"] == "abc123"


def test_set_correlation_id_generates_when_none():
    logging_config.clear_log_context()
    cid = logging_config.set_correlation_id(None)
    assert cid
    assert len(cid) >= 8


def test_context_cleared_between_runs():
    logging_config.set_log_context(user_id="u1", article_id="a1")
    assert logging_config._user_id.get() == "u1"
    logging_config.clear_log_context()
    assert logging_config._user_id.get() == ""


def test_set_log_context_partial_update():
    logging_config.clear_log_context()
    logging_config.set_log_context(user_id="u1")
    logging_config.set_log_context(article_id="a1")
    assert logging_config._user_id.get() == "u1"
    assert logging_config._article_id.get() == "a1"


def test_exc_info_serialized_in_json():
    stream, _ = _capture_logs(use_json=True)
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        logging.getLogger("errtest").exception("caught")
    payload = json.loads(stream.getvalue().strip().splitlines()[-1])
    assert payload["msg"] == "caught"
    assert "exc_info" in payload
    assert "RuntimeError" in payload["exc_info"]


def test_extra_fields_are_merged_when_serializable():
    stream, _ = _capture_logs(use_json=True)
    logging.getLogger("extra").info("with extras", extra={"article_id": "art-1", "custom": 42})
    payload = json.loads(stream.getvalue().strip().splitlines()[-1])
    # article_id goes through the contextvar path, custom stays as-is
    assert payload["custom"] == 42

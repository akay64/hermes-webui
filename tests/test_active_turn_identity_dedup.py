"""Regression coverage for active-turn identity deduplication.

These tests reconstruct the reported eager-checkpoint/recovery shape because the
original private session capture is not present in this checkout. They assert
behavior at the provider-error, cancellation, and stale-recovery boundaries,
including the important distinction between one turn replayed twice and two
intentional turns with identical prompt text.
"""
from __future__ import annotations

import copy
import queue
import threading

import pytest

import api.config as config
import api.models as models
from api.models import Session, _apply_core_sync_or_error_marker
from api.process_event_utils import build_active_turn_token
from api.streaming import _materialize_pending_user_turn_before_error, cancel_stream


@pytest.fixture(autouse=True)
def isolate_state(tmp_path, monkeypatch):
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    models.SESSIONS.clear()
    config.STREAMS.clear()
    config.CANCEL_FLAGS.clear()
    config.AGENT_INSTANCES.clear()
    config.SESSION_AGENT_LOCKS.clear()
    yield
    models.SESSIONS.clear()
    config.STREAMS.clear()
    config.CANCEL_FLAGS.clear()
    config.AGENT_INSTANCES.clear()
    config.SESSION_AGENT_LOCKS.clear()


def _checkpoint(stream_id, started_at, content="[Workspace::v1: /tmp/project]\nPlease commit."):
    return {
        "role": "user",
        "content": content,
        "timestamp": int(started_at),
        "_active_turn_token": build_active_turn_token(stream_id, started_at),
        "attachments": [{"name": "notes.txt", "path": "/tmp/notes.txt"}],
    }


def _pending_session(stream_id, started_at, messages):
    session = Session(session_id=f"identity-{stream_id}", messages=messages)
    session.active_stream_id = stream_id
    session.pending_started_at = started_at
    session.pending_user_message = "Please commit."
    session.pending_attachments = [{"name": "notes.txt", "path": "/tmp/notes.txt"}]
    session.pending_user_source = "webui"
    session.context_messages = [copy.deepcopy(messages[0])] if messages else []
    return session


def test_provider_error_reuses_eager_checkpoint_after_assistant_output():
    stream_id = "provider-error-stream"
    started_at = 1234.5
    checkpoint = _checkpoint(stream_id, started_at)
    session = _pending_session(
        stream_id,
        started_at,
        [checkpoint, {"role": "assistant", "content": "provider failed", "_error": True}],
    )

    materialized = _materialize_pending_user_turn_before_error(session)

    assert materialized is False
    user_messages = [m for m in session.messages if m.get("role") == "user"]
    assert user_messages == [checkpoint]
    assert [m for m in session.context_messages if m.get("role") == "user"] == [checkpoint]
    assert session.messages[0].get("_recovered") is None
    assert session.messages[0]["attachments"] == checkpoint["attachments"]


def test_provider_error_synthesizes_once_and_stamps_token():
    stream_id = "provider-error-new-stream"
    started_at = 2345.5
    session = _pending_session(
        stream_id,
        started_at,
        [{"role": "assistant", "content": "previous answer"}],
    )
    session.context_messages = [copy.deepcopy(session.messages[0])]

    assert _materialize_pending_user_turn_before_error(session) is True
    recovered = [m for m in session.messages if m.get("role") == "user"]

    assert len(recovered) == 1
    assert recovered[0]["_active_turn_token"] == build_active_turn_token(stream_id, started_at)
    assert recovered[0]["_recovered"] is True
    assert recovered[0]["attachments"] == session.pending_attachments
    assert [m for m in session.context_messages if m.get("role") == "user"] == recovered


def test_identical_prompt_with_different_token_is_a_distinct_turn():
    first_stream = "first-identical-stream"
    second_stream = "second-identical-stream"
    first_started = 3000.5
    second_started = 3001.5
    first_checkpoint = _checkpoint(first_stream, first_started, "Please commit.")
    session = _pending_session(
        second_stream,
        second_started,
        [first_checkpoint, {"role": "assistant", "content": "first failed", "_error": True}],
    )
    session.context_messages = [copy.deepcopy(first_checkpoint)]

    assert _materialize_pending_user_turn_before_error(session) is True
    user_messages = [m for m in session.messages if m.get("role") == "user"]

    assert len(user_messages) == 2
    assert {m["_active_turn_token"] for m in user_messages} == {
        build_active_turn_token(first_stream, first_started),
        build_active_turn_token(second_stream, second_started),
    }


def test_cancel_reuses_checkpoint_and_preserves_it_after_reload():
    stream_id = "cancel-identity-stream"
    started_at = 4000.5
    checkpoint = _checkpoint(stream_id, started_at)
    session = _pending_session(
        stream_id,
        started_at,
        [checkpoint],
    )
    session_id = session.session_id
    session.save()
    models.SESSIONS[session_id] = session
    config.STREAMS[stream_id] = queue.Queue()
    config.CANCEL_FLAGS[stream_id] = threading.Event()

    class Agent:
        def __init__(self, sid):
            self.session_id = sid

        @staticmethod
        def interrupt():
            return None

    config.AGENT_INSTANCES[stream_id] = Agent(session_id)

    assert cancel_stream(stream_id) is True
    models.SESSIONS.clear()
    reloaded = Session.load(session_id)
    user_messages = [m for m in reloaded.messages if m.get("role") == "user"]

    assert len(user_messages) == 1
    assert user_messages[0]["_active_turn_token"] == build_active_turn_token(stream_id, started_at)
    assert [m for m in reloaded.context_messages if m.get("role") == "user"] == user_messages


def test_stale_recovery_reuses_checkpoint_and_does_not_mark_it_recovered(tmp_path):
    stream_id = "stale-identity-stream"
    started_at = 5000.5
    checkpoint = _checkpoint(stream_id, started_at)
    session = _pending_session(
        stream_id,
        started_at,
        [checkpoint, {"role": "assistant", "content": "partial", "_partial": True}],
    )
    session_id = session.session_id
    session.save()
    models.SESSIONS[session_id] = session

    applied = _apply_core_sync_or_error_marker(
        session,
        tmp_path / "missing-core.json",
        stream_id_for_recheck=stream_id,
        require_stream_dead=False,
    )

    assert applied is True
    user_messages = [m for m in session.messages if m.get("role") == "user"]
    assert user_messages == [checkpoint]
    assert user_messages[0].get("_recovered") is None
    assert [m for m in session.context_messages if m.get("role") == "user"] == [checkpoint]

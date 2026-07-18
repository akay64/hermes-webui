"""DB-first contract for destructive WebUI transcript mutations."""

from __future__ import annotations

import contextlib
import copy

import pytest

from api.models import Session
from api.session_db_bridge import WebUISessionDBBridgeError


def _session(tmp_path, sid="db-first"):
    messages = [
        {"role": "user", "content": "q1", "timestamp": 1.0},
        {"role": "assistant", "content": "a1", "timestamp": 2.0},
        {"role": "user", "content": "q2", "timestamp": 3.0},
        {"role": "assistant", "content": "a2", "timestamp": 4.0},
    ]
    return Session(
        session_id=sid,
        workspace=str(tmp_path),
        messages=copy.deepcopy(messages),
        context_messages=copy.deepcopy(messages),
    )


def _install_session(monkeypatch, session):
    import api.session_ops as session_ops

    monkeypatch.setattr(session_ops, "get_session", lambda _sid: session)
    monkeypatch.setattr(session_ops, "SESSIONS", {session.session_id: session})
    monkeypatch.setattr(
        session_ops,
        "_get_session_agent_lock",
        lambda _sid: contextlib.nullcontext(),
    )
    return session_ops


def test_retry_db_failure_does_not_mutate_or_save_sidecar(monkeypatch, tmp_path):
    import api.session_db_bridge as bridge

    session = _session(tmp_path, "retry-db-first")
    original_messages = copy.deepcopy(session.messages)
    original_context = copy.deepcopy(session.context_messages)
    saved = []
    session.save = lambda: saved.append(True)
    session_ops = _install_session(monkeypatch, session)

    def fail(*_args, **_kwargs):
        raise WebUISessionDBBridgeError("simulated DB failure")

    monkeypatch.setattr(bridge, "replace_webui_active_transcript", fail)

    with pytest.raises(WebUISessionDBBridgeError, match="simulated DB failure"):
        session_ops.retry_last(session.session_id)

    assert session.messages == original_messages
    assert session.context_messages == original_context
    assert saved == []


def test_undo_db_failure_does_not_mutate_or_save_sidecar(monkeypatch, tmp_path):
    import api.session_db_bridge as bridge

    session = _session(tmp_path, "undo-db-first")
    original_messages = copy.deepcopy(session.messages)
    original_context = copy.deepcopy(session.context_messages)
    saved = []
    session.save = lambda: saved.append(True)
    session_ops = _install_session(monkeypatch, session)

    def fail(*_args, **_kwargs):
        raise WebUISessionDBBridgeError("simulated DB failure")

    monkeypatch.setattr(bridge, "rewind_webui_active_transcript", fail)

    with pytest.raises(WebUISessionDBBridgeError, match="simulated DB failure"):
        session_ops.undo_last(session.session_id)

    assert session.messages == original_messages
    assert session.context_messages == original_context
    assert saved == []

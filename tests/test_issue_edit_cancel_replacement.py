"""Production-boundary regressions for cancel → edit and regeneration truncation."""

from __future__ import annotations

import json
import sqlite3
from io import BytesIO
from types import SimpleNamespace

import api.helpers as helpers


def _msg(role: str, content: str, ts: float, mid: str, **metadata) -> dict:
    return {"id": mid, "role": role, "content": content, "timestamp": ts, **metadata}


def _post_truncate(monkeypatch, routes, body):
    body_bytes = json.dumps(body).encode()
    captured = {}
    monkeypatch.setattr(routes, "_check_csrf", lambda handler: True)
    fake_j = lambda handler, payload, status=200, extra_headers=None: captured.update(
        payload=payload, status=status
    )
    monkeypatch.setattr(routes, "j", fake_j)
    monkeypatch.setattr(helpers, "j", fake_j)
    handler = SimpleNamespace(
        headers={"Content-Length": str(len(body_bytes))},
        rfile=BytesIO(body_bytes),
    )
    routes.handle_post(handler, SimpleNamespace(path="/api/session/truncate"))
    return captured


def test_cancelled_user_edit_replacement_survives_reload_and_state_reconciliation(
    monkeypatch, tmp_path
):
    import api.models as models
    import api.routes as routes
    from api.models import Session
    from api.session_db_bridge import replace_webui_active_transcript

    session_dir = tmp_path / "sessions"
    session_dir.mkdir(parents=True)
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    models.SESSIONS.clear()
    monkeypatch.setattr("api.config._evict_session_agent", lambda _sid: None)

    original = _msg(
        "user",
        "Okay great, hmm",
        3.0,
        "original-prompt",
        _active_turn_token="cancelled-stream:3",
    )
    cancellation = _msg(
        "assistant",
        "**Task cancelled:** Task cancelled.",
        4.0,
        "cancel-marker",
        _error=True,
        provider_details="Task cancelled.",
        provider_details_label="Cancellation details",
    )
    messages = [
        _msg("user", "Earlier", 1.0, "earlier-user"),
        _msg("assistant", "Earlier reply", 2.0, "earlier-reply"),
        original,
        cancellation,
    ]
    session = Session(
        session_id="cancel-edit-lifecycle",
        messages=messages,
        context_messages=list(messages),
    )
    session.save()
    replace_webui_active_transcript(session, messages)

    result = _post_truncate(
        monkeypatch,
        routes,
        {
            "session_id": session.session_id,
            "keep_count": 0,
            "target_message": {
                "id": original["id"],
                "_active_turn_token": original["_active_turn_token"],
                "role": original["role"],
                "timestamp": original["timestamp"],
                "content": original["content"],
            },
        },
    )
    assert result["status"] == 200

    truncated = Session.load(session.session_id)
    assert truncated is not None
    replacement = _msg(
        "user",
        "Okay great, hmm.. standby then. Let me do this.",
        5.0,
        "replacement-prompt",
    )
    truncated.messages.append(replacement)
    truncated.context_messages.append(replacement)
    replace_webui_active_transcript(truncated, truncated.context_messages)
    truncated.save()
    models.SESSIONS.pop(session.session_id, None)

    reloaded = Session.load(session.session_id)
    assert reloaded is not None
    assert [message["content"] for message in reloaded.messages] == [
        "Earlier",
        "Earlier reply",
        "Okay great, hmm.. standby then. Let me do this.",
    ]
    assert not any("Task cancelled" in message.get("content", "") for message in reloaded.messages)
    assert [message["content"] for message in reloaded.context_messages] == [
        "Earlier",
        "Earlier reply",
        "Okay great, hmm.. standby then. Let me do this.",
    ]

    with sqlite3.connect(models._active_state_db_path()) as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE session_id = ? AND active = 1 ORDER BY id",
            (session.session_id,),
        ).fetchall()
    assert [(row[0], row[1]) for row in rows] == [
        ("user", "Earlier"),
        ("assistant", "Earlier reply"),
        ("user", "Okay great, hmm.. standby then. Let me do this."),
    ]


def test_regeneration_selector_truncates_divergent_context_at_assistant(
    monkeypatch, tmp_path
):
    import api.models as models
    import api.routes as routes
    from api.models import Session
    from api.session_db_bridge import replace_webui_active_transcript

    session_dir = tmp_path / "sessions"
    session_dir.mkdir(parents=True)
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    models.SESSIONS.clear()
    monkeypatch.setattr("api.config._evict_session_agent", lambda _sid: None)

    display = [
        _msg("user", "first", 1.0, "u1"),
        _msg("assistant", "first reply", 2.0, "a1"),
        _msg("user", "second", 3.0, "u2"),
        _msg("assistant", "second reply", 4.0, "a2"),
    ]
    context = [_msg("user", "compaction-only", 0.5, "summary"), *display]
    session = Session(
        session_id="regeneration-divergent-context",
        messages=display,
        context_messages=context,
    )
    session.save()
    replace_webui_active_transcript(session, context)

    result = _post_truncate(
        monkeypatch,
        routes,
        {
            "session_id": session.session_id,
            "keep_count": 999,
            "target_message": {
                "id": "a1",
                "role": "assistant",
                "timestamp": 2.0,
                "content": "first reply",
            },
        },
    )
    assert result["status"] == 200

    models.SESSIONS.pop(session.session_id, None)
    reloaded = Session.load(session.session_id)
    assert reloaded is not None
    assert [message["content"] for message in reloaded.messages] == ["first"]
    assert [message["content"] for message in reloaded.context_messages] == [
        "compaction-only",
        "first",
    ]
    assert all(
        message.get("content") not in {"first reply", "second", "second reply"}
        for message in reloaded.context_messages
    )

"""Tests for the compressed-context viewer (context_summary).

Covers:
- Task 1: ``is_context_compression_marker()`` recognition of flagged
  ``[PRIOR CONTEXT ...]`` merge-into-tail messages (and rejection of
  unflagged lookalike user text).
- Task 2: ``has_compressed_context`` in ``Session.compact()`` (context
  messages first, display-messages fallback).
- Task 3: ``_find_last_compression_marker`` last-wins / fallback semantics.
- Route boundary: ``GET /api/session/context_summary`` response shape,
  400 for missing sid, 404 for unknown session (review finding: unknown
  session must not masquerade as a marker-free session), and redaction
  wiring that respects the global ``api_redact_enabled`` setting.
"""
from urllib.parse import urlparse

import pytest

import api.models as models
from api.compression_anchor import is_context_compression_marker
from api.models import SESSIONS, Session

COMPACTION_MARKER = (
    "[CONTEXT COMPACTION — REFERENCE ONLY]\n"
    "## Historical Task Snapshot\n"
    "Some summary text here.\n"
)
PRIOR_CONTEXT_MARKER = (
    "[PRIOR CONTEXT — for reference only; not a new message]\n"
    "Basically the original tail message text.\n"
)
TASK_LIST_MARKER = (
    "[Your active task list was preserved across context compression]\n"
    "- [>] preserve the current task"
)
UNBRACKETED_COMPACTION_MARKER = (
    "Context compaction — legacy flagged summary\n"
    "Older compressed context."
)
SESSION_ARC_MARKER = "[SESSION ARC SUMMARY] earlier compressed context"
MERGED_COMPRESSION_ENVELOPE = (
    "[PRIOR CONTEXT — for reference only; not a new message]\n"
    "Preserved tail text.\n\n"
    "[END OF PRIOR CONTEXT — COMPACTION SUMMARY BELOW]\n\n"
    + COMPACTION_MARKER
)


@pytest.fixture(autouse=True)
def _isolate_sessions(tmp_path, monkeypatch):
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    SESSIONS.clear()
    yield session_dir
    SESSIONS.clear()


@pytest.fixture
def marker_session(tmp_path):
    s = Session(
        session_id="ctx_marker",
        workspace=str(tmp_path),
        messages=[{"role": "user", "content": "hi"}],
        context_messages=[
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": COMPACTION_MARKER},
        ],
    )
    s.save()
    return s


# ── Task 1: detector ─────────────────────────────────────────────────────────


def test_detector_recognizes_flagged_prior_context():
    message = {
        "role": "assistant",
        "content": PRIOR_CONTEXT_MARKER,
        "_compressed_summary": True,
    }
    assert is_context_compression_marker(message) is True


def test_detector_rejects_unflagged_prior_context():
    """The agent-stamped flag is required — ordinary text can't match."""
    message = {"role": "assistant", "content": PRIOR_CONTEXT_MARKER}
    assert is_context_compression_marker(message) is False


def test_detector_rejects_plain_user_turn_starting_with_prior_context():
    message = {"role": "user", "content": "[prior context question?] what do you think"}
    assert is_context_compression_marker(message) is False


def test_detector_still_matches_canonical_markers():
    assert (
        is_context_compression_marker(
            {"role": "assistant", "content": COMPACTION_MARKER}
        )
        is True
    )
    assert (
        is_context_compression_marker(
            {
                "role": "user",
                "content": "[your active task list was preserved across context compression]",
            }
        )
        is True
    )
    assert (
        is_context_compression_marker(
            {"role": "assistant", "content": "[session arc summary] early"}
        )
        is True
    )


# ── Task 2: compact() flag ───────────────────────────────────────────────────


def test_compact_flag_true_when_marker_in_context_messages(marker_session):
    assert marker_session.compact()["has_compressed_context"] is True


def test_compact_flag_false_when_no_marker(tmp_path):
    s = Session(
        session_id="ctx_plain",
        workspace=str(tmp_path),
        messages=[{"role": "user", "content": "hi"}],
    )
    s.save()
    assert s.compact()["has_compressed_context"] is False


def test_compact_flag_true_when_marker_only_in_display_messages(tmp_path):
    """Merge-into-tail summaries can survive only in the display projection."""
    s = Session(
        session_id="ctx_disp_only",
        workspace=str(tmp_path),
        messages=[
            {"role": "assistant", "content": PRIOR_CONTEXT_MARKER, "_compressed_summary": True}
        ],
        context_messages=[{"role": "user", "content": "hi"}],
    )
    s.save()
    assert s.compact()["has_compressed_context"] is True


# ── Task 3: last-marker scan ─────────────────────────────────────────────────


def test_last_marker_wins_and_context_preferred(tmp_path):
    from api.routes import _find_last_compression_marker

    s = Session(
        session_id="ctx_multi",
        workspace=str(tmp_path),
        messages=[{"role": "assistant", "content": "tail"}],
        context_messages=[
            {"role": "assistant", "content": "[session arc summary] early"},
            {"role": "assistant", "content": COMPACTION_MARKER + "latest"},
        ],
    )
    marker = _find_last_compression_marker(s)
    assert marker is not None
    assert marker["content"].endswith("latest")


def test_find_marker_falls_back_to_display_messages(tmp_path):
    from api.routes import _find_last_compression_marker

    s = Session(
        session_id="ctx_fb",
        workspace=str(tmp_path),
        messages=[
            {"role": "assistant", "content": PRIOR_CONTEXT_MARKER, "_compressed_summary": True}
        ],
        context_messages=[{"role": "user", "content": "hi"}],
    )
    marker = _find_last_compression_marker(s)
    assert marker is not None
    assert marker["content"].startswith("[PRIOR CONTEXT")


def test_find_marker_prefers_substantive_markers_over_later_task_card(tmp_path):
    from api.routes import _find_last_compression_marker

    cases = [
        (
            {"role": "assistant", "content": UNBRACKETED_COMPACTION_MARKER, "_compressed_summary": True},
            "flagged unbracketed compaction",
        ),
        (
            {"role": "assistant", "content": COMPACTION_MARKER},
            "canonical compaction",
        ),
        (
            {"role": "assistant", "content": PRIOR_CONTEXT_MARKER, "_compressed_summary": True},
            "flagged prior context",
        ),
        (
            {"role": "assistant", "content": SESSION_ARC_MARKER},
            "session arc summary",
        ),
        (
            {
                "role": "assistant",
                "content": [
                    {"type": "input_text", "text": MERGED_COMPRESSION_ENVELOPE},
                ],
            },
            "unflagged merged envelope",
        ),
    ]

    for substantive, label in cases:
        task_card = {"role": "user", "content": TASK_LIST_MARKER}
        session = Session(
            session_id=f"ctx_rank_{label.replace(' ', '_')}",
            workspace=str(tmp_path),
            context_messages=[substantive, task_card],
        )
        marker = _find_last_compression_marker(session)
        assert marker is substantive, label


def test_find_marker_uses_display_substantive_before_context_task_fallback(tmp_path):
    from api.routes import _find_last_compression_marker

    substantive = {"role": "assistant", "content": MERGED_COMPRESSION_ENVELOPE}
    session = Session(
        session_id="ctx_display_substantive",
        workspace=str(tmp_path),
        context_messages=[{"role": "user", "content": TASK_LIST_MARKER}],
        messages=[substantive],
    )

    assert _find_last_compression_marker(session) is substantive


def test_unflagged_merged_envelope_requires_all_ordered_delimiters(tmp_path):
    from api.routes import _find_last_compression_marker

    incomplete_envelope = (
        "[PRIOR CONTEXT — for reference only; not a new message]\n"
        "[END OF PRIOR CONTEXT — COMPACTION SUMMARY BELOW]\n"
        "ordinary preserved text without a compaction summary"
    )
    task_card = {"role": "user", "content": TASK_LIST_MARKER}
    session = Session(
        session_id="ctx_incomplete_envelope",
        workspace=str(tmp_path),
        context_messages=[
            {"role": "assistant", "content": incomplete_envelope},
            task_card,
        ],
    )

    assert _find_last_compression_marker(session) is task_card


def test_task_list_marker_is_generic_fallback_when_no_substantive_summary(tmp_path):
    from api.routes import _find_last_compression_marker

    task_card = {"role": "user", "content": TASK_LIST_MARKER}
    session = Session(
        session_id="ctx_task_only",
        workspace=str(tmp_path),
        context_messages=[task_card],
    )

    assert _find_last_compression_marker(session) is task_card


# ── Two-phase load: metadata-only sessions (review finding) ──────────────────


def _write_sidecar(session_dir, sid, *, anchor_summary, context_messages=None):
    """Write a modern-layout sidecar: metadata fields (incl. the anchor
    summary) BEFORE the message arrays, so load_metadata_only() reads them
    via the cheap prefix."""
    import json

    payload = {
        "session_id": sid,
        "title": "meta test",
        "created_at": 100.0,
        "updated_at": 200.0,
        "workspace": str(session_dir),
        "compression_anchor_summary": anchor_summary,
        "message_count": 1,
        "messages": [{"role": "user", "content": "hi"}],
    }
    if context_messages is not None:
        payload["context_messages"] = context_messages
    (session_dir / f"{sid}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_metadata_only_compact_reports_compressed_from_anchor_summary(_isolate_sessions):
    """Regression: GET /api/session?messages=0 loads metadata-only, where the
    message arrays are empty and the marker scan cannot run. The persisted
    compression_anchor_summary (metadata prefix) is the authoritative signal."""
    _write_sidecar(
        _isolate_sessions,
        "meta_compressed",
        anchor_summary="[CONTEXT COMPACTION — REFERENCE ONLY]\nsome summary",
    )
    s = Session.load_metadata_only("meta_compressed")
    assert s is not None
    assert s.messages == []  # cheap prefix load — arrays are skipped
    assert s.compact()["has_compressed_context"] is True


def test_metadata_only_compact_reports_false_without_anchor_summary(_isolate_sessions):
    _write_sidecar(_isolate_sessions, "meta_plain", anchor_summary=None)
    s = Session.load_metadata_only("meta_plain")
    assert s is not None
    assert s.compact()["has_compressed_context"] is False


def test_metadata_only_heuristic_does_not_leak_into_full_loads(tmp_path):
    """A full-loaded session whose anchor summary is absent but whose context
    messages DO contain a marker must still report true (scan path wins)."""
    s = Session(
        session_id="ctx_full_scan",
        workspace=str(tmp_path),
        context_messages=[
            {"role": "assistant", "content": COMPACTION_MARKER}
        ],
        compression_anchor_summary=None,
    )
    assert s.compact()["has_compressed_context"] is True


# ── Route boundary ───────────────────────────────────────────────────────────


def _capture_j(monkeypatch):
    import api.routes as routes

    captured = {}

    def fake_j(handler, payload, status=200, extra_headers=None):
        captured.update(payload=payload, status=status)
        return True

    monkeypatch.setattr(routes, "j", fake_j)
    return captured


def _capture_bad(monkeypatch):
    import api.routes as routes

    captured = {}

    def fake_bad(handler, message, status=400):
        captured.update(message=message, status=status)
        return True

    monkeypatch.setattr(routes, "bad", fake_bad)
    return captured


def test_route_returns_compressed_marker(marker_session, monkeypatch):
    import api.routes as routes

    captured = _capture_j(monkeypatch)
    handled = routes.handle_get(
        object(),
        urlparse("/api/session/context_summary?session_id=ctx_marker"),
    )

    assert handled is True
    assert captured["status"] == 200
    payload = captured["payload"]
    assert payload["found"] is True
    assert payload["role"] == "assistant"
    assert payload["content"].startswith("[CONTEXT COMPACTION")
    assert payload["text"].startswith("[CONTEXT COMPACTION")


def test_route_returns_merged_envelope_before_task_card(tmp_path, monkeypatch):
    import api.routes as routes

    Session(
        session_id="ctx_route_merged",
        workspace=str(tmp_path),
        messages=[{"role": "user", "content": TASK_LIST_MARKER}],
        context_messages=[
            {"role": "assistant", "content": MERGED_COMPRESSION_ENVELOPE},
            {"role": "user", "content": TASK_LIST_MARKER},
        ],
    ).save()
    captured = _capture_j(monkeypatch)

    handled = routes.handle_get(
        object(),
        urlparse("/api/session/context_summary?session_id=ctx_route_merged"),
    )

    assert handled is True
    assert captured["status"] == 200
    payload = captured["payload"]
    assert payload["found"] is True
    assert payload["role"] == "assistant"
    assert payload["content"].startswith("[PRIOR CONTEXT")
    assert payload["content"] != TASK_LIST_MARKER
    assert payload["text"].strip() == payload["content"].strip()


def test_route_marker_free_session_returns_found_false(tmp_path, monkeypatch):
    import api.routes as routes

    Session(
        session_id="ctx_plain",
        workspace=str(tmp_path),
        messages=[{"role": "user", "content": "hi"}],
    ).save()
    captured = _capture_j(monkeypatch)

    handled = routes.handle_get(
        object(),
        urlparse("/api/session/context_summary?session_id=ctx_plain"),
    )

    assert handled is True
    assert captured["status"] == 200
    assert captured["payload"] == {
        "found": False,
        "role": None,
        "content": None,
        "text": None,
    }


def test_route_missing_sid_returns_400(monkeypatch):
    import api.routes as routes

    captured = _capture_bad(monkeypatch)
    handled = routes.handle_get(object(), urlparse("/api/session/context_summary"))

    assert handled is True
    assert captured["status"] == 400


def test_route_unknown_session_returns_404(monkeypatch):
    """Review finding: unknown session must 404, not masquerade as a
    marker-free session (``found:false`` is for existing sessions only)."""
    import api.routes as routes

    captured = _capture_bad(monkeypatch)
    handled = routes.handle_get(
        object(),
        urlparse("/api/session/context_summary?session_id=does_not_exist"),
    )

    assert handled is True
    assert captured["status"] == 404
    assert captured["message"] == "Session not found"


def test_route_redaction_respects_global_setting(tmp_path, monkeypatch):
    import api.config as config
    import api.routes as routes

    secret = "sk-test-secret-value-123456"
    Session(
        session_id="ctx_redact",
        workspace=str(tmp_path),
        context_messages=[
            {"role": "assistant", "content": COMPACTION_MARKER + "\n" + secret}
        ],
    ).save()
    captured = _capture_j(monkeypatch)

    # Setting on: both content and text must be redacted.
    monkeypatch.setattr(config, "load_settings", lambda: {"api_redact_enabled": True})
    routes.handle_get(
        object(),
        urlparse("/api/session/context_summary?session_id=ctx_redact"),
    )
    assert secret not in captured["payload"]["content"]
    assert secret not in captured["payload"]["text"]

    # Setting off: the raw marker is the truth the agent sees — no redaction.
    monkeypatch.setattr(config, "load_settings", lambda: {"api_redact_enabled": False})
    routes.handle_get(
        object(),
        urlparse("/api/session/context_summary?session_id=ctx_redact"),
    )
    assert secret in captured["payload"]["content"]
    assert secret in captured["payload"]["text"]

"""Session prompt-stash lifecycle and UI contract coverage."""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _request(base, path, *, method="GET", body=None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _new_session(base):
    status, data = _request(base, "/api/session/new", method="POST", body={})
    assert status == 200, data
    return data["session"]["session_id"]


def _delete_session(base, sid):
    _request(base, "/api/session/delete", method="POST", body={"session_id": sid})


def test_prompt_stash_is_session_scoped_atomic_and_consumable(base_url):
    first = _new_session(base_url)
    second = _new_session(base_url)
    try:
        status, stashed = _request(
            base_url,
            "/api/session/prompt-stash",
            method="POST",
            body={"session_id": first, "action": "stash", "text": "unfinished train of thought"},
        )
        assert status == 200, stashed
        assert stashed["draft"]["text"] == ""
        assert [item["text"] for item in stashed["stash"]] == ["unfinished train of thought"]
        item_id = stashed["stash"][0]["id"]

        status, first_stash = _request(
            base_url,
            "/api/session/prompt-stash?session_id=" + urllib.parse.quote(first),
        )
        assert status == 200
        assert [item["id"] for item in first_stash["stash"]] == [item_id]

        status, second_stash = _request(
            base_url,
            "/api/session/prompt-stash?session_id=" + urllib.parse.quote(second),
        )
        assert status == 200
        assert second_stash["stash"] == []

        status, restored = _request(
            base_url,
            "/api/session/prompt-stash",
            method="POST",
            body={
                "session_id": first,
                "action": "restore",
                "id": item_id,
                "current_text": "urgent interruption",
            },
        )
        assert status == 200, restored
        assert restored["draft"]["text"] == "urgent interruption\n\nunfinished train of thought"
        assert restored["stash"] == []

        status, session_data = _request(
            base_url,
            "/api/session?session_id=" + urllib.parse.quote(first) + "&messages=0&resolve_model=0",
        )
        assert status == 200
        assert session_data["session"]["composer_draft"]["text"] == restored["draft"]["text"]
        assert session_data["session"]["prompt_stash"] == []
    finally:
        _delete_session(base_url, first)
        _delete_session(base_url, second)


def test_prompt_stash_clear_all_and_attachment_guard(base_url):
    sid = _new_session(base_url)
    try:
        for text in ("first", "second"):
            status, data = _request(
                base_url,
                "/api/session/prompt-stash",
                method="POST",
                body={"session_id": sid, "action": "stash", "text": text},
            )
            assert status == 200, data

        status, cleared = _request(
            base_url,
            "/api/session/prompt-stash",
            method="DELETE",
            body={"session_id": sid, "all": True},
        )
        assert status == 200, cleared
        assert cleared["stash"] == []

        status, _ = _request(
            base_url,
            "/api/session/draft",
            method="POST",
            body={"session_id": sid, "text": "with a file", "files": [{"name": "note.txt"}]},
        )
        assert status == 200
        status, blocked = _request(
            base_url,
            "/api/session/prompt-stash",
            method="POST",
            body={"session_id": sid, "action": "stash", "text": "with a file"},
        )
        assert status == 409
        assert "attachments" in blocked["error"].lower()
    finally:
        _delete_session(base_url, sid)


def test_stale_unsaved_shell_with_prompt_stash_stays_resident():
    from api.models import Session, _UNSAVED_SHELL_GRACE_S, _session_is_evictable

    shell = Session(session_id="stash-test-" + uuid.uuid4().hex[:12])
    shell.created_at = time.time() - (_UNSAVED_SHELL_GRACE_S + 60)
    shell.prompt_stash = [{"id": "one", "text": "keep me", "label": "keep me", "created_at": 1}]
    assert _session_is_evictable(shell) is False


def test_prompt_stash_round_trips_without_bloating_metadata_prefix(tmp_path, monkeypatch):
    import api.models as models

    monkeypatch.setattr(models, "SESSION_DIR", tmp_path)
    session = models.Session(session_id="stash-prefix-test")
    session.prompt_stash = [
        {"id": f"item-{idx}", "text": str(idx) + ("x" * 11_999), "label": f"item {idx}", "created_at": idx}
        for idx in range(20)
    ]
    session.save(touch_updated_at=False, skip_index=True)

    prefix = models._read_metadata_json_prefix(session.path)
    assert prefix is not None, "stash must remain after messages so the 64 KB metadata read stays bounded"
    assert "prompt_stash" not in json.loads(prefix)
    loaded = models.Session.load(session.session_id)
    assert loaded.prompt_stash == session.prompt_stash


def test_prompt_stash_does_not_copy_or_touch_transcript_rendering():
    routes = (ROOT / "api" / "routes.py").read_text(encoding="utf-8")
    messages = (ROOT / "static" / "messages.js").read_text(encoding="utf-8")
    sessions = (ROOT / "static" / "sessions.js").read_text(encoding="utf-8")
    duplicate = routes.split('if parsed.path == "/api/session/duplicate":', 1)[1].split(
        'if parsed.path == "/api/default-model":', 1
    )[0]
    branch = routes.split('if parsed.path == "/api/session/branch":', 1)[1].split(
        'if parsed.path == "/api/session/compress/start":', 1
    )[0]
    assert "prompt_stash=" not in duplicate
    assert "prompt_stash=" not in branch

    feature_start = messages.index("function _promptStashSessionId()")
    feature_end = messages.index("const _PENDING_CONTEXT_HIGHLIGHT_NAME", feature_start)
    feature = messages[feature_start:feature_end]
    assert "renderMessages(" not in feature
    assert "MutationObserver" not in feature
    assert "requestAnimationFrame" not in feature
    assert "_hasPendingSelections" in feature
    assert "S.pendingFiles" in feature
    assert "showConfirmDialog" in feature
    accept_start = sessions.index("function _acceptPromptStashComposerDraft(")
    accept_end = sessions.index("// Immediate save used before session switches.", accept_start)
    accept = sessions[accept_start:accept_end]
    assert accept.index("clearTimeout(_draftSaveTimer)") < accept.index("_rememberComposerDraftPayloadState")


def test_prompt_stash_limits_and_metadata_wiring_present():
    routes = (ROOT / "api" / "routes.py").read_text(encoding="utf-8")
    models = (ROOT / "api" / "models.py").read_text(encoding="utf-8")
    assert "_PROMPT_STASH_MAX_ITEMS = 20" in routes
    assert "_PROMPT_STASH_MAX_TEXT = 50_000" in routes
    assert "_PROMPT_STASH_MAX_TOTAL_TEXT = 250_000" in routes
    assert "session.save(touch_updated_at=False, skip_index=True)" in routes
    assert "'prompt_stash': self.prompt_stash" in models


def test_prompt_popup_tab_switch_stops_click_before_replacing_target():
    messages = (ROOT / "static" / "messages.js").read_text(encoding="utf-8")
    header_start = messages.index("function _promptPopupHeader(")
    header_end = messages.index("function _wirePromptRowActivation(", header_start)
    header = messages[header_start:header_end]
    handler_start = header.index("button.onclick=(event)=>{")
    handler_end = header.index("};", handler_start)
    handler = header[handler_start:handler_end]

    # _renderPromptsPopup replaces the clicked tab. If its click reaches the
    # document outside-click handler afterward, the detached target no longer
    # belongs to the popup and the popup closes.
    assert handler.index("event.stopPropagation()") < handler.index("_renderPromptsPopup(popup)")


def test_prompt_popup_has_stable_four_row_desktop_footprint():
    styles = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    popup_start = styles.index(".saved-prompts-popup{position:absolute")
    popup_end = styles.index("}", popup_start)
    popup = styles[popup_start:popup_end]
    body_start = styles.index(".prompts-popup-body{")
    body_end = styles.index("}", body_start)
    body = styles[body_start:body_end]

    assert "box-sizing:border-box" in popup
    assert "width:380px" in popup
    assert "height:320px" in popup
    assert "max-width:calc(100vw - 24px)" in popup
    assert "max-height:calc(100dvh - 24px)" in popup
    assert "flex:1 1 auto" in body

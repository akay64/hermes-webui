"""Focused durability tests for WebUI-created branch and duplicate sessions."""

from __future__ import annotations

import copy
import json
import sys
import threading
import types

import pytest


class _StateStore:
    def __init__(self):
        self.sessions = {}
        self.messages = {}
        self.archived = set()
        self.titles = {}
        self.fail_replace = False
        self.replace_active_only = []
        self.rewind_count = {}
        self.reconcile_calls = []


class _FakeSessionDB:
    def __init__(self, *, store, db_path=None):
        self.store = store
        self.db_path = db_path
        self.closed = False

    def get_session(self, session_id):
        row = self.store.sessions.get(session_id)
        return copy.deepcopy(row) if row is not None else None

    def create_session(self, session_id, source, **kwargs):
        parent = kwargs.get("parent_session_id")
        if parent and parent not in self.store.sessions:
            raise RuntimeError(f"missing parent {parent}")
        if session_id in self.store.sessions:
            raise RuntimeError(f"session exists: {session_id}")
        self.store.sessions[session_id] = {
            "id": session_id,
            "source": source,
            "parent_session_id": parent,
            "model": kwargs.get("model"),
        }
        self.store.messages.setdefault(session_id, [])

    def get_messages(self, session_id):
        return copy.deepcopy(self.store.messages.get(session_id, []))

    def has_archived_messages(self, session_id):
        return session_id in self.store.archived

    def replace_messages(self, session_id, messages, active_only=False):
        if self.store.fail_replace:
            raise RuntimeError("simulated replace failure")
        self.store.messages[session_id] = copy.deepcopy(list(messages))
        self.store.replace_active_only.append(bool(active_only))

    def rewind_to_message(self, session_id, target_message_id):
        messages = self.store.messages.get(session_id, [])
        target_idx = next(
            idx for idx, message in enumerate(messages)
            if message.get("id") == target_message_id
        )
        removed = messages[target_idx:]
        self.store.messages[session_id] = copy.deepcopy(messages[:target_idx])
        self.store.rewind_count[session_id] = self.store.rewind_count.get(session_id, 0) + 1
        return {"rewound_count": len(removed)}

    def reconcile_active_transcript_for_rewind(self, session_id, messages):
        previous = self.store.messages.get(session_id, [])
        replacement = copy.deepcopy(list(messages))
        self.store.messages[session_id] = replacement
        self.store.reconcile_calls.append((session_id, replacement))
        self.store.rewind_count[session_id] = self.store.rewind_count.get(session_id, 0) + 1
        return {"rewound_count": len(previous), "inserted_count": len(replacement)}

    def set_session_title(self, session_id, title):
        self.store.titles[session_id] = title

    def delete_session(self, session_id, sessions_dir=None):
        existed = session_id in self.store.sessions
        self.store.sessions.pop(session_id, None)
        self.store.messages.pop(session_id, None)
        self.store.titles.pop(session_id, None)
        self.store.archived.discard(session_id)
        return existed

    def close(self):
        self.closed = True


@pytest.fixture
def bridge_env(monkeypatch, tmp_path):
    from api import config, models

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    old = {
        "config_session_dir": config.SESSION_DIR,
        "models_session_dir": models.SESSION_DIR,
        "config_index": config.SESSION_INDEX_FILE,
        "models_index": models.SESSION_INDEX_FILE,
        "config_sessions": config.SESSIONS,
        "config_lock": config.LOCK,
    }
    config.SESSION_DIR = sessions_dir
    models.SESSION_DIR = sessions_dir
    config.SESSION_INDEX_FILE = sessions_dir / "index.json"
    models.SESSION_INDEX_FILE = sessions_dir / "index.json"
    config.SESSIONS = {}
    config.LOCK = threading.Lock()

    store = _StateStore()
    fake_module = types.SimpleNamespace(
        SessionDB=lambda db_path=None: _FakeSessionDB(store=store, db_path=db_path)
    )
    monkeypatch.setitem(sys.modules, "hermes_state", fake_module)
    monkeypatch.setattr(models, "_active_state_db_path", lambda: tmp_path / "state.db")

    try:
        yield store, sessions_dir
    finally:
        config.SESSION_DIR = old["config_session_dir"]
        models.SESSION_DIR = old["models_session_dir"]
        config.SESSION_INDEX_FILE = old["config_index"]
        models.SESSION_INDEX_FILE = old["models_index"]
        config.SESSIONS = old["config_sessions"]
        config.LOCK = old["config_lock"]


def _messages(prefix, count):
    return [
        {
            "role": "user" if idx % 2 == 0 else "assistant",
            "content": f"{prefix}-{idx}",
            "timestamp": float(idx + 1),
        }
        for idx in range(count)
    ]


def _session(session_id, messages, context_messages=None, *, parent=None, source="webui"):
    from api.models import Session

    return Session(
        session_id=session_id,
        title=session_id,
        workspace="/tmp/workspace",
        model="test-model",
        messages=copy.deepcopy(messages),
        context_messages=copy.deepcopy(context_messages if context_messages is not None else messages),
        parent_session_id=parent,
        session_source=source,
    )


def test_branch_materializes_missing_parent_and_seeds_only_active_context(bridge_env):
    store, sessions_dir = bridge_env
    from api.session_db_bridge import persist_webui_child_session

    display = _messages("display", 6)
    context = _messages("context", 4)
    source = _session("source", display, context)
    child = _session("branch", display[:4], context[:3], parent=source.session_id, source="fork")

    persist_webui_child_session(
        child,
        child.context_messages,
        source_session=source,
        parent_session_id=source.session_id,
        source_context=source.context_messages,
    )

    assert store.sessions[source.session_id]["parent_session_id"] is None
    assert store.sessions[child.session_id]["parent_session_id"] == source.session_id
    assert store.messages[source.session_id] == context
    assert store.messages[child.session_id] == context[:3]
    assert child.path.exists()
    payload = json.loads(child.path.read_text(encoding="utf-8"))
    assert payload["messages"] == display[:4]
    assert payload["context_messages"] == context[:3]
    assert sessions_dir.joinpath("index.json").exists()


def test_duplicate_is_parentless_and_does_not_materialize_source(bridge_env):
    store, _ = bridge_env
    from api.session_db_bridge import persist_webui_child_session

    display = _messages("display", 5)
    context = _messages("compressed", 3)
    duplicate = _session("duplicate", display, context)

    persist_webui_child_session(duplicate, duplicate.context_messages)

    assert "source" not in store.sessions
    assert store.sessions[duplicate.session_id]["parent_session_id"] is None
    assert store.messages[duplicate.session_id] == context


def test_compressed_branch_does_not_copy_parent_archived_rows(bridge_env):
    store, _ = bridge_env
    from api.session_db_bridge import persist_webui_child_session

    source = _session("compressed-source", _messages("display", 10), _messages("active", 4))
    store.sessions[source.session_id] = {
        "id": source.session_id,
        "source": "webui",
        "parent_session_id": None,
    }
    store.messages[source.session_id] = copy.deepcopy(source.context_messages)
    store.archived.add(source.session_id)
    child = _session(
        "compressed-branch",
        source.messages,
        source.context_messages,
        parent=source.session_id,
        source="fork",
    )

    persist_webui_child_session(
        child,
        child.context_messages,
        source_session=source,
        parent_session_id=source.session_id,
        source_context=source.context_messages,
    )

    assert store.messages[child.session_id] == source.context_messages
    assert child.session_id not in store.archived
    assert source.session_id in store.archived


def test_empty_branch_is_persisted_as_durable_child(bridge_env):
    store, _ = bridge_env
    from api.session_db_bridge import persist_webui_child_session

    source = _session("empty-source", _messages("display", 2), _messages("context", 2))
    child = _session(
        "empty-branch",
        [],
        [],
        parent=source.session_id,
        source="fork",
    )

    persist_webui_child_session(
        child,
        [],
        source_session=source,
        parent_session_id=source.session_id,
        source_context=source.context_messages,
    )

    assert store.sessions[child.session_id]["parent_session_id"] == source.session_id
    assert store.messages[child.session_id] == []
    assert child.path.exists()
    payload = json.loads(child.path.read_text(encoding="utf-8"))
    assert payload["messages"] == []
    assert payload["context_messages"] == []


def test_active_replacement_uses_active_only_and_preserves_sidecar(bridge_env):
    store, _ = bridge_env
    from api.session_db_bridge import replace_webui_active_transcript

    session = _session("replace-source", _messages("display", 4), _messages("context", 4))
    store.sessions[session.session_id] = {"id": session.session_id, "source": "webui"}
    store.messages[session.session_id] = copy.deepcopy(session.context_messages)
    replacement = session.context_messages[:2]

    result = replace_webui_active_transcript(session, replacement)

    assert result == {"active_count": 2}
    assert store.messages[session.session_id] == replacement
    assert store.replace_active_only[-1] is True
    assert not session.path.exists(), "DB bridge must not publish the sidecar"


def test_clean_rewind_uses_target_message_boundary(bridge_env):
    store, _ = bridge_env
    from api.session_db_bridge import rewind_webui_active_transcript

    before = _messages("turn", 4)
    for idx, message in enumerate(before, start=10):
        message["id"] = idx
    session = _session("rewind-clean", before, before)
    store.sessions[session.session_id] = {"id": session.session_id, "source": "webui"}
    store.messages[session.session_id] = copy.deepcopy(before)

    result = rewind_webui_active_transcript(session, before, before[:2])

    assert result == {"mode": "targeted", "rewound_count": 2}
    assert store.messages[session.session_id] == before[:2]
    assert store.rewind_count[session.session_id] == 1
    assert store.reconcile_calls == []


def test_diverged_rewind_reconciles_exact_active_transcript(bridge_env):
    store, _ = bridge_env
    from api.session_db_bridge import rewind_webui_active_transcript

    before = _messages("sidecar", 4)
    session = _session("rewind-diverged", before, before)
    store.sessions[session.session_id] = {"id": session.session_id, "source": "webui"}
    store.messages[session.session_id] = _messages("ghost", 7)

    result = rewind_webui_active_transcript(session, before, before[:2])

    assert result == {"mode": "reconciled", "rewound_count": 7}
    assert store.messages[session.session_id] == before[:2]
    assert store.reconcile_calls == [(session.session_id, before[:2])]
    assert store.rewind_count[session.session_id] == 1


def test_database_seed_failure_rolls_back_child_without_touching_existing_parent(bridge_env):
    store, _ = bridge_env
    from api.session_db_bridge import WebUISessionDBBridgeError, persist_webui_child_session

    source = _session("db-failure-source", _messages("display", 2), _messages("context", 2))
    store.sessions[source.session_id] = {
        "id": source.session_id,
        "source": "webui",
        "parent_session_id": None,
    }
    store.messages[source.session_id] = copy.deepcopy(source.context_messages)
    child = _session(
        "db-failure-child",
        source.messages,
        source.context_messages,
        parent=source.session_id,
        source="fork",
    )
    store.fail_replace = True

    with pytest.raises(WebUISessionDBBridgeError, match="durably create child"):
        persist_webui_child_session(
            child,
            child.context_messages,
            source_session=source,
            parent_session_id=source.session_id,
            source_context=source.context_messages,
        )

    assert source.session_id in store.sessions
    assert child.session_id not in store.sessions
    assert not child.path.exists()


def test_archived_only_parent_fails_closed_without_child_sidecar(bridge_env):
    store, _ = bridge_env
    from api.session_db_bridge import WebUISessionDBBridgeError, persist_webui_child_session

    source = _session("archived-only", _messages("display", 4), _messages("context", 2))
    store.sessions[source.session_id] = {"id": source.session_id, "source": "webui"}
    store.archived.add(source.session_id)
    child = _session(
        "refused-branch",
        source.messages,
        source.context_messages,
        parent=source.session_id,
        source="fork",
    )

    with pytest.raises(WebUISessionDBBridgeError, match="archived history"):
        persist_webui_child_session(
            child,
            child.context_messages,
            source_session=source,
            parent_session_id=source.session_id,
            source_context=source.context_messages,
        )

    assert child.session_id not in store.sessions
    assert not child.path.exists()


def test_sidecar_failure_rolls_back_child_and_materialized_parent(bridge_env):
    store, _ = bridge_env
    from api.session_db_bridge import WebUISessionDBBridgeError, persist_webui_child_session

    source = _session("rollback-source", _messages("display", 3), _messages("context", 3))
    child = _session(
        "rollback-child",
        source.messages,
        source.context_messages,
        parent=source.session_id,
        source="fork",
    )

    def fail_save():
        raise OSError("simulated sidecar failure")

    child.save = fail_save
    with pytest.raises(WebUISessionDBBridgeError, match="durably create child"):
        persist_webui_child_session(
            child,
            child.context_messages,
            source_session=source,
            parent_session_id=source.session_id,
            source_context=source.context_messages,
        )

    assert source.session_id not in store.sessions
    assert child.session_id not in store.sessions
    assert not child.path.exists()

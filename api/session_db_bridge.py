"""Durable state.db lifecycle helpers for WebUI-created child sessions.

WebUI sidecars carry both the display transcript and the model-facing
``context_messages`` list.  A new WebUI branch or duplicate must mirror the
model-facing list into Hermes Agent's SessionDB before the sidecar is
published; otherwise the first Agent turn treats the inherited context as
already present and only persists the new turn.

This module deliberately owns only the local, in-process WebUI bridge.  A
remote Gateway has a different state authority and is rejected by the route
layer rather than being silently written to a local state.db.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)


class WebUISessionDBBridgeError(RuntimeError):
    """Raised when a WebUI child cannot be durably materialized."""


def _ensure_agent_import_path() -> None:
    """Restore the discovered Agent source path before a lazy import.

    WebUI discovers the Agent checkout during configuration loading, but some
    optional integrations temporarily alter ``sys.path`` while probing for
    Agent modules.  Child-session persistence is a later lazy import, so use
    the already-discovered trusted path when it is available instead of
    turning an unrelated path mutation into a false database-unavailable
    response.
    """
    try:
        from api import config as api_config

        agent_dir = getattr(api_config, "_AGENT_DIR", None)
    except Exception:
        agent_dir = None
    if agent_dir:
        agent_path = str(Path(agent_dir).expanduser().resolve())
        if agent_path not in sys.path:
            sys.path.append(agent_path)


def _profile_state_db_path(profile: str | None) -> Path:
    from api.models import _active_state_db_path, _get_profile_home

    if isinstance(profile, str) and profile:
        return _get_profile_home(profile) / "state.db"
    return _active_state_db_path()


def _open_session_db(profile: str | None):
    try:
        _ensure_agent_import_path()
        from hermes_state import SessionDB

        return SessionDB(db_path=_profile_state_db_path(profile))
    except Exception as exc:
        raise WebUISessionDBBridgeError(
            "Session database unavailable; the child session was not created."
        ) from exc


def _session_metadata(session: Any) -> dict[str, Any]:
    raw_source = str(
        getattr(session, "source_tag", None)
        or getattr(session, "raw_source", None)
        or ""
    ).strip().lower()
    return {
        "source": raw_source or "webui",
        "model": getattr(session, "model", None),
    }


def _active_messages(db, session_id: str) -> list:
    try:
        return list(db.get_messages(session_id) or [])
    except Exception as exc:
        raise WebUISessionDBBridgeError(
            f"Could not inspect the source session database: {exc}"
        ) from exc


def _has_archived_messages(db, session_id: str) -> bool:
    checker = getattr(db, "has_archived_messages", None)
    if callable(checker):
        try:
            return bool(checker(session_id))
        except Exception as exc:
            raise WebUISessionDBBridgeError(
                f"Could not inspect archived source messages: {exc}"
            ) from exc
    return False


def _create_or_seed_parent(db, source_session: Any, source_context: list) -> bool:
    """Ensure a branch parent row exists without overwriting durable history.

    Returns ``True`` when this operation created the parent row, allowing the
    caller to remove it during compensation if child creation fails.
    """
    source_id = str(getattr(source_session, "session_id", "") or "")
    if not source_id:
        raise WebUISessionDBBridgeError("Source session has no durable session ID.")

    existing = db.get_session(source_id)
    if existing:
        active = _active_messages(db, source_id)
        if not active and _has_archived_messages(db, source_id):
            raise WebUISessionDBBridgeError(
                "Source session has archived history but no active context; "
                "the branch was not created."
            )
        if not active and source_context:
            db.replace_messages(source_id, list(source_context))
        return False

    db.create_session(source_id, **_session_metadata(source_session))
    try:
        if source_context:
            db.replace_messages(source_id, list(source_context))
        set_title = getattr(db, "set_session_title", None)
        if callable(set_title) and getattr(source_session, "title", None):
            set_title(source_id, str(source_session.title))
    except Exception:
        try:
            db.delete_session(source_id)
        except Exception:
            logger.exception("Failed to roll back materialized branch parent %s", source_id)
        raise
    return True


def _delete_session_quietly(db, session_id: str) -> None:
    try:
        db.delete_session(session_id)
    except Exception:
        logger.exception("Failed to roll back WebUI state.db session %s", session_id)


def _message_identity(message: Any) -> tuple[str, ...]:
    """Return a storage-agnostic identity for active transcript alignment."""
    if not isinstance(message, dict):
        return ("", repr(message))

    def _stable(value: Any) -> str:
        try:
            return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return repr(value)

    return (
        str(message.get("role") or ""),
        _stable(message.get("content")),
        str(message.get("tool_call_id") or message.get("tool_use_id") or ""),
        str(message.get("tool_name") or message.get("name") or ""),
        _stable(message.get("tool_calls") or []),
    )


def _messages_align(left: Iterable[dict], right: Iterable[dict]) -> bool:
    return [_message_identity(message) for message in left] == [
        _message_identity(message) for message in right
    ]


def _ensure_existing_session(db, session: Any, seed_messages: list) -> bool:
    """Ensure *session* has a DB row; return whether this call created it."""
    session_id = str(getattr(session, "session_id", "") or "")
    if not session_id:
        raise WebUISessionDBBridgeError("Session has no durable session ID.")
    if db.get_session(session_id):
        return False
    db.create_session(session_id, **_session_metadata(session))
    try:
        if seed_messages:
            db.replace_messages(session_id, seed_messages)
    except Exception:
        _delete_session_quietly(db, session_id)
        raise
    return True


def replace_webui_active_transcript(
    session: Any, active_messages: Iterable[dict] | None
) -> dict[str, int]:
    """Replace only a WebUI session's live Agent transcript.

    Retry, regenerate, and edit-resubmit are replacement operations rather
    than audit rewinds.  Inactive compaction/rewind rows remain untouched.
    """
    session_id = str(getattr(session, "session_id", "") or "")
    replacement = [
        dict(message) for message in (active_messages or []) if isinstance(message, dict)
    ]
    db = _open_session_db(getattr(session, "profile", None))
    created = False
    try:
        created = _ensure_existing_session(db, session, [])
        db.replace_messages(session_id, replacement, active_only=True)
        return {"active_count": len(replacement)}
    except Exception as exc:
        if created:
            _delete_session_quietly(db, session_id)
        if isinstance(exc, WebUISessionDBBridgeError):
            raise
        raise WebUISessionDBBridgeError(
            f"Could not replace the active session transcript: {exc}"
        ) from exc
    finally:
        try:
            db.close()
        except Exception:
            logger.debug("Failed to close active-transcript state.db handle", exc_info=True)


def rewind_webui_active_transcript(
    session: Any,
    before_messages: Iterable[dict] | None,
    after_messages: Iterable[dict] | None,
) -> dict[str, Any]:
    """Persist an Agent-compatible undo before WebUI publishes its sidecar.

    A clean active transcript uses ``rewind_to_message`` so surviving prefix
    rows retain identity.  Legacy JSON/DB divergence uses the Agent's atomic
    reconciliation primitive to publish the exact intended active view while
    retaining all previous rows as inactive audit history.
    """
    session_id = str(getattr(session, "session_id", "") or "")
    before = [dict(message) for message in (before_messages or []) if isinstance(message, dict)]
    after = [dict(message) for message in (after_messages or []) if isinstance(message, dict)]
    if len(after) >= len(before):
        raise WebUISessionDBBridgeError("Undo did not produce a shorter active transcript.")

    db = _open_session_db(getattr(session, "profile", None))
    created = False
    try:
        created = _ensure_existing_session(db, session, before)
        active = _active_messages(db, session_id)
        target = active[len(after)] if len(active) > len(after) else None
        clean_boundary = (
            _messages_align(active, before)
            and isinstance(target, dict)
            and target.get("role") == "user"
            and target.get("id") is not None
        )
        if clean_boundary:
            result = db.rewind_to_message(session_id, int(target["id"]))
            return {
                "mode": "targeted",
                "rewound_count": int(result.get("rewound_count", 0)),
            }

        repair = getattr(db, "reconcile_active_transcript_for_rewind", None)
        if not callable(repair):
            raise WebUISessionDBBridgeError(
                "Installed Hermes Agent cannot repair a diverged session transcript."
            )
        result = repair(session_id, after)
        logger.warning(
            "Repaired diverged active transcript for session %s: active %d→%d",
            session_id,
            len(active),
            len(after),
        )
        return {
            "mode": "reconciled",
            "rewound_count": int(result.get("rewound_count", 0)),
        }
    except Exception as exc:
        if created:
            _delete_session_quietly(db, session_id)
        if isinstance(exc, WebUISessionDBBridgeError):
            raise
        raise WebUISessionDBBridgeError(
            f"Could not rewind the active session transcript: {exc}"
        ) from exc
    finally:
        try:
            db.close()
        except Exception:
            logger.debug("Failed to close rewind state.db handle", exc_info=True)


def _remove_new_sidecar(child_session: Any) -> None:
    """Remove a sidecar/index entry created by a failed child save."""
    try:
        path = child_session.path
        path.unlink(missing_ok=True)
        path.with_suffix(".json.bak").unlink(missing_ok=True)
    except Exception:
        logger.exception(
            "Failed to remove partial WebUI child sidecar %s",
            getattr(child_session, "session_id", "?"),
        )
    try:
        from api.models import prune_session_from_index

        prune_session_from_index(child_session.session_id)
    except Exception:
        logger.exception(
            "Failed to remove partial WebUI child index entry %s",
            getattr(child_session, "session_id", "?"),
        )


def persist_webui_child_session(
    child_session: Any,
    seed_messages: Iterable[dict] | None,
    *,
    source_session: Any | None = None,
    parent_session_id: str | None = None,
    source_context: Iterable[dict] | None = None,
) -> None:
    """Persist a WebUI child in state.db and then publish its sidecar.

    ``seed_messages`` is the child's model-facing context, never the parent's
    archived transcript.  For branches, ``source_session`` is required so a
    missing parent row can be materialized before the child foreign key is
    created.  The sidecar is saved only after the DB rows are committed.
    """
    child_id = str(getattr(child_session, "session_id", "") or "")
    if not child_id:
        raise WebUISessionDBBridgeError("Child session has no durable session ID.")
    if parent_session_id and source_session is None:
        raise WebUISessionDBBridgeError("A branch parent is required for durable lineage.")

    seed = [dict(message) for message in (seed_messages or []) if isinstance(message, dict)]
    source_seed = [
        dict(message)
        for message in (source_context or [])
        if isinstance(message, dict)
    ]
    profile = getattr(child_session, "profile", None)
    sidecar_preexisted = False
    try:
        sidecar_preexisted = bool(child_session.path.exists())
    except Exception:
        pass
    db = _open_session_db(profile)
    parent_created = False
    child_created = False
    sidecar_published = False
    try:
        if db.get_session(child_id):
            raise WebUISessionDBBridgeError(
                f"A durable session with ID {child_id} already exists."
            )

        if parent_session_id:
            parent_created = _create_or_seed_parent(db, source_session, source_seed)

        db.create_session(
            child_id,
            **_session_metadata(child_session),
            parent_session_id=parent_session_id,
        )
        child_created = True
        db.replace_messages(child_id, seed)

        set_title = getattr(db, "set_session_title", None)
        if callable(set_title) and getattr(child_session, "title", None):
            set_title(child_id, str(child_session.title))

        child_session.save()
        sidecar_published = True
    except WebUISessionDBBridgeError:
        if child_created:
            _delete_session_quietly(db, child_id)
        if parent_created and source_session is not None:
            _delete_session_quietly(db, source_session.session_id)
        if not sidecar_published and not sidecar_preexisted:
            _remove_new_sidecar(child_session)
        raise
    except Exception as exc:
        if child_created:
            _delete_session_quietly(db, child_id)
        if parent_created and source_session is not None:
            _delete_session_quietly(db, source_session.session_id)
        if not sidecar_published and not sidecar_preexisted:
            _remove_new_sidecar(child_session)
        raise WebUISessionDBBridgeError(
            f"Could not durably create child session {child_id}: {exc}"
        ) from exc
    finally:
        try:
            db.close()
        except Exception:
            logger.debug("Failed to close child-session state.db handle", exc_info=True)

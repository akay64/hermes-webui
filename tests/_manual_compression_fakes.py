"""Small DB-backed fakes for manual-compression route tests.

The WebUI test environment does not install Hermes Agent as an importable
package, so route tests need to model the narrow SessionDB/AIAgent lifecycle
that production exercises.  These fakes deliberately preserve the important
invariants: seed active rows once, archive them on a successful compression,
and leave them untouched when the compressor aborts or the archive fails.
"""

from __future__ import annotations

from copy import deepcopy


class FakeSessionDB:
    def __init__(self):
        self.active = {}
        self.archived = {}
        self.system_prompts = {}
        self.archive_calls = 0
        self.closed = False

    def create_session(self, session_id, source, **kwargs):
        self.active.setdefault(session_id, [])
        return session_id

    def get_messages(self, session_id, include_inactive=False):
        messages = list(self.active.get(session_id, []))
        if include_inactive:
            messages = list(self.archived.get(session_id, [])) + messages
        return deepcopy(messages)

    def has_archived_messages(self, session_id):
        return bool(self.archived.get(session_id))

    def append_message(self, session_id, role, content=None, **kwargs):
        message = {"role": role, "content": deepcopy(content)}
        for key in (
            "tool_name",
            "tool_calls",
            "tool_call_id",
            "reasoning",
            "reasoning_content",
            "timestamp",
        ):
            if kwargs.get(key) is not None:
                message[key] = deepcopy(kwargs[key])
        self.active.setdefault(session_id, []).append(message)
        return len(self.active[session_id])

    def archive_and_compact(self, session_id, compacted_messages):
        self.archived.setdefault(session_id, []).extend(
            deepcopy(self.active.get(session_id, []))
        )
        self.active[session_id] = deepcopy(compacted_messages)
        self.archive_calls += 1
        return len(self.active[session_id])

    def update_system_prompt(self, session_id, system_prompt):
        self.system_prompts[session_id] = system_prompt

    def close(self):
        self.closed = True


def make_db_backed_agent_class(agent_cls, session_db):
    """Wrap a route-test agent with the current compression lifecycle."""

    class DBBackedAgent(agent_cls):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.session_id = kwargs.get("session_id")
            self.model = kwargs.get("model")
            self._session_db = session_db
            self._session_db_created = False
            self.compression_in_place = True
            self._last_compaction_in_place = False
            self._last_compaction_db_error = None
            self._last_compaction_guard_error = None
            if not hasattr(self.context_compressor, "_last_compress_aborted"):
                self.context_compressor._last_compress_aborted = False
            if not hasattr(self.context_compressor, "compression_count"):
                self.context_compressor.compression_count = 0

        def _ensure_db_session(self):
            if not self._session_db_created:
                self._session_db.create_session(
                    self.session_id,
                    "webui",
                    model=self.model,
                )
                self._session_db_created = True

        def _flush_messages_to_session_db(self, messages, conversation_history=None):
            if self._session_db.get_messages(self.session_id):
                return
            for message in messages:
                self._session_db.append_message(
                    self.session_id,
                    message.get("role") or "user",
                    content=message.get("content"),
                    tool_calls=message.get("tool_calls"),
                    timestamp=message.get("timestamp") or message.get("_ts"),
                )

        def _compress_context(
            self,
            messages,
            system_message,
            *,
            approx_tokens=None,
            task_id="default",
            focus_topic=None,
            force=False,
        ):
            self._last_compaction_in_place = False
            self._last_compaction_db_error = None
            self._last_compaction_guard_error = None
            compressor = self.context_compressor
            try:
                compressed = compressor.compress(
                    messages,
                    current_tokens=approx_tokens,
                    focus_topic=focus_topic,
                    force=force,
                )
            except TypeError:
                compressed = compressor.compress(
                    messages,
                    current_tokens=approx_tokens,
                    focus_topic=focus_topic,
                )
            if getattr(compressor, "_last_compress_aborted", False):
                return list(messages), system_message
            try:
                commit_guard = getattr(self, "_compression_before_db_commit", None)
                if callable(commit_guard):
                    try:
                        commit_guard()
                    except Exception as exc:
                        self._last_compaction_guard_error = str(exc)
                        raise
                self._session_db.archive_and_compact(self.session_id, compressed)
            except Exception as exc:
                self._last_compaction_db_error = str(exc)
                return list(messages), system_message
            compressor.compression_count += 1
            self._last_compaction_in_place = True
            return compressed, system_message

    return DBBackedAgent

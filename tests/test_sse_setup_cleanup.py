"""Subscriber cleanup when persistent SSE setup fails before its drain loop."""

import io
from urllib.parse import urlparse

import pytest

from api import clarify, routes


class _HeaderFailureHandler:
    def send_response(self, *_args, **_kwargs):
        return None

    def send_header(self, *_args, **_kwargs):
        raise BrokenPipeError("client disconnected during SSE headers")


class _InitialWriteHandler:
    def __init__(self):
        self.wfile = io.BytesIO()

    def send_response(self, *_args, **_kwargs):
        return None

    def send_header(self, *_args, **_kwargs):
        return None

    def end_headers(self):
        return None


@pytest.mark.parametrize("kind", ["approval", "clarify"])
def test_persistent_prompt_sse_unsubscribes_when_header_setup_fails(kind):
    sid = f"setup-failure-{kind}"
    handler = _HeaderFailureHandler()

    if kind == "approval":
        registry = routes._approval_sse_subscribers
        lock = routes._lock
        invoke = routes._handle_approval_sse_stream
    else:
        registry = clarify._clarify_sse_subscribers
        lock = clarify._lock
        invoke = routes._handle_clarify_sse_stream

    try:
        invoke(handler, urlparse(f"/stream?session_id={sid}"))
        with lock:
            assert sid not in registry
    finally:
        with lock:
            registry.pop(sid, None)


@pytest.mark.parametrize("kind", ["approval", "clarify"])
def test_persistent_prompt_sse_unsubscribes_when_initial_write_fails(monkeypatch, kind):
    from api import streaming

    sid = f"initial-failure-{kind}"
    handler = _InitialWriteHandler()

    if kind == "approval":
        registry = routes._approval_sse_subscribers
        lock = routes._lock
        invoke = routes._handle_approval_sse_stream
    else:
        registry = clarify._clarify_sse_subscribers
        lock = clarify._lock
        invoke = routes._handle_clarify_sse_stream

    monkeypatch.setattr(
        streaming,
        "_sse",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(BrokenPipeError("initial write failed")),
    )
    try:
        invoke(handler, urlparse(f"/stream?session_id={sid}"))
        with lock:
            assert sid not in registry
    finally:
        with lock:
            registry.pop(sid, None)
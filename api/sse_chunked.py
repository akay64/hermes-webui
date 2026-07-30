"""Opt-in chunked transfer-encoding for SSE responses.

The stdlib HTTP server emits SSE bodies with no framing at all (no
Content-Length, no Transfer-Encoding). Tornado-based reverse proxies —
notably jupyter-server-proxy, which fronts the WebUI in JupyterHub/REANA
deployments — read such responses with read-until-close semantics and
buffer the ENTIRE body until the upstream connection dies, so the browser
receives no events while a stream is live: chat appears frozen, then the
client's watchdog kills it ("Connection interrupted"). Explicit chunked
framing lets every event flush through each hop immediately.

This is opt-in via the ``HERMES_WEBUI_SSE_CHUNKED`` environment variable so
the default wire format is unchanged: directly-served deployments keep the
historical unframed stream, and only deployments behind a buffering proxy
need to set the flag. It sits alongside the existing ``X-Accel-Buffering: no``
proxy-compatibility hint on these same handlers.

Usage: in an SSE handler, replace ``handler.end_headers()`` with
``end_sse_headers(handler)``. When the flag is set, all subsequent
``handler.wfile.write()`` calls are framed transparently.
"""

import os
import time

_TRUTHY = {"1", "true", "yes", "on"}


def chunked_sse_enabled() -> bool:
    """True when ``HERMES_WEBUI_SSE_CHUNKED`` opts into chunked SSE framing."""
    return os.getenv("HERMES_WEBUI_SSE_CHUNKED", "").strip().lower() in _TRUTHY


class _ChunkedSSEWriter:
    """Wrap ``wfile`` so each write becomes an HTTP/1.1 chunk."""

    def __init__(self, raw):
        self._raw = raw

    def write(self, data):
        if not data:
            return 0
        payload = bytes(data)
        self._raw.write(b"%X\r\n" % len(payload) + payload + b"\r\n")
        return len(payload)

    def flush(self):
        self._raw.flush()

    def __getattr__(self, name):
        return getattr(self._raw, name)


class _LeasedSSEWriter:
    """End an SSE response once its planned connection lease expires."""

    def __init__(self, raw, deadline: float):
        self._raw = raw
        self._deadline = deadline

    def _check_lease(self):
        if self._deadline and time.monotonic() >= self._deadline:
            raise TimeoutError("SSE connection lease expired")

    def write(self, data):
        self._check_lease()
        return self._raw.write(data)

    def flush(self):
        self._check_lease()
        return self._raw.flush()

    def __getattr__(self, name):
        return getattr(self._raw, name)


def end_sse_headers(handler, *, lease: bool = False) -> bool:
    """Finish SSE response headers, optionally enabling chunked framing.

    When ``HERMES_WEBUI_SSE_CHUNKED`` is set, send ``Transfer-Encoding: chunked``
    and wrap ``wfile`` so each write is framed as one HTTP/1.1 chunk; otherwise
    behave exactly like ``handler.end_headers()`` so the default wire format is
    preserved. Chunked + ``Connection: close`` is legal and unambiguous on the
    HTTP/1.1 responses these handlers emit.
    """
    server = getattr(handler, "server", None)
    admit_sse = getattr(server, "admit_sse", None)
    deadline = admit_sse(handler, lease=lease) if admit_sse is not None else 0.0
    if deadline is None:
        # send_response()/send_header() only buffer bytes until end_headers(), so
        # admission can still replace the pending 200 without corrupting the wire.
        handler._headers_buffer = []
        handler.send_response(503)
        handler.send_header("Retry-After", "2")
        handler.send_header("Connection", "close")
        handler.send_header("Content-Length", "0")
        handler.end_headers()
        handler.close_connection = True
        return False
    try:
        if chunked_sse_enabled():
            handler.send_header("Transfer-Encoding", "chunked")
            handler.end_headers()
            handler.wfile = _ChunkedSSEWriter(handler.wfile)
        else:
            handler.end_headers()
        if deadline:
            handler.wfile = _LeasedSSEWriter(handler.wfile, deadline)
        return True
    except Exception:
        release_sse = getattr(server, "_release_sse_for_current_request", None)
        if release_sse is not None:
            release_sse()
        raise

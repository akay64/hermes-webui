"""Focused tests for WebUI's durable auto-compression writeback signal."""

from types import SimpleNamespace

from api.streaming import _webui_compression_was_committed


def _agent(*, in_place, committed, count=1):
    return SimpleNamespace(
        session_id="signal-test",
        compression_in_place=in_place,
        _last_compaction_in_place=committed,
        context_compressor=SimpleNamespace(compression_count=count),
    )


def test_committed_in_place_compression_is_a_writeback_boundary():
    assert _webui_compression_was_committed(
        _agent(in_place=True, committed=True), 0
    ) is True


def test_uncommitted_in_place_compression_is_not_a_writeback_boundary():
    assert _webui_compression_was_committed(
        _agent(in_place=True, committed=False), 0
    ) is False


def test_no_new_compression_is_not_a_writeback_boundary():
    assert _webui_compression_was_committed(
        _agent(in_place=True, committed=True, count=2), 2
    ) is False


def test_legacy_rotation_keeps_existing_count_based_behavior():
    assert _webui_compression_was_committed(
        _agent(in_place=False, committed=False), 0
    ) is True

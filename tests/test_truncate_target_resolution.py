"""Unit coverage for selector-based WebUI transcript truncation."""

import pytest

from api.session_ops import (
    TargetMessageAmbiguousError,
    TargetMessageNotFoundError,
    TargetMessageSelectorError,
    resolve_truncate_target_index,
)


def _row(role, content, timestamp, *, mid=None, token=None, source=None, attachments=None):
    row = {"role": role, "content": content, "timestamp": timestamp}
    if mid is not None:
        row["id"] = mid
    if token is not None:
        row["_active_turn_token"] = token
    if source is not None:
        row["_source"] = source
    if attachments is not None:
        row["attachments"] = attachments
    return row


def test_resolves_unique_message_id_to_sidecar_index():
    messages = [
        _row("user", "same", 1.0, mid="u1"),
        _row("assistant", "reply", 2.0, mid="a1"),
    ]
    assert resolve_truncate_target_index(messages, {"id": "a1"}) == 1


def test_resolves_active_turn_token_before_legacy_content():
    messages = [
        _row("user", "same", 1.0, token="turn-1"),
        _row("user", "same", 1.0, token="turn-2"),
    ]
    assert resolve_truncate_target_index(
        messages,
        {"_active_turn_token": "turn-2"},
    ) == 1


def test_conflicting_strong_identity_does_not_fall_back_to_content():
    messages = [_row("user", "same", 1.0, mid="actual")]
    with pytest.raises(TargetMessageNotFoundError):
        resolve_truncate_target_index(
            messages,
            {
                "id": "missing",
                "role": "user",
                "timestamp": 1.0,
                "content": "same",
            },
        )


def test_same_content_with_different_tokens_remains_distinct():
    messages = [
        _row("user", "same", 1.0, token="turn-1"),
        _row("user", "same", 1.0, token="turn-2"),
    ]
    assert resolve_truncate_target_index(
        messages,
        {
            "role": "user",
            "timestamp": 1.0,
            "content": "same",
            "_active_turn_token": "turn-1",
        },
    ) == 0


def test_legacy_selector_requires_unique_exact_identity():
    messages = [
        _row("user", "same", 1.0, source="webui", attachments=[]),
        _row("user", "same", 1.0, source="webui", attachments=[]),
    ]
    with pytest.raises(TargetMessageAmbiguousError):
        resolve_truncate_target_index(
            messages,
            {
                "role": "user",
                "timestamp": 1.0,
                "content": "same",
                "source": "webui",
                "attachments": [],
            },
        )


@pytest.mark.parametrize(
    "selector",
    [
        {"role": "robot", "content": "x", "timestamp": 1.0},
        {"id": None, "role": "user", "content": "x", "timestamp": 1.0},
        {"id": "", "role": "user", "content": "x", "timestamp": 1.0},
        {"_active_turn_token": "", "role": "user", "content": "x", "timestamp": 1.0},
    ],
)
def test_malformed_selector_is_rejected(selector):
    with pytest.raises(TargetMessageSelectorError):
        resolve_truncate_target_index(
            [{"role": "user", "content": "x", "timestamp": 1.0}],
            selector,
        )


def test_strong_identity_mismatch_is_not_weakened_by_matching_legacy_fields():
    messages = [
        _row("user", "same", 1.0, mid="u1", token="turn-1"),
        _row("user", "same", 1.0, mid="u2", token="turn-2"),
    ]
    with pytest.raises(TargetMessageNotFoundError):
        resolve_truncate_target_index(
            messages,
            {
                "id": "u1",
                "_active_turn_token": "turn-2",
                "role": "user",
                "timestamp": 1.0,
                "content": "same",
            },
        )

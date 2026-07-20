"""Transient transcript annotations for pending selected contexts."""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MESSAGES = (ROOT / "static" / "messages.js").read_text(encoding="utf-8")
STYLE = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
UI = (ROOT / "static" / "ui.js").read_text(encoding="utf-8")
I18N = (ROOT / "static" / "i18n.js").read_text(encoding="utf-8")


def _function_body(source: str, signature: str, next_signature: str) -> str:
    start = source.index(signature)
    end = source.index(next_signature, start)
    return source[start:end]


def test_pending_annotation_state_stays_frontend_only_and_selection_owned():
    assert "pending_user_annotations" not in MESSAGES
    assert "webui_annotations" not in MESSAGES
    assert "let _selectedTextReplyInfo=null;" in MESSAGES
    assert "_pendingSelections.push({id, name, text, ordinal:_selectionIdCounter, source:source||null});" in MESSAGES

    add_body = _function_body(
        MESSAGES,
        "function _addNamedContextBlock(text, source){",
        "function _removeNamedContextBlock(id){",
    )
    assert "_renderPendingSelectionAnnotations();" in add_body


def test_all_pending_selection_death_paths_share_visual_cleanup():
    remove_body = _function_body(
        MESSAGES,
        "function _removeNamedContextBlock(id){",
        "function _clearPendingSelections(){",
    )
    clear_body = _function_body(
        MESSAGES,
        "function _clearPendingSelections(){",
        "if(typeof window!=='undefined') window._clearPendingSelections",
    )
    assert "_renderPendingSelectionAnnotations();" in remove_body
    assert "_clearPendingSelectionAnnotationVisuals();" in clear_body
    assert "_pendingSelections=[];" in clear_body

    # Send/queue, Clear all, New Chat, and real session changes already call the
    # same helper; keeping visual ownership there prevents lifecycle drift.
    assert "_clearPendingSelections();" in MESSAGES


def test_rename_syncs_by_immutable_id_without_rerendering_transcript():
    edit_body = _function_body(
        MESSAGES,
        "function _editSelectionChipName(id,chip){",
        "function _composerTextWithPendingSelections(){",
    )
    assert "_syncPendingSelectionAnnotationLabels();" in edit_body
    assert "renderMessages(" not in edit_body
    assert "data-pending-context-id" in MESSAGES


def test_annotation_projection_is_idempotent_and_has_no_observer_or_render_loop():
    projection = _function_body(
        MESSAGES,
        "function _renderPendingSelectionAnnotations(root){",
        "function _addNamedContextBlock(text, source){",
    )
    assert "_clearPendingSelectionAnnotationVisuals(root);" in projection
    assert "MutationObserver" not in projection
    assert "ResizeObserver" not in projection
    assert "requestAnimationFrame" not in projection
    assert "renderMessages(" not in projection
    assert "getBoundingClientRect" not in projection
    assert "CSS.highlights.set" in projection


def test_existing_transcript_render_paths_reapply_pending_visuals_after_rebuild():
    assert UI.count("_renderPendingSelectionAnnotations(inner)") >= 2
    assert "::highlight(hermes-pending-context)" in STYLE
    assert ".pending-context-bubble" in STYLE
    assert ".pending-context-source" in STYLE
    assert "context_annotations_label: 'Selected contexts'" in I18N

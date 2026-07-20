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
        "if(typeof window!=='undefined')window._renderPendingSelectionAnnotations",
    )
    assert "_clearPendingSelectionAnnotationVisuals(root);" in projection
    assert "MutationObserver" not in projection
    assert "ResizeObserver" not in projection
    assert "requestAnimationFrame" not in projection
    assert "renderMessages(" not in projection
    assert "_positionPendingContextBubbles(layers);" in projection
    assert "CSS.highlights.set" in projection

    positioning = _function_body(
        MESSAGES,
        "function _positionPendingContextBubbles(layers){",
        "function _focusPendingSelectionCard(id){",
    )
    assert "range.getBoundingClientRect()" in positioning
    assert "group.layer.getBoundingClientRect()" in positioning
    assert "MutationObserver" not in positioning
    assert "ResizeObserver" not in positioning
    assert "requestAnimationFrame" not in positioning
    assert "renderMessages(" not in positioning


def test_range_resolution_checks_every_rendered_segment_of_the_message():
    resolution = _function_body(
        MESSAGES,
        "function _resolvePendingSelectionRange(selection,root){",
        "function _positionPendingContextBubbles(layers){",
    )
    assert "root.querySelectorAll(selector)" in resolution
    assert "for(const sourceNode of sourceNodes)" in resolution
    assert "bodyText.slice(capturedStart,capturedEnd)===exact" in resolution
    assert "return {range,sourceNode};" in resolution


def test_paragraph_selection_can_include_only_trailing_boundary_whitespace():
    capture = _function_body(
        MESSAGES,
        "function _selectedTextReplySourceForRange(range){",
        "function _selectedTextReplySelection(){",
    )
    assert "const sourceRange=range.cloneRange();" in capture
    assert "sourceRange.setEnd(startBody,startBody.childNodes.length);" in capture
    assert "sourceRange.toString().trim()!==range.toString().trim()" in capture
    assert "before.setEnd(sourceRange.startContainer,sourceRange.startOffset);" in capture
    assert "through.setEnd(sourceRange.endContainer,sourceRange.endOffset);" in capture


def test_highlight_and_bubble_share_the_same_context_focus_action():
    assert "function _pendingSelectionAnnotationClick(e){" in MESSAGES
    click_body = _function_body(
        MESSAGES,
        "function _pendingSelectionAnnotationClick(e){",
        "function _syncPendingSelectionAnnotationLabels(){",
    )
    assert "range.getClientRects()" in click_body
    assert "_focusPendingSelectionCard(id);" in click_body
    assert "document.addEventListener('click',_pendingSelectionAnnotationClick);" in MESSAGES


def test_collapsed_tray_focus_runs_after_disclosure_and_flashes_target_once():
    focus_body = _function_body(
        MESSAGES,
        "function _focusPendingSelectionCard(id){",
        "function _pendingSelectionAnnotationClick(e){",
    )
    assert "if(_selectionTrayCollapsed){" in focus_body
    assert "_setSelectionTrayCollapsed(false);" in focus_body
    assert "window.requestAnimationFrame(reveal);" in focus_body
    assert "pending-context-focus-flash" in focus_body
    assert "scrollIntoView({block:'center',behavior:'smooth'})" in focus_body


def test_resize_realign_is_single_frame_coalesced_and_cancellable():
    schedule = _function_body(
        MESSAGES,
        "function _schedulePendingSelectionAnnotationLayout(){",
        "function _addNamedContextBlock(text, source){",
    )
    assert "if(!_pendingSelections.length||_pendingSelectionAnnotationResizeRaf)return;" in schedule
    assert "window.requestAnimationFrame" in schedule
    assert "_renderPendingSelectionAnnotations();" in schedule
    assert "MutationObserver" not in schedule
    assert "renderMessages(" not in schedule
    assert "window.cancelAnimationFrame(_pendingSelectionAnnotationResizeRaf);" in MESSAGES


def test_existing_transcript_render_paths_reapply_pending_visuals_after_rebuild():
    assert UI.count("_renderPendingSelectionAnnotations(inner)") >= 4
    assert UI.count("_postProcessWithAnchorSuppression(inner);") >= 2
    assert "::highlight(hermes-pending-context)" in STYLE
    assert ".pending-context-bubble" in STYLE
    assert ".pending-context-source" in STYLE
    assert "@keyframes pending-context-focus-flash" in STYLE
    assert "context_annotations_label: 'Selected contexts'" in I18N


def test_annotation_bubbles_overlay_without_reflow_and_fade_on_hover():
    assert ".pending-context-source>.msg-body" not in STYLE
    assert "const desired=Math.max(0,rect.top-layerRect.top);" in MESSAGES
    bubble_rule = STYLE[STYLE.index(".pending-context-bubble{"):]
    bubble_rule = bubble_rule[:bubble_rule.index("}")]
    assert "opacity" in bubble_rule
    hover_rule = STYLE[STYLE.index(".pending-context-bubble:hover{"):]
    hover_rule = hover_rule[:hover_rule.index("}")]
    assert "opacity:" in hover_rule

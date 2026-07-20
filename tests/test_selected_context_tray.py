"""Behavior and layout regressions for the pending selected-context tray."""
from __future__ import annotations

from pathlib import Path

import pytest

try:
    from playwright.sync_api import sync_playwright
except Exception:
    sync_playwright = None


ROOT = Path(__file__).resolve().parents[1]
STYLE = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
MESSAGES = (ROOT / "static" / "messages.js").read_text(encoding="utf-8")
I18N = (ROOT / "static" / "i18n.js").read_text(encoding="utf-8")

_BROWSER_ARGS = ["--no-sandbox", "--disable-dev-shm-usage"]


def _require_playwright():
    if sync_playwright is None:
        pytest.skip("playwright is unavailable; run `playwright install chromium`")
    return sync_playwright


def test_tray_source_contract_keeps_cards_unshrinkable_and_collapse_state_separate():
    """Non-browser runs still pin the two chokepoints behind the behavior."""
    assert ".selection-context-card{display:flex;flex:0 0 auto;" in STYLE
    assert ".composer-selection-list" in STYLE
    assert ".composer-selection-list[hidden]{display:none;}" in STYLE
    assert "let _selectionTrayCollapsed=false;" in MESSAGES
    assert "function _setSelectionTrayCollapsed(collapsed)" in MESSAGES
    assert "toggle.setAttribute('aria-expanded', _selectionTrayCollapsed?'false':'true')" in MESSAGES
    assert "list.hidden=_selectionTrayCollapsed" in MESSAGES
    assert "_renderSelectionChips({revealId:id})" in MESSAGES
    clear_fn = MESSAGES[
        MESSAGES.index("function _clearPendingSelections(){"):
        MESSAGES.index("if(typeof window!=='undefined') window._clearPendingSelections", MESSAGES.index("function _clearPendingSelections(){"))
    ]
    assert "_selectionTrayCollapsed=false;" in clear_fn
    assert "_selectionTrayScrollTop=0;" in clear_fn
    assert "context_blocks_count: (n) =>" in I18N
    assert "context_blocks_expand: 'Expand selected contexts'" in I18N
    assert "context_blocks_collapse: 'Collapse selected contexts'" in I18N
    assert "context_blocks_clear_all: 'Clear all'" in I18N
    assert "clearAll.className='composer-selection-clear'" in MESSAGES
    assert "clearAll.addEventListener('click',()=>{" in MESSAGES
    assert "_clearPendingSelections();" in MESSAGES


def test_collapsed_summary_has_input_separation_without_transcript_fade_overlay():
    assert "margin:0 auto 8px;" in STYLE
    assert ".composer-wrap::before" not in STYLE


def test_many_context_cards_keep_intrinsic_content_and_scroll_instead_of_shrinking():
    """Eight cards must overflow the bounded list, not compress each other."""
    sp = _require_playwright()
    cards = "".join(
        f"""
        <article class="selection-context-card">
          <div class="selection-context-accent"></div>
          <div class="selection-context-body">
            <div class="selection-context-header">
              <button class="selection-context-name">Context {idx}</button>
              <button class="selection-context-remove" aria-label="Remove Context {idx}">×</button>
            </div>
            <blockquote class="selection-context-quote">Selected text for context {idx}</blockquote>
          </div>
        </article>
        """
        for idx in range(1, 9)
    )
    html = f"""<!doctype html><html><head><style>{STYLE}</style></head>
    <body><div class="composer-selection-list">{cards}</div></body></html>"""

    with sp() as pw:
        browser = pw.chromium.launch(headless=True, args=_BROWSER_ARGS)
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 720})
            page.set_content(html)
            result = page.evaluate(
                """() => {
                  const list = document.querySelector('.composer-selection-list');
                  const cards = [...list.querySelectorAll('.selection-context-card')];
                  const rects = cards.map(card => card.getBoundingClientRect());
                  const quotes = cards.map(card => card.querySelector('.selection-context-quote').getBoundingClientRect());
                  return {
                    flexShrink: getComputedStyle(cards[0]).flexShrink,
                    scrolls: list.scrollHeight > list.clientHeight,
                    quoteHeights: quotes.map(rect => rect.height),
                    overlaps: rects.slice(1).some((rect, idx) => rect.top < rects[idx].bottom - 0.5),
                  };
                }"""
            )
            assert result["flexShrink"] == "0"
            assert result["scrolls"] is True
            assert min(result["quoteHeights"]) > 0
            assert result["overlaps"] is False
        finally:
            browser.close()


def test_live_tray_collapses_without_discarding_pending_contexts():
    """The real app control hides only presentation; Send content remains."""
    sp = _require_playwright()
    from tests._pytest_port import BASE

    with sp() as pw:
        browser = pw.chromium.launch(headless=True, args=_BROWSER_ARGS)
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 720})
            page.goto(BASE + "/", wait_until="domcontentloaded")
            page.wait_for_selector("#composerSelectionChips")
            page.evaluate(
                """() => {
                  for (let idx = 1; idx <= 8; idx++) {
                    _addNamedContextBlock(`Selected text ${idx}`);
                  }
                }"""
            )

            toggle = page.locator("#composerSelectionToggle")
            selection_list = page.locator("#composerSelectionList")
            assert toggle.get_attribute("aria-expanded") == "true"
            assert selection_list.locator(".selection-context-card").count() == 8
            assert page.evaluate("window._hasPendingSelections()") is True

            toggle.click()
            assert toggle.get_attribute("aria-expanded") == "false"
            assert selection_list.is_hidden()
            assert page.evaluate("window._hasPendingSelections()") is True

            page.evaluate("_addNamedContextBlock('Selected while collapsed')")
            assert toggle.get_attribute("aria-expanded") == "false"
            assert toggle.locator(".composer-selection-count").inner_text() == "9 contexts"
            assert selection_list.locator(".selection-context-card").count() == 9
            assert page.evaluate("window._hasPendingSelections()") is True

            page.locator("#msg").fill("Keep this draft")
            page.locator("#composerSelectionClear").click()
            assert page.locator("#composerSelectionChips").is_hidden()
            assert page.evaluate("window._hasPendingSelections()") is False
            assert page.locator("#msg").input_value() == "Keep this draft"

            page.evaluate("_addNamedContextBlock('New context after clear')")
            assert toggle.get_attribute("aria-expanded") == "true"
            assert selection_list.is_visible()
            assert toggle.locator(".composer-selection-count").inner_text() == "1 context"
            assert selection_list.locator(".selection-context-card").count() == 1
        finally:
            browser.close()

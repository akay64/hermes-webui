"""Browser regression coverage for long user-message disclosure behavior."""
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
UI_JS_PATH = ROOT / "static" / "ui.js"
STYLE_CSS_PATH = ROOT / "static" / "style.css"


@pytest.fixture
def browser_page():
    try:
        from playwright.sync_api import sync_playwright
    except Exception:  # pragma: no cover - dependency missing path
        pytest.skip("playwright is unavailable; run the disclosure browser tests")

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
        except Exception as exc:  # pragma: no cover - browser missing path
            pytest.skip(f"Chromium is unavailable: {exc}")
        page = browser.new_page()
        page.set_content("<!doctype html><html><body></body></html>")
        page.add_style_tag(path=str(STYLE_CSS_PATH))
        page.add_script_tag(path=str(UI_JS_PATH))
        yield page
        browser.close()


def test_long_user_message_boundaries_and_unicode_preview(browser_page):
    result = browser_page.evaluate(
        """
        () => ({
          shortChars: _isLongUserMessage('x'.repeat(599)),
          thresholdChars: _isLongUserMessage('x'.repeat(600)),
          shortLines: _isLongUserMessage(Array(7).fill('line').join('\\n')),
          thresholdLines: _isLongUserMessage(Array(8).fill('line').join('\\n')),
          longPastedLine: _isLongUserMessage('pasted '.repeat(100)),
          previewLength: Array.from(_userDisclosurePreview('😀'.repeat(200))).length,
          previewEndsWithEllipsis: _userDisclosurePreview('😀'.repeat(200)).endsWith('…'),
          previewHasReplacementCharacter: _userDisclosurePreview('😀'.repeat(200)).includes('�'),
          shortUnicodePreserved: _userDisclosurePreview('😀'.repeat(100)) === '😀'.repeat(100),
        })
        """
    )

    assert result == {
        "shortChars": False,
        "thresholdChars": True,
        "shortLines": False,
        "thresholdLines": True,
        "longPastedLine": True,
        "previewLength": 180,
        "previewEndsWithEllipsis": True,
        "previewHasReplacementCharacter": False,
        "shortUnicodePreserved": True,
    }


def test_disclosure_rehydration_updates_native_toggle_accessibility(browser_page):
    result = browser_page.evaluate(
        """
        () => {
          const root = document.createElement('div');
          root.id = 'msgInner';
          root.innerHTML = `
            <div class="msg-row" data-role="user" data-session-msg-idx="3"
                 data-raw-text="${'x'.repeat(700)}" data-user-disclosure-long="1">
              <details class="user-message-disclosure">
                <summary><span class="user-message-disclosure-preview">preview</span><span class="user-message-disclosure-action" aria-hidden="true">Expand full message</span></summary>
                <div class="msg-body">complete content</div>
                <button type="button" class="user-message-disclosure-collapse-bottom">Collapse message</button>
              </details>
            </div>`;
          document.body.appendChild(root);
          _ensureUserDisclosureSession('browser-disclosure-session');
          _rehydrateUserMessageDisclosures(root);
          const details = root.querySelector('details');
          const summary = root.querySelector('summary');
          const action = root.querySelector('.user-message-disclosure-action');
          const initiallyClosed = !details.open &&
            summary.getAttribute('aria-label') === 'Expand full message' &&
            action.textContent === 'Expand full message' &&
            getComputedStyle(summary).borderTopStyle !== 'none' &&
            root.querySelector('.msg-body').getClientRects().length === 0;
          details.open = true;
          details.dispatchEvent(new Event('toggle', {bubbles: true}));
          const bodyRect = root.querySelector('.msg-body').getBoundingClientRect();
          const summaryRect = summary.getBoundingClientRect();
          const bottomButton = root.querySelector('.user-message-disclosure-collapse-bottom');
          const bottomRect = bottomButton.getBoundingClientRect();
          const expanded = summary.getAttribute('aria-label') === 'Collapse message' &&
            action.textContent === 'Collapse message' &&
            getComputedStyle(summary).backgroundColor === 'rgba(0, 0, 0, 0)' &&
            getComputedStyle(root.querySelector('.user-message-disclosure-preview')).display === 'none' &&
            summaryRect.bottom <= bodyRect.top &&
            bottomRect.top >= bodyRect.bottom;
          bottomButton.click();
          const collapsedFromBottom = !details.open &&
            summary.getAttribute('aria-label') === 'Expand full message' &&
            getComputedStyle(bottomButton).display === 'none';
          const contentPreserved = root.querySelector('.msg-body').textContent === 'complete content';
          root.remove();
          return {initiallyClosed, expanded, collapsedFromBottom, contentPreserved};
        }
        """
    )

    assert result == {
        "initiallyClosed": True,
        "expanded": True,
        "collapsedFromBottom": True,
        "contentPreserved": True,
    }


def test_disclosure_heights_keep_collapsed_and_expanded_measurements(browser_page):
    result = browser_page.evaluate(
        """
        () => {
          const row = document.createElement('div');
          const details = document.createElement('details');
          row.className = 'msg-row';
          row.dataset.role = 'user';
          row.dataset.sessionMsgIdx = '4';
          row.dataset.rawText = 'x'.repeat(2000);
          row.dataset.userDisclosureLong = '1';
          details.className = 'user-message-disclosure';
          row.appendChild(details);
          document.body.appendChild(row);

          _applyUserRowIntrinsicHeight(row, row.dataset.rawText);
          const collapsedEstimate = row.style.containIntrinsicSize;
          details.open = true;
          _rememberUserDisclosureHeight(row, 1800, true);
          _applyUserRowIntrinsicHeight(row, row.dataset.rawText);
          const expandedMeasured = row.style.containIntrinsicSize;
          details.open = false;
          _rememberUserDisclosureHeight(row, 120, false);
          _applyUserRowIntrinsicHeight(row, row.dataset.rawText);
          const collapsedMeasured = row.style.containIntrinsicSize;
          details.open = true;
          _applyUserRowIntrinsicHeight(row, row.dataset.rawText);
          const reopenedMeasured = row.style.containIntrinsicSize;
          row.remove();
          return {collapsedEstimate, expandedMeasured, collapsedMeasured, reopenedMeasured};
        }
        """
    )

    assert result["collapsedEstimate"] != result["expandedMeasured"]
    assert result["expandedMeasured"] == "auto 1800px"
    assert result["collapsedMeasured"] != result["expandedMeasured"]
    assert result["reopenedMeasured"] == "auto 1800px"


def test_editing_closed_long_message_opens_editor_and_cancel_restores_state(browser_page):
    result = browser_page.evaluate(
        """
        async () => {
          const root = document.createElement('div');
          root.innerHTML = `
            <div class="msg-row" data-role="user" data-msg-idx="7"
                 data-session-msg-idx="7" data-raw-text="${'x'.repeat(700)}"
                 data-user-disclosure-long="1">
              <details class="user-message-disclosure">
                <summary>preview</summary>
                <div class="msg-body user-message-disclosure-body">original content</div>
              </details>
              <div class="msg-foot"><button onclick="editMessage(this)">Edit</button></div>
            </div>`;
          document.body.appendChild(root);
          _ensureUserDisclosureSession('browser-edit-session');
          _rehydrateUserMessageDisclosures(root);
          const row = root.querySelector('.msg-row');
          const editButton = root.querySelector('button');
          editMessage(editButton);
          const details = row.querySelector('details');
          const textarea = row.querySelector('.msg-edit-area');
          const bar = row.querySelector('.msg-edit-bar');
          await new Promise(requestAnimationFrame);
          const editorVisible = details.open && textarea && bar &&
            textarea.getClientRects().length > 0 &&
            bar.getClientRects().length > 0;
          const editorFocused = document.activeElement === textarea;
          bar.querySelector('.msg-edit-cancel').click();
          const restored = !details.open &&
            row.querySelector('.msg-body').textContent === 'original content' &&
            !row.querySelector('.msg-edit-area') && !row.dataset.editing;
          root.remove();
          return {editorVisible, editorFocused, restored};
        }
        """
    )

    assert result == {
        "editorVisible": True,
        "editorFocused": True,
        "restored": True,
    }


def test_narrow_attachment_layout_is_reserved_for_collapsed_and_expanded_rows(browser_page):
    browser_page.set_viewport_size({"width": 390, "height": 800})
    result = browser_page.evaluate(
        """
        () => {
          document.body.style.margin = '0';
          const style = document.createElement('style');
          style.textContent = `
            *, *::before, *::after { box-sizing: border-box; }
            .message-column { width: calc(100% - 20px); margin: 0 10px; }
            .msg-files { display: flex; flex-wrap: wrap; gap: 6px; padding-left: 30px; margin-bottom: 10px; }
            .msg-media-img { display: inline-block; width: 120px; height: 90px; margin: 3px 4px 3px 0; border: 1px solid #000; }
            .user-message-disclosure { display: block; }
          `;
          document.head.appendChild(style);
          const root = document.createElement('div');
          root.className = 'message-column';
          root.innerHTML = `
            <div class="msg-row" data-role="user" data-session-msg-idx="8"
                 data-raw-text="${'x'.repeat(700)}" data-user-disclosure-long="1">
              <div class="msg-files">
                <img class="msg-media-img" alt="one">
                <img class="msg-media-img" alt="two">
                <img class="msg-media-img" alt="three">
                <img class="msg-media-img" alt="four">
                <img class="msg-media-img" alt="five">
              </div>
              <details class="user-message-disclosure">
                <summary>preview</summary>
                <div class="msg-body">full content</div>
              </details>
            </div>`;
          document.body.appendChild(root);
          const row = root.querySelector('.msg-row');
          const files = root.querySelector('.msg-files');
          const imageTops = [...root.querySelectorAll('.msg-media-img')]
            .map(image => Math.round(image.getBoundingClientRect().top));
          const imageRows = new Set(imageTops).size;
          const attachmentHint = _estimateUserDisclosureAttachmentHeight([
            'one.png', 'two.png', 'three.png', 'four.png', 'five.png',
          ]);
          row.dataset.userDisclosureAttachmentHeight = String(attachmentHint);
          _applyUserRowIntrinsicHeight(row, row.dataset.rawText);
          const collapsedReserve = Number(row.style.containIntrinsicSize.match(/(\\d+)px$/)[1]);
          const filesHeight = Math.round(files.getBoundingClientRect().height);
          row.querySelector('details').open = true;
          _applyUserRowIntrinsicHeight(row, row.dataset.rawText);
          const expandedReserve = Number(row.style.containIntrinsicSize.match(/(\\d+)px$/)[1]);
          root.remove();
          style.remove();
          return {attachmentHint, collapsedReserve, expandedReserve, filesHeight, imageRows};
        }
        """
    )

    assert result["imageRows"] == 3
    assert result["filesHeight"] > 202
    assert result["attachmentHint"] >= result["filesHeight"]
    assert result["collapsedReserve"] >= result["filesHeight"]
    assert result["expandedReserve"] >= result["filesHeight"]

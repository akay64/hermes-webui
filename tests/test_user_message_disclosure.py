"""Browser regression coverage for long user-message disclosure behavior."""
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
UI_JS_PATH = ROOT / "static" / "ui.js"


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
                <summary><span class="user-message-disclosure-preview">preview</span></summary>
                <div class="msg-body">complete content</div>
              </details>
            </div>`;
          document.body.appendChild(root);
          _ensureUserDisclosureSession('browser-disclosure-session');
          _rehydrateUserMessageDisclosures(root);
          const details = root.querySelector('details');
          const summary = root.querySelector('summary');
          const initiallyClosed = !details.open && summary.getAttribute('aria-label') === 'Expand full message';
          details.open = true;
          details.dispatchEvent(new Event('toggle', {bubbles: true}));
          const expanded = summary.getAttribute('aria-label') === 'Collapse message';
          const contentPreserved = root.querySelector('.msg-body').textContent === 'complete content';
          root.remove();
          return {initiallyClosed, expanded, contentPreserved};
        }
        """
    )

    assert result == {
        "initiallyClosed": True,
        "expanded": True,
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

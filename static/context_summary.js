// ── Compressed Context Viewer ───────────────────────────────────────────────
// Floating button (chat view only) that opens a modal showing the latest
// compressed-context marker message exactly as the agent sees it.
// _ctxSid + _ctxFetchSeq guard against stale renders/fetches when the user
// switches sessions or re-opens the dialog.

'use strict';

(function() {

let _ctxSid = null;       // session id the dialog content was fetched for
let _ctxOpen = false;     // whether the dialog is currently visible
let _ctxFetchSeq = 0;     // monotonically increasing fetch guard

// Returns the current session id, or null if no session is loaded.
function _currentSid() {
  return (S && S.session && S.session.session_id) || null;
}

function _ctxAllowed() {
  const compact = window.matchMedia && window.matchMedia('(max-width:900px)').matches;
  // Chat-view affordance only — never show the toggle while another MAIN
  // panel (settings, tasks, insights, …) is active. Same gate as the
  // conversation outline (_currentPanel is owned by panels.js).
  const panel = (typeof _currentPanel === 'undefined') ? 'chat' : (_currentPanel || 'chat');
  const onChatView = panel === 'chat' || panel === 'todos';
  const hasCtx = !!(S && S.session && S.session.has_compressed_context === true);
  return hasCtx && !compact && onChatView;
}

function applyContextSummaryPreference() {
  const toggle = document.getElementById('contextSummaryToggleBtn');
  const enabled = _ctxAllowed();
  document.documentElement.dataset.contextSummary = enabled ? 'enabled' : 'disabled';
  if (toggle) toggle.hidden = !enabled;
  // Session switch while the dialog is open: close so stale content can't
  // linger (the toggle alone can't detect this — a new session may also
  // have compression).
  if (_ctxOpen && _ctxSid && _currentSid() !== _ctxSid) {
    closeContextSummaryDialog();
  }
  if (!enabled && _ctxOpen) {
    closeContextSummaryDialog();
  }
}

// Fallback extraction matching the backend's canonical part semantics
// (text / input_text / output_text, newline-joined). The endpoint already
// returns pre-extracted `text`, so this only runs for plain-string content.
function _markerText(content) {
  if (Array.isArray(content)) {
    return content
      .filter(function(p) {
        return p && (p.type === 'text' || p.type === 'input_text' || p.type === 'output_text');
      })
      .map(function(p) { return p.text || p.content || ''; })
      .join('\n');
  }
  return String(content || '');
}

// Simple HTML-escape for plain-text fallback (renderMd output is already safe).
function _escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function _renderCtxBody(text) {
  const body = document.getElementById('contextSummaryBody');
  if (!body) return;
  if (!text || !text.trim()) {
    body.innerHTML = '<p class="context-summary-empty">' + t('context_summary_empty') + '</p>';
    return;
  }
  const rendered = (typeof renderMd === 'function') ? renderMd(text) : _escHtml(text);
  body.innerHTML = rendered;
}

function openContextSummaryDialog() {
  if (!_ctxAllowed()) {
    applyContextSummaryPreference();
    return;
  }
  const sid = _currentSid();
  if (!sid) return;
  const overlay = document.getElementById('contextSummaryOverlay');
  const body = document.getElementById('contextSummaryBody');
  if (!overlay) return;

  _ctxOpen = true;
  _ctxSid = sid;
  const seq = ++_ctxFetchSeq;
  overlay.hidden = false;
  if (body) body.innerHTML = '<p class="context-summary-empty">' + t('context_summary_loading') + '</p>';

  api('/api/session/context_summary?session_id=' + encodeURIComponent(sid))
    .then(function(data) {
      if (!_ctxOpen || seq !== _ctxFetchSeq) return;   // closed / re-opened meanwhile
      if (_currentSid() !== sid) return;               // session switched
      if (!data || !data.found) {
        _renderCtxBody(null);
        return;
      }
      _renderCtxBody(data.text != null ? data.text : _markerText(data.content));
    })
    .catch(function() {
      if (!_ctxOpen || seq !== _ctxFetchSeq) return;
      const b = document.getElementById('contextSummaryBody');
      if (b) b.innerHTML = '<p class="context-summary-empty">' + t('context_summary_load_error') + '</p>';
    });
}

function closeContextSummaryDialog() {
  _ctxOpen = false;
  _ctxFetchSeq++;          // invalidate any in-flight fetch
  _ctxSid = null;
  const overlay = document.getElementById('contextSummaryOverlay');
  if (overlay) overlay.hidden = true;
}

// Public API (inline onclick handlers).
window.openContextSummaryDialog = openContextSummaryDialog;
window.closeContextSummaryDialog = closeContextSummaryDialog;
window.applyContextSummaryPreference = applyContextSummaryPreference;

// Re-evaluate visibility after every renderMessages() call (terminal SSE
// replaces S.session, so a freshly compressed session gains the button
// without a reload).
(function _hookRenderMessages() {
  if (typeof window._ctxSummaryRenderHooked !== 'undefined') return;

  const _orig = window.renderMessages;
  if (typeof _orig !== 'function') {
    // renderMessages may not be defined yet — retry after DOMContentLoaded.
    if (!window._ctxSummaryRenderHookPending) {
      window._ctxSummaryRenderHookPending = true;
      document.addEventListener('DOMContentLoaded', _hookRenderMessages, { once: true });
    }
    return;
  }
  window._ctxSummaryRenderHooked = true;
  window._ctxSummaryRenderHookPending = false;
  window.renderMessages = function() {
    const result = _orig.apply(this, arguments);
    applyContextSummaryPreference();
    return result;
  };
})();

document.addEventListener('DOMContentLoaded', function() {
  applyContextSummaryPreference();
  const root = document.documentElement;
  // Re-evaluate when the active main panel changes (switchPanel() toggles
  // showing-<panel> classes on <main> and data-workspace-panel on <html>).
  const observer = new MutationObserver(applyContextSummaryPreference);
  observer.observe(root, {
    attributes: true,
    attributeFilter: ['data-workspace-panel']
  });
  const mainEl = document.querySelector('main.main');
  if (mainEl) {
    observer.observe(mainEl, {
      attributes: true,
      attributeFilter: ['class']
    });
  }
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape' && _ctxOpen) closeContextSummaryDialog();
  });
});
window.addEventListener('resize', applyContextSummaryPreference);

})();

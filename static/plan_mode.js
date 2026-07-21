(function (global) {
  'use strict';

  let savingSessionId = null;

  function _planModeSession() {
    return global.S && global.S.session ? global.S.session : null;
  }

  function _planModeButton() {
    if (typeof global.$ === 'function') return global.$('planModeToggle');
    return global.document ? global.document.getElementById('planModeToggle') : null;
  }

  function syncPlanModeToggle() {
    const button = _planModeButton();
    if (!button) return;

    const session = _planModeSession();
    const enabled = !session || session.plan_mode !== false;
    const busy = Boolean(global.S && (global.S.busy || global.S.activeStreamId));
    const saving = Boolean(session && savingSessionId === session.session_id);

    button.classList.toggle('active', enabled);
    button.setAttribute('aria-pressed', enabled ? 'true' : 'false');
    button.disabled = !session || busy || saving;
    if (!session) {
      button.title = 'Plan Mode is enabled by default for new conversations';
    } else if (busy) {
      button.title = 'Plan Mode can be changed after the current turn finishes';
    } else if (saving) {
      button.title = 'Saving Plan Mode…';
    } else {
      button.title = enabled ? 'Plan Mode is on' : 'Plan Mode is off';
    }
  }

  async function togglePlanMode() {
    const session = _planModeSession();
    if (!session || !session.session_id) return;
    if (savingSessionId || (global.S && (global.S.busy || global.S.activeStreamId))) return;

    const sid = session.session_id;
    const previous = session.plan_mode !== false;
    const requested = !previous;
    savingSessionId = sid;
    session.plan_mode = requested;
    syncPlanModeToggle();

    try {
      const data = await global.api('/api/session/update', {
        method: 'POST',
        body: JSON.stringify({ session_id: sid, plan_mode: requested }),
      });
      const current = _planModeSession();
      if (current && current.session_id === sid) {
        const effective = data && data.session && typeof data.session.plan_mode === 'boolean'
          ? data.session.plan_mode
          : requested;
        current.plan_mode = effective;
      }
    } catch (error) {
      const current = _planModeSession();
      if (current && current.session_id === sid && current.plan_mode === requested) {
        current.plan_mode = previous;
      }
      if (typeof global.showToast === 'function') {
        global.showToast('Failed to update Plan Mode: ' + (error && error.message ? error.message : error));
      }
    } finally {
      if (savingSessionId === sid) savingSessionId = null;
      syncPlanModeToggle();
    }
  }

  global.syncPlanModeToggle = syncPlanModeToggle;
  global.togglePlanMode = togglePlanMode;
})(typeof window !== 'undefined' ? window : globalThis);

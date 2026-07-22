"""Focused regression tests for bounded settled-session message windows."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
SESSIONS_JS = (REPO / "static" / "sessions.js").read_text(encoding="utf-8")
MESSAGES_JS = (REPO / "static" / "messages.js").read_text(encoding="utf-8")
UI_JS = (REPO / "static" / "ui.js").read_text(encoding="utf-8")
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is required for settled-window runtime tests")


def _node_driver(body: str, source: Path | None = None) -> dict:
    assert NODE is not None
    source = source or (REPO / "static" / "sessions.js")
    result = subprocess.run(
        [NODE, "-e", body, str(source)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip())


_EXTRACT = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[1], 'utf8');
function extractFunction(name) {
  const markers = [`async function ${name}(`, `function ${name}(`];
  let start = -1;
  for (const marker of markers) {
    start = src.indexOf(marker);
    if (start >= 0) break;
  }
  if (start < 0) throw new Error(`missing ${name}`);
  const brace = src.indexOf('{', start);
  let depth = 0;
  for (let i = brace; i < src.length; i += 1) {
    if (src[i] === '{') depth += 1;
    else if (src[i] === '}') {
      depth -= 1;
      if (depth === 0) return src.slice(start, i + 1);
    }
  }
  throw new Error(`unterminated ${name}`);
}
"""


def test_settled_window_limit_preserves_loaded_width_and_turn_allowance():
    outcome = _node_driver(
        _EXTRACT
        + r"""
const _INITIAL_MSG_LIMIT = 30;
let _messagesTruncated = true;
let loadedRenderable = 30;
const S = {
  messages: Array.from({length: 30}, () => ({role: 'user'})),
  session: {message_count: 100},
};
function _currentLoadedRenderableMessageCount() { return loadedRenderable; }
eval(extractFunction('_settledSessionMessageWindowLimit'));
const results = [
  _settledSessionMessageWindowLimit({message_count: 103}, {}),
  _settledSessionMessageWindowLimit(null, {reserveNewTurn: true}),
];
_messagesTruncated = false;
results.push(_settledSessionMessageWindowLimit({message_count: 1000}, {}));
_messagesTruncated = true;
loadedRenderable = 80;
S.messages = Array.from({length: 80}, () => ({role: 'user'}));
S.session.message_count = 200;
results.push(_settledSessionMessageWindowLimit({message_count: 205}, {}));
console.log(JSON.stringify(results));
"""
    )

    assert outcome == [33, 60, None, 85]


def test_settled_window_fetch_uses_canonical_session_pagination():
    outcome = _node_driver(
        _EXTRACT
        + r"""
const _INITIAL_MSG_LIMIT = 30;
const _MSG_LIMIT_MAX = 500;
let _msgLimitMax = _MSG_LIMIT_MAX;
let _messagesTruncated = true;
let loadedRenderable = 30;
const S = {
  messages: Array.from({length: 30}, () => ({role: 'user'})),
  session: {message_count: 100},
};
function _currentLoadedRenderableMessageCount() { return loadedRenderable; }
const calls = [];
async function api(url, options) {
  calls.push({url, options});
  return {session: {_messages_truncated: true, _messages_offset: 70, messages: []}};
}
eval(extractFunction('_settledSessionMessageWindowLimit'));
eval(extractFunction('_sessionMessageReloadUrl'));
eval(extractFunction('_settledSessionMessageWindowUrl'));
eval(extractFunction('_fetchSettledSessionMessageWindow'));
(async()=>{
const bounded = await _fetchSettledSessionMessageWindow('sid-1', {message_count: 103}, {});
_messagesTruncated = false;
const full = await _fetchSettledSessionMessageWindow('sid-1', {message_count: 1000}, {});
const forced = await _fetchSettledSessionMessageWindow('sid-1', null, {reserveNewTurn: true, forceBounded: true});
const fullRecoveryUrl = _settledSessionMessageWindowUrl('sid-1', null, {reserveNewTurn: true, forceBounded: true});
_messagesTruncated = true;
loadedRenderable = 600;
S.messages = Array.from({length: 600}, () => ({role: 'user'}));
const aboveCeilingRecoveryUrl = _settledSessionMessageWindowUrl('sid-1', null, {reserveNewTurn: true, forceBounded: true});
console.log(JSON.stringify({bounded, full, forced, fullRecoveryUrl, aboveCeilingRecoveryUrl, calls}));
})().catch(err=>{ console.error(err.stack || err); process.exit(1); });
"""
    )

    assert outcome["bounded"]["_messages_offset"] == 70
    assert outcome["full"] is None
    assert outcome["forced"] is None
    assert "msg_limit=" not in outcome["fullRecoveryUrl"]
    assert "msg_limit=" not in outcome["aboveCeilingRecoveryUrl"]
    assert len(outcome["calls"]) == 1
    assert "session_id=sid-1&messages=1&resolve_model=0&msg_limit=33&expand_renderable=1" in outcome["calls"][0]["url"]
    assert outcome["calls"][0]["options"] == {"timeoutMs": 120000}


def test_reload_limit_preserves_expanded_window_without_force_reload_hint():
    outcome = _node_driver(
        _EXTRACT
        + r"""
const _INITIAL_MSG_LIMIT = 30;
let _messagesTruncated = true;
let _sameSessionForceReloadHint = null;
let loadedRenderable = 90;
const S = {
  messages: Array.from({length: 90}, () => ({role: 'user'})),
  session: {session_id: 'sid-1', message_count: 300},
};
function _currentLoadedRenderableMessageCount() { return loadedRenderable; }
eval(extractFunction('_settledSessionMessageWindowLimit'));
eval(extractFunction('_messageReloadLimitForSession'));
console.log(JSON.stringify({
  expanded: _messageReloadLimitForSession('sid-1'),
  initial: (() => {
    loadedRenderable = 30;
    S.messages = Array.from({length: 30}, () => ({role: 'user'}));
    return _messageReloadLimitForSession('sid-1');
  })(),
}));
"""
    )

    assert outcome == {"expanded": 90, "initial": 30}


def test_mutation_reload_uses_renderable_width_not_hidden_tool_rows():
    outcome = _node_driver(
        _EXTRACT
        + r"""
const _INITIAL_MSG_LIMIT = 30;
let _messagesTruncated = true;
let _sameSessionForceReloadHint = null;
let loadedRenderable = 30;
const S = {
  messages: [
    ...Array.from({length: 30}, () => ({role: 'assistant', content: 'visible'})),
    ...Array.from({length: 30}, () => ({role: 'tool', content: 'hidden'})),
  ],
  session: {session_id: 'sid-1', message_count: 300},
};
function _currentLoadedRenderableMessageCount() { return loadedRenderable; }
eval(extractFunction('_captureSameSessionForceReloadHint'));
eval(extractFunction('_messageReloadLimitForSession'));
_captureSameSessionForceReloadHint('sid-1');
const beforeUndo = _messageReloadLimitForSession('sid-1');
S.session.message_count = 299;
const afterUndo = _messageReloadLimitForSession('sid-1');
console.log(JSON.stringify({beforeUndo, afterUndo}));
"""
    )

    assert outcome == {"beforeUndo": 30, "afterUndo": 30}


def test_rotated_done_window_uses_continuation_session_data():
    compact = "".join(MESSAGES_JS.split())
    done_start = MESSAGES_JS.index("source.addEventListener('done'")
    done_end = MESSAGES_JS.index("source.addEventListener('stream_end'", done_start)
    done_body = "".join(MESSAGES_JS[done_start:done_end].split())
    completed_sid_idx = done_body.index("constcompletedSid=completedSession.session_id||activeSid;")
    fetch_idx = done_body.index("_settledDoneWindow=await_fetchSettledSessionMessageWindow")
    assert completed_sid_idx < fetch_idx
    assert "_settledDoneWindow=await_fetchSettledSessionMessageWindow(completedSid,completedSession)" in compact
    assert "_settledDoneWindow=await_fetchSettledSessionMessageWindow(activeSid,completedSession)" not in done_body

    outcome = _node_driver(
        r"""
const activeSid = 'parent-session';
const completedSession = {session_id: 'continuation-session', message_count: 101};
const completedSid = completedSession.session_id || activeSid;
const windows = {
  'parent-session': {
    messages: [{role: 'assistant', content: 'STALE PARENT WINDOW'}],
    tool_calls: [{id: 'parent-tool'}],
    _messages_truncated: true,
    _messages_offset: 70,
  },
  'continuation-session': {
    messages: [{role: 'assistant', content: 'FINAL ANSWER'}],
    tool_calls: [{id: 'continuation-tool'}],
    _messages_truncated: true,
    _messages_offset: 70,
  },
};
async function _fetchSettledSessionMessageWindow(sid) {
  return windows[sid];
}
(async()=>{
  const _settledDoneWindow = await _fetchSettledSessionMessageWindow(completedSid, completedSession);
  const settled = {
    session_id: completedSid,
    messages: _settledDoneWindow.messages,
    tool_calls: _settledDoneWindow.tool_calls,
    _messages_offset: _settledDoneWindow._messages_offset,
  };
  console.log(JSON.stringify(settled));
})().catch(err=>{ console.error(err.stack || err); process.exit(1); });
"""
    )
    assert outcome == {
        "session_id": "continuation-session",
        "messages": [{"role": "assistant", "content": "FINAL ANSWER"}],
        "tool_calls": [{"id": "continuation-tool"}],
        "_messages_offset": 70,
    }


def test_done_and_recovery_paths_do_not_expand_the_render_window():
    compact = "".join(MESSAGES_JS.split())
    assert "_fetchSettledSessionMessageWindow(completedSid,completedSession)" in compact
    assert "_settledSessionMessageWindowUrl(activeSid,null,{reserveNewTurn:true,forceBounded:true})" in compact
    assert "_messagesTruncated=!!session._messages_truncated" in compact
    assert "_messageRenderWindowSize=Math.max(typeof _currentMessageRenderWindowSize" not in compact

    done_start = MESSAGES_JS.index("source.addEventListener('done'")
    done_end = MESSAGES_JS.index("source.addEventListener('stream_end'", done_start)
    done_body = MESSAGES_JS[done_start:done_end]
    assert done_body.index("const _settledDoneInflightSnapshot") < done_body.index("_clearOwnerInflightState({deferSessionStreamResume:true})")
    refresh_idx = done_body.index("await _fetchSettledSessionMessageWindow")
    ownership_idx = done_body.index("if(isActiveSession&&!_isSessionCurrentPane(activeSid)) isActiveSession=false;")
    assert refresh_idx < ownership_idx < done_body.index("S.session=_settledSession")


def test_done_defers_session_stream_resume_until_after_async_settlement():
    done_start = MESSAGES_JS.index("source.addEventListener('done'")
    done_end = MESSAGES_JS.index("source.addEventListener('stream_end'", done_start)
    done_body = MESSAGES_JS[done_start:done_end]
    clear_idx = done_body.index("_clearOwnerInflightState({deferSessionStreamResume:true})")
    fetch_idx = done_body.index("await _fetchSettledSessionMessageWindow")
    idle_idx = done_body.index("_setActivePaneIdleIfOwner()")
    resume_idx = done_body.rindex("_resumeSessionStreamAfterLiveChat(completedSid)")
    assert clear_idx < fetch_idx < idle_idx < resume_idx
    assert "finally" in done_body[idle_idx:resume_idx]


def test_deferred_owner_cleanup_waits_for_settlement_before_resuming_continuation():
    outcome = _node_driver(
        _EXTRACT
        + r"""
const activeSid='parent-session';
const streamId='stream-1';
const S={session:{session_id:activeSid},activeStreamId:streamId};
const INFLIGHT={[activeSid]:{messages:[]}};
const resumes=[];
function _isActiveSession(){return S.session&&S.session.session_id===activeSid;}
function clearInflightState(){}
function _clearActivePaneInflightIfOwner(){S.activeStreamId=null;}
function _resumeSessionStreamAfterLiveChat(sid){resumes.push(sid);}
eval(extractFunction('_clearOwnerInflightState'));

let resolveSettlement;
const settlement=new Promise(resolve=>{resolveSettlement=resolve;});
(async()=>{
  const ownsDeferredResume=_clearOwnerInflightState({deferSessionStreamResume:true});
  const whilePending=resumes.slice();
  resolveSettlement();
  await settlement;
  if(ownsDeferredResume) _resumeSessionStreamAfterLiveChat('continuation-session');
  console.log(JSON.stringify({whilePending,afterSettlement:resumes,ownsDeferredResume}));
})().catch(err=>{console.error(err.stack||err);process.exit(1);});
""",
        REPO / "static" / "messages.js",
    )
    assert outcome == {
        "whilePending": [],
        "afterSettlement": ["continuation-session"],
        "ownsDeferredResume": True,
    }


def test_settled_window_helpers_and_cross_module_callers_are_present():
    assert "function _settledSessionMessageWindowLimit" in SESSIONS_JS
    assert "async function _fetchSettledSessionMessageWindow" in SESSIONS_JS
    assert "_fetchSettledSessionMessageWindow(completedSid,completedSession)" in MESSAGES_JS


def test_reconnect_refresh_uses_a_bounded_session_window():
    start = UI_JS.index("async function refreshSession()")
    end = UI_JS.index("// ── Update banner", start)
    body = "".join(UI_JS[start:end].split())
    assert "_messageReloadLimitForSession(sid)" in body
    assert "_sessionMessageReloadUrl(sid,refreshLimit)" in body
    assert "api(`/api/session?session_id=${encodeURIComponent(S.session.session_id)}`)" not in body

"""Focused regression tests for bounded settled-session message windows."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
SESSIONS_JS = (REPO / "static" / "sessions.js").read_text(encoding="utf-8")
MESSAGES_JS = (REPO / "static" / "messages.js").read_text(encoding="utf-8")
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is required for settled-window runtime tests")


def _node_driver(body: str) -> dict:
    result = subprocess.run(
        [NODE, "-e", body, str(REPO / "static" / "sessions.js")],
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
eval(extractFunction('_settledSessionMessageWindowUrl'));
eval(extractFunction('_fetchSettledSessionMessageWindow'));
(async()=>{
  const bounded = await _fetchSettledSessionMessageWindow('sid-1', {message_count: 103}, {});
  _messagesTruncated = false;
  const full = await _fetchSettledSessionMessageWindow('sid-1', {message_count: 1000}, {});
  console.log(JSON.stringify({bounded, full, calls}));
})().catch(err=>{ console.error(err.stack || err); process.exit(1); });
"""
    )

    assert outcome["bounded"]["_messages_offset"] == 70
    assert outcome["full"] is None
    assert len(outcome["calls"]) == 1
    assert "session_id=sid-1&messages=1&resolve_model=0&msg_limit=33" in outcome["calls"][0]["url"]
    assert outcome["calls"][0]["options"] == {"timeoutMs": 120000}


def test_done_and_recovery_paths_do_not_expand_the_render_window():
    compact = "".join(MESSAGES_JS.split())
    assert "_fetchSettledSessionMessageWindow(activeSid,completedSession)" in compact
    assert "_settledSessionMessageWindowUrl(activeSid,null,{reserveNewTurn:true})" in compact
    assert "_messagesTruncated=!!session._messages_truncated" in compact
    assert "_messageRenderWindowSize=Math.max(typeof _currentMessageRenderWindowSize" not in compact

    done_start = MESSAGES_JS.index("source.addEventListener('done'")
    done_end = MESSAGES_JS.index("source.addEventListener('stream_end'", done_start)
    done_body = MESSAGES_JS[done_start:done_end]
    assert done_body.index("const _settledDoneInflightSnapshot") < done_body.index("_clearOwnerInflightState()")
    refresh_idx = done_body.index("await _fetchSettledSessionMessageWindow")
    ownership_idx = done_body.index("if(isActiveSession&&!_isSessionCurrentPane(activeSid)) isActiveSession=false;")
    assert refresh_idx < ownership_idx < done_body.index("S.session=_settledSession")


def test_settled_window_helpers_and_cross_module_callers_are_present():
    assert "function _settledSessionMessageWindowLimit" in SESSIONS_JS
    assert "async function _fetchSettledSessionMessageWindow" in SESSIONS_JS
    assert "_fetchSettledSessionMessageWindow(activeSid,completedSession)" in MESSAGES_JS

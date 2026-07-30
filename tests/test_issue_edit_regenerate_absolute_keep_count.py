"""Executable edit/regenerate behavior against the real static UI functions."""

import json
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
UI_JS = (REPO / "static" / "ui.js").read_text(encoding="utf-8")


def _function_body(src: str, name: str) -> str:
    needle_async = f"async function {name}"
    needle_sync = f"function {name}"
    start = src.find(needle_async)
    if start < 0:
        start = src.index(needle_sync)
    brace = src.index("{", start)
    depth = 0
    for i in range(brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
    raise AssertionError(f"function {name!r} body not found")


def _run_window_shift(function_name: str) -> dict:
    helpers = "\n".join(
        _function_body(UI_JS, name)
        for name in (
            "_truncateTargetSelector",
            "_truncateTargetValuesEqual",
            "_truncateTargetMatchesMessage",
            "_findLoadedTruncateTargetIndex",
        )
    )
    mutation = _function_body(UI_JS, function_name)
    invocation = (
        "submitEdit(20, 'replacement')"
        if function_name == "submitEdit"
        else "regenerateResponse({closest:()=>({dataset:{msgIdx:'20'}})})"
    )
    script = f"""
const assert = require('assert');
const makeMessages=(start,end)=>Array.from({{length:end-start}},(_,i)=>{{
  const absolute=start+i;
  return {{
    id:'message-'+absolute,
    role:absolute%2===0?'assistant':'user',
    content:'abs-'+absolute,
    timestamp:absolute+0.5,
  }};
}});
const S={{session:{{session_id:'sid-race'}},busy:false,messages:makeMessages(70,100)}};
let _oldestIdx=70;
let _messagesTruncated=true;
let resolveTruncate;
const truncatePending=new Promise(resolve=>{{resolveTruncate=resolve;}});
const calls=[];
let sent=0;
async function api(url,opts){{calls.push({{url,opts}});return truncatePending;}}
function _ensureAllMessagesLoaded(){{throw new Error('paginated window must not full-load');}}
function _deliberateSessionModelPick(){{return null;}}
function _reArmRecoveryPick(){{}}
function renderMessages(){{}}
function msgContent(message){{return String(message&&message.content||'');}}
function setStatus(message){{throw new Error(message);}}
function send(){{sent+=1;return Promise.resolve();}}
function t(key){{return key+': ';}}
const input={{value:''}};
function $(id){{return id==='msg'?input:null;}}
{helpers}
{mutation}
(async()=>{{
  const target=S.messages[20];
  const expectedSelector=_truncateTargetSelector(target);
  assert.deepStrictEqual(expectedSelector,{{
    id:'message-90', role:'assistant', content:'abs-90', timestamp:90.5,
    source:'webui', attachments:[]
  }});
  const pending={invocation};
  await Promise.resolve();
  S.messages=makeMessages(40,100);
  _oldestIdx=40;
  resolveTruncate({{ok:true}});
  await pending;
  const request=JSON.parse(calls[0].opts.body);
  process.stdout.write(JSON.stringify({{
    keepCount:request.keep_count,
    target:request.target_message,
    rows:S.messages.map(message=>message.content),
    sent,
    input:input.value,
  }}));
}})().catch(error=>{{console.error(error.stack||error);process.exit(1);}});
"""
    completed = subprocess.run(
        ["node", "-e", script], check=False, capture_output=True, text=True
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_edit_and_regenerate_send_identity_and_slice_current_window():
    for function_name in ("submitEdit", "regenerateResponse"):
        result = _run_window_shift(function_name)
        assert result["keepCount"] == 90
        assert result["target"]["id"] == "message-90"
        assert result["rows"] == [f"abs-{absolute}" for absolute in range(40, 90)]
        assert result["sent"] == 1


def test_paginated_edit_and_regenerate_do_not_force_full_reload():
    for name in ("submitEdit", "regenerateResponse"):
        body = "".join(_function_body(UI_JS, name).split())
        assert "if(!initialWindowTruncated&&typeof_ensureAllMessagesLoaded==='function')" in body
        assert "_findLoadedTruncateTargetIndex(S.messages,targetSelector)" in body
        assert "target_message:targetSelector" in body

"""Node-harness tests for the compressed-context viewer's phase-2 hydration.

Drives the ACTUAL ``_copyPhase2HasCompressedContext`` in static/sessions.js
via node (same extractFunc pattern as test_renderer_js_behaviour.py), so a
stale Python mirror can't pass while the live JS is broken.

Regression: the messages=1 payload carries the canonical
``has_compressed_context`` derived from the FULL message lists, while the
messages=0 phase-1 fetch derived it from empty arrays (metadata-only load)
and always reported false — without the copy the viewer button stays hidden
on first load of a compressed session.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.resolve()
SESSIONS_JS_PATH = REPO_ROOT / "static" / "sessions.js"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node not on PATH")


_DRIVER_SRC = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');

function extractFunc(name) {
  const re = new RegExp('function\\s+' + name + '\\s*\\(');
  const start = src.search(re);
  if (start < 0) throw new Error(name + ' not found');
  let i = src.indexOf('{', start);
  let depth = 1; i++;
  while (depth > 0 && i < src.length) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') depth--;
    i++;
  }
  return src.slice(start, i);
}
eval(extractFunc('_copyPhase2HasCompressedContext'));

let buf = '';
process.stdin.on('data', c => { buf += c; });
process.stdin.on('end', () => {
  const cases = JSON.parse(buf);
  const out = cases.map(c => _copyPhase2HasCompressedContext(c.target, c.full));
  process.stdout.write(JSON.stringify(out));
});
"""


def _run_helper(cases, driver_path):
    assert NODE is not None  # module is skipped when node is missing
    proc = subprocess.run(
        [NODE, driver_path, str(SESSIONS_JS_PATH)],
        input=json.dumps(cases),
        text=True,
        capture_output=True,
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.fixture
def driver_path(tmp_path):
    p = tmp_path / "context_summary_driver.js"
    p.write_text(_DRIVER_SRC, encoding="utf-8")
    return str(p)


def test_phase2_copies_true_onto_metadata_only_target(driver_path):
    """The messages=0 target said false (empty arrays); phase 2 must fix it."""
    target = {"session_id": "s1", "has_compressed_context": False, "message_count": 0}
    full = {"session_id": "s1", "has_compressed_context": True, "message_count": 42}
    out = _run_helper([{"target": target, "full": full}], driver_path)[0]
    assert out["has_compressed_context"] is True
    assert out["message_count"] == 0  # untouched — only the flag is in scope


def test_phase2_copies_false_onto_compressed_target(driver_path):
    """A session whose markers were pruned must flip back to false."""
    target = {"session_id": "s2", "has_compressed_context": True}
    full = {"session_id": "s2", "has_compressed_context": False}
    out = _run_helper([{"target": target, "full": full}], driver_path)[0]
    assert out["has_compressed_context"] is False


def test_phase2_leaves_flag_alone_when_field_absent(driver_path):
    """Copy-when-present only: an older server response without the field
    must not clobber a value established by an SSE terminal payload."""
    target = {"session_id": "s3", "has_compressed_context": True}
    full = {"session_id": "s3", "message_count": 7}
    out = _run_helper([{"target": target, "full": full}], driver_path)[0]
    assert out["has_compressed_context"] is True


def test_phase2_handles_missing_target_or_full(driver_path):
    out = _run_helper([
        {"target": None, "full": {"has_compressed_context": True}},
        {"target": {"session_id": "s4"}, "full": None},
    ], driver_path)
    assert out[0] is None
    assert out[1] == {"session_id": "s4"}  # target returned unchanged

import json
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import api.models as models
import api.routes as routes
from api.gateway_chat import _apply_gateway_turn_options
from api.models import Session


def test_new_sessions_default_to_plan_mode_on():
    assert Session().plan_mode is True


def test_plan_mode_persists_and_compacts(monkeypatch, tmp_path):
    monkeypatch.setattr("api.models.SESSION_DIR", Path(tmp_path))
    session = Session(plan_mode=False)
    session.save()

    loaded = Session.load(session.session_id)
    assert loaded is not None
    assert loaded.plan_mode is False
    assert loaded.compact()["plan_mode"] is False


def test_legacy_session_without_plan_mode_loads_off(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "SESSION_DIR", tmp_path)
    session_id = "legacy-session"
    (tmp_path / f"{session_id}.json").write_text(
        json.dumps({
            "session_id": session_id,
            "title": "Legacy",
            "messages": [],
            "tool_calls": [],
        }),
        encoding="utf-8",
    )

    loaded = Session.load(session_id)

    assert loaded is not None
    assert loaded.plan_mode is False


def test_session_update_persists_boolean_plan_mode(monkeypatch):
    session = Session(plan_mode=True, workspace="/tmp")
    saved = []
    responses = {}

    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(
        routes,
        "read_body",
        lambda _handler: {"session_id": session.session_id, "plan_mode": False},
    )
    monkeypatch.setattr(routes, "_get_or_materialize_session", lambda _sid: session)
    monkeypatch.setattr(routes, "resolve_trusted_workspace", lambda workspace: workspace)
    monkeypatch.setattr(routes, "set_last_workspace", lambda _workspace: None)
    monkeypatch.setattr(session, "save", lambda: saved.append(session.plan_mode))
    def capture_json(_handler, payload, *args, **kwargs):
        responses["ok"] = payload
        return True

    def capture_bad(_handler, message, status=400):
        responses["bad"] = (message, status)
        return True

    monkeypatch.setattr(routes, "j", capture_json)
    monkeypatch.setattr(routes, "bad", capture_bad)

    assert routes.handle_post(object(), urlparse("/api/session/update")) is True
    assert session.plan_mode is False
    assert saved == [False]
    assert responses["ok"]["session"]["plan_mode"] is False
    assert "bad" not in responses


def test_session_update_rejects_non_boolean_before_loading_session(monkeypatch):
    responses = {}
    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(
        routes,
        "read_body",
        lambda _handler: {"session_id": "session", "plan_mode": "false"},
    )
    monkeypatch.setattr(
        routes,
        "_get_or_materialize_session",
        lambda _sid: (_ for _ in ()).throw(AssertionError("invalid input must not load session")),
    )
    def capture_bad(_handler, message, status=400):
        responses["bad"] = (message, status)
        return True

    monkeypatch.setattr(routes, "bad", capture_bad)

    assert routes.handle_post(object(), urlparse("/api/session/update")) is True
    assert responses["bad"] == ("plan_mode must be a boolean", 400)


def test_start_run_snapshots_session_plan_mode(monkeypatch):
    from api.routes import _start_run

    monkeypatch.setattr("api.runtime_adapter.runtime_adapter_enabled", lambda: False)
    monkeypatch.setattr("api.runtime_adapter.runtime_adapter_runner_enabled", lambda: False)
    captured = {}

    def fake_start(*args, **kwargs):
        captured.update(kwargs)
        return {"stream_id": "stream", "_status": 200}

    monkeypatch.setattr("api.routes._start_chat_stream_for_session", fake_start)
    session = Session(plan_mode=False, model="test/model")

    _start_run(
        session,
        msg="hello",
        attachments=[],
        workspace="/tmp",
        model="test/model",
        model_provider="test",
        normalized_model=True,
        source="webui",
        route="test",
    )

    assert captured["plan_mode"] is False


def test_gateway_request_options_include_boolean_plan_mode():
    assert _apply_gateway_turn_options({}, plan_mode=True) == {"plan_mode": True}
    assert _apply_gateway_turn_options(
        {},
        model_provider="custom",
        reasoning_effort="high",
        plan_mode=False,
    ) == {
        "provider": "custom",
        "reasoning_effort": "high",
        "plan_mode": False,
    }


def test_plan_mode_toggle_runtime_updates_current_session():
    script_path = Path("static/plan_mode.js").resolve()
    driver = r"""
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const classes = new Set();
const attributes = {};
const button = {
  disabled: false,
  title: '',
  classList: {toggle(name, enabled) { enabled ? classes.add(name) : classes.delete(name); }},
  setAttribute(name, value) { attributes[name] = value; },
};
const calls = [];
const toasts = [];
const context = {button, calls, toasts, console};
context.window = context;
context.globalThis = context;
vm.createContext(context);
vm.runInContext(`
  const S = {
    session: {session_id: 'session-1', plan_mode: false},
    busy: false,
    activeStreamId: null,
  };
  const $ = id => id === 'planModeToggle' ? button : null;
  let api = async (path, options) => {
    calls.push({path, options});
    return {session: {plan_mode: false}};
  };
  function showToast(message) { toasts.push(message); }
`, context);
assert.strictEqual(context.S, undefined);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);

(async () => {
  context.syncPlanModeToggle();
  assert.strictEqual(attributes['aria-pressed'], 'false');
  assert.strictEqual(classes.has('active'), false);
  assert.strictEqual(button.disabled, false);

  await context.togglePlanMode();
  assert.strictEqual(calls.length, 1);
  assert.strictEqual(calls[0].path, '/api/session/update');
  assert.deepStrictEqual(JSON.parse(calls[0].options.body), {
    session_id: 'session-1',
    plan_mode: true,
  });
  assert.strictEqual(vm.runInContext('S.session.plan_mode', context), false);
  assert.strictEqual(attributes['aria-pressed'], 'false');
  assert.strictEqual(classes.has('active'), false);
  assert.strictEqual(button.disabled, false);

  vm.runInContext('S.busy = true', context);
  context.syncPlanModeToggle();
  assert.strictEqual(button.disabled, true);
  await context.togglePlanMode();
  assert.strictEqual(calls.length, 1);

  vm.runInContext(`
    S.busy = false;
    api = async () => { throw new Error('network'); };
  `, context);
  await context.togglePlanMode();
  assert.strictEqual(vm.runInContext('S.session.plan_mode', context), false);
  assert.strictEqual(button.disabled, false);
  assert.deepStrictEqual(toasts, ['Failed to update Plan Mode: network']);

  vm.runInContext(`
    api = async () => {
      S.session = {session_id: 'session-2', plan_mode: true};
      return {session: {plan_mode: false}};
    };
  `, context);
  await context.togglePlanMode();
  assert.strictEqual(vm.runInContext('S.session.session_id', context), 'session-2');
  assert.strictEqual(vm.runInContext('S.session.plan_mode', context), true);
})().catch(error => { console.error(error); process.exit(1); });
"""
    result = subprocess.run(
        ["node", "-e", driver, str(script_path)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr

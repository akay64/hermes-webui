"""Behavioral coverage for sidebar ordering, Todos titles, and badges."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
requires_node = pytest.mark.skipif(NODE is None, reason="node not on PATH")


def _src(name: str) -> str:
    return (ROOT / "static" / name).read_text(encoding="utf-8")


def _nav_panels(source: str, container: str) -> list[str]:
    if container == "rail":
        pattern = r'<nav class="rail"[^>]*>(.*?)</nav>'
    else:
        pattern = r'<div class="sidebar-nav"[^>]*>(.*?)</div>'
    match = re.search(pattern, source, re.DOTALL)
    assert match, f"{container} navigation container not found"
    return re.findall(r'data-panel="([^"]+)"', match.group(1))


def test_default_navigation_order_keeps_requested_sequence_and_existing_panels():
    source = _src("index.html")
    requested = [
        "chat",
        "todos",
        "workspaces",
        "skills",
        "memory",
        "profiles",
        "tasks",
        "insights",
    ]

    for container in ("rail", "sidebar-nav"):
        panels = _nav_panels(source, container)
        assert panels[: len(requested)] == requested, container
        assert panels.index("kanban") > panels.index("insights"), container
        assert panels.index("logs") > panels.index("insights"), container
        assert "settings" in panels, container
    assert source.count('class="todos-count-badge"') == 2


def _node_result(script: str, *args: str) -> str:
    node = NODE
    assert node is not None
    result = subprocess.run(
        [node, "-e", script, *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return result.stdout.strip()


@requires_node
def test_todos_titlebar_and_browser_title_follow_session_without_changing_chat_owner():
    script = r'''
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync(process.argv[1], 'utf8');

function extractFn(source, name) {
  const start = source.indexOf(`function ${name}(`);
  if (start < 0) throw new Error(`${name}() not found`);
  let i = source.indexOf('{', start);
  let depth = 0;
  let quote = null;
  let escaped = false;
  let lineComment = false;
  let blockComment = false;
  for (; i < source.length; i++) {
    const ch = source[i];
    const next = source[i + 1] || '';
    if (lineComment) { if (ch === '\n') lineComment = false; continue; }
    if (blockComment) { if (ch === '*' && next === '/') { blockComment = false; i++; } continue; }
    if (quote) {
      if (escaped) escaped = false;
      else if (ch === '\\') escaped = true;
      else if (ch === quote) quote = null;
      continue;
    }
    if (ch === '/' && next === '/') { lineComment = true; i++; continue; }
    if (ch === '/' && next === '*') { blockComment = true; i++; continue; }
    if (ch === '\'' || ch === '"' || ch === '`') { quote = ch; continue; }
    if (ch === '{') depth++;
    if (ch === '}') { depth--; if (depth === 0) return source.slice(start, i + 1); }
  }
  throw new Error(`${name}() did not terminate`);
}

const titleEl = {textContent:'', ondblclick:null};
const subEl = {textContent:'', hidden:true};
const context = {
  _currentPanel: 'todos',
  _renamingAppTitlebar: false,
  S: {session:{title:'Project Notes'}, messages:[]},
  document: {
    title: '',
    getElementById(id) {
      if (id === 'appTitlebarTitle') return titleEl;
      if (id === 'appTitlebarSub') return subEl;
      return null;
    },
    querySelector() { return null; },
  },
  window: {},
  t(key) { return key === 'tab_todos' ? 'Todos' : 'Untitled'; },
  assistantDisplayName() { return 'Hermes'; },
};
vm.createContext(context);
vm.runInContext(`
const APP_TITLEBAR_KEYS = {todos:'tab_todos', chat:'tab_chat'};
${extractFn(src, 'syncAppTitlebar')}
this.syncAppTitlebar = syncAppTitlebar;
`, context);
function assert(cond, msg) { if (!cond) throw new Error(msg); }

context.syncAppTitlebar();
assert(titleEl.textContent === 'Project Notes - Todos', 'active Todos titlebar mismatch');
assert(context.document.title === 'Project Notes - Todos — Hermes', 'active Todos browser title mismatch');

context.S.session = {title:'Renamed session'};
context.syncAppTitlebar();
assert(titleEl.textContent === 'Renamed session - Todos', 'renamed Todos titlebar mismatch');
assert(context.document.title === 'Renamed session - Todos — Hermes', 'renamed Todos browser title mismatch');

context.S.session = null;
context.syncAppTitlebar();
assert(titleEl.textContent === 'Todos', 'sessionless Todos titlebar mismatch');
assert(context.document.title === 'Todos — Hermes', 'sessionless Todos browser title mismatch');

context._currentPanel = 'chat';
context.S.session = {title:'Chat session'};
context.document.title = 'Chat session — Hermes';
context.syncAppTitlebar();
assert(titleEl.textContent === 'Chat session', 'Chat titlebar behavior changed');
assert(context.document.title === 'Chat session — Hermes', 'Chat browser title owner changed');
'''
    _node_result(script, str(ROOT / "static" / "panels.js"))


@requires_node
def test_todo_snapshot_badges_count_normalized_items_and_refresh_when_inactive():
    panels = _src("panels.js")
    ui = _src("ui.js")
    script = r'''
const fs = require('fs');
const vm = require('vm');
const panelsSrc = fs.readFileSync(process.argv[1], 'utf8');
const uiSrc = fs.readFileSync(process.argv[2], 'utf8');

function extractFn(source, name) {
  const start = source.indexOf(`function ${name}(`);
  if (start < 0) throw new Error(`${name}() not found`);
  let i = source.indexOf('{', start);
  let depth = 0;
  let quote = null;
  let escaped = false;
  let lineComment = false;
  let blockComment = false;
  for (; i < source.length; i++) {
    const ch = source[i];
    const next = source[i + 1] || '';
    if (lineComment) { if (ch === '\n') lineComment = false; continue; }
    if (blockComment) { if (ch === '*' && next === '/') { blockComment = false; i++; } continue; }
    if (quote) {
      if (escaped) escaped = false;
      else if (ch === '\\') escaped = true;
      else if (ch === quote) quote = null;
      continue;
    }
    if (ch === '/' && next === '/') { lineComment = true; i++; continue; }
    if (ch === '/' && next === '*') { blockComment = true; i++; continue; }
    if (ch === '\'' || ch === '"' || ch === '`') { quote = ch; continue; }
    if (ch === '{') depth++;
    if (ch === '}') { depth--; if (depth === 0) return source.slice(start, i + 1); }
  }
  throw new Error(`${name}() did not terminate`);
}

function makeBadge() {
  return {textContent:'', style:{display:''}, setAttribute(){}, getAttribute(){return null;}};
}
function makeButton() {
  const attrs = {'aria-label':'Todos'};
  return {
    dataset:{label:'Todos'},
    getAttribute(name) { return Object.prototype.hasOwnProperty.call(attrs, name) ? attrs[name] : null; },
    setAttribute(name, value) { attrs[name] = String(value); },
    aria() { return attrs['aria-label']; },
  };
}
const badges = [makeBadge(), makeBadge()];
const buttons = [makeButton(), makeButton()];
const panel = {classList:{contains(name){return false;}}};
const context = {
  S: {session:{messages:[]}, todos:[], todoStateMeta:{}},
  document: {
    querySelectorAll(selector) {
      if (selector === '[data-panel="todos"] .todos-count-badge') return badges;
      if (selector === '[data-panel="todos"]') return buttons;
      return [];
    },
    getElementById(id) { return id === 'panelTodos' ? panel : null; },
  },
  requestAnimationFrame(callback) { callback(); return 0; },
  loadTodos() {},
  _refreshWorkspacePanelTodos() {},
};
vm.createContext(context);
vm.runInContext(`
const TODO_STATUS_RENDERING = {pending:{}, in_progress:{}, completed:{}, cancelled:{}};
${extractFn(uiSrc, 'todoStatusKey')}
${extractFn(panelsSrc, '_legacyTodosFromMessages')}
${extractFn(panelsSrc, '_getCurrentTodosSnapshot')}
${extractFn(uiSrc, '_countUnfinishedTodos')}
${extractFn(uiSrc, '_updateTodosBadges')}
let _todosRenderRafId = 0;
function _todosPanelIsActive() { return false; }
${extractFn(uiSrc, 'scheduleTodosRefresh')}
this.api = {getCurrentTodos:_getCurrentTodosSnapshot, updateBadges:_updateTodosBadges, schedule:scheduleTodosRefresh};
`, context);
function assert(cond, msg) { if (!cond) throw new Error(msg); }
function assertBadges(text, display, label) {
  for (const badge of badges) {
    assert(badge.textContent === text, `badge text mismatch: expected ${text}, got ${badge.textContent}`);
    assert(badge.style.display === display, `badge display mismatch: expected ${display}, got ${badge.style.display}`);
  }
  for (const button of buttons) assert(button.aria() === label, 'button aria label mismatch');
}

context.S.todos = [
  {id:'a', status:'pending'},
  {id:'b', status:'in_progress'},
  {id:'c'},
  {id:'d', status:'unknown'},
  {id:'e', status:'completed'},
  {id:'f', status:'cancelled'},
];
context.api.updateBadges();
assertBadges('4', 'inline-flex', 'Todos (4)');

context.S.todos = [{id:'e', status:'completed'}, {id:'f', status:'cancelled'}];
context.api.updateBadges();
assertBadges('', 'none', 'Todos');

context.S.todos = [{id:'legacy', status:'pending'}];
context.S.todoStateMeta = null;
context.S.session.messages = [{role:'tool', content:JSON.stringify({todos:[{id:'legacy', status:'in_progress'}]})}];
assert(context.api.getCurrentTodos()[0].id === 'legacy', 'legacy snapshot was not selected');
context.api.updateBadges();
assertBadges('1', 'inline-flex', 'Todos (1)');

context.S.todos = [{id:'live', status:'pending'}];
context.S.todoStateMeta = {};
context.api.schedule();
assertBadges('1', 'inline-flex', 'Todos (1)');

context.S.todos = [];
context.S.todoStateMeta = null;
context.S.session = {messages:[]};
context.api.schedule();
assertBadges('', 'none', 'Todos');
'''
    _node_result(script, str(ROOT / "static" / "panels.js"), str(ROOT / "static" / "ui.js"))


def test_load_todos_and_badge_share_the_current_snapshot_resolver():
    panels = _src("panels.js")
    load_start = panels.find("function loadTodos()")
    legacy_start = panels.find("function _legacyTodosFromMessages()")
    assert load_start >= 0 and legacy_start > load_start
    load_block = panels[load_start:legacy_start]
    assert "_getCurrentTodosSnapshot()" in load_block
    assert "_legacyTodosFromMessages()" not in load_block

    ui = _src("ui.js")
    schedule_start = ui.find("function scheduleTodosRefresh()")
    reset_start = ui.find("function _resetTodosRenderCache()", schedule_start)
    schedule_block = ui[schedule_start:reset_start]
    assert "_updateTodosBadges()" in schedule_block

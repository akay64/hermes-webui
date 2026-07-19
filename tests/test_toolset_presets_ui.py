"""Static UI contracts for the Toolset Presets composer workflow."""

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
UI = (REPO / "static" / "ui.js").read_text(encoding="utf-8")
SESSIONS = (REPO / "static" / "sessions.js").read_text(encoding="utf-8")
HTML = (REPO / "static" / "index.html").read_text(encoding="utf-8")
CSS = (REPO / "static" / "style.css").read_text(encoding="utf-8")
BOOT = (REPO / "static" / "boot.js").read_text(encoding="utf-8")


def test_presets_use_profile_scoped_backend_api_not_browser_storage():
    assert "api('/api/toolset-presets')" in UI
    assert "api('/api/toolset-presets/default'" in UI
    assert "localStorage" not in UI[UI.index("// ── Session toolsets chip") : UI.index("function _syncMobileComposerConfigButton")]


def test_mid_conversation_cache_warning_has_both_actions():
    assert "Changing tools rebuilds this conversation’s agent and may reduce prompt-cache reuse." in UI
    assert "Start new chat with this preset" in UI
    assert "Change this conversation anyway" in UI
    assert "newSession(true, { enabled_toolsets: change.toolsets })" in UI


def test_mobile_configuration_panel_exposes_toolsets():
    assert 'id="composerMobileToolsetsAction"' in HTML
    assert 'id="composerMobileToolsetsLabel"' in HTML
    assert ".composer-toolsets-dropdown" in CSS
    assert "mobileAction = $('composerMobileToolsetsAction')" in UI


def test_new_session_preserves_omitted_null_and_exact_list_states():
    assert "Object.prototype.hasOwnProperty.call(options,'enabled_toolsets')" in SESSIONS
    assert "S._pendingSessionToolsetsExplicit" in SESSIONS
    assert "reqBody.enabled_toolsets=S._pendingSessionToolsets" in SESSIONS


def test_blank_composer_hydrates_default_from_boot_profile_response():
    assert "toolsetPresets: p.toolset_presets || null" in BOOT
    assert "window.hydrateToolsetPresets(activeProfileState.toolsetPresets)" in BOOT
    assert "function hydrateToolsetPresets(payload)" in UI


def test_profile_switch_reloads_presets_without_expanding_switch_response_contract():
    panels = (REPO / "static" / "panels.js").read_text(encoding="utf-8")
    assert "window.reloadToolsetPresets = function()" in UI
    assert "window.reloadToolsetPresets()" in panels
    assert "data.toolset_presets" not in panels

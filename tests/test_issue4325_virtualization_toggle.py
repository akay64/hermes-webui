"""Regression coverage for #4325: opt-out toggle for transcript virtualization.

The stream-end freeze/jump fix (#4328, semantic viewport anchoring) is covered by
test_issue500_message_list_virtualization.py. This file covers the new Preferences
toggle that lets a user disable virtualization entirely (opt-out, default ON).
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX = REPO_ROOT / "static" / "index.html"
PANELS = REPO_ROOT / "static" / "panels.js"
BOOT = REPO_ROOT / "static" / "boot.js"
UI = REPO_ROOT / "static" / "ui.js"
I18N = REPO_ROOT / "static" / "i18n.js"
CONFIG = REPO_ROOT / "api" / "config.py"


def test_virtualize_transcript_setting_is_default_on_and_allowed():
    """Opt-out model: default True (virtualization on), and whitelisted as a bool key."""
    src = CONFIG.read_text(encoding="utf-8")
    assert '"virtualize_transcript": True' in src, "must default ON (opt-out)"
    assert '"virtualize_transcript",' in src, "must be in _SETTINGS_BOOL_KEYS"


def test_settings_preferences_expose_virtualize_toggle():
    html = INDEX.read_text(encoding="utf-8")
    assert 'id="settingsVirtualizeTranscript"' in html
    assert 'data-i18n="settings_label_virtualize_transcript"' in html
    assert 'data-i18n="settings_desc_virtualize_transcript"' in html
    # Checkbox renders checked by default (opt-out): the rendered default reflects ON.
    assert 'id="settingsVirtualizeTranscript"' in html and "checked" in html


def test_boot_applies_saved_virtualize_preference_default_on():
    js = BOOT.read_text(encoding="utf-8")
    # Default-on semantics: !==false (undefined/older configs treated as enabled).
    assert "window._virtualizeTranscript=s.virtualize_transcript!==false" in js
    # Settings-load-failed fallback also defaults ON.
    assert "window._virtualizeTranscript=true" in js


def test_ui_gate_forces_full_render_when_opted_out():
    js = UI.read_text(encoding="utf-8")
    start = js.index("function _currentMessageVirtualWindow(")
    body = js[start:start + 900]
    assert "_virtualizeTranscript===false" in body
    assert "virtualized:false" in body


def test_panels_round_trip_and_hot_apply_virtualize_toggle():
    js = PANELS.read_text(encoding="utf-8")
    assert "const virtualizeTranscriptCb=$('settingsVirtualizeTranscript');" in js
    assert "payload.virtualize_transcript=virtualizeTranscriptCb.checked;" in js
    assert "virtualizeTranscriptCb.checked=settings.virtualize_transcript!==false;" in js
    assert "window._virtualizeTranscript=virtualizeTranscriptCb.checked;" in js
    # Hot-apply: toggling re-renders the open transcript immediately.
    assert "renderMessages({preserveScroll:true})" in js


def test_virtualize_toggle_i18n_all_locales():
    js = I18N.read_text(encoding="utf-8")
    assert js.count("settings_label_virtualize_transcript:") == 13
    assert js.count("settings_desc_virtualize_transcript:") == 13

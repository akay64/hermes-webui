"""Contracts for profile-scoped Toolset Presets."""

from __future__ import annotations

import json

import pytest

from api import toolset_presets as presets
from api import routes


def test_json_round_trip_deduplicates_and_uses_profile_webui_dir(tmp_path):
    created = presets.create_preset(
        tmp_path, label="  Everyday  ", toolsets=["terminal", "web", "terminal"]
    )

    assert created["label"] == "Everyday"
    assert created["toolsets"] == ["terminal", "web"]
    assert presets.store_path(tmp_path) == tmp_path / "webui" / "toolset_presets.json"
    on_disk = json.loads(presets.store_path(tmp_path).read_text(encoding="utf-8"))
    assert on_disk["version"] == presets.SCHEMA_VERSION
    assert presets.load_store(tmp_path)["presets"][0] == created


def test_profiles_have_independent_stores(tmp_path):
    first = tmp_path / "profile-a"
    second = tmp_path / "profile-b"
    presets.create_preset(first, label="A", toolsets=["terminal"])

    assert len(presets.load_store(first)["presets"]) == 1
    assert presets.load_store(second) == {
        "version": presets.SCHEMA_VERSION,
        "default_preset_id": None,
        "presets": [],
    }


def test_crud_default_and_default_deletion(tmp_path):
    created = presets.create_preset(tmp_path, label="Everyday", toolsets=["terminal"])
    presets.set_default(tmp_path, created["id"])
    updated = presets.update_preset(
        tmp_path, created["id"], label="Daily", toolsets=["terminal", "web"]
    )

    assert updated["created_at"] == created["created_at"]
    assert presets.default_toolsets(tmp_path) == ["terminal", "web"]
    result = presets.delete_preset(tmp_path, created["id"])
    assert result["default_preset_id"] is None
    assert presets.default_toolsets(tmp_path) is None


def test_atomic_replace_failure_preserves_previous_file(tmp_path, monkeypatch):
    created = presets.create_preset(tmp_path, label="Everyday", toolsets=["terminal"])
    original = presets.store_path(tmp_path).read_bytes()

    def fail_replace(_source, _target):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(presets.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        presets.update_preset(
            tmp_path, created["id"], label="Changed", toolsets=["web"]
        )

    assert presets.store_path(tmp_path).read_bytes() == original


@pytest.mark.parametrize(
    "payload",
    [
        {"version": 999, "default_preset_id": None, "presets": []},
        {"version": 1, "default_preset_id": "missing", "presets": []},
        {"version": 1, "default_preset_id": None, "presets": [{"id": "bad"}]},
    ],
)
def test_malformed_store_fails_closed(tmp_path, payload):
    path = presets.store_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(presets.ToolsetPresetError):
        presets.load_store(tmp_path)


def test_session_copy_does_not_track_later_preset_edits(tmp_path):
    created = presets.create_preset(tmp_path, label="Everyday", toolsets=["terminal"])
    presets.set_default(tmp_path, created["id"])
    session_value = presets.default_toolsets(tmp_path)
    presets.update_preset(tmp_path, created["id"], label="Everyday", toolsets=["web"])

    assert session_value == ["terminal"]
    assert presets.default_toolsets(tmp_path) == ["web"]


def test_disabled_and_unknown_entries_need_attention(monkeypatch):
    catalog = {
        "toolsets": [
            {"name": "terminal", "kind": "builtin", "available": True},
            {"name": "ticktick", "kind": "mcp", "available": False, "status": "disabled"},
        ]
    }
    annotated = routes._annotate_toolset_preset(
        {"id": "morning", "toolsets": ["terminal", "ticktick", "missing"]},
        catalog,
    )

    assert annotated["status"] == "needs_attention"
    assert annotated["unknown_toolsets"] == ["missing"]
    assert annotated["unavailable_toolsets"] == [
        {"name": "ticktick", "kind": "mcp", "status": "disabled"}
    ]


def test_ticktick_is_one_server_toolset_not_a_list_of_operations(tmp_path, monkeypatch):
    config = {
        "mcp_servers": {
            "ticktick": {
                "enabled": True,
                "url": "https://mcp.ticktick.com",
                "tools": {"exclude": ["search", "list_projects"]},
            }
        }
    }
    monkeypatch.setattr(routes, "_toolset_preset_home", lambda profile=None: tmp_path)
    monkeypatch.setattr(routes, "get_config_for_profile_home", lambda home: config)
    monkeypatch.setattr(routes, "_resolve_cli_toolsets", lambda cfg: ["terminal", "ticktick"])
    monkeypatch.setattr(
        routes,
        "_mcp_runtime_status_by_name",
        lambda: {"ticktick": {"connected": True, "tools": 7}},
    )

    catalog = routes._toolset_catalog_payload()
    ticktick = [item for item in catalog["mcp"] if item["name"] == "ticktick"]

    assert ticktick == [{
        "name": "ticktick",
        "kind": "mcp",
        "enabled": True,
        "available": True,
        "registered": True,
        "status": "active",
        "tool_count": 7,
    }]
    assert not any(item["name"] in {"search", "list_projects"} for item in catalog["toolsets"])


def test_specialized_direct_session_constructor_does_not_inherit_saved_default(tmp_path):
    from api.models import new_session

    created = presets.create_preset(tmp_path, label="Everyday", toolsets=["terminal"])
    presets.set_default(tmp_path, created["id"])
    session = new_session(workspace=tmp_path)

    assert session.enabled_toolsets is None

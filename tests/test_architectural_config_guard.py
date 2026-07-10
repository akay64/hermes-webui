"""Test the architectural guard in _save_yaml_config_file.

Verifies that ``${VAR}`` environment variable references in config.yaml survive
a read-modify-write cycle through a save path that reads expanded config.
"""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _prepare_config_path(tmp_path, monkeypatch):
    """Isolate config file access to a temp directory."""
    import api.config as config

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(config, "_get_config_path", lambda: config_path)
    monkeypatch.setattr(config, "reload_config", lambda: None)

    # Write a starting config with a ${VAR} reference
    env_var = "TICKTICK_MCP_KEY"
    monkeypatch.setenv(env_var, "tp_real_secret_value")
    monkeypatch.setenv(
        "UNRELATED_DASHBOARD_KEY",
        "dash_secret_value",
    )

    config_path.write_text(
        "\n".join([
            "model:",
            "  default: deepseek-v4-flash",
            "mcp_servers:",
            "  ticktick:",
            "    url: https://mcp.ticktick.com",
            "    headers:",
            "      Authorization: Bearer ${TICKTICK_MCP_KEY}",
            "    timeout: 30",
            "",
        ]),
        encoding="utf-8",
    )

    # Wipe any in-memory config caches so the raw file is read fresh
    config._yaml_file_cache.clear()
    _orig_cfg_cache = config._cfg_cache
    config._cfg_cache = None

    yield config_path

    config._cfg_cache = _orig_cfg_cache


class TestArchitecturalConfigGuard:
    """``_save_yaml_config_file`` preserves ``${VAR}`` references in
    untouched keys when the caller passes an expanded dict."""

    def _expanded_dict(self, config_path: Path) -> dict:
        """Simulate a buggy caller that loads expanded config via
        ``get_config()`` — mutates ONE key — and passes the full expanded
        dict to ``_save_yaml_config_file()``."""

        import api.config as config

        # Load via the expanded path (like get_config does)
        expanded = config._load_yaml_config_file(config_path)
        # Mutate exactly one key (like set_reasoning_effort does)
        expanded.setdefault("agent", {})["reasoning_effort"] = "high"
        return expanded

    def test_env_var_survives_save_via_expanded_loader(self, _prepare_config_path):
        """A ``${VAR}`` in an unrelated section is preserved when saving
        a mutated expanded config."""
        import api.config as config

        config_path = _prepare_config_path
        expanded = self._expanded_dict(config_path)

        # This is the call under test
        config._save_yaml_config_file(config_path, expanded)

        # Read back raw and verify the ${VAR} reference survived
        saved = config._load_yaml_config_file_raw(config_path, _copy=False)
        assert isinstance(saved, dict), f"Expected dict, got {type(saved)}"

        mcp = saved.get("mcp_servers", {})
        ticktick = mcp.get("ticktick", {})
        headers = ticktick.get("headers", {})
        auth = headers.get("Authorization", "")

        assert (
            "${TICKTICK_MCP_KEY}" in auth
        ), f"Expected ${{TICKTICK_MCP_KEY}} in Authorization header, got: {auth!r}"

    def test_mutated_key_is_written(self, _prepare_config_path):
        """A key the caller intentionally changed is still written."""
        import api.config as config

        config_path = _prepare_config_path
        expanded = self._expanded_dict(config_path)

        config._save_yaml_config_file(config_path, expanded)

        saved = config._load_yaml_config_file_raw(config_path, _copy=False)
        agent = saved.get("agent", {})
        assert agent.get("reasoning_effort") == "high", (
            f"Expected reasoning_effort=high, got {agent.get('reasoning_effort')!r}"
        )

    def test_nested_env_var_survives(self, _prepare_config_path):
        """A ``${VAR}`` nested inside a dict section that wasn't touched
        is preserved."""

        import api.config as config

        config_path = _prepare_config_path
        expanded = self._expanded_dict(config_path)

        config._save_yaml_config_file(config_path, expanded)

        saved = config._load_yaml_config_file_raw(config_path, _copy=False)
        ticktick = saved.get("mcp_servers", {}).get("ticktick", {})
        assert ticktick.get("url") == "https://mcp.ticktick.com"
        assert ticktick.get("timeout") == 30

    def test_list_env_var_survives_item_deleted_before_it(self, tmp_path, monkeypatch):
        """A ``${VAR}`` in a list item is preserved when an item before it
        is deleted — tests that the merge matches by value, not by index."""
        import api.config as config

        # Simulate: raw config has ["${TOKEN_A}", "${TOKEN_B}"] → expanded
        # to ["secret_a", "secret_b"]. Caller deletes the first item and saves
        # ["secret_b"]. The old index-based merge would compare value[0]
        # "secret_b" against expanded_raw[0] "secret_a" — different → write
        # literal. The fix matches by value across the full list, finds
        # "secret_b" at the old index 1, and preserves raw[1] = "${TOKEN_B}".
        monkeypatch.setenv("TOKEN_A", "secret_a")
        monkeypatch.setenv("TOKEN_B", "secret_b")

        config_path = tmp_path / "config.yaml"
        monkeypatch.setattr(config, "_get_config_path", lambda: config_path)
        monkeypatch.setattr(config, "reload_config", lambda: None)

        config_path.write_text(
            "\n".join([
                "models:",
                "  - ${TOKEN_A}",
                "  - ${TOKEN_B}",
                "agent:",
                "  reasoning_effort: medium",
            ]),
            encoding="utf-8",
        )

        # Wipe caches so we read fresh
        config._yaml_file_cache.clear()
        _orig_cache = config._cfg_cache
        config._cfg_cache = None

        # Load expanded, delete first item, save
        expanded = config._load_yaml_config_file(config_path)
        expanded["models"] = [expanded["models"][1]]  # keep only the second
        config._save_yaml_config_file(config_path, expanded)

        config._cfg_cache = _orig_cache

        # Read back raw — ${TOKEN_B} must survive at position 0
        saved = config._load_yaml_config_file_raw(config_path, _copy=False)
        models = saved.get("models", [])
        assert len(models) == 1, f"Expected 1 model, got {len(models)}: {models}"
        assert "${TOKEN_B}" in str(models[0]), (
            f"Expected ${{TOKEN_B}} at shifted position, got: {models[0]!r}"
        )

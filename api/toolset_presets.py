"""Profile-scoped persistence for saved WebUI toolset presets.

The store is intentionally independent from session persistence.  A preset is
configuration owned by one Hermes profile; sessions copy its toolset list when
they are created and never retain a live reference to this file.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
MAX_PRESETS = 50
MAX_LABEL_LENGTH = 80
MAX_TOOLSETS_PER_PRESET = 64
MAX_TOOLSET_NAME_LENGTH = 128

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_LOCK = threading.RLock()


class ToolsetPresetError(ValueError):
    """Raised when the preset store or a requested mutation is invalid."""


def store_path(hermes_home: str | os.PathLike[str]) -> Path:
    return Path(hermes_home) / "webui" / "toolset_presets.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _empty_store() -> dict[str, Any]:
    return {"version": SCHEMA_VERSION, "default_preset_id": None, "presets": []}


def normalize_toolsets(value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ToolsetPresetError("toolsets must be a non-empty list")
    if len(value) > MAX_TOOLSETS_PER_PRESET:
        raise ToolsetPresetError(f"toolsets may contain at most {MAX_TOOLSETS_PER_PRESET} entries")
    result: list[str] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, str):
            raise ToolsetPresetError("each toolset must be a string")
        name = raw.strip()
        if not name:
            raise ToolsetPresetError("each toolset must be a non-empty string")
        if len(name) > MAX_TOOLSET_NAME_LENGTH:
            raise ToolsetPresetError(
                f"toolset names may contain at most {MAX_TOOLSET_NAME_LENGTH} characters"
            )
        if name not in seen:
            seen.add(name)
            result.append(name)
    return result


def _normalize_label(value: Any) -> str:
    if not isinstance(value, str):
        raise ToolsetPresetError("label must be a string")
    label = value.strip()
    if not label:
        raise ToolsetPresetError("label must not be empty")
    if len(label) > MAX_LABEL_LENGTH:
        raise ToolsetPresetError(f"label may contain at most {MAX_LABEL_LENGTH} characters")
    return label


def _validate_preset(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ToolsetPresetError("each preset must be an object")
    preset_id = raw.get("id")
    if not isinstance(preset_id, str) or not _ID_RE.fullmatch(preset_id):
        raise ToolsetPresetError("preset id is invalid")
    created_at = raw.get("created_at")
    updated_at = raw.get("updated_at")
    if not isinstance(created_at, str) or not created_at:
        raise ToolsetPresetError("preset created_at is invalid")
    if not isinstance(updated_at, str) or not updated_at:
        raise ToolsetPresetError("preset updated_at is invalid")
    return {
        "id": preset_id,
        "label": _normalize_label(raw.get("label")),
        "toolsets": normalize_toolsets(raw.get("toolsets")),
        "created_at": created_at,
        "updated_at": updated_at,
    }


def _validate_store(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ToolsetPresetError("toolset preset file must contain an object")
    if raw.get("version") != SCHEMA_VERSION:
        raise ToolsetPresetError("unsupported toolset preset schema version")
    presets_raw = raw.get("presets")
    if not isinstance(presets_raw, list):
        raise ToolsetPresetError("presets must be a list")
    if len(presets_raw) > MAX_PRESETS:
        raise ToolsetPresetError(f"preset file exceeds the {MAX_PRESETS}-preset limit")
    presets = [_validate_preset(item) for item in presets_raw]
    ids = [item["id"] for item in presets]
    if len(ids) != len(set(ids)):
        raise ToolsetPresetError("preset ids must be unique")
    default_id = raw.get("default_preset_id")
    if default_id is not None and default_id not in set(ids):
        raise ToolsetPresetError("default_preset_id does not reference an existing preset")
    return {"version": SCHEMA_VERSION, "default_preset_id": default_id, "presets": presets}


def load_store(hermes_home: str | os.PathLike[str]) -> dict[str, Any]:
    path = store_path(hermes_home)
    with _LOCK:
        if not path.exists():
            return _empty_store()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ToolsetPresetError(f"could not read toolset presets: {exc}") from exc
        return _validate_store(raw)


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def save_store(hermes_home: str | os.PathLike[str], payload: dict[str, Any]) -> dict[str, Any]:
    normalized = _validate_store(payload)
    with _LOCK:
        _atomic_write(store_path(hermes_home), normalized)
    return normalized


def create_preset(hermes_home: str | os.PathLike[str], *, label: Any, toolsets: Any) -> dict[str, Any]:
    with _LOCK:
        data = load_store(hermes_home)
        if len(data["presets"]) >= MAX_PRESETS:
            raise ToolsetPresetError(f"at most {MAX_PRESETS} presets may be saved")
        timestamp = _now()
        preset = {
            "id": uuid.uuid4().hex[:16],
            "label": _normalize_label(label),
            "toolsets": normalize_toolsets(toolsets),
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        data["presets"].append(preset)
        save_store(hermes_home, data)
        return dict(preset, toolsets=list(preset["toolsets"]))


def update_preset(
    hermes_home: str | os.PathLike[str], preset_id: str, *, label: Any, toolsets: Any
) -> dict[str, Any]:
    with _LOCK:
        data = load_store(hermes_home)
        for preset in data["presets"]:
            if preset["id"] == preset_id:
                preset["label"] = _normalize_label(label)
                preset["toolsets"] = normalize_toolsets(toolsets)
                preset["updated_at"] = _now()
                save_store(hermes_home, data)
                return dict(preset, toolsets=list(preset["toolsets"]))
        raise KeyError(preset_id)


def delete_preset(hermes_home: str | os.PathLike[str], preset_id: str) -> dict[str, Any]:
    with _LOCK:
        data = load_store(hermes_home)
        remaining = [preset for preset in data["presets"] if preset["id"] != preset_id]
        if len(remaining) == len(data["presets"]):
            raise KeyError(preset_id)
        data["presets"] = remaining
        if data["default_preset_id"] == preset_id:
            data["default_preset_id"] = None
        return save_store(hermes_home, data)


def set_default(hermes_home: str | os.PathLike[str], preset_id: Any) -> dict[str, Any]:
    with _LOCK:
        data = load_store(hermes_home)
        if preset_id is not None:
            if not isinstance(preset_id, str) or preset_id not in {
                preset["id"] for preset in data["presets"]
            }:
                raise KeyError(preset_id)
        data["default_preset_id"] = preset_id
        return save_store(hermes_home, data)


def default_toolsets(hermes_home: str | os.PathLike[str]) -> list[str] | None:
    data = load_store(hermes_home)
    default_id = data["default_preset_id"]
    if default_id is None:
        return None
    for preset in data["presets"]:
        if preset["id"] == default_id:
            return list(preset["toolsets"])
    raise ToolsetPresetError("default preset is missing")

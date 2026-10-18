"""Settings stores for the Copper Thief dialog.

Two levels are supported:

* Global settings in the KiCad user configuration directory (last used values
  and a fallback when the board has never been saved).
* A project configuration file next to the board, containing any number of
  named parameter presets: ``<board>.copper_thief.json``.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


def kicad_config_dir() -> Path:
    version = "10.0"
    try:
        import pcbnew

        version = pcbnew.GetMajorMinorVersion()
    except Exception:
        pass

    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "kicad" / version
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Preferences" / "kicad" / version
    return Path.home() / ".config" / "kicad" / version


def default_settings_path() -> Path:
    override = os.environ.get("COPPER_THIEF_SETTINGS")
    if override:
        return Path(override)
    return kicad_config_dir() / "copper_thief.json"


def _read_json(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_json(path: Path, data: dict) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix="copper_thief_", suffix=".json", dir=path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        return True
    except Exception:
        return False


def load_settings(path: Path | None = None) -> dict:
    path = Path(path) if path is not None else default_settings_path()
    return _read_json(path)


def save_settings(data: dict, path: Path | None = None) -> bool:
    path = Path(path) if path is not None else default_settings_path()
    return _write_json(path, data)


# ---------------------------------------------------------------------------
# Project configuration file
# ---------------------------------------------------------------------------


def project_config_path(board) -> Path | None:
    """Return ``<board>.copper_thief.json`` next to the board file."""
    try:
        filename = board.GetFileName()
    except Exception:
        return None
    if not filename:
        return None
    try:
        path = Path(str(filename))
        if not path.name:
            return None
        return path.with_suffix(".copper_thief.json")
    except Exception:
        return None


def load_project_config(path: Path | None) -> dict:
    """Load and validate a project configuration file.

    The returned structure is always:

        {"version": 1, "active": str, "configs": {name: params_dict}}
    """
    if path is None:
        return {"version": 1, "active": "", "configs": {}}
    data = _read_json(Path(path))
    configs = data.get("configs")
    if not isinstance(configs, dict):
        configs = {}
    clean: dict[str, dict] = {}
    for name, params in configs.items():
        if isinstance(name, str) and name.strip() and isinstance(params, dict):
            clean[name.strip()] = params
    active = data.get("active")
    if not isinstance(active, str) or active not in clean:
        active = next(iter(clean), "")
    return {"version": 1, "active": active, "configs": clean}


def save_project_config(path: Path | None, data: dict) -> bool:
    if path is None:
        return False
    payload = {
        "schema": "copper_thief.project",
        "version": 1,
        "active": str(data.get("active", "")),
        "configs": data.get("configs", {}),
    }
    return _write_json(Path(path), payload)

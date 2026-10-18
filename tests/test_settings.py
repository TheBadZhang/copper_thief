"""Tests for global settings and project configuration files."""

from __future__ import annotations

import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "tests" / "_artifacts"
ARTIFACTS.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))

import settings  # noqa: E402


class FakeBoard:
    def __init__(self, filename: str):
        self.filename = filename

    def GetFileName(self) -> str:
        return self.filename


class ProjectConfigTests(unittest.TestCase):
    def _path(self, name: str) -> pathlib.Path:
        path = ARTIFACTS / name
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return path

    def test_project_config_path(self):
        board = FakeBoard("/work/uDap/uDap.kicad_pcb")
        path = settings.project_config_path(board)
        self.assertIsNotNone(path)
        self.assertEqual(path.name, "uDap.copper_thief.json")
        self.assertEqual(path.parent.name, "uDap")

    def test_unsaved_board_has_no_project_path(self):
        self.assertIsNone(settings.project_config_path(FakeBoard("")))

    def test_round_trip_multiple_configs(self):
        path = self._path("demo.copper_thief.json")
        configs = {
            "密集": {"size_mm": 0.5, "layers": ["F.Cu", "B.Cu"]},
            "稀疏": {"size_mm": 1.2, "layers": ["F.Cu"]},
        }
        self.assertTrue(
            settings.save_project_config(path, {"active": "稀疏", "configs": configs})
        )
        loaded = settings.load_project_config(path)
        self.assertEqual(loaded["active"], "稀疏")
        self.assertEqual(set(loaded["configs"]), {"密集", "稀疏"})
        self.assertEqual(loaded["configs"]["密集"]["size_mm"], 0.5)

    def test_invalid_project_file_falls_back(self):
        path = self._path("bad.copper_thief.json")
        path.write_text("{not json", encoding="utf-8")
        loaded = settings.load_project_config(path)
        self.assertEqual(loaded["configs"], {})
        self.assertEqual(loaded["active"], "")

    def test_non_dict_configs_are_ignored(self):
        path = self._path("mixed.copper_thief.json")
        path.write_text(
            json.dumps(
                {
                    "active": "good",
                    "configs": {
                        "good": {"size_mm": 1},
                        "bad": "not a dict",
                        "": {"size_mm": 2},
                    },
                }
            ),
            encoding="utf-8",
        )
        loaded = settings.load_project_config(path)
        self.assertEqual(set(loaded["configs"]), {"good"})
        self.assertEqual(loaded["active"], "good")


if __name__ == "__main__":
    unittest.main()

r"""Integration tests that use pcbnew and kicad-cli.

Run with the Python interpreter bundled with KiCad::

    "C:\Program Files\KiCad\10.0\bin\python.exe" -m unittest tests.test_integration -v

The tests exercise the real board reader/writer and run KiCad's own DRC on the
generated board.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "tests" / "_artifacts"
ARTIFACTS.mkdir(parents=True, exist_ok=True)

os.environ.setdefault("COPPER_THIEF_NO_REGISTER", "1")
os.environ.setdefault("COPPER_THIEF_SETTINGS", str(ARTIFACTS / "settings.json"))
sys.path.insert(0, str(ROOT))

import pcbnew  # noqa: E402

import board_adapter as ba  # noqa: E402
import core  # noqa: E402

MM = pcbnew.FromMM


def _new_board() -> pcbnew.BOARD:
    return pcbnew.BOARD()


def _add_outline(board: pcbnew.BOARD, width_mm: float, height_mm: float) -> None:
    shape = pcbnew.PCB_SHAPE(board)
    shape.SetShape(pcbnew.SHAPE_T_RECT)
    shape.SetStart(pcbnew.VECTOR2I(0, 0))
    shape.SetEnd(pcbnew.VECTOR2I(MM(width_mm), MM(height_mm)))
    shape.SetLayer(pcbnew.Edge_Cuts)
    shape.SetWidth(MM(0.1))
    board.Add(shape)


def _add_cutout(board: pcbnew.BOARD, cx_mm: float, cy_mm: float, radius_mm: float) -> None:
    shape = pcbnew.PCB_SHAPE(board)
    shape.SetShape(pcbnew.SHAPE_T_CIRCLE)
    shape.SetCenter(pcbnew.VECTOR2I(MM(cx_mm), MM(cy_mm)))
    shape.SetRadius(MM(radius_mm))
    shape.SetLayer(pcbnew.Edge_Cuts)
    shape.SetWidth(MM(0.1))
    board.Add(shape)


def _add_net(board: pcbnew.BOARD, name: str) -> pcbnew.NETINFO_ITEM:
    net = pcbnew.NETINFO_ITEM(board, name)
    board.Add(net)
    return net


def _add_track(
    board: pcbnew.BOARD,
    net,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    layer: int = pcbnew.F_Cu,
    width: float = 0.3,
) -> pcbnew.PCB_TRACK:
    track = pcbnew.PCB_TRACK(board)
    track.SetStart(pcbnew.VECTOR2I(MM(x0), MM(y0)))
    track.SetEnd(pcbnew.VECTOR2I(MM(x1), MM(y1)))
    track.SetWidth(MM(width))
    track.SetLayer(layer)
    track.SetNet(net)
    board.Add(track)
    return track


def _add_pad(
    board: pcbnew.BOARD,
    net,
    x: float,
    y: float,
    *,
    pth: bool = False,
    npth: bool = False,
    drill: float = 0.0,
) -> pcbnew.PAD:
    footprint = pcbnew.FOOTPRINT(board)
    footprint.SetPosition(pcbnew.VECTOR2I(MM(x), MM(y)))
    footprint.SetReference("J1")
    board.Add(footprint)
    pad = pcbnew.PAD(footprint)
    pad.SetNumber("1")
    pad.SetShape(pcbnew.PAD_SHAPE_CIRCLE)
    pad.SetSize(pcbnew.VECTOR2I(MM(2.5), MM(2.5)))
    pad.SetPosition(pcbnew.VECTOR2I(MM(x), MM(y)))
    layers = pcbnew.LSET()
    layers.AddLayer(pcbnew.F_Cu)
    layers.AddLayer(pcbnew.B_Cu)
    pad.SetLayerSet(layers)
    if drill:
        pad.SetDrillSize(pcbnew.VECTOR2I(MM(drill), MM(drill)))
    if npth:
        pad.SetAttribute(pcbnew.PAD_ATTRIB_NPTH)
    elif pth:
        pad.SetAttribute(pcbnew.PAD_ATTRIB_PTH)
        pad.SetNet(net)
    else:
        pad.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
        pad.SetNet(net)
    footprint.Add(pad)
    return pad


def _add_zone(
    board: pcbnew.BOARD,
    net,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    *,
    keepout: bool = False,
) -> pcbnew.ZONE:
    zone = pcbnew.ZONE(board)
    zone.SetLayer(pcbnew.F_Cu)
    if keepout:
        zone.SetIsRuleArea(True)
        zone.SetDoNotAllowZoneFills(True)
    else:
        zone.SetNet(net)
    outline = zone.Outline()
    outline.NewOutline()
    for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
        outline.Append(MM(x), MM(y))
    board.Add(zone)
    if not keepout:
        zone.SetIsFilled(True)
        polyset = pcbnew.SHAPE_POLY_SET()
        polyset.NewOutline()
        for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
            polyset.Append(MM(x), MM(y))
        zone.SetFilledPolysList(pcbnew.F_Cu, polyset)
    return zone


def _build_test_board() -> pcbnew.BOARD:
    board = _new_board()
    _add_outline(board, 70, 45)
    _add_cutout(board, 35, 22.5, 3.0)
    gnd = _add_net(board, "GND")
    sig = _add_net(board, "SIG")

    for index in range(5):
        _add_track(board, gnd, 5, 5 + index * 3, 24, 5 + index * 3)
        _add_track(board, sig, 5, 26 + index * 3, 24, 26 + index * 3)

    _add_pad(board, gnd, 48, 8, pth=True, drill=1.0)
    _add_pad(board, gnd, 56, 8, npth=True, drill=3.2)
    _add_pad(board, sig, 48, 16)

    via = pcbnew.PCB_VIA(board)
    via.SetPosition(pcbnew.VECTOR2I(MM(56), MM(16)))
    via.SetWidth(MM(0.8))
    via.SetDrill(MM(0.4))
    via.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
    via.SetNet(gnd)
    board.Add(via)

    _add_zone(board, gnd, 28, 32, 66, 42)
    _add_zone(board, gnd, 52, 24, 66, 31, keepout=True)

    board.BuildConnectivity()
    return board


def _cli() -> pathlib.Path:
    candidate = pathlib.Path(sys.executable).with_name("kicad-cli.exe")
    if candidate.exists():
        return candidate
    for name in ("kicad-cli", "kicad-cli.exe"):
        found = pathlib.Path(name)
        if found.exists():
            return found
    raise unittest.SkipTest("kicad-cli not found next to the Python interpreter")


def _run_drc(board_path: pathlib.Path) -> dict:
    report = board_path.with_suffix(".drc.json")
    result = subprocess.run(
        [
            str(_cli()),
            "pcb",
            "drc",
            "--format",
            "json",
            "-o",
            str(report),
            str(board_path),
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )
    if not report.exists():
        raise AssertionError(f"kicad-cli did not create a report:\n{result.stdout}\n{result.stderr}")
    return json.loads(report.read_text(encoding="utf-8"))


def _assert_no_clearance_violations(test: unittest.TestCase, drc: dict) -> None:
    bad = []
    for violation in drc.get("violations", []):
        vtype = str(violation.get("type", ""))
        if (
            "clearance" in vtype
            or vtype.startswith("copper_edge")
            or "hole" in vtype
            or vtype in ("shorting_items", "copper_sliver", "starved_thermal")
        ):
            bad.append(violation)
    test.assertEqual(bad, [], f"generated copper caused DRC violations: {bad}")


class IntegrationTests(unittest.TestCase):
    def test_generate_and_drc(self):
        board = _build_test_board()
        geometry = ba.build_board_geometry(
            board,
            [pcbnew.F_Cu],
            target_netcode=0,
            user_clearance=MM(0.2),
        )
        self.assertGreater(len(geometry.layers[pcbnew.F_Cu].copper), 5)
        self.assertGreater(len(geometry.layers[pcbnew.F_Cu].drills), 2)
        self.assertGreater(len(geometry.layers[pcbnew.F_Cu].keepouts), 0)

        config = core.PatternConfig(
            shape=core.SHAPE_ROUNDED_SQUARE,
            size=MM(1.2),
            corner_radius=MM(0.25),
            gap=MM(0.35),
            edge_clearance=MM(0.5),
            max_count=50_000,
        )
        config = ba.apply_rule_clearances(board, config)
        result = core.Planner(geometry, config).plan()
        self.assertGreater(result.stats.placed, 500)
        self.assertGreater(result.stats.skipped_copper, 0)
        self.assertGreater(result.stats.skipped_keepout, 0)

        before = len(board.GetDrawings())
        removed = ba.remove_generated(board)
        self.assertEqual(removed, 0)
        created = ba.create_generated(board, config, result.placements, 0)
        self.assertEqual(created, result.stats.placed)
        # PCB groups are not part of GetDrawings(); the group contains the
        # generated shapes, but the shapes themselves are board drawings.
        self.assertEqual(len(board.GetDrawings()), before + created)
        self.assertEqual(len(ba.generated_groups(board)), 1)

        path = ARTIFACTS / "integration_generated.kicad_pcb"
        pcbnew.SaveBoard(str(path), board)
        self.assertTrue(path.exists())

        drc = _run_drc(path)
        _assert_no_clearance_violations(self, drc)

        removed = ba.remove_generated(board)
        self.assertEqual(removed, created)
        self.assertEqual(len(ba.generated_groups(board)), 0)

    def test_net_assignment(self):
        board = _new_board()
        _add_outline(board, 25, 20)
        gnd = _add_net(board, "GND")
        _add_pad(board, gnd, 5, 5)
        geometry = ba.build_board_geometry(board, [pcbnew.F_Cu], gnd.GetNetCode(), MM(0.2))
        config = ba.apply_rule_clearances(
            board,
            core.PatternConfig(
                shape=core.SHAPE_CIRCLE,
                size=MM(1),
                gap=MM(0.4),
                edge_clearance=MM(0.5),
                max_count=1000,
            ),
        )
        result = core.Planner(geometry, config, target_netcode=gnd.GetNetCode()).plan()
        self.assertGreater(result.stats.placed, 0)
        ba.create_generated(board, config, result.placements[:10], gnd.GetNetCode())
        net_names = set()
        for group in ba.generated_groups(board):
            for item in group.GetItems():
                net_names.add(item.GetNetname())
        self.assertIn("GND", net_names)

        path = ARTIFACTS / "integration_net.kicad_pcb"
        pcbnew.SaveBoard(str(path), board)
        drc = _run_drc(path)
        _assert_no_clearance_violations(self, drc)
        ba.remove_generated(board)

    def test_generated_items_share_one_group(self):
        board = _new_board()
        _add_outline(board, 30, 20)
        geometry = ba.build_board_geometry(board, [pcbnew.F_Cu], 0, MM(0.2))
        config = ba.apply_rule_clearances(
            board,
            core.PatternConfig(
                shape=core.SHAPE_CIRCLE,
                size=MM(1),
                gap=MM(0.4),
                edge_clearance=MM(0.5),
                max_count=5000,
            ),
        )
        result = core.Planner(geometry, config).plan()
        self.assertGreaterEqual(result.stats.placed, 10)
        first = ba.create_generated(board, config, result.placements[:5], 0)
        second = ba.create_generated(board, config, result.placements[5:10], 0)
        groups = ba.generated_groups(board)
        self.assertEqual(first + second, 10)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0].GetItems()), 10)
        ba.remove_generated(board)
        self.assertEqual(len(ba.generated_groups(board)), 0)

    def test_legacy_groups_are_merged(self):
        board = _new_board()
        _add_outline(board, 30, 20)
        config = ba.apply_rule_clearances(
            board,
            core.PatternConfig(
                shape=core.SHAPE_CIRCLE,
                size=MM(1),
                gap=MM(0.4),
                edge_clearance=MM(0.5),
                max_count=5000,
            ),
        )
        for index in range(2):
            group = pcbnew.PCB_GROUP(board)
            group.SetName(f"CopperThief_old{index}")
            board.Add(group)
            shape = pcbnew.PCB_SHAPE(board)
            shape.SetShape(pcbnew.SHAPE_T_CIRCLE)
            shape.SetCenter(pcbnew.VECTOR2I(MM(5 + index * 5), MM(5)))
            shape.SetRadius(MM(0.5))
            shape.SetLayer(pcbnew.F_Cu)
            shape.SetWidth(0)
            shape.SetFilled(True)
            board.Add(shape)
            group.AddItem(shape)
        geometry = ba.build_board_geometry(board, [pcbnew.F_Cu], 0, MM(0.2))
        result = core.Planner(geometry, config).plan()
        self.assertGreaterEqual(result.stats.placed, 3)
        ba.create_generated(board, config, result.placements[:3], 0)
        groups = ba.generated_groups(board)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0].GetItems()), 5)
        ba.remove_generated(board)

    def test_unfilled_zone_is_ignored(self):
        board = _new_board()
        _add_outline(board, 30, 20)
        gnd = _add_net(board, "GND")
        # Simulate an imported board with a zone outline but no fill data.
        zone = pcbnew.ZONE(board)
        zone.SetLayer(pcbnew.F_Cu)
        zone.SetNet(gnd)
        outline = zone.Outline()
        outline.NewOutline()
        for x, y in ((0.5, 0.5), (29.5, 0.5), (29.5, 19.5), (0.5, 19.5)):
            outline.Append(MM(x), MM(y))
        board.Add(zone)
        geometry = ba.build_board_geometry(board, [pcbnew.F_Cu], 0, MM(0.2))
        layer = geometry.layers[pcbnew.F_Cu]
        self.assertGreaterEqual(layer.unfilled_zones, 1)
        self.assertEqual(len(layer.copper), 0)
        config = ba.apply_rule_clearances(
            board,
            core.PatternConfig(
                shape=core.SHAPE_CIRCLE,
                size=MM(1),
                gap=MM(0.4),
                edge_clearance=MM(0.5),
                max_count=5000,
            ),
        )
        result = core.Planner(geometry, config).plan()
        self.assertGreater(result.stats.placed, 0)

    def test_bottom_layer_is_independent(self):
        board = _new_board()
        _add_outline(board, 30, 20)
        gnd = _add_net(board, "GND")
        _add_track(board, gnd, 2, 10, 28, 10, layer=pcbnew.F_Cu)
        geometry = ba.build_board_geometry(board, [pcbnew.B_Cu], 0, MM(0.2))
        self.assertEqual(len(geometry.layers[pcbnew.B_Cu].copper), 0)
        config = ba.apply_rule_clearances(
            board,
            core.PatternConfig(
                shape=core.SHAPE_CIRCLE,
                size=MM(1),
                gap=MM(0.3),
                edge_clearance=MM(0.5),
                max_count=5000,
            ),
        )
        result = core.Planner(geometry, config).plan()
        self.assertGreater(result.stats.placed, 0)
        self.assertEqual(result.stats.skipped_copper, 0)


if __name__ == "__main__":
    unittest.main()

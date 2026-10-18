"""Build a small demo PCB and fill it with the Copper Thief pattern.

Run from the repository root with KiCad's Python::

    python demo/make_demo.py

It writes demo/copper_thief_demo.kicad_pcb.  The same code path is used by
the ActionPlugin, so it is a convenient way to sanity-check the installation
without opening the GUI.
"""

from __future__ import annotations

import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("COPPER_THIEF_NO_REGISTER", "1")
os.environ.setdefault("COPPER_THIEF_SETTINGS", str(HERE / "_settings.json"))

import pcbnew  # noqa: E402

import board_adapter as ba  # noqa: E402
import core  # noqa: E402

MM = pcbnew.FromMM


def add_outline(board, width, height):
    shape = pcbnew.PCB_SHAPE(board)
    shape.SetShape(pcbnew.SHAPE_T_RECT)
    shape.SetStart(pcbnew.VECTOR2I(0, 0))
    shape.SetEnd(pcbnew.VECTOR2I(MM(width), MM(height)))
    shape.SetLayer(pcbnew.Edge_Cuts)
    shape.SetWidth(MM(0.1))
    board.Add(shape)


def add_cutout(board, cx, cy, radius):
    shape = pcbnew.PCB_SHAPE(board)
    shape.SetShape(pcbnew.SHAPE_T_CIRCLE)
    shape.SetCenter(pcbnew.VECTOR2I(MM(cx), MM(cy)))
    shape.SetRadius(MM(radius))
    shape.SetLayer(pcbnew.Edge_Cuts)
    shape.SetWidth(MM(0.1))
    board.Add(shape)


def add_track(board, net, x0, y0, x1, y1, width=0.3):
    track = pcbnew.PCB_TRACK(board)
    track.SetStart(pcbnew.VECTOR2I(MM(x0), MM(y0)))
    track.SetEnd(pcbnew.VECTOR2I(MM(x1), MM(y1)))
    track.SetWidth(MM(width))
    track.SetLayer(pcbnew.F_Cu)
    track.SetNet(net)
    board.Add(track)


def add_pad(board, net, x, y, reference, number="1"):
    footprint = pcbnew.FOOTPRINT(board)
    footprint.SetPosition(pcbnew.VECTOR2I(MM(x), MM(y)))
    footprint.SetReference(reference)
    footprint.SetValue("DEMO")
    board.Add(footprint)
    pad = pcbnew.PAD(footprint)
    pad.SetNumber(number)
    pad.SetShape(pcbnew.PAD_SHAPE_ROUNDRECT)
    pad.SetSize(pcbnew.VECTOR2I(MM(2.4), MM(1.4)))
    pad.SetRoundRectCornerRadius(MM(0.25))
    pad.SetPosition(pcbnew.VECTOR2I(MM(x), MM(y)))
    layers = pcbnew.LSET()
    layers.AddLayer(pcbnew.F_Cu)
    pad.SetLayerSet(layers)
    pad.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
    pad.SetNet(net)
    footprint.Add(pad)


def add_keepout(board, x0, y0, x1, y1):
    zone = pcbnew.ZONE(board)
    zone.SetLayer(pcbnew.F_Cu)
    zone.SetIsRuleArea(True)
    zone.SetDoNotAllowZoneFills(True)
    outline = zone.Outline()
    outline.NewOutline()
    for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
        outline.Append(MM(x), MM(y))
    board.Add(zone)


def main():
    board = pcbnew.BOARD()
    add_outline(board, 70, 45)
    add_cutout(board, 35, 22.5, 3.0)
    gnd = pcbnew.NETINFO_ITEM(board, "GND")
    board.Add(gnd)
    sig = pcbnew.NETINFO_ITEM(board, "SIG")
    board.Add(sig)

    # One connected GND tree, so the demo itself is DRC-clean.
    add_track(board, gnd, 6, 5, 6, 17)
    for index in range(5):
        y = 5 + index * 3
        add_track(board, gnd, 6, y, 25, y)
        add_pad(board, gnd, 25, y, f"J{index + 1}")

    # A second, also-connected signal group.
    add_track(board, sig, 30, 6, 45, 6)
    add_pad(board, sig, 30, 6, "J6")
    add_pad(board, sig, 45, 6, "J7")
    add_track(board, sig, 30, 10, 45, 10)
    add_pad(board, sig, 30, 10, "J8")
    add_pad(board, sig, 45, 10, "J9")
    add_track(board, sig, 30, 6, 30, 10)

    add_keepout(board, 52, 24, 66, 31)

    board.BuildConnectivity()
    geometry = ba.build_board_geometry(board, [pcbnew.F_Cu], 0, MM(0.2))
    config = ba.apply_rule_clearances(
        board,
        core.PatternConfig(
            shape=core.SHAPE_ROUNDED_SQUARE,
            size=MM(1.2),
            corner_radius=MM(0.25),
            gap=MM(0.35),
            edge_clearance=MM(0.5),
            max_count=50_000,
        ),
    )
    plan = core.Planner(geometry, config).plan()
    ba.remove_generated(board)
    ba.create_generated(board, config, plan.placements, 0)

    output = HERE / "copper_thief_demo.kicad_pcb"
    pcbnew.SaveBoard(str(output), board)
    print(core.summarize_plan(plan))
    print(f"saved {output}")


if __name__ == "__main__":
    main()

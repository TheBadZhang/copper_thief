"""KiCad pcbnew adapter for the Copper Thief placement core.

Everything in this module knows about pcbnew.  The geometry/planner core stays
pure Python so it can be tested without loading KiCad.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Iterable

import pcbnew

try:
    from . import core
except ImportError:  # pragma: no cover - allows top-level test/demo imports
    import core

GENERATED_GROUP_PREFIX = "CopperThief"
_MAX_ERROR = pcbnew.FromMM(0.002)  # 2 um polygon approximation error
_TEXTBOX_TYPE = getattr(pcbnew, "PCB_TEXTBOX", None)


def to_mm(value: int) -> float:
    return value / 1_000_000.0


def from_mm(value: float) -> int:
    return int(round(value * 1_000_000.0))


def mm(value: float) -> int:
    return pcbnew.FromMM(value)


def layer_name(layer_id: int) -> str:
    return pcbnew.LayerName(layer_id)


def copper_layer_ids(board: pcbnew.BOARD) -> list[int]:
    """Return enabled copper layers in board stack order."""
    count = board.GetCopperLayerCount()
    ids: list[int] = [pcbnew.F_Cu]
    for index in range(1, max(1, count - 1)):
        ids.append(pcbnew.In1_Cu + (index - 1) * 2)
    if count > 1:
        ids.append(pcbnew.B_Cu)
    enabled = board.GetEnabledLayers()
    return [lid for lid in ids if enabled.Contains(lid)]


def net_choices(board: pcbnew.BOARD) -> list[tuple[int, str]]:
    """Return (netcode, name) pairs, with no-net first."""
    result: list[tuple[int, str]] = [(0, "<无网络>")]
    try:
        nets = board.GetNetsByNetcode()
        items = list(nets.items())
    except Exception:
        items = []
    for code, net in items:
        try:
            name = net.GetNetname()
        except Exception:
            continue
        if not name:
            continue
        result.append((int(code), str(name)))
    result[1:] = sorted(result[1:], key=lambda item: item[1].lower())
    return result


def net_by_code(board: pcbnew.BOARD, netcode: int):
    if not netcode:
        return None
    try:
        net = board.FindNet(int(netcode))
        if net is not None:
            return net
    except Exception:
        pass
    try:
        for code, net in board.GetNetsByNetcode().items():
            if int(code) == int(netcode):
                return net
    except Exception:
        pass
    return None


class ClearanceResolver:
    """Resolve effective clearances from board rules and net classes."""

    def __init__(
        self,
        board: pcbnew.BOARD,
        target_netcode: int | None,
        user_clearance: int,
    ) -> None:
        self.board = board
        self.user_clearance = max(0, int(user_clearance))
        design = board.GetDesignSettings()
        self.board_min = max(0, int(getattr(design, "m_MinClearance", 0) or 0))
        self.hole_clearance = max(
            self.user_clearance,
            int(getattr(design, "m_HoleClearance", 0) or 0),
        )

        self.netclasses: dict[str, int] = {}
        try:
            classes = board.GetAllNetClasses()
            for name, netclass in classes.items():
                try:
                    self.netclasses[str(name)] = max(0, int(netclass.GetClearance()))
                except Exception:
                    pass
        except Exception:
            pass

        self.default_clearance = 0
        try:
            self.default_clearance = max(
                0,
                int(design.m_NetSettings.GetDefaultNetclass().GetClearance()),
            )
        except Exception:
            if self.netclasses:
                self.default_clearance = next(iter(self.netclasses.values()))

        self.target_clearance = 0
        if target_netcode:
            net = net_by_code(board, target_netcode)
            if net is not None:
                try:
                    self.target_clearance = max(0, int(net.GetNetClassSlow().GetClearance()))
                except Exception:
                    try:
                        self.target_clearance = int(
                            self.netclasses.get(net.GetNetClassName(), self.default_clearance)
                        )
                    except Exception:
                        self.target_clearance = self.default_clearance

    def for_item(self, item) -> int:
        netclass_clearance = self.default_clearance
        try:
            name = item.GetNetClassName()
            if name:
                netclass_clearance = self.netclasses.get(name, self.default_clearance)
        except Exception:
            pass

        local = 0
        try:
            local_value = item.GetLocalClearance()
            if local_value is not None:
                local = max(0, int(local_value))
        except Exception:
            pass

        return max(
            self.user_clearance,
            self.board_min,
            self.target_clearance,
            netclass_clearance,
            local,
        )


def effective_edge_clearance(board: pcbnew.BOARD, user_clearance: int) -> int:
    design = board.GetDesignSettings()
    return max(
        max(0, int(user_clearance)),
        int(getattr(design, "m_CopperEdgeClearance", 0) or 0),
    )


def effective_hole_clearance(board: pcbnew.BOARD, user_clearance: int) -> int:
    design = board.GetDesignSettings()
    return max(
        max(0, int(user_clearance)),
        int(getattr(design, "m_HoleClearance", 0) or 0),
    )


def _ring_from_chain(chain) -> core.Ring | None:
    points: list[core.Point] = []
    try:
        count = chain.PointCount()
        for index in range(count):
            point = chain.CPoint(index)
            points.append((int(point.x), int(point.y)))
    except Exception:
        return None
    try:
        return core.Ring(tuple(points))
    except ValueError:
        return None


def _regions_from_polyset(polyset) -> list[core.Region]:
    if polyset is None:
        return []
    regions: list[core.Region] = []
    try:
        outline_count = int(polyset.OutlineCount())
    except Exception:
        return []
    for outline_index in range(outline_count):
        outer = _ring_from_chain(polyset.Outline(outline_index))
        if outer is None:
            continue
        holes: list[core.Ring] = []
        try:
            hole_count = int(polyset.HoleCount(outline_index))
        except Exception:
            hole_count = 0
        for hole_index in range(hole_count):
            hole = _ring_from_chain(polyset.Hole(outline_index, hole_index))
            if hole is not None:
                holes.append(hole)
        regions.append(core.Region(outer, tuple(holes)))
    return regions


def _regions_from_shape(shape) -> list[core.Region]:
    if shape is None:
        return []
    polyset = pcbnew.SHAPE_POLY_SET()
    try:
        shape.TransformToPolygon(polyset, _MAX_ERROR, pcbnew.ERROR_OUTSIDE)
    except Exception:
        return []
    return _regions_from_polyset(polyset)


def _item_regions(item, layer_id: int) -> list[core.Region]:
    # Text needs its glyph shapes; ordinary shapes/pads use the effective
    # copper shape.
    text_regions: list[core.Region] = []
    try:
        if hasattr(item, "GetEffectiveTextShape"):
            text_regions = _regions_from_shape(item.GetEffectiveTextShape(True))
    except Exception:
        text_regions = []

    if isinstance(item, pcbnew.PAD):
        try:
            polygon = item.GetEffectivePolygon(layer_id, pcbnew.ERROR_OUTSIDE)
            return _regions_from_polyset(polygon)
        except Exception:
            pass

    try:
        shape_regions = _regions_from_shape(item.GetEffectiveShape(layer_id))
    except Exception:
        shape_regions = []

    if text_regions and _TEXTBOX_TYPE is not None and isinstance(item, _TEXTBOX_TYPE):
        # A copper text box also has a border; include both text and border.
        return text_regions + shape_regions
    if text_regions:
        return text_regions
    return shape_regions


def _hole_regions(item) -> list[core.Region]:
    try:
        if not item.HasHole():
            return []
    except Exception:
        return []
    try:
        shape = item.GetEffectiveHoleShape()
    except Exception:
        return []
    return _regions_from_shape(shape)


def _netcode(item) -> int:
    try:
        return int(item.GetNetCode())
    except Exception:
        return 0


def _is_generated(item) -> bool:
    try:
        group = item.GetParentGroup()
    except Exception:
        return False
    while group is not None:
        try:
            name = group.GetName()
        except Exception:
            return False
        if name.startswith(GENERATED_GROUP_PREFIX):
            return True
        try:
            parent = group.GetParentGroup()
        except Exception:
            return False
        if parent is group:
            return False
        group = parent
    return False


def _add_copper(
    layer: core.LayerGeometry,
    item,
    regions: list[core.Region],
    resolver: ClearanceResolver,
) -> None:
    if not regions:
        return
    layer.add_copper(
        core.CopperObstacle(
            netcode=_netcode(item),
            clearance=resolver.for_item(item),
            regions=tuple(regions),
            label=type(item).__name__,
        )
    )


def _add_drills(layer: core.LayerGeometry, item, clearance: int) -> None:
    for region in _hole_regions(item):
        layer.add_drill(region, clearance)


def _add_drawing(
    layer: core.LayerGeometry,
    item,
    resolver: ClearanceResolver,
    hole_clearance: int,
) -> None:
    if _is_generated(item):
        return
    regions = _item_regions(item, layer.layer_id)
    _add_copper(layer, item, regions, resolver)
    # A copper drawing normally has no hole, but text boxes / custom items
    # might in future; this is cheap and harmless.
    _add_drills(layer, item, hole_clearance)


def _add_zone(
    layer: core.LayerGeometry,
    zone,
    resolver: ClearanceResolver,
) -> None:
    if _is_generated(zone):
        return
    if zone.GetIsRuleArea():
        for region in _regions_from_polyset(zone.Outline()):
            layer.add_keepout(region)
        return

    regions: list[core.Region] = []
    try:
        if zone.HasFilledPolysForLayer(layer.layer_id):
            regions = _regions_from_polyset(zone.GetFilledPolysList(layer.layer_id))
    except Exception:
        regions = []
    if not regions:
        # A zone that has never been filled (or whose fill data is empty)
        # contributes no copper.  Do not treat its outline as a solid copper
        # obstacle: imported/old boards often carry dozens of empty zone
        # outlines, which would otherwise block the entire board.
        layer.unfilled_zones += 1
        return
    layer.add_copper(
        core.CopperObstacle(
            netcode=_netcode(zone),
            clearance=resolver.for_item(zone),
            regions=tuple(regions),
            label="Zone",
        )
    )


def build_board_geometry(
    board: pcbnew.BOARD,
    layer_ids: Iterable[int],
    target_netcode: int | None,
    user_clearance: int,
) -> core.BoardGeometry:
    """Read the board and build the pure-Python geometry used by the planner."""
    outline = pcbnew.SHAPE_POLY_SET()
    try:
        ok = board.GetBoardPolygonOutlines(outline, False)
    except Exception as exc:
        raise ValueError(f"无法读取板框 (Edge.Cuts): {exc}") from exc
    if not ok:
        raise ValueError("未找到闭合的板框，请检查 Edge.Cuts 图形。")

    board_regions = _regions_from_polyset(outline)
    if not board_regions:
        raise ValueError("板框为空，请检查 Edge.Cuts。")

    resolver = ClearanceResolver(board, target_netcode, user_clearance)
    hole_clearance = resolver.hole_clearance

    layers: dict[int, core.LayerGeometry] = {}
    for layer_id in layer_ids:
        layers[int(layer_id)] = core.LayerGeometry(int(layer_id), layer_name(layer_id))

    for layer in layers.values():
        # Tracks, arcs and vias.
        for track in board.GetTracks():
            try:
                if not track.IsOnLayer(layer.layer_id) or _is_generated(track):
                    continue
            except Exception:
                continue
            _add_copper(layer, track, _item_regions(track, layer.layer_id), resolver)
            _add_drills(layer, track, hole_clearance)

        # Footprints: pads, copper graphics and fields.
        for footprint in board.GetFootprints():
            for pad in footprint.Pads():
                try:
                    attr = pad.GetAttribute()
                    on_layer = pad.IsOnLayer(layer.layer_id)
                except Exception:
                    continue
                if attr != pcbnew.PAD_ATTRIB_NPTH and on_layer:
                    _add_copper(
                        layer,
                        pad,
                        _item_regions(pad, layer.layer_id),
                        resolver,
                    )
                # NPTH mounting holes go through every copper layer, even
                # when IsOnLayer() reports false.
                if attr == pcbnew.PAD_ATTRIB_NPTH or on_layer:
                    _add_drills(layer, pad, hole_clearance)

            for graphic in footprint.GraphicalItems():
                try:
                    if not graphic.IsOnLayer(layer.layer_id) or _is_generated(graphic):
                        continue
                except Exception:
                    continue
                _add_drawing(layer, graphic, resolver, hole_clearance)

            for field in (footprint.Reference(), footprint.Value()):
                try:
                    if not field.IsOnLayer(layer.layer_id) or _is_generated(field):
                        continue
                except Exception:
                    continue
                _add_drawing(layer, field, resolver, hole_clearance)

        # Board drawings (graphics, text, dimensions, ...).
        for drawing in board.GetDrawings():
            try:
                if not drawing.IsOnLayer(layer.layer_id) or _is_generated(drawing):
                    continue
            except Exception:
                continue
            _add_drawing(layer, drawing, resolver, hole_clearance)

        # Copper zones and rule areas.
        for zone in board.Zones():
            try:
                if not zone.IsOnLayer(layer.layer_id):
                    continue
            except Exception:
                continue
            _add_zone(layer, zone, resolver)

    bbox = board_regions[0].bbox
    for region in board_regions[1:]:
        bbox = core.bbox_union(bbox, region.bbox)
    return core.BoardGeometry(tuple(board_regions), bbox, layers)


def apply_rule_clearances(
    board: pcbnew.BOARD,
    config: core.PatternConfig,
) -> core.PatternConfig:
    """Raise the user's edge clearance to the board's copper-edge rule."""
    return replace(
        config,
        edge_clearance=effective_edge_clearance(board, config.edge_clearance),
    )


# ---------------------------------------------------------------------------
# Generated-item lifecycle
# ---------------------------------------------------------------------------


def generated_groups(board: pcbnew.BOARD) -> list:
    groups = []
    try:
        all_groups = board.Groups()
    except Exception:
        return groups
    for group in all_groups:
        try:
            if group.GetName().startswith(GENERATED_GROUP_PREFIX):
                groups.append(group)
        except Exception:
            pass
    return groups


def remove_generated(board: pcbnew.BOARD) -> int:
    """Delete all CopperThief groups and their items. Returns item count."""
    removed = 0
    for group in generated_groups(board):
        try:
            items = list(group.GetItems())
        except Exception:
            items = []
        for item in items:
            try:
                group.RemoveItem(item)
            except Exception:
                pass
            try:
                board.Remove(item)
                removed += 1
            except Exception:
                pass
        try:
            board.Remove(group)
        except Exception:
            pass
    if removed:
        try:
            board.BuildConnectivity()
            board.SetModified()
        except Exception:
            pass
    return removed


def _make_shape(board, config: core.PatternConfig, layer_id: int, x: int, y: int):
    shape = pcbnew.PCB_SHAPE(board)
    shape.SetLayer(int(layer_id))
    shape.SetWidth(0)
    shape.SetFilled(True)
    if config.shape == core.SHAPE_CIRCLE:
        shape.SetShape(pcbnew.SHAPE_T_CIRCLE)
        shape.SetCenter(pcbnew.VECTOR2I(int(x), int(y)))
        shape.SetRadius(int(config.radius))
    else:
        shape.SetShape(pcbnew.SHAPE_T_POLY)
        points = core.pattern_points(config, int(x), int(y))
        shape.SetPolyPoints([pcbnew.VECTOR2I(px, py) for px, py in points])
    return shape


def create_generated(
    board: pcbnew.BOARD,
    config: core.PatternConfig,
    placements: Iterable[core.Placement],
    netcode: int | None = None,
) -> int:
    """Create generated copper shapes in the single CopperThief group.

    If the group already exists (for example when the caller deliberately
    appends a second fill instead of replacing), new items are added to that
    same group so the board always has one manageable CopperThief group.
    """
    placements = list(placements)
    if not placements:
        return 0

    net = net_by_code(board, netcode) if netcode else None
    existing = generated_groups(board)
    if existing:
        group = existing[0]
        # Older versions could leave more than one CopperThief group.  Merge
        # all of them so the board always has exactly one group to manage.
        for other in existing[1:]:
            try:
                items = list(other.GetItems())
            except Exception:
                items = []
            for item in items:
                try:
                    other.RemoveItem(item)
                    group.AddItem(item)
                except Exception:
                    pass
            try:
                board.Remove(other)
            except Exception:
                pass
    else:
        group = pcbnew.PCB_GROUP(board)
        group.SetName(GENERATED_GROUP_PREFIX)
        board.Add(group)

    created = 0
    for placement in placements:
        shape = _make_shape(board, config, placement.layer_id, placement.x, placement.y)
        if net is not None:
            try:
                shape.SetNet(net)
            except Exception:
                pass
        board.Add(shape)
        try:
            group.AddItem(shape)
        except Exception:
            pass
        created += 1

    try:
        board.BuildConnectivity()
        board.SetModified()
    except Exception:
        pass
    return created


def refresh_view() -> None:
    try:
        pcbnew.Refresh()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


def _net_name(board: pcbnew.BOARD, netcode: int) -> str:
    if not netcode:
        return "<无网络>"
    net = net_by_code(board, netcode)
    try:
        return str(net.GetNetname()) if net is not None else f"<net {netcode}>"
    except Exception:
        return f"<net {netcode}>"


def _region_bbox(regions: tuple[core.Region, ...]) -> core.BBox:
    box = regions[0].bbox
    for region in regions[1:]:
        box = core.bbox_union(box, region.bbox)
    return box


def _fmt_box(box: core.BBox) -> str:
    return (
        f"({to_mm(box[0]):.3f}, {to_mm(box[1]):.3f}) - "
        f"({to_mm(box[2]):.3f}, {to_mm(box[3]):.3f}) mm"
    )


def diagnostic_report(
    board: pcbnew.BOARD,
    geometry: core.BoardGeometry,
    config: core.PatternConfig,
    plan: core.PlanResult | None,
    layer_ids: Iterable[int],
) -> str:
    """Build a copy/paste-friendly report explaining a failed fill."""
    board_box = geometry.bbox
    board_w = to_mm(board_box[2] - board_box[0])
    board_h = to_mm(board_box[3] - board_box[1])
    board_area = max(1.0, board_w * board_h)

    lines = [
        "Copper Thief diagnostic report",
        f"KiCad: {pcbnew.Version()}",
        f"Board bbox: {_fmt_box(board_box)}  ({board_w:.3f} x {board_h:.3f} mm)",
        f"Board outline regions: {len(geometry.board_regions)}",
        f"Board outline holes: {sum(len(r.holes) for r in geometry.board_regions)}",
        f"Copper layer count: {board.GetCopperLayerCount()}",
        "",
        "Pattern:",
        f"  shape={config.shape} size={to_mm(config.size):.3f} mm "
        f"corner={to_mm(config.corner_radius):.3f} mm "
        f"gap={to_mm(config.gap):.3f} mm",
        f"  edge_clearance={to_mm(config.edge_clearance):.3f} mm "
        f"centered={config.centered} "
        f"offset=({to_mm(config.offset_x):.3f}, {to_mm(config.offset_y):.3f}) mm "
        f"avoid_same_net={config.avoid_same_net}",
        "",
    ]

    for layer_id in layer_ids:
        layer = geometry.layers.get(int(layer_id))
        if layer is None:
            continue
        lines.append(f"Layer {layer.name} (id={layer.layer_id}):")
        lines.append(
            f"  copper items={len(layer.copper)} "
            f"regions={sum(len(o.regions) for o in layer.copper)} "
            f"keepouts={len(layer.keepouts)} drills={len(layer.drills)} "
            f"unfilled_zones={layer.unfilled_zones} "
            f"max_copper_clearance={to_mm(layer.max_copper_clearance):.3f} mm"
        )
        if layer.unfilled_zones:
            lines.append(
                "  NOTE: empty/unfilled zones were ignored (they contain no copper); "
                "refill zones in KiCad if you want them included in clearance checks."
            )
        if layer.copper:
            ranked = []
            for obstacle in layer.copper:
                box = _region_bbox(obstacle.regions)
                area = max(0.0, to_mm(box[2] - box[0])) * max(
                    0.0, to_mm(box[3] - box[1])
                )
                ranked.append((area, obstacle, box))
            ranked.sort(key=lambda item: item[0], reverse=True)
            for area, obstacle, box in ranked[:5]:
                ratio = area / board_area
                lines.append(
                    f"  - {obstacle.label} net={_net_name(board, obstacle.netcode)} "
                    f"clearance={to_mm(obstacle.clearance):.3f} mm "
                    f"bbox={_fmt_box(box)} area~{ratio * 100:.1f}% of board bbox"
                )
                if ratio > 0.80:
                    lines.append(
                        "    WARNING: this copper item covers most of the board bbox; "
                        "the selected layer is probably a solid pour/plane."
                    )
        else:
            lines.append("  (no copper obstacles)")
        lines.append("")

    if plan is not None:
        lines.append(core.summarize_plan(plan))
    else:
        lines.append("No placement plan was produced.")

    return "\n".join(lines)

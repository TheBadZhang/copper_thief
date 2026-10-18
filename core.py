"""
Pure geometry and placement planning for the Copper Thief plugin.

This module deliberately has no dependency on pcbnew or wxPython so that the
placement algorithm can be unit-tested with a normal Python interpreter.

All coordinates are integer nanometres, matching KiCad's internal board units.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

Point = tuple[int, int]
BBox = tuple[int, int, int, int]  # min_x, min_y, max_x, max_y

# Skip reasons ---------------------------------------------------------------
SKIP_OUTSIDE = "outside"
SKIP_EDGE = "edge"
SKIP_COPPER = "copper"
SKIP_KEEPOUT = "keepout"
SKIP_DRILL = "drill"

# Solid kinds ----------------------------------------------------------------
KIND_BOARD = "board"
KIND_COPPER = "copper"
KIND_KEEPOUT = "keepout"
KIND_DRILL = "drill"

SHAPE_CIRCLE = "circle"
SHAPE_ROUNDED_SQUARE = "rounded_square"

# Extra planning margin (2 um).  Curved shapes are approximated by polygons
# for the collision test; this keeps the approximation on the safe side.
PLANNING_MARGIN = 2_000


# ---------------------------------------------------------------------------
# Basic geometry helpers
# ---------------------------------------------------------------------------


def bbox_from_points(points: Iterable[Point]) -> BBox:
    pts = list(points)
    if not pts:
        raise ValueError("empty point list")
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def bbox_union(a: BBox, b: BBox) -> BBox:
    return min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])


def bbox_inflate(box: BBox, amount: int) -> BBox:
    return box[0] - amount, box[1] - amount, box[2] + amount, box[3] + amount


def bbox_contains(box: BBox, point: Point) -> bool:
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def bbox_intersects(a: BBox, b: BBox) -> bool:
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def bbox_of_segment(a: Point, b: Point) -> BBox:
    return min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])


def _cells_for_bbox(box: BBox, cell: int) -> Iterable[tuple[int, int]]:
    x0 = math.floor(box[0] / cell)
    y0 = math.floor(box[1] / cell)
    x1 = math.floor(box[2] / cell)
    y1 = math.floor(box[3] / cell)
    for cy in range(y0, y1 + 1):
        for cx in range(x0, x1 + 1):
            yield cx, cy


def _cell_of(point: Point, cell: int) -> tuple[int, int]:
    return math.floor(point[0] / cell), math.floor(point[1] / cell)


def distance_point_segment_sq(p: Point, a: Point, b: Point) -> float:
    """Squared distance from point p to segment a-b."""
    px, py = p
    ax, ay = a
    bx, by = b
    dx = bx - ax
    dy = by - ay
    if dx == 0 and dy == 0:
        ex = px - ax
        ey = py - ay
        return float(ex * ex + ey * ey)
    t = ((px - ax) * dx + (py - ay) * dy) / float(dx * dx + dy * dy)
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0
    qx = ax + t * dx
    qy = ay + t * dy
    ex = px - qx
    ey = py - qy
    return float(ex * ex + ey * ey)


def min_distance_point_to_ring_sq(point: Point, points: Sequence[Point]) -> float:
    best = float("inf")
    n = len(points)
    for i in range(n):
        d = distance_point_segment_sq(point, points[i], points[(i + 1) % n])
        if d < best:
            best = d
    return best


def point_in_polygon(point: Point, points: Sequence[Point]) -> bool:
    """Even-odd ray-cast test. Boundary points are not guaranteed either way."""
    x, y = point
    inside = False
    n = len(points)
    j = n - 1
    for i in range(n):
        xi, yi = points[i]
        xj, yj = points[j]
        if (yi > y) != (yj > y):
            x_cross = xj + (y - yj) * (xi - xj) / float(yi - yj)
            if x_cross > x:
                inside = not inside
        j = i
    return inside


def segment_intersects_polygon(a: Point, b: Point, points: Sequence[Point]) -> bool:
    """True when segment a-b touches/intersects the polygon boundary."""
    n = len(points)
    for i in range(n):
        c = points[i]
        d = points[(i + 1) % n]
        if _segments_intersect(a, b, c, d):
            return True
    return False


def _cross(a: Point, b: Point, c: Point) -> int:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a: Point, b: Point, p: Point) -> bool:
    return (
        min(a[0], b[0]) <= p[0] <= max(a[0], b[0])
        and min(a[1], b[1]) <= p[1] <= max(a[1], b[1])
        and _cross(a, b, p) == 0
    )


def _segments_intersect(a: Point, b: Point, c: Point, d: Point) -> bool:
    o1 = _cross(a, b, c)
    o2 = _cross(a, b, d)
    o3 = _cross(c, d, a)
    o4 = _cross(c, d, b)
    if (o1 > 0 > o2) or (o1 < 0 < o2):
        if (o3 > 0 > o4) or (o3 < 0 < o4):
            return True
    if o1 == 0 and _on_segment(a, b, c):
        return True
    if o2 == 0 and _on_segment(a, b, d):
        return True
    if o3 == 0 and _on_segment(c, d, a):
        return True
    if o4 == 0 and _on_segment(c, d, b):
        return True
    return False


def polygon_bbox(points: Sequence[Point]) -> BBox:
    return bbox_from_points(points)


# ---------------------------------------------------------------------------
# Immutable board geometry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Ring:
    """A closed polygon ring."""

    points: tuple[Point, ...]
    bbox: BBox = field(init=False, repr=False, compare=False)
    _row_edges: tuple[tuple[int, ...], ...] | None = field(
        init=False, repr=False, compare=False
    )
    _row_y0: int = field(init=False, repr=False, compare=False)
    _row_height: int = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        pts = list(self.points)
        if len(pts) >= 2 and pts[0] == pts[-1]:
            pts.pop()
        if len(pts) < 3:
            raise ValueError("a ring needs at least three distinct points")
        object.__setattr__(self, "points", tuple(pts))
        box = bbox_from_points(pts)
        object.__setattr__(self, "bbox", box)

        # Large zone/text polygons can contain thousands of vertices.  A
        # Y-bucket index keeps point-in-polygon queries cheap without changing
        # the exact ray-casting result.
        rows: tuple[tuple[int, ...], ...] | None = None
        row_y0 = box[1]
        row_height = 1
        height = box[3] - box[1]
        if len(pts) > 256 and height > 0:
            row_height = max(500_000, height // 256)
            row_count = height // row_height + 1
            buckets: list[list[int]] = [[] for _ in range(row_count)]
            n = len(pts)
            for index in range(n):
                _, y1 = pts[index]
                _, y2 = pts[(index + 1) % n]
                if y1 == y2:
                    continue
                low = min(y1, y2)
                high = max(y1, y2)
                first = (low - row_y0) // row_height
                last = (high - row_y0) // row_height
                first = max(0, min(row_count - 1, first))
                last = max(0, min(row_count - 1, last))
                for bucket_index in range(first, last + 1):
                    buckets[bucket_index].append(index)
            rows = tuple(tuple(bucket) for bucket in buckets)
        object.__setattr__(self, "_row_edges", rows)
        object.__setattr__(self, "_row_y0", row_y0)
        object.__setattr__(self, "_row_height", row_height)

    def contains(self, point: Point) -> bool:
        if not bbox_contains(self.bbox, point):
            return False
        x, y = point
        rows = self._row_edges
        if rows is None:
            return point_in_polygon(point, self.points)

        bucket_index = (y - self._row_y0) // self._row_height
        bucket_index = max(0, min(len(rows) - 1, bucket_index))
        inside = False
        pts = self.points
        for index in rows[bucket_index]:
            xi, yi = pts[index]
            xj, yj = pts[(index + 1) % len(pts)]
            if (yi > y) != (yj > y):
                x_cross = xj + (y - yj) * (xi - xj) / float(yi - yj)
                if x_cross > x:
                    inside = not inside
        return inside

    def iter_edges(self) -> Iterable[tuple[Point, Point]]:
        n = len(self.points)
        for i in range(n):
            yield self.points[i], self.points[(i + 1) % n]


@dataclass(frozen=True)
class Region:
    """A filled polygon with optional holes."""

    outer: Ring
    holes: tuple[Ring, ...] = ()
    bbox: BBox = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "bbox", self.outer.bbox)

    def contains(self, point: Point) -> bool:
        if not self.outer.contains(point):
            return False
        return not any(hole.contains(point) for hole in self.holes)

    def iter_rings(self) -> Iterable[Ring]:
        yield self.outer
        yield from self.holes


@dataclass(frozen=True)
class CopperObstacle:
    """Copper belonging to one board item."""

    netcode: int
    clearance: int
    regions: tuple[Region, ...]
    label: str = ""


@dataclass
class LayerGeometry:
    layer_id: int
    name: str
    copper: list[CopperObstacle] = field(default_factory=list)
    keepouts: list[Region] = field(default_factory=list)
    drills: list[Region] = field(default_factory=list)
    drill_clearance: int = 0
    max_copper_clearance: int = 0
    unfilled_zones: int = 0

    def add_copper(self, obstacle: CopperObstacle) -> None:
        if not obstacle.regions:
            return
        self.copper.append(obstacle)
        if obstacle.clearance > self.max_copper_clearance:
            self.max_copper_clearance = obstacle.clearance

    def add_keepout(self, region: Region) -> None:
        self.keepouts.append(region)

    def add_drill(self, region: Region, clearance: int) -> None:
        self.drills.append(region)
        if clearance > self.drill_clearance:
            self.drill_clearance = clearance


@dataclass
class BoardGeometry:
    board_regions: tuple[Region, ...]
    bbox: BBox
    layers: dict[int, LayerGeometry]


# ---------------------------------------------------------------------------
# Pattern configuration and placement results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PatternConfig:
    shape: str = SHAPE_CIRCLE
    size: int = 1_200_000
    corner_radius: int = 200_000
    gap: int = 300_000
    edge_clearance: int = 500_000
    centered: bool = True
    offset_x: int = 0
    offset_y: int = 0
    max_count: int = 20_000
    avoid_same_net: bool = True

    def __post_init__(self) -> None:
        # Accept floats from callers/tests but store integer nanometres.
        for name in (
            "size",
            "corner_radius",
            "gap",
            "edge_clearance",
            "offset_x",
            "offset_y",
            "max_count",
        ):
            object.__setattr__(self, name, int(getattr(self, name)))

    @property
    def pitch(self) -> int:
        return self.size + self.gap

    @property
    def radius(self) -> int:
        return self.size // 2

    def validate(self) -> None:
        if self.shape not in (SHAPE_CIRCLE, SHAPE_ROUNDED_SQUARE):
            raise ValueError(f"unsupported pattern shape: {self.shape!r}")
        if self.size <= 0:
            raise ValueError("size must be greater than zero")
        if self.gap < 0:
            raise ValueError("gap cannot be negative")
        if self.edge_clearance < 0:
            raise ValueError("edge clearance cannot be negative")
        if self.max_count <= 0:
            raise ValueError("max_count must be greater than zero")
        if self.shape == SHAPE_ROUNDED_SQUARE:
            if self.corner_radius < 0:
                raise ValueError("corner radius cannot be negative")
            if self.corner_radius > self.size / 2:
                raise ValueError("corner radius cannot exceed half of the side length")
        if self.pitch <= 0:
            raise ValueError("pitch must be greater than zero")


@dataclass(frozen=True)
class Placement:
    layer_id: int
    x: int
    y: int


@dataclass
class LayerStats:
    layer_id: int
    layer_name: str
    candidates: int = 0
    placed: int = 0
    skipped_outside: int = 0
    skipped_edge: int = 0
    skipped_copper: int = 0
    skipped_keepout: int = 0
    skipped_drill: int = 0

    @property
    def skipped(self) -> int:
        return (
            self.skipped_outside
            + self.skipped_edge
            + self.skipped_copper
            + self.skipped_keepout
            + self.skipped_drill
        )

    def add_skip(self, reason: str) -> None:
        if reason == SKIP_OUTSIDE:
            self.skipped_outside += 1
        elif reason == SKIP_EDGE:
            self.skipped_edge += 1
        elif reason == SKIP_COPPER:
            self.skipped_copper += 1
        elif reason == SKIP_KEEPOUT:
            self.skipped_keepout += 1
        elif reason == SKIP_DRILL:
            self.skipped_drill += 1
        else:
            raise ValueError(f"unknown skip reason: {reason!r}")


@dataclass
class PlanStats:
    layers: dict[int, LayerStats] = field(default_factory=dict)
    candidates: int = 0
    placed: int = 0
    skipped_outside: int = 0
    skipped_edge: int = 0
    skipped_copper: int = 0
    skipped_keepout: int = 0
    skipped_drill: int = 0

    def finalize(self) -> None:
        self.candidates = sum(s.candidates for s in self.layers.values())
        self.placed = sum(s.placed for s in self.layers.values())
        self.skipped_outside = sum(s.skipped_outside for s in self.layers.values())
        self.skipped_edge = sum(s.skipped_edge for s in self.layers.values())
        self.skipped_copper = sum(s.skipped_copper for s in self.layers.values())
        self.skipped_keepout = sum(s.skipped_keepout for s in self.layers.values())
        self.skipped_drill = sum(s.skipped_drill for s in self.layers.values())

    @property
    def skipped(self) -> int:
        return self.candidates - self.placed


@dataclass
class PlanResult:
    config: PatternConfig
    placements: list[Placement]
    stats: PlanStats
    board_bbox: BBox
    estimated_area: float = 0.0


class PlanCancelled(Exception):
    pass


class TooManyPlacements(Exception):
    def __init__(self, requested: int, limit: int) -> None:
        super().__init__(
            f"pattern would test {requested} positions, above the limit of {limit}"
        )
        self.requested = requested
        self.limit = limit


# ---------------------------------------------------------------------------
# Pattern shape generation
# ---------------------------------------------------------------------------


def _segments_for_radius(radius: int, max_chord: int = 20_000) -> int:
    if radius <= 0:
        return 24
    circumference = 2.0 * math.pi * radius
    n = int(math.ceil(circumference / max_chord))
    return max(32, min(192, n))


def _circle_points(cx: int, cy: int, radius: int, segments: int | None = None) -> list[Point]:
    """Return a polygon that circumscribes the true circle.

    The tiny outward bias keeps the clearance check conservative: the real
    copper is never closer to an obstacle than the polygon used for planning.
    """
    if segments is None:
        segments = _segments_for_radius(radius)
    # Circumscribed radius; also add one nanometre of rounding safety.
    rr = (radius / math.cos(math.pi / segments)) + 1.0
    pts: list[Point] = []
    for i in range(segments):
        angle = 2.0 * math.pi * i / segments
        pts.append((int(round(cx + rr * math.cos(angle))), int(round(cy + rr * math.sin(angle)))))
    return pts


def _corner_segments(radius: int, max_error: int = PLANNING_MARGIN) -> int:
    if radius <= max_error:
        return 4
    ratio = max(-1.0, min(1.0, 1.0 - max_error / float(radius)))
    step = 2.0 * math.acos(ratio)
    if step <= 0.0:
        return 64
    return max(6, min(64, int(math.ceil((math.pi / 2.0) / step))))


def _rounded_square_points(
    cx: int,
    cy: int,
    side: int,
    radius: int,
    segments_per_corner: int = 12,
) -> list[Point]:
    half = side / 2.0
    if radius <= 0:
        return [
            (int(round(cx - half)), int(round(cy - half))),
            (int(round(cx + half)), int(round(cy - half))),
            (int(round(cx + half)), int(round(cy + half))),
            (int(round(cx - half)), int(round(cy + half))),
        ]
    if radius >= half:
        return _circle_points(cx, cy, int(round(half)))

    # In KiCad screen coordinates y grows downwards.  These arcs run
    # clockwise around the shape: TL left->top, TR top->right,
    # BR right->bottom, BL bottom->left.
    corners = (
        (cx - half + radius, cy - half + radius, 180.0),
        (cx + half - radius, cy - half + radius, 270.0),
        (cx + half - radius, cy + half - radius, 0.0),
        (cx - half + radius, cy + half - radius, 90.0),
    )
    pts: list[Point] = []
    for ccx, ccy, start_deg in corners:
        for step in range(segments_per_corner + 1):
            angle = math.radians(start_deg + 90.0 * step / segments_per_corner)
            pts.append(
                (
                    int(round(ccx + radius * math.cos(angle))),
                    int(round(ccy + radius * math.sin(angle))),
                )
            )
    return pts


def pattern_points(config: PatternConfig, cx: int, cy: int) -> list[Point]:
    """Return the candidate copper outline centred at (cx, cy)."""
    if config.shape == SHAPE_CIRCLE:
        return _circle_points(cx, cy, config.radius)
    radius = max(0, min(config.corner_radius, config.size // 2))
    return _rounded_square_points(
        cx,
        cy,
        config.size,
        radius,
        segments_per_corner=_corner_segments(radius),
    )


def pattern_area(config: PatternConfig) -> float:
    """Approximate copper area in square nanometres."""
    if config.shape == SHAPE_CIRCLE:
        return math.pi * (config.size / 2.0) ** 2
    r = max(0.0, min(float(config.corner_radius), config.size / 2.0))
    return config.size * config.size - (4.0 - math.pi) * r * r


# ---------------------------------------------------------------------------
# Spatial indexes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Edge:
    a: Point
    b: Point
    kind: str
    clearance: int
    netcode: int = 0
    label: str = ""


class _EdgeIndex:
    def __init__(self, cell: int = 3_000_000) -> None:
        self.cell = cell
        self.edges: list[_Edge] = []
        self.cells: dict[tuple[int, int], list[int]] = {}

    def add(self, edge: _Edge) -> None:
        idx = len(self.edges)
        self.edges.append(edge)
        for cell in _cells_for_bbox(bbox_of_segment(edge.a, edge.b), self.cell):
            self.cells.setdefault(cell, []).append(idx)

    def query(self, box: BBox) -> list[_Edge]:
        seen: set[int] = set()
        result: list[_Edge] = []
        for cell in _cells_for_bbox(box, self.cell):
            for idx in self.cells.get(cell, ()):
                if idx in seen:
                    continue
                seen.add(idx)
                result.append(self.edges[idx])
        return result


@dataclass(frozen=True)
class _Solid:
    kind: str
    region: Region
    netcode: int = 0
    label: str = ""


class _SolidIndex:
    def __init__(self, cell: int = 3_000_000) -> None:
        self.cell = cell
        self.solids: list[_Solid] = []
        self.cells: dict[tuple[int, int], list[int]] = {}

    def add(self, solid: _Solid) -> None:
        idx = len(self.solids)
        self.solids.append(solid)
        for cell in _cells_for_bbox(solid.region.bbox, self.cell):
            self.cells.setdefault(cell, []).append(idx)

    def query_points(self, points: Sequence[Point]) -> list[_Solid]:
        seen: set[int] = set()
        result: list[_Solid] = []
        for point in points:
            for idx in self.cells.get(_cell_of(point, self.cell), ()):
                if idx in seen:
                    continue
                seen.add(idx)
                result.append(self.solids[idx])
        return result


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------


class _LayerIndex:
    def __init__(self, edges: _EdgeIndex, solids: _SolidIndex, max_clearance: int) -> None:
        self.edges = edges
        self.solids = solids
        self.max_clearance = max(0, max_clearance)


def _edge_conflicts(
    edge: _Edge,
    candidate_points: Sequence[Point],
    candidate_bbox: BBox,
    clearance: int,
) -> bool:
    # Cheap rejection before the point-in-polygon / distance work.
    edge_box = bbox_of_segment(edge.a, edge.b)
    if not bbox_intersects(bbox_inflate(edge_box, clearance), candidate_bbox):
        return False

    # A whole obstacle inside the candidate is just as bad as a crossing.
    if point_in_polygon(edge.a, candidate_points) or point_in_polygon(edge.b, candidate_points):
        return True

    if segment_intersects_polygon(edge.a, edge.b, candidate_points):
        return True

    limit = float(clearance * clearance)
    n = len(candidate_points)
    for i in range(n):
        d = distance_point_segment_sq(edge.a, candidate_points[i], candidate_points[(i + 1) % n])
        if d <= limit:
            return True
        d = distance_point_segment_sq(edge.b, candidate_points[i], candidate_points[(i + 1) % n])
        if d <= limit:
            return True
    # The closest pair can also be a candidate vertex and the interior of the
    # obstacle edge, so check the other direction as well.
    for point in candidate_points:
        if distance_point_segment_sq(point, edge.a, edge.b) <= limit:
            return True
    return False


class Planner:
    """Generate a regular array of isolated copper shapes inside a board."""

    def __init__(
        self,
        board: BoardGeometry,
        config: PatternConfig,
        target_netcode: int | None = None,
        progress: Callable[[int, int], bool] | None = None,
    ) -> None:
        config.validate()
        if not board.board_regions:
            raise ValueError("board outline is empty")
        self.board = board
        self.config = config
        self.target_netcode = target_netcode if target_netcode else None
        self.progress = progress
        self._rel_points = pattern_points(config, 0, 0)
        self._rel_bbox = bbox_from_points(self._rel_points)
        self._layer_indexes: dict[int, _LayerIndex] = {}

    # -- public API ---------------------------------------------------------

    def plan(self) -> PlanResult:
        layers = [self.board.layers[lid] for lid in sorted(self.board.layers)]
        if not layers:
            raise ValueError("no copper layers selected")

        x_count, y_count, x0, y0 = self._grid()
        total = x_count * y_count * len(layers)
        if total <= 0:
            stats = PlanStats()
            for layer in layers:
                stats.layers[layer.layer_id] = LayerStats(layer.layer_id, layer.name)
            stats.finalize()
            return PlanResult(self.config, [], stats, self.board.bbox, 0.0)
        if total > self.config.max_count:
            raise TooManyPlacements(total, self.config.max_count)

        stats = PlanStats()
        for layer in layers:
            stats.layers[layer.layer_id] = LayerStats(layer.layer_id, layer.name)

        placements: list[Placement] = []
        done = 0
        self._report(0, total)
        step = max(1, total // 100)
        for layer in layers:
            layer_stats = stats.layers[layer.layer_id]
            index = self._index_for(layer)
            for row in range(y_count):
                cy = y0 + row * self.config.pitch
                for col in range(x_count):
                    cx = x0 + col * self.config.pitch
                    layer_stats.candidates += 1
                    done += 1
                    reason = self._check(cx, cy, layer, index)
                    if reason is None:
                        placements.append(Placement(layer.layer_id, cx, cy))
                        layer_stats.placed += 1
                    else:
                        layer_stats.add_skip(reason)
                    if done % step == 0:
                        self._report(done, total)
        self._report(total, total)
        stats.finalize()
        return PlanResult(
            self.config,
            placements,
            stats,
            self.board.bbox,
            pattern_area(self.config) * len(placements),
        )

    # -- grid construction --------------------------------------------------

    def _grid(self) -> tuple[int, int, int, int]:
        xmin, ymin, xmax, ymax = self.board.bbox
        width = xmax - xmin
        height = ymax - ymin
        size = self.config.size
        pitch = self.config.pitch
        # Keep a little extra room beyond the exact board rule so that the
        # polygon approximation margin cannot reject the outermost row/column.
        edge = self.config.edge_clearance + PLANNING_MARGIN
        half = size // 2

        if self.config.centered:
            usable_w = width - 2 * edge - size
            usable_h = height - 2 * edge - size
            x_count = usable_w // pitch + 1 if usable_w >= 0 else 0
            y_count = usable_h // pitch + 1 if usable_h >= 0 else 0
            span_x = (x_count - 1) * pitch if x_count else 0
            span_y = (y_count - 1) * pitch if y_count else 0
            x0 = (
                xmin + edge + half + (usable_w - span_x) // 2 + self.config.offset_x
                if x_count
                else xmin + edge + half + self.config.offset_x
            )
            y0 = (
                ymin + edge + half + (usable_h - span_y) // 2 + self.config.offset_y
                if y_count
                else ymin + edge + half + self.config.offset_y
            )
            return x_count, y_count, x0, y0

        x0 = xmin + edge + half + self.config.offset_x
        y0 = ymin + edge + half + self.config.offset_y
        max_cx = xmax - edge - half
        max_cy = ymax - edge - half
        x_count = max(0, (max_cx - x0) // pitch + 1) if max_cx >= x0 else 0
        y_count = max(0, (max_cy - y0) // pitch + 1) if max_cy >= y0 else 0
        return x_count, y_count, x0, y0

    # -- indexes ------------------------------------------------------------

    def _index_for(self, layer: LayerGeometry) -> _LayerIndex:
        cached = self._layer_indexes.get(layer.layer_id)
        if cached is not None:
            return cached

        edges = _EdgeIndex()
        solids = _SolidIndex()
        max_clearance = max(
            1,
            self.config.edge_clearance,
            layer.max_copper_clearance,
            layer.drill_clearance,
        ) + PLANNING_MARGIN

        for region in self.board.board_regions:
            for ring in region.iter_rings():
                for a, b in ring.iter_edges():
                    edges.add(
                        _Edge(a, b, KIND_BOARD, self.config.edge_clearance, label="board edge")
                    )

        for obstacle in layer.copper:
            for region in obstacle.regions:
                solids.add(
                    _Solid(KIND_COPPER, region, obstacle.netcode, obstacle.label)
                )
                for ring in region.iter_rings():
                    for a, b in ring.iter_edges():
                        edges.add(
                            _Edge(
                                a,
                                b,
                                KIND_COPPER,
                                obstacle.clearance,
                                obstacle.netcode,
                                obstacle.label,
                            )
                        )

        for region in layer.keepouts:
            solids.add(_Solid(KIND_KEEPOUT, region, label="keepout"))
            for ring in region.iter_rings():
                for a, b in ring.iter_edges():
                    edges.add(_Edge(a, b, KIND_KEEPOUT, 0, label="keepout"))

        for region in layer.drills:
            solids.add(_Solid(KIND_DRILL, region, label="drill"))
            for ring in region.iter_rings():
                for a, b in ring.iter_edges():
                    edges.add(
                        _Edge(a, b, KIND_DRILL, layer.drill_clearance, label="drill")
                    )

        index = _LayerIndex(edges, solids, max_clearance)
        self._layer_indexes[layer.layer_id] = index
        return index

    # -- candidate checking -------------------------------------------------

    def _check(self, cx: int, cy: int, layer: LayerGeometry, index: _LayerIndex) -> str | None:
        candidate = [(px + cx, py + cy) for px, py in self._rel_points]
        cbox = (
            self._rel_bbox[0] + cx,
            self._rel_bbox[1] + cy,
            self._rel_bbox[2] + cx,
            self._rel_bbox[3] + cy,
        )
        center = (cx, cy)

        containing = None
        for region in self.board.board_regions:
            if bbox_contains(region.bbox, center) and region.outer.contains(center):
                containing = region
                break
        if containing is None:
            return SKIP_OUTSIDE

        if any(not containing.outer.contains(p) for p in candidate):
            return SKIP_EDGE
        if containing.holes:
            points_to_test = (center,) + tuple(candidate)
            for hole in containing.holes:
                if any(hole.contains(p) for p in points_to_test):
                    return SKIP_EDGE

        query_box = bbox_inflate(cbox, index.max_clearance)
        for edge in index.edges.query(query_box):
            if (
                edge.kind == KIND_COPPER
                and self.target_netcode is not None
                and edge.netcode == self.target_netcode
                and not self.config.avoid_same_net
            ):
                continue
            if edge.kind == KIND_BOARD:
                if _edge_conflicts(
                    edge, candidate, cbox, self.config.edge_clearance + PLANNING_MARGIN
                ):
                    return SKIP_EDGE
            elif edge.kind == KIND_COPPER:
                if _edge_conflicts(
                    edge, candidate, cbox, edge.clearance + PLANNING_MARGIN
                ):
                    return SKIP_COPPER
            elif edge.kind == KIND_KEEPOUT:
                if _edge_conflicts(edge, candidate, cbox, PLANNING_MARGIN):
                    return SKIP_KEEPOUT
            elif edge.kind == KIND_DRILL:
                if _edge_conflicts(
                    edge, candidate, cbox, layer.drill_clearance + PLANNING_MARGIN
                ):
                    return SKIP_DRILL

        for solid in index.solids.query_points((center,) + tuple(candidate)):
            if (
                solid.kind == KIND_COPPER
                and self.target_netcode is not None
                and solid.netcode == self.target_netcode
                and not self.config.avoid_same_net
            ):
                continue
            if solid.region.contains(center) or any(solid.region.contains(p) for p in candidate):
                if solid.kind == KIND_COPPER:
                    return SKIP_COPPER
                if solid.kind == KIND_KEEPOUT:
                    return SKIP_KEEPOUT
                if solid.kind == KIND_DRILL:
                    return SKIP_DRILL
        return None

    # -- progress -----------------------------------------------------------

    def _report(self, done: int, total: int) -> None:
        if self.progress is None:
            return
        if not self.progress(done, total):
            raise PlanCancelled()


# ---------------------------------------------------------------------------
# Human-readable helpers used by the UI
# ---------------------------------------------------------------------------


def reason_label(reason: str) -> str:
    return {
        SKIP_OUTSIDE: "板框外",
        SKIP_EDGE: "板边间距",
        SKIP_COPPER: "铜箔间距",
        SKIP_KEEPOUT: "禁布区",
        SKIP_DRILL: "钻孔间距",
    }.get(reason, reason)


def summarize_plan(result: PlanResult) -> str:
    stats = result.stats
    lines = [
        f"候选位置: {stats.candidates}",
        f"可放置:   {stats.placed}",
        f"跳过:     {stats.skipped} "
        f"(板外 {stats.skipped_outside}, 板边 {stats.skipped_edge}, "
        f"铜箔 {stats.skipped_copper}, 禁布 {stats.skipped_keepout}, "
        f"钻孔 {stats.skipped_drill})",
    ]
    for layer_id in sorted(stats.layers):
        ls = stats.layers[layer_id]
        lines.append(f"  {ls.layer_name}: {ls.placed} 个")
    if result.estimated_area:
        lines.append(f"预计铜面积: {result.estimated_area / 1e12:.2f} mm²")
    return "\n".join(lines)

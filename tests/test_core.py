"""Pure-Python tests for the Copper Thief placement core."""

from __future__ import annotations

import math
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import core  # noqa: E402

MM = 1_000_000


def rect_region(x0: int, y0: int, x1: int, y1: int) -> core.Region:
    return core.Region(
        core.Ring(((x0, y0), (x1, y0), (x1, y1), (x0, y1)))
    )


def make_board(
    width: int = 30 * MM,
    height: int = 20 * MM,
    layers: tuple[int, ...] = (0,),
    holes: tuple[core.Ring, ...] = (),
) -> core.BoardGeometry:
    outer = core.Ring(((0, 0), (width, 0), (width, height), (0, height)))
    region = core.Region(outer, holes)
    layer_map = {lid: core.LayerGeometry(lid, f"L{lid}") for lid in layers}
    return core.BoardGeometry((region,), outer.bbox, layer_map)


def min_distance_between_polygons(a: list[core.Point], b: list[core.Point]) -> float:
    best = float("inf")
    for point in a:
        for j in range(len(b)):
            d = core.distance_point_segment_sq(point, b[j], b[(j + 1) % len(b)])
            if d < best:
                best = d
    for point in b:
        for j in range(len(a)):
            d = core.distance_point_segment_sq(point, a[j], a[(j + 1) % len(a)])
            if d < best:
                best = d
    return math.sqrt(best)


class GeometryTests(unittest.TestCase):
    def test_circle_polygon_is_conservative(self):
        cfg = core.PatternConfig(shape=core.SHAPE_CIRCLE, size=2 * MM)
        points = core.pattern_points(cfg, 10 * MM, 10 * MM)
        self.assertGreater(len(points), 16)
        for x, y in points:
            dx = x - 10 * MM
            dy = y - 10 * MM
            self.assertGreaterEqual(math.hypot(dx, dy), cfg.radius)

    def test_rounded_square_is_square(self):
        cfg = core.PatternConfig(
            shape=core.SHAPE_ROUNDED_SQUARE,
            size=4 * MM,
            corner_radius=1 * MM,
        )
        points = core.pattern_points(cfg, 0, 0)
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        self.assertEqual(max(xs) - min(xs), cfg.size)
        self.assertEqual(max(ys) - min(ys), cfg.size)

    def test_zero_radius_is_sharp_square(self):
        cfg = core.PatternConfig(
            shape=core.SHAPE_ROUNDED_SQUARE,
            size=3 * MM,
            corner_radius=0,
        )
        self.assertEqual(len(core.pattern_points(cfg, 0, 0)), 4)

    def test_half_radius_becomes_circle(self):
        cfg = core.PatternConfig(
            shape=core.SHAPE_ROUNDED_SQUARE,
            size=3 * MM,
            corner_radius=1.5 * MM,
        )
        points = core.pattern_points(cfg, 0, 0)
        radii = [math.hypot(x, y) for x, y in points]
        self.assertTrue(all(abs(r - 1.5 * MM) < 3_000 for r in radii))

    def test_ring_hole_containment(self):
        outer = core.Ring(((0, 0), (10, 0), (10, 10), (0, 10)))
        hole = core.Ring(((3, 3), (7, 3), (7, 7), (3, 7)))
        region = core.Region(outer, (hole,))
        self.assertTrue(region.contains((1, 1)))
        self.assertFalse(region.contains((5, 5)))
        self.assertFalse(region.contains((20, 5)))

    def test_large_ring_row_index_matches_ray_cast(self):
        import random

        cx, cy, radius = 50 * MM, 40 * MM, 30 * MM
        points = tuple(
            (
                int(cx + radius * math.cos(2 * math.pi * i / 1000)),
                int(cy + radius * math.sin(2 * math.pi * i / 1000)),
            )
            for i in range(1000)
        )
        ring = core.Ring(points)
        rng = random.Random(1234)
        for _ in range(300):
            point = (
                rng.randint(cx - radius - 5 * MM, cx + radius + 5 * MM),
                rng.randint(cy - radius - 5 * MM, cy + radius + 5 * MM),
            )
            self.assertEqual(ring.contains(point), core.point_in_polygon(point, ring.points))


class PlannerTests(unittest.TestCase):
    def _plan(
        self,
        board: core.BoardGeometry,
        cfg: core.PatternConfig,
        netcode: int | None = None,
    ) -> core.PlanResult:
        return core.Planner(board, cfg, target_netcode=netcode).plan()

    def test_basic_array_fills_inside_edge_clearance(self):
        board = make_board()
        cfg = core.PatternConfig(
            shape=core.SHAPE_CIRCLE,
            size=1 * MM,
            gap=0.5 * MM,
            edge_clearance=0.5 * MM,
            max_count=10000,
        )
        result = self._plan(board, cfg)
        self.assertGreater(result.stats.placed, 100)
        self.assertEqual(result.stats.skipped_edge, 0)
        for placement in result.placements:
            points = core.pattern_points(cfg, placement.x, placement.y)
            box = core.bbox_from_points(points)
            self.assertGreaterEqual(box[0], cfg.edge_clearance)
            self.assertGreaterEqual(box[1], cfg.edge_clearance)
            self.assertLessEqual(box[2], 30 * MM - cfg.edge_clearance)
            self.assertLessEqual(box[3], 20 * MM - cfg.edge_clearance)

    def test_copper_clearance(self):
        board = make_board()
        obstacle = rect_region(13 * MM, 8 * MM, 17 * MM, 12 * MM)
        clearance = 0.5 * MM
        board.layers[0].add_copper(core.CopperObstacle(2, clearance, (obstacle,), "pad"))
        cfg = core.PatternConfig(
            shape=core.SHAPE_CIRCLE,
            size=1 * MM,
            gap=0.5 * MM,
            edge_clearance=0.5 * MM,
            max_count=10000,
        )
        result = self._plan(board, cfg, netcode=1)
        self.assertGreater(result.stats.skipped_copper, 0)
        obstacle_points = list(obstacle.outer.points)
        for placement in result.placements:
            points = core.pattern_points(cfg, placement.x, placement.y)
            self.assertFalse(any(obstacle.contains(p) for p in points))
            self.assertFalse(
                any(core.point_in_polygon(p, points) for p in obstacle_points)
            )
            distance = min_distance_between_polygons(points, obstacle_points)
            self.assertGreaterEqual(distance, clearance + core.PLANNING_MARGIN - 1)

    def test_same_net_option(self):
        board = make_board()
        obstacle = rect_region(10 * MM, 5 * MM, 20 * MM, 15 * MM)
        board.layers[0].add_copper(core.CopperObstacle(1, 0.4 * MM, (obstacle,), "zone"))
        avoid_cfg = core.PatternConfig(
            shape=core.SHAPE_CIRCLE,
            size=1 * MM,
            gap=0.4 * MM,
            edge_clearance=0.5 * MM,
            avoid_same_net=True,
            max_count=10000,
        )
        allow_cfg = core.PatternConfig(
            shape=core.SHAPE_CIRCLE,
            size=1 * MM,
            gap=0.4 * MM,
            edge_clearance=0.5 * MM,
            avoid_same_net=False,
            max_count=10000,
        )
        avoided = self._plan(board, avoid_cfg, netcode=1)
        allowed = self._plan(board, allow_cfg, netcode=1)
        self.assertGreater(avoided.stats.skipped_copper, 0)
        self.assertEqual(allowed.stats.skipped_copper, 0)
        self.assertGreater(allowed.stats.placed, avoided.stats.placed)

    def test_keepout(self):
        board = make_board()
        keepout = rect_region(10 * MM, 6 * MM, 20 * MM, 14 * MM)
        board.layers[0].add_keepout(keepout)
        cfg = core.PatternConfig(
            shape=core.SHAPE_CIRCLE,
            size=1 * MM,
            gap=0.5 * MM,
            edge_clearance=0.5 * MM,
            max_count=10000,
        )
        result = self._plan(board, cfg)
        self.assertGreater(result.stats.skipped_keepout, 0)
        for placement in result.placements:
            points = core.pattern_points(cfg, placement.x, placement.y)
            self.assertFalse(any(keepout.contains(p) for p in points))

    def test_board_cutout(self):
        hole = core.Ring(
            (
                (12 * MM, 8 * MM),
                (18 * MM, 8 * MM),
                (18 * MM, 12 * MM),
                (12 * MM, 12 * MM),
            )
        )
        board = make_board(holes=(hole,))
        cfg = core.PatternConfig(
            shape=core.SHAPE_CIRCLE,
            size=1 * MM,
            gap=0.5 * MM,
            edge_clearance=0.5 * MM,
            max_count=10000,
        )
        result = self._plan(board, cfg)
        self.assertGreater(result.stats.skipped_edge, 0)
        for placement in result.placements:
            points = core.pattern_points(cfg, placement.x, placement.y)
            self.assertFalse(any(hole.contains(p) for p in points))
            self.assertFalse(any(core.point_in_polygon(p, points) for p in hole.points))

    def test_drill_is_forbidden(self):
        board = make_board()
        drill = rect_region(12 * MM, 9 * MM, 14 * MM, 11 * MM)
        board.layers[0].add_drill(drill, 0.25 * MM)
        cfg = core.PatternConfig(
            shape=core.SHAPE_CIRCLE,
            size=1 * MM,
            gap=0.5 * MM,
            edge_clearance=0.5 * MM,
            max_count=10000,
        )
        result = self._plan(board, cfg)
        self.assertGreater(result.stats.skipped_drill, 0)
        for placement in result.placements:
            points = core.pattern_points(cfg, placement.x, placement.y)
            self.assertFalse(any(drill.contains(p) for p in points))

    def test_multiple_layers(self):
        board = make_board(layers=(0, 2))
        cfg = core.PatternConfig(
            shape=core.SHAPE_ROUNDED_SQUARE,
            size=1.2 * MM,
            corner_radius=0.2 * MM,
            gap=0.4 * MM,
            edge_clearance=0.5 * MM,
            max_count=20000,
        )
        result = self._plan(board, cfg)
        layer_ids = {p.layer_id for p in result.placements}
        self.assertEqual(layer_ids, {0, 2})
        self.assertEqual(result.stats.placed, sum(s.placed for s in result.stats.layers.values()))

    def test_cancel(self):
        board = make_board()
        cfg = core.PatternConfig(max_count=10000)
        calls = {"n": 0}

        def progress(done: int, total: int) -> bool:
            calls["n"] += 1
            return done < 100

        with self.assertRaises(core.PlanCancelled):
            core.Planner(board, cfg, progress=progress).plan()
        self.assertGreater(calls["n"], 0)

    def test_too_many_placements(self):
        board = make_board()
        cfg = core.PatternConfig(size=0.2 * MM, gap=0.1 * MM, max_count=100)
        with self.assertRaises(core.TooManyPlacements):
            self._plan(board, cfg)

    def test_config_validation(self):
        with self.assertRaises(ValueError):
            core.PatternConfig(
                shape=core.SHAPE_ROUNDED_SQUARE,
                size=2 * MM,
                corner_radius=2 * MM,
            ).validate()
        with self.assertRaises(ValueError):
            core.PatternConfig(size=0).validate()
        with self.assertRaises(ValueError):
            core.PatternConfig(gap=-1).validate()


if __name__ == "__main__":
    unittest.main()

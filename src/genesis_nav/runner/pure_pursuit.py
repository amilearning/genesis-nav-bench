"""
Pure-pursuit controller for diff-drive robots.

Given:
  - a dense path (list of (x, y) waypoints, e.g. from smoother.densify_*)
  - current robot pose (x, y, yaw)
  - target speed
  - lookahead distance

Compute (v_lin, omega) by aiming the robot at a "carrot" point on the path
that's `lookahead` metres ahead. Lookahead is what lets the controller
gracefully cut corners instead of overshooting / oscillating.

Tunables (with sensible Husky defaults):
  lookahead_m      = 1.2     # carrot distance
  target_v         = 1.0     # m/s
  v_slowdown_kappa = 0.6     # speed = target * exp(-v_slowdown_kappa * dist_to_corner)
  max_omega        = 1.5     # rad/s ceiling
  wheel_max        = 12.0    # rad/s ceiling per wheel (Husky URDF allows ~15)
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class PurePursuitConfig:
    lookahead_m: float = 1.2
    target_v: float = 1.0
    max_omega: float = 1.5
    wheel_max: float = 12.0
    wheel_radius: float = 0.178      # Husky
    track: float = 0.555             # Husky wheelbase width
    goal_tolerance_m: float = 0.30
    slow_in_turn_threshold: float = 0.6   # |yaw_err| above this → slow down


class PurePursuit:
    """Stateful pure-pursuit follower; call `step(...)` per sim tick."""

    def __init__(self, path_xy: list[list[float]], cfg: PurePursuitConfig | None = None):
        if len(path_xy) < 2:
            raise ValueError("path must have ≥2 points")
        self.path = path_xy
        self.cfg = cfg or PurePursuitConfig()
        self._search_idx = 0   # for cheap nearest-point lookup
        self.done = False

    def _nearest_idx(self, x: float, y: float) -> int:
        """Closest path index from a small window around the previous match
        (so we don't re-scan the whole path every tick)."""
        best_i = self._search_idx
        best_d2 = float("inf")
        N = len(self.path)
        # Scan a window — never goes backwards once we've passed a point.
        for i in range(self._search_idx, min(self._search_idx + 50, N)):
            dx = self.path[i][0] - x
            dy = self.path[i][1] - y
            d2 = dx * dx + dy * dy
            if d2 < best_d2:
                best_d2 = d2; best_i = i
        self._search_idx = best_i
        return best_i

    def _carrot(self, near_idx: int, x: float, y: float) -> tuple[float, float, int]:
        """Find the first path point >= lookahead_m ahead of (x, y), starting
        from near_idx. Returns (cx, cy, idx)."""
        target_d = self.cfg.lookahead_m
        N = len(self.path)
        # Walk forward and accumulate arc-length distance to the path point.
        for i in range(near_idx, N):
            dx = self.path[i][0] - x
            dy = self.path[i][1] - y
            if math.hypot(dx, dy) >= target_d:
                return self.path[i][0], self.path[i][1], i
        # Past the end of the path → aim at the final goal
        return self.path[-1][0], self.path[-1][1], N - 1

    def step(self, x: float, y: float, yaw: float) -> tuple[float, float]:
        """Return (v_lin, omega) command for the current pose. Sets self.done
        when within goal_tolerance_m of the final point."""
        cfg = self.cfg
        gx, gy = self.path[-1]
        if math.hypot(gx - x, gy - y) < cfg.goal_tolerance_m:
            self.done = True
            return 0.0, 0.0
        near = self._nearest_idx(x, y)
        cx, cy, _ = self._carrot(near, x, y)
        dx, dy = cx - x, cy - y
        yaw_to_carrot = math.atan2(dy, dx)
        yaw_err = yaw_to_carrot - yaw
        # wrap to [-pi, pi]
        while yaw_err >  math.pi: yaw_err -= 2 * math.pi
        while yaw_err < -math.pi: yaw_err += 2 * math.pi
        # Speed: slow down when the carrot is sideways (sharp turn coming)
        v_scale = 0.3 if abs(yaw_err) > cfg.slow_in_turn_threshold else 1.0
        v_lin = cfg.target_v * v_scale
        # Pure-pursuit curvature: kappa = 2 * sin(yaw_err) / lookahead
        kappa = 2.0 * math.sin(yaw_err) / max(0.1, cfg.lookahead_m)
        omega = v_lin * kappa
        omega = max(-cfg.max_omega, min(cfg.max_omega, omega))
        return v_lin, omega

    def wheel_velocities(self, v_lin: float, omega: float) -> tuple[float, float]:
        """Convert body (v, omega) to (left_wheel_vel, right_wheel_vel) rad/s."""
        c = self.cfg
        v_l = (v_lin - omega * c.track / 2.0) / c.wheel_radius
        v_r = (v_lin + omega * c.track / 2.0) / c.wheel_radius
        return (max(-c.wheel_max, min(c.wheel_max, v_l)),
                max(-c.wheel_max, min(c.wheel_max, v_r)))

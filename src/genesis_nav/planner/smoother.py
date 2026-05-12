"""
Path smoother — turn a sparse waypoint sequence into a dense path the
controller can pure-pursuit.

Two methods:
  - `densify_linear(waypoints, step=0.1)` — straight-line resampling. Use when
    waypoints already lie on safe straight segments (A*+LOS output).
  - `densify_spline(waypoints, step=0.1)` — natural cubic spline + arc-length
    resampling. Smooths sharp corners; only safe when the spline stays inside
    the obstacle-free region.
"""
from __future__ import annotations

import math
import numpy as np


def densify_linear(waypoints: list[list[float]], step: float = 0.1) -> list[list[float]]:
    """Resample a polyline at fixed arc length. Keeps the path inside any
    line-of-sight-clear corridor (since the path stays straight between input
    waypoints)."""
    if len(waypoints) < 2:
        return [list(p) for p in waypoints]
    out: list[list[float]] = [list(waypoints[0])]
    for a, b in zip(waypoints[:-1], waypoints[1:]):
        ax, ay = a; bx, by = b
        d = math.hypot(bx - ax, by - ay)
        n = max(1, int(round(d / step)))
        for k in range(1, n + 1):
            t = k / n
            out.append([ax + t * (bx - ax), ay + t * (by - ay)])
    return out


def densify_spline(waypoints: list[list[float]], step: float = 0.1) -> list[list[float]]:
    """Fit a natural cubic spline through the waypoints, then resample by arc
    length. ~10× smoother than linear at corners but can shortcut through
    obstacles if the input waypoints sit too close to them — use only with
    a planner that already has corner inflation (we do)."""
    wp = np.asarray(waypoints, dtype=float)
    if len(wp) < 3:
        return densify_linear(waypoints, step)

    # Cumulative chord-length as parameter (natural choice)
    seg = np.linalg.norm(np.diff(wp, axis=0), axis=1)
    u = np.concatenate(([0.0], np.cumsum(seg)))
    total = u[-1]

    # Fit per-axis natural cubic. scipy is heavy; do it by hand with a
    # band-solve so we don't add deps.
    def cubic_spline_natural(t, y):
        n = len(t)
        h = np.diff(t)
        A = np.zeros((n, n))
        b = np.zeros(n)
        A[0, 0] = 1; A[-1, -1] = 1
        for i in range(1, n - 1):
            A[i, i-1] = h[i-1]
            A[i, i]   = 2 * (h[i-1] + h[i])
            A[i, i+1] = h[i]
            b[i] = 3 * ((y[i+1] - y[i]) / h[i] - (y[i] - y[i-1]) / h[i-1])
        c = np.linalg.solve(A, b)
        a = y[:-1]
        d_coef = (c[1:] - c[:-1]) / (3 * h)
        b_coef = (y[1:] - y[:-1]) / h - h * (2 * c[:-1] + c[1:]) / 3
        return a, b_coef, c[:-1], d_coef

    ax, bx, cx, dx = cubic_spline_natural(u, wp[:, 0])
    ay, by_, cy, dy_ = cubic_spline_natural(u, wp[:, 1])

    def eval_seg(i, dt):
        return (ax[i] + bx[i]*dt + cx[i]*dt**2 + dx[i]*dt**3,
                ay[i] + by_[i]*dt + cy[i]*dt**2 + dy_[i]*dt**3)

    # Sample at fixed `step` along param `u`. Since u is chord-length, the
    # spline arc length is close to u; sub-segment density makes it close
    # enough for control purposes.
    n_pts = max(2, int(round(total / step)) + 1)
    us = np.linspace(0, total, n_pts)
    out: list[list[float]] = []
    seg_idx = 0
    for ui in us:
        while seg_idx < len(u) - 2 and ui > u[seg_idx + 1]:
            seg_idx += 1
        dt = ui - u[seg_idx]
        x, y = eval_seg(seg_idx, dt)
        out.append([float(x), float(y)])
    return out


def path_length(points: list[list[float]]) -> float:
    if len(points) < 2: return 0.0
    arr = np.asarray(points, dtype=float)
    return float(np.linalg.norm(np.diff(arr, axis=0), axis=1).sum())


def path_max_curvature(points: list[list[float]]) -> float:
    """Approximate max |curvature| along the path (1/m). Useful sanity check."""
    if len(points) < 3: return 0.0
    arr = np.asarray(points, dtype=float)
    diffs = np.diff(arr, axis=0)
    angles = np.arctan2(diffs[:, 1], diffs[:, 0])
    dtheta = np.diff(angles)
    # wrap
    dtheta = (dtheta + math.pi) % (2 * math.pi) - math.pi
    ds = np.linalg.norm(diffs[1:], axis=1)
    valid = ds > 1e-6
    if not valid.any(): return 0.0
    kappa = np.abs(dtheta[valid]) / ds[valid]
    return float(kappa.max())

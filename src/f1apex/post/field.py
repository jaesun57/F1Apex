"""Extract a mid-plane velocity slice as a regular grid, for browser animation.

The browser advects particles through this field with bilinear interpolation,
which requires a *structured* grid - scattered polydata from a plain slice is
useless for that. So the volume is sampled onto a regular lattice instead.

ARRAY ORDERING - the browser must index this identically or the animation looks
plausible and is completely wrong:

    index = iz * nx + ix          (x is the fast axis, z the slow axis)

This is also VTK's own point ordering for an ImageData with dimensions
(nx, 1, nz), so no transposition happens anywhere in the pipeline.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pyvista as pv

from f1apex.post.streamlines import load_volume


def _round_sig(a: np.ndarray, sig: int = 4) -> list[float]:
    """Round to significant digits and guarantee JSON-safe finite values.

    json.dumps emits bare NaN/Infinity, which is invalid JSON: the browser's
    JSON.parse throws and the failure surfaces far from its cause.
    """
    a = np.nan_to_num(np.asarray(a, dtype=float), nan=0.0, posinf=0.0, neginf=0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        mag = np.where(a == 0, 0, np.floor(np.log10(np.abs(a))))
    factor = np.power(10.0, sig - 1 - mag)
    out = np.where(a == 0, 0.0, np.round(a * factor) / factor)
    return [float(v) for v in out]


def extract_midplane(
    case_dir: Path,
    body_bounds: dict,
    nx: int = 320,
    nz: int = 160,
    upstream: float = 1.5,
    downstream: float = 3.5,
    ceiling: float = 3.5,
) -> dict:
    """Sample the y=0 plane onto a regular grid.

    Extents are multiples of the body size so the framing is sensible for a
    0.4 m block or a 5.5 m car without retuning.
    """
    volume = load_volume(case_dir)

    (bx0, bx1) = body_bounds["x"]
    (bz0, bz1) = body_bounds["z"]
    length = bx1 - bx0
    height = max(bz1, 1e-6)

    vx0, vx1, vy0, vy1, vz0, vz1 = volume.bounds
    x0 = max(bx0 - upstream * length, vx0)
    x1 = min(bx1 + downstream * length, vx1)
    z0 = max(0.0, vz0)
    z1 = min(ceiling * height, vz1)

    # ImageData with a single cell in y puts the plane exactly at y=0 and gives
    # point ordering x-fastest, which is the ordering documented above.
    dx = (x1 - x0) / (nx - 1)
    dz = (z1 - z0) / (nz - 1)
    grid = pv.ImageData(dimensions=(nx, 1, nz), spacing=(dx, 1.0, dz), origin=(x0, 0.0, z0))

    sampled = grid.sample(volume)
    u_all = np.asarray(sampled["U"], dtype=float)
    if "vtkValidPointMask" in sampled.point_data:
        valid = np.asarray(sampled["vtkValidPointMask"], dtype=float)
    else:
        valid = np.ones(len(u_all), dtype=float)

    u = u_all[:, 0]
    w = u_all[:, 2]

    pts = np.asarray(sampled.points, dtype=float)
    inside_body = (
        (pts[:, 0] >= bx0) & (pts[:, 0] <= bx1) & (pts[:, 2] >= bz0) & (pts[:, 2] <= bz1)
    )
    # A point that failed to sample is either inside the solid or outside the
    # mesh; either way particles must not enter it.
    mask = (inside_body | (valid < 0.5)).astype(int)

    speed = np.sqrt(u**2 + w**2)
    live = speed[mask == 0]
    # 99.5th percentile, not the raw maximum: a single bad cell near the ground
    # contact produced 215 m/s against a 50 m/s freestream in an earlier run,
    # and the raw max would flatten the colour scale for the whole animation.
    speed_max = float(np.percentile(live, 99.5)) if live.size else 1.0
    if not math.isfinite(speed_max) or speed_max <= 0:
        speed_max = 1.0

    return {
        "nx": int(nx),
        "nz": int(nz),
        "x0": round(float(x0), 6),
        "x1": round(float(x1), 6),
        "z0": round(float(z0), 6),
        "z1": round(float(z1), 6),
        "ordering": "index = iz * nx + ix",
        "u": _round_sig(u),
        "w": _round_sig(w),
        "mask": [int(v) for v in mask],
        "speed_max": round(speed_max, 4),
        "body": {
            "x0": round(float(bx0), 6),
            "x1": round(float(bx1), 6),
            "z0": round(float(bz0), 6),
            "z1": round(float(bz1), 6),
        },
    }


def write_field(field: dict, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # allow_nan=False turns a silent bad payload into a loud failure here,
    # rather than a JSON.parse error in the browser.
    path.write_text(json.dumps(field, separators=(",", ":"), allow_nan=False))
    return path

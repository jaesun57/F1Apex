"""Export 3D streamlines as flat buffers for a WebGL viewer.

The browser draws these directly as GL_LINES and animates bright particles
riding along them. That is far cheaper than shipping the whole 3D velocity
field: a volume grid dense enough to advect through would be tens of megabytes,
while a few hundred precomputed polylines is under a megabyte and gives the
same visual result for a steady field.

Layout is flat and index-addressed because that is what maps onto GL buffers
without any per-frame restructuring:

    positions   [x0,y0,z0, x1,y1,z1, ...]   3 floats per vertex
    speeds      [s0, s1, ...]               1 float  per vertex
    offsets[i]  first vertex index of line i
    counts[i]   number of vertices in line i
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pyvista as pv

from f1apex.post.streamlines import SeedRake, load_volume, trace


def _seed_3d(body_bounds: dict, n_y: int, n_z: int, x: float) -> SeedRake:
    (by0, by1) = body_bounds["y"]
    (bz0, bz1) = body_bounds["z"]
    return SeedRake(
        x=x,
        y_half=1.7 * max(abs(by0), abs(by1)),
        z_min=0.004,
        z_max=bz1 * 1.7,
        n_y=n_y,
        n_z=n_z,
    )


def export_tracks(
    case_dir: Path,
    body_bounds: dict,
    n_y: int = 19,
    n_z: int = 11,
    max_length: float = 3.2,
    min_points: int = 6,
    decimate: int = 2,
) -> dict:
    """Trace 3D streamlines and flatten them into GL-ready buffers.

    `decimate` drops every Nth vertex. Streamline integrators emit points far
    denser than a screen can show, and halving them roughly halves the payload
    with no visible difference.
    """
    volume = load_volume(case_dir)
    rake = _seed_3d(body_bounds, n_y, n_z, x=body_bounds["x"][0] - 0.55)
    lines = trace(volume, rake, max_length=max_length)

    # PyVista returns one polydata with a packed connectivity array:
    # [n0, i0, i1, ..., n1, j0, j1, ...]
    pts = np.asarray(lines.points, dtype=float)
    spd = np.asarray(lines["speed"], dtype=float)
    conn = np.asarray(lines.lines, dtype=np.int64)

    positions: list[float] = []
    speeds: list[float] = []
    offsets: list[int] = []
    counts: list[int] = []

    i = 0
    while i < len(conn):
        n = int(conn[i])
        idx = conn[i + 1 : i + 1 + n]
        i += n + 1
        if n < min_points:
            continue
        idx = idx[::decimate]
        if len(idx) < 3:
            continue
        offsets.append(len(speeds))
        counts.append(len(idx))
        for k in idx:
            p = pts[k]
            positions.extend((float(p[0]), float(p[1]), float(p[2])))
            speeds.append(float(spd[k]))

    speeds_arr = np.asarray(speeds)
    # 99.5th percentile rather than the raw maximum - a single bad cell near the
    # ground contact produced 215 m/s against a 50 m/s freestream in an earlier
    # run, and using the max would wash out the whole colour scale.
    speed_max = float(np.percentile(speeds_arr, 99.5)) if speeds_arr.size else 1.0

    pos = np.asarray(positions).reshape(-1, 3)
    return {
        "n_lines": len(counts),
        "n_vertices": len(speeds),
        "positions": [round(float(v), 5) for v in np.asarray(positions)],
        "speeds": [round(float(v), 3) for v in speeds],
        "offsets": offsets,
        "counts": counts,
        "speed_max": round(speed_max, 4),
        "bounds": {
            "x": [round(float(pos[:, 0].min()), 5), round(float(pos[:, 0].max()), 5)],
            "y": [round(float(pos[:, 1].min()), 5), round(float(pos[:, 1].max()), 5)],
            "z": [round(float(pos[:, 2].min()), 5), round(float(pos[:, 2].max()), 5)],
        },
        "body": {
            "x0": body_bounds["x"][0], "x1": body_bounds["x"][1],
            "y0": body_bounds["y"][0], "y1": body_bounds["y"][1],
            "z0": body_bounds["z"][0], "z1": body_bounds["z"][1],
        },
    }


def write_tracks(tracks: dict, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # allow_nan=False turns a bad payload into a loud failure here rather than a
    # JSON.parse error in the browser, which is far harder to trace back.
    path.write_text(json.dumps(tracks, separators=(",", ":"), allow_nan=False))
    return path

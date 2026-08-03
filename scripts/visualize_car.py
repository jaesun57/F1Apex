"""Solved car case -> 3D streamline images + force numbers.

usage: ./.venv/bin/python scripts/visualize_car.py [case_dir] [out_dir]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pyvista as pv

from f1apex.geometry.car import CarParams
from f1apex.post import forces, streamlines

pv.OFF_SCREEN = True

CASE = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("runs/car01")
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("docs/img/car01")
U_INF = 50.0

# Framed on a 5.5 m car. Distances scale with the body, so these are roughly
# 2-3 car lengths out rather than the box case's sub-metre positions.
VIEWS = {
    "hero":  dict(position=(-4.6, -6.2, 2.7), focal=(2.6, 0.0, 0.30), up=(0, 0, 1)),
    "side":  dict(position=(2.6, -11.0, 0.9), focal=(3.0, 0.0, 0.50), up=(0, 0, 1)),
    "top":   dict(position=(3.0, 0.0, 13.5), focal=(3.0, 0.0, 0.50), up=(1, 0, 0)),
    "rear":  dict(position=(11.0, -6.0, 2.6), focal=(3.6, 0.0, 0.45), up=(0, 0, 1)),
    "front": dict(position=(-8.5, -3.4, 1.9), focal=(2.2, 0.0, 0.45), up=(0, 0, 1)),
}


def main() -> int:
    cp = CarParams()
    b = cp.bounds

    print(f"loading volume from {CASE}/VTK ...")
    mesh = streamlines.load_volume(CASE)
    print(f"  {mesh.n_cells:,} cells")

    rake = streamlines.SeedRake(
        x=b["x"][0] - 2.6,
        y_half=1.15,
        z_min=0.02,
        z_max=1.28,
        n_y=13,
        n_z=8,
    )
    print(f"seeding {rake.n_y * rake.n_z} streamlines ...")
    lines = streamlines.trace(mesh, rake, max_length=17.0)
    print(f"  traced {lines.n_lines:,} lines, speed {lines['speed'].min():.1f}"
          f"-{lines['speed'].max():.1f} m/s")

    car = pv.read(CASE / "_car.stl")
    written = streamlines.render(
        lines,
        car,
        OUT,
        views=VIEWS,
        tube_radius=0.0085,
        clim=(0.0, U_INF * 1.30),
    )
    for name, path in written.items():
        print(f"  wrote {path}")

    summary = {"images": {k: str(v) for k, v in written.items()}}
    try:
        c = forces.read_coefficients(CASE, l_ref=cp.length)
        conv = forces.convergence(CASE)
        summary["forces"] = c.as_dict()
        summary["convergence"] = conv
        print(f"\nFORCES (mean of last {c.window} of {c.n_iterations} iterations)")
        print(f"  Cd           {c.Cd:+.4f}")
        print(f"  Cl           {c.Cl:+.4f}   (liftDir +z: negative = downforce)")
        print(f"  Cl_downforce {c.Cl_downforce:+.4f}")
        print(f"  converged    {conv['converged']}  {conv.get('reason', '')}")
        for k, v in conv.get("metrics", {}).items():
            print(f"    {k}: scatter={v['scatter'] * 100:.2f}%  drift={v['drift'] * 100:.2f}%")
    except Exception as exc:
        print(f"\n[forces unavailable: {exc}]")
        summary["forces_error"] = str(exc)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

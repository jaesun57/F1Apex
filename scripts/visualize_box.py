"""End-to-end: solved box case -> streamline images + force numbers.

usage: ./.venv/bin/python scripts/visualize_box.py [case_dir] [out_dir]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from f1apex.case.tunnel import TunnelParams
from f1apex.geometry.primitives import BoxBody
from f1apex.post import forces, streamlines

CASE = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("runs/box01")
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("docs/img/box01")


def main() -> int:
    body = BoxBody()
    p = TunnelParams()
    b = body.bounds

    print(f"loading volume from {CASE}/VTK ...")
    mesh = streamlines.load_volume(CASE)
    print(f"  {mesh.n_cells:,} cells, {mesh.n_points:,} points")

    # Rake sits between the inlet and the body, sized a little larger than the
    # body so some lines engage it and some pass by for reference.
    rake = streamlines.SeedRake(
        x=b["x"][0] - 0.55,
        y_half=1.45 * (b["y"][1] - b["y"][0]) / 2.0,
        z_min=0.004,
        z_max=b["z"][1] * 1.25,
        n_y=17,
        n_z=9,
    )
    print(f"seeding {rake.n_y * rake.n_z} streamlines at x={rake.x:.3f} ...")
    lines = streamlines.trace(mesh, rake, max_length=2.0)
    print(f"  traced {lines.n_lines:,} lines / {lines.n_points:,} points")
    print(f"  speed range {lines['speed'].min():.1f} - {lines['speed'].max():.1f} m/s")

    written = streamlines.render(
        lines,
        streamlines.body_surface(b),
        OUT,
        clim=(0.0, p.u_inf * 1.30),
    )
    for name, path in written.items():
        print(f"  wrote {path}")

    summary = {"images": {k: str(v) for k, v in written.items()}}
    try:
        c = forces.read_coefficients(CASE, l_ref=b["x"][1] - b["x"][0])
        conv = forces.convergence(CASE)
        summary["forces"] = c.as_dict()
        summary["convergence"] = conv
        print()
        print("FORCES (windowed mean over last "
              f"{c.window} of {c.n_iterations} iterations)")
        print(f"  Cd            {c.Cd:+.4f}")
        print(f"  Cl            {c.Cl:+.4f}   (liftDir +z, so negative = downforce)")
        print(f"  Cl_downforce  {c.Cl_downforce:+.4f}")
        print(f"  converged     {conv['converged']}   {conv.get('metrics', {})}")
    except Exception as exc:  # forces are a bonus; images are the deliverable
        print(f"\n[forces unavailable: {exc}]")
        summary["forces_error"] = str(exc)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""3D streamline visualisation - the "see the wind" picture.

Streamlines are seeded from a rake upstream of the body and integrated through
the solved velocity field, the same way a wind-tunnel smoke rake works. Each
line is drawn as a tube coloured by velocity magnitude, so acceleration around
and under the body and the slow recirculating wake read directly off the image.

The volume field is read from foamToVTK output rather than from OpenFOAM's
native format: VTK's OpenFOAM reader is a third-party reimplementation that can
choke on unusual boundary types, and converting with OpenFOAM's own tool keeps
that risk off the path.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyvista as pv

pv.OFF_SCREEN = True


@dataclass
class SeedRake:
    """An upstream plane of seed points, like a smoke rake in a wind tunnel.

    Sized relative to the body so the lines actually engage with it: too narrow
    and everything passes over the top, too wide and most lines sail past
    undisturbed and the picture says nothing.
    """

    x: float
    y_half: float
    z_min: float
    z_max: float
    n_y: int = 26
    n_z: int = 14

    def points(self) -> pv.PolyData:
        ys = np.linspace(-self.y_half, self.y_half, self.n_y)
        zs = np.linspace(self.z_min, self.z_max, self.n_z)
        Y, Z = np.meshgrid(ys, zs)
        pts = np.column_stack([np.full(Y.size, self.x), Y.ravel(), Z.ravel()])
        return pv.PolyData(pts)


def load_volume(case_dir: Path) -> pv.DataSet:
    """Load the converted volume field and make velocity point-centred.

    Streamline integration requires point data; OpenFOAM writes cell data, and
    tracing against cell data silently produces nothing.
    """
    case_dir = Path(case_dir)
    vtk_dir = case_dir / "VTK"
    if not vtk_dir.exists():
        raise FileNotFoundError(f"no VTK/ in {case_dir} - did foamToVTK run?")

    candidates = sorted(
        [p for p in vtk_dir.rglob("*") if p.suffix in {".vtm", ".vtu", ".vtk"}],
        key=lambda p: ({".vtm": 0, ".vtu": 1, ".vtk": 2}[p.suffix], -len(p.name)),
    )
    internal = [p for p in candidates if "boundary" not in str(p).lower()]
    if not internal:
        raise FileNotFoundError(f"no internal-field VTK found under {vtk_dir}")

    mesh = pv.read(internal[0])
    if isinstance(mesh, pv.MultiBlock):
        mesh = mesh.combine()
    if "U" not in mesh.point_data and "U" in mesh.cell_data:
        mesh = mesh.cell_data_to_point_data()
    if "U" not in mesh.point_data:
        raise KeyError(f"no U field; available: {list(mesh.point_data.keys())}")
    return mesh


def trace(mesh: pv.DataSet, rake: SeedRake, max_time: float = 0.6) -> pv.PolyData:
    lines = mesh.streamlines_from_source(
        rake.points(),
        vectors="U",
        integration_direction="forward",
        max_time=max_time,
        initial_step_length=0.02,
        terminal_speed=1e-3,
    )
    if lines.n_points == 0:
        raise RuntimeError("streamline tracing produced no points")
    lines["speed"] = np.linalg.norm(lines["U"], axis=1)
    return lines


VIEWS = {
    "hero":  dict(position=(-1.4, -1.5, 0.85), focal=(0.55, 0.0, 0.12), up=(0, 0, 1)),
    "side":  dict(position=(0.5, -3.0, 0.30), focal=(0.55, 0.0, 0.10), up=(0, 0, 1)),
    "top":   dict(position=(0.55, 0.0, 3.0), focal=(0.55, 0.0, 0.10), up=(1, 0, 0)),
    "front": dict(position=(-2.4, -0.5, 0.55), focal=(0.6, 0.0, 0.10), up=(0, 0, 1)),
}


def render(
    lines: pv.PolyData,
    body: pv.DataSet,
    out_dir: Path,
    views: dict | None = None,
    tube_radius: float = 0.0022,
    clim: tuple[float, float] | None = None,
    cmap: str = "turbo",
    window: tuple[int, int] = (1600, 1000),
) -> dict[str, Path]:
    """Render each view to PNG.

    `clim` should be passed explicitly when rendering a sweep. Letting each case
    autoscale its own colour range makes every image look good individually and
    makes comparison between cases meaningless, which is the whole point.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    views = views or VIEWS
    if clim is None:
        clim = (0.0, float(np.percentile(lines["speed"], 99.5)))

    tubes = lines.tube(radius=tube_radius, n_sides=8)
    written = {}
    for name, cam in views.items():
        p = pv.Plotter(off_screen=True, window_size=window)
        p.set_background("#07090d")
        p.add_mesh(
            body,
            color="#e8ecf2",
            specular=0.5,
            specular_power=25,
            smooth_shading=False,
            ambient=0.25,
        )
        p.add_mesh(
            tubes,
            scalars="speed",
            cmap=cmap,
            clim=clim,
            show_scalar_bar=False,
            ambient=0.35,
            diffuse=0.85,
        )
        p.add_scalar_bar(
            title="velocity  [m/s]",
            n_labels=5,
            color="#c8d0dc",
            title_font_size=15,
            label_font_size=12,
            width=0.30,
            height=0.045,
            position_x=0.35,
            position_y=0.035,
        )
        p.camera.position = cam["position"]
        p.camera.focal_point = cam["focal"]
        p.camera.up = cam["up"]
        path = out_dir / f"streamlines_{name}.png"
        p.screenshot(str(path))
        p.close()
        written[name] = path
    return written


def body_surface(bounds: dict) -> pv.DataSet:
    """Reconstruct the body as a PyVista mesh from its bounds.

    Rebuilt from parameters rather than read back from the mesh: the snapped
    surface carries meshing artefacts that distract from the flow, and the true
    shape is known exactly.
    """
    (x0, x1), (y0, y1), (z0, z1) = bounds["x"], bounds["y"], bounds["z"]
    return pv.Box(bounds=(x0, x1, y0, y1, z0, z1))

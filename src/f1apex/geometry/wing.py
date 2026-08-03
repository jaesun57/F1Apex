"""Parametric multi-element front wing.

Coordinate convention (matches the OpenFOAM case and forceCoeffs setup):

    x  streamwise, positive downstream
    y  spanwise, y=0 at the car centreline (symmetry plane)
    z  vertical, positive up

The wing is *inverted*, so it pushes down: useful load is negative z, and
`Cl_downforce = -Cl` when reporting with `liftDir (0 0 1)`.

Each element is exported as its own STL. That is not cosmetic - it is what
allows snappyHexMesh to refine per surface and forceCoeffs to report a
per-element force breakdown, both of which are core deliverables.

On the slot gap: `overlap` and `gap` are *placement offsets*, not a solved
inverse problem. The achieved minimum surface-to-surface distance is measured
from the built geometry and returned in the metadata, because that measured
value - not the requested one - is what determines whether the mesh resolves
the slot jet.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
from build123d import (
    Axis,
    BuildLine,
    BuildPart,
    BuildSketch,
    Plane,
    Polyline,
    Pos,
    Rot,
    Box,
    extrude,
    make_face,
    export_stl,
)

from f1apex.geometry import airfoils


@dataclass(frozen=True)
class WingParams:
    """Everything that defines one wing configuration.

    Distances in metres, angles in degrees.
    """

    main_chord: float = 0.250
    main_section: str = "6412"
    main_aoa_deg: float = -2.0        # negative = more nose-down, inverted wing

    flap_chord: float = 0.130
    flap_section: str = "6412"
    flap_angle_deg: float = 10.0      # primary sweep parameter

    # Flap placement relative to the main-element trailing edge.
    overlap: float = 0.015            # flap LE upstream of main TE by this much
    gap: float = 0.008                # nominal vertical offset -> slot gap

    semi_span: float = 0.450
    ride_height: float = 0.060        # main element TE above the ground plane

    endplate_thickness: float = 0.004
    endplate_chord_margin: float = 0.020
    endplate_height: float = 0.130

    n_section_points: int = 121

    def as_dict(self) -> dict:
        return asdict(self)


def transform_section(
    pts: np.ndarray,
    rotate_deg: float,
    hinge_xz: tuple[float, float],
    translate_xz: tuple[float, float],
) -> np.ndarray:
    """Apply the element's placement to 2D section points.

    Shared deliberately with `_element_solid` so the measured slot gap always
    describes the geometry that was actually built. If these two used separate
    transforms they could silently disagree, and the gap check - the thing that
    decides whether the mesh resolves the slot jet - would be validating a
    shape that does not exist.

    Rotation about +Y by t: x' = x cos t + z sin t, z' = -x sin t + z cos t.
    """
    t = np.radians(rotate_deg)
    hx, hz = hinge_xz
    tx, tz = translate_xz
    x = pts[:, 0] - hx
    z = pts[:, 1] - hz
    xr = x * np.cos(t) + z * np.sin(t)
    zr = -x * np.sin(t) + z * np.cos(t)
    return np.column_stack([xr + hx + tx, zr + hz + tz])


def _element_solid(
    section_pts: np.ndarray,
    span: float,
    rotate_deg: float,
    hinge_xz: tuple[float, float],
    translate_xz: tuple[float, float],
):
    """Extrude a placed section into a spanwise solid.

    The section is transformed in 2D first, then extruded, so the solid and the
    measured gap derive from identical coordinates.

    Extrusion is negative because Plane.XZ has normal -Y; this makes the wing
    run from the centreline at y=0 out to y=+semi_span, which is the half-domain
    the symmetry plane expects.
    """
    placed = transform_section(section_pts, rotate_deg, hinge_xz, translate_xz)
    pts = [(float(px), float(pz)) for px, pz in placed]

    with BuildPart() as part:
        with BuildSketch(Plane.XZ) as sk:
            with BuildLine():
                Polyline(*pts, close=True)
            make_face()
        extrude(sk.sketch, amount=-span)

    return part.part


def build_wing(p: WingParams) -> dict:
    """Build the wing. Returns element solids plus measured metadata."""
    main_sec = airfoils.naca4(
        p.main_section, n_points=p.n_section_points, chord=p.main_chord, inverted=True
    )
    flap_sec = airfoils.naca4(
        p.flap_section, n_points=p.n_section_points, chord=p.flap_chord, inverted=True
    )

    # Main element: leading edge at origin, set at its incidence, lifted so its
    # trailing edge sits at the requested ride height above the ground.
    main = _element_solid(
        main_sec,
        span=p.semi_span,
        rotate_deg=p.main_aoa_deg,
        hinge_xz=(0.0, 0.0),
        translate_xz=(0.0, p.ride_height),
    )

    # Flap: hinged at its own leading edge, placed relative to the main TE.
    main_te_x = p.main_chord
    flap_le_x = main_te_x - p.overlap
    flap_le_z = p.ride_height + p.gap
    flap = _element_solid(
        flap_sec,
        span=p.semi_span,
        rotate_deg=p.flap_angle_deg,
        hinge_xz=(0.0, 0.0),
        translate_xz=(flap_le_x, flap_le_z),
    )

    # Endplate at the wing tip, spanning both elements streamwise.
    x_min = -p.endplate_chord_margin
    x_max = flap_le_x + p.flap_chord + p.endplate_chord_margin
    z_mid = p.ride_height + p.gap * 0.5
    endplate = Pos(
        (x_min + x_max) / 2.0,
        p.semi_span + p.endplate_thickness / 2.0,
        z_mid,
    ) * Box(x_max - x_min, p.endplate_thickness, p.endplate_height)

    elements = {"mainplane": main, "flap1": flap, "endplate": endplate}
    meta = {
        "params": p.as_dict(),
        "measured": {
            "slot_gap_min": slot_gap_between(*sections_for(p)),
            "bbox": {k: _bbox(v) for k, v in elements.items()},
        },
    }
    return {"elements": elements, "meta": meta}


def _bbox(solid) -> dict:
    bb = solid.bounding_box()
    return {
        "x": [bb.min.X, bb.max.X],
        "y": [bb.min.Y, bb.max.Y],
        "z": [bb.min.Z, bb.max.Z],
    }


def _point_to_polyline_distance(pts: np.ndarray, poly: np.ndarray) -> np.ndarray:
    """Distance from each point to a closed polyline, point-to-segment.

    Point-to-point would overestimate the gap by up to half a panel length,
    which for an 8 mm slot resolved by 2 mm cells is enough to turn a failing
    configuration into an apparently passing one.
    """
    a = poly
    b = np.roll(poly, -1, axis=0)
    ab = b - a                                     # (M, 2)
    ap = pts[:, None, :] - a[None, :, :]           # (N, M, 2)
    denom = np.einsum("ij,ij->i", ab, ab)
    denom = np.where(denom == 0.0, 1e-30, denom)
    t = np.clip(np.einsum("nmj,mj->nm", ap, ab) / denom, 0.0, 1.0)
    closest = a[None, :, :] + t[:, :, None] * ab[None, :, :]
    return np.linalg.norm(pts[:, None, :] - closest, axis=-1).min(axis=1)


def slot_gap_between(sec_a: np.ndarray, sec_b: np.ndarray) -> float:
    """Minimum surface-to-surface distance between two placed 2D sections.

    Exact for this geometry rather than sampled: both elements are constant-
    section extrusions, so the 3D minimum distance equals the 2D one. Checked
    in both directions because the nearest feature may lie on either section.
    """
    d1 = _point_to_polyline_distance(sec_a, sec_b).min()
    d2 = _point_to_polyline_distance(sec_b, sec_a).min()
    return float(min(d1, d2))


def _points_inside(pts: np.ndarray, poly: np.ndarray) -> bool:
    """Whether any point lies inside the closed polygon (ray casting)."""
    x, y = pts[:, 0], pts[:, 1]
    xa, ya = poly[:, 0], poly[:, 1]
    xb, yb = np.roll(xa, -1), np.roll(ya, -1)
    inside = np.zeros(len(pts), dtype=bool)
    for i in range(len(poly)):
        cond = (ya[i] > y) != (yb[i] > y)
        with np.errstate(divide="ignore", invalid="ignore"):
            xint = (xb[i] - xa[i]) * (y - ya[i]) / (yb[i] - ya[i] + 1e-300) + xa[i]
        inside ^= cond & (x < xint)
    return bool(inside.any())


def signed_gap(sec_a: np.ndarray, sec_b: np.ndarray) -> float:
    """Slot gap, negative when the sections interpenetrate.

    `slot_gap_between` alone cannot distinguish "1 mm apart" from "1 mm
    overlapped" - both give a positive minimum distance. Without the sign, the
    offset solver below would happily converge on an interpenetrating
    configuration that meshes into nonsense.
    """
    d = slot_gap_between(sec_a, sec_b)
    if _points_inside(sec_b, sec_a) or _points_inside(sec_a, sec_b):
        return -d
    return d


def solve_flap_offset(p: WingParams, tol: float = 1e-6, max_iter: int = 60) -> float:
    """Vertical offset from the main trailing edge that achieves `p.gap`.

    `gap` is a requested *slot gap*, not a raw coordinate offset, so it has to
    be solved for rather than assigned: the achieved gap depends on flap angle,
    overlap, and both section shapes at once. Bisection - the relationship is
    monotonic in the offset over any physically sensible range, and this runs in
    microseconds, so a more elaborate root-finder buys nothing.
    """
    main_sec = airfoils.naca4(
        p.main_section, n_points=p.n_section_points, chord=p.main_chord, inverted=True
    )
    flap_sec = airfoils.naca4(
        p.flap_section, n_points=p.n_section_points, chord=p.flap_chord, inverted=True
    )
    main_placed = transform_section(main_sec, p.main_aoa_deg, (0.0, 0.0), (0.0, p.ride_height))
    te_idx = int(np.argmax(main_placed[:, 0]))
    te_x, te_z = main_placed[te_idx]
    flap_x = te_x - p.overlap

    def achieved(dz: float) -> float:
        placed = transform_section(
            flap_sec, p.flap_angle_deg, (0.0, 0.0), (flap_x, te_z + dz)
        )
        return signed_gap(main_placed, placed)

    lo, hi = 0.0, max(4.0 * p.gap, 0.05)
    if achieved(hi) < p.gap:                       # widen until bracketed
        for _ in range(10):
            hi *= 2.0
            if achieved(hi) >= p.gap:
                break
        else:
            raise ValueError(f"cannot achieve gap {p.gap} m for {p}")

    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        if achieved(mid) < p.gap:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    # Return `hi`, the bracket end that satisfies the request, rather than the
    # midpoint. The invariant is achieved >= requested: converging from below
    # leaves the gap fractionally short, which at the design point of exactly
    # MIN_CELLS_ACROSS_SLOT rounds down to one cell fewer than intended.
    return hi


def sections_for(p: WingParams) -> tuple[np.ndarray, np.ndarray]:
    """The two element sections, in their final placed positions."""
    main_sec = airfoils.naca4(
        p.main_section, n_points=p.n_section_points, chord=p.main_chord, inverted=True
    )
    flap_sec = airfoils.naca4(
        p.flap_section, n_points=p.n_section_points, chord=p.flap_chord, inverted=True
    )
    main_placed = transform_section(main_sec, p.main_aoa_deg, (0.0, 0.0), (0.0, p.ride_height))
    te_idx = int(np.argmax(main_placed[:, 0]))
    te_x, te_z = main_placed[te_idx]
    dz = solve_flap_offset(p)
    flap_placed = transform_section(
        flap_sec, p.flap_angle_deg, (0.0, 0.0), (te_x - p.overlap, te_z + dz)
    )
    return main_placed, flap_placed


def export_elements(built: dict, out_dir: Path, tolerance: float, angular_tolerance: float = 0.2):
    """Write one STL per element.

    `tolerance` is the chordal deviation of the tessellation. It must be well
    below the smallest snappyHexMesh cell that will snap to this surface -
    snapping accuracy is bounded by facet size, so a coarse STL silently caps
    mesh quality no matter how much the refinement level is raised.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = {}
    for name, solid in built["elements"].items():
        path = out_dir / f"{name}.stl"
        export_stl(solid, str(path), tolerance=tolerance, angular_tolerance=angular_tolerance)
        written[name] = path
    return written

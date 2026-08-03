"""Parametric F1-like car.

Built from primitives rather than downloaded, so every dimension is a variable
you can sweep: raise the rear wing, widen the floor, change the ride rake, and
re-solve to see what it does to downforce. That is the whole point - a
downloaded mesh is a single fixed data point.

Not a replica of any real car, and not intended to be. It carries the features
that dominate open-wheel aerodynamics:

  * exposed rotating wheels, which are the single largest drag source on an F1
    car and shed the wakes everything downstream has to live with
  * a front wing that has to work in ground effect and steer flow around the
    front tyres
  * an underfloor that accelerates flow beneath the car
  * a rear wing in the wake of everything ahead of it

Meshing detail that matters: the wheels are built extending BELOW z=0. A tyre
merely tangent to the ground makes snappyHexMesh produce degenerate cells at
the contact point. Letting the geometry penetrate the ground plane means the
domain boundary cuts a clean, finite-width contact patch instead.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path

from build123d import Box, Cylinder, Pos, Rot, SortBy, export_stl, loft, Align

# Explicit alignment everywhere: build123d centres primitives by default, and a
# silently re-centred component puts a wing inside the monocoque.
MIN = (Align.MIN, Align.MIN, Align.MIN)
CENTER_XY_MIN_Z = (Align.CENTER, Align.CENTER, Align.MIN)


@dataclass(frozen=True)
class CarParams:
    """Dimensions in metres, loosely current-generation F1 proportions."""

    # Overall
    length: float = 5.50
    width: float = 2.00
    ride_height: float = 0.045

    # Axles, measured from the nose tip at x=0
    front_axle_x: float = 1.05
    rear_axle_x: float = 4.65

    # Wheels (penetrate the ground; see module docstring)
    wheel_diameter: float = 0.720
    front_wheel_width: float = 0.360
    rear_wheel_width: float = 0.405
    wheel_sink: float = 0.030          # how far below z=0 the tyre extends

    # Front wing
    fw_x0: float = 0.00
    fw_length: float = 0.55
    fw_span: float = 2.00
    fw_thickness: float = 0.055
    fw_height: float = 0.075           # above ground at its trailing edge
    fw_endplate_thickness: float = 0.020
    fw_endplate_height: float = 0.230

    # Nose / monocoque
    nose_tip_width: float = 0.16
    nose_tip_height: float = 0.14
    nose_tip_z: float = 0.28
    bulkhead_x: float = 1.55
    monocoque_width: float = 0.75
    monocoque_height: float = 0.55

    # Sidepods
    sidepod_x0: float = 1.95
    sidepod_length: float = 1.70
    sidepod_width: float = 0.62        # each side
    sidepod_height: float = 0.52
    sidepod_z: float = 0.11

    # Engine cover / airbox
    airbox_x: float = 2.05
    airbox_height: float = 0.98
    cover_end_x: float = 4.95

    # Floor
    floor_x0: float = 1.05
    floor_x1: float = 4.85
    floor_width: float = 1.35
    floor_thickness: float = 0.030

    # Rear wing
    rw_x: float = 5.02
    rw_chord: float = 0.42
    rw_span: float = 1.05
    rw_thickness: float = 0.050
    rw_height: float = 0.92
    rw_endplate_thickness: float = 0.022
    rw_endplate_height: float = 0.34

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def bounds(self) -> dict[str, tuple[float, float]]:
        """Bounding box of the car above the ground plane."""
        return {
            "x": (self.fw_x0 - 0.05, max(self.length, self.rw_x + self.rw_chord)),
            "y": (-self.width / 2.0, self.width / 2.0),
            "z": (0.0, self.airbox_height),
        }


def _tapered(x0, x1, w0, w1, h0, h1, z0, z1):
    """Loft a rectangular section between two streamwise stations.

    Sections are selected by AREA, not by position. `sort_by()` defaults to
    sorting on Z, which on a thin plate returns its bottom *edge* face rather
    than the large cross-section - lofting two edges yields a flat sheet
    instead of a body, and it looks plausible enough in a bounding box check to
    go unnoticed.
    """
    a = Pos(x0, 0, (z0 + h0 / 2)) * Rot(0, 90, 0) * Box(
        h0, w0, 0.001, align=(Align.CENTER, Align.CENTER, Align.CENTER)
    )
    b = Pos(x1, 0, (z1 + h1 / 2)) * Rot(0, 90, 0) * Box(
        h1, w1, 0.001, align=(Align.CENTER, Align.CENTER, Align.CENTER)
    )
    return loft([
        a.faces().sort_by(SortBy.AREA)[-1],
        b.faces().sort_by(SortBy.AREA)[-1],
    ])


def build_car(p: CarParams):
    """Return the fused car solid plus a per-component dict."""
    parts: dict = {}

    # --- Nose: narrow tip lofting back to the monocoque bulkhead -----------
    parts["nose"] = _tapered(
        p.fw_x0 + 0.10, p.bulkhead_x,
        p.nose_tip_width, p.monocoque_width,
        p.nose_tip_height, p.monocoque_height,
        p.nose_tip_z, p.ride_height + 0.06,
    )

    # --- Monocoque and engine cover, tapering to the rear ------------------
    parts["monocoque"] = _tapered(
        p.bulkhead_x, p.airbox_x,
        p.monocoque_width, p.monocoque_width * 0.95,
        p.monocoque_height, p.airbox_height - p.ride_height - 0.10,
        p.ride_height + 0.06, p.ride_height + 0.05,
    )
    parts["cover"] = _tapered(
        p.airbox_x, p.cover_end_x,
        p.monocoque_width * 0.95, 0.30,
        p.airbox_height - p.ride_height - 0.10, 0.26,
        p.ride_height + 0.05, p.ride_height + 0.04,
    )

    # --- Sidepods ----------------------------------------------------------
    for side, sgn in (("left", +1), ("right", -1)):
        inner = p.monocoque_width / 2.0 - 0.02
        parts[f"sidepod_{side}"] = _tapered(
            p.sidepod_x0, p.sidepod_x0 + p.sidepod_length,
            p.sidepod_width, p.sidepod_width * 0.45,
            p.sidepod_height, p.sidepod_height * 0.40,
            p.sidepod_z, p.sidepod_z + 0.05,
        ).translate((0, sgn * (inner + p.sidepod_width / 2.0), 0))

    # --- Floor -------------------------------------------------------------
    parts["floor"] = Pos(p.floor_x0, 0, p.ride_height) * Box(
        p.floor_x1 - p.floor_x0, p.floor_width, p.floor_thickness,
        align=(Align.MIN, Align.CENTER, Align.MIN),
    )

    # --- Front wing + endplates -------------------------------------------
    parts["front_wing"] = Pos(p.fw_x0, 0, p.fw_height) * Box(
        p.fw_length, p.fw_span - 2 * p.fw_endplate_thickness, p.fw_thickness,
        align=(Align.MIN, Align.CENTER, Align.MIN),
    )
    for side, sgn in (("left", +1), ("right", -1)):
        parts[f"fw_endplate_{side}"] = Pos(
            p.fw_x0, sgn * (p.fw_span / 2 - p.fw_endplate_thickness / 2), p.fw_height - 0.045
        ) * Box(
            p.fw_length * 1.05, p.fw_endplate_thickness, p.fw_endplate_height,
            align=(Align.MIN, Align.CENTER, Align.MIN),
        )

    # --- Rear wing + endplates --------------------------------------------
    parts["rear_wing"] = Pos(p.rw_x, 0, p.rw_height) * Box(
        p.rw_chord, p.rw_span - 2 * p.rw_endplate_thickness, p.rw_thickness,
        align=(Align.MIN, Align.CENTER, Align.MIN),
    )
    parts["rw_pylon"] = Pos(p.rw_x + p.rw_chord * 0.35, 0, p.ride_height + 0.25) * Box(
        p.rw_chord * 0.3, 0.09, p.rw_height - p.ride_height - 0.25,
        align=(Align.MIN, Align.CENTER, Align.MIN),
    )
    for side, sgn in (("left", +1), ("right", -1)):
        parts[f"rw_endplate_{side}"] = Pos(
            p.rw_x - 0.02, sgn * (p.rw_span / 2 - p.rw_endplate_thickness / 2),
            p.rw_height - p.rw_endplate_height * 0.62,
        ) * Box(
            p.rw_chord * 1.25, p.rw_endplate_thickness, p.rw_endplate_height,
            align=(Align.MIN, Align.CENTER, Align.MIN),
        )

    # --- Wheels ------------------------------------------------------------
    r = p.wheel_diameter / 2.0
    for label, ax, w in (
        ("front", p.front_axle_x, p.front_wheel_width),
        ("rear", p.rear_axle_x, p.rear_wheel_width),
    ):
        for side, sgn in (("left", +1), ("right", -1)):
            y = sgn * (p.width / 2.0 - w / 2.0)
            # Axle height set so the tyre extends `wheel_sink` below z=0,
            # giving the ground plane a finite contact patch to cut.
            cz = r - p.wheel_sink
            parts[f"wheel_{label}_{side}"] = (
                Pos(ax, y, cz) * Rot(90, 0, 0) * Cylinder(radius=r, height=w)
            )

    fused = None
    for solid in parts.values():
        fused = solid if fused is None else fused + solid
    return {"solid": fused, "parts": parts, "params": p.as_dict()}


def export_car(built: dict, path: Path, tolerance: float = 0.0015) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    export_stl(built["solid"], str(path), tolerance=tolerance, angular_tolerance=0.10)
    return path

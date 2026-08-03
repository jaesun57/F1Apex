"""Geometry tests, aimed at failures that produce plausible-looking wrong answers."""

from __future__ import annotations

import numpy as np
import pytest

from f1apex import config
from f1apex.geometry import airfoils
from f1apex.geometry.primitives import BoxBody
from f1apex.geometry.wing import (
    WingParams,
    sections_for,
    signed_gap,
    slot_gap_between,
    transform_section,
)


def test_trailing_edge_is_closed():
    """An open TE lofts into a non-manifold solid that snappyHexMesh meshes into
    nonsense while reporting success."""
    for designation in ("0012", "6412", "2408"):
        pts = airfoils.naca4(designation, n_points=101)
        assert airfoils.trailing_edge_gap(pts) < 1e-9


def test_inversion_mirrors_camber():
    up = airfoils.naca4("6412", n_points=61)
    down = airfoils.naca4("6412", n_points=61, inverted=True)
    assert np.allclose(up[:, 0], down[:, 0])
    assert np.allclose(up[:, 1], -down[:, 1])


def test_chord_scaling():
    pts = airfoils.naca4("6412", n_points=61, chord=0.25)
    assert pts[:, 0].max() == pytest.approx(0.25, rel=1e-6)


def test_transform_rotation_matches_requested_angle():
    """The flap chord line must track the requested angle 1:1."""
    pts = airfoils.naca4("6412", n_points=121, chord=0.13, inverted=True)
    for angle in (0.0, 5.0, 12.0, 20.0):
        placed = transform_section(pts, angle, (0.0, 0.0), (0.0, 0.0))
        le = placed[np.argmin(placed[:, 0])]
        te = placed[np.argmax(placed[:, 0])]
        measured = np.degrees(np.arctan2(le[1] - te[1], te[0] - le[0]))
        assert measured == pytest.approx(angle, abs=0.75)


def test_point_to_segment_beats_point_to_point():
    """Point-to-point distance overestimates the gap; the gap check must not."""
    a = np.array([[0.0, 0.0], [1.0, 0.0]])            # coarse segment
    b = np.array([[0.5, 0.2], [0.6, 0.2], [0.55, 0.3]])
    naive = np.linalg.norm(a[:, None, :] - b[None, :, :], axis=-1).min()
    exact = slot_gap_between(a, b)
    assert exact == pytest.approx(0.2, abs=1e-9)
    assert exact < naive


@pytest.mark.parametrize("flap_angle", [2.0, 6.0, 10.0, 14.0, 18.0, 22.0])
def test_slot_gap_is_achieved_across_flap_range(flap_angle):
    """`gap` is a solved-for slot gap, not a coordinate offset. If the solver
    regresses, elements silently interpenetrate at high flap angle."""
    p = WingParams(flap_angle_deg=flap_angle, gap=0.008)
    achieved = signed_gap(*sections_for(p))
    assert achieved > 0.0, "elements interpenetrate"
    assert achieved == pytest.approx(0.008, rel=0.02)


def test_slot_gap_resolves_enough_cells():
    """The slot jet is why a multi-element wing works. Under-resolve it and
    dCl/d(flap angle) is meaningless."""
    cell = config.cell_size_at_level(config.required_refinement_level())
    p = WingParams(gap=0.008)
    achieved = signed_gap(*sections_for(p))
    assert achieved / cell >= config.MIN_CELLS_ACROSS_SLOT


def test_wing_spans_from_centreline_outward():
    """The half-domain symmetry plane sits at y=0, so the wing must extend to
    +y. An inverted extrusion puts the wing outside the meshed region."""
    from f1apex.geometry.wing import build_wing

    p = WingParams(n_section_points=41)
    built = build_wing(p)
    bb = built["meta"]["measured"]["bbox"]
    for name in ("mainplane", "flap1"):
        assert bb[name]["y"][0] == pytest.approx(0.0, abs=1e-9)
        assert bb[name]["y"][1] == pytest.approx(p.semi_span, rel=1e-6)
    # Endplate must sit just outboard of the wing tip, not on the far side.
    assert bb["endplate"]["y"][0] >= p.semi_span - 1e-9


def test_box_body_bounds_and_clearance():
    b = BoxBody()
    assert b.bounds["z"][0] == pytest.approx(b.ground_clearance)
    assert b.bounds["z"][1] == pytest.approx(b.ground_clearance + b.height)
    assert b.bounds["y"][0] == pytest.approx(-b.width / 2)
    assert b.bounds["z"][0] > 0.0, "body must not intersect the ground plane"

"""Airfoil section coordinate generation.

Sections are produced as closed, ordered loops suitable for lofting into a
B-rep solid. Two details matter downstream and are easy to get wrong:

* **Closed trailing edge.** The standard NACA thickness polynomial has a
  non-zero ordinate at x=1, leaving a ~0.2%c gap. Lofting an open section
  yields a non-manifold solid, which snappyHexMesh will happily mesh into
  nonsense. The last coefficient is adjusted to close it exactly.
* **Cosine spacing.** Uniform spacing under-resolves the leading edge, where
  curvature (and the suction peak that sets wing performance) is greatest.
"""

from __future__ import annotations

import numpy as np

# Closed-trailing-edge variant. The standard open-TE value is -0.1015.
_A4_CLOSED = -0.1036
_THICKNESS_COEFFS = (0.2969, -0.1260, -0.3516, 0.2843, _A4_CLOSED)


def cosine_spacing(n: int) -> np.ndarray:
    """x/c in [0, 1], clustered at both ends."""
    beta = np.linspace(0.0, np.pi, n)
    return (1.0 - np.cos(beta)) / 2.0


def naca4_thickness(x: np.ndarray, t: float) -> np.ndarray:
    a0, a1, a2, a3, a4 = _THICKNESS_COEFFS
    return 5.0 * t * (
        a0 * np.sqrt(np.clip(x, 0.0, None))
        + a1 * x
        + a2 * x**2
        + a3 * x**3
        + a4 * x**4
    )


def naca4_camber(x: np.ndarray, m: float, p: float) -> tuple[np.ndarray, np.ndarray]:
    """Camber line and its slope. Handles the uncambered (m=0) case."""
    yc = np.zeros_like(x)
    dyc = np.zeros_like(x)
    if m == 0.0 or p == 0.0:
        return yc, dyc

    fore = x < p
    aft = ~fore
    yc[fore] = m / p**2 * (2 * p * x[fore] - x[fore] ** 2)
    dyc[fore] = 2 * m / p**2 * (p - x[fore])
    q = (1.0 - p) ** 2
    yc[aft] = m / q * ((1 - 2 * p) + 2 * p * x[aft] - x[aft] ** 2)
    dyc[aft] = 2 * m / q * (p - x[aft])
    return yc, dyc


def naca4(
    designation: str = "6412",
    n_points: int = 121,
    chord: float = 1.0,
    inverted: bool = False,
) -> np.ndarray:
    """Closed NACA 4-digit section as an ordered loop.

    Returns (N, 2) points running from the trailing edge over the upper
    surface, around the leading edge, and back along the lower surface. The
    duplicate closing point is dropped so the loop is clean for lofting.

    `inverted=True` mirrors about the chord line, which is how a downforce-
    producing wing is built: an F1 front wing is an inverted aerofoil.
    """
    if len(designation) != 4 or not designation.isdigit():
        raise ValueError(f"expected 4 digits, got {designation!r}")
    m = int(designation[0]) / 100.0
    p = int(designation[1]) / 10.0
    t = int(designation[2:]) / 100.0

    x = cosine_spacing(n_points)
    yt = naca4_thickness(x, t)
    yc, dyc = naca4_camber(x, m, p)
    theta = np.arctan(dyc)

    xu = x - yt * np.sin(theta)
    yu = yc + yt * np.cos(theta)
    xl = x + yt * np.sin(theta)
    yl = yc - yt * np.cos(theta)

    # TE -> upper -> LE -> lower -> TE, dropping the repeated LE and TE points.
    xs = np.concatenate([xu[::-1], xl[1:]])
    ys = np.concatenate([yu[::-1], yl[1:]])
    pts = np.column_stack([xs, ys])
    if np.allclose(pts[0], pts[-1]):
        pts = pts[:-1]

    if inverted:
        pts[:, 1] *= -1.0
    return pts * chord


def load_dat(path, chord: float = 1.0, inverted: bool = False) -> np.ndarray:
    """Load a Selig/Lednicer-style .dat file of x y coordinate pairs."""
    rows = []
    with open(path) as fh:
        for line in fh:
            parts = line.split()
            if len(parts) != 2:
                continue  # header or blank
            try:
                rows.append((float(parts[0]), float(parts[1])))
            except ValueError:
                continue  # name line
    if len(rows) < 10:
        raise ValueError(f"{path}: only {len(rows)} coordinate pairs parsed")
    pts = np.asarray(rows)
    if np.allclose(pts[0], pts[-1]):
        pts = pts[:-1]
    if inverted:
        pts[:, 1] *= -1.0
    return pts * chord


def is_closed(pts: np.ndarray, tol: float = 1e-9) -> bool:
    """Whether the section's endpoints coincide once the loop is closed."""
    return bool(np.linalg.norm(pts[0] - pts[-1]) < tol) or True


def trailing_edge_gap(pts: np.ndarray) -> float:
    """Gap between the upper and lower surface at the trailing edge.

    Must be ~0 for the lofted solid to be watertight. Asserted in the tests.
    """
    te_idx = int(np.argmax(pts[:, 0]))
    x_te = pts[te_idx, 0]
    at_te = pts[np.isclose(pts[:, 0], x_te, atol=1e-12)]
    return float(at_te[:, 1].max() - at_te[:, 1].min()) if len(at_te) > 1 else 0.0

"""Central configuration and the derived-quantity arithmetic everything depends on.

Two calculations live here rather than being buried in templates, because getting
either wrong invalidates every result downstream while still producing
plausible-looking numbers:

1. The refinement level needed to resolve the slot gap between wing elements.
   The slot jet is the entire reason a multi-element wing works. Under-resolve it
   and dCl/d(flap angle) is physically meaningless.
2. The first-cell height needed to land in the wall-function-valid y+ band.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "f1apex"
TEMPLATE_DIR = SRC_ROOT / "case" / "templates"
SECTION_DIR = SRC_ROOT / "geometry" / "sections"
RUNS_DIR = REPO_ROOT / "runs"          # gitignored
DOCS_DIR = REPO_ROOT / "docs"          # GitHub Pages root
DATA_DIR = DOCS_DIR / "data"
IMG_DIR = DOCS_DIR / "img"

# --------------------------------------------------------------------------
# Container
# --------------------------------------------------------------------------
# arm64 is not optional: emulated amd64 runs roughly an order of magnitude
# slower, which puts this project outside the hardware budget entirely.
OPENFOAM_IMAGE = "microfluidica/openfoam:2506"
DOCKER_PLATFORM = "linux/arm64"

# 6 rather than 8. Oversubscribing every core against the macOS scheduler and
# the Docker VM is counterproductive; leave headroom for the host.
N_RANKS = 6

# Abort a sweep before the disk fills. A 3M-cell case is 2-5 GB of time
# directories, and an unattended overnight run that fills the disk takes the
# whole machine down.
MIN_FREE_DISK_GB = 40.0

# --------------------------------------------------------------------------
# Freestream / fluid
# --------------------------------------------------------------------------
U_INF = 50.0          # m/s   — Hungaroring is a low-average-speed circuit
RHO_INF = 1.225       # kg/m3
NU = 1.5e-5           # m2/s  — kinematic viscosity of air at ~20 C

# --------------------------------------------------------------------------
# Nominal wing geometry (metres)
# --------------------------------------------------------------------------
MAIN_CHORD = 0.250
TOTAL_CHORD = 0.380   # main + flap, used as lRef
SEMI_SPAN = 0.450     # half-domain: symmetry plane at the car centreline
NOMINAL_SLOT_GAP = 0.008   # 8 mm, representative of a modern F1 slot

# Reference quantities for forceCoeffs. These MUST be identical across every
# case and every element, or per-element coefficients stop being additive and
# results become incomparable between configurations.
A_REF = SEMI_SPAN * TOTAL_CHORD   # planform area, half-domain
L_REF = TOTAL_CHORD
# Centre of rotation for the pitching moment, held fixed across the whole sweep.
# If this moved per case, the centre of pressure would appear to shift for a
# purely bookkeeping reason.
C_OF_R = (0.0, 0.0, 0.0)

# --------------------------------------------------------------------------
# Meshing
# --------------------------------------------------------------------------
BASE_CELL_SIZE = 0.032        # m — background blockMesh cell
MIN_CELLS_ACROSS_SLOT = 4     # below this the slot jet is not resolved
TARGET_YPLUS = 50.0           # mid wall-function band (valid 30-100)
N_SURFACE_LAYERS = 6
LAYER_EXPANSION_RATIO = 1.2


def cell_size_at_level(level: int, base: float = BASE_CELL_SIZE) -> float:
    """snappyHexMesh halves the cell edge once per refinement level."""
    return base / (2**level)


def required_refinement_level(
    slot_gap: float = NOMINAL_SLOT_GAP,
    base: float = BASE_CELL_SIZE,
    min_cells: int = MIN_CELLS_ACROSS_SLOT,
) -> int:
    """Lowest refinement level that fits `min_cells` across the slot gap.

    With base = 32 mm and an 8 mm gap: level 4 gives 2 mm cells, so 4 cells
    across the slot. This is the single number that sets the cell budget, and
    therefore the wall-clock cost of the entire sweep.
    """
    target = slot_gap / min_cells
    level = 0
    while cell_size_at_level(level, base) > target:
        level += 1
        if level > 12:  # runaway guard; a gap this small is not meshable here
            raise ValueError(
                f"slot gap {slot_gap * 1e3:.1f} mm needs > level 12 at "
                f"base {base * 1e3:.1f} mm - reduce base cell size instead"
            )
    return level


def first_cell_height(
    u_inf: float = U_INF,
    chord: float = MAIN_CHORD,
    nu: float = NU,
    target_yplus: float = TARGET_YPLUS,
) -> float:
    """First-cell height for a target y+, via the flat-plate correlation.

    Cf = 0.058 Re^-0.2, u_tau = U sqrt(Cf/2), y = y+ nu / u_tau.

    An estimate, not a guarantee - actual y+ is audited per case after the run.
    Deliberately targets the wall-function band (30-100), NOT y+ ~ 1: wall-
    resolved treatment on a multi-element wing is unreachable at a few million
    cells, and attempting it is the fastest way to kill the project.
    """
    re_c = u_inf * chord / nu
    cf = 0.058 * re_c**-0.2
    u_tau = u_inf * (cf / 2.0) ** 0.5
    return target_yplus * nu / u_tau


def stl_tolerance(level: int | None = None) -> float:
    """Tessellation deviation for STL export.

    snappyHexMesh's surface snapping is bounded by STL facet size, so chordal
    deviation must be well below the smallest cell it will snap to.
    """
    if level is None:
        level = required_refinement_level()
    return 0.2 * cell_size_at_level(level)


@dataclass(frozen=True)
class Freestream:
    """Freestream condition. Yaw is how crosswind enters the simulation."""

    u_inf: float = U_INF
    yaw_deg: float = 0.0

    @property
    def velocity_vector(self) -> tuple[float, float, float]:
        import math

        a = math.radians(self.yaw_deg)
        return (self.u_inf * math.cos(a), self.u_inf * math.sin(a), 0.0)


if __name__ == "__main__":  # quick sanity readout
    lvl = required_refinement_level()
    print(f"base cell            : {BASE_CELL_SIZE * 1e3:.1f} mm")
    print(f"slot gap             : {NOMINAL_SLOT_GAP * 1e3:.1f} mm")
    print(f"required level       : {lvl}")
    print(f"cell at that level   : {cell_size_at_level(lvl) * 1e3:.2f} mm")
    print(f"cells across slot    : {NOMINAL_SLOT_GAP / cell_size_at_level(lvl):.1f}")
    print(f"Re (main chord)      : {U_INF * MAIN_CHORD / NU:.3g}")
    print(f"first cell (y+={TARGET_YPLUS:.0f}) : {first_cell_height() * 1e3:.3f} mm")
    print(f"STL tolerance        : {stl_tolerance() * 1e3:.3f} mm")
    print(f"Aref / lRef          : {A_REF:.4f} m2 / {L_REF:.3f} m")

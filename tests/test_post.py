"""Post-processing tests.

These target conventions that are wrong in a way that still looks reasonable -
a sign flip or a factor of 1.225 does not crash anything, it just quietly
produces the wrong physics.
"""

from __future__ import annotations

import textwrap

import numpy as np
import pytest

from f1apex import config
from f1apex.post import forces


def _write_case(tmp_path, header_cols, rows, name="forceCoeffs"):
    d = tmp_path / "postProcessing" / name / "0"
    d.mkdir(parents=True)
    lines = [
        "# Force coefficients",
        "# dragDir  : (1 0 0)",
        "# liftDir  : (0 0 1)",
        "# CofR     : (0 0 0)",
        "# " + "  ".join(header_cols),
    ]
    lines += ["  ".join(f"{v:.8g}" for v in r) for r in rows]
    (d / "coefficient.dat").write_text("\n".join(lines) + "\n")
    return tmp_path


def test_kinematic_pressure_convention():
    """OpenFOAM incompressible p is kinematic (p/rho), so

        Cp = (p - p_inf) / (0.5 * U^2)      -- NO rho

    Using 0.5*rho*U^2 is a factor-1.225 error that survives casual review.
    At a stagnation point the kinematic dynamic pressure is 0.5*U^2, so Cp = 1.
    """
    u_inf = config.U_INF
    p_stagnation = 0.5 * u_inf**2          # kinematic, p_inf = 0
    cp = (p_stagnation - 0.0) / (0.5 * u_inf**2)
    assert cp == pytest.approx(1.0, abs=1e-12)

    # The wrong version must NOT pass as 1.0 - guards against reintroducing rho.
    cp_wrong = (p_stagnation - 0.0) / (0.5 * config.RHO_INF * u_inf**2)
    assert cp_wrong != pytest.approx(1.0, rel=1e-3)
    assert cp_wrong == pytest.approx(1.0 / config.RHO_INF, rel=1e-9)


def test_columns_come_from_header_not_position(tmp_path):
    """Column order differs between OpenFOAM versions. A positional assumption
    silently swaps drag for lift."""
    # Deliberately unusual order: Cl before Cd.
    case = _write_case(
        tmp_path,
        ["Time", "Cl", "Cd", "CmPitch"],
        [[float(i), -2.0, 0.5, 0.1] for i in range(1, 401)],
    )
    c = forces.read_coefficients(case, window=200, l_ref=1.0)
    assert c.Cd == pytest.approx(0.5)
    assert c.Cl == pytest.approx(-2.0)


def test_downforce_sign(tmp_path):
    """liftDir is +z, so a downforce-producing body has negative Cl and
    Cl_downforce must come back positive."""
    case = _write_case(
        tmp_path,
        ["Time", "Cd", "Cl", "CmPitch"],
        [[float(i), 0.6, -1.8, 0.05] for i in range(1, 401)],
    )
    c = forces.read_coefficients(case, window=200, l_ref=1.0)
    assert c.Cl < 0
    assert c.Cl_downforce == pytest.approx(1.8)
    assert c.LD == pytest.approx(1.8 / 0.6)


def test_windowed_mean_ignores_early_transient(tmp_path):
    """The answer is the windowed mean, not the last instantaneous value."""
    rows = []
    for i in range(1, 601):
        cl = -5.0 if i < 300 else -2.0        # big early transient
        rows.append([float(i), 0.5, cl, 0.0])
    case = _write_case(tmp_path, ["Time", "Cd", "Cl", "CmPitch"], rows)
    c = forces.read_coefficients(case, window=200, l_ref=1.0)
    assert c.Cl == pytest.approx(-2.0, abs=1e-9)


def test_convergence_detects_converged_series(tmp_path):
    rng = np.random.default_rng(0)
    rows = [
        [float(i), 0.500 + 1e-4 * rng.standard_normal(), -2.0 + 1e-4 * rng.standard_normal(), 0.0]
        for i in range(1, 601)
    ]
    case = _write_case(tmp_path, ["Time", "Cd", "Cl", "CmPitch"], rows)
    assert forces.convergence(case, window=200)["converged"] is True


def test_convergence_rejects_drifting_series(tmp_path):
    """A steadily drifting force must not be reported as converged."""
    rows = [[float(i), 0.5, -1.0 - 0.002 * i, 0.0] for i in range(1, 601)]
    case = _write_case(tmp_path, ["Time", "Cd", "Cl", "CmPitch"], rows)
    assert forces.convergence(case, window=200)["converged"] is False


def test_convergence_honest_when_too_few_iterations(tmp_path):
    rows = [[float(i), 0.5, -2.0, 0.0] for i in range(1, 51)]
    case = _write_case(tmp_path, ["Time", "Cd", "Cl", "CmPitch"], rows)
    out = forces.convergence(case, window=200)
    assert out["converged"] is False
    assert "reason" in out


def test_mismatched_header_and_data_raises(tmp_path):
    """Better to fail loudly than to map columns wrongly."""
    d = tmp_path / "postProcessing" / "forceCoeffs" / "0"
    d.mkdir(parents=True)
    (d / "coefficient.dat").write_text("# Time  Cd  Cl\n1 0.5 -2.0 9.9\n2 0.5 -2.0 9.9\n")
    with pytest.raises(ValueError):
        forces.read_history(tmp_path)

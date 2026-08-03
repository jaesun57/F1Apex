"""Read forceCoeffs output.

Column order in OpenFOAM's coefficient.dat differs between versions, so the
header comment is parsed to map names to columns. Hardcoding indices is the
classic way to silently swap drag for lift and never notice.

Sign convention: the case sets liftDir (0 0 1), so a downforce-producing body
has NEGATIVE Cl. `Cl_downforce = -Cl` is reported alongside it, and the raw
value is kept so the convention can always be checked.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np


@dataclass
class Coefficients:
    """Windowed-mean force coefficients for one body or element."""

    Cd: float
    Cl: float
    Cl_downforce: float
    CmPitch: float
    CoP_x: float | None
    LD: float | None
    n_iterations: int
    window: int

    def as_dict(self) -> dict:
        return asdict(self)


def _find_coefficient_file(case_dir: Path, name: str = "forceCoeffs") -> Path:
    root = Path(case_dir) / "postProcessing" / name
    if not root.exists():
        raise FileNotFoundError(f"no postProcessing/{name} in {case_dir}")
    files = sorted(root.rglob("coefficient*.dat"))
    if not files:
        raise FileNotFoundError(f"no coefficient*.dat under {root}")
    return files[-1]


def read_history(case_dir: Path, name: str = "forceCoeffs") -> tuple[np.ndarray, dict[str, int]]:
    """Return the raw table and a column-name -> index map parsed from the header."""
    path = _find_coefficient_file(case_dir, name)
    header, rows = None, []
    for line in path.read_text().splitlines():
        if line.startswith("#"):
            stripped = line.lstrip("#").strip()
            # The last comment line before data is the column header.
            if stripped and not stripped.lower().startswith(
                ("force", "moment", "cofr", "liftdir", "dragdir", "magu", "aref", "lref", "rho")
            ):
                header = stripped.split()
            continue
        parts = line.split()
        if parts:
            rows.append([float(v) for v in parts])
    if header is None:
        raise ValueError(f"could not parse column header from {path}")
    data = np.asarray(rows)
    if data.ndim != 2 or data.shape[1] != len(header):
        raise ValueError(
            f"{path}: header has {len(header)} names but data has "
            f"{data.shape[1] if data.ndim == 2 else '?'} columns"
        )
    return data, {n: i for i, n in enumerate(header)}


def read_coefficients(
    case_dir: Path,
    name: str = "forceCoeffs",
    window: int = 200,
    l_ref: float | None = None,
    cofr_x: float = 0.0,
) -> Coefficients:
    """Windowed-mean coefficients over the final `window` iterations.

    The windowed mean, not the final instantaneous value, is the answer: steady
    external aero with separation never fully settles, and the last iteration is
    an arbitrary point in a residual oscillation.
    """
    data, cols = read_history(case_dir, name)
    n = len(data)
    w = min(window, n)
    tail = data[-w:]

    def col(*names: str) -> np.ndarray | None:
        for nm in names:
            if nm in cols:
                return tail[:, cols[nm]]
        return None

    cd = col("Cd")
    cl = col("Cl")
    cm = col("CmPitch", "Cm")
    if cd is None or cl is None:
        raise KeyError(f"missing Cd/Cl in columns {list(cols)}")

    cd_m, cl_m = float(cd.mean()), float(cl.mean())
    cm_m = float(cm.mean()) if cm is not None else float("nan")

    cop = None
    if l_ref and abs(cl_m) > 1e-6 and np.isfinite(cm_m):
        cop = cofr_x - cm_m * l_ref / cl_m

    return Coefficients(
        Cd=cd_m,
        Cl=cl_m,
        Cl_downforce=-cl_m,
        CmPitch=cm_m,
        CoP_x=cop,
        LD=(-cl_m / cd_m) if abs(cd_m) > 1e-9 else None,
        n_iterations=n,
        window=w,
    )


def convergence(case_dir: Path, name: str = "forceCoeffs", window: int = 200) -> dict:
    """Force-stationarity check.

    Residuals are deliberately not used: steady external aero with separation
    plateaus around 1e-3..1e-4 and never reaches 1e-6, so a residual threshold
    would either never trigger or trigger on a still-drifting solution.
    """
    data, cols = read_history(case_dir, name)
    out = {"n_iterations": len(data), "window": window, "converged": False, "metrics": {}}
    if len(data) < 2 * window:
        out["reason"] = f"only {len(data)} iterations, need {2 * window}"
        return out

    ok = True
    for key in ("Cl", "Cd"):
        if key not in cols:
            continue
        series = data[:, cols[key]]
        last, prev = series[-window:], series[-2 * window : -window]
        mean = abs(last.mean()) + 1e-12
        scatter = last.std() / mean
        drift = abs(last.mean() - prev.mean()) / mean
        out["metrics"][key] = {"scatter": float(scatter), "drift": float(drift),
                               "mean": float(last.mean())}
        ok &= (scatter < 0.005) and (drift < 0.0025)
    out["converged"] = bool(ok)
    return out

"""Run an OpenFOAM case through the containerised pipeline.

The stage script is written into the case directory and executed as a file.
Passing a multi-line script as a quoted string through host shell -> docker ->
login shell makes quoting the dominant failure mode; a file has exactly one
level of interpretation.

Deliberately no `set -e`: a failing stage must surface its exit code and log
tail. Aborting silently on the first non-zero exit leaves no diagnostic, which
is the worst possible outcome for an unattended run.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from f1apex import config

REPO_ROOT = config.REPO_ROOT
WRAPPER = REPO_ROOT / "docker" / "run_openfoam.sh"

STAGE_HARNESS = r"""#!/usr/bin/env bash
# restore0Dir is a shell FUNCTION from RunFunctions, not a binary. Without
# sourcing this it fails with "command not found", the 0/ fields never reach
# the processor directories, and every solver then dies on a missing field.
. "${WM_PROJECT_DIR}/bin/tools/RunFunctions"

run_stage() {   # run_stage <label> <logfile> <command...>
    local label="$1" log="$2"; shift 2
    local t0 t1 rc
    t0=$(date +%s)
    "$@" > "$log" 2>&1; rc=$?
    t1=$(date +%s)
    echo "STAGE ${label} exit=${rc} seconds=$((t1-t0))"
    if [ $rc -ne 0 ]; then
        echo "--- ${log} (tail) ---"
        tail -25 "$log"
    fi
    return $rc
}
"""


@dataclass
class StageResult:
    label: str
    exit_code: int
    seconds: int

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


def run_pipeline(case_dir: Path, n_ranks: int = config.N_RANKS, timeout: int = 7200):
    """Mesh and solve, then convert the final time to VTK.

    foamToVTK is used rather than reading OpenFOAM's native format from Python:
    VTK's OpenFOAM reader is a third-party reimplementation and does
    occasionally choke on unusual boundary types. Converting with OpenFOAM's own
    tool keeps that risk off the path entirely.
    """
    case_dir = Path(case_dir)
    script = STAGE_HARNESS + f"""
M="mpirun -np {n_ranks}"

T0=$(date +%s)
run_stage blockMesh      log.blockMesh      blockMesh                              || exit 10
run_stage decomposePar   log.decomposePar   decomposePar -force                    || exit 11
run_stage snappyHexMesh  log.snappyHexMesh  $M snappyHexMesh -overwrite -parallel  || exit 12
run_stage restore0Dir    log.restore0Dir    restore0Dir -processor                 || exit 13
run_stage checkMesh      log.checkMesh      $M checkMesh -parallel -constant       || true
run_stage potentialFoam  log.potentialFoam  $M potentialFoam -parallel             || true
run_stage simpleFoam     log.simpleFoam     $M simpleFoam -parallel                || exit 15
run_stage reconstruct    log.reconstructPar reconstructPar -latestTime             || exit 16
run_stage foamToVTK      log.foamToVTK      foamToVTK -latestTime                  || exit 17
T1=$(date +%s)
echo "TOTAL seconds=$((T1-T0))"
"""
    (case_dir / "_stages.sh").write_text(script)

    proc = subprocess.run(
        [str(WRAPPER), str(case_dir), "bash _stages.sh"],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    out = proc.stdout + proc.stderr
    (case_dir / "_stages.log").write_text(out)

    stages = [
        StageResult(m.group(1), int(m.group(2)), int(m.group(3)))
        for m in re.finditer(r"STAGE (\S+) exit=(-?\d+) seconds=(\d+)", out)
    ]
    total = re.search(r"TOTAL seconds=(\d+)", out)
    return {
        "stages": stages,
        "total_seconds": int(total.group(1)) if total else None,
        "returncode": proc.returncode,
        "output": out,
        "cells": read_cell_count(case_dir),
    }


def read_cell_count(case_dir: Path) -> int | None:
    log = Path(case_dir) / "log.checkMesh"
    if not log.exists():
        return None
    m = re.findall(r"^\s*cells:\s+(\d+)", log.read_text(), re.M)
    return int(m[-1]) if m else None

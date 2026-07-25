#!/usr/bin/env bash
# Phase 1 calibration: run the stock motorBike tutorial and time each stage.
#
# motorBike is the right benchmark because it exercises the same code path
# production will use - snappyHexMesh + simpleFoam external aerodynamics - at a
# size that finishes in minutes. Convergence is not the point; throughput is.
#
# The stage script is written into the case directory and executed as a file
# rather than passed as a quoted string. Nesting a multi-line script through
# host shell -> docker -> login shell makes quoting the dominant failure mode;
# a file has exactly one level of interpretation.
#
# usage: scripts/calibrate.sh [ranks] [iterations]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RANKS="${1:-6}"
ITERS="${2:-200}"
WORK="${REPO_ROOT}/runs/calibration-np${RANKS}"

rm -rf "$WORK"; mkdir -p "$WORK"

echo "### Staging motorBike (ranks=${RANKS}, iterations=${ITERS})"
docker run --rm --platform linux/arm64 -v "${WORK}:/out" \
    microfluidica/openfoam:2506 bash -lc '
        cp -r $FOAM_TUTORIALS/incompressible/simpleFoam/motorBike/. /out/
        mkdir -p /out/constant/triSurface
        cp -f $FOAM_TUTORIALS/resources/geometry/motorBike.obj.gz /out/constant/triSurface/
        chown -R '"$(id -u):$(id -g)"' /out
    ' >/dev/null

cat > "${WORK}/system/decomposeParDict" <<EOF
FoamFile { version 2.0; format ascii; class dictionary; object decomposeParDict; }
numberOfSubdomains ${RANKS};
method          hierarchical;
coeffs          { n (${RANKS} 1 1); }
EOF

sed -i.bak "s/^endTime.*/endTime         ${ITERS};/" "${WORK}/system/controlDict"

# --- stage script, written as a file ---------------------------------------
cat > "${WORK}/_stages.sh" <<EOF
#!/usr/bin/env bash
# Deliberately no 'set -e': a stage failure should be reported with its exit
# code and log, not silently abort the run leaving no diagnostic behind.
M="mpirun -np ${RANKS}"
EOF
cat >> "${WORK}/_stages.sh" <<'EOF'
echo "WM_OPTIONS=$WM_OPTIONS"

# restore0Dir is a shell function from OpenFOAM's RunFunctions, not a binary.
# Without this it fails with "command not found", the 0/ fields are never
# placed into the processor directories, and every solver then dies with
# "cannot find file processorN/0/p".
. "${WM_PROJECT_DIR}/bin/tools/RunFunctions"

run_stage() {   # run_stage <label> <logfile> <command...>
    local label="$1" log="$2"; shift 2
    local t0 t1 rc
    t0=$(date +%s)
    "$@" > "$log" 2>&1; rc=$?
    t1=$(date +%s)
    echo "STAGE ${label} exit=${rc} seconds=$((t1-t0))"
    if [ $rc -ne 0 ]; then
        echo "--- ${log} (tail) ---"; tail -20 "$log"
    fi
    return $rc
}

T0=$(date +%s)
run_stage surfaceFeatureExtract log.surfaceFeatureExtract surfaceFeatureExtract || exit 1
run_stage blockMesh             log.blockMesh             blockMesh             || exit 1
run_stage decomposePar          log.decomposePar          decomposePar -force   || exit 1
run_stage snappyHexMesh         log.snappyHexMesh         $M snappyHexMesh -overwrite -parallel || exit 1
run_stage topoSet               log.topoSet               $M topoSet -parallel  || true
run_stage restore0Dir           log.restore0Dir           restore0Dir -processor || true
run_stage potentialFoam         log.potentialFoam         $M potentialFoam -writephi -parallel || true
run_stage checkMesh             log.checkMesh             $M checkMesh -parallel -constant || true
run_stage simpleFoam            log.simpleFoam            $M simpleFoam -parallel || exit 1
T1=$(date +%s)
echo "TOTAL seconds=$((T1-T0))"
EOF
chmod +x "${WORK}/_stages.sh"

echo "### Running"
"${REPO_ROOT}/docker/run_openfoam.sh" "$WORK" "bash _stages.sh" 2>&1 | tee "${WORK}/_stages.log"

echo
echo "### Mesh size"
grep -E "^ *cells:" "${WORK}/log.checkMesh" 2>/dev/null | tail -1 || echo "(checkMesh cell count unavailable)"
echo "### Final iteration reached"
grep -E "^Time = " "${WORK}/log.simpleFoam" 2>/dev/null | tail -1 || echo "(none)"
echo "### Force coefficients (last line)"
tail -1 "${WORK}"/postProcessing/forceCoeffs*/0/coefficient*.dat 2>/dev/null || echo "(no forceCoeffs output)"

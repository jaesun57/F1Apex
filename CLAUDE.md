# F1Apex — working notes

CFD of bluff bodies and an F1-like car: OpenFOAM RANS in Docker, post-processed
into a static viewer. Everything below was learned by getting it wrong first.

## Environment

| | |
|---|---|
| Host | Apple M3, 8 cores, 24 GB, arm64 |
| Python | `./.venv/bin/python` (3.11). **Do not use the anaconda base env.** |
| OpenFOAM | `microfluidica/openfoam:2506`, **native arm64** (`linuxARM64GccDPInt32Opt`) |
| Ranks | 6, not 8 — leave headroom for the host scheduler |
| Docker VM | 8.2 GB (default; not raised) |

Measured throughput: **813,000 cell-iterations/s** on 6 ranks. See
[docs/calibration.md](docs/calibration.md). Every wall-clock estimate derives from that
number, and it assumes nothing else is running (see contention below).

## Container gotchas — all of these fail silently

1. **The entrypoint runs a bare `cd` when non-root**, overriding Docker's `-w`.
   Without an explicit `cd /case`, every relative path — all logs, the whole
   `postProcessing/` tree — lands in the tmpfs and vanishes on exit.
   `docker/run_openfoam.sh` handles this; do not undo it.
2. **`bash -l` is required.** The login shell sets `LD_LIBRARY_PATH`; without it
   every solver dies with `libfileFormats.so: cannot open shared object file`.
3. **Write multi-line scripts to a file** and run `bash file.sh`. Passing them as
   quoted strings through host → docker → login shell makes quoting the dominant
   failure mode. `case/runner.py` already does this.
4. **`restore0Dir` is a shell function**, not a binary. Source
   `${WM_PROJECT_DIR}/bin/tools/RunFunctions` first, or it is "command not found",
   the `0/` fields never reach the processor directories, and every solver aborts
   on a missing field.
5. **`0.orig` fields need `#includeEtc "caseDicts/setConstraintTypes"`**, or
   parallel runs abort with `Cannot find patchField entry for procBoundaryNtoM`.
6. **`reconstructPar` needs `reconstructParMesh -constant` first** when snappy
   built the mesh in parallel with `-overwrite`.
7. **Function objects need explicit `executeControl`.** It defaults to every time
   step regardless of `writeControl`, so `yPlus` was recomputing over the whole
   mesh every iteration and writing once.

## CPU contention — the biggest time sink so far

An apparent 20× slowdown (3.94 s/iter vs an expected 0.46) turned out to be
**orphaned MPI processes from earlier runs**: host load average reached **82 on
8 cores**. Nothing was wrong with the case.

Before any timed run:

```bash
docker ps -q | xargs -r docker kill; uptime
```

Never start a second solve while one is active.

## Conventions that must not drift

- **Pressure is kinematic.** OpenFOAM incompressible `p` is p/ρ, so
  `Cp = (p - p_inf) / (0.5 * U_inf**2)` — **no ρ**. A stray ρ is a factor-1.225
  error that looks entirely plausible. Unit-tested in `tests/test_post.py`.
- **`liftDir` is `(0 0 1)`, so downforce is NEGATIVE `Cl`.** Always report
  `Cl_downforce = -Cl` and keep the raw value alongside.
- **Never assume `coefficient.dat` column order** — parse the header comment.
  Column order differs between OpenFOAM versions, and a positional assumption
  silently swaps drag for lift.
- **`Aref`, `lRef`, `CofR` must be identical across every case and element**, or
  per-element coefficients stop being additive and the centre of pressure appears
  to move for bookkeeping reasons.
- **Colour scales use the 99.5th percentile, not the max.** One bad cell near a
  wheel contact patch produced 215 m/s against a 50 m/s freestream; the raw max
  would flatten every image in a sweep.
- **Field array ordering is `index = iz * nx + ix`** (x fast, z slow), documented
  in both `post/field.py` and `docs/js/flow.js`. A transposed read animates
  plausibly and is completely wrong — no visual inspection will catch it.

## Convergence

Residuals are **not** used: steady external aero with separation plateaus at
1e-3–1e-4 and never reaches 1e-6. `post/forces.convergence()` watches the forces
instead, over a 200-iteration window, and reports three states:

- `drifting` — the mean is still moving between windows; the run is unfinished.
- `oscillating` — the mean is fixed but the instantaneous value swings. The
  solver has settled into a limit cycle. **The mean is usable, the instantaneous
  value is not**, and the oscillation itself says the flow is unsteady and steady
  RANS is the wrong model for the wake.
- `converged` — both stationary.

The box case is `oscillating`: Cd drift 0.001% / scatter 0.128%, Cl drift 0.005%
/ scatter 3.348%. That is physically right — a sharp-edged bluff body genuinely
sheds vortices.

## Layout

```
src/f1apex/
  config.py              constants, paths, cell-budget and y+ arithmetic
  geometry/
    primitives.py        BoxBody
    car.py               parametric F1-like car
    airfoils.py wing.py  multi-element wing (slot gap solved, not assigned)
  case/
    tunnel.py            writes a full OpenFOAM case from any STL
    runner.py            staged container pipeline with per-stage timing
  post/
    forces.py            coefficients + three-state convergence
    field.py             y=0 slice -> regular grid, for the 2D animation
    tracks.py            3D streamlines -> flat GL buffers
    streamlines.py       PyVista tracing and offline PNG rendering
docs/                    GitHub Pages root (main:/docs), fully self-contained
runs/                    gitignored; ~1.5 GB, regenerable from code
```

## Commands

```bash
./.venv/bin/python -m pytest tests/ -q          # 22 tests
./scripts/calibrate.sh 6 200                    # motorBike throughput benchmark
./docker/run_openfoam.sh <case_dir> "bash x.sh" # run anything in the container
cd docs && python3 -m http.server 8777          # serve the viewer locally
```

The viewer must stay **fully self-contained** — no CDN, no npm, no build step —
because that is what lets `docs/` deploy to Pages verbatim. Check with:

```bash
grep -rnE "https?://|cdn|unpkg" docs/js docs/index.html
```

## Geometry notes

- **Wheels extend below z=0** (`wheel_sink`). A tyre tangent to the ground makes
  snappyHexMesh produce degenerate cells at the contact point; letting geometry
  penetrate means the domain boundary cuts a clean finite-width patch.
- **Loft sections are selected by AREA**, not `sort_by()` default. `sort_by()`
  sorts on Z, which on a thin section plate returns its bottom *edge* rather than
  the cross-section. Lofting two edges produced a flat sheet instead of a car
  body — volume 0.875 m³ where it should have been 2.832 — and the bounding box
  looked correct the whole time.
- **`snappyHexMesh` `locationInMesh` is computed, never guessed.** A point inside
  the solid or outside the domain makes snappy mesh the wrong region and report
  success.
- The wing's `gap` is a **requested slot gap solved for by bisection**, not a
  coordinate offset — the offset achieving it depends on flap angle, overlap and
  both section shapes at once. The bisection returns the bracket end that
  satisfies the request, since converging from below leaves the gap fractionally
  short and rounds down a cell at the design point.

## Current state and honest limits

Nothing is validated. No comparison against experiment or published data, and no
grid-convergence study. Meshes are coarse and use wall functions, so boundary
layers are modelled rather than resolved. Treat the coefficients as the right
order of magnitude, not as numbers to quote.

The parametric car currently generates **lift, not downforce** (Cl = +0.36).
That is correct for the geometry as built: its wings are flat plates at zero
incidence. Wiring the existing inverted aerofoil sections from
`geometry/airfoils.py` into the car is the obvious next step.

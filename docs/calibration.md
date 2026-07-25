# Phase 1 — Container calibration

Measured, not estimated. Every wall-clock projection in this project derives
from the throughput figure below.

## Platform

| | |
|---|---|
| Host | Apple M3, 8 cores, 24 GB RAM |
| Docker VM | 8.2 GB (default; not yet raised) |
| Image | `microfluidica/openfoam:2506` |
| Build | `linuxARM64GccDPInt32Opt` — **natively compiled arm64, not emulated** |
| Ranks | 6 (leaving headroom for the host scheduler) |

Native arm64 was the single biggest feasibility risk. It is confirmed closed:
the container reports an ARM64 build, and throughput is consistent with native
execution rather than the ~10× penalty emulation would impose.

## Benchmark — stock `motorBike` tutorial

Same code path production will use: `snappyHexMesh` + `simpleFoam` external
aerodynamics with `forceCoeffs`.

| Stage | Time |
|---|---|
| surfaceFeatureExtract | 1 s |
| blockMesh | 0 s |
| decomposePar | 0 s |
| **snappyHexMesh** | **60 s** |
| topoSet / restore0Dir | 0 s |
| potentialFoam | 1 s |
| checkMesh | 1 s |
| **simpleFoam (200 iterations)** | **87 s** |
| **Total** | **150 s** |

Mesh: **353,853 cells**. Reached iteration 200; `forceCoeffs` wrote coefficients.

## Derived throughput

```
meshing   5,898 cells/s
solving   813,455 cell-iterations/s          (135,576 per core)
```

## Projected cost per production case

| cells | iterations | mesh | solve | total | 19-case sweep |
|---|---|---|---|---|---|
| 2M | 1000 | 5.7 m | 41.0 m | **46.6 m** | **14.8 h** |
| 2M | 1500 | 5.7 m | 61.5 m | 67.1 m | 21.3 h |
| 3M | 1000 | 8.5 m | 61.5 m | 69.9 m | 22.1 h |
| 3M | 1500 | 8.5 m | 92.2 m | 100.7 m | 31.9 h |
| 4M | 1500 | 11.3 m | 122.9 m | 134.2 m | 42.5 h |

## What this changes

The plan assumed 20–45 min per case with the sweep running in a single night.
That holds **only at ~2M cells and ~1000 iterations** (47 min/case, ~15 h — one
long night). At 3M cells and 1500 iterations it is ~1.7 h/case and ~32 h, which
is two to three nights.

This is a real constraint on Phase 3, where the refinement study picks the
production mesh. The choice is not free: it trades slot-gap resolution against
sweep duration, and the honest version of that decision needs the refinement
cost curve, not a guess.

Caveat: motorBike is a bluff body, not a wing. Cell-iterations per second
transfers between the two; **iterations required to converge does not** — a
multi-element wing in ground effect may well need more than 1000. The iteration
counts above are assumptions until Phase 3 measures force stationarity on the
real geometry.

## Reproduce

```bash
scripts/calibrate.sh 6 200
```

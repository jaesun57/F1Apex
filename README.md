# F1Apex

CFD-backed analysis of a Formula 1 front wing: **how do downforce, drag, and the flow field change when you change the wing setup?**

This is not a racing game and not a lap simulator. It is a parametric aerodynamics study:

```
parametric wing (build123d) → per-element STL
  → OpenFOAM case (snappyHexMesh → potentialFoam → simpleFoam, Spalart-Allmaras)
  → per-element forces + Cp curves + flow slices
  → aero map (JSON) + rendered PNGs
  → static site, interpolated in the browser
```

## Status

Early. Nothing here is validated yet — see the roadmap below.

## What it computes

- Downforce, drag, L/D and centre of pressure, broken down **per wing element**
- Cp distribution along each element at several spanwise stations
- Velocity / pressure / streamline slices through the wing
- Swept over flap angle, ride height, and yaw (yaw ≡ crosswind)

## What it does not compute — read this first

**v1 solves the wing in isolation.** There is no front tyre in the domain.

That matters more than it sounds. A modern F1 front wing's primary job is arguably not raw downforce but **outwash** — steering flow around the rotating front tyre and managing the tyre wake that would otherwise hit the floor. That is what the endplate and the Y250 vortex are for.

So v1 answers *"how does this wing perform"*, not *"what does this wing do for the car"*. Flap-angle trends are directionally meaningful; the interaction driving most real setup decisions is absent until the rotating wheel lands in v1.5.

Also absent in v1: any full-car coupling (front wing wake feeding the floor and diffuser), unsteady effects, gusts, and any lap-time model.

**On "Hungaroring":** the circuit enters only as the choice of freestream velocity (~50 m/s, reflecting its low average speed) and the high-downforce end of the flap-angle range. There is no track model.

## Accuracy posture

Treat this as a **Δ-tool**. With wall functions on a few-million-cell mesh, absolute Cl/Cd carries several percent error. Deltas across the sweep are substantially more trustworthy than absolute values, and mesh-independence (GCI) plus the validation curve are published so you can calibrate for yourself. Every case reports its own mesh quality — cell count, y+ percentiles, prism-layer coverage, convergence flag.

## Roadmap

| Phase | Content |
|---|---|
| 0 | Repo containment ✅ |
| 1 | OpenFOAM arm64 container calibration |
| 2 | Walking skeleton, end to end at coarse mesh |
| 3 | Mesh + solver quality; slot-gap resolution study |
| 4 | **Validation gate** — Zhang & Zerihan wing in ground effect, GCI, yaw symmetry |
| 5–7 | Geometry hardening, sweep infrastructure, production sweep |
| 8 | Static frontend + corner proxy |
| 9 | v1.5 — rotating front wheel |
| — | v2 — full car |

The production sweep does not start until Phase 4 passes.

## Requirements

Apple Silicon or Linux, Docker (native arm64 OpenFOAM image), Python 3.11+.

## License

MIT

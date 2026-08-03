"""Render the wing section geometry - a sanity check you can actually look at.

Not decoration: the slot gap and ground clearance are the two things that decide
whether the mesh resolves the physics, and a plot catches an interpenetrating or
mis-placed element far faster than a failed snappyHexMesh run does.
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from f1apex import config
from f1apex.geometry.wing import WingParams, sections_for, signed_gap

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("docs/img/geometry")
ANGLES = [2, 6, 10, 14, 18]
CELL = config.cell_size_at_level(config.required_refinement_level())


def plot_sections(ax, p: WingParams, annotate: bool = True):
    main, flap = sections_for(p)
    gap = signed_gap(main, flap)

    ax.fill(main[:, 0], main[:, 1], color="#2b6cb0", alpha=0.85, lw=0, zorder=3)
    ax.fill(flap[:, 0], flap[:, 1], color="#c05621", alpha=0.85, lw=0, zorder=3)
    ax.axhline(0.0, color="#444", lw=2.0, zorder=1)
    ax.text(0.005, 0.0015, "moving ground", fontsize=6, color="#444", va="bottom")

    if annotate:
        # Mark where the slot is narrowest - the jet that makes a multi-element
        # wing work has to pass through here.
        from f1apex.geometry.wing import _point_to_polyline_distance

        d = _point_to_polyline_distance(main, flap)
        i = int(np.argmin(d))
        ax.plot(*main[i], "o", ms=3, color="crimson", zorder=5)
        ax.annotate(
            f"slot {gap * 1e3:.1f} mm\n({gap / CELL:.1f} cells)",
            xy=main[i],
            xytext=(main[i, 0] + 0.03, main[i, 1] + 0.045),
            fontsize=6.5,
            color="crimson",
            arrowprops=dict(arrowstyle="->", color="crimson", lw=0.7),
        )
    ax.set_aspect("equal")
    ax.set_xlim(-0.04, 0.42)
    ax.set_ylim(-0.01, 0.16)
    return gap


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    # 1. Flap-angle sweep
    fig, axes = plt.subplots(len(ANGLES), 1, figsize=(7.2, 1.5 * len(ANGLES)), sharex=True)
    for ax, a in zip(axes, ANGLES):
        p = WingParams(flap_angle_deg=a)
        g = plot_sections(ax, p)
        ax.set_ylabel(f"{a}°", rotation=0, ha="right", va="center", fontsize=9)
        ax.tick_params(labelsize=6)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        print(f"flap {a:>3}deg  gap {g * 1e3:5.2f} mm  {g / CELL:.1f} cells")
    axes[0].set_title(
        "Front wing sections vs flap angle  (main plane blue, flap orange)", fontsize=10
    )
    axes[-1].set_xlabel("x  [m]", fontsize=8)
    fig.tight_layout()
    fig.savefig(OUT / "flap_angle_sweep.png", dpi=170)
    print(f"wrote {OUT / 'flap_angle_sweep.png'}")

    # 2. Ride-height sweep at fixed flap angle
    fig, axes = plt.subplots(3, 1, figsize=(7.2, 4.5), sharex=True)
    for ax, rh in zip(axes, [0.040, 0.060, 0.080]):
        p = WingParams(ride_height=rh, flap_angle_deg=10)
        plot_sections(ax, p, annotate=False)
        ax.set_ylabel(f"h={rh * 1e3:.0f}mm", rotation=0, ha="right", va="center", fontsize=8)
        ax.tick_params(labelsize=6)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
    axes[0].set_title("Ride height sweep at flap 10°  (ground effect)", fontsize=10)
    axes[-1].set_xlabel("x  [m]", fontsize=8)
    fig.tight_layout()
    fig.savefig(OUT / "ride_height_sweep.png", dpi=170)
    print(f"wrote {OUT / 'ride_height_sweep.png'}")


if __name__ == "__main__":
    main()

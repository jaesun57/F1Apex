"""Simple bluff-body geometry.

Deliberately the crudest possible stand-in for a car: a rectangular block with
ground clearance. It exists so the whole pipeline - mesh, solve, streamline
render - can be proven end to end before geometric complexity is introduced.

Everything here returns a watertight solid exported as STL, the same contract
the wing geometry uses, so downstream case building does not care which one it
is given.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path

from build123d import Box, Pos, export_stl


@dataclass(frozen=True)
class BoxBody:
    """A rectangular body sitting above a ground plane.

    Proportions are loosely car-like (long, low, with clearance underneath) so
    the flow shows the features worth looking at: an upstream stagnation
    region, acceleration under the floor, separation off the sharp trailing
    edge, and a recirculating wake.
    """

    length: float = 0.400      # x, streamwise
    width: float = 0.200       # y, spanwise
    height: float = 0.150      # z
    ground_clearance: float = 0.030
    x_origin: float = 0.0      # nose position

    @property
    def bounds(self) -> dict[str, tuple[float, float]]:
        return {
            "x": (self.x_origin, self.x_origin + self.length),
            "y": (-self.width / 2.0, self.width / 2.0),
            "z": (self.ground_clearance, self.ground_clearance + self.height),
        }

    def solid(self):
        cx = self.x_origin + self.length / 2.0
        cz = self.ground_clearance + self.height / 2.0
        return Pos(cx, 0.0, cz) * Box(self.length, self.width, self.height)

    def export(self, path: Path, tolerance: float = 1e-4) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        export_stl(self.solid(), str(path), tolerance=tolerance, angular_tolerance=0.2)
        return path

    def as_dict(self) -> dict:
        return asdict(self)

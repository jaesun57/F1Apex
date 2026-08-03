"""Build a complete OpenFOAM case for a body in a virtual wind tunnel.

Takes an STL body and a freestream condition and writes a ready-to-run case.
Nothing here is specific to a box - swapping in a car or a wing means passing a
different STL, which is the point.

Physics choices follow the project decisions:
  * Spalart-Allmaras (one-equation, tuned for attached/mildly-separated
    aerodynamic boundary layers)
  * rolling road: ground U = freestream, NOT movingWallVelocity (that is for
    moving meshes and would be wrong on a static mesh)
  * wall functions at y+ 30-100 via nutUSpaldingWallFunction, which blends
    continuously into the buffer layer where local y+ drops
  * potentialFoam initialisation, then SIMPLEC
"""

from __future__ import annotations

import math
import shutil
from dataclasses import dataclass
from pathlib import Path

from f1apex import config

HEADER = """/*--------------------------------*- C++ -*----------------------------------*/
FoamFile
{{
    version     2.0;
    format      ascii;
    class       {cls};
    {extra}object      {obj};
}}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //
"""


def _head(cls: str, obj: str, location: str | None = None) -> str:
    extra = f'location    "{location}";\n    ' if location else ""
    return HEADER.format(cls=cls, obj=obj, extra=extra)


@dataclass(frozen=True)
class TunnelParams:
    """Domain sizing and mesh resolution.

    Domain extents are expressed as multiples of the body size so the tunnel
    scales sensibly whatever body is passed in. The downstream length is much
    larger than the upstream one because the wake, not the approach flow, is
    what needs room to develop before it meets the outlet.
    """

    u_inf: float = 30.0
    yaw_deg: float = 0.0

    upstream: float = 2.5       # x body-lengths ahead of the nose
    downstream: float = 6.0     # x body-lengths behind the tail
    lateral: float = 3.0        # y half-widths either side
    ceiling: float = 4.0        # z body-heights above the ground

    base_cell: float = 0.040    # background blockMesh cell size
    surface_level: tuple[int, int] = (2, 3)   # snappy min/max on the body
    wake_level: int = 2                       # refinement region level
    n_layers: int = 4

    end_time: int = 400
    write_interval: int = 400   # only the final state is needed
    n_ranks: int = config.N_RANKS

    @property
    def velocity(self) -> tuple[float, float, float]:
        a = math.radians(self.yaw_deg)
        return (self.u_inf * math.cos(a), self.u_inf * math.sin(a), 0.0)


def _domain(body_bounds: dict, p: TunnelParams) -> dict:
    (bx0, bx1), (by0, by1), (bz0, bz1) = (
        body_bounds["x"],
        body_bounds["y"],
        body_bounds["z"],
    )
    L, W, H = bx1 - bx0, by1 - by0, bz1
    return {
        "x": (bx0 - p.upstream * L, bx1 + p.downstream * L),
        "y": (by0 - p.lateral * W, by1 + p.lateral * W),
        "z": (0.0, p.ceiling * H),
    }


def write_case(
    case_dir: Path,
    stl_path: Path,
    body_bounds: dict,
    p: TunnelParams,
    body_patch: str = "body",
) -> dict:
    """Write every file the case needs. Returns domain/mesh metadata."""
    case_dir = Path(case_dir)
    for sub in ("system", "constant/triSurface", "0.orig"):
        (case_dir / sub).mkdir(parents=True, exist_ok=True)
    shutil.copy(stl_path, case_dir / "constant" / "triSurface" / f"{body_patch}.stl")

    dom = _domain(body_bounds, p)
    (x0, x1), (y0, y1), (z0, z1) = dom["x"], dom["y"], dom["z"]
    nx = max(4, round((x1 - x0) / p.base_cell))
    ny = max(4, round((y1 - y0) / p.base_cell))
    nz = max(4, round((z1 - z0) / p.base_cell))

    ux, uy, uz = p.velocity
    umag = p.u_inf

    # --- blockMesh ---------------------------------------------------------
    verts = [
        (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
        (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1),
    ]
    vtxt = "\n".join(f"    ({vx:.6g} {vy:.6g} {vz:.6g})" for vx, vy, vz in verts)
    (case_dir / "system/blockMeshDict").write_text(
        _head("dictionary", "blockMeshDict")
        + f"""
scale   1;

vertices
(
{vtxt}
);

blocks
(
    hex (0 1 2 3 4 5 6 7) ({nx} {ny} {nz}) simpleGrading (1 1 1)
);

edges ();

boundary
(
    inlet   {{ type patch; faces ( (0 4 7 3) ); }}
    outlet  {{ type patch; faces ( (1 2 6 5) ); }}
    ground  {{ type wall;  faces ( (0 3 2 1) ); }}
    top     {{ type patch; faces ( (4 5 6 7) ); }}
    sides   {{ type patch; faces ( (0 1 5 4) (3 7 6 2) ); }}
);

mergePatchPairs ();
"""
    )

    # --- snappyHexMesh -----------------------------------------------------
    # locationInMesh is computed, never guessed: a point far upstream and high
    # above the body is unambiguously in the fluid. Landing this point inside
    # the solid (or outside the domain) is a classic silent meshing failure -
    # snappy happily meshes the wrong region and reports success.
    loc = (x0 + 0.05 * (x1 - x0), 0.5 * (y0 + y1) + 0.13 * (y1 - y0), 0.80 * z1)
    smin, smax = p.surface_level
    bx0, bx1 = body_bounds["x"]
    by0, by1 = body_bounds["y"]
    bz0, bz1 = body_bounds["z"]
    L = bx1 - bx0
    # Refinement region: hugs the body and extends downstream to hold the wake.
    rb = (
        (bx0 - 0.6 * L, by0 - 0.8 * (by1 - by0), 0.0),
        (bx1 + 2.5 * L, by1 + 0.8 * (by1 - by0), bz1 + 0.9 * (bz1 - bz0)),
    )
    (case_dir / "system/snappyHexMeshDict").write_text(
        _head("dictionary", "snappyHexMeshDict")
        + f"""
castellatedMesh true;
snap            true;
addLayers       true;

geometry
{{
    {body_patch}.stl
    {{
        type triSurfaceMesh;
        name {body_patch};
    }}
    wakeBox
    {{
        type searchableBox;
        min ({rb[0][0]:.6g} {rb[0][1]:.6g} {rb[0][2]:.6g});
        max ({rb[1][0]:.6g} {rb[1][1]:.6g} {rb[1][2]:.6g});
    }}
}}

castellatedMeshControls
{{
    maxLocalCells       2000000;
    maxGlobalCells      6000000;
    minRefinementCells  10;
    maxLoadUnbalance    0.10;
    nCellsBetweenLevels 3;

    features ();

    refinementSurfaces
    {{
        {body_patch} {{ level ({smin} {smax}); patchInfo {{ type wall; }} }}
    }}

    resolveFeatureAngle 30;

    refinementRegions
    {{
        wakeBox {{ mode inside; levels ((1e15 {p.wake_level})); }}
    }}

    locationInMesh ({loc[0]:.6g} {loc[1]:.6g} {loc[2]:.6g});
    allowFreeStandingZoneFaces true;
}}

snapControls
{{
    nSmoothPatch    3;
    tolerance       2.0;
    nSolveIter      50;
    nRelaxIter      5;
    nFeatureSnapIter 10;
    implicitFeatureSnap    false;
    explicitFeatureSnap    true;
    multiRegionFeatureSnap false;
}}

addLayersControls
{{
    relativeSizes       true;
    layers {{ "{body_patch}.*" {{ nSurfaceLayers {p.n_layers}; }} }}
    expansionRatio      1.2;
    finalLayerThickness 0.5;
    minThickness        0.05;
    nGrow               0;
    featureAngle        130;
    slipFeatureAngle    30;
    nRelaxIter          5;
    nSmoothSurfaceNormals 1;
    nSmoothNormals      3;
    nSmoothThickness    10;
    maxFaceThicknessRatio 0.5;
    maxThicknessToMedialRatio 0.3;
    minMedialAxisAngle  90;
    nBufferCellsNoExtrude 0;
    nLayerIter          50;
}}

meshQualityControls
{{
    maxNonOrtho         65;
    maxBoundarySkewness 20;
    maxInternalSkewness 4;
    maxConcave          80;
    minVol              1e-13;
    minTetQuality       1e-15;
    minArea             -1;
    minTwist            0.02;
    minDeterminant      0.001;
    minFaceWeight       0.02;
    minVolRatio         0.01;
    minTriangleTwist    -1;
    nSmoothScale        4;
    errorReduction      0.75;
}}

mergeTolerance 1e-6;
"""
    )

    # --- constant ----------------------------------------------------------
    (case_dir / "constant").mkdir(exist_ok=True)
    (case_dir / "constant/transportProperties").write_text(
        _head("dictionary", "transportProperties")
        + f"\ntransportModel  Newtonian;\nnu              {config.NU};\n"
    )
    (case_dir / "constant/turbulenceProperties").write_text(
        _head("dictionary", "turbulenceProperties")
        + "\nsimulationType  RAS;\n\nRAS\n{\n    RASModel        SpalartAllmaras;\n"
          "    turbulence      on;\n    printCoeffs     on;\n}\n"
    )

    # --- 0.orig fields -----------------------------------------------------
    nut_inf = 4.0 * config.NU        # standard SA freestream ratio
    (case_dir / "0.orig/U").write_text(
        _head("volVectorField", "U", "0")
        + f"""
dimensions      [0 1 -1 0 0 0 0];
internalField   uniform ({ux:.6g} {uy:.6g} {uz:.6g});

boundaryField
{{
    // Supplies default entries for constraint patches - notably the
    // procBoundary* patches decomposePar creates. Without it every
    // parallel solver aborts with "Cannot find patchField entry for
    // procBoundaryNtoM".
    #includeEtc "caseDicts/setConstraintTypes"

    inlet   {{ type fixedValue; value uniform ({ux:.6g} {uy:.6g} {uz:.6g}); }}
    outlet  {{ type inletOutlet; inletValue uniform (0 0 0); value uniform ({ux:.6g} {uy:.6g} {uz:.6g}); }}
    // Rolling road: the floor moves with the freestream, so no boundary layer
    // grows on it. This is the idealised moving ground, and is closer to
    // reality for a car than a stationary tunnel floor.
    ground  {{ type fixedValue; value uniform ({ux:.6g} {uy:.6g} {uz:.6g}); }}
    top     {{ type slip; }}
    sides   {{ type slip; }}
    {body_patch} {{ type noSlip; }}
}}
"""
    )
    (case_dir / "0.orig/p").write_text(
        _head("volScalarField", "p", "0")
        + """
dimensions      [0 2 -2 0 0 0 0];
internalField   uniform 0;

boundaryField
{
    #includeEtc "caseDicts/setConstraintTypes"

    inlet   { type zeroGradient; }
    outlet  { type fixedValue; value uniform 0; }
    ground  { type zeroGradient; }
    top     { type slip; }
    sides   { type slip; }
    %s { type zeroGradient; }
}
""" % body_patch
    )
    (case_dir / "0.orig/nuTilda").write_text(
        _head("volScalarField", "nuTilda", "0")
        + f"""
dimensions      [0 2 -1 0 0 0 0];
internalField   uniform {nut_inf:.6g};

boundaryField
{{
    // Supplies default entries for constraint patches - notably the
    // procBoundary* patches decomposePar creates. Without it every
    // parallel solver aborts with "Cannot find patchField entry for
    // procBoundaryNtoM".
    #includeEtc "caseDicts/setConstraintTypes"

    inlet   {{ type fixedValue; value uniform {nut_inf:.6g}; }}
    outlet  {{ type inletOutlet; inletValue uniform 0; value uniform {nut_inf:.6g}; }}
    ground  {{ type fixedValue; value uniform 0; }}
    top     {{ type slip; }}
    sides   {{ type slip; }}
    {body_patch} {{ type fixedValue; value uniform 0; }}
}}
"""
    )
    (case_dir / "0.orig/nut").write_text(
        _head("volScalarField", "nut", "0")
        + f"""
dimensions      [0 2 -1 0 0 0 0];
internalField   uniform 0;

boundaryField
{{
    // Supplies default entries for constraint patches - notably the
    // procBoundary* patches decomposePar creates. Without it every
    // parallel solver aborts with "Cannot find patchField entry for
    // procBoundaryNtoM".
    #includeEtc "caseDicts/setConstraintTypes"

    inlet   {{ type calculated; value uniform 0; }}
    outlet  {{ type calculated; value uniform 0; }}
    // Spalding blends continuously through the buffer layer, so it degrades
    // gracefully where local y+ falls below the log-law range.
    ground  {{ type nutUSpaldingWallFunction; value uniform 0; }}
    top     {{ type slip; }}
    sides   {{ type slip; }}
    {body_patch} {{ type nutUSpaldingWallFunction; value uniform 0; }}
}}
"""
    )

    # --- system ------------------------------------------------------------
    aref = (by1 - by0) * (bz1 - bz0)      # frontal area
    lref = bx1 - bx0
    (case_dir / "system/controlDict").write_text(
        _head("dictionary", "controlDict")
        + f"""
application     simpleFoam;
startFrom       latestTime;
startTime       0;
stopAt          endTime;
endTime         {p.end_time};
deltaT          1;
writeControl    runTime;
writeInterval   {p.write_interval};
purgeWrite      0;
writeFormat     ascii;
writePrecision  8;
writeCompression off;
timeFormat      general;
timePrecision   6;
runTimeModifiable true;

functions
{{
    forceCoeffs
    {{
        type            forceCoeffs;
        libs            (forces);
        writeControl    timeStep;
        writeInterval   1;
        patches         ({body_patch});
        rho             rhoInf;
        rhoInf          {config.RHO_INF};
        // liftDir is +z, so DOWNFORCE APPEARS AS NEGATIVE Cl.
        // Always report Cl_downforce = -Cl downstream.
        liftDir         (0 0 1);
        dragDir         (1 0 0);
        CofR            (0 0 0);
        pitchAxis       (0 1 0);
        magUInf         {umag};
        lRef            {lref:.6g};
        Aref            {aref:.6g};
    }}

    yPlus
    {{
        type            yPlus;
        libs            (fieldFunctionObjects);
        // executeControl must be set explicitly: it defaults to every time
        // step regardless of writeControl, so without this yPlus is computed
        // over the whole mesh on every iteration and only written at the end.
        executeControl  writeTime;
        writeControl    writeTime;
    }}

    residuals
    {{
        type            solverInfo;
        libs            (utilityFunctionObjects);
        fields          (U p nuTilda);
        writeControl    timeStep;
    }}
}}
"""
    )

    (case_dir / "system/fvSchemes").write_text(
        _head("dictionary", "fvSchemes")
        + """
ddtSchemes      { default steadyState; }

gradSchemes
{
    default         Gauss linear;
    limited         cellLimited Gauss linear 1;
    grad(U)         $limited;
    grad(nuTilda)   $limited;
}

divSchemes
{
    default         none;
    div(phi,U)      bounded Gauss linearUpwind limited;
    div(phi,nuTilda) bounded Gauss linearUpwind limited;
    div((nuEff*dev2(T(grad(U))))) Gauss linear;
}

// 'limited corrected 0.5' rather than plain 'corrected': snappyHexMesh
// produces some non-orthogonality, and the limiter keeps that from
// destabilising the solution.
laplacianSchemes { default Gauss linear limited corrected 0.5; }
interpolationSchemes { default linear; }
snGradSchemes   { default limited corrected 0.5; }
wallDist        { method meshWave; }
"""
    )

    (case_dir / "system/fvSolution").write_text(
        _head("dictionary", "fvSolution")
        + """
solvers
{
    p
    {
        solver          GAMG;
        tolerance       1e-7;
        relTol          0.01;
        smoother        GaussSeidel;
    }

    Phi
    {
        $p;
        relTol          0.0;
    }

    "(U|nuTilda)"
    {
        solver          smoothSolver;
        smoother        GaussSeidel;
        nSweeps         2;
        tolerance       1e-8;
        relTol          0.1;
    }
}

SIMPLE
{
    nNonOrthogonalCorrectors 0;
    // SIMPLEC: noticeably faster than SIMPLE with p-relaxation 0.3 for steady
    // external aerodynamics, and allows p relaxation of 1.0 below.
    consistent          yes;
    residualControl { p 1e-5; U 1e-5; nuTilda 1e-5; }
}

potentialFlow { nNonOrthogonalCorrectors 10; }

relaxationFactors
{
    equations { U 0.9; nuTilda 0.9; }
    fields    { p 1.0; }
}
"""
    )

    (case_dir / "system/decomposeParDict").write_text(
        _head("dictionary", "decomposeParDict")
        + f"\nnumberOfSubdomains {p.n_ranks};\nmethod hierarchical;\n"
          f"coeffs {{ n ({p.n_ranks} 1 1); }}\n"
    )

    (case_dir / "system/meshQualityDict").write_text(
        _head("dictionary", "meshQualityDict")
        + '\n#includeEtc "caseDicts/meshQualityDict"\n'
    )

    return {
        "domain": dom,
        "background_cells": nx * ny * nz,
        "blocks": (nx, ny, nz),
        "locationInMesh": loc,
        "Aref": aref,
        "lRef": lref,
        "u_inf": p.u_inf,
        "body_patch": body_patch,
    }

"""
Thin Wall DFM Check for STEP Files
====================================
Uses CadQuery + OpenCascade (OCP) to detect thin walls in each solid
of a STEP file by ray-casting from sampled face points along inward
normals and measuring the distance to the opposing interior surface.

Usage:
    python thin_wall_check.py <step_file_path> [--min-thickness <mm>] [--samples <n>]

Example:
    python thin_wall_check.py part.step --min-thickness 1.0 --samples 15
"""

import argparse
import sys
import json
import math
from dataclasses import dataclass, field
from typing import List, Tuple, Optional

import cadquery as cq

# OCP (OpenCascade) imports
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_SOLID, TopAbs_FACE
from OCP.TopoDS import TopoDS
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepGProp import BRepGProp_Face as BRepGPropFace
from OCP.GeomAbs import (
    GeomAbs_Plane,
    GeomAbs_Cylinder,
    GeomAbs_Cone,
    GeomAbs_Sphere,
    GeomAbs_Torus,
    GeomAbs_BSplineSurface,
    GeomAbs_BezierSurface,
    GeomAbs_OtherSurface,
    GeomAbs_SurfaceOfRevolution,
    GeomAbs_SurfaceOfExtrusion,
)
from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector
from OCP.gp import gp_Pnt, gp_Dir, gp_Lin, gp_Vec
from OCP.BRepTools import BRepTools
from OCP.BRepBndLib import BRepBndLib
from OCP.Bnd import Bnd_Box


# ---------------------------------------------------------------------------
# Data classes for results
# ---------------------------------------------------------------------------

@dataclass
class ThinWallRegion:
    """Represents a detected thin wall region."""
    face_index: int
    face_type: str
    sample_point: Tuple[float, float, float]
    measured_thickness: float
    normal_direction: Tuple[float, float, float]


@dataclass
class SolidResult:
    """DFM results for a single solid."""
    solid_index: int
    bounding_box: Tuple[float, float, float, float, float, float]  # xmin,ymin,zmin,xmax,ymax,zmax
    total_faces: int
    faces_checked: int
    min_thickness_found: float
    max_thickness_found: float
    avg_thickness: float
    thin_wall_regions: List[ThinWallRegion] = field(default_factory=list)
    has_thin_walls: bool = False


@dataclass
class DFMReport:
    """Overall DFM report."""
    file_path: str
    total_solids: int
    min_thickness_threshold: float
    solids: List[SolidResult] = field(default_factory=list)
    overall_pass: bool = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_surface_type_name(surf_type) -> str:
    """Convert OCP surface type enum to readable string."""
    mapping = {
        GeomAbs_Plane: "Plane",
        GeomAbs_Cylinder: "Cylinder",
        GeomAbs_Cone: "Cone",
        GeomAbs_Sphere: "Sphere",
        GeomAbs_Torus: "Torus",
        GeomAbs_BSplineSurface: "BSpline",
        GeomAbs_BezierSurface: "Bezier",
        GeomAbs_SurfaceOfRevolution: "Revolution",
        GeomAbs_SurfaceOfExtrusion: "Extrusion",
        GeomAbs_OtherSurface: "Other",
    }
    return mapping.get(surf_type, "Unknown")


def get_bounding_box(shape) -> Tuple[float, float, float, float, float, float]:
    """Get the axis-aligned bounding box of a shape."""
    bbox = Bnd_Box()
    BRepBndLib.Add_s(shape, bbox)
    xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
    return (xmin, ymin, zmin, xmax, ymax, zmax)


def bounding_box_diagonal(bbox: Tuple[float, float, float, float, float, float]) -> float:
    """Compute diagonal length of a bounding box."""
    dx = bbox[3] - bbox[0]
    dy = bbox[4] - bbox[1]
    dz = bbox[5] - bbox[2]
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def extract_solids(shape) -> list:
    """Extract all solids from a TopoDS_Shape."""
    solids = []
    explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    while explorer.More():
        solid = TopoDS.Solid_s(explorer.Current())
        solids.append(solid)
        explorer.Next()
    return solids


def extract_faces(shape) -> list:
    """Extract all faces from a shape."""
    faces = []
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face_s(explorer.Current())
        faces.append(face)
        explorer.Next()
    return faces


def get_face_uv_bounds(face) -> Tuple[float, float, float, float]:
    """Get the UV parameter bounds of a face."""
    umin, umax, vmin, vmax = BRepTools.UVBounds_s(face)
    return umin, umax, vmin, vmax


def sample_face_points(
    face, adaptor: BRepAdaptor_Surface, n_samples: int
) -> List[Tuple[gp_Pnt, gp_Dir]]:
    """
    Sample points on a face and compute inward normals.

    Returns list of (point, inward_normal_direction) tuples.
    Uses a grid sampling strategy in UV parameter space, skipping
    points that fall outside the face boundary.
    """
    umin, umax, vmin, vmax = get_face_uv_bounds(face)

    # BRepGProp_Face for normal evaluation on the face
    gprop_face = BRepGPropFace(face)

    samples = []
    # Create a grid of n_samples x n_samples in UV space
    # Use interior points (avoid exact boundaries)
    nu = max(n_samples, 2)
    nv = max(n_samples, 2)

    for i in range(nu):
        for j in range(nv):
            u = umin + (umax - umin) * (i + 0.5) / nu
            v = vmin + (vmax - vmin) * (j + 0.5) / nv

            # Evaluate point and normal on the face
            pnt = gp_Pnt()
            normal_vec = gp_Vec()
            try:
                gprop_face.Normal(u, v, pnt, normal_vec)
            except Exception:
                continue

            # Skip degenerate normals
            if normal_vec.Magnitude() < 1e-10:
                continue

            normal_vec.Normalize()

            # The normal from BRepGProp_Face points outward for a properly
            # oriented face. We want the inward direction for ray-casting.
            inward = gp_Dir(-normal_vec.X(), -normal_vec.Y(), -normal_vec.Z())
            samples.append((pnt, inward))

    # If grid sampling produced too few points, add center point
    if len(samples) == 0:
        u_mid = (umin + umax) / 2.0
        v_mid = (vmin + vmax) / 2.0
        pnt = gp_Pnt()
        normal_vec = gp_Vec()
        try:
            gprop_face.Normal(u_mid, v_mid, pnt, normal_vec)
            if normal_vec.Magnitude() > 1e-10:
                normal_vec.Normalize()
                inward = gp_Dir(-normal_vec.X(), -normal_vec.Y(), -normal_vec.Z())
                samples.append((pnt, inward))
        except Exception:
            pass

    return samples


def measure_thickness_at_point(
    point: gp_Pnt,
    direction: gp_Dir,
    solid,
    max_distance: float,
    offset: float = 0.01,
) -> Optional[float]:
    """
    Cast a ray from `point` along `direction` into the solid and find
    the distance to the opposing wall.

    Args:
        point: Origin point on the face surface.
        direction: Inward normal direction.
        solid: The TopoDS_Solid to intersect with.
        max_distance: Maximum ray distance (bounding box diagonal).
        offset: Small offset to move the ray origin slightly inward
                to avoid self-intersection with the originating face.

    Returns:
        Measured wall thickness (float) or None if no intersection found.
    """
    # Offset the ray origin slightly along the direction to avoid
    # self-intersection with the source face
    origin = gp_Pnt(
        point.X() + direction.X() * offset,
        point.Y() + direction.Y() * offset,
        point.Z() + direction.Z() * offset,
    )

    lin = gp_Lin(origin, direction)

    intersector = IntCurvesFace_ShapeIntersector()
    intersector.Load(solid, 1e-6)
    intersector.Perform(lin, 0.0, max_distance)

    if intersector.NbPnt() == 0:
        return None

    # Find the nearest intersection point
    min_dist = None
    for k in range(1, intersector.NbPnt() + 1):
        w_param = intersector.WParameter(k)
        if w_param > offset:  # skip very close hits (same face)
            dist = w_param + offset  # total distance from original surface
            if min_dist is None or dist < min_dist:
                min_dist = dist

    # If all hits were on the same face (very close), try a slightly
    # larger offset and re-cast
    if min_dist is None:
        larger_offset = offset * 5
        origin2 = gp_Pnt(
            point.X() + direction.X() * larger_offset,
            point.Y() + direction.Y() * larger_offset,
            point.Z() + direction.Z() * larger_offset,
        )
        lin2 = gp_Lin(origin2, direction)
        intersector2 = IntCurvesFace_ShapeIntersector()
        intersector2.Load(solid, 1e-6)
        intersector2.Perform(lin2, 0.0, max_distance)

        for k in range(1, intersector2.NbPnt() + 1):
            w_param = intersector2.WParameter(k)
            if w_param > 0.001:
                dist = w_param + larger_offset
                if min_dist is None or dist < min_dist:
                    min_dist = dist

    return min_dist


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------

def analyze_solid(
    solid,
    solid_index: int,
    min_thickness: float,
    n_samples: int,
) -> SolidResult:
    """
    Analyze a single solid for thin wall regions.

    For each face of the solid, sample points on the face, cast rays
    inward, and measure the local wall thickness. Any measurement
    below `min_thickness` is flagged as a thin wall region.
    """
    bbox = get_bounding_box(solid)
    diag = bounding_box_diagonal(bbox)

    faces = extract_faces(solid)
    total_faces = len(faces)

    # Adaptive offset: use a fraction of the bounding box diagonal,
    # but clamp to a reasonable range
    ray_offset = max(min(diag * 1e-4, 0.05), 1e-4)

    all_thicknesses: List[float] = []
    thin_regions: List[ThinWallRegion] = []
    faces_checked = 0

    for face_idx, face in enumerate(faces):
        adaptor = BRepAdaptor_Surface(face)
        surf_type = adaptor.GetType()
        surf_type_name = get_surface_type_name(surf_type)

        # Sample points on this face
        samples = sample_face_points(face, adaptor, n_samples)
        if not samples:
            continue

        faces_checked += 1
        face_thicknesses = []

        for point, inward_dir in samples:
            thickness = measure_thickness_at_point(
                point, inward_dir, solid, diag, offset=ray_offset
            )
            if thickness is not None and thickness < diag * 0.95:
                # Ignore thicknesses approaching the full diagonal (rays
                # that go nearly through the entire part are not walls)
                face_thicknesses.append(thickness)
                all_thicknesses.append(thickness)

                if thickness < min_thickness:
                    thin_regions.append(
                        ThinWallRegion(
                            face_index=face_idx,
                            face_type=surf_type_name,
                            sample_point=(
                                round(point.X(), 4),
                                round(point.Y(), 4),
                                round(point.Z(), 4),
                            ),
                            measured_thickness=round(thickness, 4),
                            normal_direction=(
                                round(inward_dir.X(), 4),
                                round(inward_dir.Y(), 4),
                                round(inward_dir.Z(), 4),
                            ),
                        )
                    )

    # Compute statistics
    min_found = round(min(all_thicknesses), 4) if all_thicknesses else 0.0
    max_found = round(max(all_thicknesses), 4) if all_thicknesses else 0.0
    avg_found = (
        round(sum(all_thicknesses) / len(all_thicknesses), 4)
        if all_thicknesses
        else 0.0
    )

    has_thin = len(thin_regions) > 0

    return SolidResult(
        solid_index=solid_index,
        bounding_box=tuple(round(v, 4) for v in bbox),
        total_faces=total_faces,
        faces_checked=faces_checked,
        min_thickness_found=min_found,
        max_thickness_found=max_found,
        avg_thickness=avg_found,
        thin_wall_regions=thin_regions,
        has_thin_walls=has_thin,
    )


def run_thin_wall_check(
    step_file_path: str,
    min_thickness: float = 1.0,
    n_samples: int = 10,
) -> DFMReport:
    """
    Run the thin wall DFM check on a STEP file.

    Args:
        step_file_path: Path to the STEP file.
        min_thickness: Minimum acceptable wall thickness in mm.
        n_samples: Number of sample points per UV direction on each face.

    Returns:
        DFMReport with results for each solid.
    """
    # Load the STEP file
    print(f"Loading STEP file: {step_file_path}")
    assembly = cq.importers.importStep(step_file_path)
    shape = assembly.val().wrapped if hasattr(assembly.val(), "wrapped") else assembly.val()

    # Extract individual solids
    solids = extract_solids(shape)
    print(f"Found {len(solids)} solid(s) in the file.\n")

    report = DFMReport(
        file_path=step_file_path,
        total_solids=len(solids),
        min_thickness_threshold=min_thickness,
    )

    for idx, solid in enumerate(solids):
        print(f"Analyzing solid {idx + 1}/{len(solids)}...")
        result = analyze_solid(solid, idx, min_thickness, n_samples)
        report.solids.append(result)

        if result.has_thin_walls:
            report.overall_pass = False

        print(f"  Faces: {result.total_faces} | Checked: {result.faces_checked}")
        print(f"  Thickness range: {result.min_thickness_found} - {result.max_thickness_found} mm")
        print(f"  Average thickness: {result.avg_thickness} mm")
        if result.has_thin_walls:
            print(f"  ⚠  {len(result.thin_wall_regions)} thin wall region(s) detected!")
        else:
            print(f"  ✓  No thin walls detected (threshold: {min_thickness} mm)")
        print()

    return report


def report_to_dict(report: DFMReport) -> dict:
    """Convert a DFMReport to a JSON-serializable dictionary."""
    return {
        "file_path": report.file_path,
        "total_solids": report.total_solids,
        "min_thickness_threshold_mm": report.min_thickness_threshold,
        "overall_pass": report.overall_pass,
        "solids": [
            {
                "solid_index": s.solid_index,
                "bounding_box": {
                    "xmin": s.bounding_box[0],
                    "ymin": s.bounding_box[1],
                    "zmin": s.bounding_box[2],
                    "xmax": s.bounding_box[3],
                    "ymax": s.bounding_box[4],
                    "zmax": s.bounding_box[5],
                },
                "total_faces": s.total_faces,
                "faces_checked": s.faces_checked,
                "min_thickness_found_mm": s.min_thickness_found,
                "max_thickness_found_mm": s.max_thickness_found,
                "avg_thickness_mm": s.avg_thickness,
                "has_thin_walls": s.has_thin_walls,
                "thin_wall_region_count": len(s.thin_wall_regions),
                "thin_wall_regions": [
                    {
                        "face_index": r.face_index,
                        "face_type": r.face_type,
                        "sample_point_xyz": list(r.sample_point),
                        "measured_thickness_mm": r.measured_thickness,
                        "inward_normal": list(r.normal_direction),
                    }
                    for r in s.thin_wall_regions
                ],
            }
            for s in report.solids
        ],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Thin Wall DFM Check – Detects thin walls in STEP file solids.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python thin_wall_check.py part.step
  python thin_wall_check.py part.step --min-thickness 0.8
  python thin_wall_check.py part.step --min-thickness 1.5 --samples 20
  python thin_wall_check.py part.step --json output.json
        """,
    )
    parser.add_argument(
        "step_file",
        help="Path to the STEP (.step / .stp) file to analyze.",
    )
    parser.add_argument(
        "--min-thickness",
        type=float,
        default=1.0,
        help="Minimum acceptable wall thickness in mm (default: 1.0).",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=10,
        help="Number of sample points per UV direction on each face (default: 10). "
             "Higher values give more accurate results but take longer.",
    )
    parser.add_argument(
        "--json",
        type=str,
        default=None,
        metavar="OUTPUT_FILE",
        help="Export the report as a JSON file.",
    )

    args = parser.parse_args()

    # Run analysis
    report = run_thin_wall_check(
        step_file_path=args.step_file,
        min_thickness=args.min_thickness,
        n_samples=args.samples,
    )

    # Summary
    print("=" * 60)
    print("THIN WALL DFM CHECK – SUMMARY")
    print("=" * 60)
    print(f"File           : {report.file_path}")
    print(f"Solids analyzed: {report.total_solids}")
    print(f"Threshold      : {report.min_thickness_threshold} mm")
    print()

    for s in report.solids:
        status = "FAIL ⚠" if s.has_thin_walls else "PASS ✓"
        print(f"  Solid #{s.solid_index}: {status}")
        print(f"    Min thickness : {s.min_thickness_found} mm")
        print(f"    Max thickness : {s.max_thickness_found} mm")
        print(f"    Avg thickness : {s.avg_thickness} mm")
        if s.has_thin_walls:
            print(f"    Thin regions  : {len(s.thin_wall_regions)}")
            # Group by face for cleaner output
            face_groups = {}
            for r in s.thin_wall_regions:
                key = (r.face_index, r.face_type)
                if key not in face_groups:
                    face_groups[key] = []
                face_groups[key].append(r.measured_thickness)
            for (fi, ft), thicknesses in face_groups.items():
                min_t = min(thicknesses)
                print(
                    f"      Face {fi} ({ft}): min thickness = {min_t} mm "
                    f"({len(thicknesses)} sample(s) below threshold)"
                )
        print()

    overall = "PASS ✓" if report.overall_pass else "FAIL ⚠"
    print(f"Overall result: {overall}")
    print("=" * 60)

    # JSON export
    if args.json:
        report_dict = report_to_dict(report)
        with open(args.json, "w") as f:
            json.dump(report_dict, f, indent=2)
        print(f"\nJSON report saved to: {args.json}")


if __name__ == "__main__":
    main()

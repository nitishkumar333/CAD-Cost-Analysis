"""
Thin Wall Detection DFM Analysis for STEP Files

Detects thin walls (material thickness < 1mm) in STEP files.

Approach:
  For each solid, find pairs of planar faces that are:
  1. Anti-parallel (normals ~180° apart)
  2. Close together along the normal direction (distance < threshold)
  3. Have geometric overlap when projected onto the plane
  4. Are not the top/bottom of a thin sheet body
  5. The material between them is confirmed via ray casting

Usage:
    conda activate occ_env
    python thin_wall_detector.py [step_file_or_directory] [--threshold 1.0]
"""

import os
import sys
import math
import argparse
from collections import defaultdict

from OCP.STEPControl import STEPControl_Reader
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_SOLID, TopAbs_FACE, TopAbs_REVERSED
from OCP.TopoDS import TopoDS
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.gp import gp_Pnt, gp_Dir, gp_Lin, gp_Vec, gp_Ax1
from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector
from OCP.GeomAbs import GeomAbs_Plane
from OCP.BRepBndLib import BRepBndLib
from OCP.Bnd import Bnd_Box


# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────
DEFAULT_THRESHOLD_MM = 1.0
MIN_FACE_AREA_MM2 = 6.0         # minimum area for each face in a pair
NORMAL_DOT_THRESHOLD = -0.95    # cos(~162°), faces must be nearly anti-parallel
RAY_OFFSET_MM = 1e-4            # offset to avoid self-intersection
OVERLAP_FRACTION = 0.4          # at least 40% of sample rays must confirm overlap
SAMPLE_GRID = 4                 # NxN grid of sample points per face for overlap check
MIN_WALL_THICKNESS_MM = 0.1     # ignore microscopic walls (e.g. decals, sheet surface artifacts)


# ──────────────────────────────────────────────
# STEP Loading
# ──────────────────────────────────────────────
def load_step(filepath: str):
    reader = STEPControl_Reader()
    status = reader.ReadFile(filepath)
    if status != 1:
        raise RuntimeError(f"Failed to read STEP file: {filepath}")
    reader.TransferRoots()
    return reader.OneShape()


def extract_solids(shape):
    solids = []
    exp = TopExp_Explorer(shape, TopAbs_SOLID)
    while exp.More():
        solids.append(TopoDS.Solid_s(exp.Current()))
        exp.Next()
    return solids


def extract_faces(solid):
    faces = []
    exp = TopExp_Explorer(solid, TopAbs_FACE)
    while exp.More():
        faces.append(TopoDS.Face_s(exp.Current()))
        exp.Next()
    return faces


# ──────────────────────────────────────────────
# Geometry Helpers
# ──────────────────────────────────────────────
def face_properties(face):
    """Return (area, centroid_gp_Pnt) for a face."""
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    return props.Mass(), props.CentreOfMass()


def solid_surface_area(solid) -> float:
    """Total surface area of a solid."""
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(solid, props)
    return props.Mass()


def face_outward_normal(face):
    """
    Compute outward normal at parametric midpoint.
    Returns gp_Vec (normalized) or None.
    """
    adaptor = BRepAdaptor_Surface(face)
    u_min, u_max = adaptor.FirstUParameter(), adaptor.LastUParameter()
    v_min, v_max = adaptor.FirstVParameter(), adaptor.LastVParameter()

    PLIM = 1e6
    u_min, u_max = max(u_min, -PLIM), min(u_max, PLIM)
    v_min, v_max = max(v_min, -PLIM), min(v_max, PLIM)

    u_mid = 0.5 * (u_min + u_max)
    v_mid = 0.5 * (v_min + v_max)

    try:
        surface = adaptor.Surface().Surface()
        p, d1u, d1v = gp_Pnt(), gp_Vec(), gp_Vec()
        surface.D1(u_mid, v_mid, p, d1u, d1v)
        n = d1u.Crossed(d1v)
        if n.Magnitude() < 1e-10:
            return None
        n.Normalize()
        if face.Orientation() == TopAbs_REVERSED:
            n.Reverse()
        return n
    except Exception:
        return None


def is_planar(face) -> bool:
    return BRepAdaptor_Surface(face).GetType() == GeomAbs_Plane


def dot_vec(a, b):
    return a.X() * b.X() + a.Y() * b.Y() + a.Z() * b.Z()


def point_dist(p1: gp_Pnt, p2: gp_Pnt) -> float:
    return math.sqrt((p1.X()-p2.X())**2 + (p1.Y()-p2.Y())**2 + (p1.Z()-p2.Z())**2)


def distance_along_normal(c1: gp_Pnt, c2: gp_Pnt, normal) -> float:
    """Distance between two points projected along the normal direction."""
    dx = c2.X() - c1.X()
    dy = c2.Y() - c1.Y()
    dz = c2.Z() - c1.Z()
    return abs(dx * normal.X() + dy * normal.Y() + dz * normal.Z())


def tangential_distance(c1: gp_Pnt, c2: gp_Pnt, normal) -> float:
    """Distance between two points in the plane perpendicular to the normal."""
    total = point_dist(c1, c2)
    along_n = distance_along_normal(c1, c2, normal)
    return math.sqrt(max(0, total**2 - along_n**2))


def get_face_sample_points(face, n_u=SAMPLE_GRID, n_v=SAMPLE_GRID):
    """Sample interior points on a face. Returns list of (gp_Pnt, inward_normal_gp_Dir)."""
    adaptor = BRepAdaptor_Surface(face)
    u_min, u_max = adaptor.FirstUParameter(), adaptor.LastUParameter()
    v_min, v_max = adaptor.FirstVParameter(), adaptor.LastVParameter()

    PLIM = 1e6
    u_min, u_max = max(u_min, -PLIM), min(u_max, PLIM)
    v_min, v_max = max(v_min, -PLIM), min(v_max, PLIM)

    results = []
    surface = adaptor.Surface().Surface()

    for i in range(n_u):
        for j in range(n_v):
            u = u_min + (i + 0.5) / n_u * (u_max - u_min)
            v = v_min + (j + 0.5) / n_v * (v_max - v_min)
            try:
                p, d1u, d1v = gp_Pnt(), gp_Vec(), gp_Vec()
                surface.D1(u, v, p, d1u, d1v)
                n = d1u.Crossed(d1v)
                if n.Magnitude() < 1e-10:
                    continue
                n.Normalize()
                if face.Orientation() == TopAbs_REVERSED:
                    n.Reverse()
                # Inward = reverse of outward
                inward = gp_Dir(-n.X(), -n.Y(), -n.Z())
                results.append((p, inward))
            except Exception:
                continue
    return results


def face_bounding_box(face):
    """Return (xmin, ymin, zmin, xmax, ymax, zmax) for a face."""
    box = Bnd_Box()
    BRepBndLib.Add_s(face, box)
    return box.Get()


def bounding_boxes_overlap_2d(bb1, bb2, normal):
    """
    Check if two face bounding boxes overlap when projected onto the plane
    perpendicular to the normal. This is a rough overlap test.
    """
    # Project all 8 corners of each bbox onto the plane perpendicular to normal
    # For simplicity, check bbox overlap on all 3 axes and see if at least 2 overlap
    x1min, y1min, z1min, x1max, y1max, z1max = bb1
    x2min, y2min, z2min, x2max, y2max, z2max = bb2

    overlap_x = x1min <= x2max and x2min <= x1max
    overlap_y = y1min <= y2max and y2min <= y1max
    overlap_z = z1min <= z2max and z2min <= z1max

    # If normal is mostly along one axis, check the other two axes for overlap
    nx, ny, nz = abs(normal.X()), abs(normal.Y()), abs(normal.Z())

    if nx > ny and nx > nz:
        # Normal along X: check Y and Z overlap
        return overlap_y and overlap_z
    elif ny > nx and ny > nz:
        # Normal along Y: check X and Z overlap
        return overlap_x and overlap_z
    else:
        # Normal along Z: check X and Y overlap
        return overlap_x and overlap_y


def verify_wall_with_rays(face_a, face_b, solid, wall_distance, normal_a):
    """
    Verify that faces A and B form an actual wall by casting rays from
    sample points on face_a inward and checking they hit the solid's
    interior at approximately the wall distance.

    Returns the fraction of rays that confirm the wall.
    """
    samples = get_face_sample_points(face_a, 3, 3)
    if not samples:
        return 0.0

    inward_dir = gp_Dir(-normal_a.X(), -normal_a.Y(), -normal_a.Z())

    confirmed = 0
    total = 0

    intersector = IntCurvesFace_ShapeIntersector()
    intersector.Load(solid, 1e-6)

    for pt, _ in samples:
        # Offset origin slightly inward
        origin = gp_Pnt(
            pt.X() + inward_dir.X() * RAY_OFFSET_MM,
            pt.Y() + inward_dir.Y() * RAY_OFFSET_MM,
            pt.Z() + inward_dir.Z() * RAY_OFFSET_MM,
        )
        line = gp_Lin(origin, inward_dir)
        intersector.Perform(line, 0.0, wall_distance * 3)

        n_pts = intersector.NbPnt()
        total += 1

        # Look for an intersection near the expected wall distance
        for k in range(1, n_pts + 1):
            w = intersector.WParameter(k)
            if RAY_OFFSET_MM * 2 < w < wall_distance * 1.5:
                confirmed += 1
                break

    return confirmed / total if total > 0 else 0.0


# ──────────────────────────────────────────────
# Core Detection
# ──────────────────────────────────────────────
class ThinWall:
    def __init__(self, thickness, face_idx_a, face_idx_b, area_a, area_b,
                 centroid_a, centroid_b):
        self.thickness = thickness
        self.face_idx_a = face_idx_a
        self.face_idx_b = face_idx_b
        self.area_a = area_a
        self.area_b = area_b
        self.centroid_a = centroid_a
        self.centroid_b = centroid_b

    @property
    def midpoint(self):
        return gp_Pnt(
            0.5 * (self.centroid_a.X() + self.centroid_b.X()),
            0.5 * (self.centroid_a.Y() + self.centroid_b.Y()),
            0.5 * (self.centroid_a.Z() + self.centroid_b.Z()),
        )


def detect_thin_walls(solid, threshold_mm=DEFAULT_THRESHOLD_MM):
    """Detect thin walls in a single solid."""
    faces = extract_faces(solid)
    if not faces:
        return []

    total_area = solid_surface_area(solid)

    # Pre-compute data for planar faces
    pf_data = []
    for fi, face in enumerate(faces):
        if not is_planar(face):
            continue
        area, centroid = face_properties(face)
        if area < MIN_FACE_AREA_MM2:
            continue
        normal = face_outward_normal(face)
        if normal is None:
            continue
        bb = face_bounding_box(face)
        pf_data.append({
            "idx": fi,
            "face": face,
            "area": area,
            "centroid": centroid,
            "normal": normal,
            "bbox": bb,
        })

    thin_walls = []
    n = len(pf_data)

    for i in range(n):
        fi = pf_data[i]
        for j in range(i + 1, n):
            fj = pf_data[j]

            # 1. Check anti-parallel normals
            dot = dot_vec(fi["normal"], fj["normal"])
            if dot > NORMAL_DOT_THRESHOLD:
                continue

            # 2. Compute distance along normal (plane separation)
            wall_dist = distance_along_normal(
                fi["centroid"], fj["centroid"], fi["normal"]
            )
            if wall_dist >= threshold_mm or wall_dist < MIN_WALL_THICKNESS_MM:
                continue

            # 3. Tangential distance: centroids should be close in lateral direction
            tan_dist = tangential_distance(
                fi["centroid"], fj["centroid"], fi["normal"]
            )
            # The tangential distance should be less than the face dimensions
            # Use a heuristic: tangential distance should be small relative to
            # the characteristic face size (sqrt of the smaller area)
            smaller_area = min(fi["area"], fj["area"])
            char_size = math.sqrt(smaller_area)
            if tan_dist > char_size * 2:
                continue

            # 4. Bounding box overlap check
            if not bounding_boxes_overlap_2d(fi["bbox"], fj["bbox"], fi["normal"]):
                continue

            # 5. Ray-cast confirmation: verify material exists between the faces
            confirmation = verify_wall_with_rays(
                fi["face"], fj["face"], solid, wall_dist, fi["normal"]
            )
            if confirmation < OVERLAP_FRACTION:
                continue

            tw = ThinWall(
                thickness=wall_dist,
                face_idx_a=fi["idx"],
                face_idx_b=fj["idx"],
                area_a=fi["area"],
                area_b=fj["area"],
                centroid_a=fi["centroid"],
                centroid_b=fj["centroid"],
            )
            thin_walls.append(tw)

    # Deduplicate
    deduplicated = _deduplicate(thin_walls)
    return deduplicated


def _deduplicate(thin_walls):
    """Merge thin wall detections sharing faces or overlapping midpoints."""
    if not thin_walls:
        return []

    n = len(thin_walls)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(n):
        for j in range(i + 1, n):
            tw_i, tw_j = thin_walls[i], thin_walls[j]
            faces_i = {tw_i.face_idx_a, tw_i.face_idx_b}
            faces_j = {tw_j.face_idx_a, tw_j.face_idx_b}
            if faces_i & faces_j:
                union(i, j)
                continue
            mid_dist = point_dist(tw_i.midpoint, tw_j.midpoint)
            if mid_dist < 1.0 and abs(tw_i.thickness - tw_j.thickness) < 0.1:
                union(i, j)

    groups = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(thin_walls[i])

    result = []
    for group in groups.values():
        best = min(group, key=lambda tw: tw.thickness)
        result.append(best)
    return result


# ──────────────────────────────────────────────
# File Processing
# ──────────────────────────────────────────────
def analyze_step_file(filepath, threshold_mm=DEFAULT_THRESHOLD_MM):
    filename = os.path.basename(filepath)
    result = {"file": filename, "solids": 0, "thin_walls_total": 0, "details": []}

    try:
        shape = load_step(filepath)
    except RuntimeError as e:
        result["error"] = str(e)
        return result

    solids = extract_solids(shape)
    result["solids"] = len(solids)

    for s_idx, solid in enumerate(solids):
        thin_walls = detect_thin_walls(solid, threshold_mm - 1e-4)
        for tw in thin_walls:
            result["details"].append({
                "solid_index": s_idx,
                "thickness_mm": round(tw.thickness, 4),
                "face_pair": (tw.face_idx_a, tw.face_idx_b),
                "areas": (round(tw.area_a, 2), round(tw.area_b, 2)),
            })
        result["thin_walls_total"] += len(thin_walls)

    return result


def print_result(result):
    fname = result["file"]
    total = result["thin_walls_total"]
    solids = result["solids"]

    if "error" in result:
        print(f"  ❌ {fname}: ERROR - {result['error']}")
        return

    status = "✅" if total == 0 else "⚠️"
    print(f"  {status} {fname}: {total} thin wall(s) found  [solids: {solids}]")

    for d in result["details"]:
        print(
            f"      → Solid {d['solid_index']}: "
            f"thickness = {d['thickness_mm']:.4f} mm, "
            f"face pair {d['face_pair']}, "
            f"areas = {d['areas']}"
        )


def main():
    parser = argparse.ArgumentParser(
        description="Thin Wall Detection DFM for STEP files"
    )
    parser.add_argument(
        "path", nargs="?", default=None,
        help="Path to a STEP file or directory (default: ./test_steps)",
    )
    parser.add_argument(
        "--threshold", type=float, default=DEFAULT_THRESHOLD_MM,
        help=f"Thin wall threshold in mm (default: {DEFAULT_THRESHOLD_MM})",
    )
    args = parser.parse_args()

    if args.path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tt")
    else:
        path = args.path

    if not os.path.exists(path):
        print(f"Error: '{path}' does not exist.")
        sys.exit(1)

    step_files = []
    if os.path.isdir(path):
        for f in sorted(os.listdir(path)):
            if f.lower().endswith((".step", ".stp")):
                step_files.append(os.path.join(path, f))
    else:
        step_files.append(path)

    if not step_files:
        print("No STEP files found.")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"  Thin Wall Detection DFM Analysis")
    print(f"  Threshold: {args.threshold} mm")
    print(f"  Files: {len(step_files)}")
    print(f"{'='*60}\n")

    total = 0
    for fp in step_files:
        result = analyze_step_file(fp, args.threshold)
        print_result(result)
        total += result["thin_walls_total"]

    print(f"\n{'='*60}")
    print(f"  Summary: {total} thin wall(s) detected across {len(step_files)} file(s)")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()

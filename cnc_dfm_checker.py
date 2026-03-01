"""
DFM (Design for Manufacturability) Checker for STEP/STP CAD Files
=================================================================
Performs the following checks:
  1.  Confined Hollow
  2.  Floating Parts Check
  3.  Large Part for Material
  4.  Material Thickness
  5.  Model Fidelity
  6.  Model Shell Count
  7.  Part Exceeds Maximum Size for Finish
  8. Small Holes
  9. Assembled Part

Dependencies:
    pip install pythonocc-core numpy

Usage:
    python dfm_checker.py <path_to_step_file> [--config config.json]
"""

import sys
import os
import yaml
import math
import argparse
import logging
from dataclasses import dataclass, field, asdict
from typing import Tuple
from enum import Enum

# ── OCC imports (pythonocc-core) ──────────────────────────────────────────────
from OCC.Core.STEPControl import STEPControl_Reader
from OCC.Core.IFSelect import IFSelect_RetDone
from OCC.Core.BRep import BRep_Builder, BRep_Tool
from OCC.Core.BRepGProp import brepgprop_SurfaceProperties, brepgprop
from OCC.Core.GProp import GProp_GProps
from OCC.Core.Bnd import Bnd_Box
from OCC.Core.BRepBndLib import brepbndlib
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import (
    TopAbs_SHELL, TopAbs_SOLID, TopAbs_FACE,
    TopAbs_EDGE, TopAbs_VERTEX, TopAbs_COMPOUND,
    TopAbs_WIRE, TopAbs_REVERSED, TopAbs_FORWARD
)
# ── FIX: import topods for safe downcasting ───────────────────────────────────
from OCC.Core.TopoDS import (
    TopoDS_Shell, TopoDS_Solid, TopoDS_Face, TopoDS_Edge,
    TopoDS_Wire, TopoDS_Compound, topods
)
from OCC.Core.BRepCheck import BRepCheck_Analyzer
from OCC.Core.ShapeAnalysis import ShapeAnalysis_Shell
from OCC.Core.BRepClass3d import BRepClass3d_SolidClassifier
from OCC.Core.gp import gp_Pnt, gp_Vec, gp_Dir, gp_Ax1
from OCC.Extend.TopologyUtils import TopologyExplorer
from OCC.Core.BRepAdaptor import BRepAdaptor_Surface, BRepAdaptor_Curve
from OCC.Core.GeomAbs import (
    GeomAbs_Cylinder, GeomAbs_Plane, GeomAbs_Circle,
    GeomAbs_Line, GeomAbs_BSplineSurface, GeomAbs_Cone
)
from OCC.Core.GeomLProp import GeomLProp_SLProps
from OCC.Core.BRepTools import breptools_UVBounds, breptools
from OCC.Core.TopExp import topexp_MapShapesAndAncestors, topexp_MapShapes
from OCC.Core.TopTools import TopTools_IndexedDataMapOfShapeListOfShape, TopTools_IndexedMapOfShape

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


# ── Configuration ─────────────────────────────────────────────────────────────
DEFAULT_CONFIG = {
    # Material / process defaults (all units in mm unless noted)
    "material": "aluminium",
    "finish": "anodizing",

    # Thresholds — original checks
    "min_wall_thickness_mm": 0.8,          # below → Material Thickness fail
    "max_part_volume_cm3": 30_000,          # above → Large Part for Material fail
    "max_finish_size_mm": [600, 400, 300],  # [X, Y, Z] bounding box limits
    "fidelity_min_faces": 4,               # below → Model Fidelity fail (degenerate)
    "fidelity_edge_tolerance_mm": 0.01,    # gap / overlap tolerance
    "max_shells": 50,                       # above → Model Shell Count warning
    "min_hole_diameter_mm": 1.0,            # smallest standard drill bit
}


# ── Result structures ─────────────────────────────────────────────────────────
class Severity(str, Enum):
    PASS = "PASS"
    WARNING = "WARNING"
    FAIL = "FAIL"
    ERROR = "ERROR"
    SKIP = "SKIP"


@dataclass
class CheckResult:
    name: str
    severity: Severity
    message: str
    details: dict = field(default_factory=dict)


@dataclass
class DFMReport:
    file: str
    results: list = field(default_factory=list)

    def add(self, result: CheckResult):
        self.results.append(result)

    def print_summary(self):
        print("\n" + "=" * 65)
        print(f"  DFM Report  →  {os.path.basename(self.file)}")
        print("=" * 65)
        for r in self.results:
            icon = {"PASS": "✅", "WARNING": "⚠️ ", "FAIL": "❌", "ERROR": "💥", "SKIP": "⏭️ "}.get(r.severity, " ")
            print(f"  {icon}  [{r.severity:<7}]  {r.name}")
            print(f"           {r.message}")
            if r.details:
                for k, v in r.details.items():
                    print(f"             • {k}: {v}")
        print("=" * 65)
        fails = sum(1 for r in self.results if r.severity == Severity.FAIL)
        warns = sum(1 for r in self.results if r.severity == Severity.WARNING)
        print(f"  Total: {len(self.results)} checks | {fails} failures | {warns} warnings")
        print("=" * 65 + "\n")

    def to_json(self) -> str:
        data = {"file": self.file, "results": [asdict(r) for r in self.results]}
        return yaml.dump(data, sort_keys=False)


# ── STEP loader ───────────────────────────────────────────────────────────────
def load_step(filepath: str):
    """Load a STEP file and return the root TopoDS_Shape."""
    reader = STEPControl_Reader()
    status = reader.ReadFile(filepath)
    if status != IFSelect_RetDone:
        raise RuntimeError(f"Failed to read STEP file: {filepath}")
    reader.TransferRoots()
    shape = reader.OneShape()
    if shape.IsNull():
        raise RuntimeError("STEP file loaded but shape is null.")
    return shape


# ── Geometry helpers ──────────────────────────────────────────────────────────
def get_bounding_box(shape):
    """Return (xmin, ymin, zmin, xmax, ymax, zmax) in mm."""
    bbox = Bnd_Box()
    brepbndlib.Add(shape, bbox)
    return bbox.Get()  # xmin, ymin, zmin, xmax, ymax, zmax


def get_volume_cm3(shape) -> float:
    props = GProp_GProps()
    brepgprop.VolumeProperties(shape, props)
    return props.Mass() / 1000.0  # mm³ → cm³


def get_surface_area_mm2(shape) -> float:
    props = GProp_GProps()
    brepgprop_SurfaceProperties(shape, props)
    return props.Mass()


def get_shells(shape) -> list:
    """Return all shells as properly downcast TopoDS_Shell objects."""
    exp = TopExp_Explorer(shape, TopAbs_SHELL)
    shells = []
    while exp.More():
        # ── FIX: use topods.Shell() instead of TopoDS_Shell(exp.Current()) ──
        shells.append(topods.Shell(exp.Current()))
        exp.Next()
    return shells


def get_solids(shape) -> list:
    """Return all solids as properly downcast TopoDS_Solid objects."""
    exp = TopExp_Explorer(shape, TopAbs_SOLID)
    solids = []
    while exp.More():
        # ── FIX: use topods.Solid() instead of TopoDS_Solid(exp.Current()) ──
        solids.append(topods.Solid(exp.Current()))
        exp.Next()
    return solids


def count_faces(shape) -> int:
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    n = 0
    while exp.More():
        n += 1
        exp.Next()
    return n


# ── Individual DFM Checks ─────────────────────────────────────────────────────

def check_confined_hollow(shape, cfg: dict) -> CheckResult:
    """
    Detect internal hollow cavities that have small or no openings.
    Heuristic: compare closed-shell volume vs open-shell surface geometry.
    A more rigorous approach would use ray-casting or boolean subtraction.
    """
    name = "Confined Hollow"
    try:
        solids = get_solids(shape)
        if not solids:
            return CheckResult(name, Severity.SKIP, "No solids found – cannot analyse hollows.")

        hollow_count = 0
        for solid in solids:
            texp = TopologyExplorer(solid)
            shells = list(texp.shells())
            # A solid with >1 shell likely has an internal void
            if len(shells) > 1:
                hollow_count += 1

        if hollow_count:
            return CheckResult(
                name, Severity.FAIL,
                f"{hollow_count} solid(s) contain internal hollow cavities with limited access.",
                {"confined_hollows": hollow_count,
                 "advice": "Ensure machining/printing access; add drain/vent holes if needed."}
            )
        return CheckResult(name, Severity.PASS, "No confined hollows detected.")
    except Exception as e:
        return CheckResult(name, Severity.ERROR, f"Check failed: {e}")


def check_floating_parts(shape, cfg: dict) -> CheckResult:
    """
    Detect disconnected / floating sub-bodies within the assembly.
    Uses connected-components logic on the top-level shape.
    """
    name = "Floating Parts Check"
    try:
        solids = get_solids(shape)

        # If the root is a compound with multiple disjoint solids → floating risk
        if len(solids) > 1:
            # Simple heuristic: check if bounding boxes overlap
            floating = []
            boxes = []
            for s in solids:
                bb = Bnd_Box()
                brepbndlib.Add(s, bb)
                boxes.append(bb)

            for i, b in enumerate(boxes):
                overlapping = any(
                    not b.IsOut(boxes[j])
                    for j in range(len(boxes)) if j != i
                )
                if not overlapping:
                    floating.append(i)

            if floating:
                return CheckResult(
                    name, Severity.FAIL,
                    f"{len(floating)} solid(s) appear to be floating / disconnected.",
                    {"floating_solid_indices": floating,
                     "advice": "Ensure all parts are properly joined or assembled."}
                )
            return CheckResult(
                name, Severity.WARNING,
                f"Multiple solids ({len(solids)}) found in file. Verify intentional assembly.",
                {"solid_count": len(solids)}
            )
        return CheckResult(name, Severity.PASS, "Single solid body – no floating parts.")
    except Exception as e:
        return CheckResult(name, Severity.ERROR, f"Check failed: {e}")


def check_large_part_for_material(shape, cfg: dict) -> CheckResult:
    """Volume-based large part check relative to chosen material."""
    name = "Large Part for Material"
    try:
        vol = get_volume_cm3(shape)
        limit = cfg["max_part_volume_cm3"]
        material = cfg.get("material", "unknown")
        if vol > limit:
            return CheckResult(
                name, Severity.FAIL,
                f"Part volume {vol:.1f} cm³ exceeds {limit} cm³ limit for {material}.",
                {"volume_cm3": round(vol, 2), "limit_cm3": limit, "material": material}
            )
        return CheckResult(
            name, Severity.PASS,
            f"Part volume {vol:.1f} cm³ is within limits for {material}.",
            {"volume_cm3": round(vol, 2)}
        )
    except Exception as e:
        return CheckResult(name, Severity.ERROR, f"Check failed: {e}")


def check_material_thickness(shape, cfg: dict) -> CheckResult:
    """
    Approximate minimum wall thickness using bounding box thin-dimension heuristic.
    For rigorous analysis, mesh-based ray-casting would be used.
    """
    name = "Material Thickness"
    try:
        xmin, ymin, zmin, xmax, ymax, zmax = get_bounding_box(shape)
        dims = sorted([xmax - xmin, ymax - ymin, zmax - zmin])
        estimated_min = dims[0]  # smallest bounding box dimension
        min_allowed = cfg["min_wall_thickness_mm"]

        if estimated_min < min_allowed:
            return CheckResult(
                name, Severity.FAIL,
                f"Estimated minimum thickness {estimated_min:.3f} mm is below minimum {min_allowed} mm.",
                {"estimated_min_mm": round(estimated_min, 3),
                 "minimum_allowed_mm": min_allowed,
                 "note": "This is a bounding-box heuristic; mesh ray-cast analysis recommended for precision."}
            )
        return CheckResult(
            name, Severity.PASS,
            f"Estimated minimum thickness {estimated_min:.3f} mm meets the {min_allowed} mm minimum.",
            {"estimated_min_mm": round(estimated_min, 3)}
        )
    except Exception as e:
        return CheckResult(name, Severity.ERROR, f"Check failed: {e}")


def check_model_fidelity(shape, cfg: dict) -> CheckResult:
    """
    Check geometric validity: BRep integrity, degenerate geometry, gaps, and open edges.
    """
    name = "Model Fidelity"
    issues = []
    try:
        # BRep validity
        analyzer = BRepCheck_Analyzer(shape)
        if not analyzer.IsValid():
            issues.append("BRep check failed – shape has geometric errors.")

        # Degenerate face count
        face_count = count_faces(shape)
        if face_count < cfg["fidelity_min_faces"]:
            issues.append(f"Very low face count ({face_count}) – possible degenerate model.")

        # Open shells (non-manifold)
        shells = get_shells(shape)
        shell_analysis = ShapeAnalysis_Shell()
        for shell in shells:
            shell_analysis.LoadShells(shell)
            if shell_analysis.HasFreeEdges():
                issues.append("Open / free edges detected on shell – non-watertight geometry.")
                break

        if issues:
            return CheckResult(
                name, Severity.FAIL,
                "Model fidelity issues found.",
                {"issues": issues, "face_count": face_count}
            )
        return CheckResult(
            name, Severity.PASS,
            f"Model passes fidelity checks ({face_count} faces, watertight shells).",
            {"face_count": face_count}
        )
    except Exception as e:
        return CheckResult(name, Severity.ERROR, f"Check failed: {e}")


def check_model_shell_count(shape, cfg: dict) -> CheckResult:
    """Count shells and warn if excessively high (complex or multi-body part)."""
    name = "Model Shell Count"
    try:
        shells = get_shells(shape)
        count = len(shells)
        max_shells = cfg["max_shells"]
        if count == 0:
            return CheckResult(name, Severity.WARNING, "No shells found – model may be empty or surface-only.")
        if count > max_shells:
            return CheckResult(
                name, Severity.WARNING,
                f"High shell count ({count}) may indicate overly complex or fragmented geometry.",
                {"shell_count": count, "max_recommended": max_shells}
            )
        return CheckResult(
            name, Severity.PASS,
            f"Shell count {count} is within acceptable range.",
            {"shell_count": count}
        )
    except Exception as e:
        return CheckResult(name, Severity.ERROR, f"Check failed: {e}")


def check_part_exceeds_max_size_for_finish(shape, cfg: dict) -> CheckResult:
    """
    Check if the part's bounding box exceeds the maximum process tank/chamber size
    for the chosen surface finish.
    """
    name = "Part Exceeds Maximum Size for Finish"
    try:
        xmin, ymin, zmin, xmax, ymax, zmax = get_bounding_box(shape)
        dims = sorted([xmax - xmin, ymax - ymin, zmax - zmin], reverse=True)
        limits = sorted(cfg["max_finish_size_mm"], reverse=True)
        finish = cfg.get("finish", "unknown")

        exceeded = [d > l for d, l in zip(dims, limits)]
        if any(exceeded):
            return CheckResult(
                name, Severity.FAIL,
                f"Part dimensions exceed maximum size allowed for '{finish}'.",
                {
                    "part_dims_mm": [round(d, 1) for d in dims],
                    "max_allowed_mm": limits,
                    "finish": finish
                }
            )
        return CheckResult(
            name, Severity.PASS,
            f"Part fits within maximum size envelope for '{finish}'.",
            {"part_dims_mm": [round(d, 1) for d in dims], "finish": finish}
        )
    except Exception as e:
        return CheckResult(name, Severity.ERROR, f"Check failed: {e}")


# ── New CNC-Specific DFM Checks ──────────────────────────────────────────────

def _get_faces(shape) -> list:
    """Return all TopoDS_Face objects from the shape."""
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    faces = []
    while exp.More():
        # ── FIX: use topods.Face() instead of TopoDS_Face(exp.Current()) ────
        faces.append(topods.Face(exp.Current()))
        exp.Next()
    return faces


def _get_edges(shape) -> list:
    """Return all TopoDS_Edge objects from the shape."""
    exp = TopExp_Explorer(shape, TopAbs_EDGE)
    edges = []
    while exp.More():
        edges.append(exp.Current())
        exp.Next()
    return edges


def _face_normal_at_center(face) -> Tuple[gp_Pnt, gp_Dir]:
    """Compute the outward-facing normal at the UV centre of a face."""
    surf = BRepAdaptor_Surface(face)
    umin, umax, vmin, vmax = breptools_UVBounds(face)
    umid = 0.5 * (umin + umax)
    vmid = 0.5 * (vmin + vmax)
    # Evaluate point and partial derivatives at UV center
    pnt = gp_Pnt()
    d1u = gp_Vec()
    d1v = gp_Vec()
    surf.D1(umid, vmid, pnt, d1u, d1v)
    # Cross product gives normal
    normal_vec = d1u.Crossed(d1v)
    if normal_vec.Magnitude() < 1e-10:
        # Fallback for degenerate parametrisation
        return pnt, gp_Dir(0, 0, 1)
    normal = gp_Dir(normal_vec)
    # Reverse normal if the face orientation is reversed
    if face.Orientation() == TopAbs_REVERSED:
        normal.Reverse()
    return pnt, normal


def _get_wires(face) -> list:
    """Return all TopoDS_Wire objects from a face."""
    wires = []
    exp = TopExp_Explorer(face, TopAbs_WIRE)
    while exp.More():
        wires.append(exp.Current())
        exp.Next()
    return wires


def _get_solids(shape) -> list:
    """Return all TopoDS_Solid objects from a shape."""
    return get_solids(shape)  # reuse the fixed helper above


def _is_redundant(new_center, new_radius, new_normal, stored_holes, tol=1e-4):
    """Return True if a hole with the same axis and radius already exists."""
    for hole in stored_holes:
        if abs(hole["radius"] - new_radius) > tol:
            continue
        old_center = hole["center"]
        old_normal = hole["normal"]
        vec = (
            new_center[0] - old_center[0],
            new_center[1] - old_center[1],
            new_center[2] - old_center[2],
        )
        dot = vec[0]*old_normal[0] + vec[1]*old_normal[1] + vec[2]*old_normal[2]
        projected = (
            old_center[0] + dot*old_normal[0],
            old_center[1] + dot*old_normal[1],
            old_center[2] + dot*old_normal[2],
        )
        if math.sqrt(sum((projected[i] - new_center[i])**2 for i in range(3))) < tol:
            return True
    return False


def check_small_holes(shape, cfg: dict) -> CheckResult:
    """
    Detect holes whose diameter is below the configured minimum.

    Strategy:
      • Collect all SOLIDs upfront into a list (avoid nested live explorers).
      • For each solid, collect all planar FACEs into a list.
      • For each planar face, collect all WIREs into a list, skip outer wire.
      • For each inner wire, collect all EDGEs into a list.
      • If every edge is GeomAbs_Circle → valid circular hole.
      • Deduplicate via _is_redundant(), then check diameter threshold.
    """
    name = "Small Holes"
    try:
        min_dia    = cfg["min_hole_diameter_mm"]
        all_holes  = []
        issues     = []

        # ── Collect solids first, never nest live explorers ──────────────
        solids = _get_solids(shape)
        log.info(f"Found {len(solids)} solid(s).")

        for solid in solids:
            detected_holes = []

            # Collect all faces of this solid upfront
            faces = _get_faces(solid)

            for face in faces:
                surf = BRepAdaptor_Surface(face)

                if surf.GetType() != GeomAbs_Plane:
                    continue

                plane      = surf.Plane()
                normal_dir = plane.Axis().Direction()
                normal_vec = (normal_dir.X(), normal_dir.Y(), normal_dir.Z())

                outer_wire = breptools.OuterWire(face)

                # Collect all wires of this face upfront
                wires = _get_wires(face)

                for wire in wires:
                    if wire.IsSame(outer_wire):
                        continue

                    # Collect all edges of this wire upfront
                    edges = _get_edges(wire)

                    is_circle     = True
                    circle_center = None
                    circle_radius = None

                    for edge in edges:
                        curve = BRepAdaptor_Curve(edge)
                        if curve.GetType() != GeomAbs_Circle:
                            is_circle = False
                            break
                        circ          = curve.Circle()
                        loc           = circ.Location()
                        circle_center = (loc.X(), loc.Y(), loc.Z())
                        circle_radius = circ.Radius()

                    if not (is_circle and circle_center is not None):
                        continue

                    if _is_redundant(circle_center, circle_radius,
                                     normal_vec, detected_holes):
                        continue

                    detected_holes.append({
                        "center": circle_center,
                        "radius": circle_radius,
                        "normal": normal_vec,
                    })

                    diameter = 2.0 * circle_radius
                    if diameter < min_dia:
                        issues.append({
                            "center_mm":   tuple(round(v, 3) for v in circle_center),
                            "diameter_mm": round(diameter, 3),
                            "normal":      tuple(round(v, 4) for v in normal_vec),
                            "problems":    [
                                f"diameter {diameter:.3f} mm < min {min_dia} mm"
                            ],
                        })

            all_holes.extend(detected_holes)

        total_holes = len(all_holes)
        log.info(f"Total unique holes detected: {total_holes}")

        if issues:
            return CheckResult(
                name, Severity.FAIL,
                f"{len(issues)} of {total_holes} hole(s) are below the minimum diameter.",
                {
                    "holes_with_issues": issues,
                    "total_holes_found": total_holes,
                    "min_diameter_mm":   min_dia,
                    "advice": (
                        "Increase hole diameter to meet the minimum. "
                        "Consider EDM or laser drilling for unavoidably small holes."
                    ),
                },
            )

        return CheckResult(
            name, Severity.PASS,
            f"All {total_holes} hole(s) meet the minimum diameter ({min_dia} mm).",
            {"total_holes_found": total_holes, "min_diameter_mm": min_dia},
        )

    except Exception as e:
        return CheckResult(name, Severity.ERROR, f"Check failed: {e}")


def check_assembled_part(shape, cfg: dict) -> CheckResult:
    """
    Detect if the STEP file represents a multi-body assembly rather than
    a single monolithic part.  Assemblies cannot be CNC-machined as one
    piece and indicate either an import error or a design issue.
    """
    name = "Assembled Part"
    try:
        solids = get_solids(shape)
        n_solids = len(solids)

        if n_solids <= 1:
            return CheckResult(
                name, Severity.PASS,
                "Single solid body – suitable for CNC machining as one part.",
                {"solid_count": n_solids}
            )

        # Analyse each solid's bounding box and volume
        solid_info = []
        for idx, s in enumerate(solids):
            vol = get_volume_cm3(s)
            bbox = Bnd_Box()
            brepbndlib.Add(s, bbox)
            bxmin, bymin, bzmin, bxmax, bymax, bzmax = bbox.Get()
            solid_info.append({
                "index": idx,
                "volume_cm3": round(vol, 2),
                "bbox_mm": [
                    round(bxmax - bxmin, 1),
                    round(bymax - bymin, 1),
                    round(bzmax - bzmin, 1)
                ]
            })

        # Check for overlapping vs. separated solids
        boxes = []
        for s in solids:
            bb = Bnd_Box()
            brepbndlib.Add(s, bb)
            boxes.append(bb)

        separated = 0
        overlapping = 0
        for i in range(len(boxes)):
            has_overlap = False
            for j in range(len(boxes)):
                if i != j and not boxes[i].IsOut(boxes[j]):
                    has_overlap = True
                    break
            if has_overlap:
                overlapping += 1
            else:
                separated += 1

        severity = Severity.FAIL if n_solids > 1 else Severity.WARNING
        return CheckResult(
            name, severity,
            f"Assembly detected: {n_solids} separate solid bodies. Cannot CNC-machine as single part.",
            {
                "solid_count": n_solids,
                "overlapping_solids": overlapping,
                "separated_solids": separated,
                "solids": solid_info[:10],  # cap output
                "advice": "Combine into a single solid, or process each body separately."
            }
        )
    except Exception as e:
        return CheckResult(name, Severity.ERROR, f"Check failed: {e}")


# ── Main orchestrator ─────────────────────────────────────────────────────────
def run_dfm_checks(filepath: str, cfg: dict) -> DFMReport:
    report = DFMReport(file=filepath)

    log.info(f"Loading STEP file: {filepath}")
    shape = load_step(filepath)
    log.info("STEP file loaded successfully.")

    checks = [
        check_confined_hollow,
        check_floating_parts,
        check_large_part_for_material,
        check_material_thickness,
        check_model_fidelity,
        check_model_shell_count,
        check_part_exceeds_max_size_for_finish,
        check_small_holes,
        check_assembled_part,
    ]

    for check_fn in checks:
        log.info(f"Running: {check_fn.__name__}")
        result = check_fn(shape, cfg)
        report.add(result)

    return report


# ── CLI ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="DFM Checker for STEP/STP files")
    parser.add_argument("step_file", help="Path to the .step or .stp file")
    parser.add_argument("--config", help="YAML config file to override defaults", default=None)
    parser.add_argument("--output-yaml", help="Write YAML report to this file", default=None)
    parser.add_argument("--material", help="Material name (e.g. aluminium, steel)", default=None)
    parser.add_argument("--finish", help="Surface finish (e.g. anodizing, powder_coat)", default=None)
    args = parser.parse_args()

    # Build config
    cfg = DEFAULT_CONFIG.copy()
    if args.config:
        with open(args.config) as f:
            cfg.update(yaml.safe_load(f))
    if args.material:
        cfg["material"] = args.material
    if args.finish:
        cfg["finish"] = args.finish

    if not os.path.isfile(args.step_file):
        print(f"ERROR: File not found: {args.step_file}")
        sys.exit(1)

    try:
        report = run_dfm_checks(args.step_file, cfg)
    except Exception as e:
        print(f"FATAL: {e}")
        sys.exit(1)

    report.print_summary()

    if args.output_yaml:
        with open(args.output_yaml, "w") as f:
            f.write(report.to_json()) # using the same to_json name to return yaml
        log.info(f"YAML report written to: {args.output_yaml}")

    # Exit with non-zero if any failures
    has_fail = any(r.severity == Severity.FAIL for r in report.results)
    sys.exit(1 if has_fail else 0)


if __name__ == "__main__":
    main()
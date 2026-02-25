"""
step_analyzer.py — Analyze STEP files to extract geometry for CNC machining.

Uses cadquery to read STEP files and extract:
  - Bounding box (stock dimensions)
  - Face classification (planar, cylindrical, freeform)
  - Hole detection (cylindrical faces aligned to Z-axis)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Tuple

import cadquery as cq


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class HoleFeature:
    """A detected hole (cylindrical cut through the part)."""
    center_x: float
    center_y: float
    top_z: float
    bottom_z: float
    diameter: float

    @property
    def depth(self) -> float:
        return self.top_z - self.bottom_z


@dataclass
class BoundingBox:
    """Axis-aligned bounding box of the part."""
    x_min: float
    y_min: float
    z_min: float
    x_max: float
    y_max: float
    z_max: float

    @property
    def x_size(self) -> float:
        return self.x_max - self.x_min

    @property
    def y_size(self) -> float:
        return self.y_max - self.y_min

    @property
    def z_size(self) -> float:
        return self.z_max - self.z_min


@dataclass
class PartGeometry:
    """Complete geometry analysis of a STEP part."""
    bounding_box: BoundingBox
    stock_x: float              # Stock dimension X (bb + clearance)
    stock_y: float              # Stock dimension Y (bb + clearance)
    stock_z: float              # Stock dimension Z (bb + clearance)
    num_faces: int
    num_planar_faces: int
    num_curved_faces: int
    holes: List[HoleFeature] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------

class StepAnalyzer:
    """Read a STEP file and extract machining-relevant geometry."""

    def __init__(self, step_path: str | Path, stock_clearance: float = 2.0):
        self.step_path = Path(step_path)
        self.stock_clearance = stock_clearance
        if not self.step_path.exists():
            raise FileNotFoundError(f"STEP file not found: {self.step_path}")

    def analyze(self) -> PartGeometry:
        """Return a PartGeometry describing the loaded STEP part."""
        shape = self._load_step()
        bb = self._bounding_box(shape)
        faces = self._classify_faces(shape)
        holes = self._detect_holes(shape, bb)
        return PartGeometry(
            bounding_box=bb,
            stock_x=bb.x_size + 2 * self.stock_clearance,
            stock_y=bb.y_size + 2 * self.stock_clearance,
            stock_z=bb.z_size + self.stock_clearance,   # clearance on top only
            num_faces=faces["total"],
            num_planar_faces=faces["planar"],
            num_curved_faces=faces["curved"],
            holes=holes,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_step(self) -> cq.Shape:
        """Load a STEP file and return the combined shape."""
        wp = cq.importers.importStep(str(self.step_path))
        return wp.val()

    def _bounding_box(self, shape: cq.Shape) -> BoundingBox:
        bb = shape.BoundingBox()
        return BoundingBox(
            x_min=bb.xmin, y_min=bb.ymin, z_min=bb.zmin,
            x_max=bb.xmax, y_max=bb.ymax, z_max=bb.zmax,
        )

    def _classify_faces(self, shape: cq.Shape) -> dict:
        """Count planar vs curved faces."""
        from OCP.TopAbs import TopAbs_FACE
        from OCP.TopExp import TopExp_Explorer
        from OCP.GeomAbs import GeomAbs_Plane
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.TopoDS import TopoDS

        explorer = TopExp_Explorer(shape.wrapped, TopAbs_FACE)
        total = planar = curved = 0
        while explorer.More():
            face = TopoDS.Face_s(explorer.Current())
            adaptor = BRepAdaptor_Surface(face)
            if adaptor.GetType() == GeomAbs_Plane:
                planar += 1
            else:
                curved += 1
            total += 1
            explorer.Next()
        return {"total": total, "planar": planar, "curved": curved}

    def _detect_holes(self, shape: cq.Shape, bb: BoundingBox) -> List[HoleFeature]:
        """Detect cylindrical faces whose axis is parallel to Z → treat as holes."""
        from OCP.TopAbs import TopAbs_FACE
        from OCP.TopExp import TopExp_Explorer
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.GeomAbs import GeomAbs_Cylinder
        from OCP.TopoDS import TopoDS

        holes: List[HoleFeature] = []
        seen_centers: set = set()
        explorer = TopExp_Explorer(shape.wrapped, TopAbs_FACE)

        while explorer.More():
            face = explorer.Current()
            adaptor = BRepAdaptor_Surface(TopoDS.Face_s(face))
            if adaptor.GetType() == GeomAbs_Cylinder:
                cylinder = adaptor.Cylinder()
                axis = cylinder.Axis().Direction()
                # Check if axis is approximately parallel to Z
                if abs(axis.Z()) > 0.95:
                    loc = cylinder.Location()
                    cx, cy = round(loc.X(), 3), round(loc.Y(), 3)
                    radius = round(cylinder.Radius(), 3)
                    key = (cx, cy, radius)
                    if key not in seen_centers:
                        seen_centers.add(key)
                        # Determine top/bottom Z from face bounds
                        u_min, u_max, v_min, v_max = self._face_uv_bounds(adaptor)
                        # For Z-aligned cylinder, V param corresponds to Z
                        p_low = adaptor.Value(u_min, v_min)
                        p_high = adaptor.Value(u_min, v_max)
                        z_vals = sorted([p_low.Z(), p_high.Z()])
                        holes.append(HoleFeature(
                            center_x=cx,
                            center_y=cy,
                            top_z=z_vals[1],
                            bottom_z=z_vals[0],
                            diameter=round(radius * 2, 3),
                        ))
            explorer.Next()
        return holes

    @staticmethod
    def _face_uv_bounds(adaptor) -> Tuple[float, float, float, float]:
        return (adaptor.FirstUParameter(), adaptor.LastUParameter(),
                adaptor.FirstVParameter(), adaptor.LastVParameter())


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------

def analyze_step(step_path: str | Path, stock_clearance: float = 2.0) -> PartGeometry:
    """One-call convenience function."""
    return StepAnalyzer(step_path, stock_clearance).analyze()

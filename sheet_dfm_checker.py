import sys
import math
import cadquery as cq
from OCP.BRepAdaptor import BRepAdaptor_Surface, BRepAdaptor_Curve
from OCC.Core.GeomAbs import GeomAbs_Circle
from collections import defaultdict


# ---------------------------------------------------------------------------
# Default DFM configuration
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "material": "sheet_metal",
    "max_part_volume_cm3": 30_000,   # above → Large Part for Material fail
    "min_hole_diameter_mm": 1.0,     # below → Small Holes fail
}


class SheetMetalAnalyzer:
    """
    Analyzes STEP files to detect sheet metal thickness and bend count
    using geometric face analysis (planar and cylindrical surfaces).

    Also performs the following DFM checks (mirrored from cnc_dfm_checker.py):
      • Floating Parts Check
      • Confined Hollow
      • Large Part for Material
      • Small Holes
      • Assembled Part
    """

    def __init__(self, step_file: str, config: dict | None = None):
        self.step_file = step_file
        self.part = None
        self.cfg = {**DEFAULT_CONFIG, **(config or {})}

    def load(self):
        """Load the STEP file and return self for chaining."""
        self.part = cq.importers.importStep(self.step_file)
        return self

    # ------------------------------------------------------------------
    # Internal helpers – geometry
    # ------------------------------------------------------------------

    @staticmethod
    def _group_cylinders(cyl_list: list) -> list:
        """
        Group cylinder face descriptors that share the same axis
        (same direction vector and collinear location).
        """
        groups = []
        for c in cyl_list:
            pc_loc = cq.Vector(c["loc"])
            pc_dir = cq.Vector(c["dir"])
            placed = False
            for g in groups:
                ref = g[0]
                ref_loc = cq.Vector(ref["loc"])
                ref_dir = cq.Vector(ref["dir"])
                if pc_dir.cross(ref_dir).Length < 1e-3:
                    vec = pc_loc - ref_loc
                    if vec.cross(ref_dir).Length < 1e-3:
                        g.append(c)
                        placed = True
                        break
            if not placed:
                groups.append([c])
        return groups

    @staticmethod
    def _classify_cylinders(cyl_faces: list) -> tuple[list, list]:
        """
        Split cylinder faces into closed (full-revolution) and partial lists.
        Returns (closed_cyls, partial_cyls) where each item is a dict with
        keys: radius, area, loc, dir.
        """
        closed_cyls = []
        partial_cyls = []

        for face in cyl_faces:
            surf = BRepAdaptor_Surface(face.wrapped, True)
            is_closed = surf.IsUClosed() or surf.IsVClosed()

            cylinder = surf.Cylinder()
            radius = cylinder.Radius()
            axis = cylinder.Axis()
            loc = axis.Location()
            dir_ = axis.Direction()

            item = {
                "radius": radius,
                "area": face.Area(),
                "loc": (loc.X(), loc.Y(), loc.Z()),
                "dir": (dir_.X(), dir_.Y(), dir_.Z()),
            }
            (closed_cyls if is_closed else partial_cyls).append(item)

        return closed_cyls, partial_cyls

    @staticmethod
    def _solid_bbox(solid_shape) -> tuple[float, float, float, float, float, float]:
        """
        Return (xmin, ymin, zmin, xmax, ymax, zmax) bounding box for a raw
        OCC TopoDS shape using CadQuery's BoundingBox helper.
        """
        bb = cq.Shape(solid_shape).BoundingBox()
        return bb.xmin, bb.ymin, bb.zmin, bb.xmax, bb.ymax, bb.zmax

    @staticmethod
    def _solid_volume_cm3(solid_shape) -> float:
        """Return volume in cm³ for a raw OCC TopoDS shape."""
        vol_mm3 = cq.Shape(solid_shape).Volume()
        return vol_mm3 / 1_000.0

    @staticmethod
    def _boxes_overlap(bb1, bb2, tol: float = 1e-3) -> bool:
        """
        Return True if two bounding-box tuples (xmin,ymin,zmin,xmax,ymax,zmax)
        overlap (share any volume within tolerance).
        """
        for i in range(3):
            if bb1[i + 3] + tol < bb2[i] or bb2[i + 3] + tol < bb1[i]:
                return False
        return True

    # ------------------------------------------------------------------
    # Thickness detection
    # ------------------------------------------------------------------

    def detect_thickness(self, planar_faces: list, closed_axis_groups: list) -> float:
        """
        Estimate sheet metal thickness from:
          1. Distance between parallel, opposing planar face pairs.
          2. Radial difference between concentric closed-cylinder face pairs.

        Returns the weighted-average thickness in mm (rounded to 3 dp),
        or 0.0 if it cannot be determined.
        """
        dist_weights: dict[float, float] = defaultdict(float)

        # --- planar face pairs ---
        for j in range(len(planar_faces)):
            for k in range(j + 1, len(planar_faces)):
                f1 = planar_faces[j]
                f2 = planar_faces[k]
                n1 = f1.normalAt()
                n2 = f2.normalAt()

                # Must be anti-parallel (opposite normals → facing faces)
                if n1.cross(n2).Length < 1e-4 and n1.dot(n2) < -0.99:
                    c1 = f1.Center()
                    c2 = f2.Center()
                    vec = cq.Vector(c2.x - c1.x, c2.y - c1.y, c2.z - c1.z)
                    dist = abs(vec.dot(n1))
                    if dist > 1e-3:
                        dist_weights[round(dist, 1)] += f1.Area() + f2.Area()

        # --- closed cylinder pairs (radial diff) ---
        for group in closed_axis_groups:
            if len(group) >= 2:
                for j in range(len(group)):
                    for k in range(j + 1, len(group)):
                        diff = abs(group[j]["radius"] - group[k]["radius"])
                        if diff > 1e-3:
                            dist_weights[round(diff, 1)] += (
                                group[j]["area"] + group[k]["area"]
                            )

        if not dist_weights:
            return 0.0

        # Pick the candidate with the highest combined face area
        best_rounded = max(dist_weights, key=dist_weights.__getitem__)

        # Refine to a precise weighted average
        sum_val = 0.0
        sum_weight = 0.0

        for j in range(len(planar_faces)):
            for k in range(j + 1, len(planar_faces)):
                f1 = planar_faces[j]
                f2 = planar_faces[k]
                n1 = f1.normalAt()
                n2 = f2.normalAt()
                if n1.cross(n2).Length < 1e-4 and n1.dot(n2) < -0.99:
                    c1 = f1.Center()
                    c2 = f2.Center()
                    dist = abs(
                        cq.Vector(c2.x - c1.x, c2.y - c1.y, c2.z - c1.z).dot(n1)
                    )
                    if dist > 1e-3 and round(dist, 1) == best_rounded:
                        weight = f1.Area() + f2.Area()
                        sum_val += dist * weight
                        sum_weight += weight

        for group in closed_axis_groups:
            for j in range(len(group)):
                for k in range(j + 1, len(group)):
                    diff = abs(group[j]["radius"] - group[k]["radius"])
                    if diff > 1e-3 and round(diff, 1) == best_rounded:
                        weight = group[j]["area"] + group[k]["area"]
                        sum_val += diff * weight
                        sum_weight += weight

        if sum_weight > 0:
            return round(sum_val / sum_weight, 3)
        return best_rounded

    # ------------------------------------------------------------------
    # Bend detection
    # ------------------------------------------------------------------

    def detect_bends(self, partial_axis_groups: list, thickness: float) -> int:
        """
        Count bends by looking for concentric partial-cylinder axis groups
        whose adjacent radii differ by approximately one sheet thickness.

        Returns the number of detected bends.
        """
        bends = 0
        for group in partial_axis_groups:
            radii = sorted(
                {round(g["radius"], 3) for g in group}
            )
            for r_idx in range(len(radii) - 1):
                diff = radii[r_idx + 1] - radii[r_idx]
                if abs(diff - thickness) < 1e-2:
                    bends += 1
                    break  # one bend per axis group
        return bends

    # ------------------------------------------------------------------
    # Non-sheet features detection
    # ------------------------------------------------------------------

    def detect_non_sheet_features(self, faces: list) -> bool:
        """
        Detect non-sheet features (e.g., countersinks, chamfers, complex forms)
        by checking for face geometries other than PLANE and CYLINDER.
        """
        for face in faces:
            if face.geomType() in ("SPHERE", "TORUS"):
                return True
        return False

    # ------------------------------------------------------------------
    # DFM Check: Floating Parts
    # ------------------------------------------------------------------

    def check_floating_parts(self, solids: list) -> dict:
        """
        Detect disconnected / floating sub-bodies within the part.

        A solid is considered 'floating' when its bounding box does not
        overlap with any other solid's bounding box (i.e. it is spatially
        isolated from the rest of the assembly).

        Returns a result dict with keys: status, message, details.
        """
        name = "Floating Parts Check"
        try:
            n = len(solids)
            if n <= 1:
                return {
                    "check": name,
                    "status": "PASS",
                    "message": "Single solid body – no floating parts.",
                    "details": {"solid_count": n},
                }

            boxes = [self._solid_bbox(s.wrapped) for s in solids]
            floating_indices = []
            for i, bb in enumerate(boxes):
                overlaps_any = any(
                    self._boxes_overlap(bb, boxes[j])
                    for j in range(n) if j != i
                )
                if not overlaps_any:
                    floating_indices.append(i)

            if floating_indices:
                return {
                    "check": name,
                    "status": "FAIL",
                    "message": (
                        f"{len(floating_indices)} solid(s) appear to be floating / disconnected."
                    ),
                    "details": {
                        "floating_solid_indices": floating_indices,
                        "advice": "Ensure all parts are properly joined or assembled.",
                    },
                }

            return {
                "check": name,
                "status": "WARNING",
                "message": (
                    f"Multiple solids ({n}) found. Bounding boxes overlap – "
                    "verify this is an intentional assembly."
                ),
                "details": {"solid_count": n},
            }

        except Exception as exc:
            return {"check": name, "status": "ERROR", "message": str(exc), "details": {}}

    # ------------------------------------------------------------------
    # DFM Check: Confined Hollow
    # ------------------------------------------------------------------

    def check_confined_hollow(self, solids: list) -> dict:
        """
        Detect internal hollow cavities (confined voids) within solids.

        A solid that contains more than one shell is a strong indicator
        of an internal void: the outer shell encloses the part surface and
        every additional shell represents a separate internal surface (cavity).

        Returns a result dict with keys: status, message, details.
        """
        name = "Confined Hollow"
        try:
            if not solids:
                return {
                    "check": name,
                    "status": "SKIP",
                    "message": "No solids found – cannot analyse hollows.",
                    "details": {},
                }

            hollow_count = 0
            for solid in solids:
                shell_count = len(solid.Shells())
                # More than one shell → internal void present
                if shell_count > 1:
                    hollow_count += 1

            if hollow_count:
                return {
                    "check": name,
                    "status": "FAIL",
                    "message": (
                        f"{hollow_count} solid(s) contain internal hollow cavities "
                        "with limited access."
                    ),
                    "details": {
                        "confined_hollows": hollow_count,
                        "advice": "Add drain/vent holes or redesign to eliminate enclosed voids.",
                    },
                }

            return {
                "check": name,
                "status": "PASS",
                "message": "No confined hollows detected.",
                "details": {},
            }

        except Exception as exc:
            return {"check": name, "status": "ERROR", "message": str(exc), "details": {}}

    # ------------------------------------------------------------------
    # DFM Check: Large Part for Material
    # ------------------------------------------------------------------

    def check_large_part_for_material(self, solids: list) -> dict:
        """
        Volume-based large-part check relative to the configured material.

        The total volume of all solids is compared against
        cfg['max_part_volume_cm3'].  Sheet metal parts are typically flat and
        lightweight; unusually high volumes may indicate a modelling error or
        a design that is impractical to fabricate.

        Returns a result dict with keys: status, message, details.
        """
        name = "Large Part for Material"
        try:
            total_vol = sum(self._solid_volume_cm3(s.wrapped) for s in solids)
            limit = self.cfg["max_part_volume_cm3"]
            material = self.cfg.get("material", "unknown")

            if total_vol > limit:
                return {
                    "check": name,
                    "status": "FAIL",
                    "message": (
                        f"Part volume {total_vol:.1f} cm³ exceeds {limit} cm³ "
                        f"limit for {material}."
                    ),
                    "details": {
                        "volume_cm3": round(total_vol, 2),
                        "limit_cm3": limit,
                        "material": material,
                    },
                }

            return {
                "check": name,
                "status": "PASS",
                "message": (
                    f"Part volume {total_vol:.1f} cm³ is within limits for {material}."
                ),
                "details": {"volume_cm3": round(total_vol, 2)},
            }

        except Exception as exc:
            return {"check": name, "status": "ERROR", "message": str(exc), "details": {}}

    # ------------------------------------------------------------------
    # DFM Check: Small Holes
    # ------------------------------------------------------------------

    @staticmethod
    def _face_inner_wires(cq_face):
        """
        Return inner (hole) wires of a CadQuery Face as lists of CadQuery Edge objects.

        CadQuery's face.Wires() returns all wires; the first wire (by largest
        bounding-box area) is the outer boundary.  All remaining wires are inner
        loops (holes).
        """
        all_wires = cq_face.Wires()
        if len(all_wires) <= 1:
            return []

        # Identify outer wire by largest area approximation:
        # use the wire that produces the largest bounding-box diagonal.
        def _wire_bbox_diag(w):
            bb = cq.Shape(w.wrapped).BoundingBox()
            return (bb.xlen ** 2 + bb.ylen ** 2 + bb.zlen ** 2) ** 0.5

        outer = max(all_wires, key=_wire_bbox_diag)
        return [w for w in all_wires if not w.wrapped.IsSame(outer.wrapped)]

    @staticmethod
    def _is_redundant_hole(new_center, new_radius, new_normal, stored, tol=1e-4):
        """Return True if an equivalent hole already exists in *stored*."""
        for h in stored:
            if abs(h["radius"] - new_radius) > tol:
                continue
            vec = tuple(new_center[i] - h["center"][i] for i in range(3))
            dot = sum(vec[i] * h["normal"][i] for i in range(3))
            proj = tuple(h["center"][i] + dot * h["normal"][i] for i in range(3))
            if math.sqrt(sum((proj[i] - new_center[i]) ** 2 for i in range(3))) < tol:
                return True
        return False

    def check_small_holes(self, solids: list) -> dict:
        """
        Detect holes whose diameter is below cfg['min_hole_diameter_mm'].

        Strategy:
          • For each solid, examine planar faces.
          • For each planar face, inspect inner wires (skip the outer wire).
          • If every edge of an inner wire is a circular arc with the same
            centre and radius → it is a circular hole.
          • Deduplicate using axis + radius equality, then compare diameter
            against the configured minimum.

        Returns a result dict with keys: status, message, details.
        """
        name = "Small Holes"
        try:
            min_dia = self.cfg["min_hole_diameter_mm"]
            all_holes: list[dict] = []
            issues: list[dict] = []

            for solid in solids:
                detected: list[dict] = []

                for cq_face in solid.Faces():
                    if cq_face.geomType() != "PLANE":
                        continue

                    n_vec = cq_face.normalAt()
                    normal = (n_vec.x, n_vec.y, n_vec.z)

                    # _face_inner_wires returns CadQuery Wire objects for holes only
                    for cq_wire in self._face_inner_wires(cq_face):
                        cq_edges = cq_wire.Edges()
                        if not cq_edges:
                            continue

                        is_circle = True
                        circle_center = None
                        circle_radius = None

                        for cq_edge in cq_edges:
                            curve = BRepAdaptor_Curve(cq_edge.wrapped)
                            if curve.GetType() != GeomAbs_Circle:
                                is_circle = False
                                break
                            circ = curve.Circle()
                            loc = circ.Location()
                            circle_center = (loc.X(), loc.Y(), loc.Z())
                            circle_radius = circ.Radius()

                        if not (is_circle and circle_center is not None):
                            continue

                        if self._is_redundant_hole(circle_center, circle_radius,
                                                   normal, detected):
                            continue

                        detected.append({
                            "center": circle_center,
                            "radius": circle_radius,
                            "normal": normal,
                        })

                        diameter = 2.0 * circle_radius
                        if diameter < min_dia:
                            issues.append({
                                "center_mm": tuple(round(v, 3) for v in circle_center),
                                "diameter_mm": round(diameter, 3),
                                "normal": tuple(round(v, 4) for v in normal),
                                "problem": (
                                    f"diameter {diameter:.3f} mm < min {min_dia} mm"
                                ),
                            })

                all_holes.extend(detected)

            total_holes = len(all_holes)

            if issues:
                return {
                    "check": name,
                    "status": "FAIL",
                    "message": (
                        f"{len(issues)} of {total_holes} hole(s) are below "
                        f"the minimum diameter ({min_dia} mm)."
                    ),
                    "details": {
                        "holes_with_issues": issues,
                        "total_holes_found": total_holes,
                        "min_diameter_mm": min_dia,
                        "advice": (
                            "Increase hole diameter or consider laser cutting for "
                            "unavoidably small holes."
                        ),
                    },
                }

            return {
                "check": name,
                "status": "PASS",
                "message": (
                    f"All {total_holes} hole(s) meet the minimum diameter "
                    f"({min_dia} mm)."
                ),
                "details": {
                    "total_holes_found": total_holes,
                    "min_diameter_mm": min_dia,
                },
            }

        except Exception as exc:
            return {"check": name, "status": "ERROR", "message": str(exc), "details": {}}

    # ------------------------------------------------------------------
    # DFM Check: Assembled Part
    # ------------------------------------------------------------------

    def check_assembled_part(self, solids: list) -> dict:
        """
        Detect if the STEP file represents a multi-body assembly rather than
        a single monolithic part.

        Sheet metal parts are normally a single flat blank (possibly bent);
        multiple disjoint solids almost always indicate a multi-part assembly
        that cannot be fabricated as one piece.

        Returns a result dict with keys: status, message, details.
        """
        name = "Assembled Part"
        try:
            n = len(solids)

            if n <= 1:
                return {
                    "check": name,
                    "status": "PASS",
                    "message": "Single solid body – suitable for sheet metal fabrication.",
                    "details": {"solid_count": n},
                }

            # Collect per-solid volume and bounding box
            solid_info = []
            boxes = []
            for idx, s in enumerate(solids):
                vol = self._solid_volume_cm3(s.wrapped)
                bb = self._solid_bbox(s.wrapped)
                boxes.append(bb)
                solid_info.append({
                    "index": idx,
                    "volume_cm3": round(vol, 2),
                    "bbox_mm": [
                        round(bb[3] - bb[0], 1),
                        round(bb[4] - bb[1], 1),
                        round(bb[5] - bb[2], 1),
                    ],
                })

            # Classify each solid as overlapping or separated
            separated = sum(
                1 for i, bb in enumerate(boxes)
                if not any(
                    self._boxes_overlap(bb, boxes[j])
                    for j in range(n) if j != i
                )
            )
            overlapping = n - separated

            return {
                "check": name,
                "status": "FAIL",
                "message": (
                    f"Assembly detected: {n} separate solid bodies. "
                    "Cannot fabricate as a single sheet metal part."
                ),
                "details": {
                    "solid_count": n,
                    "overlapping_solids": overlapping,
                    "separated_solids": separated,
                    "solids": solid_info[:10],  # cap output
                    "advice": (
                        "Combine into a single solid, or process each body separately."
                    ),
                },
            }

        except Exception as exc:
            return {"check": name, "status": "ERROR", "message": str(exc), "details": {}}

    # ------------------------------------------------------------------
    # Result formatting helper
    # ------------------------------------------------------------------

    @staticmethod
    def _format_dfm_result(result: dict) -> str:
        icon_map = {"PASS": "✅", "FAIL": "❌", "WARNING": "⚠️ ", "SKIP": "⏭️ ", "ERROR": "💥"}
        icon = icon_map.get(result["status"], " ")
        lines = [f"  {icon}  [{result['status']:<7}]  {result['check']}",
                 f"           {result['message']}"]
        for k, v in result.get("details", {}).items():
            lines.append(f"             • {k}: {v}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Public analysis entry-point
    # ------------------------------------------------------------------

    def analyze(self):
        """
        Run the full analysis on every solid in the loaded STEP file
        and print results to stdout.

        For each solid the original geometry checks are reported (thickness,
        bend count, non-sheet features) followed by the DFM checks which
        evaluate the whole part (all solids together):
          • Floating Parts Check
          • Confined Hollow
          • Large Part for Material
          • Small Holes
          • Assembled Part
        """
        if self.part is None:
            self.load()

        solids = self.part.solids().vals()

        # ── Per-solid geometric analysis ────────────────────────────────
        for i, solid in enumerate(solids):
            faces = solid.Faces()
            planar_faces = [f for f in faces if f.geomType() == "PLANE"]
            cyl_faces = [f for f in faces if f.geomType() == "CYLINDER"]

            closed_cyls, partial_cyls = self._classify_cylinders(cyl_faces)
            closed_axis_groups = self._group_cylinders(closed_cyls)
            partial_axis_groups = self._group_cylinders(partial_cyls)

            thickness = self.detect_thickness(planar_faces, closed_axis_groups)
            bends = self.detect_bends(partial_axis_groups, thickness)
            non_sheet = self.detect_non_sheet_features(faces)

            print(
                f"File: {self.step_file} | "
                f"Solid {i}: Thickness = {thickness}mm | "
                f"Bends = {bends} | "
                f"Non-Sheet Features = {'Yes' if non_sheet else 'No'}"
            )

        # ── Part-level DFM checks ───────────────────────────────────────
        print("\n" + "=" * 65)
        print(f"  DFM Checks  →  {self.step_file}")
        print("=" * 65)

        dfm_checks = [
            self.check_floating_parts(solids),
            self.check_confined_hollow(solids),
            self.check_large_part_for_material(solids),
            self.check_small_holes(solids),
            self.check_assembled_part(solids),
        ]

        for result in dfm_checks:
            print(self._format_dfm_result(result))

        fails = sum(1 for r in dfm_checks if r["status"] == "FAIL")
        warns = sum(1 for r in dfm_checks if r["status"] == "WARNING")
        print("=" * 65)
        print(
            f"  Total: {len(dfm_checks)} DFM checks | "
            f"{fails} failures | {warns} warnings"
        )
        print("=" * 65 + "\n")

        return dfm_checks


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print("Usage: python sheet_metal_analyzer.py <step_file1> [step_file2 ...]")
        sys.exit(1)

    for step_file in sys.argv[1:]:
        try:
            analyzer = SheetMetalAnalyzer(step_file)
            analyzer.analyze()
        except Exception as e:
            print(f"Error processing {step_file}: {e}")


if __name__ == "__main__":
    main()
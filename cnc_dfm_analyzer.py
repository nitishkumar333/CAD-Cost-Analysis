import math
import cadquery as cq
from OCP.BRepAdaptor import BRepAdaptor_Surface, BRepAdaptor_Curve
from collections import defaultdict
from OCC.Core.GeomAbs import GeomAbs_Circle
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.ShapeAnalysis import ShapeAnalysis_Shell
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_SHELL, TopAbs_FACE


# ---------------------------------------------------------------------------
# Default DFM configuration
# ---------------------------------------------------------------------------

class CNCAnalyzer:
    """
    Analyzes STEP files for CNC machine
    using geometric face analysis (planar and cylindrical surfaces).

    Also performs the following DFM checks (mirrored from cnc_dfm_checker.py):
      • Floating Parts Check
      • Confined Hollow
      • Large Part for Material
      • Small Holes
      • Assembled Part
      • Model Fidelity
    """

    def __init__(self, step_file: str, config: dict = None):
        self.step_file = step_file
        self.part = None
        self.cfg = config

    def load(self):
        """Load the STEP file and return self for chaining."""
        self.part = cq.importers.importStep(self.step_file)
        return self

    # ------------------------------------------------------------------
    # Internal helpers - geometry
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
        Estimate thickness from:
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
                    "message": "No solids found - cannot analyse hollows.",
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
        cfg['max_part_volume_cm3']. High volumes may indicate a modelling error or
        a design that is impractical to fabricate.

        Returns a result dict with keys: status, message, details.
        """
        name = "Large Part for Material"
        try:
            total_vol = sum(self._solid_volume_cm3(s.wrapped) for s in solids)
            limit = self.cfg["cnc_max_part_volume_cm3"]
            material = self.cfg.get("material")

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
        Detect holes whose diameter is below cfg['cnc_min_hole_diameter_mm'].

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
            min_dia = self.cfg["cnc_min_hole_diameter_mm"]
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

        Returns a result dict with keys: status, message, details.
        """
        name = "Assembled Part"
        try:
            n = len(solids)

            if n <= 1:
                return {
                    "check": name,
                    "status": "PASS",
                    "message": "Single solid body - suitable for CNC process.",
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
                    "Cannot fabricate as a single part."
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
    # DFM Check: Check Thickness
    # ------------------------------------------------------------------

    def check_thickness(self, solids: list) -> dict:
        """
        Verify detected thickness for each solid individually
        against configured min/max bounds.
        """
        name = "Thickness Check"
        try:
            min_t = self.cfg.get("cnc_min_materail_thickness_mm")
            max_t = self.cfg.get("cnc_max_materail_thickness_mm")

            solid_results = []
            overall_status = "PASS"

            for idx, solid in enumerate(solids, start=1):
                faces = solid.Faces()
                planar_faces = [f for f in faces if f.geomType() == "PLANE"]
                cyl_faces = [f for f in faces if f.geomType() == "CYLINDER"]

                closed_cyls, _ = self._classify_cylinders(cyl_faces)
                closed_axis_groups = self._group_cylinders(closed_cyls)

                thickness = self.detect_thickness(planar_faces, closed_axis_groups)

                if thickness <= 0:
                    result = {
                        "solid_index": idx,
                        "status": "SKIP",
                        "message": "Could not detect thickness.",
                        "detected_thickness_mm": None,
                    }
                    overall_status = "FAIL"
                else:
                    thickness = round(thickness, 3)

                    if thickness < min_t or thickness > max_t:
                        result = {
                            "solid_index": idx,
                            "status": "FAIL",
                            "message": f"Thickness {thickness} mm is out of range [{min_t}, {max_t}] mm.",
                            "detected_thickness_mm": thickness,
                        }
                        overall_status = "FAIL"
                    else:
                        result = {
                            "solid_index": idx,
                            "status": "PASS",
                            "message": f"Thickness {thickness} mm is within range.",
                            "detected_thickness_mm": thickness,
                        }

                solid_results.append(result)

            if not solid_results:
                return {
                    "check": name,
                    "status": "SKIP",
                    "message": "No solids found.",
                    "details": {},
                }

            # Overall summary
            return {
                "check": name,
                "status": overall_status,
                "message": (
                    "All solids have valid thickness."
                    if overall_status == "PASS"
                    else "One or more solids have invalid or undetected thickness."
                ),
                "details": {
                    "min_materail_thickness_mm": min_t,
                    "max_materail_thickness_mm": max_t,
                    "solids": solid_results,
                },
            }

        except Exception as exc:
            return {
                "check": name,
                "status": "ERROR",
                "message": str(exc),
                "details": {},
            }

    # ================================================================
    #  MODEL FIDELITY CHECKS
    # ================================================================

    # ------------------------------------------------------------------
    # Model Fidelity
    # ------------------------------------------------------------------

    def _check_solid_fidelity(self, cq_shape: cq.Shape, cfg: dict) -> dict:
        """
        Check geometric validity using CadQuery: BRep integrity, degenerate
        geometry, and open/free edges (non-watertight shells).

        Config keys
        -----------
        fidelity_min_faces : int   minimum acceptable face count (default 4)

        Returns a result dict with keys: check, status, message, details.
        """
        name = "Model Fidelity"
        issues = []

        try:
            shape = cq_shape.wrapped  # underlying OCC TopoDS_Shape

            # ── 1. BRep integrity ─────────────────────────────────────────
            analyzer = BRepCheck_Analyzer(shape)
            if not analyzer.IsValid():
                issues.append("BRep check failed - shape has geometric errors.")

            # ── 2. Degenerate face count ───────────────────────────────────
            faces = cq_shape.Faces()
            face_count = len(faces)
            min_faces = cfg.get("cnc_fidelity_min_faces")
            if face_count < min_faces:
                issues.append(
                    f"Very low face count ({face_count}) - possible degenerate model."
                )

            # ── 3. Open shells (non-watertight) ───────────────────────────
            shell_explorer = TopExp_Explorer(shape, TopAbs_SHELL)
            shell_analysis = ShapeAnalysis_Shell()
            while shell_explorer.More():
                shell = shell_explorer.Current()
                shell_analysis.LoadShells(shell)
                if shell_analysis.HasFreeEdges():
                    issues.append(
                        "Open / free edges detected on shell - non-watertight geometry."
                    )
                    break
                shell_explorer.Next()

            # ── Result ────────────────────────────────────────────────────
            if issues:
                return {
                    "check": name,
                    "status": "FAIL",
                    "message": "Model fidelity issues found.",
                    "details": {"issues": issues, "face_count": face_count},
                }

            return {
                "check": name,
                "status": "PASS",
                "message": f"Model passes fidelity checks ({face_count} faces, watertight shells).",
                "details": {"face_count": face_count},
            }

        except Exception as exc:
            return {
                "check": name,
                "status": "ERROR",
                "message": f"Check failed: {exc}",
                "details": {},
            }

    def check_model_fidelity(self, solids) -> dict:
        """
        Run fidelity check for all solids and return a consolidated result.

        Returns
        -------
        dict with keys:
            check, status, message, details
        """
        name = "Model Fidelity"
        solid_results = []
        overall_issues = []

        try:
            solids = self.part.solids().vals()

            if not solids:
                return {
                    "check": name,
                    "status": "FAIL",
                    "message": "No solids found in the model.",
                    "details": {}
                }

            # Run fidelity for each solid
            for idx, solid in enumerate(solids, start=1):
                result = self._check_solid_fidelity(solid, self.cfg)

                # Attach solid index
                result["details"]["solid_index"] = idx
                solid_results.append(result)

                # Collect failures
                if result["status"] == "FAIL":
                    overall_issues.append({
                        "solid_index": idx,
                        "issues": result["details"].get("issues", [])
                    })

            # Determine overall status
            if overall_issues:
                return {
                    "check": name,
                    "status": "FAIL",
                    "message": f"Fidelity issues found in {len(overall_issues)} solid(s).",
                    "details": {
                        "solid_count": len(solids),
                        "failed_solids": overall_issues,
                        "per_solid_results": solid_results
                    }
                }

            return {
                "check": name,
                "status": "PASS",
                "message": f"All {len(solids)} solid(s) passed fidelity checks.",
                "details": {
                    "solid_count": len(solids),
                    "per_solid_results": solid_results
                }
            }

        except Exception as exc:
            return {
                "check": name,
                "status": "ERROR",
                "message": f"Check failed: {exc}",
                "details": {}
            }

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

        For each solid the original geometry checks are reported followed by the DFM checks which
        evaluate the whole part (all solids together):
          • Floating Parts Check
          • Confined Hollow
          • Large Part for Material
          • Small Holes
          • Assembled Part
          • Model Fidelity
        """
        if self.part is None:
            self.load()

        solids = self.part.solids().vals()

        # ── Per-solid geometric analysis ────────────────────────────────
        for i, solid in enumerate(solids):
            faces = solid.Faces()
            planar_faces = [f for f in faces if f.geomType() == "PLANE"]
            cyl_faces = [f for f in faces if f.geomType() == "CYLINDER"]

            closed_cyls, _ = self._classify_cylinders(cyl_faces)
            closed_axis_groups = self._group_cylinders(closed_cyls)

            thickness = self.detect_thickness(planar_faces, closed_axis_groups)

            print(
                f"File: {self.step_file} | "
                f"Solid {i}: Thickness = {thickness}mm | "
            )

        # ── Part-level DFM checks ───────────────────────────────────────
        print("\n" + "=" * 65)
        print(f"  DFM Checks  →  {self.step_file}")
        print("=" * 65)

        dfm_checks = [
            self.check_thickness(solids),
            self.check_confined_hollow(solids),
            self.check_large_part_for_material(solids),
            self.check_small_holes(solids),
            self.check_assembled_part(solids),
            self.check_model_fidelity(solids)
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
from collections import defaultdict
import cadquery as cq
from core.base_test import BaseTest
from core.geometry_utils import classify_cylinders, group_cylinders

class CNCThicknessTest(BaseTest):
    @property
    def name(self) -> str:
        return "Thickness Check"

    def detect_thickness(self, planar_faces: list, closed_axis_groups: list) -> float:
        dist_weights = defaultdict(float)

        for j in range(len(planar_faces)):
            for k in range(j + 1, len(planar_faces)):
                f1 = planar_faces[j]
                f2 = planar_faces[k]
                n1 = f1.normalAt()
                n2 = f2.normalAt()

                if n1.cross(n2).Length < 1e-4 and n1.dot(n2) < -0.99:
                    c1 = f1.Center()
                    c2 = f2.Center()
                    vec = cq.Vector(c2.x - c1.x, c2.y - c1.y, c2.z - c1.z)
                    dist = abs(vec.dot(n1))
                    if dist > 1e-3:
                        dist_weights[round(dist, 1)] += f1.Area() + f2.Area()

        for group in closed_axis_groups:
            if len(group) >= 2:
                for j in range(len(group)):
                    for k in range(j + 1, len(group)):
                        diff = abs(group[j]["radius"] - group[k]["radius"])
                        if diff > 1e-3:
                            dist_weights[round(diff, 1)] += (group[j]["area"] + group[k]["area"])

        if not dist_weights:
            return 0.0

        best_rounded = max(dist_weights, key=dist_weights.__getitem__)
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
                    dist = abs(cq.Vector(c2.x - c1.x, c2.y - c1.y, c2.z - c1.z).dot(n1))
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

    def analyze(self, solids: list, part=None) -> dict:
        try:
            min_t = self.cfg.get("cnc_min_materail_thickness_mm")
            max_t = self.cfg.get("cnc_max_materail_thickness_mm")

            solid_results = []
            overall_status = "PASS"

            for idx, solid in enumerate(solids, start=1):
                faces = solid.Faces()
                planar_faces = [f for f in faces if f.geomType() == "PLANE"]
                cyl_faces = [f for f in faces if f.geomType() == "CYLINDER"]

                closed_cyls, _ = classify_cylinders(cyl_faces)
                closed_axis_groups = group_cylinders(closed_cyls)

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

                    if min_t is not None and max_t is not None and (thickness < min_t or thickness > max_t):
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
                    "check": self.name,
                    "status": "SKIP",
                    "message": "No solids found.",
                    "details": {},
                }

            return {
                "check": self.name,
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
                "check": self.name,
                "status": "ERROR",
                "message": str(exc),
                "details": {},
            }

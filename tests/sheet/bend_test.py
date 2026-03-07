from core.base_test import BaseTest
from core.geometry_utils import classify_cylinders, group_cylinders
from tests.sheet.sheet_thickness_test import SheetThicknessTest

class BendTest(BaseTest):
    @property
    def name(self) -> str:
        return "Bends Check"

    def detect_bends(self, partial_axis_groups: list, thickness: float) -> int:
        bends = 0
        for group in partial_axis_groups:
            radii = sorted({round(g["radius"], 3) for g in group})
            for r_idx in range(len(radii) - 1):
                diff = radii[r_idx + 1] - radii[r_idx]
                if abs(diff - thickness) < 1e-2:
                    bends += 1
                    break  # one bend per axis group
        return bends

    def analyze(self, solids: list, part=None) -> dict:
        try:
            max_bends = self.cfg.get("sheet_max_bends", 20)
            solid_results = []
            overall_status = "PASS"
            total_bends = 0
            
            thickness_test = SheetThicknessTest(self.cfg)

            for idx, solid in enumerate(solids, start=1):
                faces = solid.Faces()
                planar_faces = [f for f in faces if f.geomType() == "PLANE"]
                cyl_faces = [f for f in faces if f.geomType() == "CYLINDER"]

                closed_cyls, partial_cyls = classify_cylinders(cyl_faces)
                closed_axis_groups = group_cylinders(closed_cyls)
                partial_axis_groups = group_cylinders(partial_cyls)

                thickness = thickness_test.detect_thickness(planar_faces, closed_axis_groups)
                
                bends = 0
                if thickness > 0:
                    bends = self.detect_bends(partial_axis_groups, thickness)
                
                total_bends += bends
                
                solid_results.append({
                    "solid_index": idx,
                    "bends": bends,
                    "thickness_mm": thickness
                })

            if total_bends > max_bends:
                overall_status = "FAIL"
                
            return {
                "check": self.name,
                "status": overall_status,
                "message": f"Detected {total_bends} bend(s) across all solids." if overall_status == "PASS" else f"Detected {total_bends} bend(s), exceeding maximum of {max_bends}.",
                "details": {
                    "total_bends": total_bends,
                    "max_bends": max_bends,
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

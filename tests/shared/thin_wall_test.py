from core.base_test import BaseTest
from core.thin_wall_utils import detect_thin_walls

class ThinWallTest(BaseTest):
    @property
    def name(self) -> str:
        return "Thin Walls"

    def analyze(self, solids: list, part=None) -> dict:
        try:
            process = self.cfg.get("process_type", "cnc").lower()
            threshold_mm = self.cfg.get(f"{process}_min_materail_thickness_mm", 1.0)

            issues = []
            overall_status = "PASS"
            total_thin_walls = 0

            for idx, solid in enumerate(solids, start=1):
                shape = solid.wrapped
                
                thin_walls = detect_thin_walls(shape, threshold_mm=threshold_mm - 1e-4)

                if thin_walls:
                    overall_status = "FAIL"
                    total_thin_walls += len(thin_walls)
                    
                    wall_details = []
                    for tw in thin_walls:
                        wall_details.append({
                            "thickness_mm": round(tw.thickness, 4),
                            "face_pair": (tw.face_idx_a, tw.face_idx_b),
                            "areas_mm2": (round(tw.area_a, 2), round(tw.area_b, 2)),
                        })
                        
                    issues.append({
                        "solid_index": idx,
                        "thin_walls_found": len(thin_walls),
                        "walls": wall_details
                    })

            if overall_status == "FAIL":
                return {
                    "check": self.name,
                    "status": overall_status,
                    "message": f"Detected {total_thin_walls} thin wall section(s) under {threshold_mm} mm.",
                    "details": {
                        "issues": issues,
                        "threshold_mm": threshold_mm,
                        "advice": "Increase wall thickness to avoid breakage or warping."
                    },
                }

            return {
                "check": self.name,
                "status": overall_status,
                "message": f"No thin walls detected below {threshold_mm} mm.",
                "details": {
                    "threshold_mm": threshold_mm,
                },
            }
        except Exception as exc:
            return {
                "check": self.name,
                "status": "ERROR",
                "message": str(exc),
                "details": {},
            }

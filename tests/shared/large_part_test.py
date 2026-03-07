from core.base_test import BaseTest
from core.geometry_utils import solid_volume_cm3

class LargePartTest(BaseTest):
    @property
    def name(self) -> str:
        return "Large Part for Material"

    def analyze(self, solids: list, part=None) -> dict:
        try:
            total_vol = sum(solid_volume_cm3(s.wrapped) for s in solids)
            
            process = self.cfg.get("process_type", "cnc").lower()
            limit_key = f"{process}_max_part_volume_cm3"
            limit = self.cfg.get(limit_key, 50000.0)
            material = self.cfg.get("material", "unknown material")

            if total_vol > limit:
                return {
                    "check": self.name,
                    "status": "FAIL",
                    "message": f"Part volume {total_vol:.1f} cm³ exceeds {limit} cm³ limit for {material}.",
                    "details": {
                        "volume_cm3": round(total_vol, 2),
                        "limit_cm3": limit,
                        "material": material,
                    },
                }

            return {
                "check": self.name,
                "status": "PASS",
                "message": f"Part volume {total_vol:.1f} cm³ is within limits for {material}.",
                "details": {"volume_cm3": round(total_vol, 2)},
            }

        except Exception as exc:
            return {"check": self.name, "status": "ERROR", "message": str(exc), "details": {}}

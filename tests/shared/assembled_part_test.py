from core.base_test import BaseTest
from core.geometry_utils import solid_volume_cm3, solid_bbox, boxes_overlap

class AssembledPartTest(BaseTest):
    @property
    def name(self) -> str:
        return "Assembled Part"

    def analyze(self, solids: list, part=None) -> dict:
        try:
            n = len(solids)

            if n <= 1:
                return {
                    "check": self.name,
                    "status": "PASS",
                    "message": "Single solid body - suitable for process.",
                    "details": {"solid_count": n},
                }

            solid_info = []
            boxes = []
            for idx, s in enumerate(solids):
                vol = solid_volume_cm3(s.wrapped)
                bb = solid_bbox(s.wrapped)
                boxes.append(bb)
                solid_info.append({
                    "volume_cm3": round(vol, 2),
                    "bbox_mm": [
                        round(bb[3] - bb[0], 1),
                        round(bb[4] - bb[1], 1),
                        round(bb[5] - bb[2], 1),
                    ],
                })

            separated = sum(
                1 for i, bb in enumerate(boxes)
                if not any(boxes_overlap(bb, boxes[j]) for j in range(n) if j != i)
            )
            overlapping = n - separated

            return {
                "check": self.name,
                "status": "FAIL",
                "message": f"Assembly detected: {n} separate solid bodies. Cannot fabricate as a single part.",
                "details": {
                    "solid_count": n,
                    "overlapping_solids": overlapping,
                    "separated_solids": separated,
                    "solids": solid_info[:10],
                    "advice": "Combine into a single solid, or process each body separately.",
                },
            }

        except Exception as exc:
            return {"check": self.name, "status": "ERROR", "message": str(exc), "details": {}}

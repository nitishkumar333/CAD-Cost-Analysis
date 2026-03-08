from core.base_test import BaseTest

class NonSheetFeaturesTest(BaseTest):
    @property
    def name(self) -> str:
        return "Non-Sheet Features"

    def detect_non_sheet_features(self, solid) -> bool:
        vol = solid.Volume()
        bb = solid.BoundingBox()
        bb_vol = (bb.xmax - bb.xmin) * (bb.ymax - bb.ymin) * (bb.zmax - bb.zmin)
        ratio = vol / bb_vol if bb_vol > 0 else 0
        
        max_dim = max(bb.xmax - bb.xmin, bb.ymax - bb.ymin, bb.zmax - bb.zmin)
        min_dim = min(bb.xmax - bb.xmin, bb.ymax - bb.ymin, bb.zmax - bb.zmin)

        # Bent sheet metal typically has a very low bounding box ratio because it occupies sparse 3D space
        if ratio < 0.30:
            return False
            
        # Perfectly flat, unbent rectangular plate
        if ratio > 0.85:
            # If the plate is very thin relative to its size, it's sheet metal.
            # If it's too thick (e.g. > 15mm) or cube-like, it's a solid block.
            if min_dim > 15.0 or (max_dim > 0 and min_dim / max_dim > 0.5):
                return True # Solid block / non-sheet
            else:
                return False # Flat sheet metal plate
                
        # Anything falling in the middle (0.30 <= ratio <= 0.85) is usually hardware/fasteners
        return True

    def analyze(self, solids: list, part=None) -> dict:
        try:
            allow_non_sheet = self.cfg.get("sheet_allow_non_sheet_features", False)
            solid_results = []
            
            non_sheet_count = 0

            for idx, solid in enumerate(solids, start=1):
                found = self.detect_non_sheet_features(solid)
                if found:
                    non_sheet_count += 1
                solid_results.append({
                    "solid_index": idx,
                    "has_non_sheet_features": found
                })

            all_non_sheet = (non_sheet_count == len(solids)) and len(solids) > 0

            if all_non_sheet and not allow_non_sheet:
                return {
                    "check": self.name,
                    "status": "FAIL",
                    "message": "Only non-sheet parts (e.g. solid blocks/fasteners) detected, but not allowed by config.",
                    "details": {
                        "allow_non_sheet_features": allow_non_sheet,
                        "solids": solid_results,
                    },
                }

            return {
                "check": self.name,
                "status": "PASS",
                "message": "Valid sheet metal parts detected." if not all_non_sheet else "All non-sheet parts detected, but allowed by config.",
                "details": {
                    "allow_non_sheet_features": allow_non_sheet,
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

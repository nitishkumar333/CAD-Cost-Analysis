from core.base_test import BaseTest

class NonSheetFeaturesTest(BaseTest):
    @property
    def name(self) -> str:
        return "Non-Sheet Features"

    def detect_non_sheet_features(self, faces: list) -> bool:
        for face in faces:
            if face.geomType() in ("SPHERE", "TORUS"):
                return True
        return False

    def analyze(self, solids: list, part=None) -> dict:
        try:
            allow_non_sheet = self.cfg.get("sheet_allow_non_sheet_features", False)
            solid_results = []
            overall_status = "PASS"
            found_any = False

            for idx, solid in enumerate(solids, start=1):
                faces = solid.Faces()
                found = self.detect_non_sheet_features(faces)
                if found:
                    found_any = True
                solid_results.append({
                    "solid_index": idx,
                    "has_non_sheet_features": found
                })

            if found_any and not allow_non_sheet:
                return {
                    "check": self.name,
                    "status": "FAIL",
                    "message": "Non-sheet features (e.g. countersinks, chamfers) detected, but not allowed by config.",
                    "details": {
                        "allow_non_sheet_features": allow_non_sheet,
                        "solids": solid_results,
                    },
                }

            return {
                "check": self.name,
                "status": "PASS",
                "message": "No disallowed non-sheet features detected." if not found_any else "Non-sheet features detected, but allowed by config.",
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

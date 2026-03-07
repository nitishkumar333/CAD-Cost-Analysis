from core.base_test import BaseTest

class ConfinedHollowTest(BaseTest):
    @property
    def name(self) -> str:
        return "Confined Hollow"

    def analyze(self, solids: list, part=None) -> dict:
        try:
            if not solids:
                return {
                    "check": self.name,
                    "status": "SKIP",
                    "message": "No solids found - cannot analyse hollows.",
                    "details": {},
                }

            hollow_count = 0
            for solid in solids:
                shell_count = len(solid.Shells())
                if shell_count > 1:
                    hollow_count += 1

            if hollow_count:
                return {
                    "check": self.name,
                    "status": "FAIL",
                    "message": f"{hollow_count} solid(s) contain internal hollow cavities with limited access.",
                    "details": {
                        "confined_hollows": hollow_count,
                        "advice": "Add drain/vent holes or redesign to eliminate enclosed voids.",
                    },
                }

            return {
                "check": self.name,
                "status": "PASS",
                "message": "No confined hollows detected.",
                "details": {},
            }

        except Exception as exc:
            return {"check": self.name, "status": "ERROR", "message": str(exc), "details": {}}

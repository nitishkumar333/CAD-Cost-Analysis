from core.base_test import BaseTest

class ConfinedHollowTest(BaseTest):
    @property
    def name(self) -> str:
        return "Confined Hollow"

    def analyze(self, solids: list, part=None) -> dict:
        try:
            from OCP.TopExp import TopExp_Explorer
            from OCP.TopAbs import TopAbs_SHELL, TopAbs_REVERSED

            if not solids:
                return {
                    "check": self.name,
                    "status": "SKIP",
                    "message": "No solids found - cannot analyse hollows.",
                    "details": {},
                }

            hollow_count = 0
            solids_affected = 0
            for solid in solids:
                topods_solid = getattr(solid, 'wrapped', solid)
                exp = TopExp_Explorer(topods_solid, TopAbs_SHELL)
                solid_hollows = 0
                while exp.More():
                    shell = exp.Current()
                    if shell.Orientation() == TopAbs_REVERSED:
                        solid_hollows += 1
                    exp.Next()
                
                if solid_hollows > 0:
                    hollow_count += solid_hollows
                    solids_affected += 1

            if hollow_count > 0:
                return {
                    "check": self.name,
                    "status": "FAIL",
                    "message": f"Found {hollow_count} internal hollow cavit{'y' if hollow_count == 1 else 'ies'} across {solids_affected} solid(s).",
                    "details": {
                        "confined_hollows": hollow_count,
                        "solids_affected": solids_affected,
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

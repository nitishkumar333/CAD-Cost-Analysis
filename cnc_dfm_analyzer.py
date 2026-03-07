import cadquery as cq
from tests.shared.model_fidelity_test import ModelFidelityTest
from tests.shared.confined_hollow_test import ConfinedHollowTest
from tests.shared.large_part_test import LargePartTest
from tests.shared.assembled_part_test import AssembledPartTest
from tests.shared.hole_cutout_test import HoleCutoutTest
from tests.shared.thin_wall_test import ThinWallTest
from tests.cnc.cnc_thickness_test import CNCThicknessTest

class CNCAnalyzer:
    """
    Analyzes STEP files for CNC machine DFM checks using OOP-based test architecture.
    """

    def __init__(self, step_file: str, config: dict = None):
        self.step_file = step_file
        self.part = None
        self.cfg = config or {}
        
        # Instantiate all tests
        self.tests = [
            ModelFidelityTest(self.cfg),
            ConfinedHollowTest(self.cfg),
            LargePartTest(self.cfg),
            AssembledPartTest(self.cfg),
            CNCThicknessTest(self.cfg),
            HoleCutoutTest(self.cfg),
            ThinWallTest(self.cfg)
        ]

    def load(self):
        """Load the STEP file and return self for chaining."""
        self.part = cq.importers.importStep(self.step_file)
        return self

    @staticmethod
    def _format_dfm_result(result: dict) -> str:
        icon_map = {"PASS": "✅", "FAIL": "❌", "WARNING": "⚠️ ", "SKIP": "⏭️ ", "ERROR": "💥"}
        icon = icon_map.get(result.get("status", " "), " ")
        lines = [f"  {icon}  [{result.get('status', ' '):<7}]  {result.get('check', 'Unknown')}"]
        
        if "message" in result:
            lines.append(f"           {result['message']}")
            
        for k, v in result.get("details", {}).items():
            # If the value is a list of dicts or large, handle it carefully
            if isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict):
                lines.append(f"             • {k}: {len(v)} item(s)")
            else:
                lines.append(f"             • {k}: {v}")
        return "\n".join(lines)

    def analyze(self):
        """
        Run the full analysis on every solid in the loaded STEP file
        and print results to stdout.
        """
        if self.part is None:
            self.load()

        solids = self.part.solids().vals()
        if not solids:
            print("❌ No solids found in the STEP file.")
            return

        print(f"\n{'='*60}")
        print(f"  CNC DFM Analysis Report")
        print(f"  File: {self.step_file}")
        print(f"  Solids Detected: {len(solids)}")
        print(f"{'='*60}\n")

        overall_status = "PASS"
        
        # Run all tests
        for test in self.tests:
            print(f"Running Check: {test.name}...")
            result = test.analyze(solids, part=self.part)
            
            if result.get("status") in ["FAIL", "ERROR"]:
                overall_status = "FAIL"
                
            print(self._format_dfm_result(result))
            print("-" * 60)

        print(f"\n{'='*60}")
        print(f"  OVERALL STATUS: {overall_status}")
        print(f"{'='*60}\n")
import cadquery as cq
from datetime import datetime
from tests.shared.model_fidelity_test import ModelFidelityTest
from tests.shared.confined_hollow_test import ConfinedHollowTest
from tests.shared.large_part_test import LargePartTest
from tests.shared.assembled_part_test import AssembledPartTest
from tests.shared.hole_cutout_test import HoleCutoutTest
from tests.shared.hole_test import HoleTest
from tests.shared.cutout_test import CutoutTest
from tests.shared.thin_wall_test import ThinWallTest
from tests.sheet.sheet_thickness_test import SheetThicknessTest
from tests.sheet.bend_test import BendTest
from tests.sheet.non_sheet_features_test import NonSheetFeaturesTest


class SheetMetalAnalyzer:
    """
    Analyzes STEP files for Sheet Metal DFM checks using OOP-based test architecture.
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
            SheetThicknessTest(self.cfg),
            HoleCutoutTest(self.cfg),
            HoleTest(self.cfg),
            CutoutTest(self.cfg),
            ThinWallTest(self.cfg),
            BendTest(self.cfg),
            NonSheetFeaturesTest(self.cfg),
        ]

    def load(self):
        """Load the STEP file and return self for chaining."""
        self.part = cq.importers.importStep(self.step_file)
        return self

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _status_icon(status: str) -> str:
        return {"PASS": "✅", "FAIL": "❌", "WARNING": "⚠️", "SKIP": "⏭️", "ERROR": "💥"}.get(status, "❓")

    @staticmethod
    def _format_dfm_result(result: dict) -> str:
        """Return a plain-text formatted result (used for stdout)."""
        icon_map = {"PASS": "✅", "FAIL": "❌", "WARNING": "⚠️ ", "SKIP": "⏭️ ", "ERROR": "💥"}
        icon = icon_map.get(result.get("status", " "), " ")
        lines = [f"  {icon}  [{result.get('status', ' '):<7}]  {result.get('check', 'Unknown')}"]

        if "message" in result:
            lines.append(f"           {result['message']}")

        def format_value(value, indent: int):
            prefix = " " * indent
            if isinstance(value, dict):
                for k, v in value.items():
                    if isinstance(v, (dict, list)):
                        lines.append(f"{prefix}• {k}:")
                        format_value(v, indent + 4)
                    else:
                        lines.append(f"{prefix}• {k}: {v}")
            elif isinstance(value, list):
                for i, item in enumerate(value):
                    if isinstance(item, dict):
                        lines.append(f"{prefix}[{i}]")
                        format_value(item, indent + 4)
                    else:
                        lines.append(f"{prefix}- {item}")
            else:
                lines.append(f"{prefix}{value}")

        format_value(result.get("details", {}), indent=13)
        return "\n".join(lines)

    @staticmethod
    def _details_to_md(value, indent: int = 0) -> list[str]:
        """Recursively convert a details dict/list into Markdown lines."""
        lines = []
        prefix = "  " * indent
        if isinstance(value, dict):
            for k, v in value.items():
                if isinstance(v, (dict, list)):
                    lines.append(f"{prefix}- **{k}**:")
                    lines.extend(SheetMetalAnalyzer._details_to_md(v, indent + 1))
                else:
                    lines.append(f"{prefix}- **{k}:** {v}")
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, dict):
                    lines.append(f"{prefix}- *Item {i}*:")
                    lines.extend(SheetMetalAnalyzer._details_to_md(item, indent + 1))
                else:
                    lines.append(f"{prefix}- {item}")
        else:
            lines.append(f"{prefix}{value}")
        return lines

    def _result_to_md(self, result: dict) -> list[str]:
        """Convert a single test result to Markdown lines."""
        status = result.get("status", "UNKNOWN")
        icon = self._status_icon(status)
        check = result.get("check", "Unknown Check")

        lines = [
            f"### {icon} {check}",
            "",
            f"**Status:** `{status}`",
        ]

        if "message" in result:
            lines += ["", f"**Message:** {result['message']}"]

        details = result.get("details", {})
        if details:
            lines += ["", "**Details:**", ""]
            lines.extend(self._details_to_md(details, indent=0))

        lines.append("")
        return lines

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(self, output_md: str = None):
        """
        Run the full analysis on every solid in the loaded STEP file.

        Prints results to stdout and, if *output_md* is provided, saves a
        formatted Markdown report to that path.

        Args:
            output_md: Optional file path (e.g. ``"report.md"``) where the
                       Markdown report will be written.
        """
        if self.part is None:
            self.load()

        solids = self.part.solids().vals()
        if not solids:
            print("❌ No solids found in the STEP file.")
            return

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # ---- stdout header ----
        print(f"\n{'='*60}")
        print(f"  Sheet Metal DFM Analysis Report")
        print(f"  File: {self.step_file}")
        print(f"  Solids Detected: {len(solids)}")
        print(f"{'='*60}\n")

        overall_status = "PASS"
        results = []

        # ---- run tests ----
        for test in self.tests:
            print(f"Running Check: {test.name}...")
            result = test.analyze(solids, part=self.part)

            if result.get("status") in ["FAIL", "ERROR"]:
                overall_status = "FAIL"

            results.append(result)
            print(self._format_dfm_result(result))
            print("-" * 60)

        # ---- stdout footer ----
        print(f"\n{'='*60}")
        print(f"  OVERALL STATUS: {overall_status}")
        print(f"{'='*60}\n")

        # ---- Markdown report ----
        if output_md:
            import os
            action = "appended to" if os.path.isfile(output_md) else "saved to"
            self._write_markdown(
                path=output_md,
                results=results,
                overall_status=overall_status,
                num_solids=len(solids),
                timestamp=timestamp,
            )
            print(f"📄 Markdown report {action}: {output_md}")

    def _write_markdown(
        self,
        path: str,
        results: list[dict],
        overall_status: str,
        num_solids: int,
        timestamp: str,
    ):
        """
        Write the Markdown report to *path*.

        - If the file does **not** exist: creates it with a top-level title and
          the full report as the first run.
        - If the file **already exists**: appends the new run as a clearly
          separated section (no duplicate top-level title).
        """
        import os

        file_exists = os.path.isfile(path)
        overall_icon = self._status_icon(overall_status)

        # ---- summary table ----
        summary_rows = []
        for r in results:
            st = r.get("status", "UNKNOWN")
            summary_rows.append(
                f"| {self._status_icon(st)} | {r.get('check', 'Unknown')} | `{st}` |"
            )

        if file_exists:
            # Append mode: open with a clear visual separator and run header
            md_lines = [
                "",
                "=" * 60,
                "",
                f"## Run — {timestamp}",
                "",
                f"| | |",
                f"|---|---|",
                f"| **File** | `{self.step_file}` |",
                f"| **Solids detected** | {num_solids} |",
                f"| **Overall status** | {overall_icon} **{overall_status}** |",
            ]
        else:
            # New file: include the top-level title
            md_lines = [
                "# Sheet Metal DFM Analysis Report",
                "",
                f"## Run — {timestamp}",
                "",
                f"| | |",
                f"|---|---|",
                f"| **File** | `{self.step_file}` |",
                f"| **Solids detected** | {num_solids} |",
                f"| **Overall status** | {overall_icon} **{overall_status}** |",
            ]

        md_lines += [
            "",
            "---",
            "",
            "### Summary",
            "",
            "| Status | Check | Result |",
            "|:------:|-------|:------:|",
            *summary_rows,
            "",
            "---",
            "",
            "### Detailed Results",
            "",
        ]

        for result in results:
            md_lines.extend(self._result_to_md(result))
            md_lines.append("---")
            md_lines.append("")

        md_lines += [
            "### Overall Verdict",
            "",
            f"> {overall_icon} **{overall_status}**",
            "",
            f"*Report generated on {timestamp}*",
            "",
        ]

        mode = "a" if file_exists else "w"
        with open(path, mode, encoding="utf-8") as fh:
            fh.write("\n".join(md_lines) + "\n")
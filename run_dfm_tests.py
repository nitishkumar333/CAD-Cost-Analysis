"""
DFM Test Runner: Generate test STEP files and validate DFM check results.

Generates defective STEP files via generate_test_steps.py, runs the CNC
and Sheet Metal DFM analyzers, and compares results against expectations.

Usage:
    conda activate occ_env
    cd /home/nitishkumar/Desktop/dfm
    python run_dfm_tests.py
"""

import os
import sys
import yaml
import subprocess

# ── Generate test parts first ────────────────────────────────────────
print("🔧 Generating test STEP files...\n")
gen_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "generate_test_steps.py")
subprocess.run([sys.executable, gen_script], check=True)
print()

from cnc_dfm_analyzer import CNCAnalyzer
from sheet_dfm_analyzer import SheetMetalAnalyzer

TEST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_parts")
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")

with open(CONFIG_PATH) as f:
    base_cfg = yaml.safe_load(f) or {}


# ── Test case definitions ────────────────────────────────────────────
# Each test: (filename, analyzer_type, expected_fails)
#   expected_fails: set of check names that should have status "FAIL"
#   If empty set → all checks should PASS

CNC_TESTS = [
    {
        "file": "cnc_thin_wall.step",
        "desc": "Thin wall (0.5mm) - below min thickness",
        "expected_fails": {"Thickness Check"},
    },
    {
        "file": "cnc_thick_part.step",
        "desc": "Thick part (50mm) - above max thickness",
        "expected_fails": {"Thickness Check"},
    },
    {
        "file": "cnc_small_holes.step",
        "desc": "Small holes (0.6mm dia) - below min hole diameter",
        "expected_fails": {"Small Holes"},
    },
    {
        "file": "cnc_assembly.step",
        "desc": "Multi-body assembly - two separated solids",
        "expected_fails": {"Assembled Part"},
    },
    {
        "file": "cnc_oversized.step",
        "desc": "Oversized part (~64000 cm³) - exceeds volume limit",
        "expected_fails": {"Large Part for Material"},
    },
    {
        "file": "cnc_good_part.step",
        "desc": "Good CNC part - should pass all checks",
        "expected_fails": set(),
    },
]

SHEET_TESTS = [
    {
        "file": "sheet_thin.step",
        "desc": "Thin sheet (0.5mm) - below min thickness",
        "expected_fails": {"Thickness Check"},
    },
    {
        "file": "sheet_thick.step",
        "desc": "Thick sheet (20mm) - above max thickness",
        "expected_fails": {"Thickness Check"},
    },
    {
        "file": "sheet_small_holes.step",
        "desc": "Small holes (0.6mm dia) - below min hole diameter",
        "expected_fails": {"Small Holes"},
    },
    {
        "file": "sheet_many_bends.step",
        "desc": "5 bends - exceeds custom max bend count (3)",
        "expected_fails": {"Bend Count Check"},
        "config_override": {"sheet_max_bends": 3},  # lower threshold
    },
    {
        "file": "sheet_non_sheet_features.step",
        "desc": "Non-sheet features (sphere) - not sheet-compatible",
        "expected_fails": {"Non-Sheet Features"},
    },
    {
        "file": "sheet_assembly.step",
        "desc": "Multi-body assembly - two separated sheets",
        "expected_fails": {"Assembled Part"},
    },
    {
        "file": "sheet_oversized.step",
        "desc": "Oversized sheet (~32000 cm³) - exceeds volume limit",
        "expected_fails": {"Large Part for Material"},
    },
    {
        "file": "sheet_good_part.step",
        "desc": "Good sheet metal part - should pass all checks",
        "expected_fails": set(),
    },
]


def run_analyzer(step_path, analyzer_type, config_override=None):
    """Run the appropriate DFM analyzer and return the check results."""
    cfg = dict(base_cfg)
    if config_override:
        cfg.update(config_override)

    if analyzer_type == "cnc":
        cfg["process_type"] = "cnc"
        analyzer = CNCAnalyzer(step_path, config=cfg)
    else:
        cfg["process_type"] = "sheet_metal"
        analyzer = SheetMetalAnalyzer(step_path, config=cfg)

    return analyzer.analyze()


def evaluate_test(test_case, analyzer_type):
    """Run one test case and compare with expectations."""
    step_path = os.path.join(TEST_DIR, test_case["file"])

    if not os.path.exists(step_path):
        return {
            "file": test_case["file"],
            "desc": test_case["desc"],
            "result": "SKIP",
            "reason": "STEP file not found",
            "details": [],
        }

    try:
        config_override = test_case.get("config_override")
        results = run_analyzer(step_path, analyzer_type, config_override)
    except Exception as e:
        return {
            "file": test_case["file"],
            "desc": test_case["desc"],
            "result": "ERROR",
            "reason": str(e),
            "details": [],
        }

    expected_fails = test_case["expected_fails"]

    # Build actual fail set
    actual_fails = set()
    check_details = []
    for r in results:
        check_name = r.get("check", "?")
        status = r.get("status", "?")
        check_details.append(f"{check_name}: {status}")
        if status == "FAIL":
            actual_fails.add(check_name)

    # Compare
    missing_fails = expected_fails - actual_fails  # expected FAIL but got PASS
    unexpected_fails = actual_fails - expected_fails  # expected PASS but got FAIL

    if not missing_fails and not unexpected_fails:
        return {
            "file": test_case["file"],
            "desc": test_case["desc"],
            "result": "PASS",
            "reason": "",
            "details": check_details,
        }
    else:
        reasons = []
        if missing_fails:
            reasons.append(f"Expected FAIL not triggered: {missing_fails}")
        if unexpected_fails:
            reasons.append(f"Unexpected FAILs: {unexpected_fails}")
        return {
            "file": test_case["file"],
            "desc": test_case["desc"],
            "result": "FAIL",
            "reason": "; ".join(reasons),
            "details": check_details,
        }


def main():
    all_results = []

    # ── CNC Tests ────────────────────────────────────────────────────
    print("\n" + "=" * 75)
    print("  CNC DFM TEST RESULTS")
    print("=" * 75)

    for test in CNC_TESTS:
        print(f"\n── Testing: {test['file']} ──")
        print(f"   {test['desc']}")
        result = evaluate_test(test, "cnc")
        all_results.append(("CNC", result))

        icon = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭️ ", "ERROR": "💥"}.get(
            result["result"], "?"
        )
        print(f"   Test Result: {icon} {result['result']}")
        if result["reason"]:
            print(f"   Reason: {result['reason']}")
        for d in result["details"]:
            print(f"     • {d}")

    # ── Sheet Metal Tests ────────────────────────────────────────────
    print("\n" + "=" * 75)
    print("  SHEET METAL DFM TEST RESULTS")
    print("=" * 75)

    for test in SHEET_TESTS:
        print(f"\n── Testing: {test['file']} ──")
        print(f"   {test['desc']}")
        result = evaluate_test(test, "sheet_metal")
        all_results.append(("Sheet", result))

        icon = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭️ ", "ERROR": "💥"}.get(
            result["result"], "?"
        )
        print(f"   Test Result: {icon} {result['result']}")
        if result["reason"]:
            print(f"   Reason: {result['reason']}")
        for d in result["details"]:
            print(f"     • {d}")

    # ── Summary ──────────────────────────────────────────────────────
    print("\n" + "=" * 75)
    print("  SUMMARY")
    print("=" * 75)
    print(f"{'Type':<8} {'File':<35} {'Result':<8} {'Notes'}")
    print("-" * 75)

    total = len(all_results)
    passed = 0
    failed = 0
    skipped = 0

    for typ, r in all_results:
        icon = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭️", "ERROR": "💥"}.get(
            r["result"], "?"
        )
        note = r["reason"][:40] if r["reason"] else ""
        print(f"{typ:<8} {r['file']:<35} {icon:<3} {r['result']:<5}  {note}")
        if r["result"] == "PASS":
            passed += 1
        elif r["result"] == "FAIL":
            failed += 1
        else:
            skipped += 1

    print("-" * 75)
    print(f"Total: {total} | ✅ Passed: {passed} | ❌ Failed: {failed} | ⏭️  Skipped: {skipped}")
    print("=" * 75)


if __name__ == "__main__":
    main()

import cadquery as cq
from core.base_test import BaseTest
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.ShapeAnalysis import ShapeAnalysis_Shell
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_SHELL

class ModelFidelityTest(BaseTest):
    @property
    def name(self) -> str:
        return "Model Fidelity"

    def _check_solid_fidelity(self, cq_shape: cq.Shape) -> dict:
        issues = []
        try:
            shape = cq_shape.wrapped
            analyzer = BRepCheck_Analyzer(shape)
            if not analyzer.IsValid():
                issues.append("BRep check failed - shape has geometric errors.")

            faces = cq_shape.Faces()
            face_count = len(faces)
            
            process = self.cfg.get("process_type", "cnc").lower()
            min_faces = self.cfg.get(f"{process}_fidelity_min_faces", 4)
            
            if face_count < min_faces:
                issues.append(f"Very low face count ({face_count}) - possible degenerate model.")

            shell_explorer = TopExp_Explorer(shape, TopAbs_SHELL)
            shell_analysis = ShapeAnalysis_Shell()
            while shell_explorer.More():
                shell = shell_explorer.Current()
                shell_analysis.LoadShells(shell)
                if shell_analysis.HasFreeEdges():
                    issues.append("Open / free edges detected on shell - non-watertight geometry.")
                    break
                shell_explorer.Next()

            if issues:
                return {
                    "status": "FAIL",
                    "details": {"issues": issues, "face_count": face_count},
                }

            return {
                "status": "PASS",
                "details": {"face_count": face_count},
            }

        except Exception as exc:
            return {
                "status": "ERROR",
                "details": {"error": str(exc)},
            }

    def analyze(self, solids: list, part=None) -> dict:
        solid_results = []
        overall_issues = []

        try:
            if not solids:
                return {
                    "check": self.name,
                    "status": "FAIL",
                    "message": "No solids found in the model.",
                    "details": {}
                }

            for idx, solid in enumerate(solids, start=1):
                result = self._check_solid_fidelity(solid)
                result["details"]["solid_index"] = idx
                solid_results.append(result)

                if result["status"] == "FAIL":
                    overall_issues.append({
                        "solid_index": idx,
                        "issues": result["details"].get("issues", [])
                    })

            if overall_issues:
                return {
                    "check": self.name,
                    "status": "FAIL",
                    "message": f"Fidelity issues found in {len(overall_issues)} solid(s).",
                    "details": {
                        "solid_count": len(solids),
                        "failed_solids": overall_issues,
                        "per_solid_results": solid_results
                    }
                }

            return {
                "check": self.name,
                "status": "PASS",
                "message": f"All {len(solids)} solid(s) passed fidelity checks.",
                "details": {
                    "solid_count": len(solids),
                    "per_solid_results": solid_results
                }
            }

        except Exception as exc:
            return {
                "check": self.name,
                "status": "ERROR",
                "message": f"Check failed: {exc}",
                "details": {}
            }

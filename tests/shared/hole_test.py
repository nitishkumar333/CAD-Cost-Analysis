import cadquery as cq
from core.base_test import BaseTest


class HoleTest(BaseTest):
    @property
    def name(self) -> str:
        return "Small Holes"

    def analyze(self, solids: list, part=None) -> dict:
        try:
            process = self.cfg.get("process_type", "cnc").lower()
            min_dia = self.cfg.get(f"{process}_min_hole_diameter_mm", 1.0)

            solid_results = []
            overall_status = "PASS"
            issues = []

            for idx, solid in enumerate(solids, start=1):
                faces = solid.Faces()

                # Build edge → face adjacency map
                edge_map = {}
                for f_id, f in enumerate(faces):
                    for e in f.Edges():
                        h = hash(e)
                        if h not in edge_map:
                            edge_map[h] = []
                        edge_map[h].append((e, f_id))

                # Collect all inner-wire openings
                openings = []
                opening_edge_hashes = {}

                for f_id, f in enumerate(faces):
                    for w in f.innerWires():
                        is_cyl = True
                        for e in w.Edges():
                            if e.geomType() != "CIRCLE":
                                is_cyl = False
                            h = hash(e)
                            if h not in opening_edge_hashes:
                                opening_edge_hashes[h] = []
                            opening_edge_hashes[h].append(e)

                        # Only collect cylindrical (circular) openings for this test
                        if is_cyl:
                            openings.append({
                                "id": len(openings),
                                "wire": w,
                                "surface_face_id": f_id,
                                "is_cylindrical": True,
                                "wall_faces": set(),
                                "pocket_faces": set(),
                            })

                def is_opening_edge(query_edge):
                    h = hash(query_edge)
                    for e_obj in opening_edge_hashes.get(h, []):
                        if query_edge.isSame(e_obj):
                            return True
                    return False

                # Find wall faces adjacent to each opening wire
                for op in openings:
                    for e in op["wire"].Edges():
                        h = hash(e)
                        for e_obj, adj_f_id in edge_map.get(h, []):
                            if e.isSame(e_obj) and adj_f_id != op["surface_face_id"]:
                                op["wall_faces"].add(adj_f_id)

                # BFS to collect all pocket faces reachable from wall faces
                for op in openings:
                    queue = list(op["wall_faces"])
                    visited = set(queue)
                    while queue:
                        curr_f_id = queue.pop(0)
                        op["pocket_faces"].add(curr_f_id)
                        curr_f = faces[curr_f_id]
                        for e in curr_f.Edges():
                            if is_opening_edge(e):
                                continue
                            h = hash(e)
                            for e_obj, adj_f_id in edge_map.get(h, []):
                                if e.isSame(e_obj) and adj_f_id != curr_f_id:
                                    if adj_f_id not in visited:
                                        visited.add(adj_f_id)
                                        queue.append(adj_f_id)

                # Union-Find to merge openings that share pocket faces
                parent = {op["id"]: op["id"] for op in openings}

                def find(i):
                    if parent[i] == i:
                        return i
                    parent[i] = find(parent[i])
                    return parent[i]

                def union(i, j):
                    ri, rj = find(i), find(j)
                    if ri != rj:
                        parent[ri] = rj

                for i in range(len(openings)):
                    for j in range(i + 1, len(openings)):
                        if openings[i]["pocket_faces"].intersection(openings[j]["pocket_faces"]):
                            union(i, j)

                # Group into unique cylindrical pockets
                unique_holes = {}
                for op in openings:
                    root = find(op["id"])
                    if root not in unique_holes:
                        unique_holes[root] = {
                            "openings": [],
                            "pocket_faces": set(),
                        }
                    unique_holes[root]["openings"].append(op)
                    unique_holes[root]["pocket_faces"].update(op["pocket_faces"])

                small_holes_in_solid = 0
                holes_in_solid = []

                for p_id, p_data in unique_holes.items():
                    op_wire = p_data["openings"][0]["wire"]
                    try:
                        first_edge = op_wire.Edges()[0]
                        radius = first_edge.radius()
                        diameter = radius * 2

                        # Compute bounding box of the pocket faces
                        pocket_face_objs = [faces[fi] for fi in p_data["pocket_faces"]]
                        if pocket_face_objs:
                            bb = pocket_face_objs[0].BoundingBox()
                            for pf in pocket_face_objs[1:]:
                                pbb = pf.BoundingBox()
                                bb.xmin = min(bb.xmin, pbb.xmin)
                                bb.ymin = min(bb.ymin, pbb.ymin)
                                bb.zmin = min(bb.zmin, pbb.zmin)
                                bb.xmax = max(bb.xmax, pbb.xmax)
                                bb.ymax = max(bb.ymax, pbb.ymax)
                                bb.zmax = max(bb.zmax, pbb.zmax)
                            bounding_box = {
                                "xmin": round(bb.xmin, 3),
                                "ymin": round(bb.ymin, 3),
                                "zmin": round(bb.zmin, 3),
                                "xmax": round(bb.xmax, 3),
                                "ymax": round(bb.ymax, 3),
                                "zmax": round(bb.zmax, 3),
                                "xsize": round(bb.xmax - bb.xmin, 3),
                                "ysize": round(bb.ymax - bb.ymin, 3),
                                "zsize": round(bb.zmax - bb.zmin, 3),
                            }
                        else:
                            bounding_box = None

                        hole_info = {
                            "diameter_mm": round(diameter, 3),
                            # "bounding_box": bounding_box,
                        }

                        if diameter < min_dia:
                            small_holes_in_solid += 1
                            issues.append({
                                "solid_index": idx,
                                **hole_info,
                            })

                        holes_in_solid.append(hole_info)

                    except Exception:
                        pass

                solid_results.append({
                    "solid_index": idx,
                    "total_holes": len(holes_in_solid),
                    "small_holes": small_holes_in_solid,
                    "holes": holes_in_solid,
                })

            if issues:
                overall_status = "FAIL"

            total_holes = sum(sr["total_holes"] for sr in solid_results)

            if overall_status == "FAIL":
                return {
                    "check": self.name,
                    "status": overall_status,
                    "message": (
                        f"{len(issues)} hole(s) are below the minimum diameter ({min_dia} mm)."
                    ),
                    "details": {
                        "holes_with_issues": issues,
                        "total_holes_found": total_holes,
                        "min_diameter_mm": min_dia,
                        "advice": "Increase hole diameter or verify tooling availability.",
                        "solids": solid_results,
                    },
                }

            return {
                "check": self.name,
                "status": overall_status,
                "message": (
                    f"All {total_holes} hole(s) meet the minimum diameter ({min_dia} mm)."
                    if total_holes > 0
                    else "No holes detected."
                ),
                "details": {
                    "total_holes_found": total_holes,
                    "min_diameter_mm": min_dia,
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
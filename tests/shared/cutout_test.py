import cadquery as cq
from core.base_test import BaseTest


class CutoutTest(BaseTest):
    @property
    def name(self) -> str:
        return "Cutouts"

    def analyze(self, solids: list, part=None) -> dict:
        try:
            process = self.cfg.get("process_type", "cnc").lower()
            min_vol = self.cfg.get(f"{process}_min_cutout_volume", 1.0)

            solid_results = []
            overall_status = "PASS"
            issues = []
            all_cutouts = []

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

                        # Only collect non-cylindrical openings for this test
                        if not is_cyl:
                            openings.append({
                                "id": len(openings),
                                "wire": w,
                                "surface_face_id": f_id,
                                "is_cylindrical": False,
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

                # Group into unique non-cylindrical pockets (cutouts)
                unique_cutouts = {}
                for op in openings:
                    root = find(op["id"])
                    if root not in unique_cutouts:
                        unique_cutouts[root] = {
                            "openings": [],
                            "pocket_faces": set(),
                        }
                    unique_cutouts[root]["openings"].append(op)
                    unique_cutouts[root]["pocket_faces"].update(op["pocket_faces"])

                cutouts_in_solid = []
                small_cutouts_in_solid = 0

                for p_id, p_data in unique_cutouts.items():
                    # Compute bounding box over all pocket faces of this cutout
                    pocket_face_objs = [faces[fi] for fi in p_data["pocket_faces"]]

                    bounding_box = None
                    volume_mm3 = None
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
                        xsize = round(bb.xmax - bb.xmin, 3)
                        ysize = round(bb.ymax - bb.ymin, 3)
                        zsize = round(bb.zmax - bb.zmin, 3)
                        bounding_box = {
                            "xmin": round(bb.xmin, 3),
                            "ymin": round(bb.ymin, 3),
                            "zmin": round(bb.zmin, 3),
                            "xmax": round(bb.xmax, 3),
                            "ymax": round(bb.ymax, 3),
                            "zmax": round(bb.zmax, 3),
                            "xsize": xsize,
                            "ysize": ysize,
                            "zsize": zsize,
                        }
                        # Approximate pocket volume from bounding box extents
                        volume_mm3 = round(xsize * ysize * zsize, 3)

                    # Collect edge geometry types present in this cutout's openings
                    edge_types = set()
                    for op in p_data["openings"]:
                        for e in op["wire"].Edges():
                            edge_types.add(e.geomType())

                    is_small = volume_mm3 is not None and volume_mm3 < min_vol

                    if is_small:
                        small_cutouts_in_solid += 1
                        issues.append({
                            "solid_index": idx,
                            "volume_mm3": volume_mm3,
                            "bounding_box": bounding_box,
                        })

                    cutout_info = {
                        "edge_types": sorted(edge_types),
                        "opening_count": len(p_data["openings"]),
                        "volume_mm3": volume_mm3,
                        "is_small": is_small,
                        "bounding_box": bounding_box,
                    }
                    cutouts_in_solid.append(cutout_info)
                    all_cutouts.append({"solid_index": idx, **cutout_info})

                solid_results.append({
                    "solid_index": idx,
                    "total_cutouts": len(cutouts_in_solid),
                    "small_cutouts": small_cutouts_in_solid,
                    "cutouts": cutouts_in_solid,
                })

            if issues:
                overall_status = "FAIL"

            total_cutouts = sum(sr["total_cutouts"] for sr in solid_results)

            if overall_status == "FAIL":
                return {
                    "check": self.name,
                    "status": overall_status,
                    "message": (
                        f"{len(issues)} cutout(s) are below the minimum volume ({min_vol} mm³)."
                    ),
                    "details": {
                        "cutouts_with_issues": issues,
                        "total_cutouts_found": total_cutouts,
                        "min_volume_mm3": min_vol,
                        "advice": "Increase cutout dimensions or verify tooling availability.",
                        "solids": solid_results,
                    },
                }

            return {
                "check": self.name,
                "status": overall_status,
                "message": (
                    f"All {total_cutouts} cutout(s) meet the minimum volume ({min_vol} mm³)."
                    if total_cutouts > 0
                    else "No cutouts detected."
                ),
                "details": {
                    "total_cutouts_found": total_cutouts,
                    "min_volume_mm3": min_vol,
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
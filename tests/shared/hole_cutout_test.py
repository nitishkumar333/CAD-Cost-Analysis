import cadquery as cq
from core.base_test import BaseTest

class HoleCutoutTest(BaseTest):
    @property
    def name(self) -> str:
        return "Small Holes & Cutouts"

    def analyze(self, solids: list, part=None) -> dict:
        try:
            process = self.cfg.get("process_type", "cnc").lower()
            min_dia = self.cfg.get(f"{process}_min_hole_diameter_mm", 1.0)
            
            solid_results = []
            overall_status = "PASS"
            issues = []
            
            for idx, solid in enumerate(solids, start=1):
                faces = solid.Faces()
                
                edge_map = {}
                for f_id, f in enumerate(faces):
                    for e in f.Edges():
                        h = hash(e)
                        if h not in edge_map:
                            edge_map[h] = []
                        edge_map[h].append((e, f_id))
                        
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
                            
                        openings.append({
                            'id': len(openings),
                            'wire': w,
                            'surface_face_id': f_id,
                            'is_cylindrical': is_cyl,
                            'wall_faces': set(),
                            'pocket_faces': set()
                        })
                        
                def is_opening_edge(query_edge):
                    h = hash(query_edge)
                    for e_obj in opening_edge_hashes.get(h, []):
                        if query_edge.isSame(e_obj):
                            return True
                    return False

                for op in openings:
                    for e in op['wire'].Edges():
                        h = hash(e)
                        for e_obj, adj_f_id in edge_map.get(h, []):
                            if e.isSame(e_obj) and adj_f_id != op['surface_face_id']:
                                op['wall_faces'].add(adj_f_id)

                for op in openings:
                    queue = list(op['wall_faces'])
                    visited = set(queue)
                    
                    while queue:
                        curr_f_id = queue.pop(0)
                        op['pocket_faces'].add(curr_f_id)
                        
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

                parent = {op['id']: op['id'] for op in openings}
                
                def find(i):
                    if parent[i] == i: return i
                    parent[i] = find(parent[i])
                    return parent[i]
                    
                def union(i, j):
                    root_i = find(i)
                    root_j = find(j)
                    if root_i != root_j:
                        parent[root_i] = root_j

                for i in range(len(openings)):
                    for j in range(i + 1, len(openings)):
                        if openings[i]['pocket_faces'].intersection(openings[j]['pocket_faces']):
                            union(i, j)

                unique_pockets = {}
                for op in openings:
                    root = find(op['id'])
                    if root not in unique_pockets:
                        unique_pockets[root] = {
                            'is_cylindrical': True,
                            'openings': [],
                            'pocket_faces': set()
                        }
                    
                    if not op['is_cylindrical']:
                        unique_pockets[root]['is_cylindrical'] = False
                        
                    unique_pockets[root]['openings'].append(op)
                    unique_pockets[root]['pocket_faces'].update(op['pocket_faces'])

                small_holes_in_solid = 0
                for p_id, p_data in unique_pockets.items():
                    if p_data['is_cylindrical']:
                        op_wire = p_data['openings'][0]['wire']
                        try:
                            first_edge = op_wire.Edges()[0]
                            radius = first_edge.radius()
                            diameter = radius * 2
                            
                            if diameter < min_dia:
                                small_holes_in_solid += 1
                                issues.append({
                                    "solid_index": idx,
                                    "diameter_mm": round(diameter, 3)
                                })
                        except Exception:
                            pass

                solid_results.append({
                    "solid_index": idx,
                    "holes": sum(1 for p in unique_pockets.values() if p['is_cylindrical']),
                    "cutouts": sum(1 for p in unique_pockets.values() if not p['is_cylindrical']),
                    "small_holes": small_holes_in_solid
                })

            if issues:
                overall_status = "FAIL"

            total_holes = sum(sr["holes"] for sr in solid_results)

            if overall_status == "FAIL":
                return {
                    "check": self.name,
                    "status": overall_status,
                    "message": f"{len(issues)} hole(s) are below the minimum diameter ({min_dia} mm).",
                    "details": {
                        "holes_with_issues": issues,
                        "total_holes_found": total_holes,
                        "min_diameter_mm": min_dia,
                        "advice": "Increase hole diameter or verify tooling availability.",
                        "solids": solid_results
                    },
                }

            return {
                "check": self.name,
                "status": overall_status,
                "message": f"All {total_holes} hole(s) meet the minimum diameter ({min_dia} mm)." if total_holes > 0 else "No small holes detected.",
                "details": {
                    "total_holes_found": total_holes,
                    "min_diameter_mm": min_dia,
                    "solids": solid_results
                },
            }
        except Exception as exc:
            return {
                "check": self.name,
                "status": "ERROR",
                "message": str(exc),
                "details": {},
            }

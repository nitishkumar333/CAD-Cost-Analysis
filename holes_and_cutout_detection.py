import cadquery as cq
import argparse
import os

def analyze_pockets_in_step(step_file_path):
    if not os.path.exists(step_file_path):
        print(f"Error: File '{step_file_path}' does not exist.")
        return

    print(f"Loading STEP file: '{step_file_path}'...")
    try:
        model = cq.importers.importStep(step_file_path)
    except Exception as e:
        print(f"Failed to load STEP file. Error: {e}")
        return

    solids = model.solids().vals()
    if not solids:
        print("No solids were found in the provided STEP file.")
        return

    print(f"Successfully found {len(solids)} solid(s) in the model.\n")

    for idx, solid in enumerate(solids, start=1):
        faces = solid.Faces()
        
        # 1. Map edges to faces
        edge_map = {}
        for f_id, f in enumerate(faces):
            for e in f.Edges():
                h = hash(e)
                if h not in edge_map:
                    edge_map[h] = []
                edge_map[h].append((e, f_id))
                
        # 2. Find all "Openings"
        openings =[]
        opening_edge_hashes = {}
        
        for f_id, f in enumerate(faces):
            for w in f.innerWires():
                is_cyl = True
                for e in w.Edges():
                    if e.geomType() != "CIRCLE":
                        is_cyl = False
                    h = hash(e)
                    if h not in opening_edge_hashes:
                        opening_edge_hashes[h] =[]
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
            for e_obj in opening_edge_hashes.get(h,[]):
                if query_edge.isSame(e_obj):
                    return True
            return False

        # 3. Find immediate "wall faces" for each opening
        for op in openings:
            for e in op['wire'].Edges():
                h = hash(e)
                for e_obj, adj_f_id in edge_map.get(h,[]):
                    if e.isSame(e_obj) and adj_f_id != op['surface_face_id']:
                        op['wall_faces'].add(adj_f_id)

        # 4. BFS to map all pocket faces
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
                    for e_obj, adj_f_id in edge_map.get(h,[]):
                        if e.isSame(e_obj) and adj_f_id != curr_f_id:
                            if adj_f_id not in visited:
                                visited.add(adj_f_id)
                                queue.append(adj_f_id)

        # 5. Group openings into Distinct Features
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

        # 6. Aggregate Unique Pockets and Extract Dimensions
        unique_pockets = {}
        for op in openings:
            root = find(op['id'])
            if root not in unique_pockets:
                unique_pockets[root] = {
                    'is_cylindrical': True,
                    'openings':[],
                    'pocket_faces': set()
                }
            
            if not op['is_cylindrical']:
                unique_pockets[root]['is_cylindrical'] = False
                
            unique_pockets[root]['openings'].append(op)
            unique_pockets[root]['pocket_faces'].update(op['pocket_faces'])

        # Calculate dimensions & centers for each assembled pocket
        for p_id, p_data in unique_pockets.items():
            p_faces = [faces[f_id] for f_id in p_data['pocket_faces']]
            
            # --- Dimension Calculation via Bounding Box ---
            if p_faces:
                bb = p_faces[0].BoundingBox()
                xmin, ymin, zmin = bb.xmin, bb.ymin, bb.zmin
                xmax, ymax, zmax = bb.xmax, bb.ymax, bb.zmax
                
                # Expand composite bounding box with all pocket faces
                for f in p_faces[1:]:
                    bb_f = f.BoundingBox()
                    xmin = min(xmin, bb_f.xmin)
                    ymin = min(ymin, bb_f.ymin)
                    zmin = min(zmin, bb_f.zmin)
                    xmax = max(xmax, bb_f.xmax)
                    ymax = max(ymax, bb_f.ymax)
                    zmax = max(zmax, bb_f.zmax)
                    
                dx, dy, dz = xmax - xmin, ymax - ymin, zmax - zmin
                p_data['dimensions'] = (dx, dy, dz)
                p_data['bounding_volume'] = dx * dy * dz
                
                # Calculate the 3D Volumetric center of the pocket void
                p_data['center'] = ((xmin + xmax) / 2.0, (ymin + ymax) / 2.0, (zmin + zmax) / 2.0)
            else:
                p_data['dimensions'] = (0, 0, 0)
                p_data['bounding_volume'] = 0
                p_data['center'] = (0, 0, 0)
                
            # --- Extract specific properties & Opening Center ---
            op_wire = p_data['openings'][0]['wire']
            
            # Get the exact X,Y,Z of the opening on the surface (Drill entry point)
            op_center = op_wire.Center()
            p_data['opening_center'] = (op_center.x, op_center.y, op_center.z)

            if p_data['is_cylindrical']:
                try:
                    first_edge = op_wire.Edges()[0]
                    p_data['radius'] = first_edge.radius()
                    p_data['diameter'] = p_data['radius'] * 2
                except Exception:
                    p_data['diameter'] = None
            else:
                bb_w = op_wire.BoundingBox()
                p_data['opening_profile'] = (bb_w.xmax - bb_w.xmin, bb_w.ymax - bb_w.ymin, bb_w.zmax - bb_w.zmin)

        # Count up the results
        cyl_count = sum(1 for p in unique_pockets.values() if p['is_cylindrical'])
        non_cyl_count = sum(1 for p in unique_pockets.values() if not p['is_cylindrical'])
        total = cyl_count + non_cyl_count

        # 7. Print Cleanly
        print(f"--- Solid {idx} ---")
        print(f"Total Unique Pockets/Holes : {total}")
        if total > 0:
            print(f"  -> Holes         : {cyl_count}")
            print(f"  -> Cutouts       : {non_cyl_count}\n")
            
            # for p_id, p_data in unique_pockets.items():
            #     p_type = "Cylindrical" if p_data['is_cylindrical'] else "Non-Cylindrical"
                
            #     is_through = len(p_data['openings']) > 1
            #     hole_type = "Through-Hole" if is_through else "Blind Pocket"
                
            #     print(f"  * Feature {p_id} ({p_type} {hole_type}):")
                
            #     # --- Print Coordinate Centers ---
            #     op_cen = p_data['opening_center']
            #     print(f"      Opening Center  : (X: {op_cen[0]:.3f}, Y: {op_cen[1]:.3f}, Z: {op_cen[2]:.3f}) mm")
                
            #     vol_cen = p_data['center']
            #     print(f"      Pocket 3D Center: (X: {vol_cen[0]:.3f}, Y: {vol_cen[1]:.3f}, Z: {vol_cen[2]:.3f}) mm")
                
            #     # --- Print Dimensions ---
            #     if p_data['is_cylindrical'] and p_data.get('diameter'):
            #         print(f"      Diameter        : {p_data['diameter']:.3f} mm")
            #     else:
            #         prof = p_data.get('opening_profile', (0,0,0))
            #         print(f"      Opening Bounds  : {prof[0]:.3f} x {prof[1]:.3f} x {prof[2]:.3f} mm")
                
            #     dims = p_data['dimensions']
            #     print(f"      Overall 3D BBox : {dims[0]:.3f} x {dims[1]:.3f} x {dims[2]:.3f} mm")
            #     print(f"      BBox Volume     : {p_data['bounding_volume']:.3f} mm³")
            #     print("")
        else:
            print("  * No internal pockets or holes detected on this solid.\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Analyze a STEP file to count unique pockets and through-holes using Feature Recognition."
    )
    parser.add_argument("step_file", type=str, help="Path to the .step file")
    
    args = parser.parse_args()
    analyze_pockets_in_step(args.step_file)
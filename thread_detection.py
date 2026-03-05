import cadquery as cq
import numpy as np
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.GeomAbs import GeomAbs_Line, GeomAbs_Circle, GeomAbs_Ellipse

def is_helical(edge_obj):
    """
    Determines if an edge is non-planar (helical) by performing PCA on sampled points.
    A true helix is strictly 3D, meaning its smallest variance (eigenvalue) is significantly non-zero.
    """
    try:
        adaptor = BRepAdaptor_Curve(edge_obj.wrapped)
        ctype = adaptor.GetType()
        
        # Simple analytic geometries are definitively planar or 1D
        if ctype in[GeomAbs_Line, GeomAbs_Circle, GeomAbs_Ellipse]:
            return False
            
        first = adaptor.FirstParameter()
        last = adaptor.LastParameter()
        
        if np.isnan(first) or np.isnan(last) or first == last:
            return False
            
        # Sample points evenly along the parameter space
        pts =[]
        for t in np.linspace(first, last, 20):
            p = adaptor.Value(t)
            pts.append([p.X(), p.Y(), p.Z()])
            
        pts = np.array(pts)
        mean = np.mean(pts, axis=0)
        pts_centered = pts - mean
        
        # Covariance matrix and eigenvalues
        cov = np.cov(pts_centered.T)
        eigenvalues, _ = np.linalg.eig(cov)
        eigenvalues = np.sort(np.abs(eigenvalues))
        
        # Check ratio of smallest to largest variance
        # A threshold of 1e-4 confidently separates flat planar curves from 3D helical curves
        if eigenvalues[0] / max(eigenvalues[2], 1e-10) > 1e-4:
            return True
            
    except Exception:
        # Catch exceptions if OpenCASCADE struggles to evaluate a degenerate parameter space
        pass
        
    return False

def get_face_edges_wrapped(face):
    """ Helper to get underlying OCP Edge objects for fast comparison """
    return [e.wrapped for e in face.Edges()]

def faces_share_edge(edges1_wrapped, edges2_wrapped):
    """ Check if two faces share at least one physical edge via OpenCASCADE IsSame """
    for e1 in edges1_wrapped:
        for e2 in edges2_wrapped:
            if e1.IsSame(e2):
                return True
    return False

def detect_threads(step_file_path):
    print(f"Loading model from: {step_file_path}")
    try:
        model = cq.importers.importStep(step_file_path)
    except Exception as e:
        print(f"Failed to load STEP file: {e}")
        return
        
    solids = model.solids().vals()
    print(f"Found {len(solids)} solid(s).")
    
    # Treat each solid individually
    for s_idx, solid in enumerate(solids):
        print(f"\n--- Analyzing Solid {s_idx + 1} ---")
        
        # 1. Collect faces that have at least one helical edge
        helical_faces =[]
        for face in solid.Faces():
            if any(is_helical(e) for e in face.Edges()):
                helical_faces.append(face)
                
        if not helical_faces:
            print("No explicitly modeled threads detected in this solid.")
            continue
            
        print(f"Detected {len(helical_faces)} faces with helical characteristics. Clustering into thread regions...")
        
        # 2. Build adjacency graph (Faces are adjacent if they share an edge)
        face_edges_map = {f: get_face_edges_wrapped(f) for f in helical_faces}
        visited = set()
        thread_clusters =[]
        
        for f in helical_faces:
            if f in visited:
                continue
                
            # Breadth-First Search (BFS) to find all connected thread faces
            cluster = []
            queue = [f]
            visited.add(f)
            
            while queue:
                curr = queue.pop(0)
                cluster.append(curr)
                curr_edges = face_edges_map[curr]
                
                for other_f in helical_faces:
                    if other_f not in visited:
                        if faces_share_edge(curr_edges, face_edges_map[other_f]):
                            visited.add(other_f)
                            queue.append(other_f)

            if len(cluster) > 5:                
                thread_clusters.append(cluster)
            
        print(f"Identified {len(thread_clusters)} discrete thread(s).")
        
        # 3. Calculate position/dimensions for each detected thread region
        for c_idx, cluster in enumerate(thread_clusters):
            min_pts =[]
            max_pts =[]
            
            for f in cluster:
                bb = f.BoundingBox()
                min_pts.append([bb.xmin, bb.ymin, bb.zmin])
                max_pts.append([bb.xmax, bb.ymax, bb.zmax])
                
            min_pt = np.min(min_pts, axis=0)
            max_pt = np.max(max_pts, axis=0)
            center = (min_pt + max_pt) / 2.0
            
            # Approximate outer bounds
            length_x = max_pt[0] - min_pt[0]
            length_y = max_pt[1] - min_pt[1]
            length_z = max_pt[2] - min_pt[2]
            
            print(f"  Thread {c_idx + 1}:")
            print(f"    Center Position: X={center[0]:.3f}, Y={center[1]:.3f}, Z={center[2]:.3f}")
            print(f"    Bounding Box: Min={np.round(min_pt, 3)}, Max={np.round(max_pt, 3)}")
            print(f"    Dimensions: {length_x:.3f} x {length_y:.3f} x {length_z:.3f}")
            print(f"    Comprised of {len(cluster)} faces")

if __name__ == "__main__":
    # Replace this string with the path to your STEP file
    step_path = "/home/nitishkumar/Desktop/dfm/original_step_files/405-00089-00.step"
    detect_threads(step_path)
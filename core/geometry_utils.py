import cadquery as cq
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.GeomAbs import GeomAbs_Circle

def group_cylinders(cyl_list: list) -> list:
    """
    Group cylinder face descriptors that share the same axis
    (same direction vector and collinear location).
    """
    groups = []
    for c in cyl_list:
        pc_loc = cq.Vector(c["loc"])
        pc_dir = cq.Vector(c["dir"])
        placed = False
        for g in groups:
            ref = g[0]
            ref_loc = cq.Vector(ref["loc"])
            ref_dir = cq.Vector(ref["dir"])
            if pc_dir.cross(ref_dir).Length < 1e-3:
                vec = pc_loc - ref_loc
                if vec.cross(ref_dir).Length < 1e-3:
                    g.append(c)
                    placed = True
                    break
        if not placed:
            groups.append([c])
    return groups

def classify_cylinders(cyl_faces: list) -> tuple[list, list]:
    """
    Split cylinder faces into closed (full-revolution) and partial lists.
    Returns (closed_cyls, partial_cyls) where each item is a dict with
    keys: radius, area, loc, dir, face.
    """
    closed_cyls = []
    partial_cyls = []

    for face in cyl_faces:
        surf = BRepAdaptor_Surface(face.wrapped, True)
        is_closed = surf.IsUClosed() or surf.IsVClosed()

        cylinder = surf.Cylinder()
        radius = cylinder.Radius()
        axis = cylinder.Axis()
        loc = axis.Location()
        dir_ = axis.Direction()

        item = {
            "radius": radius,
            "area": face.Area(),
            "loc": (loc.X(), loc.Y(), loc.Z()),
            "dir": (dir_.X(), dir_.Y(), dir_.Z()),
            "face": face,
        }
        (closed_cyls if is_closed else partial_cyls).append(item)

    return closed_cyls, partial_cyls

def solid_bbox(solid_shape) -> tuple[float, float, float, float, float, float]:
    """
    Return (xmin, ymin, zmin, xmax, ymax, zmax) bounding box for a raw
    OCC TopoDS shape using CadQuery's BoundingBox helper.
    """
    bb = cq.Shape(solid_shape).BoundingBox()
    return bb.xmin, bb.ymin, bb.zmin, bb.xmax, bb.ymax, bb.zmax

def solid_volume_cm3(solid_shape) -> float:
    """Return volume in cm³ for a raw OCC TopoDS shape."""
    vol_mm3 = cq.Shape(solid_shape).Volume()
    return vol_mm3 / 1_000.0

def boxes_overlap(bb1, bb2, tol: float = 1e-3) -> bool:
    """
    Return True if two bounding-box tuples (xmin,ymin,zmin,xmax,ymax,zmax)
    overlap (share any volume within tolerance).
    """
    for i in range(3):
        if bb1[i + 3] + tol < bb2[i] or bb2[i + 3] + tol < bb1[i]:
            return False
    return True

def face_inner_wires(cq_face):
    """
    Return inner (hole) wires of a CadQuery Face as lists of CadQuery Edge objects.

    CadQuery's face.Wires() returns all wires; the first wire (by largest
    bounding-box area) is the outer boundary.  All remaining wires are inner
    loops (holes).
    """
    all_wires = cq_face.Wires()
    if len(all_wires) <= 1:
        return []

    def _wire_bbox_diag(w):
        bb = cq.Shape(w.wrapped).BoundingBox()
        return (bb.xlen ** 2 + bb.ylen ** 2 + bb.zlen ** 2) ** 0.5

    outer = max(all_wires, key=_wire_bbox_diag)
    return [w for w in all_wires if not w.wrapped.IsSame(outer.wrapped)]

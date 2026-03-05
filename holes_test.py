import math
from OCC.Core.STEPControl import STEPControl_Reader
from OCC.Core.IFSelect import IFSelect_RetDone
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_SOLID, TopAbs_FACE, TopAbs_WIRE, TopAbs_EDGE
from OCC.Core.BRepAdaptor import BRepAdaptor_Surface, BRepAdaptor_Curve
from OCC.Core.GeomAbs import GeomAbs_Plane, GeomAbs_Circle
from OCC.Core.BRepTools import breptools
from OCC.Core.BRepBndLib import brepbndlib
from OCC.Core.Bnd import Bnd_Box

TOL = 1e-4


def load_step(filepath):
    reader = STEPControl_Reader()
    status = reader.ReadFile(filepath)
    if status != IFSelect_RetDone:
        raise Exception("STEP read failed")
    reader.TransferRoots()
    return reader.OneShape()


def distance(p1, p2):
    return math.sqrt(
        (p1[0] - p2[0])**2 +
        (p1[1] - p2[1])**2 +
        (p1[2] - p2[2])**2
    )


# --------------------------------------------------
# AXIS CHECK ONLY (NO RADIUS CHECK)
# --------------------------------------------------
def is_same_axis(new_center, hole_group):
    old_center = hole_group["axis_point"]
    old_normal = hole_group["axis_dir"]

    vec = (
        new_center[0] - old_center[0],
        new_center[1] - old_center[1],
        new_center[2] - old_center[2]
    )

    dot = vec[0]*old_normal[0] + vec[1]*old_normal[1] + vec[2]*old_normal[2]

    projected = (
        old_center[0] + dot*old_normal[0],
        old_center[1] + dot*old_normal[1],
        old_center[2] + dot*old_normal[2]
    )

    return distance(projected, new_center) < TOL


# --------------------------------------------------
# GROUP HOLES BY AXIS
# --------------------------------------------------
def group_hole(circle_center, circle_radius, normal_vec, hole_groups):

    for group in hole_groups:
        if is_same_axis(circle_center, group):
            group["sections"].append({
                "center": circle_center,
                "radius": circle_radius
            })
            return

    # Create new group if no axis match
    hole_groups.append({
        "axis_point": circle_center,
        "axis_dir": normal_vec,
        "sections": [{
            "center": circle_center,
            "radius": circle_radius
        }]
    })


# --------------------------------------------------
# AXIS PROJECTION
# --------------------------------------------------
def axis_projection(center, axis_point, axis_dir):
    vec = (
        center[0] - axis_point[0],
        center[1] - axis_point[1],
        center[2] - axis_point[2]
    )
    return vec[0]*axis_dir[0] + vec[1]*axis_dir[1] + vec[2]*axis_dir[2]



from OCC.Core.GeomAbs import GeomAbs_Cylinder, GeomAbs_Cone


def detect_axis_surfaces(solid, axis_dir):

    face_explorer = TopExp_Explorer(solid, TopAbs_FACE)

    cylinder_count = 0
    cone_count = 0

    while face_explorer.More():

        face = face_explorer.Current()
        surf = BRepAdaptor_Surface(face)

        surf_type = surf.GetType()

        if surf_type == GeomAbs_Cylinder:
            cyl = surf.Cylinder()
            dir_vec = cyl.Axis().Direction()

            cyl_axis = (dir_vec.X(), dir_vec.Y(), dir_vec.Z())

            # Axis alignment check
            dot = abs(
                cyl_axis[0]*axis_dir[0] +
                cyl_axis[1]*axis_dir[1] +
                cyl_axis[2]*axis_dir[2]
            )

            if dot > 0.99:
                cylinder_count += 1

        elif surf_type == GeomAbs_Cone:
            cone = surf.Cone()
            dir_vec = cone.Axis().Direction()

            cone_axis = (dir_vec.X(), dir_vec.Y(), dir_vec.Z())

            dot = abs(
                cone_axis[0]*axis_dir[0] +
                cone_axis[1]*axis_dir[1] +
                cone_axis[2]*axis_dir[2]
            )

            if dot > 0.99:
                cone_count += 1

        face_explorer.Next()

    return cylinder_count, cone_count


# --------------------------------------------------
# CLASSIFY HOLE TYPE
# --------------------------------------------------
def classify_hole(group, solid):

    axis_point = group["axis_point"]
    axis_dir = group["axis_dir"]

    radii = list(set([round(sec["radius"], 6) for sec in group["sections"]]))
    radii.sort()

    projections = [
        axis_projection(sec["center"], axis_point, axis_dir)
        for sec in group["sections"]
    ]
    hole_depth = max(projections) - min(projections)

    cyl_count, cone_count = detect_axis_surfaces(solid, axis_dir)

    # ---- Detect threads ----
    if cyl_count == 1 and cone_count == 0:
        geometry = "Straight Hole"
    elif cyl_count >= 1 and cone_count >= 1:
        geometry = "Countersink Hole"
    elif cyl_count == 2 and cone_count == 0:
        geometry = "Counterbore Hole"
    elif cyl_count > 2:
        geometry = "Stepped Hole"
    else:
        geometry = "Complex Hole"

    return f"{geometry}", hole_depth



# --------------------------------------------------
# MAIN DETECTION FUNCTION
# --------------------------------------------------
def detect_holes_by_solid(shape):

    solid_explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    solid_index = 1

    while solid_explorer.More():

        solid = solid_explorer.Current()
        print(f"==============================")
        print(f"Processing Solid {solid_index}")
        print(f"==============================")

        hole_groups = []

        face_explorer = TopExp_Explorer(solid, TopAbs_FACE)

        while face_explorer.More():
            face = face_explorer.Current()
            surf = BRepAdaptor_Surface(face)

            if surf.GetType() != GeomAbs_Plane:
                face_explorer.Next()
                continue

            plane = surf.Plane()
            normal = plane.Axis().Direction()

            normal_vec = (
                normal.X(),
                normal.Y(),
                normal.Z()
            )

            outer_wire = breptools.OuterWire(face)
            wire_explorer = TopExp_Explorer(face, TopAbs_WIRE)

            while wire_explorer.More():
                wire = wire_explorer.Current()

                if wire.IsSame(outer_wire):
                    wire_explorer.Next()
                    continue

                edge_explorer = TopExp_Explorer(wire, TopAbs_EDGE)

                is_circle = True
                circle_center = None
                circle_radius = None

                while edge_explorer.More():
                    edge = edge_explorer.Current()
                    curve = BRepAdaptor_Curve(edge)

                    if curve.GetType() != GeomAbs_Circle:
                        is_circle = False
                        break

                    circle = curve.Circle()
                    center = circle.Location()

                    circle_center = (
                        center.X(),
                        center.Y(),
                        center.Z()
                    )

                    circle_radius = circle.Radius()

                    edge_explorer.Next()

                if is_circle and circle_center:

                    # GROUP BY AXIS (NO RADIUS CHECK)
                    group_hole(circle_center,
                               circle_radius,
                               normal_vec,
                               hole_groups)

                wire_explorer.Next()

            face_explorer.Next()

        # --------------------------------------------------
        # PRINT RESULTS
        # --------------------------------------------------
        print("\nDetected Hole Features:")

        for idx, group in enumerate(hole_groups, 1):

            hole_type, depth = classify_hole(group, solid)

            print(f"\nHole {idx}")
            print("Axis:", group["axis_dir"])
            print("Radii:", [sec["radius"] for sec in group["sections"]])
            print("Hole Type:", hole_type)
            print("Depth:", round(depth, 4))

        print(f"\nTotal Hole Features in Solid {solid_index}: {len(hole_groups)}")

        solid_index += 1
        solid_explorer.Next()


if __name__ == "__main__":
    shape = load_step("/home/nitishkumar/Desktop/dfm/original_step_files/405-00089-00.step")

    detect_holes_by_solid(shape)
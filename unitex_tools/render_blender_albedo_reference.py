"""Render transparent, shadow-free albedo references with Blender.

Run with Blender, for example:
    blender --background --python render_blender_albedo_reference.py -- \
        input.obj output_dir
"""

from __future__ import annotations

import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import bpy
from mathutils import Vector


VIEWS = {
    "front": (0.0, -3.0, 0.35),
    "front_left": (-2.2, -2.2, 1.15),
    "front_right": (2.2, -2.2, 1.15),
    "front_right_mid": (2.2, -2.2, 0.85),
    "front_right_low": (2.2, -2.2, 0.60),
    "front_right_eye_side": (3.0, -1.5, 1.0),
    "front_right_eye_side_soft": (2.8, -1.8, 1.0),
    "front_right_near_side": (4.0, -0.9, 1.0),
    "front_right_near_side_flat": (4.0, -0.5, 1.0),
    "front_right_tail_side": (1.8, -2.8, 1.0),
    "left": (-3.0, 0.0, 0.35),
    "right": (3.0, 0.0, 0.35),
    "back_left": (-2.2, 2.2, 1.15),
    "back_right": (2.2, 2.2, 1.15),
    "back": (0.0, 3.0, 0.35),
}


def import_voxelshop_dae(path: Path) -> bpy.types.Object:
    """Import the compact VoxelShop COLLADA asset without Blender's add-on."""
    root = ET.parse(path).getroot()
    geometry_mesh = root.find(".//{*}library_geometries/{*}geometry/{*}mesh")
    if geometry_mesh is None:
        raise ValueError("COLLADA file has no mesh")

    arrays: dict[str, list[float]] = {}
    for source in geometry_mesh.findall("{*}source"):
        values = source.find("{*}float_array")
        if values is not None and values.text:
            arrays[source.attrib["id"]] = [float(value) for value in values.text.split()]

    vertices_node = geometry_mesh.find("{*}vertices")
    position_input = vertices_node.find("{*}input[@semantic='POSITION']")
    position_source = position_input.attrib["source"].lstrip("#")
    positions_flat = arrays[position_source]
    positions = [tuple(positions_flat[i : i + 3]) for i in range(0, len(positions_flat), 3)]

    polylist = geometry_mesh.find("{*}polylist")
    if polylist is None:
        raise ValueError("COLLADA mesh has no polylist")
    inputs = polylist.findall("{*}input")
    stride = max(int(node.attrib["offset"]) for node in inputs) + 1
    vertex_offset = int(next(node for node in inputs if node.attrib["semantic"] == "VERTEX").attrib["offset"])
    texcoord_node = next(node for node in inputs if node.attrib["semantic"] == "TEXCOORD")
    texcoord_offset = int(texcoord_node.attrib["offset"])
    texcoord_source = texcoord_node.attrib["source"].lstrip("#")
    uv_flat = arrays[texcoord_source]
    uvs = [tuple(uv_flat[i : i + 2]) for i in range(0, len(uv_flat), 2)]
    counts = [int(value) for value in polylist.find("{*}vcount").text.split()]
    indices = [int(value) for value in polylist.find("{*}p").text.split()]

    faces: list[list[int]] = []
    face_uvs: list[list[int]] = []
    cursor = 0
    for count in counts:
        block = indices[cursor : cursor + count * stride]
        faces.append([block[i * stride + vertex_offset] for i in range(count)])
        face_uvs.append([block[i * stride + texcoord_offset] for i in range(count)])
        cursor += count * stride

    scale_node = root.find(".//{*}library_visual_scenes/{*}visual_scene/{*}node/{*}scale")
    scale = [1.0, 1.0, 1.0]
    if scale_node is not None and scale_node.text:
        scale = [float(value) for value in scale_node.text.split()]
    positions = [tuple(value * scale[axis] for axis, value in enumerate(point)) for point in positions]

    mesh = bpy.data.meshes.new(path.stem)
    mesh.from_pydata(positions, [], faces)
    mesh.update()
    uv_layer = mesh.uv_layers.new(name="UVMap")
    for polygon, polygon_uvs in zip(mesh.polygons, face_uvs):
        for loop_index, uv_index in zip(polygon.loop_indices, polygon_uvs):
            uv_layer.data[loop_index].uv = uvs[uv_index]

    image_ref = root.find(".//{*}library_images/{*}image/{*}init_from")
    if image_ref is None or not image_ref.text:
        raise ValueError("COLLADA file has no diffuse image")
    image = bpy.data.images.load(str((path.parent / image_ref.text).resolve()))
    # VoxelShop's atlas bakes a mid-grey shade into some otherwise white
    # surfaces.  For a shadow-free albedo reference, collapse only bright
    # neutral shades to the brightest neutral value while preserving black and
    # chromatic colours.  The source image on disk is never modified.
    image = image.copy()
    image.name = f"{path.stem}_neutral_albedo"
    pixels = list(image.pixels)
    neutral_indices = []
    target = 0.0
    for index in range(0, len(pixels), 4):
        rgb = pixels[index : index + 3]
        if max(rgb) - min(rgb) < 0.02 and max(rgb) > 0.20:
            neutral_indices.append(index)
            target = max(target, max(rgb))
    for index in neutral_indices:
        pixels[index : index + 3] = [target, target, target]
    image.pixels[:] = pixels
    image.update()
    material = bpy.data.materials.new(f"{path.stem}_material")
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    principled = nodes.get("Principled BSDF")
    texture = nodes.new("ShaderNodeTexImage")
    texture.image = image
    texture.interpolation = "Closest"
    links.new(texture.outputs["Color"], principled.inputs["Base Color"])
    mesh.materials.append(material)

    obj = bpy.data.objects.new(path.stem, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def make_materials_softly_lit() -> None:
    for material in bpy.data.materials:
        material.use_nodes = True
        nodes = material.node_tree.nodes
        links = material.node_tree.links
        image_node = next((node for node in nodes if node.type == "TEX_IMAGE"), None)
        image = image_node.image if image_node is not None else None
        diffuse_colour = tuple(material.diffuse_color)
        nodes.clear()
        output = nodes.new("ShaderNodeOutputMaterial")
        principled = nodes.new("ShaderNodeBsdfPrincipled")
        principled.inputs["Roughness"].default_value = 1.0
        if "Specular IOR Level" in principled.inputs:
            principled.inputs["Specular IOR Level"].default_value = 0.0
        if "Emission Strength" in principled.inputs:
            principled.inputs["Emission Strength"].default_value = 0.55
        texture = None
        if image is not None:
            texture = nodes.new("ShaderNodeTexImage")
            texture.image = image
            texture.interpolation = "Closest"
            links.new(texture.outputs["Color"], principled.inputs["Base Color"])
            if "Emission Color" in principled.inputs:
                links.new(texture.outputs["Color"], principled.inputs["Emission Color"])
        else:
            principled.inputs["Base Color"].default_value = diffuse_colour
            if "Emission Color" in principled.inputs:
                principled.inputs["Emission Color"].default_value = diffuse_colour
        links.new(principled.outputs["BSDF"], output.inputs["Surface"])


def add_shadowless_area_light(
    name: str,
    location: tuple[float, float, float],
    target: Vector,
    energy: float,
) -> None:
    data = bpy.data.lights.new(name, type="AREA")
    data.energy = energy
    data.shape = "DISK"
    data.size = 5.0
    data.use_shadow = False
    light = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(light)
    light.location = location
    light.rotation_euler = (target - light.location).to_track_quat("-Z", "Y").to_euler()


def add_soft_lighting(points: list[Vector]) -> None:
    centre = sum(points, Vector()) / len(points)
    add_shadowless_area_light("Key", (4.0, -6.0, 7.0), centre, 180.0)
    add_shadowless_area_light("Fill", (-5.0, -3.0, 4.0), centre, 140.0)
    add_shadowless_area_light("Rim", (1.0, 5.0, 6.0), centre, 100.0)


def separate_coplanar_colour_details(objects: list[bpy.types.Object]) -> None:
    """Nudge non-white palette faces outward to remove decal z-fighting.

    The source OBJ stores eyes, wings and other coloured panels exactly on top
    of the white body surface.  The tiny geometric offset only resolves that
    ambiguous depth ordering; it is far below one output pixel and does not
    alter the visible form.
    """
    epsilon = 0.0005
    for obj in objects:
        mesh = obj.data
        uv_layer = mesh.uv_layers.active
        if uv_layer is None or not obj.material_slots:
            continue
        material = obj.material_slots[0].material
        image_node = next(
            (node for node in material.node_tree.nodes if node.type == "TEX_IMAGE"),
            None,
        )
        if image_node is None or image_node.image is None:
            continue
        image = image_node.image
        width, height = image.size
        pixels = list(image.pixels)
        moved = 0
        for polygon in mesh.polygons:
            uv = sum(
                (uv_layer.data[index].uv for index in polygon.loop_indices),
                Vector((0.0, 0.0)),
            ) / len(polygon.loop_indices)
            x = min(max(round(uv.x * (width - 1)), 0), width - 1)
            y = min(max(round(uv.y * (height - 1)), 0), height - 1)
            start = (y * width + x) * 4
            rgb = pixels[start : start + 3]
            if min(rgb) > 0.90:
                continue
            delta = polygon.normal * epsilon
            for vertex_index in polygon.vertices:
                mesh.vertices[vertex_index].co += delta
            moved += 1
        mesh.update()
        print(f"coplanar_detail_offset object={obj.name} faces={moved}")


def world_bounds(objects: list[bpy.types.Object]) -> list[Vector]:
    points: list[Vector] = []
    for obj in objects:
        points.extend(obj.matrix_world @ Vector(corner) for corner in obj.bound_box)
    return points


def configure_camera(camera: bpy.types.Object, direction: tuple[float, float, float], points: list[Vector]) -> None:
    centre = sum(points, Vector()) / len(points)
    direction_vector = Vector(direction).normalized()
    camera.location = centre + direction_vector * 5.0
    camera.rotation_euler = (-direction_vector).to_track_quat("-Z", "Y").to_euler()

    inverse_rotation = camera.rotation_euler.to_matrix().inverted()
    camera_space = [inverse_rotation @ (point - centre) for point in points]
    width = max(point.x for point in camera_space) - min(point.x for point in camera_space)
    height = max(point.y for point in camera_space) - min(point.y for point in camera_space)
    camera.data.ortho_scale = max(height, width) / 0.90


def main() -> None:
    args = sys.argv[sys.argv.index("--") + 1 :]
    if len(args) != 2:
        raise SystemExit("expected: input.(obj|dae) output_dir")
    input_path = Path(args[0]).resolve()
    output_dir = Path(args[1]).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    if input_path.suffix.lower() == ".dae":
        import_voxelshop_dae(input_path)
    elif input_path.suffix.lower() == ".obj":
        bpy.ops.wm.obj_import(
            filepath=str(input_path), forward_axis="NEGATIVE_Y", up_axis="Z"
        )
    else:
        raise ValueError(f"unsupported input format: {input_path.suffix}")
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    if not meshes:
        raise RuntimeError("model import produced no mesh objects")
    if input_path.suffix.lower() == ".obj":
        separate_coplanar_colour_details(meshes)
    make_materials_softly_lit()

    camera_data = bpy.data.cameras.new("ReferenceCamera")
    camera_data.type = "ORTHO"
    camera_data.lens = 50
    camera = bpy.data.objects.new("ReferenceCamera", camera_data)
    bpy.context.scene.collection.objects.link(camera)
    bpy.context.scene.camera = camera

    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = 1024
    scene.render.resolution_y = 1024
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.film_transparent = True
    scene.render.image_settings.color_depth = "8"
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "Medium High Contrast"
    scene.view_settings.exposure = 0
    scene.view_settings.gamma = 1

    points = world_bounds(meshes)
    add_soft_lighting(points)
    for name, direction in VIEWS.items():
        configure_camera(camera, direction, points)
        scene.render.filepath = str(output_dir / f"reference_{name}.png")
        bpy.ops.render.render(write_still=True)
        print(f"rendered={name} path={scene.render.filepath}")


if __name__ == "__main__":
    main()

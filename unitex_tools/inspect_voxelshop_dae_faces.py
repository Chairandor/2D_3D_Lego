"""List palette-coloured faces from a compact VoxelShop COLLADA mesh."""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from PIL import Image


path = Path(sys.argv[1])
root = ET.parse(path).getroot()
mesh = root.find(".//{*}library_geometries/{*}geometry/{*}mesh")
sources = {
    source.attrib["id"]: [float(value) for value in source.find("{*}float_array").text.split()]
    for source in mesh.findall("{*}source")
}
vertices_node = mesh.find("{*}vertices")
position_source = vertices_node.find("{*}input[@semantic='POSITION']").attrib["source"].lstrip("#")
vertices = np.asarray(sources[position_source], dtype=float).reshape(-1, 3) * 0.05
polylist = mesh.find("{*}polylist")
inputs = polylist.findall("{*}input")
stride = max(int(node.attrib["offset"]) for node in inputs) + 1
vertex_offset = int(next(node for node in inputs if node.attrib["semantic"] == "VERTEX").attrib["offset"])
uv_input = next(node for node in inputs if node.attrib["semantic"] == "TEXCOORD")
uv_offset = int(uv_input.attrib["offset"])
uvs = np.asarray(sources[uv_input.attrib["source"].lstrip("#")], dtype=float).reshape(-1, 2)
counts = [int(value) for value in polylist.find("{*}vcount").text.split()]
packed = [int(value) for value in polylist.find("{*}p").text.split()]

faces = []
face_uvs = []
cursor = 0
for count in counts:
    block = packed[cursor : cursor + count * stride]
    faces.append([block[index * stride + vertex_offset] for index in range(count)])
    face_uvs.append([block[index * stride + uv_offset] for index in range(count)])
    cursor += count * stride
faces = np.asarray(faces)
face_uvs = np.asarray(face_uvs)

texture_ref = root.find(".//{*}library_images/{*}image/{*}init_from").text
texture = np.asarray(Image.open(path.parent / texture_ref).convert("RGB"))
height, width = texture.shape[:2]
uv_centres = uvs[face_uvs].mean(axis=1)
x = np.clip(np.rint(uv_centres[:, 0] * (width - 1)), 0, width - 1).astype(int)
y = np.clip(np.rint((1.0 - uv_centres[:, 1]) * (height - 1)), 0, height - 1).astype(int)
colours = texture[y, x]
flipped_y = np.clip(np.rint(uv_centres[:, 1] * (height - 1)), 0, height - 1).astype(int)
flipped_colours = texture[flipped_y, x]

triangles = vertices[faces]
centres = triangles.mean(axis=1)
raw_normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
normals = raw_normals / np.linalg.norm(raw_normals, axis=1, keepdims=True)

for index, (triangle, centre, normal, colour) in enumerate(
    zip(triangles, centres, normals, colours)
):
    if tuple(colour) not in {(208, 208, 208), (241, 241, 241)}:
        continue
    print(
        f"{index:03d} colour={tuple(colour)} flipped={tuple(flipped_colours[index])} centre={np.round(centre, 3).tolist()} "
        f"normal={np.round(normal, 3).tolist()} min={np.round(triangle.min(axis=0), 3).tolist()} "
        f"max={np.round(triangle.max(axis=0), 3).tolist()}"
    )

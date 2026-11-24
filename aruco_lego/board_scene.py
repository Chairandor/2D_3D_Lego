"""Models placed in the coordinate system of one ArUco marker."""
import json
import math

import numpy as np
import pyrender
from trimesh.transformations import euler_matrix


def placement_pose(x_cm: float, y_cm: float, angle: float) -> np.ndarray:
    angle = math.radians(angle)
    cos, sin = math.cos(angle), math.sin(angle)
    return np.array([[cos, -sin, 0, x_cm / 100],
                     [sin, cos, 0, y_cm / 100],
                     [0, 0, 1, 0],
                     [0, 0, 0, 1]], dtype=float)


def board_lighting(scene):
    scene.ambient_light = np.full(3, .66)
    for intensity, pitch, yaw in [(1.65, -28, -30), (.48, -12, 55)]:
        pose = euler_matrix(*np.deg2rad([pitch, yaw, 0]))
        scene.add(pyrender.DirectionalLight(color=np.ones(3), intensity=intensity), pose=pose)


def board_mesh(meshes):
    primitives = []
    for mesh in meshes:
        # The LDraw loader emits one mesh per uniform palette color. Use its
        # linear RGB as the plastic's albedo so highlights stay neutral.
        rgba = mesh.visual.vertex_colors[0].astype(float) / 255
        rgba[:3] **= 2.2  # Inverse of pyrender's display gamma.
        material = pyrender.MetallicRoughnessMaterial(
            baseColorFactor=rgba, metallicFactor=0, roughnessFactor=.38, doubleSided=True,
            alphaMode='OPAQUE' if rgba[3] == 1 else 'BLEND',
        )
        primitives.extend(pyrender.Mesh.from_trimesh(mesh, smooth=False, material=material).primitives)
    return pyrender.Mesh(primitives)


class BoardScene:
    def __init__(self, scene, control_file, load_meshes):
        self.scene = scene
        self.control_file = control_file
        self.load_meshes = load_meshes
        self.modified = None
        self.nodes = {}
        self.errors = {}

    def update(self):
        try:
            modified = self.control_file.stat().st_mtime_ns
            if modified == self.modified:
                return
            data = json.loads(self.control_file.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return
        self.modified = modified
        for item in data.get('placements', []):
            key = item['id']
            if key in self.nodes or key in self.errors:
                continue
            try:
                meshes = self.load_meshes(item['path'])
                pose = placement_pose(item['x_cm'], item['y_cm'], item['angle'])
                mesh = board_mesh(meshes)
                self.nodes[key] = self.scene.add(mesh), pose
            except Exception as error:
                self.errors[key] = f"{item['label']}：{error}"

    def set_pose(self, marker_pose):
        for node, placement in self.nodes.values():
            self.scene.set_pose(node, pose=marker_pose @ placement)

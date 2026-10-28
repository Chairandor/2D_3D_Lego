from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from project_config import terminate_process_tree
from unitex_tools.local_execution import execute


class LocalUniTEXTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.resources = self.root / "resources"
        for name in ("envs/unitex/bin/python", "UniTEX/pipeline.py"):
            path = self.resources / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        wrapper = self.root / "unitex_tools/run_unitex_external.py"
        wrapper.parent.mkdir()
        wrapper.touch()
        self.reference, self.mesh = self.root / "input.png", self.root / "mesh.glb"
        self.reference.touch()
        self.mesh.touch()
        self.output = self.root / "branch/unitex"
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def cache(output):
        cache = output / "cache"
        cache.mkdir(parents=True, exist_ok=True)
        for name in ("camera_info.pth", "processed_mesh.obj", "mv_rgb.png"):
            (cache / name).write_bytes(b"original")
        (output / "mv_rgb.png").write_bytes(b"original")

    def runner(self, command, **kwargs):
        self.calls.append((command, kwargs))
        phase = command[command.index("--phase") + 1]
        if phase == "stage1":
            self.cache(self.output)
        else:
            (self.output / "textured_mesh.glb").write_bytes(b"glb")

    def execute(self, phase="project", **kwargs):
        return execute(self.reference, self.mesh, self.output, root=self.root,
                       remote_root=str(self.resources), steps=28, seed=63,
                       projection="ltm", gpu_index=1, phase=phase, force=False,
                       run=self.runner, **kwargs)

    def test_projection_rebuilds_missing_cache_without_ssh(self):
        result = self.execute()
        self.assertEqual(result.read_bytes(), b"glb")
        self.assertEqual([c[c.index("--phase") + 1] for c, _ in self.calls], ["stage1", "project"])
        for command, options in self.calls:
            self.assertEqual(Path(command[0]).resolve(),
                             (self.resources / "envs/unitex/bin/python").resolve())
            self.assertEqual(options["env"]["CUDA_VISIBLE_DEVICES"], "1")
            self.assertEqual(options["env"]["HF_HUB_OFFLINE"], "1")

    def test_override_reuses_cache_without_changing_original(self):
        original = self.root / "original"
        self.cache(original / "unitex")
        sheet = self.root / "edited.png"
        sheet.write_bytes(b"edited")
        self.execute(six_view_override=sheet, cache_run_dir=original)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual((original / "unitex/cache/mv_rgb.png").read_bytes(), b"original")
        self.assertEqual((self.output / "cache/mv_rgb.png").read_bytes(), b"edited")

    def test_existing_review_without_camera_cache_is_not_a_complete_stage(self):
        self.output.mkdir(parents=True)
        (self.output / "mv_rgb.png").touch()
        self.execute(phase="stage1")
        self.assertEqual(len(self.calls), 1)
        self.assertTrue((self.output / "cache/camera_info.pth").is_file())

    def test_completed_projection_is_reused(self):
        self.cache(self.output)
        (self.output / "textured_mesh.glb").write_bytes(b"ready")
        self.assertEqual(self.execute().read_bytes(), b"ready")
        self.assertEqual(self.calls, [])


@unittest.skipIf(os.name == "nt", "POSIX job sessions")
class ProcessTreeTests(unittest.TestCase):
    def test_stopping_job_also_stops_its_child(self):
        with tempfile.TemporaryDirectory() as temporary:
            signal_file = Path(temporary) / "child-stopped"
            child = "import signal,time,pathlib; signal.signal(signal.SIGTERM,lambda *_:pathlib.Path(" + repr(str(signal_file)) + ").touch()); print('ready',flush=True); time.sleep(30)"
            parent = "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-u','-c'," + repr(child) + "],stdout=subprocess.PIPE,text=True); print(p.stdout.readline(),flush=True); time.sleep(30)"
            process = subprocess.Popen([sys.executable, "-u", "-c", parent],
                                       stdout=subprocess.PIPE, text=True, start_new_session=True)
            try:
                self.assertEqual(process.stdout.readline().strip(), "ready")
                terminate_process_tree(process)
                process.wait(timeout=5)
                deadline = time.monotonic() + 5
                while not signal_file.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(signal_file.is_file(), "child did not receive the stop signal")
            finally:
                try:
                    os.killpg(process.pid, 9)
                except ProcessLookupError:
                    pass
                process.stdout.close()

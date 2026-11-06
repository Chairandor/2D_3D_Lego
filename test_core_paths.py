"""覆盖单仓库克隆、已有相邻核心以及显式路径配置。"""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from project_config import resolve_core_root
from ui_aruco import ArucoService


class CorePathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.parent = Path(self.directory.name)
        self.root = self.parent / "project"
        self.root.mkdir()
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        for key in ("LEGOLIZER_ROOT", "ARUCO_ROOT", "ARUCO_PYTHON"):
            os.environ.pop(key, None)

    def test_standalone_clone_uses_bundled_core_and_ar(self) -> None:
        (self.root / "src/legolizer").mkdir(parents=True)
        self.assertEqual(resolve_core_root(self.root), self.root.resolve())
        service = ArucoService(self.root)
        self.assertEqual(service.core, self.root.resolve())
        self.assertEqual(service.ar_root, (self.root / "aruco_lego").resolve())

    def test_existing_parent_layout_keeps_its_core(self) -> None:
        for directory in (self.parent, self.root):
            (directory / "src/legolizer").mkdir(parents=True)
        self.assertEqual(resolve_core_root(self.root), self.parent.resolve())

    def test_explicit_relative_root_overrides_parent(self) -> None:
        (self.parent / "src/legolizer").mkdir(parents=True)
        os.environ["LEGOLIZER_ROOT"] = "."
        self.assertEqual(resolve_core_root(self.root), self.root.resolve())
        self.assertEqual(ArucoService(self.root).core, self.root.resolve())

    def test_explicit_absolute_root_is_respected(self) -> None:
        external = self.parent / "configured-core"
        os.environ["LEGOLIZER_ROOT"] = str(external)
        self.assertEqual(resolve_core_root(self.root), external.resolve())

    def test_missing_core_retains_legacy_fallback(self) -> None:
        self.assertEqual(resolve_core_root(self.root), self.parent.resolve())


if __name__ == "__main__":
    unittest.main()

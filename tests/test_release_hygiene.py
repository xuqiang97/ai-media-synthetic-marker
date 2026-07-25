from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import tomllib
import unittest
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = PROJECT_ROOT / "scripts" / "build_release.py"


def load_build_release():
    if not BUILD_SCRIPT.is_file():
        raise unittest.SkipTest("scripts/build_release.py 尚未就绪")
    spec = importlib.util.spec_from_file_location(
        "ai_media_marker_build_release_tests",
        BUILD_SCRIPT,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载构建脚本：{BUILD_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_placeholder(path: Path, data: bytes = b"test placeholder") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def make_clean_release_stage(parent: Path) -> Path:
    """Create a structure-only release stage with no executable media."""

    stage = parent / "ai-media-synthetic-marker-v1.0.0"
    stage.mkdir()
    write_placeholder(stage / "AI人物媒体标记工具.exe")
    write_placeholder(stage / "使用说明.txt", "测试说明".encode("utf-8"))
    write_placeholder(stage / "LICENSE.txt", b"MIT test placeholder")
    write_placeholder(
        stage / "THIRD_PARTY_NOTICES.txt",
        b"third-party notices test placeholder",
    )

    licenses = stage / "licenses"
    write_placeholder(licenses / "Python-3.14-LICENSE.txt")
    write_placeholder(licenses / "Tcl-8.6-license.terms")
    write_placeholder(licenses / "Tk-8.6-license.terms")
    write_placeholder(licenses / "PyInstaller-6.21.0-COPYING.txt")

    exiftool = stage / "exiftool"
    write_placeholder(exiftool / "exiftool.exe")
    write_placeholder(exiftool / "README.txt")
    write_placeholder(exiftool / "exiftool-manifest.json")
    write_placeholder(exiftool / "exiftool_files" / "LICENSE")
    write_placeholder(
        exiftool / "exiftool_files" / "Licenses_Strawberry_Perl.zip"
    )
    write_placeholder(exiftool / "exiftool_files" / "perl.exe")
    write_placeholder(exiftool / "exiftool_files" / "readme_windows.txt")

    (stage / "待标记").mkdir()
    (stage / "运行记录").mkdir()
    return stage


class VersionMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.build_release = load_build_release()

    def test_project_source_and_exiftool_versions_are_consistent(self) -> None:
        project_version = self.build_release.read_project_version()
        source_version = self.build_release.read_source_app_version()
        lock = self.build_release.read_exiftool_lock()

        self.assertEqual("1.0.0", project_version)
        self.assertEqual(project_version, source_version)
        self.assertEqual("13.59", str(lock["version"]))

        raw_lock = json.loads(
            (PROJECT_ROOT / "packaging" / "exiftool.lock.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(raw_lock["version"], lock["version"])

    def test_python_build_baseline_is_consistent(self) -> None:
        pyproject = tomllib.loads(
            (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        agents = (PROJECT_ROOT / "AGENTS.md").read_text(encoding="utf-8")
        contributing = (PROJECT_ROOT / "CONTRIBUTING.md").read_text(
            encoding="utf-8"
        )
        notices = (PROJECT_ROOT / "THIRD_PARTY_NOTICES.md").read_text(
            encoding="utf-8"
        )
        launcher = (PROJECT_ROOT / "开发运行.cmd").read_text(encoding="utf-8")
        ci_workflow = (
            PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
        ).read_text(encoding="utf-8")
        release_workflow = (
            PROJECT_ROOT / ".github" / "workflows" / "release.yml"
        ).read_text(encoding="utf-8")

        self.assertEqual((3, 14, 6), self.build_release.EXPECTED_PYTHON)
        self.assertEqual("==3.14.6", pyproject["project"]["requires-python"])
        self.assertIn("Python 3.14.6", readme)
        self.assertIn("Python 3.14.6", agents)
        self.assertIn("Python 3.14.6", contributing)
        self.assertIn("CPython 3.14.6", notices)
        self.assertIn("(3, 14, 6)", launcher)
        self.assertIn('python-version: "3.14.6"', ci_workflow)
        self.assertIn('python-version: "3.14.6"', release_workflow)


class ReleaseStageHygieneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.build_release = load_build_release()

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_clean_whitelisted_stage_is_accepted(self) -> None:
        stage = make_clean_release_stage(self.root)

        self.build_release.validate_release_stage(stage)

        self.assertEqual([], list((stage / "待标记").iterdir()))
        self.assertEqual([], list((stage / "运行记录").iterdir()))

    def test_private_media_and_runtime_records_are_rejected(self) -> None:
        forbidden_names = [
            "private.jpg",
            "private.JPEG",
            "private.png",
            "private.MP4",
            "验证结果.csv",
            "media.mp4_original",
        ]
        for filename in forbidden_names:
            with self.subTest(filename=filename):
                case_root = self.root / hashlib.sha256(
                    filename.encode("utf-8")
                ).hexdigest()[:12]
                case_root.mkdir()
                stage = make_clean_release_stage(case_root)
                write_placeholder(stage / "待标记" / filename)

                with self.assertRaises(Exception):
                    self.build_release.validate_release_stage(stage)

    def test_source_cache_placeholders_and_unknown_top_level_files_are_rejected(self) -> None:
        forbidden_paths = [
            Path("src") / "app.py",
            Path("licenses") / "helper.pyc",
            Path("__pycache__") / "app.cpython-314.pyc",
            Path("待标记") / ".gitkeep",
            Path("unexpected.txt"),
        ]
        for relative_path in forbidden_paths:
            with self.subTest(relative_path=str(relative_path)):
                case_root = self.root / hashlib.sha256(
                    str(relative_path).encode("utf-8")
                ).hexdigest()[:12]
                case_root.mkdir()
                stage = make_clean_release_stage(case_root)
                write_placeholder(stage / relative_path)

                with self.assertRaises(Exception):
                    self.build_release.validate_release_stage(stage)

    def test_missing_required_component_is_rejected(self) -> None:
        required_paths = [
            Path("AI人物媒体标记工具.exe"),
            Path("使用说明.txt"),
            Path("LICENSE.txt"),
            Path("THIRD_PARTY_NOTICES.txt"),
            Path("exiftool") / "exiftool.exe",
            Path("exiftool") / "exiftool-manifest.json",
            Path("待标记"),
            Path("运行记录"),
        ]
        for relative_path in required_paths:
            with self.subTest(relative_path=str(relative_path)):
                case_root = self.root / hashlib.sha256(
                    str(relative_path).encode("utf-8")
                ).hexdigest()[:12]
                case_root.mkdir()
                stage = make_clean_release_stage(case_root)
                target = stage / relative_path
                if target.is_dir():
                    target.rmdir()
                else:
                    target.unlink()

                with self.assertRaises(Exception):
                    self.build_release.validate_release_stage(stage)


class DeterministicZipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.build_release = load_build_release()

    def test_zip_is_deterministic_and_contains_only_the_release_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stage = make_clean_release_stage(root)
            first_zip = root / "first.zip"
            second_zip = root / "second.zip"
            epoch = 1_700_000_000

            self.build_release.create_deterministic_zip(stage, first_zip, epoch)
            self.build_release.create_deterministic_zip(stage, second_zip, epoch)

            self.assertEqual(first_zip.read_bytes(), second_zip.read_bytes())
            with zipfile.ZipFile(first_zip) as archive:
                names = archive.namelist()
            prefix = f"{stage.name}/"
            self.assertTrue(stage.name.isascii())
            self.assertTrue(names)
            self.assertTrue(all(name.startswith(prefix) for name in names))
            folded_names = [name.casefold() for name in names]
            for suffix in (
                ".jpg",
                ".jpeg",
                ".png",
                ".mp4",
                ".csv",
                "_original",
                ".py",
                ".pyc",
                ".gitkeep",
            ):
                self.assertFalse(
                    any(name.endswith(suffix) for name in folded_names),
                    msg=f"ZIP 中不应包含 {suffix}",
                )


if __name__ == "__main__":
    unittest.main()

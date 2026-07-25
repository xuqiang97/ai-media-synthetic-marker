from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time
import tomllib
import uuid
import zipfile
from pathlib import Path, PurePosixPath


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE_PATH = PROJECT_ROOT / "src" / "ai_media_marker.py"
PYPROJECT_PATH = PROJECT_ROOT / "pyproject.toml"
REQUIREMENTS_BUILD_PATH = PROJECT_ROOT / "requirements-build.lock"
SPEC_PATH = PROJECT_ROOT / "packaging" / "marker_app.spec"
EXIFTOOL_LOCK_PATH = PROJECT_ROOT / "packaging" / "exiftool.lock.json"
EXIFTOOL_DIR = PROJECT_ROOT / "runtime" / "exiftool"
RELEASE_TEMPLATE_DIR = PROJECT_ROOT / "release_template"
TESTS_DIR = PROJECT_ROOT / "tests"
BUILD_DIR = PROJECT_ROOT / "build"
DIST_DIR = PROJECT_ROOT / "dist"
PROJECT_LICENSE_PATH = PROJECT_ROOT / "LICENSE"
THIRD_PARTY_NOTICES_PATH = PROJECT_ROOT / "THIRD_PARTY_NOTICES.md"

APP_NAME = "AI人物媒体标记工具"
APP_EXE_NAME = f"{APP_NAME}.exe"
ASSET_PREFIX = "ai-media-synthetic-marker"
PLATFORM_LABEL = "windows-x64"
EXPECTED_PYTHON = (3, 14, 6)
EXPECTED_PYINSTALLER = "6.21.0"
EXPECTED_EXIFTOOL = "13.59"
EXPECTED_TCL_TK_PREFIX = "8.6"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
EXIFTOOL_MANIFEST_NAME = "exiftool-manifest.json"

TOP_LEVEL_ENTRIES = frozenset(
    {
        APP_EXE_NAME,
        "使用说明.txt",
        "LICENSE.txt",
        "THIRD_PARTY_NOTICES.txt",
        "licenses",
        "exiftool",
        "待标记",
        "运行记录",
    }
)
LICENSE_ENTRIES = frozenset(
    {
        "Python-3.14-LICENSE.txt",
        "Tcl-8.6-license.terms",
        "Tk-8.6-license.terms",
        "PyInstaller-6.21.0-COPYING.txt",
    }
)
EXIFTOOL_REQUIRED_ENTRIES = frozenset(
    {
        "exiftool.exe",
        "README.txt",
        EXIFTOOL_MANIFEST_NAME,
        "exiftool_files",
    }
)
EXIFTOOL_OPTIONAL_ENTRIES = frozenset({"README.md"})
EXIFTOOL_FILES_REQUIRED_ENTRIES = frozenset(
    {"LICENSE", "Licenses_Strawberry_Perl.zip", "perl.exe", "readme_windows.txt"}
)
FORBIDDEN_SUFFIXES = frozenset(
    {
        ".jpg",
        ".jpeg",
        ".png",
        ".mp4",
        ".csv",
        ".py",
        ".pyw",
        ".pyc",
        ".pyo",
        ".spec",
        ".log",
    }
)
FORBIDDEN_NAMES = frozenset(
    {
        ".git",
        ".gitkeep",
        "__pycache__",
        "desktop.ini",
        "thumbs.db",
    }
)
TEXT_SCAN_SUFFIXES = frozenset(
    {".txt", ".md", ".json", ".toml", ".ini", ".yaml", ".yml"}
)
VERSION_PATTERN = re.compile(
    r"^(?P<major>0|[1-9]\d*)\."
    r"(?P<minor>0|[1-9]\d*)\."
    r"(?P<patch>0|[1-9]\d*)$"
)


class BuildError(RuntimeError):
    """A release-build failure with a concise maintainer-facing message."""


def read_project_version() -> str:
    try:
        with PYPROJECT_PATH.open("rb") as handle:
            document = tomllib.load(handle)
        version = document["project"]["version"]
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
        raise BuildError(f"无法读取 pyproject.toml 版本：{exc}") from exc

    if not isinstance(version, str) or not VERSION_PATTERN.fullmatch(version):
        raise BuildError(f"pyproject.toml 中的版本不是 x.y.z：{version!r}")
    return version


def read_source_app_version() -> str:
    try:
        tree = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"), filename=str(SOURCE_PATH))
    except (OSError, SyntaxError, UnicodeError) as exc:
        raise BuildError(f"无法读取源码 APP_VERSION：{exc}") from exc

    versions: list[str] = []
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == "APP_VERSION" for target in targets):
            continue
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            versions.append(value.value)

    if len(versions) != 1:
        raise BuildError(
            f"源码应恰好定义一个字符串 APP_VERSION，实际找到 {len(versions)} 个。"
        )
    if not VERSION_PATTERN.fullmatch(versions[0]):
        raise BuildError(f"源码 APP_VERSION 不是 x.y.z：{versions[0]!r}")
    return versions[0]


def read_exiftool_lock() -> dict[str, object]:
    try:
        lock = json.loads(EXIFTOOL_LOCK_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BuildError(f"无法读取 ExifTool 锁定文件：{exc}") from exc

    required = {"version", "platform", "archive_name", "url", "size", "sha256"}
    if not isinstance(lock, dict):
        raise BuildError("ExifTool 锁定文件的根节点必须是对象。")
    missing = required.difference(lock)
    if missing:
        raise BuildError(
            f"ExifTool 锁定文件缺少字段：{', '.join(sorted(missing))}"
        )

    if str(lock["version"]) != EXPECTED_EXIFTOOL:
        raise BuildError(
            f"ExifTool 锁定版本必须为 {EXPECTED_EXIFTOOL}，"
            f"实际为 {lock['version']}。"
        )
    if str(lock["platform"]) != PLATFORM_LABEL:
        raise BuildError(
            f"ExifTool 平台必须为 {PLATFORM_LABEL}，实际为 {lock['platform']}。"
        )
    sha256 = str(lock["sha256"])
    if not re.fullmatch(r"[0-9a-fA-F]{64}", sha256):
        raise BuildError("ExifTool 锁定文件中的 SHA-256 格式无效。")
    try:
        if int(lock["size"]) <= 0:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise BuildError("ExifTool 锁定文件中的 size 必须是正整数。") from exc
    return lock


def read_locked_build_dependencies() -> dict[str, str]:
    try:
        lines = REQUIREMENTS_BUILD_PATH.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise BuildError(f"无法读取 requirements-build.lock：{exc}") from exc

    dependencies: dict[str, str] = {}
    pattern = re.compile(
        r"(?P<name>[A-Za-z0-9_.-]+)==(?P<version>[^\s;]+)"
        r"(?P<hashes>(?:\s+--hash=sha256:[0-9a-fA-F]{64})+)"
    )
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = pattern.fullmatch(line)
        if not match:
            raise BuildError(
                "requirements-build.lock 只允许固定的 "
                "name==version 和 SHA-256，"
                f"第 {line_number} 行为：{raw_line!r}"
            )
        name = match.group("name")
        folded_name = name.casefold().replace("_", "-")
        if folded_name in dependencies:
            raise BuildError(f"requirements-build.lock 重复定义依赖：{name}")
        dependencies[folded_name] = match.group("version")

    if not dependencies:
        raise BuildError("requirements-build.lock 不能为空。")
    return dependencies


def validate_locked_build_dependencies() -> None:
    mismatches: list[str] = []
    for name, expected_version in sorted(read_locked_build_dependencies().items()):
        try:
            actual_version = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            mismatches.append(f"{name}：未安装（需要 {expected_version}）")
            continue
        if actual_version != expected_version:
            mismatches.append(
                f"{name}：实际 {actual_version}，需要 {expected_version}"
            )
    if mismatches:
        raise BuildError(
            "构建依赖与 requirements-build.lock 不一致：\n"
            + "\n".join(mismatches)
            + "\n请运行：py -3.14 -m pip install --require-hashes "
            "--only-binary=:all: -r requirements-build.lock"
        )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _collect_exiftool_payload_records(
    root: Path,
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for current_root, directory_names, file_names in os.walk(
        root,
        topdown=True,
        followlinks=False,
    ):
        current = Path(current_root)
        for directory_name in list(directory_names):
            directory = current / directory_name
            if directory.is_symlink() or _is_reparse_point(directory):
                raise BuildError(
                    f"ExifTool 运行目录包含链接或重解析点：{directory}"
                )
        for file_name in file_names:
            path = current / file_name
            relative = path.relative_to(root)
            if relative.parts in {
                ("README.md",),
                (EXIFTOOL_MANIFEST_NAME,),
            }:
                continue
            if path.is_symlink() or _is_reparse_point(path) or not path.is_file():
                raise BuildError(f"ExifTool 运行目录包含非常规文件：{path}")
            records.append(
                {
                    "path": relative.as_posix(),
                    "size": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
    records.sort(key=lambda item: str(item["path"]).casefold())
    return records


def _validate_exiftool_manifest(
    exiftool_dir: Path,
    lock: dict[str, object],
) -> None:
    manifest_path = exiftool_dir / EXIFTOOL_MANIFEST_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BuildError(f"无法读取 ExifTool 完整性清单：{exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise BuildError("ExifTool 完整性清单格式无效。")

    expected_metadata = {
        "exiftool_version": str(lock["version"]),
        "archive_name": str(lock["archive_name"]),
        "archive_size": int(lock["size"]),
        "archive_sha256": str(lock["sha256"]).casefold(),
    }
    for key, expected_value in expected_metadata.items():
        actual_value = manifest.get(key)
        if key == "archive_size":
            try:
                actual_value = int(actual_value)
            except (TypeError, ValueError):
                pass
        elif key == "archive_sha256":
            actual_value = str(actual_value).casefold()
        else:
            actual_value = str(actual_value)
        if actual_value != expected_value:
            raise BuildError(
                f"ExifTool 完整性清单的 {key} 不符："
                f"期望 {expected_value!r}，实际 {actual_value!r}"
            )

    manifest_records = manifest.get("files")
    if not isinstance(manifest_records, list):
        raise BuildError("ExifTool 完整性清单缺少 files 数组。")
    actual_records = _collect_exiftool_payload_records(exiftool_dir)
    if manifest_records != actual_records:
        raise BuildError(
            "ExifTool 运行文件与已校验压缩包的逐文件完整性清单不一致。\n"
            "请重新运行：py -3.14 scripts\\fetch_exiftool.py --force"
        )


def _run_exiftool_version(executable: Path) -> str:
    try:
        completed = subprocess.run(
            [str(executable), "-ver"],
            cwd=executable.parent,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=CREATE_NO_WINDOW,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BuildError(f"无法启动 ExifTool：{exc}") from exc

    stdout = completed.stdout.decode("utf-8-sig", errors="replace").strip()
    stderr = completed.stderr.decode("utf-8-sig", errors="replace").strip()
    if completed.returncode != 0:
        raise BuildError(
            stderr or stdout or f"ExifTool 退出码 {completed.returncode}"
        )
    return stdout


def validate_exiftool_runtime(
    exiftool_dir: Path = EXIFTOOL_DIR,
    expected_version: str = EXPECTED_EXIFTOOL,
    lock: dict[str, object] | None = None,
) -> None:
    executable = exiftool_dir / "exiftool.exe"
    files_dir = exiftool_dir / "exiftool_files"
    readme = exiftool_dir / "README.txt"

    if not executable.is_file():
        raise BuildError(
            f"缺少外置 ExifTool：{executable}\n"
            "请先运行：python scripts/fetch_exiftool.py"
        )
    if not files_dir.is_dir() or not readme.is_file():
        raise BuildError("ExifTool 必须同时包含 exiftool_files 和 README.txt。")
    missing_payload = [
        name
        for name in sorted(EXIFTOOL_FILES_REQUIRED_ENTRIES)
        if not (files_dir / name).is_file()
    ]
    if missing_payload:
        raise BuildError(
            "ExifTool 运行组件或许可证不完整，缺少："
            + ", ".join(missing_payload)
        )

    if lock is None:
        lock = read_exiftool_lock()
    _validate_exiftool_manifest(exiftool_dir, lock)

    actual_version = _run_exiftool_version(executable)
    if actual_version != expected_version:
        raise BuildError(
            f"ExifTool 版本不符：期望 {expected_version}，实际 {actual_version}"
        )


def _pyinstaller_license_path() -> Path:
    try:
        distribution = importlib.metadata.distribution("pyinstaller")
    except importlib.metadata.PackageNotFoundError as exc:
        raise BuildError(
            "未安装 PyInstaller。请先运行：\n"
            "py -3.14 -m pip install --require-hashes "
            "--only-binary=:all: -r requirements-build.lock"
        ) from exc

    for entry in distribution.files or ():
        normalized = str(entry).replace("\\", "/")
        if normalized.endswith("/licenses/COPYING.txt"):
            candidate = Path(distribution.locate_file(entry))
            if candidate.is_file():
                return candidate
    raise BuildError("当前 PyInstaller 安装缺少 COPYING.txt。")


def _license_sources() -> dict[str, Path]:
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    sources = {
        "Python-3.14-LICENSE.txt": python_license,
        "Tcl-8.6-license.terms": (
            PROJECT_ROOT / "packaging" / "licenses" / "Tcl-8.6-license.terms"
        ),
        "Tk-8.6-license.terms": (
            PROJECT_ROOT / "packaging" / "licenses" / "Tk-8.6-license.terms"
        ),
        "PyInstaller-6.21.0-COPYING.txt": _pyinstaller_license_path(),
    }
    missing = [f"{name}: {path}" for name, path in sources.items() if not path.is_file()]
    if missing:
        raise BuildError("构建环境缺少许可证文件：\n" + "\n".join(missing))
    return sources


def preflight_environment() -> tuple[str, dict[str, object], dict[str, Path]]:
    if os.name != "nt" or platform.system() != "Windows":
        raise BuildError("便携 EXE 只能在 Windows 上构建。")
    if struct.calcsize("P") * 8 != 64:
        raise BuildError("构建环境必须是 64 位 Python。")
    machine = platform.machine().casefold()
    if machine not in {"amd64", "x86_64"}:
        raise BuildError(f"构建机器必须是 x64，实际为 {platform.machine()}。")
    if sys.version_info[:3] != EXPECTED_PYTHON:
        raise BuildError(
            "构建必须使用 Python "
            f"{'.'.join(str(value) for value in EXPECTED_PYTHON)}，"
            "实际为 "
            f"{sys.version_info.major}.{sys.version_info.minor}."
            f"{sys.version_info.micro}。"
        )

    validate_locked_build_dependencies()
    pyinstaller_version = importlib.metadata.version("pyinstaller")
    if pyinstaller_version != EXPECTED_PYINSTALLER:
        raise BuildError(
            f"PyInstaller 必须为 {EXPECTED_PYINSTALLER}，"
            f"实际为 {pyinstaller_version}。\n"
            "请运行：py -3.14 -m pip install --require-hashes "
            "--only-binary=:all: -r requirements-build.lock"
        )

    try:
        import tkinter

        interpreter = tkinter.Tcl()
        tcl_version = interpreter.eval("info patchlevel")
    except Exception as exc:
        raise BuildError(f"Python 环境缺少可用的 Tcl/Tk：{exc}") from exc
    if not tcl_version.startswith(f"{EXPECTED_TCL_TK_PREFIX}."):
        raise BuildError(
            f"Tcl/Tk 必须为 {EXPECTED_TCL_TK_PREFIX}.x，实际为 {tcl_version}。"
        )

    project_version = read_project_version()
    source_version = read_source_app_version()
    if source_version != project_version:
        raise BuildError(
            "版本不一致："
            f"源码 APP_VERSION={source_version}，"
            f"pyproject.toml={project_version}。"
        )

    lock = read_exiftool_lock()
    validate_exiftool_runtime(
        expected_version=str(lock["version"]),
        lock=lock,
    )

    required_project_files = (
        SOURCE_PATH,
        REQUIREMENTS_BUILD_PATH,
        SPEC_PATH,
        RELEASE_TEMPLATE_DIR / "使用说明.txt",
        PROJECT_LICENSE_PATH,
        THIRD_PARTY_NOTICES_PATH,
    )
    missing_files = [str(path) for path in required_project_files if not path.is_file()]
    if missing_files:
        raise BuildError("构建所需文件缺失：\n" + "\n".join(missing_files))

    try:
        instructions_heading = (
            RELEASE_TEMPLATE_DIR / "使用说明.txt"
        ).read_text(encoding="utf-8").splitlines()[0]
    except (OSError, IndexError, UnicodeError) as exc:
        raise BuildError(f"无法读取发布说明版本：{exc}") from exc
    if f"v{project_version}" not in instructions_heading:
        raise BuildError(
            "发布说明版本与项目版本不一致："
            f"首行为 {instructions_heading!r}，项目版本为 {project_version}。"
        )

    licenses = _license_sources()
    return project_version, lock, licenses


def run_unittests() -> None:
    if not TESTS_DIR.is_dir():
        raise BuildError(f"测试目录不存在：{TESTS_DIR}")
    test_files = sorted(TESTS_DIR.rglob("test*.py"))
    if not test_files:
        raise BuildError("没有找到 tests/test*.py，拒绝在无测试状态下发布。")

    print("\n[1/7] 运行 unittest……", flush=True)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            str(TESTS_DIR),
            "-p",
            "test*.py",
            "-v",
        ],
        cwd=PROJECT_ROOT,
        check=False,
    )
    if completed.returncode != 0:
        raise BuildError("unittest 未通过，已停止构建。")


def _version_tuple(version: str) -> tuple[int, int, int, int]:
    match = VERSION_PATTERN.fullmatch(version)
    if not match:
        raise BuildError(f"无法转换 Windows 版本号：{version}")
    values = tuple(int(match.group(name)) for name in ("major", "minor", "patch"))
    if any(value > 65535 for value in values):
        raise BuildError("Windows 版本号的每一段必须不大于 65535。")
    return values[0], values[1], values[2], 0


def _write_windows_version_file(path: Path, version: str) -> None:
    file_version = _version_tuple(version)
    numeric = ", ".join(str(item) for item in file_version)
    content = f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({numeric}),
    prodvers=({numeric}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '080404b0',
        [
          StringStruct('CompanyName', 'AI Media Synthetic Marker contributors'),
          StringStruct('FileDescription', 'AI 人物媒体 XMP 标记工具'),
          StringStruct('FileVersion', '{version}'),
          StringStruct('InternalName', 'ai-media-synthetic-marker'),
          StringStruct('LegalCopyright', 'Copyright (c) AI Media Synthetic Marker contributors'),
          StringStruct('OriginalFilename', '{APP_EXE_NAME}'),
          StringStruct('ProductName', 'AI 人物媒体 XMP 标记工具'),
          StringStruct('ProductVersion', '{version}')
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
"""
    path.write_text(content, encoding="utf-8", newline="\n")


def resolve_source_date_epoch() -> int:
    configured = os.environ.get("SOURCE_DATE_EPOCH")
    if configured:
        try:
            epoch = int(configured)
        except ValueError as exc:
            raise BuildError("SOURCE_DATE_EPOCH 必须是整数。") from exc
        if epoch < 315532800:
            raise BuildError("SOURCE_DATE_EPOCH 不能早于 1980-01-01。")
        return epoch

    try:
        completed = subprocess.run(
            ["git", "show", "-s", "--format=%ct", "HEAD"],
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=15,
            check=False,
        )
        if completed.returncode == 0:
            epoch = int(completed.stdout.strip())
            if epoch >= 315532800:
                return epoch
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass

    # A stable fallback keeps local source archives deterministic before Git setup.
    return 315532800


def build_executable(
    work_root: Path,
    version: str,
    source_date_epoch: int,
) -> Path:
    print("\n[2/7] 使用 PyInstaller 构建单文件 GUI EXE……", flush=True)
    version_file = work_root / "windows-version-info.txt"
    pyinstaller_dist = work_root / "pyinstaller-dist"
    pyinstaller_work = work_root / "pyinstaller-work"
    pyinstaller_config = work_root / "pyinstaller-config"
    _write_windows_version_file(version_file, version)

    environment = os.environ.copy()
    environment["PYTHONHASHSEED"] = "1"
    environment["SOURCE_DATE_EPOCH"] = str(source_date_epoch)
    environment["AI_MEDIA_MARKER_VERSION_FILE"] = str(version_file)
    environment["PYINSTALLER_CONFIG_DIR"] = str(pyinstaller_config)

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(pyinstaller_dist),
        "--workpath",
        str(pyinstaller_work),
        str(SPEC_PATH),
    ]
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=environment,
        check=False,
    )
    if completed.returncode != 0:
        raise BuildError(f"PyInstaller 构建失败，退出码 {completed.returncode}。")

    executable = pyinstaller_dist / APP_EXE_NAME
    if not executable.is_file() or executable.stat().st_size == 0:
        raise BuildError(f"PyInstaller 未生成有效的 EXE：{executable}")
    return executable


def _copy_file(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise BuildError(f"白名单文件不存在：{source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def assemble_release_stage(
    stage: Path,
    executable: Path,
    license_sources: dict[str, Path],
) -> None:
    print("\n[3/7] 按白名单组装便携目录……", flush=True)
    stage.mkdir(parents=True, exist_ok=False)

    _copy_file(executable, stage / APP_EXE_NAME)
    _copy_file(RELEASE_TEMPLATE_DIR / "使用说明.txt", stage / "使用说明.txt")
    _copy_file(PROJECT_LICENSE_PATH, stage / "LICENSE.txt")
    _copy_file(
        THIRD_PARTY_NOTICES_PATH,
        stage / "THIRD_PARTY_NOTICES.txt",
    )

    (stage / "待标记").mkdir()
    (stage / "运行记录").mkdir()

    licenses_dir = stage / "licenses"
    licenses_dir.mkdir()
    for output_name in sorted(LICENSE_ENTRIES):
        _copy_file(license_sources[output_name], licenses_dir / output_name)

    exiftool_output = stage / "exiftool"
    exiftool_output.mkdir()
    _copy_file(EXIFTOOL_DIR / "exiftool.exe", exiftool_output / "exiftool.exe")
    _copy_file(EXIFTOOL_DIR / "README.txt", exiftool_output / "README.txt")
    _copy_file(
        EXIFTOOL_DIR / EXIFTOOL_MANIFEST_NAME,
        exiftool_output / EXIFTOOL_MANIFEST_NAME,
    )
    optional_readme = EXIFTOOL_DIR / "README.md"
    if optional_readme.is_file():
        _copy_file(optional_readme, exiftool_output / "README.md")
    shutil.copytree(
        EXIFTOOL_DIR / "exiftool_files",
        exiftool_output / "exiftool_files",
    )


def smoke_test_application(stage: Path) -> None:
    executable = stage / APP_EXE_NAME
    report_path = stage.parent / "application-self-test.txt"
    environment = os.environ.copy()
    environment["AI_MEDIA_MARKER_SELF_TEST_REPORT"] = str(report_path)
    print("\n[4/7] 执行主程序无界面启动自检……", flush=True)
    try:
        completed = subprocess.run(
            [str(executable), "--self-test"],
            cwd=stage,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BuildError(f"主程序无界面启动自检失败：{exc}") from exc
    report = (
        report_path.read_text(encoding="utf-8", errors="replace").strip()
        if report_path.is_file()
        else "（主程序没有生成自检诊断报告）"
    )
    print(report, flush=True)
    if completed.returncode != 0:
        raise BuildError(
            f"主程序无界面启动自检失败，退出码 {completed.returncode}：{report}"
        )


def _is_reparse_point(path: Path) -> bool:
    attributes = getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _check_text_for_local_paths(path: Path) -> None:
    if path.suffix.casefold() not in TEXT_SCAN_SUFFIXES:
        return
    if path.stat().st_size > 2 * 1024 * 1024:
        return
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace").casefold()
    except OSError as exc:
        raise BuildError(f"无法检查发布文本文件：{path}: {exc}") from exc

    candidates = {PROJECT_ROOT, Path.home(), Path(sys.base_prefix)}
    for candidate in candidates:
        raw = str(candidate).casefold()
        variants = {raw, raw.replace("\\", "/")}
        if any(value and value in text for value in variants):
            raise BuildError(f"发布文本包含本机绝对路径：{path}")


def validate_release_stage(stage: Path) -> None:
    if not stage.is_dir():
        raise BuildError(f"发布暂存目录不存在：{stage}")

    actual_top = {entry.name for entry in stage.iterdir()}
    if actual_top != TOP_LEVEL_ENTRIES:
        missing = sorted(TOP_LEVEL_ENTRIES - actual_top)
        extra = sorted(actual_top - TOP_LEVEL_ENTRIES)
        details = []
        if missing:
            details.append("缺少：" + ", ".join(missing))
        if extra:
            details.append("多出：" + ", ".join(extra))
        raise BuildError("发布顶层不符合白名单；" + "；".join(details))

    expected_files = {
        APP_EXE_NAME,
        "使用说明.txt",
        "LICENSE.txt",
        "THIRD_PARTY_NOTICES.txt",
    }
    expected_dirs = {"licenses", "exiftool", "待标记", "运行记录"}
    for name in expected_files:
        path = stage / name
        if not path.is_file() or path.stat().st_size == 0:
            raise BuildError(f"发布文件缺失或为空：{path}")
    for name in expected_dirs:
        if not (stage / name).is_dir():
            raise BuildError(f"发布目录缺失：{stage / name}")

    for empty_name in ("待标记", "运行记录"):
        if any((stage / empty_name).iterdir()):
            raise BuildError(f"发布目录必须为空：{stage / empty_name}")

    licenses_dir = stage / "licenses"
    actual_licenses = {entry.name for entry in licenses_dir.iterdir()}
    if actual_licenses != LICENSE_ENTRIES:
        raise BuildError(
            "licenses 目录不符合白名单："
            f"期望 {sorted(LICENSE_ENTRIES)}，实际 {sorted(actual_licenses)}"
        )
    for name in LICENSE_ENTRIES:
        path = licenses_dir / name
        if not path.is_file() or path.stat().st_size == 0:
            raise BuildError(f"许可证文件缺失或为空：{path}")

    exiftool_dir = stage / "exiftool"
    actual_exiftool = {entry.name for entry in exiftool_dir.iterdir()}
    allowed_exiftool = EXIFTOOL_REQUIRED_ENTRIES | EXIFTOOL_OPTIONAL_ENTRIES
    missing_exiftool = EXIFTOOL_REQUIRED_ENTRIES - actual_exiftool
    extra_exiftool = actual_exiftool - allowed_exiftool
    if missing_exiftool or extra_exiftool:
        raise BuildError(
            "exiftool 目录不符合白名单："
            f"缺少 {sorted(missing_exiftool)}，多出 {sorted(extra_exiftool)}"
        )
    if not (exiftool_dir / "exiftool_files").is_dir():
        raise BuildError("exiftool_files 必须是目录。")
    for name in EXIFTOOL_FILES_REQUIRED_ENTRIES:
        path = exiftool_dir / "exiftool_files" / name
        if not path.is_file() or path.stat().st_size == 0:
            raise BuildError(f"ExifTool 组件或许可证缺失：{path}")

    seen_casefold: set[str] = set()
    for path in sorted(stage.rglob("*"), key=lambda item: item.as_posix().casefold()):
        relative = path.relative_to(stage)
        normalized = relative.as_posix()
        folded = normalized.casefold()
        if folded in seen_casefold:
            raise BuildError(f"发布目录存在大小写冲突路径：{relative}")
        seen_casefold.add(folded)

        if path.is_symlink() or _is_reparse_point(path):
            raise BuildError(f"发布目录不允许符号链接或重解析点：{relative}")
        if path.name.casefold() in FORBIDDEN_NAMES:
            raise BuildError(f"发布目录包含禁止名称：{relative}")
        if path.name.casefold().endswith("_original"):
            raise BuildError(f"发布目录包含 ExifTool 备份：{relative}")
        if path.is_file() and path.suffix.casefold() in FORBIDDEN_SUFFIXES:
            raise BuildError(f"发布目录包含禁止文件类型：{relative}")
        if path.is_file():
            _check_text_for_local_paths(path)


def _zip_timestamp(epoch: int) -> tuple[int, int, int, int, int, int]:
    value = time.gmtime(max(epoch, 315532800))
    year = min(max(value.tm_year, 1980), 2107)
    return year, value.tm_mon, value.tm_mday, value.tm_hour, value.tm_min, value.tm_sec


def _zip_info(name: str, is_directory: bool, epoch: int) -> zipfile.ZipInfo:
    if is_directory and not name.endswith("/"):
        name += "/"
    info = zipfile.ZipInfo(name, date_time=_zip_timestamp(epoch))
    info.create_system = 3
    info.compress_type = zipfile.ZIP_STORED if is_directory else zipfile.ZIP_DEFLATED
    info.external_attr = (
        ((stat.S_IFDIR | 0o755) << 16) | 0x10
        if is_directory
        else ((stat.S_IFREG | (0o755 if name.casefold().endswith(".exe") else 0o644)) << 16)
    )
    info.extra = b""
    info.comment = b""
    return info


def create_deterministic_zip(
    package_root: Path,
    zip_path: Path,
    epoch: int,
) -> None:
    if not package_root.is_dir():
        raise BuildError(f"无法压缩不存在的目录：{package_root}")
    zip_path.parent.mkdir(parents=True, exist_ok=True)

    root_parent = package_root.parent
    members = [package_root, *package_root.rglob("*")]
    members.sort(
        key=lambda path: (
            path.relative_to(root_parent).as_posix().casefold(),
            path.relative_to(root_parent).as_posix(),
        )
    )

    with zipfile.ZipFile(
        zip_path,
        mode="x",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        allowZip64=True,
    ) as archive:
        archive.comment = b""
        for path in members:
            relative = path.relative_to(root_parent).as_posix()
            if path.is_dir():
                archive.writestr(_zip_info(relative, True, epoch), b"")
                continue
            info = _zip_info(relative, False, epoch)
            with path.open("rb") as source, archive.open(info, mode="w") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)


def _validate_zip_member_name(name: str, expected_root_name: str) -> None:
    if "\\" in name:
        raise BuildError(f"ZIP 成员使用了反斜杠：{name}")
    member = PurePosixPath(name)
    if member.is_absolute() or ".." in member.parts:
        raise BuildError(f"ZIP 包含不安全路径：{name}")
    if not member.parts or member.parts[0] != expected_root_name:
        raise BuildError(f"ZIP 成员不在唯一发布根目录内：{name}")


def validate_release_zip(
    zip_path: Path,
    expected_root_name: str,
    expected_exiftool_version: str,
) -> None:
    print("\n[6/7] 校验 ZIP、解压结构和 ExifTool 版本……", flush=True)
    if not zip_path.is_file() or not zipfile.is_zipfile(zip_path):
        raise BuildError(f"发布产物不是有效 ZIP：{zip_path}")

    with zipfile.ZipFile(zip_path) as archive:
        bad_member = archive.testzip()
        if bad_member is not None:
            raise BuildError(f"ZIP CRC 校验失败：{bad_member}")

        names = [info.filename for info in archive.infolist()]
        if len(names) != len(set(names)):
            raise BuildError("ZIP 中存在完全重复的成员名称。")
        folded = [name.casefold() for name in names]
        if len(folded) != len(set(folded)):
            raise BuildError("ZIP 中存在大小写冲突的成员名称。")
        for name in names:
            _validate_zip_member_name(name, expected_root_name)

        required_empty_dirs = {
            f"{expected_root_name}/待标记/",
            f"{expected_root_name}/运行记录/",
        }
        if not required_empty_dirs.issubset(names):
            raise BuildError("ZIP 缺少显式的空“待标记”或“运行记录”目录。")

        with tempfile.TemporaryDirectory(prefix="verify-release-", dir=BUILD_DIR) as temp:
            extracted_parent = Path(temp)
            for info in archive.infolist():
                destination = (extracted_parent / Path(*PurePosixPath(info.filename).parts)).resolve()
                try:
                    destination.relative_to(extracted_parent.resolve())
                except ValueError as exc:
                    raise BuildError(f"ZIP 解压路径越界：{info.filename}") from exc
            archive.extractall(extracted_parent)

            extracted_root = extracted_parent / expected_root_name
            validate_release_stage(extracted_root)
            validate_exiftool_runtime(
                extracted_root / "exiftool",
                expected_exiftool_version,
                read_exiftool_lock(),
            )


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def publish_outputs(candidate_zip: Path, version: str) -> tuple[Path, Path, str]:
    print("\n[7/7] 发布 ZIP 和 SHA256SUMS.txt……", flush=True)
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    output_name = f"{ASSET_PREFIX}-v{version}-{PLATFORM_LABEL}.zip"
    output_zip = DIST_DIR / output_name

    # Only replace the artifact for this exact version. Older releases and
    # unrelated files in dist belong to the maintainer and are never deleted.
    os.replace(candidate_zip, output_zip)

    digest = sha256_file(output_zip)
    checksum_path = DIST_DIR / "SHA256SUMS.txt"
    _atomic_write_text(checksum_path, f"{digest} *{output_name}\n")

    if not output_zip.is_file() or output_zip.stat().st_size == 0:
        raise BuildError(f"发布 ZIP 缺失或为空：{output_zip}")
    return output_zip, checksum_path, digest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="测试、构建并严格校验 Windows x64 便携发布包。"
    )
    return parser.parse_args()


def main() -> int:
    parse_args()
    try:
        print("正在检查固定构建环境……", flush=True)
        version, exiftool_lock, license_sources = preflight_environment()
        source_date_epoch = resolve_source_date_epoch()

        BUILD_DIR.mkdir(parents=True, exist_ok=True)
        run_unittests()

        with tempfile.TemporaryDirectory(
            prefix="release-build-",
            dir=BUILD_DIR,
            ignore_cleanup_errors=True,
        ) as temporary:
            work_root = Path(temporary)
            executable = build_executable(work_root, version, source_date_epoch)
            package_name = f"{ASSET_PREFIX}-v{version}"
            package_root = work_root / package_name
            assemble_release_stage(package_root, executable, license_sources)

            smoke_test_application(package_root)

            print("\n[5/7] 执行发布卫生检查……", flush=True)
            validate_release_stage(package_root)
            validate_exiftool_runtime(
                package_root / "exiftool",
                str(exiftool_lock["version"]),
                exiftool_lock,
            )

            candidate_zip = work_root / (
                f"{ASSET_PREFIX}-v{version}-{PLATFORM_LABEL}.zip"
            )
            create_deterministic_zip(
                package_root,
                candidate_zip,
                source_date_epoch,
            )
            validate_release_zip(
                candidate_zip,
                package_name,
                str(exiftool_lock["version"]),
            )
            output_zip, checksum_path, digest = publish_outputs(
                candidate_zip,
                version,
            )

        print("\n构建完成。", flush=True)
        print(f"ZIP：{output_zip}", flush=True)
        print(f"SHA256：{digest}", flush=True)
        print(f"校验文件：{checksum_path}", flush=True)
        return 0
    except BuildError as exc:
        print(f"\n构建失败：{exc}", file=sys.stderr, flush=True)
        return 1
    except KeyboardInterrupt:
        print("\n构建已由用户取消。", file=sys.stderr, flush=True)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

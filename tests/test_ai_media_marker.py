from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from xml.sax.saxutils import escape


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = PROJECT_ROOT / "src"
if str(SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIR))

import ai_media_marker as marker  # noqa: E402


def make_xmp(
    values: list[str],
    *,
    container: str = "Bag",
    dc_namespace: str = marker.DC_NAMESPACE,
    rdf_namespace: str = marker.RDF_NAMESPACE,
) -> str:
    items = "".join(f"<rdf:li>{escape(value)}</rdf:li>" for value in values)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        f'<rdf:RDF xmlns:rdf="{rdf_namespace}">'
        f'<rdf:Description xmlns:dc="{dc_namespace}">'
        f"<dc:subject><rdf:{container}>{items}</rdf:{container}></dc:subject>"
        "</rdf:Description>"
        "</rdf:RDF>"
        "</x:xmpmeta>"
    )


class FakeExifTool:
    """A stateful ExifTool double that never reads or writes media bytes."""

    def __init__(
        self,
        path: Path,
        subjects: list[str] | None = None,
        raw_xmp: str | bytes | None = None,
        *,
        update_xmp_after_write: bool = True,
    ) -> None:
        self.path = path.resolve()
        self.subjects = list(subjects or [])
        self.xmp = raw_xmp if raw_xmp is not None else make_xmp(self.subjects)
        self.update_xmp_after_write = update_xmp_after_write
        self.read_calls = 0
        self.raw_xmp_calls = 0
        self.add_calls = 0

    def _assert_path(self, path: Path) -> None:
        if path.resolve() != self.path:
            raise AssertionError(f"unexpected test path: {path}")

    def read_subjects(self, path: Path) -> list[str]:
        self._assert_path(path)
        self.read_calls += 1
        return list(self.subjects)

    def raw_xmp(self, path: Path) -> str:
        self._assert_path(path)
        self.raw_xmp_calls += 1
        return self.xmp

    def add_marker(self, path: Path) -> None:
        self._assert_path(path)
        self.add_calls += 1
        self.subjects.append(marker.MARKER)
        if self.update_xmp_after_write:
            self.xmp = make_xmp(self.subjects)


class ExifToolRunnerArgumentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.executable = self.root / "exiftool.exe"
        self.executable.write_bytes(b"test executable placeholder")
        self.media = self.root / "中文 示例.mp4"
        self.media.write_bytes(b"test media placeholder")
        self.runner = marker.ExifToolRunner(self.executable)

    def test_add_marker_uses_exact_append_and_no_backup_options(self) -> None:
        completed = marker.subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=b"",
            stderr=b"",
        )
        with mock.patch.object(
            marker.subprocess,
            "run",
            return_value=completed,
        ) as subprocess_run:
            self.runner.add_marker(self.media)

        positional, keyword = subprocess_run.call_args
        self.assertEqual(
            [
                str(self.executable.resolve()),
                "-charset",
                "filename=UTF8",
                "-@",
                "-",
            ],
            positional[0],
        )
        argument_lines = keyword["input"].decode("utf-8").splitlines()
        self.assertEqual(
            [
                "-overwrite_original",
                "-P",
                f"-XMP-dc:Subject+={marker.MARKER}",
            ],
            argument_lines[:-1],
        )
        self.assertTrue(Path(argument_lines[-1]).samefile(self.media))
        self.assertNotIn("Microsoft:Category", keyword["input"].decode("utf-8"))
        self.assertIsNone(keyword["timeout"])

    def test_read_subjects_requests_only_explicit_xmp_dc_subject(self) -> None:
        payload = json.dumps(
            [{"XMP-dc:Subject": ["已有关键词", marker.MARKER]}],
            ensure_ascii=False,
        ).encode("utf-8")
        completed = marker.subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=payload,
            stderr=b"",
        )
        with mock.patch.object(
            marker.subprocess,
            "run",
            return_value=completed,
        ) as subprocess_run:
            subjects = self.runner.read_subjects(self.media)

        self.assertEqual(["已有关键词", marker.MARKER], subjects)
        _, keyword = subprocess_run.call_args
        argument_lines = keyword["input"].decode("utf-8").splitlines()
        self.assertEqual(
            [
                "-j",
                "-struct",
                "-G1",
                "-s",
                "-XMP-dc:Subject",
            ],
            argument_lines[:-1],
        )
        self.assertTrue(Path(argument_lines[-1]).samefile(self.media))
        self.assertNotIn("Microsoft:Category", keyword["input"].decode("utf-8"))

    def test_raw_xmp_preserves_original_bytes_for_xml_encoding_detection(self) -> None:
        raw_xmp = b"\xff\xfe<\x00x\x00m\x00p\x00"
        completed = marker.subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=raw_xmp,
            stderr=b"",
        )
        with mock.patch.object(
            marker.subprocess,
            "run",
            return_value=completed,
        ):
            actual = self.runner.raw_xmp(self.media)

        self.assertEqual(raw_xmp, actual)


class StrictVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.input_dir = Path(self.temporary.name) / "待标记"
        self.input_dir.mkdir()
        self.media = self.input_dir / "示例占位文件.JPG"
        self.media.write_bytes(b"test placeholder; not a real media file")

    def test_exact_keyword_and_bag_li_structure_pass(self) -> None:
        subjects = ["已有关键词", marker.MARKER]
        runner = FakeExifTool(self.media, subjects, make_xmp(subjects))

        evidence = marker.verify_compliance(
            self.media,
            runner,
            exiftool_version="13.59",
        )

        self.assertEqual("通过", evidence.result)
        self.assertEqual("已确认 rdf:Bag/rdf:li", evidence.xmp_structure)
        self.assertEqual("13.59", evidence.exiftool_version)
        self.assertEqual(1, runner.raw_xmp_calls)

    def test_keyword_match_is_exact(self) -> None:
        non_matches = [
            marker.MARKER.upper(),
            f" {marker.MARKER}",
            f"{marker.MARKER} ",
            f"{marker.MARKER}-extra",
            marker.MARKER[:-1],
        ]
        for value in non_matches:
            with self.subTest(value=value):
                runner = FakeExifTool(self.media, [value], make_xmp([value]))
                evidence = marker.verify_compliance(self.media, runner)
                self.assertEqual("未标记", evidence.result)
                self.assertEqual(0, runner.raw_xmp_calls)

    def test_subject_without_target_is_unmarked(self) -> None:
        runner = FakeExifTool(self.media, ["Microsoft:Category", "其他关键词"])

        evidence = marker.verify_compliance(self.media, runner)

        self.assertEqual("未标记", evidence.result)
        self.assertIn("其他关键词", evidence.actual_value)
        self.assertEqual(0, runner.raw_xmp_calls)

    def test_target_in_rdf_seq_is_not_compliant(self) -> None:
        subjects = [marker.MARKER]
        runner = FakeExifTool(
            self.media,
            subjects,
            make_xmp(subjects, container="Seq"),
        )

        evidence = marker.verify_compliance(self.media, runner)

        self.assertEqual("失败", evidence.result)
        self.assertIn("rdf:Bag/rdf:li", evidence.error)

    def test_wrong_dc_namespace_is_not_compliant(self) -> None:
        subjects = [marker.MARKER]
        runner = FakeExifTool(
            self.media,
            subjects,
            make_xmp(subjects, dc_namespace="https://example.invalid/dc"),
        )

        evidence = marker.verify_compliance(self.media, runner)

        self.assertEqual("失败", evidence.result)
        self.assertIn("rdf:Bag/rdf:li", evidence.error)

    def test_malformed_or_missing_raw_xmp_fails(self) -> None:
        cases = [
            ("", "未读取到原始 XMP"),
            ("<not-closed>", "无法解析"),
        ]
        for raw_xmp, expected_text in cases:
            with self.subTest(raw_xmp=raw_xmp):
                runner = FakeExifTool(self.media, [marker.MARKER], raw_xmp)
                evidence = marker.verify_compliance(self.media, runner)
                self.assertEqual("失败", evidence.result)
                self.assertIn(expected_text, evidence.xmp_structure + evidence.error)

    def test_utf16_raw_xmp_is_parsed_using_its_xml_declaration(self) -> None:
        subjects = [marker.MARKER]
        raw_xmp = make_xmp(subjects).replace("UTF-8", "UTF-16").encode("utf-16")
        runner = FakeExifTool(self.media, subjects, raw_xmp)

        evidence = marker.verify_compliance(self.media, runner)

        self.assertEqual("通过", evidence.result)


class MarkingAndReadOnlyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.input_dir = Path(self.temporary.name) / "待标记"
        self.input_dir.mkdir()
        self.media = self.input_dir / "中文 名称（测试）.mp4"
        self.original_bytes = b"opaque placeholder bytes; fake runner only"
        self.media.write_bytes(self.original_bytes)

    def test_write_then_strictly_read_back_and_preserve_existing_subject(self) -> None:
        runner = FakeExifTool(self.media, ["保留的关键词"])

        result = marker.process_one(
            self.media,
            self.input_dir,
            runner,
            exiftool_version="13.59",
        )

        self.assertEqual("新增", result.status)
        self.assertEqual("通过", result.verification_result)
        self.assertEqual(1, runner.add_calls)
        self.assertEqual(["保留的关键词", marker.MARKER], runner.subjects)
        self.assertGreaterEqual(runner.read_calls, 2)
        self.assertEqual(1, runner.raw_xmp_calls)

    def test_second_run_is_idempotent_and_does_not_append_duplicate(self) -> None:
        runner = FakeExifTool(self.media, ["保留的关键词"])

        first = marker.process_one(self.media, self.input_dir, runner, "13.59")
        second = marker.process_one(self.media, self.input_dir, runner, "13.59")

        self.assertEqual("新增", first.status)
        self.assertEqual("原本已合规", second.status)
        self.assertEqual(1, runner.add_calls)
        self.assertEqual(1, runner.subjects.count(marker.MARKER))
        self.assertIn("保留的关键词", runner.subjects)

    def test_post_write_structure_mismatch_is_reported_as_failure(self) -> None:
        runner = FakeExifTool(
            self.media,
            [],
            make_xmp([], container="Seq"),
            update_xmp_after_write=False,
        )

        result = marker.process_one(self.media, self.input_dir, runner, "13.59")

        self.assertEqual("失败", result.status)
        self.assertEqual("失败", result.verification_result)
        self.assertEqual(1, runner.add_calls)
        self.assertIn("rdf:Bag/rdf:li", result.error)

    def test_read_only_verification_never_writes_media_or_calls_add(self) -> None:
        runner = FakeExifTool(self.media, [marker.MARKER])
        before = hashlib.sha256(self.media.read_bytes()).hexdigest()

        result = marker.verify_one(
            self.media,
            self.input_dir,
            runner,
            exiftool_version="13.59",
        )
        after = hashlib.sha256(self.media.read_bytes()).hexdigest()

        self.assertEqual("验证通过", result.status)
        self.assertEqual("通过", result.verification_result)
        self.assertEqual(0, runner.add_calls)
        self.assertEqual(before, after)
        self.assertEqual(self.original_bytes, self.media.read_bytes())


class ScanAndCsvTests(unittest.TestCase):
    def test_scan_recurses_and_accepts_chinese_names_and_uppercase_suffixes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            input_dir = Path(temporary) / "待标记"
            nested = input_dir / "中文 子目录（第一层）" / "第二层"
            skipped = input_dir / "跳过的重解析目录"
            nested.mkdir(parents=True)
            skipped.mkdir(parents=True)

            expected_relative = {
                "根目录.JPEG",
                str(Path("中文 子目录（第一层）") / "第二层" / "图片.JPG"),
                str(Path("中文 子目录（第一层）") / "第二层" / "透明.PNG"),
                str(Path("中文 子目录（第一层）") / "第二层" / "视频.MP4"),
            }
            for relative_path in expected_relative:
                path = input_dir / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"placeholder")
            (nested / "忽略.TXT").write_text("ignored", encoding="utf-8")
            (skipped / "不能进入.JPG").write_bytes(b"placeholder")

            original_reparse_check = marker.is_reparse_directory

            def fake_reparse_check(path: Path) -> bool:
                if path.name == skipped.name:
                    return True
                return original_reparse_check(path)

            with mock.patch.object(
                marker,
                "is_reparse_directory",
                side_effect=fake_reparse_check,
            ):
                files, issues = marker.discover_media(input_dir)

            actual_relative = {str(path.relative_to(input_dir)) for path in files}
            self.assertEqual(expected_relative, actual_relative)
            self.assertEqual(1, len(issues))
            self.assertEqual(skipped.name, issues[0].relative_path)
            self.assertIn("重解析点", issues[0].error)

    def test_scan_rejects_reparse_point_as_input_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            input_dir = Path(temporary) / "待标记"
            input_dir.mkdir()
            (input_dir / "不应扫描.JPG").write_bytes(b"placeholder")

            with mock.patch.object(
                marker,
                "is_reparse_directory",
                return_value=True,
            ):
                files, issues = marker.discover_media(input_dir)

            self.assertEqual([], files)
            self.assertEqual(1, len(issues))
            self.assertEqual(".", issues[0].relative_path)
            self.assertIn("根目录", issues[0].error)

    def test_csv_has_utf8_bom_and_exactly_eleven_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log_dir = Path(temporary) / "运行记录"
            result = marker.ProcessResult(
                relative_path="中文,名称.jpg",
                media_format="JPG",
                status="验证通过",
                error="",
                operation=marker.MODE_VERIFY,
                verification_result="通过",
                verification_field=marker.VERIFICATION_FIELD,
                actual_value=json.dumps([marker.MARKER], ensure_ascii=False),
                xmp_structure="已确认 rdf:Bag/rdf:li",
                verified_at="2026-07-25T16:00:00+08:00",
                exiftool_version="13.59",
            )

            log_path = marker.write_csv_log([result], log_dir)
            raw = log_path.read_bytes()

            self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
            with log_path.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.reader(handle))
            self.assertEqual(2, len(rows))
            self.assertEqual(11, len(rows[0]))
            self.assertEqual(11, len(rows[1]))
            self.assertEqual(
                [
                    "相对路径",
                    "格式",
                    "运行模式",
                    "处理状态",
                    "验证结果",
                    "验证字段",
                    "实际读取值",
                    "XMP结构",
                    "验证时间",
                    "ExifTool版本",
                    "错误原因",
                ],
                rows[0],
            )
            self.assertEqual("中文,名称.jpg", rows[1][0])
            self.assertEqual("13.59", rows[1][9])
            self.assertEqual([], list(log_dir.glob("*.tmp")))

    def test_csv_neutralizes_spreadsheet_formula_prefixes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log_dir = Path(temporary) / "运行记录"
            result = marker.ProcessResult(
                relative_path="=HYPERLINK(\"https://example.invalid\")",
                media_format="JPG",
                status="失败",
                error="+危险公式",
            )

            log_path = marker.write_csv_log([result], log_dir)
            with log_path.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.reader(handle))

        self.assertTrue(rows[1][0].startswith("'="))
        self.assertTrue(rows[1][10].startswith("'+"))


class SelfTestDiagnosticsTests(unittest.TestCase):
    def test_self_test_writes_runtime_versions_to_requested_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report_path = Path(temporary) / "自检报告.txt"
            interpreter = mock.Mock()
            interpreter.eval.return_value = "8.6.14"
            with (
                mock.patch.dict(
                    marker.os.environ,
                    {marker.SELF_TEST_REPORT_ENV: str(report_path)},
                ),
                mock.patch.object(marker.tk, "Tcl", return_value=interpreter),
                mock.patch.object(
                    marker.ExifToolRunner,
                    "ensure_available",
                    return_value="13.59",
                ),
            ):
                return_code = marker.run_self_test()

            self.assertEqual(0, return_code)
            report = report_path.read_text(encoding="utf-8")
            self.assertIn("Python=3.14.6", report)
            self.assertIn("Tcl=8.6.14", report)
            self.assertIn("ExifTool=13.59", report)
            self.assertIn("Result=ok", report)


class VersionConsistencyTests(unittest.TestCase):
    def test_source_project_and_evidence_versions_are_consistent(self) -> None:
        pyproject_text = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        version_match = re.search(
            r'(?m)^version\s*=\s*"([^"]+)"\s*$',
            pyproject_text,
        )
        self.assertIsNotNone(version_match)
        self.assertEqual(marker.APP_VERSION, version_match.group(1))

        lock = json.loads(
            (PROJECT_ROOT / "packaging" / "exiftool.lock.json").read_text(
                encoding="utf-8"
            )
        )
        with tempfile.TemporaryDirectory() as temporary:
            input_dir = Path(temporary)
            media = input_dir / "占位.png"
            media.write_bytes(b"placeholder")
            runner = FakeExifTool(media, [marker.MARKER])
            result = marker.verify_one(
                media,
                input_dir,
                runner,
                exiftool_version=str(lock["version"]),
            )
        self.assertEqual(str(lock["version"]), result.exiftool_version)
        self.assertEqual("13.59", result.exiftool_version)


if __name__ == "__main__":
    unittest.main()

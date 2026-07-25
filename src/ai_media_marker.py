from __future__ import annotations

import csv
import ctypes
import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import messagebox
import tkinter as tk
from tkinter import scrolledtext, ttk
from xml.etree import ElementTree


MARKER = "contains-synthetic-performer"
APP_VERSION = "1.0.0"
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".mp4"}
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DC_NAMESPACE = "http://purl.org/dc/elements/1.1/"
RDF_NAMESPACE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
VERIFICATION_FIELD = "XMP-dc:Subject"
VERIFICATION_STRUCTURE = "rdf:Bag/rdf:li"
MODE_MARK = "标记并验证"
MODE_VERIFY = "只读验证"
SELF_TEST_REPORT_ENV = "AI_MEDIA_MARKER_SELF_TEST_REPORT"

if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
    EXIFTOOL_PATH = APP_DIR / "exiftool" / "exiftool.exe"
else:
    PROJECT_DIR = Path(__file__).resolve().parent.parent
    APP_DIR = Path(
        os.environ.get("AI_MEDIA_MARKER_WORK_DIR", PROJECT_DIR / "dev")
    ).resolve()
    EXIFTOOL_PATH = Path(
        os.environ.get(
            "AI_MEDIA_MARKER_EXIFTOOL",
            PROJECT_DIR / "runtime" / "exiftool" / "exiftool.exe",
        )
    ).resolve()

INPUT_DIR = APP_DIR / "待标记"
LOG_DIR = APP_DIR / "运行记录"


class MarkerError(RuntimeError):
    """A user-facing processing error."""


@dataclass(frozen=True)
class ScanIssue:
    relative_path: str
    error: str


@dataclass(frozen=True)
class ProcessResult:
    relative_path: str
    media_format: str
    status: str
    error: str = ""
    operation: str = MODE_MARK
    verification_result: str = ""
    verification_field: str = VERIFICATION_FIELD
    actual_value: str = ""
    xmp_structure: str = ""
    verified_at: str = ""
    exiftool_version: str = ""


@dataclass(frozen=True)
class VerificationEvidence:
    result: str
    actual_value: str
    xmp_structure: str
    verified_at: str
    exiftool_version: str
    error: str = ""


class SingleInstance:
    """Prevent two copies of the tool from modifying the same folder at once."""

    ERROR_ALREADY_EXISTS = 183

    def __init__(self) -> None:
        self._handle: int | None = None
        self.already_running = False
        if os.name != "nt":
            return

        digest = hashlib.sha256(str(APP_DIR).casefold().encode("utf-8")).hexdigest()[:16]
        mutex_name = f"Local\\AiMediaSyntheticMarker_{digest}"
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_bool

        handle = kernel32.CreateMutexW(None, False, mutex_name)
        if not handle:
            raise MarkerError("无法创建单实例锁，请重新启动工具。")
        if ctypes.get_last_error() == self.ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            self.already_running = True
            return

        self._handle = int(handle)
        self._kernel32 = kernel32

    def close(self) -> None:
        if self._handle is not None:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


class ExifToolRunner:
    """Run the bundled ExifTool without a shell or visible console window."""

    def __init__(self, executable: Path) -> None:
        self.executable = executable.resolve()

    def ensure_available(self) -> str:
        if not self.executable.is_file():
            raise MarkerError(f"缺少 ExifTool：{self.executable}")
        completed = self._run(["-ver"], timeout=30)
        version = completed.stdout.strip()
        if not version:
            raise MarkerError("ExifTool 可以启动，但没有返回版本号。")
        return version

    def read_subjects(self, path: Path) -> list[str]:
        completed = self._run(
            [
                "-j",
                "-struct",
                "-G1",
                "-s",
                "-XMP-dc:Subject",
                os.path.abspath(path),
            ],
            timeout=300,
        )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise MarkerError(f"无法解析 ExifTool 返回的元数据：{exc}") from exc

        if not isinstance(payload, list) or not payload:
            raise MarkerError("ExifTool 未返回有效的媒体元数据。")

        value = payload[0].get("XMP-dc:Subject")
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item) for item in value if item is not None]
        return [str(value)]

    def add_marker(self, path: Path) -> None:
        self._run(
            [
                "-overwrite_original",
                "-P",
                f"-XMP-dc:Subject+={MARKER}",
                os.path.abspath(path),
            ],
            timeout=None,
        )

    def image_data_hash(self, path: Path) -> str:
        completed = self._run(
            [
                "-q",
                "-q",
                "-api",
                "RequestAll=3",
                "-s3",
                "-ImageDataHash",
                os.path.abspath(path),
            ],
            timeout=300,
        )
        return completed.stdout.strip()

    def raw_xmp(self, path: Path) -> bytes:
        return self._run_bytes(
            ["-q", "-q", "-b", "-XMP", os.path.abspath(path)],
            timeout=300,
        ).stdout

    def _run(self, arguments: list[str], timeout: int | None) -> subprocess.CompletedProcess[str]:
        completed = self._run_bytes(arguments, timeout)
        stdout = completed.stdout.decode("utf-8-sig", errors="replace")
        stderr = completed.stderr.decode("utf-8-sig", errors="replace")
        return subprocess.CompletedProcess(
            completed.args,
            completed.returncode,
            stdout,
            stderr,
        )

    def _run_bytes(
        self,
        arguments: list[str],
        timeout: int | None,
    ) -> subprocess.CompletedProcess[bytes]:
        if not self.executable.is_file():
            raise MarkerError(f"缺少 ExifTool：{self.executable}")

        command = [
            str(self.executable),
            "-charset",
            "filename=UTF8",
            "-@",
            "-",
        ]
        argument_stream = ("\n".join(arguments) + "\n").encode("utf-8")

        try:
            completed = subprocess.run(
                command,
                input=argument_stream,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=CREATE_NO_WINDOW,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise MarkerError("ExifTool 读取超时。文件可能过大、损坏或正在被其他程序占用。") from exc
        except OSError as exc:
            raise MarkerError(f"无法启动 ExifTool：{exc}") from exc

        if completed.returncode != 0:
            stdout = completed.stdout.decode("utf-8-sig", errors="replace")
            stderr = completed.stderr.decode("utf-8-sig", errors="replace")
            detail = (
                stderr.strip()
                or stdout.strip()
                or f"退出码 {completed.returncode}"
            ).strip()
            raise MarkerError(detail)
        return completed


def windows_file_safety_issue(path: Path) -> str | None:
    """Reject attributes that ExifTool may not preserve safely in no-backup mode."""

    if os.name != "nt":
        return None

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetFileAttributesW.argtypes = [ctypes.c_wchar_p]
    kernel32.GetFileAttributesW.restype = ctypes.c_uint32
    attributes = kernel32.GetFileAttributesW(os.path.abspath(path))
    if attributes == 0xFFFFFFFF:
        return f"无法读取文件属性（Windows 错误 {ctypes.get_last_error()}）"

    flags = (
        (0x0001, "文件为只读"),
        (0x0002, "文件为隐藏文件"),
        (0x0004, "文件为系统文件"),
        (0x0400, "文件为重解析点或符号链接"),
    )
    for flag, message in flags:
        if attributes & flag:
            return message
    return None


def is_reparse_directory(path: Path) -> bool:
    if path.is_symlink():
        return True
    if os.name != "nt":
        return False
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetFileAttributesW.argtypes = [ctypes.c_wchar_p]
    kernel32.GetFileAttributesW.restype = ctypes.c_uint32
    attributes = kernel32.GetFileAttributesW(os.path.abspath(path))
    return attributes != 0xFFFFFFFF and bool(attributes & 0x0400)


def discover_media(input_dir: Path) -> tuple[list[Path], list[ScanIssue]]:
    input_dir.mkdir(parents=True, exist_ok=True)
    if is_reparse_directory(input_dir):
        return [], [
            ScanIssue(
                ".",
                "“待标记”根目录不能是符号链接、联接点或其他重解析点",
            )
        ]
    files: list[Path] = []
    issues: list[ScanIssue] = []

    def on_walk_error(error: OSError) -> None:
        failed_path = Path(error.filename) if error.filename else input_dir
        try:
            relative = str(failed_path.relative_to(input_dir))
        except ValueError:
            relative = str(failed_path)
        issues.append(ScanIssue(relative or ".", f"无法访问目录：{error}"))

    for root, directory_names, file_names in os.walk(
        input_dir,
        topdown=True,
        onerror=on_walk_error,
        followlinks=False,
    ):
        root_path = Path(root)
        retained_directories: list[str] = []
        for directory_name in directory_names:
            directory_path = root_path / directory_name
            if is_reparse_directory(directory_path):
                relative = str(directory_path.relative_to(input_dir))
                issues.append(ScanIssue(relative, "已跳过重解析点或符号链接目录"))
            else:
                retained_directories.append(directory_name)
        directory_names[:] = retained_directories

        for file_name in file_names:
            path = root_path / file_name
            if path.suffix.casefold() not in SUPPORTED_EXTENSIONS:
                continue
            if path.is_symlink():
                issues.append(
                    ScanIssue(str(path.relative_to(input_dir)), "已跳过符号链接文件")
                )
                continue
            files.append(path)

    files.sort(key=lambda item: str(item.relative_to(input_dir)).casefold())
    issues.sort(key=lambda item: item.relative_path.casefold())
    return files, issues


def format_subjects(subjects: list[str]) -> str:
    if not subjects:
        return "（空）"
    return json.dumps(subjects, ensure_ascii=False)


def csv_safe_cell(value: object) -> str:
    """Prevent spreadsheet formulas when a UTF-8 CSV is opened in Excel."""

    text = str(value)
    first_visible = text.lstrip(" ")
    if first_visible.startswith(("=", "+", "-", "@", "\t", "\r", "\n")):
        return "'" + text
    return text


def verify_compliance(
    path: Path,
    runner: ExifToolRunner,
    exiftool_version: str = "",
    subjects: list[str] | None = None,
) -> VerificationEvidence:
    verified_at = datetime.now().astimezone().isoformat(timespec="seconds")
    actual_value = "读取失败"
    try:
        if subjects is None:
            subjects = runner.read_subjects(path)
        actual_value = format_subjects(subjects)
        if MARKER not in subjects:
            return VerificationEvidence(
                "未标记",
                actual_value,
                "未找到目标 rdf:li",
                verified_at,
                exiftool_version,
            )

        raw_xmp = runner.raw_xmp(path)
        if not raw_xmp.strip():
            return VerificationEvidence(
                "失败",
                actual_value,
                "未读取到原始 XMP",
                verified_at,
                exiftool_version,
                "字段读取到了目标值，但没有读取到可验证的原始 XMP 数据包。",
            )

        try:
            root = ElementTree.fromstring(raw_xmp)
        except ElementTree.ParseError as exc:
            return VerificationEvidence(
                "失败",
                actual_value,
                "原始 XMP XML 无法解析",
                verified_at,
                exiftool_version,
                f"原始 XMP XML 无法解析：{exc}",
            )

        subject_items = root.findall(
            f".//{{{DC_NAMESPACE}}}subject/"
            f"{{{RDF_NAMESPACE}}}Bag/"
            f"{{{RDF_NAMESPACE}}}li"
        )
        bag_values = [(item.text or "") for item in subject_items]
        if MARKER not in bag_values:
            return VerificationEvidence(
                "失败",
                actual_value,
                "未找到目标 rdf:Bag/rdf:li",
                verified_at,
                exiftool_version,
                (
                    "XMP-dc:Subject 读取到了目标值，但原始 XMP 中没有找到"
                    "要求的 rdf:Bag/rdf:li 结构。"
                ),
            )

        return VerificationEvidence(
            "通过",
            actual_value,
            f"已确认 {VERIFICATION_STRUCTURE}",
            verified_at,
            exiftool_version,
        )
    except Exception as exc:
        detail = str(exc).strip() or exc.__class__.__name__
        return VerificationEvidence(
            "失败",
            actual_value,
            "验证未完成",
            verified_at,
            exiftool_version,
            detail,
        )


def result_from_evidence(
    relative_path: str,
    media_format: str,
    status: str,
    operation: str,
    evidence: VerificationEvidence,
    error: str = "",
) -> ProcessResult:
    return ProcessResult(
        relative_path,
        media_format,
        status,
        error or evidence.error,
        operation,
        evidence.result,
        VERIFICATION_FIELD,
        evidence.actual_value,
        evidence.xmp_structure,
        evidence.verified_at,
        evidence.exiftool_version,
    )


def process_one(
    path: Path,
    input_dir: Path,
    runner: ExifToolRunner,
    exiftool_version: str = "",
) -> ProcessResult:
    relative_path = str(path.relative_to(input_dir))
    media_format = path.suffix.lstrip(".").upper()
    subjects: list[str] = []

    try:
        subjects = runner.read_subjects(path)
        if MARKER in subjects:
            evidence = verify_compliance(path, runner, exiftool_version, subjects)
            status = "原本已合规" if evidence.result == "通过" else "失败"
            return result_from_evidence(
                relative_path,
                media_format,
                status,
                MODE_MARK,
                evidence,
            )

        safety_issue = windows_file_safety_issue(path)
        if safety_issue:
            evidence = VerificationEvidence(
                "未执行",
                format_subjects(subjects),
                "未验证",
                datetime.now().astimezone().isoformat(timespec="seconds"),
                exiftool_version,
            )
            return result_from_evidence(
                relative_path,
                media_format,
                "失败",
                MODE_MARK,
                evidence,
                safety_issue,
            )

        runner.add_marker(path)
        evidence = verify_compliance(path, runner, exiftool_version)
        status = "新增" if evidence.result == "通过" else "失败"
        return result_from_evidence(
            relative_path,
            media_format,
            status,
            MODE_MARK,
            evidence,
        )
    except Exception as exc:
        detail = str(exc).strip() or exc.__class__.__name__
        evidence = VerificationEvidence(
            "失败",
            format_subjects(subjects),
            "验证未完成",
            datetime.now().astimezone().isoformat(timespec="seconds"),
            exiftool_version,
            detail,
        )
        return result_from_evidence(
            relative_path,
            media_format,
            "失败",
            MODE_MARK,
            evidence,
        )


def verify_one(
    path: Path,
    input_dir: Path,
    runner: ExifToolRunner,
    exiftool_version: str = "",
) -> ProcessResult:
    relative_path = str(path.relative_to(input_dir))
    media_format = path.suffix.lstrip(".").upper()
    evidence = verify_compliance(path, runner, exiftool_version)
    if evidence.result == "通过":
        status = "验证通过"
    elif evidence.result == "未标记":
        status = "未标记"
    else:
        status = "验证失败"
    return result_from_evidence(
        relative_path,
        media_format,
        status,
        MODE_VERIFY,
        evidence,
    )


def write_csv_log(results: list[ProcessResult], log_dir: Path) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    operation = results[0].operation if results else MODE_MARK
    prefix = "只读验证结果" if operation == MODE_VERIFY else "标记与验证结果"
    log_path = log_dir / f"{prefix}_{timestamp}.csv"
    temporary_path = log_dir / f".{log_path.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary_path.open("x", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(
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
                ]
            )
            for result in results:
                writer.writerow(
                    [
                        csv_safe_cell(value)
                        for value in (
                            result.relative_path,
                            result.media_format,
                            result.operation,
                            result.status,
                            result.verification_result,
                            result.verification_field,
                            result.actual_value,
                            result.xmp_structure,
                            result.verified_at,
                            result.exiftool_version,
                            result.error,
                        )
                    ]
                )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, log_path)
    finally:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
    return log_path


def ensure_log_directory_writable(log_dir: Path) -> None:
    probe = log_dir / f".write-test-{uuid.uuid4().hex}.tmp"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        with probe.open("x", encoding="utf-8") as handle:
            handle.write("ok")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise MarkerError(f"“运行记录”目录不可写：{exc}") from exc
    finally:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass


class MarkerApplication:
    POLL_INTERVAL_MS = 100

    def __init__(self, root: tk.Tk, runner: ExifToolRunner) -> None:
        self.root = root
        self.runner = runner
        self.events: queue.Queue[tuple] = queue.Queue()
        self.media_files: list[Path] = []
        self.scan_issues: list[ScanIssue] = []
        self.results: list[ProcessResult] = []
        self.running = False
        self.scanning = True
        self.log_path: Path | None = None
        self.exiftool_version = ""
        self.current_mode = MODE_MARK

        self.scan_status = tk.StringVar(value="正在扫描“待标记”文件夹……")
        self.current_file = tk.StringVar(value="尚未开始")
        self.first_count_label = tk.StringVar(value="新增")
        self.second_count_label = tk.StringVar(value="原本已合规")
        self.third_count_label = tk.StringVar(value="失败")
        self.added_count = tk.IntVar(value=0)
        self.compliant_count = tk.IntVar(value=0)
        self.failed_count = tk.IntVar(value=0)

        self._build_window()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close_requested)
        self.root.after(self.POLL_INTERVAL_MS, self._poll_events)
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _build_window(self) -> None:
        self.root.title(f"AI 人物媒体一键标记 v{APP_VERSION}")
        self.root.geometry("820x650")
        self.root.minsize(740, 560)

        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            outer,
            text=f"AI 人物媒体一键标记 v{APP_VERSION}",
            font=("Microsoft YaHei UI", 16, "bold"),
        ).pack(anchor=tk.W)

        ttk.Label(
            outer,
            text=f"目标目录：{INPUT_DIR}",
            wraplength=735,
        ).pack(anchor=tk.W, pady=(12, 2))
        ttk.Label(
            outer,
            text="支持格式：JPG、JPEG、PNG、MP4（包含子文件夹）",
        ).pack(anchor=tk.W)

        warning = tk.Label(
            outer,
            text=(
                "重要：工具不会识别媒体是否含 AI 人物。开始标记会直接修改原件，"
                "并且不会生成备份文件。"
            ),
            fg="#b42318",
            bg="#fef3f2",
            font=("Microsoft YaHei UI", 10, "bold"),
            padx=10,
            pady=9,
            anchor=tk.W,
        )
        warning.pack(fill=tk.X, pady=(14, 12))

        read_only_note = tk.Label(
            outer,
            text=(
                "请只放入已经人工确认需要该标签的媒体。"
                "只想检查？点击“只读验证”，不会修改任何媒体。"
                "MP4 在 Windows 属性页可能不显示“标记”，本工具以 ExifTool 严格回读 XMP 为准。"
            ),
            fg="#175cd3",
            bg="#eff8ff",
            padx=10,
            pady=9,
            anchor=tk.W,
            justify=tk.LEFT,
            wraplength=755,
        )
        read_only_note.pack(fill=tk.X, pady=(0, 12))

        ttk.Label(outer, textvariable=self.scan_status).pack(anchor=tk.W)

        self.progress = ttk.Progressbar(outer, mode="determinate", maximum=1)
        self.progress.pack(fill=tk.X, pady=(10, 5))
        ttk.Label(
            outer,
            textvariable=self.current_file,
            wraplength=735,
        ).pack(anchor=tk.W)

        counters = ttk.Frame(outer)
        counters.pack(fill=tk.X, pady=(12, 8))
        self._counter(counters, self.first_count_label, self.added_count).pack(
            side=tk.LEFT, padx=(0, 24)
        )
        self._counter(counters, self.second_count_label, self.compliant_count).pack(
            side=tk.LEFT, padx=(0, 24)
        )
        self._counter(counters, self.third_count_label, self.failed_count).pack(
            side=tk.LEFT
        )

        self.details = scrolledtext.ScrolledText(
            outer,
            height=14,
            wrap=tk.WORD,
            font=("Consolas", 9),
            state=tk.DISABLED,
        )
        self.details.pack(fill=tk.BOTH, expand=True, pady=(4, 12))

        buttons = ttk.Frame(outer)
        buttons.pack(fill=tk.X)
        self.start_button = ttk.Button(
            buttons,
            text="开始标记",
            command=self._confirm_and_start,
            state=tk.DISABLED,
        )
        self.start_button.pack(side=tk.LEFT)
        self.verify_button = ttk.Button(
            buttons,
            text="只读验证",
            command=self._start_read_only_verification,
            state=tk.DISABLED,
        )
        self.verify_button.pack(side=tk.LEFT, padx=(8, 0))
        self.open_log_button = ttk.Button(
            buttons,
            text="查看运行记录",
            command=self._open_log,
            state=tk.DISABLED,
        )
        self.open_log_button.pack(side=tk.LEFT, padx=8)
        self.close_button = ttk.Button(
            buttons,
            text="关闭",
            command=self._on_close_requested,
        )
        self.close_button.pack(side=tk.RIGHT)

    @staticmethod
    def _counter(
        parent: ttk.Frame,
        label: tk.StringVar,
        value: tk.IntVar,
    ) -> ttk.Frame:
        frame = ttk.Frame(parent)
        ttk.Label(frame, textvariable=label).pack(side=tk.LEFT)
        ttk.Label(frame, text="：").pack(side=tk.LEFT)
        ttk.Label(frame, textvariable=value, font=("Microsoft YaHei UI", 10, "bold")).pack(
            side=tk.LEFT
        )
        return frame

    def _append_detail(self, text: str) -> None:
        self.details.configure(state=tk.NORMAL)
        self.details.insert(tk.END, text + "\n")
        self.details.see(tk.END)
        self.details.configure(state=tk.DISABLED)

    def _clear_details(self) -> None:
        self.details.configure(state=tk.NORMAL)
        self.details.delete("1.0", tk.END)
        self.details.configure(state=tk.DISABLED)

    def _scan_worker(self) -> None:
        try:
            version = self.runner.ensure_available()
            files, issues = discover_media(INPUT_DIR)
            self.events.put(("scan_complete", version, files, issues))
        except Exception as exc:
            self.events.put(("scan_failed", str(exc).strip() or exc.__class__.__name__))

    def _confirm_and_start(self) -> None:
        count = len(self.media_files)
        try:
            ensure_log_directory_writable(LOG_DIR)
        except MarkerError as exc:
            messagebox.showerror("无法开始", str(exc), parent=self.root)
            return

        confirmed = messagebox.askyesno(
            "确认开始",
            (
                f"即将检查并处理 {count} 个媒体文件。\n\n"
                "本工具不会判断媒体是否含 AI 人物；所有未合规的支持文件"
                "都会被添加标签。\n"
                "请确认“待标记”中的文件均已由人工判断为适用。\n\n"
                "文件将被直接修改，且不会生成备份。\n"
                "已有正确标记的文件不会重复写入。\n\n"
                "确认开始吗？"
            ),
            icon=messagebox.WARNING,
            parent=self.root,
        )
        if not confirmed:
            return

        self._begin_run(MODE_MARK)

    def _start_read_only_verification(self) -> None:
        try:
            ensure_log_directory_writable(LOG_DIR)
        except MarkerError as exc:
            messagebox.showerror("无法开始验证", str(exc), parent=self.root)
            return

        self._begin_run(MODE_VERIFY)

    def _begin_run(self, mode: str) -> None:
        count = len(self.media_files)
        self.running = True
        self.current_mode = mode
        self.results = []
        self.log_path = None
        self._clear_details()
        self.open_log_button.configure(state=tk.DISABLED)
        self.start_button.configure(state=tk.DISABLED)
        self.verify_button.configure(state=tk.DISABLED)
        self.close_button.configure(state=tk.DISABLED)
        self.progress.configure(maximum=max(1, count), value=0)
        if mode == MODE_VERIFY:
            self.first_count_label.set("验证通过")
            self.second_count_label.set("未标记")
            self.third_count_label.set("验证失败")
            self.current_file.set("准备进行只读验证……")
            self._append_detail("—— 开始只读验证（不会修改媒体）——")
        else:
            self.first_count_label.set("新增")
            self.second_count_label.set("原本已合规")
            self.third_count_label.set("失败")
            self.current_file.set("准备处理……")
            self._append_detail("—— 开始标记并验证 ——")
        self.added_count.set(0)
        self.compliant_count.set(0)
        self.failed_count.set(len(self.scan_issues))
        threading.Thread(
            target=self._process_worker,
            args=(mode,),
            daemon=False,
        ).start()

    def _process_worker(self, mode: str) -> None:
        verified_at = datetime.now().astimezone().isoformat(timespec="seconds")
        scan_failure_status = "验证失败" if mode == MODE_VERIFY else "失败"
        results: list[ProcessResult] = [
            ProcessResult(
                issue.relative_path,
                "",
                scan_failure_status,
                issue.error,
                mode,
                "失败",
                VERIFICATION_FIELD,
                "（未读取）",
                "未验证",
                verified_at,
                self.exiftool_version,
            )
            for issue in self.scan_issues
        ]
        counts = {"first": 0, "second": 0, "failed": len(self.scan_issues)}
        log_path: Path | None = None
        log_error = ""
        fatal_error = ""
        processed_count = 0
        try:
            total = len(self.media_files)
            for index, path in enumerate(self.media_files, start=1):
                relative_path = str(path.relative_to(INPUT_DIR))
                self.events.put(("current", index, total, relative_path))
                if mode == MODE_VERIFY:
                    result = verify_one(
                        path,
                        INPUT_DIR,
                        self.runner,
                        self.exiftool_version,
                    )
                else:
                    result = process_one(
                        path,
                        INPUT_DIR,
                        self.runner,
                        self.exiftool_version,
                    )
                results.append(result)
                if mode == MODE_VERIFY:
                    if result.verification_result == "通过":
                        counts["first"] += 1
                    elif result.verification_result == "未标记":
                        counts["second"] += 1
                    else:
                        counts["failed"] += 1
                elif result.status == "新增":
                    counts["first"] += 1
                elif result.status == "原本已合规":
                    counts["second"] += 1
                else:
                    counts["failed"] += 1
                processed_count = index
                self.events.put(("result", relative_path, result, counts.copy(), index))
        except Exception as exc:
            fatal_error = str(exc).strip() or exc.__class__.__name__
            remaining_files = self.media_files[processed_count:]
            if remaining_files:
                for path in remaining_files:
                    try:
                        relative_path = str(path.relative_to(INPUT_DIR))
                    except ValueError:
                        relative_path = str(path)
                    result = ProcessResult(
                        relative_path,
                        path.suffix.lstrip(".").upper(),
                        scan_failure_status,
                        f"处理线程意外中止，文件未处理：{fatal_error}",
                        mode,
                        "失败",
                        VERIFICATION_FIELD,
                        "（未读取）",
                        "未验证",
                        datetime.now().astimezone().isoformat(timespec="seconds"),
                        self.exiftool_version,
                    )
                    results.append(result)
                    counts["failed"] += 1
                    self.events.put(
                        (
                            "result",
                            relative_path,
                            result,
                            counts.copy(),
                            processed_count,
                        )
                    )
            else:
                results.append(
                    ProcessResult(
                        "<处理线程>",
                        "",
                        scan_failure_status,
                        fatal_error,
                        mode,
                        "失败",
                        VERIFICATION_FIELD,
                        "（未读取）",
                        "未验证",
                        datetime.now().astimezone().isoformat(timespec="seconds"),
                        self.exiftool_version,
                    )
                )
                counts["failed"] += 1
        finally:
            try:
                log_path = write_csv_log(results, LOG_DIR)
            except Exception as exc:
                log_error = str(exc).strip() or exc.__class__.__name__

        self.events.put(
            ("finished", mode, results, counts, log_path, log_error, fatal_error)
        )

    def _poll_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                self._handle_event(event)
        except queue.Empty:
            pass
        self.root.after(self.POLL_INTERVAL_MS, self._poll_events)

    def _handle_event(self, event: tuple) -> None:
        event_type = event[0]
        if event_type == "scan_complete":
            _, version, files, issues = event
            self.scanning = False
            self.exiftool_version = version
            self.media_files = files
            self.scan_issues = issues
            issue_text = f"，另有 {len(issues)} 个扫描问题" if issues else ""
            self.scan_status.set(
                f"扫描完成：找到 {len(files)} 个支持的媒体文件{issue_text}；ExifTool {version}"
            )
            self.failed_count.set(len(issues))
            if files:
                self.start_button.configure(state=tk.NORMAL)
                self.verify_button.configure(state=tk.NORMAL)
            else:
                self.current_file.set("“待标记”中没有 JPG、JPEG、PNG 或 MP4 文件。")
            for issue in issues:
                self._append_detail(f"[扫描问题] {issue.relative_path}：{issue.error}")
            return

        if event_type == "scan_failed":
            self.scanning = False
            error = event[1]
            self.start_button.configure(state=tk.DISABLED)
            self.verify_button.configure(state=tk.DISABLED)
            self.scan_status.set("启动检查失败")
            self.current_file.set(error)
            self._append_detail(f"[启动失败] {error}")
            messagebox.showerror("无法启动", error, parent=self.root)
            return

        if event_type == "current":
            _, index, total, relative_path = event
            action = "只读验证" if self.current_mode == MODE_VERIFY else "处理"
            self.current_file.set(f"正在{action} [{index}/{total}]：{relative_path}")
            return

        if event_type == "result":
            _, _, result, counts, progress_value = event
            self.added_count.set(counts["first"])
            self.compliant_count.set(counts["second"])
            self.failed_count.set(counts["failed"])
            self.progress.configure(value=progress_value)
            self._append_detail(f"[{result.status}] {result.relative_path}")
            if result.verification_field:
                self._append_detail(
                    f"    字段：{result.verification_field} = {result.actual_value or '（未读取）'}"
                )
            if result.xmp_structure:
                self._append_detail(f"    结构：{result.xmp_structure}")
            if result.error:
                self._append_detail(f"    原因：{result.error}")
            return

        if event_type == "finished":
            _, mode, results, counts, log_path, log_error, fatal_error = event
            self.results = results
            self.log_path = log_path
            self.running = False
            self.added_count.set(counts["first"])
            self.compliant_count.set(counts["second"])
            self.failed_count.set(counts["failed"])
            self.close_button.configure(state=tk.NORMAL)
            if self.media_files:
                self.start_button.configure(state=tk.NORMAL)
                self.verify_button.configure(state=tk.NORMAL)
            if log_path:
                self.open_log_button.configure(state=tk.NORMAL)

            if mode == MODE_VERIFY:
                self.current_file.set("只读验证完成（未修改任何媒体）")
                self._append_detail("—— 只读验证完成：本次未修改任何媒体 ——")
                summary = (
                    "本次为只读验证，没有修改任何媒体文件。\n\n"
                    f"验证通过：{counts['first']}\n"
                    f"未标记：{counts['second']}\n"
                    f"验证失败：{counts['failed']}"
                )
                title = (
                    "只读验证完成（发现未合规文件）"
                    if counts["second"] or counts["failed"] or log_error or fatal_error
                    else "只读验证完成"
                )
                show_warning = bool(
                    counts["second"] or counts["failed"] or log_error or fatal_error
                )
            else:
                verified_total = counts["first"] + counts["second"]
                self.current_file.set("标记与严格验证完成")
                self._append_detail("—— 标记与严格验证完成 ——")
                summary = (
                    f"新增并验证通过：{counts['first']}\n"
                    f"原本已合规：{counts['second']}\n"
                    f"失败：{counts['failed']}\n\n"
                    f"XMP 合规验证通过合计：{verified_total}"
                )
                title = "处理完成（存在失败）" if counts["failed"] or log_error or fatal_error else "处理完成"
                show_warning = bool(counts["failed"] or log_error or fatal_error)

            summary += (
                f"\n\n验证字段：{VERIFICATION_FIELD}\n"
                f"要求结构：{VERIFICATION_STRUCTURE}\n"
                f"ExifTool 版本：{self.exiftool_version}\n\n"
                "提示：MP4 即使未在 Windows 属性页显示“标记”，"
                "\n只要这里显示“验证通过”，即代表上述 XMP 字段和结构均已确认。"
            )
            if log_path:
                try:
                    displayed_log_path = log_path.relative_to(APP_DIR)
                except ValueError:
                    displayed_log_path = log_path
                summary += f"\n\n运行记录：\n{displayed_log_path}"
            if log_error:
                summary += f"\n\n运行记录写入失败：{log_error}"
            if fatal_error:
                summary += f"\n\n处理线程意外中止：{fatal_error}"

            if show_warning:
                messagebox.showwarning(title, summary, parent=self.root)
            else:
                messagebox.showinfo(title, summary, parent=self.root)
            return

    def _open_log(self) -> None:
        if not self.log_path:
            return
        try:
            os.startfile(str(self.log_path))
        except OSError:
            try:
                os.startfile(str(self.log_path.parent))
            except OSError as exc:
                messagebox.showerror("无法打开运行记录", str(exc), parent=self.root)

    def _on_close_requested(self) -> None:
        if self.running:
            messagebox.showwarning(
                "任务正在进行",
                "正在处理或验证媒体，请等待当前任务全部完成后再关闭窗口。",
                parent=self.root,
            )
            return
        self.root.destroy()


def write_startup_error(error: BaseException) -> Path | None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        path = LOG_DIR / f"启动错误_{timestamp}.txt"
        path.write_text(
            "".join(traceback.format_exception(type(error), error, error.__traceback__)),
            encoding="utf-8",
        )
        return path
    except Exception:
        return None


def write_self_test_report(message: str) -> None:
    report_target = os.environ.get(SELF_TEST_REPORT_ENV)
    if not report_target:
        return
    try:
        Path(report_target).write_text(message, encoding="utf-8")
    except Exception:
        pass


def run_self_test() -> int:
    """Verify the frozen Python/Tcl runtime and adjacent ExifTool without a GUI."""

    details: list[str] = []
    try:
        details.append(
            "Python="
            f"{sys.version_info.major}.{sys.version_info.minor}."
            f"{sys.version_info.micro}"
        )
        interpreter = tk.Tcl()
        tcl_version = interpreter.eval("info patchlevel")
        details.append(f"Tcl={tcl_version}")
        if not tcl_version.startswith("8.6."):
            details.append("Result=unsupported Tcl version")
            write_self_test_report("\n".join(details) + "\n")
            return 1
        version = ExifToolRunner(EXIFTOOL_PATH).ensure_available()
        details.append(f"ExifTool={version or '(empty)'}")
        details.append(f"ExifToolPath={EXIFTOOL_PATH}")
        details.append(f"Result={'ok' if version else 'empty ExifTool version'}")
        write_self_test_report("\n".join(details) + "\n")
        return 0 if version else 1
    except Exception:
        details.append("Result=exception")
        details.append(traceback.format_exc())
        write_self_test_report("\n".join(details))
        return 1


def main() -> int:
    if sys.argv[1:] == ["--self-test"]:
        return run_self_test()

    instance: SingleInstance | None = None
    root: tk.Tk | None = None
    try:
        instance = SingleInstance()
        root = tk.Tk()
        if instance.already_running:
            root.withdraw()
            messagebox.showinfo("工具已在运行", "请使用已经打开的标记工具窗口。", parent=root)
            return 0

        MarkerApplication(root, ExifToolRunner(EXIFTOOL_PATH))
        root.mainloop()
        return 0
    except Exception as exc:
        log_path = write_startup_error(exc)
        detail = str(exc).strip() or exc.__class__.__name__
        if log_path:
            detail += f"\n\n错误记录：{log_path}"
        if root is None:
            root = tk.Tk()
            root.withdraw()
        messagebox.showerror("工具启动失败", detail, parent=root)
        return 1
    finally:
        if instance is not None:
            instance.close()
        if root is not None:
            try:
                root.destroy()
            except tk.TclError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())

# 新电脑构建与发布清单

本文是本项目在新 Windows 电脑上配置源码运行、正式构建和 GitHub 发布环境的
详细清单。日常使用便携版不需要这些开发依赖。

## 1. 基础环境

必须满足：

- Windows x64。
- Python **3.14.6 x64**，不能使用其他补丁版本。
- Python 安装中包含 `pip`、Python Launcher 和 Tcl/Tk（Tkinter）。
- GitHub Desktop 已登录，并已克隆
  `xuqiang97/ai-media-synthetic-marker`。
- 首次下载依赖和 ExifTool 时可以访问互联网。

如果需要由 Codex 或终端直接创建 PR、检查 Actions 或管理 Release，还应安装并
登录 GitHub CLI：

```powershell
gh auth login
gh auth status
```

GitHub CLI 不参与本地程序运行和构建，因此版本不由本项目锁定。项目也不需要
Visual Studio、.NET、Java 或 Node.js。

## 2. 同步仓库

在 GitHub Desktop 中切换到 `main`，依次执行 `Fetch origin` 和
`Pull origin`，确保本地没有未提交修改。也可以在项目根目录检查：

```powershell
git status --short --branch
```

开始修改时应从最新 `main` 创建独立分支；不要直接在过期分支上继续开发。

## 3. 验证 Python 与 Tkinter

```powershell
py -3.14 --version
py -3.14 -c "import sys, platform, tkinter; print(sys.version); print(platform.machine()); print('Tcl/Tk', tkinter.Tcl().eval('info patchlevel'))"
```

合格条件：

- Python 精确为 `3.14.6`。
- 架构为 `AMD64` 或 `x86_64`。
- Tcl/Tk 为 `8.6.x`。

构建脚本会再次检查这些条件。`pip` 自身不要求与其他电脑版本相同，只要能够完成
下一节的锁定安装即可。

## 4. 安装锁定的构建依赖

不要单独安装“最新版”PyInstaller。必须从项目锁定文件安装：

```powershell
py -3.14 -m pip install --require-hashes --only-binary=:all: -r requirements-build.lock
```

`requirements-build.lock` 固定了以下完整依赖集及每个安装包的 SHA-256：

| 依赖 | 版本 |
| --- | --- |
| `altgraph` | `0.17.5` |
| `packaging` | `26.2` |
| `pefile` | `2024.8.26` |
| `pyinstaller` | `6.21.0` |
| `pyinstaller-hooks-contrib` | `2026.6` |
| `pywin32-ctypes` | `0.2.3` |
| `setuptools` | `83.0.0` |

验证 PyInstaller：

```powershell
py -3.14 -m PyInstaller --version
```

必须输出 `6.21.0`。

## 5. 准备锁定的 ExifTool

本地 ExifTool 不上传 GitHub。新电脑首次配置时运行：

```powershell
py -3.14 scripts\fetch_exiftool.py
```

脚本会根据 `packaging/exiftool.lock.json` 下载并验证 Windows x64 版
ExifTool `13.59`，包括压缩包大小、SHA-256、解压路径和逐文件完整性清单。
不要手工替换或从其他电脑复制运行目录。

验证版本：

```powershell
.\runtime\exiftool\exiftool.exe -ver
```

必须输出 `13.59`。如果完整性检查失败，重新准备：

```powershell
py -3.14 scripts\fetch_exiftool.py --force
```

## 6. 启动与测试

源码版可以双击根目录的 `开发运行.cmd`，也可以运行：

```powershell
py -3.14 src\ai_media_marker.py
```

正式构建前运行完整测试：

```powershell
py -3.14 -m unittest discover -s tests -v
```

必须以 `OK` 结束。测试数量可能随项目演进而增加，不应把固定数量作为唯一判断
标准。

## 7. 正式构建

```powershell
py -3.14 scripts\build_release.py
```

构建脚本会检查：

- Windows、x64、Python 3.14.6 和 Tcl/Tk 8.6.x。
- PyInstaller 6.21.0 及全部锁定依赖。
- ExifTool 13.59、下载档案信息和逐文件完整性。
- 项目版本、源码版本、发布说明和许可证。
- 单元测试、EXE 无界面启动自检、ZIP 结构及隐私卫生。

成功后生成：

```text
dist/
├─ ai-media-synthetic-marker-v<项目版本>-windows-x64.zip
└─ SHA256SUMS.txt
```

`build/` 和 `dist/` 是本地生成物，不应提交 Git。

## 8. 发布到 GitHub

### 发布代码更新

1. 从最新 `main` 创建独立分支。
2. 只修改和暂存当前需求涉及的文件。
3. 运行相关测试；涉及构建或发布时运行完整构建。
4. 提交并推送分支，创建 PR。
5. 等待 GitHub Actions 的 Windows / Python 3.14.6 检查通过。
6. 合并到 `main`。
7. 其他电脑切换到 `main` 后执行 `Fetch origin` 和 `Pull origin`。

### 发布新的公开版本

GitHub Actions 的手动 `Build Windows release` 只构建并保存工作流产物，不会创建
公开 Release。只有推送与项目版本完全匹配的新 `v*` 标签，工作流才会在构建成功
后发布 GitHub Release。

发布新版本前必须统一更新 `pyproject.toml`、源码 `APP_VERSION`、测试和发布说明。
已发布标签不可移动、删除或复用；尤其不要重新使用现有 `v1.0.0`。

## 9. 不上云的本地内容

以下内容由 `.gitignore` 保护，拉取 GitHub 更新时通常会继续保留在本地：

- `runtime/exiftool/` 下的实际运行组件。
- `dev/待标记/` 中的私人媒体。
- `dev/运行记录/` 中的 CSV。
- `build/`、`dist/`、Python 缓存、日志和 `*_original` 备份。
- 本地环境变量文件。

提交前确认：

```powershell
git status --short
```

不得上传真实媒体、CSV 运行记录、本地 ExifTool、构建产物或包含本机绝对路径的
内容。

## 10. 最短验收命令

在项目根目录依次执行：

```powershell
py -3.14 -m pip install --require-hashes --only-binary=:all: -r requirements-build.lock
py -3.14 scripts\fetch_exiftool.py
py -3.14 -m unittest discover -s tests -v
py -3.14 scripts\build_release.py
```

四步全部成功，才表示这台电脑具备本项目的正式构建能力。

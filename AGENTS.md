# 项目维护指南

本文面向在新电脑或新对话中接手本项目的人类维护者与 AI 编程助手。开始工作前，
请完整阅读本文、[README.md](README.md) 和
[CONTRIBUTING.md](CONTRIBUTING.md)；涉及新电脑配置、构建或发布时，还必须阅读
[BUILDING.md](BUILDING.md)。然后只读检查 Git 状态、最近提交和相关源码。

如果需求仍处于交流或方案确认阶段，不要修改文件、安装依赖、构建、提交或发布。
只有在范围明确并得到实施授权后，才进行与需求直接相关的改动。

## 项目目标与边界

本项目是仅面向 Windows x64 的本地 Python/Tkinter 工具，用于在 JPG、JPEG、
PNG 和 MP4 中写入并严格验证以下披露标记：

```text
XMP-dc:Subject
└─ rdf:Bag
   └─ rdf:li = contains-synthetic-performer
```

必须保持以下产品边界：

- 工具不识别或判断媒体是否包含 AI 生成人物。
- 工具不上传媒体、不连接亚马逊，也不发送遥测数据。
- 合规依据只能是精确的 `XMP-dc:Subject` 及原始 XMP 中的
  `dc:subject/rdf:Bag/rdf:li`。
- `Microsoft:Category` 和 Windows 属性页中的“标记”不能替代上述 XMP 证据。
- “验证通过”只表示当前文件满足本工具的字段与结构检查，不代表法律意见或平台
  最终审核结论。

## 源码真源与目录职责

- `src/ai_media_marker.py`：唯一业务源码。
- `tests/`：标准库单元测试，不依赖公开真实媒体。
- `scripts/fetch_exiftool.py`：下载并校验锁定版本的 ExifTool。
- `scripts/build_release.py`：测试、构建、组包和发布卫生检查。
- `packaging/`：PyInstaller 配置、ExifTool 锁定信息和许可证。
- `runtime/exiftool/`：本地 ExifTool 运行时；实际程序文件被 Git 忽略。
- `dev/待标记/`：源码模式的本地输入目录。
- `dev/运行记录/`：源码模式的本地 CSV 输出目录。
- `release_template/`：便携包说明和空目录模板。

不要直接维护 `build/`、`dist/`、便携 EXE、旧教学版或其他生成物。需要发布时，
从统一源码和构建脚本重新生成。

## 不可破坏的实现规则

- 支持格式为 `.jpg`、`.jpeg`、`.png`、`.mp4`，扩展名不区分大小写。
- 扫描必须递归、稳定排序，并拒绝符号链接、联接点等重解析点。
- Subject 值使用区分大小写的完整字符串匹配。
- 写入前先读取 Subject；只有缺少目标值时才追加。
- 写入必须保留其他 Subject 关键词，并保持重复运行不会重复追加。
- 当前无备份写入使用 `-overwrite_original` 和 `-P`；不要暗示 `-P` 能保证所有
  文件时间或属性完全不变。
- 写入后必须重新读取字段和原始 XMP，并确认目标值位于正式 Dublin Core 与 RDF
  namespace 下的 `rdf:Bag/rdf:li`。
- 字段值与原始 XMP 结构不一致时应报告失败，不要自动猜测或修复。
- “只读验证”不能调用媒体写入逻辑；生成 CSV 运行记录是该模式唯一允许的输出。
- 单个文件失败不能中断其他文件。
- 不要宣称正常运行会逐文件验证图片像素或视频媒体流哈希。

## 隐私与仓库卫生

不得提交真实商品媒体、CSV 运行记录、日志、`*_original` 备份、本地 ExifTool
文件、构建目录、Python 缓存、环境变量文件或包含本机绝对路径的内容。

错误报告和测试证据必须先删除敏感文件名、路径和元数据。不要上传未获授权的媒体。
如果新增发布文件，必须继续遵守构建脚本的发布白名单和隐私检查。

## 开发与验证

项目要求 Python 3.14.6，并使用标准库 `unittest`：

```powershell
py -3.14 -m unittest discover -s tests -v
```

本地 ExifTool 未准备时，可运行：

```powershell
py -3.14 scripts\fetch_exiftool.py
```

该命令会联网下载并校验 `packaging/exiftool.lock.json` 锁定的版本。不要手工替换
ExifTool，也不要绕过大小、SHA-256、压缩包路径或 manifest 校验。

构建依赖必须使用锁定文件安装：

```powershell
py -3.14 -m pip install --require-hashes --only-binary=:all: -r requirements-build.lock
```

完整构建命令：

```powershell
py -3.14 scripts\build_release.py
```

不要擅自安装缺失工具。若环境不完整，先说明缺少什么并等待确认。普通源码改动至少
运行相关测试；涉及构建、打包、运行时定位或发布卫生时，应运行完整测试和构建检查。

## 版本与发布

- 版本以 `pyproject.toml` 为基准，并与源码中的 `APP_VERSION` 及相关测试保持一致。
- 准备新版本时，统一更新版本字段、测试和面向用户的发布说明。
- 已发布标签视为不可变；不要移动、删除或复用既有版本标签。
- 未经明确授权，不提交、不推送、不打标签、不创建 Release，也不改写公开历史。
- 发布前只暂存明确属于当前需求的文件，并检查工作树、暂存区差异和测试结果。

## 新环境接手清单

1. 确认当前目录、分支、远端地址和工作树状态。
2. 阅读本文、README、贡献指南及与需求直接相关的源码和测试。
3. 确认需求处于讨论阶段还是已经授权实施。
4. 检查 Python 版本；只有需要运行或构建时才检查本地 ExifTool 与构建依赖。
5. 先说明对当前状态、改动范围和风险的理解，再开始实施。

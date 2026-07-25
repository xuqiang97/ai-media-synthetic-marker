# AI 人物媒体 XMP 标记工具

一个面向 Windows 的本地桌面工具，用于在 JPG、JPEG、PNG 和 MP4
媒体中写入并严格验证：

```text
XMP-dc:Subject
└─ rdf:Bag
   └─ rdf:li = contains-synthetic-performer
```

普通用户无需输入命令行。程序不上传媒体，也不依赖 Windows 属性页
判断 MP4 是否合规。

本工具不会识别或判断媒体是否包含 AI 生成人物。“开始标记”会为
“待标记”中所有尚未合规的支持文件添加标签，因此只能放入已经人工
确认适用该披露要求的媒体。

## 核心功能

- 递归扫描“待标记”及其子文件夹。
- “开始标记”：保留已有 Subject 关键词，追加目标值，写入后严格回读。
- “只读验证”：不修改媒体，只检查 XMP 并生成 CSV 记录。
- 不把 `Microsoft:Category` 当作合规字段。
- 已严格合规的文件不会重复写入。
- 支持中文、空格、括号和大小写扩展名。
- 单个文件失败后继续处理其他文件。
- 运行记录包含实际字段值、XMP 结构、验证时间和 ExifTool 版本。

## 普通用户使用

1. 从 GitHub Releases 下载
   `ai-media-synthetic-marker-v1.0.0-windows-x64.zip`。
2. 完整解压 ZIP，不要单独移动主 EXE。
3. 人工确认媒体确实适用该标签，再放入“待标记”文件夹。
4. 建议先点击“只读验证”。
5. 如需补充标签，再点击“开始标记”。
6. 在“运行记录”中查看 CSV 证据。

便携版本已经包含 Python 运行环境和 ExifTool，普通用户无需另外安装。

## 结果说明

- **验证通过**：字段中存在精确目标值，并且原始 XMP 中确认了
  `dc:subject/rdf:Bag/rdf:li`。
- **未标记**：`XMP-dc:Subject` 中没有精确目标值。
- **验证失败**：读取异常，或者字段值与原始 XMP 结构不一致。

MP4 即使已经正确写入 XMP，Windows“属性 → 详细信息 → 标记”仍可能
显示为空。请以工具的严格回读结果为准。

## 本地开发

要求：

- Windows x64
- Python 3.14.6，包含 Tkinter
- ExifTool 13.59

新电脑首次配置、精确依赖版本、环境验收和 GitHub 发布步骤见
[BUILDING.md](BUILDING.md)。

首次准备 ExifTool：

```powershell
py -3.14 scripts\fetch_exiftool.py
```

双击源码版：

```text
开发运行.cmd
```

也可以直接运行：

```powershell
py -3.14 src\ai_media_marker.py
```

源码版使用：

```text
dev/
├─ 待标记/
└─ 运行记录/
```

这些目录中的媒体和 CSV 不会被 Git 跟踪。

## 运行测试

测试只使用标准库和可控的 Fake ExifTool，不需要公开真实媒体：

```powershell
py -3.14 -m unittest discover -s tests -v
```

## 构建便携 EXE 和 ZIP

开发与构建环境固定使用 Python 3.14.6 和 PyInstaller 6.21.0。构建脚本不会
擅自安装缺失的软件；若环境不完整，会停止并显示所需命令。

准备构建依赖：

```powershell
py -3.14 -m pip install --require-hashes --only-binary=:all: -r requirements-build.lock
```

生成发布包：

```powershell
py -3.14 scripts\build_release.py
```

输出：

```text
dist/
├─ ai-media-synthetic-marker-v1.0.0-windows-x64.zip
└─ SHA256SUMS.txt
```

发布脚本采用白名单组合便携目录，并拒绝将媒体、CSV、`*_original`
或源码缓存打入 ZIP；同时检查发布文本中是否意外写入本机绝对路径。

## 项目结构

```text
src/ai_media_marker.py          唯一业务源码
BUILDING.md                     新电脑配置、构建与发布清单
scripts/fetch_exiftool.py       下载并校验固定版本 ExifTool
scripts/build_release.py        测试、构建、组合和校验发布包
packaging/marker_app.spec       PyInstaller 配置
packaging/exiftool.lock.json    ExifTool 版本、URL 和 SHA-256
packaging/licenses/             Tcl 与 Tk 的完整许可文本
release_template/               发布说明和空目录模板
tests/                          标准库单元测试
runtime/exiftool/               本地运行组件，内容不提交 Git
dev/                            本地开发媒体目录，内容不提交 Git
```

## 隐私与风险说明

- 正式程序只处理本机文件，不上传媒体、不发送遥测数据，也不连接亚马逊。
- 程序不进行 AI 内容识别；使用者必须先人工判断哪些媒体适用该标签。
- 构建脚本可以联网下载固定版本的 ExifTool；这与正式程序处理媒体时
  的行为不同。
- “开始标记”会直接修改“待标记”中的原文件，并且不生成
  `_original` 备份。重要文件请先自行备份。
- “只读验证”不会修改媒体，但会在“运行记录”中创建 CSV。
- CSV 会记录相对文件名、Subject 实际值、验证时间和软件版本；文件名
  或元数据敏感时，请勿随意分享运行记录。
- 写入元数据会改变文件容器和整个文件的校验值。程序不主动重新编码
  图片像素或视频媒体流，但这不等于整个文件完全不变。
- `-P` 只要求 ExifTool 尽量保留修改时间，不能保证所有文件系统时间
  和属性完全不变。
- 平台上传、转码或发布过程中可能移除元数据。本工具不验证上传后的
  平台文件。
- “验证通过”只表示当前文件满足本工具检查的 XMP 字段与结构，不构成
  法律意见或平台最终审核保证。
- 本项目与 Amazon 无隶属、合作或官方认可关系。
- 当前 Windows EXE 未进行代码签名，SmartScreen 可能要求二次确认。

下载发布包后，可在 PowerShell 中核对发布页提供的 SHA-256：

```powershell
Get-FileHash .\ai-media-synthetic-marker-v1.0.0-windows-x64.zip -Algorithm SHA256
```

## 许可证

本仓库原创源码采用 [MIT License](LICENSE)。

便携包还包含 ExifTool、Python、Tcl/Tk、PyInstaller bootloader 及其
组成部分，它们保留各自许可证，不受本项目 MIT License 覆盖。详情见
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 和发布包中的
`licenses/`。

## 参与贡献

开发约束、测试命令和隐私注意事项见
[CONTRIBUTING.md](CONTRIBUTING.md)。

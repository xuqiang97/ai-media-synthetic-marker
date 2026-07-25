# 参与贡献

感谢参与改进 AI 人物媒体 XMP 标记工具。

## 开始之前

- 目前仅支持 Windows x64。
- 开发与正式构建统一使用 Python 3.14.6，并包含 Tkinter。
- 业务源码只保留在 `src/ai_media_marker.py`。
- 不要提交真实商品媒体、运行记录、`*_original` 或本地 ExifTool 文件。
- ExifTool 的版本、下载地址和校验值统一由
  `packaging/exiftool.lock.json` 管理。
- 构建脚本不会自动安装缺失的软件。

## 本地检查

```powershell
py -3.14 scripts\fetch_exiftool.py
py -3.14 -m pip install --require-hashes --only-binary=:all: -r requirements-build.lock
py -3.14 -m unittest discover -s tests -v
py -3.14 scripts\build_release.py
```

提交前请确认测试通过，并检查 `dist/` 中的发布包没有媒体、CSV、
Python 源码、缓存或 ExifTool `_original` 备份。

## 提交问题

请说明：

- Windows 版本；
- 工具版本和 ExifTool 版本；
- 使用的是“开始标记”还是“只读验证”；
- 界面或 CSV 中脱敏后的完整错误原因。

请先删除文件名、路径和元数据中的敏感信息，再分享日志。不要上传未获
授权的商品媒体。

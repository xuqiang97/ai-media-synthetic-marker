@chcp 65001 >nul
@echo off
setlocal

set "PROJECT_DIR=%~dp0"
set "APP_SCRIPT=%PROJECT_DIR%src\ai_media_marker.py"
set "AI_MEDIA_MARKER_WORK_DIR=%PROJECT_DIR%dev"
set "AI_MEDIA_MARKER_EXIFTOOL=%PROJECT_DIR%runtime\exiftool\exiftool.exe"

if not exist "%APP_SCRIPT%" (
    echo.
    echo 启动失败：找不到程序源码。
    echo 预期位置：%APP_SCRIPT%
    echo.
    pause
    exit /b 1
)

if not exist "%AI_MEDIA_MARKER_EXIFTOOL%" (
    echo.
    echo 启动失败：尚未准备 ExifTool。
    echo 预期位置：%AI_MEDIA_MARKER_EXIFTOOL%
    echo.
    echo 请先按照 README.md 的“本地开发”章节运行：
    echo py -3.14 scripts\fetch_exiftool.py
    echo.
    echo 本启动器不会自动下载或安装任何依赖。
    echo.
    pause
    exit /b 1
)

set "PYTHON314=%LocalAppData%\Programs\Python\Python314\pythonw.exe"
set "PYTHON314_CONSOLE=%LocalAppData%\Programs\Python\Python314\python.exe"
if not exist "%PYTHON314%" goto find_launcher
if not exist "%PYTHON314_CONSOLE%" goto find_launcher
"%PYTHON314_CONSOLE%" -c "import sys, tkinter; raise SystemExit(0 if sys.version_info[:3] == (3, 14, 6) else 1)" >nul 2>&1
if not errorlevel 1 goto launch_python314

:find_launcher
set "PY_LAUNCHER=%LocalAppData%\Programs\Python\Launcher\py.exe"
set "PYW_PATH="
if exist "%PY_LAUNCHER%" goto validate_launcher
set "PY_LAUNCHER="
for /f "delims=" %%I in ('where py.exe 2^>nul') do if not defined PY_LAUNCHER set "PY_LAUNCHER=%%I"
if not defined PY_LAUNCHER goto find_path_python

:validate_launcher
for %%I in ("%PY_LAUNCHER%") do set "PYW_PATH=%%~dpIpyw.exe"
if not exist "%PYW_PATH%" goto find_path_python
"%PY_LAUNCHER%" -3.14 -c "import sys, tkinter; raise SystemExit(0 if sys.version_info[:3] == (3, 14, 6) else 1)" >nul 2>&1
if not errorlevel 1 goto launch_pyw

:find_path_python
set "PYTHON_PATH="
set "PYTHONW_PATH="
for /f "delims=" %%I in ('where python.exe 2^>nul') do if not defined PYTHON_PATH set "PYTHON_PATH=%%I"
if not defined PYTHON_PATH goto launch_error
for %%I in ("%PYTHON_PATH%") do set "PYTHONW_PATH=%%~dpIpythonw.exe"
if not exist "%PYTHONW_PATH%" goto launch_error
"%PYTHON_PATH%" -c "import sys, tkinter; raise SystemExit(0 if sys.version_info[:3] == (3, 14, 6) else 1)" >nul 2>&1
if not errorlevel 1 goto launch_pythonw

:launch_error
echo.
echo 启动失败：未找到可用的 Python 3.14.6 和 Tkinter。
echo.
echo 查找顺序：
echo 1. 本机 Python 3.14.6 的 pythonw.exe
echo 2. Python Launcher 的 pyw.exe -3.14（必须解析到 3.14.6）
echo 3. PATH 中同目录的 python.exe / pythonw.exe
echo.
echo 请先安装包含 Tkinter 的 Python 3.14.6。
echo 本启动器不会自动安装 Python 或其他依赖。
echo.
pause
exit /b 1

:launch_python314
start "" /D "%PROJECT_DIR%" "%PYTHON314%" "%APP_SCRIPT%"
exit /b 0

:launch_pyw
start "" /D "%PROJECT_DIR%" "%PYW_PATH%" -3.14 "%APP_SCRIPT%"
exit /b 0

:launch_pythonw
start "" /D "%PROJECT_DIR%" "%PYTHONW_PATH%" "%APP_SCRIPT%"
exit /b 0

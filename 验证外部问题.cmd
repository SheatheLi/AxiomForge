@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel% EQU 0 (
  py -3 "验证外部问题.py"
) else (
  python "验证外部问题.py"
)
echo.
echo 检查执行完成。请阅读上方执行结果及 results 目录中的证据。
pause

@echo off
REM Double-click to build the .exe.
REM Opens the dist folder when the build finishes.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1" -Open
pause

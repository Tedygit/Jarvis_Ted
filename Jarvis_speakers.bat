@echo off
title Jarvis_Ted speakers
echo Unplug the earphone BEFORE continuing, then press a key. Jarvis speaks through the laptop speakers and cannot be interrupted by voice in this mode.
pause
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Jarvis_Ted\tools\start_jarvis.ps1" -Mode speakers
echo.
echo Jarvis stopped. Close the two server windows when you are finished.
pause

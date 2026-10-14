@echo off
title Jarvis_Ted earphone
echo Plug the earphone in fully BEFORE continuing, then press a key. Voice interruption is on.
pause
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Jarvis_Ted\tools\start_jarvis.ps1" -Mode earphone
echo.
echo Jarvis stopped. Close the two server windows when you are finished.
pause

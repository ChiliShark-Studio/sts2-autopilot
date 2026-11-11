@echo off
rem Launcher: run from cmd or double-click.
rem   sts2 fight-live
rem   sts2 fight --encounter lagavulin --runs 30
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0sts2.ps1" %*

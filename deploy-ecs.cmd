@echo off
powershell -ExecutionPolicy Bypass -File "%~dp0deploy-ecs.ps1" %*

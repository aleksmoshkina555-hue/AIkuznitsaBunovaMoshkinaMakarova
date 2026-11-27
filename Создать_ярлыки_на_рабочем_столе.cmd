@echo off
rem Ярлыки на рабочем столе: "ИИ-консультант" (вход) и "Спросить ИИ-консультанта" (без входа)
cd /d "%~dp0"
python launcher.py --create-shortcuts
pause

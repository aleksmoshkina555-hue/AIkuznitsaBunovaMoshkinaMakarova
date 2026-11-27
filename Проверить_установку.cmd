@echo off
rem Проверка компьютера: Python, пакеты, Ollama, модели, место на диске, целостность файлов после переноса
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
chcp 65001 >nul
python admin.py --check
pause

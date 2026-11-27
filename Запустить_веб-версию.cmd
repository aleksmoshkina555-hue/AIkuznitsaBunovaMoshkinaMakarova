@echo off
rem Веб-версия ИИ-консультанта: сервер на этом компьютере, программа открывается в браузере
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw web\start_web.py
    exit /b
)
where python >nul 2>nul
if %errorlevel%==0 (
    start "" python web\start_web.py
    exit /b
)
echo Не найден Python. Установите Python 3.10 или новее: https://www.python.org/downloads/
echo При установке отметьте "Add python.exe to PATH", затем запустите этот файл снова.
pause

@echo off
rem Окно сотрудника без входа: только "Задать вопрос". Без окна консоли.
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw launcher.py --employee
    exit /b
)
where python >nul 2>nul
if %errorlevel%==0 (
    start "" python launcher.py --employee
    exit /b
)
echo Не найден Python. Установите Python 3.10 или новее: https://www.python.org/downloads/
echo При установке отметьте "Add python.exe to PATH", затем запустите этот файл снова.
pause

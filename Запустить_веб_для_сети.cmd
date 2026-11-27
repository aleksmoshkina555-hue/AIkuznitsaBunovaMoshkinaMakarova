@echo off
rem Веб-версия с доступом из сети: адрес для сотрудников - http://%COMPUTERNAME%:8000
rem Брандмауэр Windows должен разрешать входящие подключения на порт 8000 (настраивает ИТ-служба)
cd /d "%~dp0"
set WEB_HOST=0.0.0.0
set WEB_ALLOWED_HOSTS=%COMPUTERNAME%,localhost,127.0.0.1
echo Сотрудники открывают в браузере: http://%COMPUTERNAME%:8000
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw web\start_web.py
    exit /b
)
start "" python web\start_web.py

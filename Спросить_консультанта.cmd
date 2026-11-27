@echo off
rem ���� ���㤭��� ��� �室�: ⮫쪮 "������ �����". ��� ���� ���᮫�.
cd /d "%~dp0"
@echo off
rem  -⠭: ஢ઠ ⮢  Ollama, ⥬  室.   ᮫.
cd /d "%~dp0"

set LLM_BASE_URL=https://proxyapi.ru
set LLM_API_KEY=sk-ваш_ключ_с_proxyapi
set LLM_MODEL=gpt-4o-mini
set EMBED_MODEL=text-embedding-3-small

where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw launcher.py %*
    exit /b
)
where python >nul 2>nul
if %errorlevel%==0 (
    start "" python launcher.py %*
    exit /b
)
echo   Python. ⠭ Python 3.10  : https://www.python.org/downloads/
echo  ⠭ ⬥ "Add python.exe to PATH", ⥬   䠩 ᭮.
pause

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
echo �� ������ Python. ��⠭���� Python 3.10 ��� �����: https://www.python.org/downloads/
echo �� ��⠭���� �⬥��� "Add python.exe to PATH", ��⥬ ������� ��� 䠩� ᭮��.
pause

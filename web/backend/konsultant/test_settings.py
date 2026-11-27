"""Настройки для тестов: данные, база знаний и справочник — копии во временной папке, «сегодня» — 23.11.2026,
поиск по смыслу и SMTP выключены (тесты не зависят от Ollama и почты). Запуск:
python manage.py test core --settings=konsultant.test_settings
"""
import os
import shutil
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_TMP = Path(tempfile.mkdtemp(prefix="web_tests_"))
# Неизменный набор демо-документов (tests/fixtures), а не рабочая папка, куда администратор добавляет свои
shutil.copytree(_ROOT / "tests" / "fixtures" / "база_знаний", _TMP / "база_знаний")
shutil.copy(_ROOT / "tests" / "fixtures" / "employees.json", _TMP / "employees.json")
shutil.copy(_ROOT / "tests" / "fixtures" / "process_map.json", _TMP / "process_map.json")  # справочник к этим документам
os.environ.update({"DATA_DIR": str(_TMP / "data"), "KB_TODAY": "23.11.2026", "EMBED_MODEL": "",
                   "KNOWLEDGE_DIR": str(_TMP / "база_знаний"), "EMPLOYEES_FILE": str(_TMP / "employees.json"),
                   "WEB_DB_FILE": str(_TMP / "test.sqlite3"),
                   "PROCESS_MAP_FILE": str(_TMP / "process_map.json")})  # копия: тесты меняют темы
os.environ.pop("SMTP_HOST", None)

from .settings import *  # noqa: E402,F401,F403

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher", "core.hashers.LegacyPBKDF2Hasher"]  # быстрее
TEST_TMP = _TMP

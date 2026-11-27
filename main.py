import io
import os
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "web" / "backend"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "konsultant.settings")
os.environ.setdefault("WEB_ALLOWED_HOSTS", "*")

import django
django.setup()

from django.contrib.auth.models import User
from django.core.management import call_command

# Применяем миграции и переносим начальные данные из data/, если база пустая
call_command("migrate", verbosity=0, stdout=io.StringIO())
from config import DATA_DIR
if not User.objects.exists() and ((DATA_DIR / "users.json").exists() or (DATA_DIR / "requests.json").exists()):
    call_command("import_legacy", verbosity=0, stdout=io.StringIO())

# Фоновый прогрев базы знаний (векторы посчитаются в фоне, не задерживая старт сервера)
from knowledge_base import get_knowledge_base
threading.Thread(target=get_knowledge_base, daemon=True, name="kb-warmup").start()

from waitress import serve
from konsultant.wsgi import application

port = int(os.environ.get("PORT", "8000"))
print(f"Starting server on 0.0.0.0:{port}")
serve(application, host="0.0.0.0", port=port, threads=8)

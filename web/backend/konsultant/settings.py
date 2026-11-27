"""Настройки веб-версии ИИ-консультанта.

Логика ответа (поиск, агент, проверки, карта ответственности) — прежние модули из корня проекта:
корень добавляется в sys.path. Данные Django (учётные записи, обращения, журналы, справочник) — в SQLite
в той же папке data/, что и журналы агента: всё, что переносится, лежит в одном месте.
"""
import os
import secrets
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent          # web/backend
PROJECT_ROOT = BACKEND_DIR.parent.parent                       # корень проекта с модулями агента
FRONTEND_DIST = BACKEND_DIR.parent / "frontend" / "dist"        # собранный React (npm run build)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("KB_BACKGROUND_RELOAD", "1")  # веб: векторы новых документов — в фоне, ответы не ждут
from config import DATA_DIR  # noqa: E402 — после sys.path: та же папка данных, что у агента

DATA_DIR.mkdir(parents=True, exist_ok=True)


def _secret_key() -> str:
    """Ключ подписи сессий: из переменной окружения или создаётся один раз в data/secret_key.txt."""
    if os.getenv("DJANGO_SECRET_KEY"):
        return os.environ["DJANGO_SECRET_KEY"]
    path = DATA_DIR / "secret_key.txt"
    if not path.exists():
        path.write_text(secrets.token_urlsafe(50), encoding="utf-8")
    return path.read_text(encoding="utf-8").strip()


SECRET_KEY = _secret_key()
DEBUG = os.getenv("DJANGO_DEBUG", "0") == "1"
# По умолчанию — только этот компьютер. Доступ из сети: WEB_ALLOWED_HOSTS="*" или имена/адреса через запятую
ALLOWED_HOSTS = [h.strip() for h in os.getenv("WEB_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.getenv("WEB_TRUSTED_ORIGINS", "").split(",") if o.strip()]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "rest_framework",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "konsultant.urls"
WSGI_APPLICATION = "konsultant.wsgi.application"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [], "APP_DIRS": False,
              "OPTIONS": {"context_processors": []}}]

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3",
                         "NAME": Path(os.getenv("WEB_DB_FILE", str(DATA_DIR / "konsultant.sqlite3")))}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Пароли: стандартный PBKDF2 Django; «legacy» — хеши из прежней версии (users.json), при входе
# пароль автоматически перехешируется в формат Django — пользователи входят с прежними паролями.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "core.hashers.LegacyPBKDF2Hasher",
]
AUTH_PASSWORD_VALIDATORS = [{"NAME": "core.validators.PolicyValidator"}]

SESSION_COOKIE_AGE = 60 * 60 * 10        # рабочий день
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = False             # фронтенд читает csrftoken и отправляет его в заголовке
X_FRAME_OPTIONS = "DENY"

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}  # счётчик неудачных входов

LANGUAGE_CODE = "ru-ru"
def _computer_time_zone() -> str:
    """Часовой пояс этого компьютера (Екатеринбург → Etc/GMT-5): время в программе совпадает с часами Windows."""
    offset = time.localtime().tm_gmtoff or 0
    if offset % 3600:
        return "Europe/Moscow"  # пояса со смещением в полчаса — задайте WEB_TIME_ZONE явно
    hours = offset // 3600
    return "UTC" if hours == 0 else f"Etc/GMT{'-' if hours > 0 else '+'}{abs(hours)}"  # знак в Etc/ — обратный


# Время в журналах и обращениях: по умолчанию — часы этого компьютера; явно: WEB_TIME_ZONE=Europe/Moscow
TIME_ZONE = os.getenv("WEB_TIME_ZONE") or _computer_time_zone()
USE_I18N = False
USE_TZ = True

DATA_UPLOAD_MAX_MEMORY_SIZE = 50 * 1024 * 1024  # документы базы знаний

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "UNAUTHENTICATED_USER": None,
}

LOGGING = {"version": 1, "disable_existing_loggers": False,
           "handlers": {"console": {"class": "logging.StreamHandler"}},
           "root": {"handlers": ["console"], "level": "WARNING"}}

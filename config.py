"""Настройки приложения. Все значения можно переопределить переменными окружения."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# Локальная модель (OpenAI-совместимый API Ollama)
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:11434/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "ollama")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen2.5:1.5b")
# Модель пишет только суть ответа (1–3 предложения), письмо собирает программа — 400 токенов с запасом
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "400"))
LLM_TIMEOUT_SEC = float(os.getenv("LLM_TIMEOUT_SEC", "120"))

# База знаний: папка с документами (.docx, .pdf, .eml, .msg, .txt, .md) и карточками *.meta.json
KNOWLEDGE_DIR = Path(os.getenv("KNOWLEDGE_DIR", str(BASE_DIR / "база_знаний")))
SYNONYMS_FILE = Path(os.getenv("SYNONYMS_FILE", str(BASE_DIR / "synonyms.json")))
# «Сегодня» для расчёта статусов (вступление в силу, пересмотр). Формат ДД.ММ.ГГГГ; пусто — текущая дата.
KB_TODAY = os.getenv("KB_TODAY", "")
# Веб-сервер: новые документы сразу ищутся по словам, векторы для поиска по смыслу считаются в фоне
# (большой документ — минуты). Окно и тесты — без фона: база пересобирается сразу и полностью.
KB_BACKGROUND_RELOAD = os.getenv("KB_BACKGROUND_RELOAD", "0") == "1"
# За сколько дней до даты пересмотра напоминать владельцу документа
REVIEW_REMIND_DAYS = int(os.getenv("REVIEW_REMIND_DAYS", "14"))

# Поиск
SEARCH_TOP_K = int(os.getenv("SEARCH_TOP_K", "3"))
# Минимальная доля значимых слов запроса, которые должны найтись в документе
SEARCH_MIN_COVERAGE = float(os.getenv("SEARCH_MIN_COVERAGE", "0.4"))
# Фрагментов одного документа в ответе
SEARCH_CHUNKS_PER_DOC = int(os.getenv("SEARCH_CHUNKS_PER_DOC", "2"))

# Поиск по смыслу (эмбеддинги через Ollama). «off» (или пусто) — только поиск по словам.
EMBED_MODEL = os.getenv("EMBED_MODEL", "bge-m3")
if EMBED_MODEL.strip().lower() in ("off", "none", "0"):
    EMBED_MODEL = ""
# Вес поиска по смыслу в гибридной оценке (0 — только слова, 1 — только смысл)
EMBED_WEIGHT = float(os.getenv("EMBED_WEIGHT", "0.5"))
# Документ считается найденным по смыслу, если косинусная близость не ниже порога
# Подобрано на демонстрационной базе: вопросы без ответа в базе — не выше 0,48, с ответом — от 0,54.
# Для реальной базы порог нужно подобрать заново (eval/run_eval.py).
EMBED_MIN_SIMILARITY = float(os.getenv("EMBED_MIN_SIMILARITY", "0.55"))

# Проверка «ответ по вопросу»: эвристика всегда, дополнительный короткий запрос к модели — если включено
RELEVANCE_LLM_CHECK = os.getenv("RELEVANCE_LLM_CHECK", "1") == "1"

# Карта ответственности (процесс → этап → ответственный → основание)
EMPLOYEES_FILE = Path(os.getenv("EMPLOYEES_FILE", str(BASE_DIR / "employees.json")))  # справочник «Сотрудники»
PROCESS_MAP_FILE = Path(os.getenv("PROCESS_MAP_FILE", str(BASE_DIR / "process_map.json")))

# Входящие обращения: папка с письмами .eml (общий почтовый ящик в демо-режиме) и срок ответа
INBOX_DIR = Path(os.getenv("INBOX_DIR", str(BASE_DIR / "входящие")))
REQUEST_SLA_HOURS = int(os.getenv("REQUEST_SLA_HOURS", "24"))

# Эскалация, если ответ не найден
ESCALATION_CONTACT = os.getenv("ESCALATION_CONTACT", "Дежурный методолог процессов (process@company.ru)")

# Базовая линия для оценки экономии: нижняя граница времени ручного поиска
# из анализа обращений 2025–2026 (30 мин – 2 ч). 0 — не показывать оценку.
BASELINE_SEARCH_MINUTES = int(os.getenv("BASELINE_SEARCH_MINUTES", "30"))

# Отправка почты. Если SMTP_HOST не задан — демо-режим: письмо сохраняется в папку outbox.
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "navigator@company.ru")

# Журналы
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))
OUTBOX_DIR = DATA_DIR / "outbox"
LOG_FILE = DATA_DIR / "agent.log"
ESCALATIONS_FILE = DATA_DIR / "escalations.jsonl"
SENT_FILE = DATA_DIR / "sent.jsonl"
NOTIFICATIONS_FILE = DATA_DIR / "notifications.jsonl"
FEEDBACK_FILE = DATA_DIR / "feedback.jsonl"
REQUESTS_FILE = DATA_DIR / "requests.json"
HISTORY_FILE = DATA_DIR / "history.jsonl"
UI_SETTINGS_FILE = DATA_DIR / "ui_settings.json"
EMBED_CACHE_FILE = DATA_DIR / "embeddings_cache.json"
USERS_FILE = DATA_DIR / "users.json"        # учётные записи (пароли — только хеши PBKDF2)
AUDIT_FILE = DATA_DIR / "audit.jsonl"       # журнал входов и действий администратора
BACKUPS_DIR = DATA_DIR / "backups"          # копии программы перед обновлением (для отката)
DELETED_DOCS_DIR = DATA_DIR / "deleted_documents"  # удалённые из базы документы (можно вернуть)
SELF_SERVICE_FILE = DATA_DIR / "self_service.jsonl"  # вопросы сотрудников в режиме «спросить самому» и отметки «помогло»

# Режим «сотрудник спрашивает сам»: ответ модели, прошедший проверку фактов и «ответ по вопросу»,
# выдаётся сотруднику сразу (1) или только после специалиста (0). Ответы по правилу и графу версий выдаются всегда.
SELF_SERVICE_TRUST_MODEL = os.getenv("SELF_SERVICE_TRUST_MODEL", "1") == "1"

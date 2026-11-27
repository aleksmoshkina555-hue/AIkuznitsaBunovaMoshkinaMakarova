"""Загрузка документов базы знаний из папки.

Форматы: .docx (python-docx), .pdf (pypdf), .eml (стандартная библиотека), .msg (extract-msg, если установлен),
.txt, .md. Рядом с файлом может лежать карточка «<имя файла>.meta.json» с метаданными:

    {"id": "ORD-2026-118", "type": "Приказ", "number": "118-П", "title": "...", "date": "10.02.2026",
     "owner": "...", "status": "Устарел", "supersedes": ["DOC-2024-09"], "effective_from": "01.03.2026",
     "review_date": "01.12.2026"}

Все поля необязательны: чего нет в карточке, извлекается из самого документа (заголовок, номер, дата,
отправитель письма). Статус по умолчанию вычисляется автоматически (см. actuality.py).
"""
import email
import hashlib
import json
import re
from datetime import date, datetime
from email import policy
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

from journal import get_logger

logger = get_logger()

SUPPORTED = {".docx", ".pdf", ".eml", ".msg", ".txt", ".md"}
META_SUFFIX = ".meta.json"

MONTHS = ("январ", "феврал", "март", "апрел", "ма", "июн", "июл", "август", "сентябр", "октябр", "ноябр", "декабр")
_NUM_DATE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")
_TEXT_DATE = re.compile(
    r"\b(\d{1,2})\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+(\d{4})",
    re.IGNORECASE)
_DOC_NUMBER = re.compile(r"№\s*([\w\-/]+)")
_POINT = re.compile(r"^\s*(\d{1,2}(?:\.\d{1,2})*)[.)]\s+")

# Тип документа по первым строкам текста или имени файла
_TYPE_WORDS = [
    ("приказ", "Приказ"), ("распоряжени", "Распоряжение"), ("регламент", "Регламент"),
    ("положени", "Положение"), ("инструкци", "Инструкция"), ("порядок", "Порядок"),
]
_TYPE_CODES = {"Приказ": "ORD", "Распоряжение": "ORD", "Регламент": "DOC", "Положение": "DOC", "Инструкция": "DOC",
               "Порядок": "DOC", "Корпоративная почта": "MAIL", "Рабочий чат": "CHAT", "Документ": "DOC"}


# ---------- даты ----------

def parse_date(value: str) -> date | None:
    """«10.02.2026» или «10 февраля 2026» → date."""
    if not value:
        return None
    m = _NUM_DATE.search(value)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    m = _TEXT_DATE.search(value)
    if m:
        month = next(i for i, stem in enumerate(MONTHS, 1) if m.group(2).lower().startswith(stem))
        return date(int(m.group(3)), month, int(m.group(1)))
    return None


def format_date(value: date | None) -> str:
    return value.strftime("%d.%m.%Y") if value else ""


def first_date(text: str) -> date | None:
    candidates = [m.start() for m in _NUM_DATE.finditer(text)] + [m.start() for m in _TEXT_DATE.finditer(text)]
    return parse_date(text[min(candidates):]) if candidates else None


# ---------- чтение форматов ----------

def _read_docx(path: Path) -> str:
    import docx  # python-docx
    document = docx.Document(str(path))
    lines = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            lines.append(" | ".join(cell.text for cell in row.cells))
    return "\n".join(lines)


def _read_pdf(path: Path) -> str:
    from pypdf import PdfReader
    return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)


def _html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>", "\n", html)
    return re.sub(r"<[^>]+>", " ", html)


def _read_eml(path: Path) -> tuple[str, dict]:
    with open(path, "rb") as file:
        message = email.message_from_binary_file(file, policy=policy.default)
    body_part = message.get_body(preferencelist=("plain", "html"))
    body = body_part.get_content() if body_part else ""
    if body_part is not None and body_part.get_content_type() == "text/html":
        body = _html_to_text(body)
    meta = {"type": "Корпоративная почта", "title": f"Письмо: {message.get('Subject', '').strip()}"}
    name, address = parseaddr(message.get("From", ""))
    if name or address:
        meta["owner"] = name or address
    if message.get("Date"):
        try:
            meta["date"] = format_date(parsedate_to_datetime(message["Date"]).date())
        except (TypeError, ValueError):
            pass
    return body, meta


def _read_msg(path: Path) -> tuple[str, dict]:
    import extract_msg  # необязательная зависимость: pip install extract-msg
    message = extract_msg.Message(str(path))
    try:
        meta = {"type": "Корпоративная почта", "title": f"Письмо: {message.subject or ''}".strip()}
        if message.sender:
            meta["owner"] = parseaddr(message.sender)[0] or message.sender
        if message.date:
            meta["date"] = format_date(message.date.date() if isinstance(message.date, datetime) else parse_date(str(message.date)))
        return message.body or "", meta
    finally:
        message.close()


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def read_file(path: Path) -> tuple[str, dict]:
    """(текст, метаданные, извлечённые из самого файла)."""
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return _read_docx(path), {}
    if suffix == ".pdf":
        return _read_pdf(path), {}
    if suffix == ".eml":
        return _read_eml(path)
    if suffix == ".msg":
        return _read_msg(path)
    return _read_text(path), {}


# ---------- метаданные ----------

def _clean(text: str) -> str:
    text = text.replace("\r", "").replace(" ", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    return "\n".join(lines).strip()


def _detect_type(text: str, path: Path) -> str:
    head = text[:300].lower()
    name = path.stem.lower()
    if "чат" in name or "chat" in name:
        return "Рабочий чат"
    for word, doc_type in _TYPE_WORDS:
        if word in name or re.search(rf"\b{word}", head):
            return doc_type
    return "Документ"


def _auto_title(lines: list[str]) -> str:
    """Заголовок: первая строка; если она из одного слова-типа («ПРИКАЗ №118-П») — склеиваем со строкой названия."""
    meaningful = [line for line in lines if line and not _NUM_DATE.fullmatch(line) and not re.fullmatch(r"от\s+.*", line)]
    if not meaningful:
        return ""
    title = meaningful[0]
    if title.isupper():  # «ПРИКАЗ №118-П» → «Приказ №118-П»
        title = " ".join(w.capitalize() if w.isalpha() else w for w in title.split())
    if len(title.split()) <= 3 and len(meaningful) > 1 and not _POINT.match(meaningful[1]):
        title = f"{title} «{meaningful[1]}»"
    return title


def _make_id(meta: dict, path: Path) -> str:
    code = _TYPE_CODES.get(meta.get("type", ""), "DOC")
    year = meta["date"][-4:] if meta.get("date") else "0000"
    tail = re.sub(r"\W", "", meta.get("number", "")) or hashlib.sha1(path.name.encode()).hexdigest()[:6].upper()
    return f"{code}-{year}-{tail}"


def split_points(text: str) -> list[dict]:
    """Разбивка на пункты «1.», «1.2.», «3)». Текст до первого пункта — «шапка». Без пунктов — абзацы."""
    lines = [line for line in text.split("\n")]
    chunks, current, label = [], [], None
    has_points = any(_POINT.match(line) for line in lines)

    def flush():
        body = " ".join(line for line in current if line).strip()
        if body:
            chunks.append({"point": label, "text": body})

    if not has_points:
        paragraphs = [p.strip().replace("\n", " ") for p in re.split(r"\n\s*\n", text) if p.strip()]
        return [{"point": None, "text": p} for p in paragraphs] or [{"point": None, "text": text.strip()}]

    for line in lines:
        m = _POINT.match(line)
        if m:
            flush()
            current, label = [line[m.end():]], m.group(1)
        else:
            current.append(line)
    flush()
    return chunks


def load_document(path: Path) -> dict | None:
    """Файл → документ (метаданные + текст + пункты). None — если формат не поддерживается или файл не читается."""
    try:
        text, file_meta = read_file(path)
    except ImportError as exc:
        logger.warning("kb: пропущен %s — не установлен пакет (%s)", path.name, exc)
        return None
    except Exception as exc:  # повреждённый файл не должен ломать всю базу
        logger.warning("kb: не удалось прочитать %s: %s: %s", path.name, type(exc).__name__, exc)
        return None

    text = _clean(text)
    meta_path = path.with_name(path.name + META_SUFFIX)
    card = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    meta = {**file_meta, **card}

    lines = [line for line in text.split("\n") if line]
    meta.setdefault("type", _detect_type(text, path))
    if "number" not in meta:
        m = _DOC_NUMBER.search("\n".join(lines[:3]))
        if m:
            meta["number"] = m.group(1)
    meta.setdefault("title", _auto_title(lines) or path.stem)
    if "date" not in meta:
        meta["date"] = format_date(first_date(text))
    meta.setdefault("owner", _find_owner(text) or "не указан")
    meta.setdefault("id", _make_id(meta, path))

    return {
        "id": meta["id"],
        "source_type": meta["type"],
        "number": meta.get("number", ""),
        "title": meta["title"],
        "date": meta["date"],
        "owner": meta["owner"],
        "content": _body_without_header(text, lines),
        "full_text": text,
        "chunks": split_points(text),
        "declared_status": meta.get("status"),
        "supersedes_declared": list(meta.get("supersedes", [])),
        "effective_from": meta.get("effective_from"),
        "review_date": meta.get("review_date"),
        "departments": departments_list(meta.get("departments")),
        "placed": meta.get("placed", ""),  # дата размещения в базе (ставится при загрузке)
        "related": meta.get("related", ""),  # «Ответ службы качества»: к какому документу относится
        "path": str(path),
    }


QUALITY_ANSWER_TYPE = "Ответ службы качества"  # вид документа: ответ специалиста, пополнивший базу знаний


def quality_answer_text(full_text: str) -> str:
    """Текст ответа из документа «Ответ службы качества» (п. 2 «Ответ: …»)."""
    m = re.search(r"(?m)^2\.\s*Ответ:\s*(.+?)\s*(?=^\d+\.\s|\Z)", full_text, re.S)
    return m.group(1).strip() if m else ""


def quality_question_text(full_text: str) -> str:
    """На какой вопрос отвечала служба качества (п. 1 «Вопрос: …»)."""
    m = re.search(r"(?m)^1\.\s*Вопрос:\s*(.+?)\s*(?=^\d+\.\s|\Z)", full_text, re.S)
    return m.group(1).strip() if m else ""


def departments_list(value) -> list[str]:
    """Для каких подразделений документ: список из карточки (или строка через запятую). Пусто — для всех."""
    if isinstance(value, str):
        value = value.split(",")
    names = [str(v).strip() for v in (value or []) if str(v).strip()]
    return sorted(set(names), key=names.index)


def _find_owner(text: str) -> str:
    m = re.search(r"(?im)^\s*(?:ответственный|владелец документа|контакт)\s*:\s*(.+)$", text)
    return m.group(1).strip() if m else ""


def _body_without_header(text: str, lines: list[str]) -> str:
    """Текст для показа и запасного ответа: без строк-шапки (заголовок, «от …», служебные строки)."""
    body = [line for line in lines
            if not re.fullmatch(r"(?i)(приказ|регламент|распоряжение|положение|инструкция)\b.{0,40}", line)
            and not re.fullmatch(r"(?i)(от|утвержд\w*)\s+.{0,30}", line)
            and not re.match(r"(?i)(ответственный|владелец документа|контакт)\s*:", line)]
    return " ".join(body) if body else text


def scan_folder(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return sorted(p for p in folder.rglob("*")
                  if p.is_file() and p.suffix.lower() in SUPPORTED and not p.name.endswith(META_SUFFIX)
                  and not p.name.startswith("~$"))


def folder_signature(folder: Path) -> tuple:
    """Отпечаток папки: меняется, если файл или карточка добавлены, удалены или изменены."""
    if not folder.exists():
        return ()
    return tuple(sorted((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in folder.rglob("*") if p.is_file()))

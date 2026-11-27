"""Логика интерфейса без Tkinter (её можно тестировать): разбор письма, уровень уверенности,
оценки ответов, история запросов, настройки окна, открытие файла-источника."""
import email
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from email import policy
from pathlib import Path

from config import FEEDBACK_FILE, HISTORY_FILE, UI_SETTINGS_FILE
from journal import append_jsonl

# ---------- письмо: вставка из буфера и файл .eml ----------

_HEADER = re.compile(r"^\s*(от|from|тема|subject|кому|to|копия|cc|отправлено|sent|дата|date)\s*:\s*(.*)$", re.IGNORECASE)


def parse_pasted_email(text: str) -> dict:
    """Текст письма, скопированный из почты («От: …», «Тема: …», пустая строка, текст) → поля формы.
    Без заголовков весь текст считается вопросом."""
    lines = text.replace("\r", "").split("\n")
    sender = subject = ""
    seen_header, i = False, 0
    while i < len(lines):
        line = lines[i]
        match = _HEADER.match(line)
        if match:
            seen_header = True
            key, value = match.group(1).lower(), match.group(2).strip()
            if key in ("от", "from"):
                sender = value
            elif key in ("тема", "subject"):
                subject = value
            i += 1
        elif not line.strip():
            i += 1
            if seen_header:
                break  # пустая строка после заголовков — дальше текст
        else:
            break
    return {"sender": sender, "subject": subject, "body": "\n".join(lines[i:]).strip()}


def load_eml(path: str | Path) -> dict:
    """Файл .eml → поля формы."""
    with open(path, "rb") as file:
        message = email.message_from_binary_file(file, policy=policy.default)
    part = message.get_body(preferencelist=("plain", "html"))
    body = part.get_content() if part else ""
    if part is not None and part.get_content_type() == "text/html":
        body = re.sub(r"<[^>]+>", " ", re.sub(r"(?i)<br\s*/?>|</p>", "\n", body))
    return {"sender": str(message.get("From", "")), "subject": str(message.get("Subject", "")), "body": body.strip()}


# ---------- уровень уверенности ответа ----------

CONFIDENCE = {
    # уровень: (цвет фона, цвет текста)
    "high": ("#dcfce7", "#166534"),        # правило или граф версий
    "checked": ("#dbeafe", "#1e40af"),     # ответ модели прошёл все проверки
    "document": ("#fef9c3", "#854d0e"),    # показан текст документа
    "escalation": ("#ffedd5", "#9a3412"),  # передано человеку
    "blocked": ("#fee2e2", "#991b1b"),     # фильтр безопасности
}


def confidence(result: dict) -> tuple[str, str]:
    """(уровень, подпись) для цветной плашки над ответом."""
    source = result.get("answer_source")
    if result.get("blocked"):
        return "blocked", "Запрос отклонён фильтром безопасности — ответьте вручную"
    if result.get("withheld"):
        return "escalation", "Система не уверена в найденном ответе — ответ не показан, вопрос передан специалисту"
    if source == "escalation" or not result.get("found", True):
        return "escalation", "Ответа в действующих документах нет — вопрос передан специалисту"
    if source == "process_map":
        note = " — укажите сумму договора" if result.get("needs_clarification") else ""
        return "high", f"Готовый ответ по теме вопроса — высокая надёжность{note}"
    if source == "quality":
        return "high", "Ответ службы качества из базы знаний — уже проверен специалистом"
    if source == "actuality":
        return "high", "Ответ по графу версий документов — высокая надёжность"
    if source == "model":
        return "checked", "Ответ модели прошёл проверку фактов и «ответ по вопросу» — сверьте с основанием"
    return "document", "Показан текст документа (ответ модели не прошёл проверку) — прочитайте внимательно"


# ---------- оценки, история, настройки ----------

def record_feedback(result: dict, question: dict, verdict: str, comment: str = "") -> dict:
    """Оценка ответа оператором: «верно», «неверно», «устарело» → feedback.jsonl."""
    record = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "verdict": verdict,
        "comment": comment,
        "sender": question.get("sender", ""),
        "subject": question.get("subject", ""),
        "body": question.get("body", ""),
        "answer_source": result.get("answer_source"),
        "answer": result.get("answer", ""),
        "basis": [d.get("id") for d in result.get("basis_docs", [])],
        "escalation_id": result.get("escalation_id"),
    }
    append_jsonl(FEEDBACK_FILE, record)
    return record


def append_history(question: dict, result: dict) -> dict:
    record = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "sender": question.get("sender", ""),
        "subject": question.get("subject", ""),
        "body": question.get("body", ""),
        "answer_source": result.get("answer_source"),
        "answer": result.get("answer", "")[:300],
    }
    append_jsonl(HISTORY_FILE, record)
    return record


def load_history(limit: int = 100) -> list[dict]:
    try:
        lines = HISTORY_FILE.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    records = []
    for line in lines[-limit:]:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def question_key(question: dict) -> str:
    """Ключ для мгновенного ответа на повторный вопрос: тема + текст без регистра и лишних пробелов."""
    return re.sub(r"\s+", " ", f"{question.get('subject', '')} {question.get('body', '')}".lower()).strip()


def load_ui_settings() -> dict:
    try:
        return json.loads(UI_SETTINGS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_ui_settings(settings: dict) -> None:
    UI_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    UI_SETTINGS_FILE.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def open_file(path: str) -> None:
    """Открыть файл в программе по умолчанию (Word, просмотр PDF, почта)."""
    if sys.platform.startswith("win"):
        os.startfile(path)  # noqa: S606 — путь из базы знаний, не от пользователя
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])

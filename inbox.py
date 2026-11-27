"""Входящие обращения сотрудников: очередь со статусами и сроками ответа.

Источник в демо-режиме — папка INBOX_DIR с письмами .eml (так выглядел бы общий почтовый ящик);
обращения можно также загрузить вручную или создать в окне. Хранятся в data/requests.json.
"""
import json
import uuid
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

from config import INBOX_DIR, REQUEST_SLA_HOURS, REQUESTS_FILE
from process_map import load_process_map
from text_processing import token_matches, tokenize
from ui_support import load_eml

NEW, WORK, READY, SENT, ESCALATED, REJECTED = (
    "Новое", "В работе", "Ответ готов", "Отправлено", "Передано специалисту", "Отклонено")
CLOSED = {SENT, REJECTED}
OTHER_PROCESS = "Прочие вопросы"
DATE_FMT = "%d.%m.%Y %H:%M"


def status_from_result(result: dict) -> str:
    if result.get("blocked"):
        return REJECTED
    if not result.get("found"):
        return ESCALATED
    return READY


def json_safe(value):
    """Результат агента → данные для JSON (даты и прочее — строками)."""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def classify_process(text: str, process_map: list | None = None) -> str:
    """Процесс из карты ответственности, к которому относится вопрос (для фильтра «Процессы»)."""
    tokens = set(tokenize(text))
    for entry in process_map if process_map is not None else load_process_map():
        groups = entry["match"]["all"]
        has = lambda stem: any(token_matches(t, stem) for t in tokens)  # noqa: E731
        if all(any(has(s) for s in group) for group in groups) and not any(has(s) for s in entry["match"].get("none", [])):
            return entry["process"]
    return OTHER_PROCESS


class RequestStore:
    def __init__(self, path: Path = REQUESTS_FILE):
        self.path = path
        self.items: list[dict] = []
        self.reload()

    def reload(self) -> None:
        """Перечитать файл: обращения могли добавить другие окна (сотрудник спросил сам, второй оператор)."""
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):  # файла нет или он записывается в эту секунду
            return
        # Те же словари обновляются на месте — ссылки на обращения, взятые раньше, остаются верными
        known = {r["id"]: r for r in self.items}
        items = []
        for fresh in loaded:
            current = known.get(fresh["id"])
            if current is not None:
                current.clear()
                current.update(fresh)
                fresh = current
            items.append(fresh)
        self.items = items

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.items, ensure_ascii=False, indent=1), encoding="utf-8")

    def get(self, request_id: str) -> dict | None:
        return next((r for r in self.items if r["id"] == request_id), None)

    def add(self, sender: str, subject: str, body: str, created: datetime | None = None, source: str = "вручную",
            source_file: str | None = None) -> dict:
        created = created or datetime.now()
        self.reload()  # не затереть обращения, добавленные другим окном
        request = {
            "id": f"REQ-{created:%Y%m%d}-{uuid.uuid4().hex[:4].upper()}",
            "created": created.strftime(DATE_FMT),
            "due": (created + timedelta(hours=REQUEST_SLA_HOURS)).strftime(DATE_FMT),
            "sender": sender, "subject": subject, "body": body,
            "status": NEW, "read": False, "source": source, "source_file": source_file,
            "process": classify_process(f"{subject} {body}"),
            "result": None, "processed_at": None, "sent_at": None,
        }
        self.items.append(request)
        self.save()
        return request

    def update(self, request_id: str, **fields) -> dict:
        self.reload()
        request = self.get(request_id)
        request.update(fields)
        self.save()
        return request

    def set_result(self, request_id: str, result: dict) -> dict:
        return self.update(request_id, status=status_from_result(result), result=json_safe(result),
                           processed_at=datetime.now().strftime(DATE_FMT))

    def import_eml(self, path: Path, source: str = "почта") -> dict | None:
        """Письмо .eml → обращение (каждый файл — один раз)."""
        path = Path(path)
        if any(r.get("source_file") == path.name for r in self.items):
            return None
        letter = load_eml(path)
        created = None
        try:
            import email
            from email import policy
            with open(path, "rb") as file:
                date_header = email.message_from_binary_file(file, policy=policy.default).get("Date")
            if date_header:
                created = parsedate_to_datetime(str(date_header)).replace(tzinfo=None)
        except (TypeError, ValueError):
            created = None
        return self.add(letter["sender"], letter["subject"], letter["body"], created=created, source=source,
                        source_file=path.name)

    def import_folder(self, folder: Path = INBOX_DIR) -> int:
        if not folder.exists():
            return 0
        added = [self.import_eml(p) for p in sorted(folder.glob("*.eml"))]
        return sum(1 for a in added if a)

    @staticmethod
    def is_overdue(request: dict, now: datetime | None = None) -> bool:
        if request["status"] in CLOSED:
            return False
        return (now or datetime.now()) > datetime.strptime(request["due"], DATE_FMT)

    @staticmethod
    def created_at(request: dict) -> datetime:
        return datetime.strptime(request["created"], DATE_FMT)

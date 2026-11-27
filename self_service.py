"""Режим «сотрудник спрашивает сам».

Сотрудник задаёт вопрос и сразу получает ответ, если ответ надёжный (правило карты ответственности,
граф версий документов, ответ модели, прошедший проверки). Ненадёжные и ненайденные ответы уходят
специалисту — обращением во «Входящие». Здесь же — журнал вопросов, отметки «Помогло / Не помогло»
и сводка: сколько вопросов закрыто без людей и сколько часов сэкономлено.

Журнал — data/self_service.jsonl: строка «question» на каждый вопрос и строки-события
«helped» (отметка сотрудника) и «handoff» (передано специалисту) с тем же id.
"""
import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from config import BASELINE_SEARCH_MINUTES, SELF_SERVICE_FILE, SELF_SERVICE_TRUST_MODEL
from inbox import CLOSED, ESCALATED, READY, SENT, RequestStore
from journal import append_jsonl

# Решение по ответу
ANSWER = "ответ сразу"            # надёжный ответ — сотрудник получает его сам
CHECK = "пункт документа"         # точного ответа нет, показан текст документа — сотрудник решает сам
CLARIFY = "нужно уточнение"       # не хватает суммы договора
REVIEW = "на проверку специалисту"  # ответ модели есть, но по настройке его сначала проверяет человек
ESCALATE = "передано специалисту"  # ответа в действующих документах нет
BLOCKED = "отклонено"             # фильтр безопасности

PERIODS = {"Неделя": 7, "Месяц": 30, "Всё время": None}


def decide(result: dict, trust_model: bool = SELF_SERVICE_TRUST_MODEL) -> str:
    """Можно ли отдать ответ сотруднику сразу, без специалиста."""
    if result.get("blocked"):
        return BLOCKED
    source = result.get("answer_source")
    if source == "escalation" or not result.get("found", True):
        return ESCALATE
    if result.get("needs_clarification"):
        return CLARIFY
    if source in ("process_map", "actuality", "quality"):
        return ANSWER
    if source == "model":
        return ANSWER if trust_model else REVIEW
    return CHECK


def needs_specialist(decision: str) -> bool:
    """Вопрос сразу становится обращением во «Входящих»."""
    return decision in (ESCALATE, REVIEW)


def log_question(employee: str, question: str, result: dict, decision: str, path: Path = SELF_SERVICE_FILE) -> dict:
    record = {
        "type": "question",
        "id": f"Q-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4].upper()}",
        "time": datetime.now().isoformat(timespec="seconds"),
        "employee": employee,
        "question": question,
        "decision": decision,
        "answer_source": result.get("answer_source"),
        "found": bool(result.get("found")) and not result.get("blocked"),
        "latency": float(result.get("latency") or 0),
        "answer": (result.get("answer") or "")[:300],
    }
    append_jsonl(path, record)
    return record


def mark_helped(record_id: str, helped: bool, comment: str = "", path: Path = SELF_SERVICE_FILE) -> None:
    append_jsonl(path, {"type": "helped", "id": record_id, "helped": helped, "comment": comment,
                        "time": datetime.now().isoformat(timespec="seconds")})


def mark_handoff(record_id: str, request_id: str, reason: str, path: Path = SELF_SERVICE_FILE) -> None:
    append_jsonl(path, {"type": "handoff", "id": record_id, "request_id": request_id, "reason": reason,
                        "time": datetime.now().isoformat(timespec="seconds")})


def hand_off(store: RequestStore, employee: str, question: str, result: dict | None, comment: str = "",
             escalate: bool = False) -> dict:
    """Вопрос сотрудника → обращение во «Входящих» с уже найденным ответом (оператор проверит и отправит).

    escalate — сотрудника ответ не устроил: обращение сразу «Передано специалисту», а не «Ответ готов».
    """
    body = question + (f"\n\nКомментарий сотрудника: {comment}" if comment else "")
    request = store.add(employee or "Сотрудник (адрес не указан)", "", body, source="сотрудник спросил сам")
    if result:
        request = store.set_result(request["id"], result)
    if escalate:
        request = store.update(request["id"], status=ESCALATED)
    return request


def load_records(path: Path = SELF_SERVICE_FILE) -> list[dict]:
    """Вопросы с учётом отметок «помогло» и передачи специалисту (последняя отметка — главная)."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    by_id, order = {}, []
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "question":
            record = dict(event, helped=None, comment="", request_id=None)
            by_id[record["id"]] = record
            order.append(record)
        elif event.get("id") in by_id:
            record = by_id[event["id"]]
            if event.get("type") == "helped":
                record["helped"], record["comment"] = event.get("helped"), event.get("comment", "")
            elif event.get("type") == "handoff":
                record["request_id"] = event.get("request_id")
    return order


def _since(period: str, now: datetime) -> datetime | None:
    days = PERIODS.get(period)
    return now - timedelta(days=days) if days else None


def closed_without_people(record: dict) -> bool:
    """Ответ получен сразу, специалисту не передавался, и сотрудник не отметил «Не помогло»."""
    return record["decision"] in (ANSWER, CHECK) and not record.get("request_id") and record.get("helped") is not False


def saved_hours(records: list[dict], baseline_minutes: int = BASELINE_SEARCH_MINUTES) -> float:
    """Оценка: ручной поиск (baseline_minutes) минус время ответа системы — по каждому вопросу, закрытому без людей."""
    seconds = sum(max(0.0, baseline_minutes * 60 - r.get("latency", 0)) for r in records if closed_without_people(r))
    return round(seconds / 3600, 1)


def summary(records: list[dict], period: str = "Месяц", now: datetime | None = None) -> dict:
    now = now or datetime.now()
    since = _since(period, now)
    items = [r for r in records if since is None or datetime.fromisoformat(r["time"]) >= since]
    # «Нужно уточнение» — промежуточный шаг: после указания суммы тот же вопрос будет записан ещё раз
    asked = [r for r in items if r["decision"] not in (BLOCKED, CLARIFY)]
    closed = [r for r in asked if closed_without_people(r)]
    handed = [r for r in asked if r.get("request_id") or needs_specialist(r["decision"])]
    rated = [r for r in asked if r.get("helped") is not None]
    answered = [r for r in asked if r["decision"] in (ANSWER, CHECK)]
    return {
        "total": len(asked),
        "closed": len(closed),
        "closed_share": round(100 * len(closed) / len(asked)) if asked else 0,
        "handed": len(handed),
        "handed_share": round(100 * len(handed) / len(asked)) if asked else 0,
        "helped": sum(1 for r in rated if r["helped"]),
        "rated": len(rated),
        "helped_share": round(100 * sum(1 for r in rated if r["helped"]) / len(rated)) if rated else None,
        "hours_saved": saved_hours(items),
        "avg_latency": round(sum(r["latency"] for r in answered) / len(answered), 1) if answered else None,
        "blocked": sum(1 for r in items if r["decision"] == BLOCKED),
    }


def inbox_summary(store: RequestStore, period: str = "Месяц", now: datetime | None = None) -> dict:
    """Обращения по почте (работа оператора) за период."""
    now = now or datetime.now()
    since = _since(period, now)
    items = [r for r in store.items if since is None or store.created_at(r) >= since]
    return {
        "total": len(items),
        "sent": sum(1 for r in items if r["status"] == SENT),
        "ready": sum(1 for r in items if r["status"] == READY),
        "escalated": sum(1 for r in items if r["status"] == ESCALATED),
        "overdue": sum(1 for r in items if store.is_overdue(r, now)),
        "open": sum(1 for r in items if r["status"] not in CLOSED),
    }


def to_review(records: list[dict], limit: int = 50) -> list[dict]:
    """Что разобрать руководителю: «Не помогло» и переданные специалисту — новые сверху."""
    chosen = [r for r in records if r.get("helped") is False or r.get("request_id") or needs_specialist(r["decision"])]
    return list(reversed(chosen))[:limit]

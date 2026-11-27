"""Перенос данных из прежней версии (окно Tkinter) в базу веб-версии.

python manage.py import_legacy

Берёт из папки data/: users.json (учётные записи — пароли остаются прежними), requests.json (обращения),
self_service.jsonl, feedback.jsonl, history.jsonl, audit.jsonl; справочник — из employees.json.
Повторный запуск ничего не дублирует: уже перенесённое пропускается.
"""
import json
from datetime import datetime

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from config import AUDIT_FILE, DATA_DIR, EMPLOYEES_FILE, FEEDBACK_FILE, HISTORY_FILE, REQUESTS_FILE, SELF_SERVICE_FILE
import employees as legacy_employees
from inbox import classify_process

from core import services as svc
from core.hashers import from_legacy
from core.models import (AuditRecord, Department, Employee, Feedback, HistoryRecord, Profile, Request,
                         SelfServiceQuestion)

DATE_FMT = "%d.%m.%Y %H:%M"


def aware(value: str | None, fmt: str | None = None):
    if not value:
        return None
    try:
        dt = datetime.strptime(value, fmt) if fmt else datetime.fromisoformat(value)
    except ValueError:
        return None
    return timezone.make_aware(dt) if timezone.is_naive(dt) else dt


def read_jsonl(path):
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


class Command(BaseCommand):
    help = "Перенести учётные записи, обращения, журналы и справочник из прежней версии"

    @transaction.atomic
    def handle(self, *args, **options):
        stats = {}
        # Учётные записи: хеш пароля переносится как есть (хешер legacy_pbkdf2) — пароли прежние
        users_file = DATA_DIR / "users.json"
        added = 0
        if users_file.exists():
            for item in json.loads(users_file.read_text(encoding="utf-8")):
                login = item["login"].lower()
                if User.objects.filter(username=login).exists():
                    continue
                user = User(username=login, is_active=item.get("active", True), password=from_legacy(item["salt"], item["hash"]))
                user.date_joined = aware(item.get("created"), DATE_FMT) or timezone.now()
                user.last_login = aware(item.get("last_login"), DATE_FMT)
                user.save()
                Profile.objects.create(user=user, name=item.get("name", login), role=item["role"])
                added += 1
        stats["учётных записей"] = added

        # Справочник
        added = 0
        if not Employee.objects.exists():
            for order, dep in enumerate(legacy_employees.load_directory(EMPLOYEES_FILE)):
                department = Department.objects.create(name=dep["department"], order=order)
                for person in dep["employees"]:
                    Employee.objects.create(department=department, **person)
                    added += 1
        stats["сотрудников"] = added

        # Обращения
        added = 0
        try:
            items = json.loads(REQUESTS_FILE.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            items = []
        for item in items:
            if Request.objects.filter(id=item["id"]).exists() or (
                    item.get("source_file") and Request.objects.filter(source_file=item["source_file"]).exists()):
                continue
            Request.objects.create(
                id=item["id"], created=aware(item["created"], DATE_FMT), due=aware(item["due"], DATE_FMT),
                sender=item.get("sender", ""), subject=item.get("subject", ""), body=item.get("body", ""),
                status=item["status"], read=item.get("read", False), source=item.get("source", ""),
                source_file=item.get("source_file"),
                process=item.get("process") or classify_process(f"{item.get('subject', '')} {item.get('body', '')}"),
                result=item.get("result"), processed_at=aware(item.get("processed_at"), DATE_FMT),
                sent_at=aware(item.get("sent_at"), DATE_FMT))
            added += 1
        stats["обращений"] = added

        # Журналы — только если в базе их ещё нет (повторный запуск не дублирует)
        if not SelfServiceQuestion.objects.exists():
            records = {r["id"]: r for r in [dict(r, helped=None, comment="", request_id=None)
                                            for r in read_jsonl(SELF_SERVICE_FILE) if r.get("type") == "question"]}
            for event in read_jsonl(SELF_SERVICE_FILE):
                if event.get("id") in records and event.get("type") == "helped":
                    records[event["id"]].update(helped=event.get("helped"), comment=event.get("comment", ""))
                elif event.get("id") in records and event.get("type") == "handoff":
                    records[event["id"]]["request_id"] = event.get("request_id")
            for r in records.values():
                q = SelfServiceQuestion.objects.create(
                    employee=r.get("employee", ""), question=r.get("question", ""), decision=r.get("decision", ""),
                    answer_source=r.get("answer_source"), found=bool(r.get("found")), latency=float(r.get("latency") or 0),
                    answer=r.get("answer", ""), helped=r.get("helped"), comment=r.get("comment", ""),
                    request=Request.objects.filter(id=r.get("request_id")).first())
                SelfServiceQuestion.objects.filter(pk=q.pk).update(created=aware(r.get("time")) or timezone.now())
            stats["вопросов сотрудников"] = len(records)
        if not Feedback.objects.exists():
            rows = read_jsonl(FEEDBACK_FILE)
            for r in rows:
                f = Feedback.objects.create(verdict=r.get("verdict", ""), comment=r.get("comment", ""), sender=r.get("sender", ""),
                                            subject=r.get("subject", ""), body=r.get("body", ""),
                                            answer_source=r.get("answer_source"), answer=r.get("answer", ""),
                                            basis=r.get("basis") or [], escalation_id=r.get("escalation_id"))
                Feedback.objects.filter(pk=f.pk).update(created=aware(r.get("time")) or timezone.now())
            stats["оценок"] = len(rows)
        if not HistoryRecord.objects.exists():
            rows = read_jsonl(HISTORY_FILE)
            for r in rows:
                h = HistoryRecord.objects.create(sender=r.get("sender", ""), subject=r.get("subject", ""),
                                                 body=r.get("body", ""), answer_source=r.get("answer_source"),
                                                 answer=r.get("answer", ""))
                HistoryRecord.objects.filter(pk=h.pk).update(created=aware(r.get("time")) or timezone.now())
            stats["записей истории"] = len(rows)
        if not AuditRecord.objects.exists():
            rows = read_jsonl(AUDIT_FILE)
            for r in rows:
                a = AuditRecord.objects.create(user=r.get("user", ""), action=r.get("action", ""), detail=r.get("detail", ""))
                AuditRecord.objects.filter(pk=a.pk).update(created=aware(r.get("time")) or timezone.now())
            stats["записей журнала"] = len(rows)

        svc.audit("перенос данных", "перенос из прежней версии", ", ".join(f"{k}: {v}" for k, v in stats.items()))
        for key, value in stats.items():
            self.stdout.write(f"{key}: {value}")

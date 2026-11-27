"""Мост между Django и прежними модулями: агент, база знаний, карта ответственности, справочник, отправка писем.

Вся логика ответа — в прежних модулях (agent.py, knowledge_base.py …), здесь только сохранение в базу
и подготовка данных для фронтенда.
"""
import email
import threading
import uuid
from datetime import datetime, timedelta
from email import policy
from email.utils import parsedate_to_datetime
from pathlib import Path

from django.db import transaction
from django.utils import timezone

import answer_confidence
import employees as legacy_employees
import self_service as legacy_self_service
from agent import _register_escalation as register_escalation
from agent import build_escalation_email, describe_doc, process_corporate_query
from config import (BASELINE_SEARCH_MINUTES, EMPLOYEES_FILE, ESCALATION_CONTACT, INBOX_DIR, REQUEST_SLA_HOURS,
                    SELF_SERVICE_TRUST_MODEL)
from inbox import CLOSED, ESCALATED, NEW, READY, SENT, WORK, classify_process, json_safe, status_from_result
from knowledge_base import SOURCE_GROUPS, get_knowledge_base, source_types_for
from ui_support import confidence, load_eml

from .models import (ADMIN, AuditRecord, Department, Employee, HistoryRecord, Profile, Request, SelfServiceQuestion,
                     Setting)

DATE_FMT = "%d.%m.%Y %H:%M"


# ---------- общее ----------

def local(dt: datetime | None) -> str:
    return timezone.localtime(dt).strftime(DATE_FMT) if dt else ""


def audit(user, action: str, detail: str = "") -> None:
    AuditRecord.objects.create(user=getattr(user, "username", None) or str(user or ""), action=action, detail=detail)


def profile(user) -> Profile:
    prof, _ = Profile.objects.get_or_create(user=user, defaults={"name": user.username})
    return prof


def is_admin(user) -> bool:
    return bool(user and user.is_authenticated and profile(user).role == ADMIN)


def user_dict(user) -> dict:
    prof = profile(user)
    person = legacy_employees.find_person(prof.name, directory()) if prof.name else None
    return {"login": user.username, "name": prof.name, "role": prof.role, "active": user.is_active,
            "created": local(user.date_joined), "last_login": local(user.last_login) or None,
            "email": (person or {}).get("email", ""),  # e-mail из справочника — для «Кто спрашивает»
            "department": (person or {}).get("department", "")}


def source_types(groups) -> set[str] | None:
    """Отметки «Искать в» с фронтенда → виды документов; None — все источники."""
    if groups is None:
        return None
    return source_types_for([g for g in groups if g in SOURCE_GROUPS])


# ---------- справочник «Сотрудники» ----------

def directory() -> list[dict]:
    """Справочник в формате прежнего модуля employees: [{"department", "employees": [...]}].
    Пока справочник в базе пуст (ещё не перенесён) — берётся employees.json."""
    if not Department.objects.exists():
        return legacy_employees.load_directory(EMPLOYEES_FILE)
    return [{"department": d.name, "employees": [{"fio": e.fio, "position": e.position, "email": e.email,
                                                   "phone": e.phone} for e in d.employees.all()]}
            for d in Department.objects.prefetch_related("employees")]


def department_names() -> list[str]:
    return [d["department"] for d in directory() if d["department"]]


def department_of(who: str) -> str | None:
    """Подразделение по «Кто спрашивает» / отправителю письма: e-mail, ФИО или «Фамилия И.О.» из справочника."""
    person = legacy_employees.find_person(who or "", directory()) if (who or "").strip() else None
    return (person or {}).get("department") or None


def asker(user) -> tuple[str, str | None]:
    """(«ФИО <e-mail>», подразделение) вошедшего пользователя: ФИО — из учётной записи, e-mail и подразделение —
    из справочника «Сотрудники». Нет в справочнике — подразделение None (ответ по всем документам)."""
    me = user_dict(user)
    name = me["name"] or user.username
    return (f"{name} <{me['email']}>" if me["email"] else name), (me["department"] or None)


AUTO, ALL_DEPARTMENTS = "", "*"


def resolve_department(choice, who: str):
    """Выбор на экране → подразделение(я) для поиска. Список (с «Главной») — выбранные подразделения, пустой —
    без учёта подразделения; «» (авто) — по справочнику; «*» — без учёта подразделения."""
    if isinstance(choice, (list, tuple)):
        known = department_names()
        picked = [c for c in choice if c in known]
        return picked or None
    choice = (choice or "").strip()
    if choice == ALL_DEPARTMENTS:
        return None
    if choice and choice in department_names():
        return choice
    return department_of(who)


def export_employees() -> None:
    """employees.json — для агента (строка «Контакты» в письме) и для архива переноса."""
    legacy_employees.save_directory(directory(), EMPLOYEES_FILE)


def contact_text(responsible: str) -> str:
    person = legacy_employees.find_person(responsible or "", directory())
    if not person:
        return ""
    surname = person["fio"].split()[0]
    parts = ([] if surname in (responsible or "") else [person["fio"]]) + [p for p in (person["email"], person["phone"]) if p]
    return " · ".join(parts)


# ---------- обращения ----------

def _request_id(created: datetime) -> str:
    return f"REQ-{timezone.localtime(created):%Y%m%d}-{uuid.uuid4().hex[:4].upper()}"


def add_request(sender: str, subject: str, body: str, created: datetime | None = None, source: str = "вручную",
                source_file: str | None = None) -> Request:
    created = created or timezone.now()
    return Request.objects.create(
        id=_request_id(created), created=created, due=created + timedelta(hours=REQUEST_SLA_HOURS), sender=sender,
        subject=subject, body=body, status=NEW, read=False, source=source, source_file=source_file,
        process=classify_process(f"{subject} {body}"))


def set_result(request: Request, result: dict) -> Request:
    request.status = status_from_result(result)
    request.result = json_safe(result)
    request.processed_at = timezone.now()
    request.save()
    return request


def is_overdue(request: Request, now: datetime | None = None) -> bool:
    return request.status not in CLOSED and (now or timezone.now()) > request.due


def request_dict(request: Request, now: datetime | None = None, full: bool = False) -> dict:
    data = {"id": request.id, "created": local(request.created), "due": local(request.due),
            "sender": request.sender, "subject": request.subject, "status": request.status, "read": request.read,
            "source": request.source, "process": request.process, "overdue": is_overdue(request, now),
            "closed": request.status in CLOSED, "sent_at": local(request.sent_at) or None,
            "assignee": (request.result or {}).get("assignee", ""),  # кому передано (эксперт темы и т. п.)
            "preview": (request.subject or request.body[:70])}
    if full:
        data.update(body=request.body, result=shown(request.result), processed_at=local(request.processed_at) or None)
    return data


def _eml_date(path: Path) -> datetime | None:
    try:
        with open(path, "rb") as file:
            header = email.message_from_binary_file(file, policy=policy.default).get("Date")
        if header:
            value = parsedate_to_datetime(str(header))
            return value if value.tzinfo else timezone.make_aware(value)
    except (TypeError, ValueError):
        return None
    return None


DELETED_FILES_KEY = "deleted_request_files"  # письма удалённых обращений: «Обновить» их больше не загружает


def forget_request(item: Request) -> None:
    """Удалить обращение. Ссылки на него в истории вопросов обнуляются (запись истории остаётся)."""
    if item.source_file:
        setting, _ = Setting.objects.get_or_create(key=DELETED_FILES_KEY, defaults={"value": {"files": []}})
        files = set(setting.value.get("files", [])) | {item.source_file}
        setting.value = {"files": sorted(files)}
        setting.save()
    item.delete()


def import_eml(path: Path, source: str = "почта", force: bool = False) -> Request | None:
    """Письмо .eml → обращение (каждый файл — один раз). Письмо удалённого обращения из папки не загружается;
    force — загрузка вручную («Загрузить .eml»): пользователь сам выбрал файл."""
    path = Path(path)
    if Request.objects.filter(source_file=path.name).exists():
        return None
    deleted = Setting.objects.filter(key=DELETED_FILES_KEY).first()
    if deleted and path.name in deleted.value.get("files", []):
        if not force:
            return None
        deleted.value = {"files": [f for f in deleted.value.get("files", []) if f != path.name]}
        deleted.save()
    letter = load_eml(path)
    return add_request(letter["sender"], letter["subject"], letter["body"], created=_eml_date(path), source=source,
                       source_file=path.name)


def import_folder(folder: Path = INBOX_DIR) -> int:
    if not Path(folder).exists():
        return 0
    return sum(1 for p in sorted(Path(folder).glob("*.eml")) if import_eml(p))


# ---------- ответ ----------

def run_query(sender: str, subject: str, body: str, groups=None, department: str | None = None,
              prefer: set[str] | None = None) -> dict:
    """Ответ агента; ответ с уверенностью не выше порога скрытия не показывается — вопрос уходит специалисту.
    department — подразделение спрашивающего (см. resolve_department); None — все документы.
    prefer — документы предыдущего ответа в цепочке уточнений."""
    result = json_safe(process_corporate_query(sender, subject, body, source_types=source_types(groups),
                                               department=department, prefer_docs=prefer))
    params = confidence_params()
    verdict = answer_confidence.assess(result, params)
    if verdict["color"] != "hidden":
        return result
    reason = f"Низкая уверенность в ответе ({verdict['score']} из 100)"
    assigned = assign_for(body, result.get("active_docs"), department)
    escalation_id = register_escalation(sender, subject, body, reason, assigned["assignee"])
    email_draft = build_escalation_email(sender, subject, escalation_id, assignee=assigned["assignee"]).replace(
        "не найден в действующих регламентах, приказах и переписке.",
        "требует проверки специалистом: система не уверена в найденном ответе.")
    hidden = answer_confidence.withhold(result, verdict, params["hide_threshold"], escalation_id, email_draft,
                                        assigned["assignee"])
    return {**hidden, "assignee": assigned["assignee"], "assignee_reason": assigned["reason"], "topic": assigned["topic"]}


def assign_for(question: str, docs: list | None, department=None) -> dict:
    """Ответственный за вопрос без ответа (topics.assign): эксперт темы → владелец документа → дежурный методолог."""
    from topics import assign
    return assign(question, docs or [], get_knowledge_base().embedder, department=department)


def my_marks(user) -> set[str]:
    """Чем вошедший помечен в поле «ответственный»: e-mail и ФИО (из учётной записи и справочника)."""
    me = user_dict(user)
    return {m.lower() for m in (me["email"], me["name"]) if m}


def is_mine(assignee: str, marks: set[str]) -> bool:
    text = (assignee or "").lower()
    return any(m and m in text for m in marks)


# ---------- темы ----------

def topic_view(entry: dict) -> dict:
    """Тема для экрана «Темы и ответственные»: что редактируется и есть ли готовый ответ."""
    if entry.get("by_department"):
        ready = {"kind": "by_department", "variants": [
            {"department": r["department"], "answer": r.get("answer", ""), "expert": r.get("expert", ""),
             "basis": r.get("basis", ""), "points": r.get("points", [])} for r in entry["by_department"]]}
    elif entry.get("rules"):
        ready = {"kind": "amount", "variants": [{"department": "", "answer": r.get("answer", ""), "expert": "",
                                                 "basis": entry.get("basis", ""), "points": []} for r in entry["rules"]]}
    elif entry.get("answer"):
        ready = {"kind": "single", "answer": entry["answer"], "basis": entry.get("basis", ""),
                 "points": entry.get("points", [])}
    else:
        ready = {"kind": "none"}
    match = entry.get("match") or {}
    return {"id": entry["id"], "name": entry.get("process", ""), "stage": entry.get("stage", ""),
            "expert": entry.get("expert", ""), "keywords": [", ".join(g) for g in match.get("all", [])],
            "exclude": ", ".join(match.get("none", [])), "examples": entry.get("examples", []),
            "documents": entry.get("documents", []), "ready": ready}


def _words(line: str) -> list[str]:
    return [w.strip().lower().replace("ё", "е") for w in line.replace(";", ",").split(",") if w.strip()]


def topic_update(entry: dict, data: dict) -> list[str]:
    """Изменения темы с экрана; возвращает ошибки (тогда ничего не меняется). Готовый ответ проверяется:
    сроки, суммы и ФИО в нём должны быть в тексте документа-основания."""
    from fact_check import find_unverified_facts
    errors, kb = [], get_knowledge_base()

    def check(answer: str, basis_id: str, where: str):
        doc = kb.get_doc(basis_id) if basis_id else None
        if doc is None:
            return
        issues = find_unverified_facts(answer, f"{doc['title']} {doc['owner']} {doc.get('full_text', '')}")
        if issues:
            errors.append(f"{where}: нет в «{doc['title']}» — {', '.join(issues)}")

    new = dict(entry)
    if "name" in data:
        if not str(data["name"]).strip():
            errors.append("Укажите название темы")
        new["process"] = str(data["name"]).strip()
    if "expert" in data:
        new["expert"] = str(data["expert"] or "").strip()
    if "keywords" in data or "exclude" in data:
        match = dict(new.get("match") or {})
        if "keywords" in data:
            match["all"] = [g for g in (_words(line) for line in data["keywords"] or []) if g]
        if "exclude" in data:
            match["none"] = _words(data["exclude"] or "")
        new["match"] = match
    if "examples" in data:
        new["examples"] = [" ".join(str(e).split()) for e in data["examples"] or [] if str(e).strip()]
    if "documents" in data:
        new["documents"] = [str(d) for d in data["documents"] or []]
    if "answer" in data and new.get("answer") is not None and not new.get("rules"):
        answer = str(data["answer"] or "").strip()
        if not answer:
            errors.append("Готовый ответ пустой")
        check(answer, new.get("basis", ""), "Готовый ответ")
        new["answer"] = answer
    if "variants" in data and new.get("by_department"):
        given = {v.get("department"): v for v in data["variants"] or []}
        rules = []
        for rule in new["by_department"]:
            v = given.get(rule["department"])
            rule = dict(rule)
            if v is not None:
                answer = str(v.get("answer") or "").strip()
                if not answer:
                    errors.append(f"Ответ для «{rule['department']}» пустой")
                check(answer, rule.get("basis", ""), f"Ответ для «{rule['department']}»")
                rule["answer"], rule["expert"] = answer, str(v.get("expert") or "").strip()
            rules.append(rule)
        new["by_department"] = rules
    if not errors:
        entry.clear()
        entry.update(new)
    return errors


def add_topic_example(topic_name: str, question: str) -> bool:
    """Вопрос, переданный специалистом «в тему», становится примером этой темы (тема определяется точнее)."""
    from process_map import load_process_map, save_process_map
    question = " ".join((question or "").split("\n\n")[0].split())[:300]  # без комментария сотрудника
    entries = load_process_map()
    topic = next((t for t in entries if t.get("process") == topic_name), None)
    if topic is None or not question:
        return False
    examples = topic.setdefault("examples", [])
    if question not in examples:
        examples.append(question)
        save_process_map(entries)
    return True


def set_assignee(item: Request, assignee: str, reason: str) -> None:
    """Ответственный за обращение — в результате обращения («Передано специалисту», фильтр «Мои»)."""
    item.result = {**(item.result or {}), "assignee": assignee, "assignee_reason": reason, "responsible": assignee}
    item.save(update_fields=["result"])


def shown(result: dict | None) -> dict | None:
    """Сохранённый результат для экрана: если по текущим критериям ответ скрывается — показать без текста ответа
    (старые записи истории и обращений; повторно специалисту не передаётся)."""
    if not result:
        return result
    params = confidence_params()
    verdict = answer_confidence.assess(result, params)
    if verdict["color"] != "hidden" or result.get("withheld"):
        return result
    hidden = answer_confidence.withhold(result, verdict, params["hide_threshold"], result.get("escalation_id"),
                                        contact=ESCALATION_CONTACT)
    if result.get("draft_saved_by"):  # черновик, сохранённый специалистом, — его работа, не скрывается
        hidden.update({k: result[k] for k in ("email_draft", "draft_saved_by", "draft_saved_at") if k in result})
    return hidden


def add_history(user, question: dict, result: dict, dialog: str = "", step: int = 0,
                understood_as: str = "") -> HistoryRecord:
    return HistoryRecord.objects.create(user=user, sender=question.get("sender", ""), subject=question.get("subject", ""),
                                        body=question.get("body", ""), answer_source=result.get("answer_source"),
                                        answer=(result.get("answer") or "")[:300], result=result,
                                        dialog=dialog, step=step, understood_as=understood_as)


# ---------- цепочка уточнений ----------

def rewrite_llm(messages: list[dict]) -> str:
    """Модель для переписывания уточнения в полный вопрос (короткий ответ)."""
    from agent import _call_llm
    completion = _call_llm(messages, max_tokens=80)
    return completion.choices[0].message.content or ""


def continue_dialog(user, previous: SelfServiceQuestion | None, typed: str) -> dict:
    """Новый вопрос или уточнение к previous: dialog, step, understood_as (самостоятельный вопрос), метод,
    prefer (документы предыдущего ответа), reset — цепочка «остыла» или слишком длинная, начат новый вопрос."""
    import follow_up
    new = {"dialog": uuid.uuid4().hex, "step": 0, "understood_as": typed, "method": "как есть", "prefer": None,
           "reset": False}
    if previous is None:
        return new
    if not previous.dialog:  # вопрос задан до появления цепочек
        previous.dialog = uuid.uuid4().hex
        previous.save(update_fields=["dialog"])
    first = SelfServiceQuestion.objects.filter(dialog=previous.dialog).order_by("created").first() or previous
    if previous.step + 1 > follow_up.MAX_STEPS or timezone.now() - first.created > timedelta(minutes=follow_up.MAX_MINUTES):
        return {**new, "reset": True}
    from guardrails import check_prompt_injection
    if check_prompt_injection(typed):  # не давать модели «пересказать» попытку обхода — пусть фильтр её отклонит
        understood, method = typed, "как есть"
    else:
        understood, method = follow_up.rewrite(previous.understood_as or previous.question, typed, llm=rewrite_llm)
    last = HistoryRecord.objects.filter(user=user, dialog=previous.dialog).order_by("-created").first()
    basis = (last.result or {}).get("basis_docs") or [] if last else []
    return {"dialog": previous.dialog, "step": previous.step + 1, "understood_as": understood, "method": method,
            "prefer": {d["id"] for d in basis if d.get("id")} or None, "reset": False}


def dialog_text(record: SelfServiceQuestion) -> str:
    """Для специалиста: вся цепочка уточнений, а не только последняя фраза."""
    if not record.dialog or record.step == 0:
        return record.question
    chain = SelfServiceQuestion.objects.filter(dialog=record.dialog, step__lte=record.step).order_by("step", "created")
    lines = [f"{'Вопрос' if q.step == 0 else 'Уточнение'} {q.step + 1}: {q.question}" for q in chain]
    return "\n".join(lines + [f"Система поняла вопрос так: {record.understood_as}"])


CONFIDENCE_KEY = "answer_confidence"


def confidence_params() -> dict:
    """Критерии уверенности: рекомендуемые значения + то, что изменил администратор."""
    saved = Setting.objects.filter(key=CONFIDENCE_KEY).first()
    params = {**answer_confidence.DEFAULTS, **(saved.value if saved else {})}
    _, errors = answer_confidence.validate(params)
    # Сохранённые раньше значения не сочетаются с новыми критериями (например, порог скрытия выше жёлтого) —
    # действуют рекомендуемые, пока администратор не сохранит заново
    return answer_confidence.DEFAULTS.copy() if errors else params


def describe(result: dict) -> dict:
    """Что показать у ответа: уровень надёжности, подпись, контакты ответственного, плашка уверенности."""
    result = shown(result)
    level, label = confidence(result)
    responsible = result.get("responsible", "")
    return {"level": level, "label": label, "contacts": contact_text(responsible),
            "escalation_contacts": contact_text(ESCALATION_CONTACT),
            "confidence": answer_confidence.assess(result, confidence_params())}


def decide(result: dict) -> str:
    return legacy_self_service.decide(result, SELF_SERVICE_TRUST_MODEL)


def hand_off(employee: str, question: str, result: dict | None, comment: str = "", escalate: bool = False) -> Request:
    body = question + (f"\n\nКомментарий сотрудника: {comment}" if comment else "")
    request = add_request(employee or "Сотрудник (адрес не указан)", "", body, source="сотрудник спросил сам")
    if result:
        set_result(request, result)
        if not result.get("assignee"):  # сотрудник сам передал вопрос с ответом — ответственный по теме ответа
            assigned = assign_for(question, result.get("basis_docs") or result.get("active_docs"),
                                  department_of(employee))
            set_assignee(request, assigned["assignee"], assigned["reason"])
    if escalate:
        request.status = ESCALATED
        request.save()
    return request


# ---------- сводка ----------

def _records(since: datetime | None):
    items = SelfServiceQuestion.objects.all()
    if since:
        items = items.filter(created__gte=since)
    return [{"time": timezone.localtime(q.created).replace(tzinfo=None).isoformat(timespec="seconds"),
             "decision": q.decision, "request_id": q.request_id, "helped": q.helped, "latency": q.latency,
             "employee": q.employee, "question": q.question, "comment": q.comment} for q in items]


def summary(period: str) -> dict:
    days = legacy_self_service.PERIODS.get(period, 30)
    now = timezone.now()
    since = now - timedelta(days=days) if days else None
    s = legacy_self_service.summary(_records(since), "Всё время")
    requests = Request.objects.all() if since is None else Request.objects.filter(created__gte=since)
    inbox = {"total": requests.count(), "sent": requests.filter(status=SENT).count(),
             "ready": requests.filter(status=READY).count(), "escalated": requests.filter(status=ESCALATED).count(),
             "overdue": sum(1 for r in requests if is_overdue(r, now)),
             "open": requests.exclude(status__in=CLOSED).count()}
    review = legacy_self_service.to_review(_records(None))
    return {"self_service": s, "inbox": inbox, "baseline_minutes": BASELINE_SEARCH_MINUTES,
            "review": [dict(r, time=r["time"].replace("T", " ")[:16]) for r in review]}


# ---------- документы базы ----------

def documents() -> list[dict]:
    kb = get_knowledge_base()
    return [{"id": d["id"], "status": d["status"], "type": d["source_type"], "title": d["title"], "date": d["date"],
             "owner": d["owner"], "reason": d["status_reason"], "priority": d["priority"],
             "departments": list(d.get("departments") or []), "placed": d.get("placed", "")}
            for d in sorted(kb.documents, key=lambda d: (-d["priority"], d["title"]))]


def document_detail(doc_id: str) -> dict | None:
    doc = get_knowledge_base().get_doc(doc_id)
    if doc is None:
        return None
    return {"id": doc["id"], "title": doc["title"], "type": doc["source_type"], "date": doc["date"], "owner": doc["owner"],
            "status": doc.get("status", ""), "reason": doc.get("status_reason", ""), "number": doc.get("number", ""),
            "effective_from": doc.get("effective_from"), "review_date": doc.get("review_date"),
            "chunks": doc.get("chunks") or [{"point": None, "text": doc.get("content", "")}],
            "has_file": bool(doc.get("path")), "label": _describe(doc),
            # для правки текста: весь текст и формат файла (на экране — предупреждение для .docx / .pdf / писем)
            "text": doc.get("full_text", ""), "file_format": Path(doc["path"]).suffix.lower() if doc.get("path") else "",
            "departments": list(doc.get("departments") or []), "placed": doc.get("placed", ""),
            # «Статус» в карточке: отмечен ли документ вручную как утративший силу
            "edition": EDITION_OUTDATED if doc.get("declared_status") == "Устарел" else EDITION_CURRENT}


EDITION_CURRENT, EDITION_OUTDATED = "Актуальная редакция", "Утратил силу"


def document_types() -> list[str]:
    """Справочник видов документов для карточки (порядок — по приоритету: приказ выше чата)."""
    from actuality import PRIORITY
    return sorted(PRIORITY, key=lambda t: -PRIORITY[t])


def _describe(doc: dict) -> str:
    try:
        return describe_doc(doc)
    except KeyError:
        return doc.get("title", doc.get("id", "документ"))


def attention() -> list[dict]:
    kb = get_knowledge_base()
    known = set(department_names())
    # Документ привязан к подразделению, которого нет в справочнике (переименовали или удалили) — для сотрудников
    # этого подразделения он больше не найдётся, пока администратор не поправит карточку
    stale = []
    for doc in kb.documents if known else []:
        missing = [x for x in doc.get("departments") or [] if x not in known]
        if missing:
            stale.append({"kind": "Подразделение", "doc_id": doc["id"],
                          "text": f"«{doc['title']}» привязан к подразделению {', '.join('«' + x + '»' for x in missing)}, "
                                  "которого нет в справочнике «Сотрудники». Исправьте «Для кого» в карточке документа."})
    return ([{"kind": "Противоречие", "text": c["text"], "doc_id": c["doc_id"]} for c in kb.conflicts]
            + [{"kind": "Пересмотр", "text": r["text"], "doc_id": r["doc_id"]} for r in kb.reminders] + stale)


# ---------- обновление: перезапуск сервера ----------

def schedule_restart(port: int, delay: float = 1.5) -> None:
    """Перезапуск после обновления или отката: новый процесс start_web.py ждёт, пока этот освободит порт,
    поднимается на том же порту (без нового окна браузера), применяет миграции — а этот процесс завершается."""
    import os
    import subprocess
    import sys
    from django.conf import settings
    script = settings.PROJECT_ROOT / "web" / "start_web.py"
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    env = {**os.environ, "WEB_PORT": str(port), "WEB_NO_BROWSER": "1", "WEB_WAIT_PORT_FREE": "1"}
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([str(pythonw if pythonw.exists() else exe), str(script)], cwd=str(settings.PROJECT_ROOT),
                     env=env, creationflags=flags, close_fds=True)
    threading.Timer(delay, lambda: os._exit(0)).start()  # ответ браузеру успеет уйти


# ---------- «Обработать новые» в фоне ----------

_batch = {"running": False, "done": 0, "total": 0, "error": None, "only": ""}
_batch_lock = threading.Lock()


def batch_status() -> dict:
    return dict(_batch)


def start_batch(groups=None) -> bool:
    with _batch_lock:
        if _batch["running"]:
            return False
        pending = list(Request.objects.filter(status=NEW).values_list("id", flat=True))
        _batch.update(running=True, done=0, total=len(pending), error=None,
                      only="" if source_types(groups) is None else ", ".join(g.lower() for g in groups))

    def worker():
        from django.db import connection
        try:
            for request_id in pending:
                request = Request.objects.get(id=request_id)
                request.status = WORK
                request.save(update_fields=["status"])
                try:
                    result = run_query(request.sender, request.subject, request.body, groups,
                                       department=department_of(request.sender))
                except Exception as exc:  # модель недоступна — остальные остаются новыми
                    request.status = NEW
                    request.save(update_fields=["status"])
                    _batch["error"] = type(exc).__name__
                    return
                with transaction.atomic():
                    set_result(request, result)
                _batch["done"] += 1
        finally:
            _batch["running"] = False
            connection.close()

    threading.Thread(target=worker, daemon=True).start()
    return True

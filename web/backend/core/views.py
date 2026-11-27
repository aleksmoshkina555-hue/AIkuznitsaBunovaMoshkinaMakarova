"""API веб-версии (все ответы — JSON). Права: вход обязателен; администрирование, справочник, документы
базы — только роли «Администратор» (проверка в каждом изменяющем запросе, а не только скрытием кнопок)."""
import re
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path

from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import FileResponse, Http404
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.response import Response

import admin as legacy_admin
import employees as legacy_employees
from agent import SYSTEM_NAME, LLMModelNotFoundError, LLMUnavailableError
from config import ESCALATION_CONTACT, INBOX_DIR, KNOWLEDGE_DIR, LLM_MODEL, REQUEST_SLA_HOURS, SMTP_HOST
from inbox import CLOSED, ESCALATED, NEW, OTHER_PROCESS, READY, SENT, WORK
from knowledge_base import SOURCE_GROUPS, get_knowledge_base, rebuild_state
from mailer import extract_email, send_reply
from process_map import load_process_map
from self_service import ANSWER, CHECK, CLARIFY, needs_specialist
from users import generate_password, password_problems

from . import services as svc
from .models import (ADMIN, ROLES, USER, Department, Employee, Feedback, HistoryRecord, Profile, Request,
                     SelfServiceQuestion, AuditRecord, Setting)

MAX_FAILURES, LOCK_SECONDS = 5, 30


class IsAdmin(BasePermission):
    message = "Действие доступно только администратору"

    def has_permission(self, request, view):
        return svc.is_admin(request.user)


def bad(text, code=status.HTTP_400_BAD_REQUEST):
    return Response({"error": text}, status=code)


def model_unavailable(exc):
    """Ollama не запущена или модель не скачана — понятное сообщение вместо «Ошибка 500»."""
    if isinstance(exc, LLMModelNotFoundError):
        text = f"В Ollama нет модели {LLM_MODEL}. Администратору: выполните «ollama pull {LLM_MODEL}»."
    else:
        text = ("Локальная модель сейчас недоступна (Ollama не запущена). Ответы по карте ответственности и по версиям "
                "документов работают и без неё; этот вопрос попробуйте позже или обратитесь к администратору.")
    return bad(text, status.HTTP_503_SERVICE_UNAVAILABLE)


# ---------- вход ----------

@api_view(["GET"])
@permission_classes([AllowAny])
@ensure_csrf_cookie
def me(request):
    """Кто вошёл (и cookie csrftoken для фронтенда)."""
    if not request.user or not request.user.is_authenticated:
        return Response({"user": None, "system": SYSTEM_NAME})
    return Response({"user": svc.user_dict(request.user), "system": SYSTEM_NAME})


@api_view(["POST"])
@permission_classes([AllowAny])
def login_view(request):
    username = (request.data.get("login") or "").strip().lower()
    password = request.data.get("password") or ""
    key = f"login-failures:{username}"
    failures = cache.get(key, 0)
    if failures >= MAX_FAILURES:
        return bad(f"Слишком много попыток. Подождите {LOCK_SECONDS} секунд.", status.HTTP_429_TOO_MANY_REQUESTS)
    user = authenticate(request, username=username, password=password)
    if user is None:
        cache.set(key, failures + 1, LOCK_SECONDS)
        svc.audit(username, "неудачный вход")
        if failures + 1 >= MAX_FAILURES:
            return bad(f"Неверный логин или пароль. Вход заблокирован на {LOCK_SECONDS} с.", status.HTTP_429_TOO_MANY_REQUESTS)
        return bad("Неверный логин или пароль, либо учётная запись заблокирована.", status.HTTP_401_UNAUTHORIZED)
    cache.delete(key)
    login(request, user)
    svc.audit(user, "вход")
    return Response({"user": svc.user_dict(user)})


@api_view(["POST"])
def logout_view(request):
    svc.audit(request.user, "выход")
    logout(request)
    return Response({"ok": True})


@api_view(["GET"])
@permission_classes([AllowAny])
def ping(request):
    """Опознавательный ответ: запускатель отличает наш сервер от другой программы на том же порту."""
    return Response({"app": "ai-konsultant"})


@api_view(["GET"])
@permission_classes([AllowAny])
def setup_state(request):
    """Первый запуск: учётных записей ещё нет — фронтенд показывает «создайте администратора»."""
    return Response({"needs_admin": not User.objects.exists()})


@api_view(["POST"])
@permission_classes([AllowAny])
def setup_admin(request):
    if User.objects.exists():
        return bad("Администратор уже создан", status.HTTP_403_FORBIDDEN)
    errors = _create_user(request.data.get("login", ""), request.data.get("name", ""), ADMIN, request.data.get("password", ""))
    if errors:
        return bad("; ".join(errors))
    user = authenticate(request, username=request.data["login"].strip().lower(), password=request.data["password"])
    login(request, user)
    svc.audit(user, "создана учётная запись", f"{user.username} ({ADMIN}) — первый запуск")
    return Response({"user": svc.user_dict(user)})


@api_view(["POST"])
def change_own_password(request):
    user = request.user
    if not user.check_password(request.data.get("old", "")):
        svc.audit(user, "неудачная смена своего пароля")
        return bad("Текущий пароль указан неверно")
    new = request.data.get("new", "")
    if new == request.data.get("old", ""):
        return bad("Новый пароль должен отличаться от текущего")
    try:
        validate_password(new, user)
    except ValidationError as exc:
        return bad("; ".join(exc.messages))
    user.set_password(new)
    user.save()
    update_session_auth_hash(request, user)  # остаться во входе после смены пароля
    svc.audit(user, "сменён пароль", user.username)
    return Response({"ok": True})


# ---------- справочная информация для фронтенда ----------

@api_view(["GET"])
def meta(request):
    processes = [e["process"] for e in load_process_map()] + [OTHER_PROCESS]
    data = {"processes": processes, "source_groups": list(SOURCE_GROUPS), "smtp": bool(SMTP_HOST),
            "sla_hours": REQUEST_SLA_HOURS, "escalation_contact": ESCALATION_CONTACT, "model": LLM_MODEL,
            "statuses": {"new": NEW, "work": WORK, "ready": READY, "sent": SENT, "escalated": ESCALATED},
            "is_admin": svc.is_admin(request.user), "departments": svc.department_names(),
            "document_types": svc.document_types()}
    if data["is_admin"]:
        data["version"] = legacy_admin.read_version()  # номер версии — только администратору
    return Response(data)


@api_view(["GET"])
def department(request):
    """Подразделение по «Кто спрашивает» / отправителю: для подсказки «Подразделение: … (по справочнику)»."""
    return Response({"department": svc.department_of(request.GET.get("who", ""))})


# ---------- «Задать вопрос»: сотрудник спрашивает сам ----------

@api_view(["POST"])
def ask(request):
    question = (request.data.get("question") or "").strip()
    if not question:
        return bad("Напишите вопрос")
    groups = request.data.get("sources")
    if groups is not None and not groups:
        return bad("Отметьте хотя бы один источник")
    # Кто спрашивает и его подразделение — по учётной записи, под которой вошли (и справочнику «Сотрудники»),
    # а не по тому, что прислал браузер: сотруднику ничего не нужно заполнять
    employee, department = svc.asker(request.user)
    # Уточнение к предыдущему вопросу (поле «Уточнить» под ответом): переписывается в самостоятельный вопрос
    previous = None
    if request.data.get("follow_up_of"):
        previous = SelfServiceQuestion.objects.filter(pk=request.data["follow_up_of"], user=request.user).first()
    try:
        chain = svc.continue_dialog(request.user, previous, question)
        result = svc.run_query(employee or "Сотрудник", "", chain["understood_as"], groups, department=department,
                               prefer=chain["prefer"])
    except (LLMUnavailableError, LLMModelNotFoundError) as exc:
        return model_unavailable(exc)
    decision = svc.decide(result)
    svc.add_history(request.user, {"sender": employee, "body": question}, result, dialog=chain["dialog"],
                    step=chain["step"], understood_as=chain["understood_as"])
    record = SelfServiceQuestion.objects.create(
        user=request.user, employee=employee, question=question, decision=decision,
        answer_source=result.get("answer_source"), found=bool(result.get("found")) and not result.get("blocked"),
        latency=float(result.get("latency") or 0), answer=(result.get("answer") or "")[:300],
        dialog=chain["dialog"], step=chain["step"], understood_as=chain["understood_as"], rewrite_method=chain["method"])
    if needs_specialist(decision):
        record.request = svc.hand_off(employee, svc.dialog_text(record), result)
        record.save()
    return Response({"record_id": record.id, "decision": decision, "result": result,
                     "request_id": record.request_id, **svc.describe(result),
                     "email_ok": bool(extract_email(employee)),
                     "dialog": chain["dialog"], "step": chain["step"], "chain_reset": chain["reset"],
                     "understood_as": chain["understood_as"] if chain["understood_as"] != question else None,
                     "rewrite_method": chain["method"]})


@api_view(["POST"])
def ask_helped(request, pk):
    record = SelfServiceQuestion.objects.filter(pk=pk, user=request.user).first()
    if record is None:
        raise Http404
    record.helped = bool(request.data.get("helped"))
    record.comment = (request.data.get("comment") or "").strip()
    record.save()
    return Response({"ok": True})


@api_view(["POST"])
def ask_handoff(request, pk):
    record = SelfServiceQuestion.objects.filter(pk=pk, user=request.user).first()
    if record is None:
        raise Http404
    if record.request_id:
        return bad("Вопрос уже передан специалисту")
    history = HistoryRecord.objects.filter(user=request.user, body=record.question,
                                           **({"dialog": record.dialog} if record.dialog else {})).first()
    comment = (request.data.get("comment") or "").strip() or "сотрудник передал вопрос специалисту"
    record.request = svc.hand_off(record.employee, svc.dialog_text(record), history.result if history else None,
                                  comment=comment, escalate=True)
    record.save()
    return Response({"request_id": record.request_id})


# ---------- «Главная»: ответ оператора на обращение ----------

@api_view(["POST"])
def answer(request):
    body = (request.data.get("body") or "").strip()
    if not body:
        return bad("Напишите вопрос или вставьте письмо")
    groups = request.data.get("sources")
    if groups is not None and not groups:
        return bad("Отметьте хотя бы один источник")
    sender, subject = request.data.get("sender") or "", request.data.get("subject") or ""
    request_id = request.data.get("request_id")
    item = Request.objects.filter(id=request_id).first() if request_id else None
    if item is None:
        item = svc.add_request(sender, subject, body)
    elif sender.strip() and sender.strip() != item.sender:
        item.sender = sender.strip()  # специалист указал или поправил «От кого вопрос»
    item.read, item.status, item.body = True, WORK, body
    item.save()
    department = svc.resolve_department(request.data.get("department"), item.sender)
    try:
        result = svc.run_query(item.sender, item.subject, body, groups, department=department)
    except (LLMUnavailableError, LLMModelNotFoundError) as exc:
        item.status = NEW  # ответа нет — обращение снова «Новое», его можно обработать позже
        item.save(update_fields=["status"])
        return model_unavailable(exc)
    svc.add_history(request.user, {"sender": item.sender, "subject": item.subject, "body": body}, result)
    svc.set_result(item, result)
    return Response({"result": result, "request": svc.request_dict(item, full=True), **svc.describe(result)})


@api_view(["POST"])
def parse_letter(request):
    """«Вставить письмо» (текст со строками «От:», «Тема:») или «Открыть .eml» → отправитель, тема, текст."""
    from ui_support import load_eml, parse_pasted_email
    upload = request.FILES.get("file")
    if upload is not None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "letter.eml"
            path.write_bytes(upload.read())
            try:
                return Response(load_eml(path))
            except Exception as exc:
                return bad(f"Не удалось прочитать письмо: {type(exc).__name__}")
    return Response(parse_pasted_email(request.data.get("text") or ""))


@api_view(["POST"])
def feedback(request):
    result = request.data.get("result") or {}
    Feedback.objects.create(user=request.user, verdict=request.data.get("verdict", ""), comment=request.data.get("comment", ""),
                            sender=request.data.get("sender", ""), subject=request.data.get("subject", ""),
                            body=request.data.get("body", ""), answer_source=result.get("answer_source"),
                            answer=result.get("answer", ""), basis=[d.get("id") for d in result.get("basis_docs", [])],
                            escalation_id=result.get("escalation_id"))
    return Response({"ok": True})


# ---------- обращения ----------

@api_view(["GET"])
def requests_list(request):
    scope, quick = request.GET.get("scope", "inbox"), request.GET.get("quick", "Все")
    needle = (request.GET.get("q") or "").strip().lower()
    now = timezone.now()
    items = Request.objects.all()
    by_scope = {"ready": READY, "escalated": ESCALATED, "sent": SENT}
    if scope in by_scope:
        items = items.filter(status=by_scope[scope])
    elif scope.startswith("proc:"):
        items = items.filter(process=scope[5:])
    if quick == "Новые":
        items = items.filter(status=NEW)
    elif quick == "В работе":
        items = items.filter(status__in=[WORK, READY, ESCALATED])
    elif quick == "30 дней":
        items = items.filter(created__gte=now - timedelta(days=30))
    mine = svc.my_marks(request.user) if quick == "Мои" else None
    if mine is not None:  # «Мои»: обращения, переданные вошедшему (по e-mail или ФИО из справочника)
        items = [i for i in items if svc.is_mine((i.result or {}).get("assignee", ""), mine)]
    result = []
    for item in items:
        if quick == "Просроченные" and not svc.is_overdue(item, now):
            continue
        if needle and needle not in f"{item.subject} {item.sender} {item.body}".lower():
            continue
        result.append(svc.request_dict(item, now))
    return Response({"items": result})


@api_view(["GET", "PATCH", "DELETE"])
def request_detail(request, pk):
    item = Request.objects.filter(pk=pk).first()
    if item is None:
        raise Http404
    if request.method == "DELETE":
        # Удаление записи из «Журнала вопросов» (любой вошедший пользователь; кто удалил — в «Журнале» действий).
        # Письмо из папки «входящие» запоминается, чтобы «Обновить» не загрузило его снова
        svc.forget_request(item)
        svc.audit(request.user, "запись удалена из журнала вопросов",
                  f"{item.id}: {(item.subject or item.body)[:120]} ({item.status})")
        return Response({"ok": True})
    if request.method == "PATCH":
        if "read" in request.data:
            item.read = bool(request.data["read"])
            item.save(update_fields=["read"])
        if "assignee" in request.data:
            # «Передать другому»: вопрос не по теме специалиста не застревает. Если указана тема — вопрос становится
            # её примером, и в следующий раз похожий вопрос сразу уйдёт нужному эксперту
            assignee = str(request.data.get("assignee") or "").strip()
            if not assignee:
                return bad("Укажите, кому передать вопрос")
            who = svc.profile(request.user).name or request.user.username
            topic_name = str(request.data.get("topic") or "").strip()
            reason = f"передал(а) {who}" + (f", тема «{topic_name}»" if topic_name else "")
            svc.set_assignee(item, assignee, reason)
            if topic_name:
                svc.add_topic_example(topic_name, item.body)
            svc.audit(request.user, "обращение передано другому", f"{item.id} → {assignee}"
                      + (f" (тема «{topic_name}»)" if topic_name else ""))
        if "draft" in request.data:
            # Специалист поправил черновик ответа и сохранил, не отправляя: правка не теряется при выходе
            if item.status == SENT:
                return bad("Ответ уже отправлен — черновик менять нельзя")
            if not item.result:
                return bad("Нет подготовленного ответа")
            draft = str(request.data["draft"])
            if not draft.strip():
                return bad("Черновик пустой")
            item.result = {**item.result, "email_draft": draft, "draft_saved_by": svc.profile(request.user).name
                           or request.user.username, "draft_saved_at": svc.local(timezone.now())}
            if item.status == NEW:
                item.status = WORK
            item.save(update_fields=["result", "status"])
            svc.audit(request.user, "черновик ответа сохранён", item.id)
    data = svc.request_dict(item, full=True)
    if item.result:
        data.update(svc.describe(item.result))
    return Response(data)


@api_view(["POST"])
@permission_classes([IsAdmin])
def request_to_knowledge(request, pk):
    """Пополнение базы знаний: ответ специалиста (службы качества) на обращение становится документом базы —
    на такой же вопрос система дальше отвечает сама."""
    item = Request.objects.filter(pk=pk).first()
    if item is None:
        raise Http404
    question = (request.data.get("question") or item.body or "").strip()
    answer_text = (request.data.get("answer") or "").strip()
    if not question or not answer_text:
        return bad("Нужны и вопрос, и ответ")
    kb = get_knowledge_base()
    related = kb.get_doc(request.data.get("related") or "") if request.data.get("related") else None
    if request.data.get("related") and related is None:
        return bad("Документ, к которому относится ответ, не найден")
    from doc_loader import departments_list
    departments = departments_list(request.data.get("departments"))
    unknown = [d for d in departments if d not in svc.department_names()]
    if unknown:
        return bad("Нет в справочнике «Сотрудники»: " + ", ".join(unknown))
    author = svc.profile(request.user).name or request.user.username
    path = legacy_admin.add_quality_answer(question, answer_text, KNOWLEDGE_DIR, item.id, author=author,
                                           related=related, departments=departments)
    svc.audit(request.user, "база знаний пополнена ответом", f"{item.id}: {question[:120]}")
    doc = next((d for d in get_knowledge_base().documents if Path(d["path"]) == path), None)
    return Response({"ok": True, "doc_id": doc["id"] if doc else None, "file": path.name})


@api_view(["POST"])
def request_send(request, pk):
    item = Request.objects.filter(pk=pk).first()
    if item is None:
        raise Http404
    if item.status == SENT:
        return bad("Ответ уже отправлен")
    if not item.result or item.result.get("blocked"):
        return bad("Нет подготовленного ответа")
    recipient = (request.data.get("recipient") or item.sender or "").strip()
    draft = (request.data.get("draft") or "").strip()
    if not extract_email(recipient):
        return bad("Нужен e-mail адресата, например: ivanov@company.ru", status.HTTP_422_UNPROCESSABLE_ENTITY)
    if not draft:
        return bad("Черновик пустой")
    mode, destination = send_reply(recipient, item.subject, draft,
                                   source_ids=[d["id"] for d in item.result.get("active_docs", []) if "id" in d])
    item.sender, item.status, item.sent_at = recipient, SENT, timezone.now()
    item.save()
    return Response({"mode": mode, "destination": str(destination), "request": svc.request_dict(item, full=True)})


@api_view(["POST"])
def requests_refresh(request):
    return Response({"added": svc.import_folder(INBOX_DIR)})


@api_view(["POST"])
def requests_upload(request):
    added = 0
    for upload in request.FILES.getlist("files"):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / Path(upload.name).name
            path.write_bytes(upload.read())
            try:
                added += 1 if svc.import_eml(path, source="загружено вручную", force=True) else 0
            except Exception as exc:
                return bad(f"Не удалось прочитать письмо {upload.name}: {type(exc).__name__}")
    return Response({"added": added})


@api_view(["POST", "GET"])
def requests_process(request):
    if request.method == "POST":
        if not svc.start_batch(request.data.get("sources")):
            return bad("Обработка уже идёт", status.HTTP_409_CONFLICT)
    return Response(svc.batch_status())


# ---------- документы базы, «Требует внимания», история, сводка ----------

@api_view(["GET"])
def documents(request):
    kb = get_knowledge_base()
    return Response({"items": svc.documents(), "search_mode": kb.search_mode, "vectors": rebuild_state()})


@api_view(["GET"])
def document_detail(request, doc_id):
    data = svc.document_detail(doc_id)
    if data is None:
        raise Http404
    return Response(data)


@api_view(["GET"])
def document_file(request, doc_id):
    doc = get_knowledge_base().get_doc(doc_id)
    if not doc or not doc.get("path") or not Path(doc["path"]).is_file():
        raise Http404
    return FileResponse(open(doc["path"], "rb"), as_attachment=True, filename=Path(doc["path"]).name)


@api_view(["POST"])
@permission_classes([IsAdmin])
def document_upload(request):
    upload = request.FILES.get("file")
    if upload is None:
        return bad("Выберите файл")
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / Path(upload.name).name
        source.write_bytes(upload.read())
        try:
            target = legacy_admin.add_document_file(source, KNOWLEDGE_DIR, overwrite=request.data.get("overwrite") == "1")
        except FileExistsError:
            return bad("Такой файл уже есть в базе", status.HTTP_409_CONFLICT)
        except ValueError as exc:
            return bad(str(exc))
    # Дата размещения — сегодня; дата документа, если система не нашла её в тексте, — тоже сегодня (можно поправить)
    from doc_loader import load_document
    today = timezone.localdate().strftime("%d.%m.%Y")
    loaded = load_document(target)
    legacy_admin.write_card(target, {"placed": today, **({} if loaded and loaded.get("date") else {"date": today})})
    svc.audit(request.user, "добавлен документ", target.name)
    doc = next((d for d in get_knowledge_base().documents if Path(d["path"]) == target), None)
    if doc is None:
        return bad("Файл скопирован, но прочитать его не удалось — подробности в журнале data/agent.log")
    return Response(svc.document_detail(doc["id"]))


@api_view(["GET", "POST"])
@permission_classes([IsAdmin])
def topics(request):
    """«Темы и ответственные»: темы вопросов (с готовыми ответами и без), эксперты, слова, примеры, документы."""
    from process_map import load_process_map, save_process_map
    entries = load_process_map()
    if request.method == "POST":
        name = str(request.data.get("name") or "").strip()
        if not name:
            return bad("Укажите название темы")
        if any(e.get("process") == name for e in entries):
            return bad("Такая тема уже есть", status.HTTP_409_CONFLICT)
        n = 1
        while any(e["id"] == f"topic-{n}" for e in entries):
            n += 1
        entry = {"id": f"topic-{n}", "process": name, "stage": "", "match": {"all": []}}
        errors = svc.topic_update(entry, request.data)
        if errors:
            return bad("; ".join(errors))
        entries.append(entry)
        save_process_map(entries)
        svc.audit(request.user, "тема добавлена", name)
        return Response(svc.topic_view(entry))
    docs = [{"id": d["id"], "title": d["title"], "type": d["source_type"]} for d in get_knowledge_base().documents]
    return Response({"topics": [svc.topic_view(e) for e in entries], "documents": docs})


@api_view(["PUT", "DELETE"])
@permission_classes([IsAdmin])
def topic_detail(request, topic_id):
    from process_map import load_process_map, save_process_map
    entries = load_process_map()
    entry = next((e for e in entries if e["id"] == topic_id), None)
    if entry is None:
        raise Http404
    if request.method == "DELETE":
        entries.remove(entry)
        save_process_map(entries)
        svc.audit(request.user, "тема удалена", entry.get("process", topic_id))
        get_knowledge_base()
        return Response({"ok": True})
    errors = svc.topic_update(entry, request.data)
    if errors:
        return bad("; ".join(errors))
    save_process_map(entries)
    svc.audit(request.user, "тема изменена", entry.get("process", topic_id))
    return Response(svc.topic_view(entry))


@api_view(["PUT"])
@permission_classes([IsAdmin])
def document_text(request, doc_id):
    """Администратор правит текст документа базы знаний. Прежняя версия — в data/document_versions."""
    from config import DATA_DIR
    doc = get_knowledge_base().get_doc(doc_id)
    if not doc or not doc.get("path") or not Path(doc["path"]).is_file():
        raise Http404
    text = str(request.data.get("text") or "")
    if not text.strip():
        return bad("Текст пустой. Чтобы убрать документ, используйте «Удалить»")
    old = Path(doc["path"])
    keep = {"id": doc["id"], "type": doc["source_type"], "title": doc["title"], "date": doc["date"], "owner": doc["owner"]}
    try:
        target = legacy_admin.replace_document_text(old, text, DATA_DIR / "document_versions", keep=keep)
    except FileExistsError as exc:
        return bad(f"В базе уже есть файл {exc}", status.HTTP_409_CONFLICT)
    svc.audit(request.user, "текст документа изменён",
              f"«{doc['title']}» ({old.name}{' → ' + target.name if target != old else ''})")
    data = svc.document_detail(doc["id"])  # база перечитывается: файл изменился
    return Response(data or {"id": doc["id"]})


@api_view(["DELETE"])
@permission_classes([IsAdmin])
def document_delete(request, doc_id):
    """Убрать документ из базы знаний: файл и карточка — в «Удалённые документы» (можно вернуть)."""
    from config import DELETED_DOCS_DIR
    doc = get_knowledge_base().get_doc(doc_id)
    if not doc or not doc.get("path") or not Path(doc["path"]).is_file():
        raise Http404
    entry = legacy_admin.delete_document(Path(doc["path"]), DELETED_DOCS_DIR)
    svc.audit(request.user, "документ удалён из базы", f"«{doc['title']}» ({Path(doc['path']).name}) → {entry.name}")
    get_knowledge_base()
    return Response({"ok": True, "deleted": entry.name})


@api_view(["GET"])
@permission_classes([IsAdmin])
def documents_deleted(request):
    from config import DELETED_DOCS_DIR
    return Response({"items": legacy_admin.list_deleted(DELETED_DOCS_DIR) if DELETED_DOCS_DIR.exists() else []})


@api_view(["DELETE"])
@permission_classes([IsAdmin])
def document_purge(request, entry):
    """«Полностью удалить из системы»: документ из «Удалённых» стирается с диска, вернуть нельзя."""
    from config import DELETED_DOCS_DIR
    folder = DELETED_DOCS_DIR / Path(entry).name  # только имя папки — без путей
    if not Path(entry).name or not folder.is_dir():
        raise Http404
    name = legacy_admin.purge_deleted(folder, DELETED_DOCS_DIR)
    svc.audit(request.user, "документ полностью удалён из системы", f"{name} ({folder.name})")
    return Response({"ok": True, "file": name})


@api_view(["POST"])
@permission_classes([IsAdmin])
def document_restore(request, entry):
    from config import DELETED_DOCS_DIR
    folder = DELETED_DOCS_DIR / Path(entry).name  # только имя папки — без путей
    if not Path(entry).name or not folder.is_dir():
        raise Http404
    try:
        target = legacy_admin.restore_document(folder, KNOWLEDGE_DIR)
    except FileExistsError:
        return bad("В базе уже есть файл с таким именем — сначала удалите или переименуйте его", status.HTTP_409_CONFLICT)
    svc.audit(request.user, "документ возвращён в базу", target.name)
    get_knowledge_base()
    return Response({"ok": True, "file": target.name})


@api_view(["PUT"])
@permission_classes([IsAdmin])
def document_card(request, doc_id):
    doc = get_knowledge_base().get_doc(doc_id)
    if not doc or not doc.get("path"):
        raise Http404
    detected = {"type": doc.get("source_type", ""), "number": doc.get("number", ""), "title": doc.get("title", ""),
                "date": doc.get("date", ""), "owner": doc.get("owner", ""),
                "effective_from": doc.get("effective_from") or "", "review_date": doc.get("review_date") or ""}
    changed = {k: str(v).strip() for k, v in request.data.items() if k in detected and str(v).strip() != detected[k]}
    if "placed" in request.data and str(request.data["placed"]).strip() != (doc.get("placed") or ""):
        changed["placed"] = str(request.data["placed"]).strip()
    if "edition" in request.data:  # «Статус»: актуальная редакция (статус — по графу версий) или утратил силу
        outdated = request.data["edition"] == svc.EDITION_OUTDATED
        if outdated != (doc.get("declared_status") == "Устарел"):
            changed["status"] = "Устарел" if outdated else ""
    if "departments" in request.data:  # «Для кого»: список подразделений; пустой — для всех
        from doc_loader import departments_list
        wanted = departments_list(request.data.get("departments"))
        unknown = [d for d in wanted if d not in svc.department_names()]
        if unknown:
            return bad("Нет в справочнике «Сотрудники»: " + ", ".join(unknown))
        if wanted != list(doc.get("departments") or []):
            changed["departments"] = wanted
    legacy_admin.write_card(Path(doc["path"]), changed)
    detail = ", ".join(f"для: {', '.join(v) or 'всех'}" if k == "departments" else k for k, v in changed.items())
    svc.audit(request.user, "карточка документа", f"{Path(doc['path']).name}: {detail or 'без изменений'}")
    get_knowledge_base()
    return Response({"ok": True, "changed": list(changed)})


@api_view(["GET"])
def attention(request):
    return Response({"items": svc.attention()})


@api_view(["GET"])
def history(request):
    items = HistoryRecord.objects.all()[:200]
    def row(h):
        result = svc.shown(h.result)
        hidden = bool(result and result.get("withheld"))
        return {"id": h.id, "time": svc.local(h.created), "sender": h.sender, "subject": h.subject, "body": h.body,
                "answer_source": "escalation" if hidden else h.answer_source,
                "answer": (result.get("answer") or "")[:300] if hidden else h.answer,
                "dialog": h.dialog, "step": h.step,
                "understood_as": h.understood_as if h.understood_as and h.understood_as != h.body else ""}
    return Response({"items": [row(h) for h in items]})


@api_view(["GET"])
def history_detail(request, pk):
    item = HistoryRecord.objects.filter(pk=pk).first()
    if item is None:
        raise Http404
    chain = [{"id": h.id, "step": h.step, "body": h.body, "understood_as": h.understood_as if h.understood_as != h.body else ""}
             for h in HistoryRecord.objects.filter(dialog=item.dialog).order_by("created")] if item.dialog else []
    return Response({"id": item.id, "sender": item.sender, "subject": item.subject, "body": item.body,
                     "understood_as": item.understood_as if item.understood_as != item.body else "", "chain": chain,
                     "result": svc.shown(item.result), **(svc.describe(item.result) if item.result else {})})


@api_view(["GET"])
def summary(request):
    return Response(svc.summary(request.GET.get("period", "Месяц")))


# ---------- справочник «Сотрудники» ----------

@api_view(["GET", "POST"])
def employees(request):
    if request.method == "POST":
        if not svc.is_admin(request.user):
            return bad("Справочник изменяет администратор", status.HTTP_403_FORBIDDEN)
        return _save_employee(request, None)
    return Response({"departments": [{"name": d.name, "employees": [
        {"id": e.id, "fio": e.fio, "position": e.position, "email": e.email, "phone": e.phone} for e in d.employees.all()]}
        for d in Department.objects.prefetch_related("employees")], "editable": svc.is_admin(request.user)})


@api_view(["PUT", "DELETE"])
@permission_classes([IsAdmin])
def employee_detail(request, pk):
    employee = Employee.objects.filter(pk=pk).first()
    if employee is None:
        raise Http404
    if request.method == "DELETE":
        department = employee.department
        svc.audit(request.user, "сотрудник удалён", employee.fio)
        employee.delete()
        if not department.employees.exists():
            department.delete()
        svc.export_employees()
        return Response({"ok": True})
    return _save_employee(request, employee)


def _save_employee(request, employee):
    card = {k: (request.data.get(k) or "").strip() for k in ("department", "fio", "position", "email", "phone")}
    errors = legacy_employees.validate(card)
    if errors:
        return bad("Проверьте: " + "; ".join(errors))
    with transaction.atomic():
        department, _ = Department.objects.get_or_create(
            name=card["department"], defaults={"order": Department.objects.count()})
        old_department = employee.department if employee else None
        employee = employee or Employee()
        employee.department = department
        for key in ("fio", "position", "email", "phone"):
            setattr(employee, key, card[key])
        employee.save()
        if old_department and old_department != department and not old_department.employees.exists():
            old_department.delete()
    svc.export_employees()
    svc.audit(request.user, "сотрудник изменён" if old_department else "сотрудник добавлен", employee.fio)
    return Response({"id": employee.id})


# ---------- администрирование ----------

def _create_user(login_name: str, name: str, role: str, password: str) -> list[str]:
    login_name = login_name.strip().lower()
    errors = []
    import re
    if not re.fullmatch(r"[a-z0-9._-]{3,32}", login_name):
        errors.append("логин — 3–32 латинские буквы, цифры, точка, дефис или подчёркивание")
    elif User.objects.filter(username=login_name).exists():
        errors.append("такой логин уже есть")
    if role not in dict(ROLES):
        errors.append("неизвестная роль")
    if len(name.split()) < 2:
        errors.append("ФИО — минимум фамилия и имя")
    errors += password_problems(password, login_name)
    if errors:
        return errors
    with transaction.atomic():
        user = User.objects.create_user(username=login_name, password=password)
        Profile.objects.create(user=user, name=name.strip(), role=role)
    return []


def _admins_left(excluding: User | None = None) -> int:
    qs = Profile.objects.filter(role=ADMIN, user__is_active=True)
    if excluding:
        qs = qs.exclude(user=excluding)
    return qs.count()


@api_view(["GET", "POST"])
@permission_classes([IsAdmin])
def users(request):
    if request.method == "POST":
        errors = _create_user(request.data.get("login", ""), request.data.get("name", ""), request.data.get("role", USER),
                              request.data.get("password", ""))
        if errors:
            return bad("Проверьте: " + "; ".join(errors))
        svc.audit(request.user, "создана учётная запись", f"{request.data['login'].strip().lower()} ({request.data.get('role')})")
        return Response({"ok": True})
    items = User.objects.select_related("profile").order_by("profile__role", "username")  # «Администратор» — первыми
    return Response({"items": [svc.user_dict(u) for u in items], "roles": [r for r, _ in ROLES]})


@api_view(["PATCH", "DELETE"])
@permission_classes([IsAdmin])
def user_detail(request, login_name):
    user = User.objects.filter(username=login_name.lower()).first()
    if user is None:
        raise Http404
    prof = svc.profile(user)
    if request.method == "DELETE":
        if user == request.user:
            return bad("Нельзя удалить свою учётную запись")
        if prof.role == ADMIN and _admins_left(excluding=user) == 0:
            return bad("Нельзя удалить последнего администратора")
        user.delete()
        svc.audit(request.user, "удалена учётная запись", login_name)
        return Response({"ok": True})
    role = request.data.get("role", prof.role)
    active = request.data.get("active", user.is_active)
    if role not in dict(ROLES):
        return bad("Неизвестная роль")
    if prof.role == ADMIN and (role != ADMIN or not active) and _admins_left(excluding=user) == 0:
        return bad("Нельзя оставить систему без администратора")
    name = request.data.get("name", prof.name)
    if len(str(name).split()) < 2:
        return bad("ФИО — минимум фамилия и имя")
    prof.name, prof.role = str(name).strip(), role
    prof.save()
    user.is_active = bool(active)
    user.save()
    svc.audit(request.user, "изменена учётная запись",
              f"{user.username}: роль {role}, {'активна' if user.is_active else 'заблокирована'}")
    return Response(svc.user_dict(user))


@api_view(["POST"])
@permission_classes([IsAdmin])
def user_password(request, login_name):
    user = User.objects.filter(username=login_name.lower()).first()
    if user is None:
        raise Http404
    if user == request.user:
        return bad("Свой пароль меняйте через «Сменить свой пароль» (нужен текущий)")
    password = request.data.get("password", "")
    try:
        validate_password(password, user)
    except ValidationError as exc:
        return bad("Проверьте: " + "; ".join(exc.messages))
    user.set_password(password)
    user.save()
    svc.audit(request.user, "сменён пароль", user.username)
    return Response({"ok": True})


@api_view(["GET"])
@permission_classes([IsAdmin])
def new_password(request):
    return Response({"password": generate_password()})


@api_view(["GET", "PUT", "DELETE"])
@permission_classes([IsAdmin])
def confidence_settings(request):
    """Критерии плашки уверенности: GET — значения и пояснения, PUT — сохранить, DELETE — вернуть рекомендуемые."""
    import answer_confidence
    if request.method == "PUT":
        values, errors = answer_confidence.validate(request.data or {})
        if errors:
            return bad("Проверьте: " + "; ".join(errors))
        Setting.objects.update_or_create(key=svc.CONFIDENCE_KEY, defaults={"value": values,
                                                                            "updated_by": request.user.username})
        changed = [answer_confidence.PARAMS[k][0] for k, v in values.items() if v != answer_confidence.DEFAULTS[k]]
        svc.audit(request.user, "критерии уверенности ответа", ", ".join(changed) or "рекомендуемые значения")
    elif request.method == "DELETE":
        Setting.objects.filter(key=svc.CONFIDENCE_KEY).delete()
        svc.audit(request.user, "критерии уверенности ответа", "возвращены рекомендуемые значения")
    current = svc.confidence_params()
    saved = Setting.objects.filter(key=svc.CONFIDENCE_KEY).first()
    return Response({"items": [{"key": key, "label": label, "help": help_text, "recommended": recommended, "min": low,
                                "max": high, "step": step, "value": current[key]}
                               for key, (label, help_text, recommended, low, high, step) in answer_confidence.PARAMS.items()],
                     "updated": svc.local(saved.updated) if saved else None,
                     "updated_by": saved.updated_by if saved else None})


@api_view(["GET"])
@permission_classes([IsAdmin])
def audit_log(request):
    return Response({"items": [{"time": svc.local(a.created), "user": a.user, "action": a.action, "detail": a.detail}
                               for a in AuditRecord.objects.all()[:300]]})


@api_view(["GET"])
@permission_classes([IsAdmin])
def system_check(request):
    return Response({"items": legacy_admin.system_check(), "version": legacy_admin.read_version()})


# ---------- обновление системы администратором ----------

UPDATES_DIR = Path(tempfile.gettempdir()) / "konsultant_updates"  # загруженные пакеты — до установки


def _server_port(request) -> int:
    try:
        return int(request.META.get("SERVER_PORT") or 8000)
    except ValueError:
        return 8000


def _backup_id(path: str) -> str:
    return Path(path).name


@api_view(["GET"])
@permission_classes([IsAdmin])
def update_state(request):
    """«Версия»: установленная версия, резервные копии (для отката), последние обновления из журнала."""
    history = [{"time": svc.local(a.created), "user": a.user, "action": a.action, "detail": a.detail}
               for a in AuditRecord.objects.filter(action__in=["обновление системы", "откат версии"]).order_by("-id")[:5]]
    return Response({"version": legacy_admin.read_version(),
                     "backups": [{**b, "id": _backup_id(b["path"]), "created": b["created"].replace("T", " ")[:16]}
                                 for b in legacy_admin.list_backups()[:10]],
                     "history": history})


@api_view(["POST"])
@permission_classes([IsAdmin])
def update_inspect(request):
    """Шаг 1: загрузить пакет и показать, что изменится (ничего не меняя)."""
    upload = request.FILES.get("file")
    if upload is None:
        return bad("Выберите файл пакета обновления (.zip)")
    UPDATES_DIR.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    package = UPDATES_DIR / f"{token}.zip"
    with open(package, "wb") as file:
        for chunk in upload.chunks():
            file.write(chunk)
    try:
        info = legacy_admin.inspect_update(package)
    except legacy_admin.UpdateError as exc:
        package.unlink(missing_ok=True)
        return bad(str(exc))
    newer = legacy_admin.version_tuple(info["to_version"]) > legacy_admin.version_tuple(info["from_version"])
    return Response({**info, "token": token, "newer": newer, "file": upload.name})


@api_view(["POST"])
@permission_classes([IsAdmin])
def update_apply(request):
    """Шаг 2: резервная копия → замена файлов программы → перезапуск сервера."""
    token = re.sub(r"[^0-9a-f]", "", str(request.data.get("token") or ""))
    package = UPDATES_DIR / f"{token}.zip"
    if not token or not package.is_file():
        return bad("Пакет не найден — выберите его ещё раз")
    try:
        changed = legacy_admin.inspect_update(package)
        result = legacy_admin.apply_update(package, allow_same_or_older=bool(request.data.get("allow_older")))
    except legacy_admin.UpdateError as exc:
        return bad(str(exc))
    finally:
        package.unlink(missing_ok=True)
    count = len(changed["changed"]) + len(changed["added"])
    svc.audit(request.user, "обновление системы",
              f"{result['from_version']} → {result['to_version']}, изменено файлов программы: {count}, "
              f"резервная копия: {_backup_id(result['backup'])}")
    svc.schedule_restart(_server_port(request))
    return Response({"from_version": result["from_version"], "to_version": result["to_version"],
                     "replaced": len(result["replaced"]), "backup": _backup_id(result["backup"]), "restarting": True})


@api_view(["POST"])
@permission_classes([IsAdmin])
def update_rollback(request):
    """Вернуть версию из резервной копии (текущая тоже сохраняется — откат можно отменить)."""
    from config import BACKUPS_DIR
    backup_id = Path(str(request.data.get("backup") or "")).name  # только имя папки — без путей
    backup = BACKUPS_DIR / backup_id
    if not backup_id or not backup.is_dir():
        return bad("Резервная копия не найдена")
    try:
        result = legacy_admin.rollback(backup)
    except legacy_admin.UpdateError as exc:
        return bad(str(exc))
    svc.audit(request.user, "откат версии", f"возвращена версия {result['version']} из {backup_id}")
    svc.schedule_restart(_server_port(request))
    return Response({"version": result["version"], "restarting": True})


@api_view(["POST"])
@permission_classes([IsAdmin])
def server_restart(request):
    svc.audit(request.user, "перезапуск сервера")
    svc.schedule_restart(_server_port(request))
    return Response({"restarting": True})


@api_view(["POST"])
@permission_classes([IsAdmin])
def transfer_archive(request):
    """Архив для переноса: «Проект» (программа, данные, база SQLite) + «Документы» (Word)."""
    accounts = [svc.user_dict(u) for u in User.objects.select_related("profile")]
    out = Path(tempfile.mkdtemp(prefix="perenos_"))
    path = legacy_admin.build_transfer_archive(out, accounts)
    svc.audit(request.user, "архив для переноса", path.name)
    return FileResponse(open(path, "rb"), as_attachment=True, filename=path.name)

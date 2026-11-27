"""Тесты API веб-версии (без Ollama: вопросы — по карте ответственности и графу версий, либо модель подменяется).
python manage.py test core --settings=konsultant.test_settings"""
import hashlib
import io
import json
import os
import zipfile
from io import BytesIO
from types import SimpleNamespace
from unittest import mock

from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

import agent
from inbox import ESCALATED
from core import services as svc
from core.hashers import from_legacy
from core.models import ADMIN, USER, AuditRecord, Department, Employee, Profile, Request, SelfServiceQuestion

RULE_Q = "Кто подписывает агентский договор на 300 000 руб.?"


def make_user(login, role=USER, password="Parol2026x", name="Тестов Тест"):
    user = User.objects.create_user(username=login, password=password)
    Profile.objects.create(user=user, name=name, role=role)
    return user


def no_llm():
    """Модель вызываться не должна (ответ по правилу, графу версий или эскалация)."""
    return mock.patch.object(agent, "_get_client", side_effect=AssertionError("модель не должна вызываться"))


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = make_user("admin", ADMIN, name="Админов Пётр")
        self.user = make_user("ivanov", USER, name="Иванов Иван")
        self.client = APIClient()

    def as_admin(self):
        self.client.force_authenticate(self.admin)

    def as_user(self):
        self.client.force_authenticate(self.user)


class AuthTests(Base):
    def test_login_logout_and_lockout(self):
        client = APIClient()
        self.assertIsNone(client.get("/api/auth/me").json()["user"])
        self.assertEqual(client.get("/api/requests").status_code, 403)  # без входа — нельзя
        response = client.post("/api/auth/login", {"login": "IVANOV", "password": "Parol2026x"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"]["role"], USER)
        self.assertEqual(client.get("/api/auth/me").json()["user"]["login"], "ivanov")
        client.post("/api/auth/logout")
        for _ in range(5):
            response = client.post("/api/auth/login", {"login": "ivanov", "password": "wrong"}, format="json")
        self.assertEqual(response.status_code, 429)  # 5 неудачных попыток — пауза
        response = client.post("/api/auth/login", {"login": "ivanov", "password": "Parol2026x"}, format="json")
        self.assertEqual(response.status_code, 429)
        self.assertTrue(AuditRecord.objects.filter(action="неудачный вход").exists())

    def test_first_run_setup(self):
        User.objects.all().delete()
        client = APIClient()
        self.assertTrue(client.get("/api/auth/setup").json()["needs_admin"])
        bad = client.post("/api/auth/setup/admin", {"login": "boss", "name": "Начальников Иван", "password": "123"}, format="json")
        self.assertEqual(bad.status_code, 400)
        ok = client.post("/api/auth/setup/admin", {"login": "boss", "name": "Начальников Иван", "password": "Boss2026x"},
                         format="json")
        self.assertEqual(ok.json()["user"]["role"], ADMIN)
        again = APIClient().post("/api/auth/setup/admin", {"login": "x2", "name": "А Б", "password": "Boss2026x"}, format="json")
        self.assertEqual(again.status_code, 403)

    def test_legacy_password_rehashed(self):
        salt = os.urandom(16)
        digest = hashlib.pbkdf2_hmac("sha256", b"Legacy2026x", salt, 200_000).hex()
        user = User.objects.create(username="old", password=from_legacy(salt.hex(), digest))
        Profile.objects.create(user=user, name="Старый Пользователь", role=USER)
        response = APIClient().post("/api/auth/login", {"login": "old", "password": "Legacy2026x"}, format="json")
        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertFalse(user.password.startswith("legacy_pbkdf2$"))  # перехеширован стандартным хешером

    def test_change_own_password(self):
        self.as_user()
        self.assertEqual(self.client.post("/api/auth/password", {"old": "wrong", "new": "Novyi2026x"}, format="json").status_code, 400)
        self.assertEqual(self.client.post("/api/auth/password", {"old": "Parol2026x", "new": "short"}, format="json").status_code, 400)
        self.assertEqual(self.client.post("/api/auth/password", {"old": "Parol2026x", "new": "Novyi2026x"}, format="json").status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Novyi2026x"))

    def test_version_only_for_admin(self):
        self.as_user()
        self.assertNotIn("version", self.client.get("/api/meta").json())
        self.as_admin()
        self.assertIn("version", self.client.get("/api/meta").json())


class AskTests(Base):
    def test_rule_answer_at_once(self):
        self.as_user()
        with no_llm():
            data = self.client.post("/api/ask", {"question": RULE_Q, "employee": "Иванов <ivanov@company.ru>"}, format="json").json()
        self.assertEqual(data["decision"], "ответ сразу")
        self.assertEqual(data["level"], "high")
        self.assertIn("Смирнов", data["result"]["answer"])
        self.assertIn("smirnov@company.ru", data["contacts"])
        self.assertIsNone(data["request_id"])
        self.assertEqual(self.client.post(f"/api/ask/{data['record_id']}/helped", {"helped": True}, format="json").status_code, 200)
        self.assertTrue(SelfServiceQuestion.objects.get(pk=data["record_id"]).helped)

    def test_no_answer_goes_to_specialist(self):
        self.as_user()
        with no_llm():
            data = self.client.post("/api/ask", {"question": "Как оформить отпуск?"}, format="json").json()
        self.assertEqual(data["decision"], "передано специалисту")
        request = Request.objects.get(id=data["request_id"])
        self.assertEqual((request.status, request.source), ("Передано специалисту", "сотрудник спросил сам"))

    def test_model_unavailable_is_clear_message(self):
        self.as_user()
        with mock.patch("core.services.run_query", side_effect=agent.LLMUnavailableError("нет соединения")):
            response = self.client.post("/api/ask", {"question": "Где шаблон договора?"}, format="json")
            self.assertEqual(response.status_code, 503)
            self.assertIn("Ollama", response.json()["error"])
            answered = self.client.post("/api/answer", {"body": "Где шаблон договора?"}, format="json")
        self.assertEqual(answered.status_code, 503)
        self.assertEqual(Request.objects.latest("created").status, "Новое")  # обращение не «зависло» в работе

    def test_handoff_and_sources(self):
        self.as_user()
        with no_llm():
            data = self.client.post("/api/ask", {"question": "Регламент согласования договоров 2024 года ещё действует?"},
                                    format="json").json()
        handoff = self.client.post(f"/api/ask/{data['record_id']}/handoff", {"comment": "нужны подробности"}, format="json").json()
        request = Request.objects.get(id=handoff["request_id"])
        self.assertEqual(request.status, "Передано специалисту")
        self.assertIn("нужны подробности", request.body)
        self.assertEqual(self.client.post("/api/ask", {"question": RULE_Q, "sources": []}, format="json").status_code, 400)


class RequestsTests(Base):
    def test_inbox_answer_and_send(self):
        self.as_user()
        self.assertEqual(self.client.post("/api/requests/refresh").json()["added"], 8)
        self.assertEqual(self.client.post("/api/requests/refresh").json()["added"], 0)  # каждое письмо — один раз
        items = self.client.get("/api/requests").json()["items"]
        big = next(i for i in items if i["subject"] == "Крупная сделка")
        self.assertTrue(big["overdue"])  # письмо от 22.11, срок 24 ч
        detail = self.client.patch(f"/api/requests/{big['id']}", {"read": True}, format="json").json()
        with no_llm():
            answered = self.client.post("/api/answer", {"request_id": big["id"], "body": detail["body"]}, format="json").json()
        self.assertEqual(answered["request"]["status"], "Ответ готов")
        self.assertIn("генеральный директор", answered["result"]["answer"])
        send = self.client.post(f"/api/requests/{big['id']}/send", {"draft": answered["result"]["email_draft"]}, format="json")
        self.assertEqual(send.json()["mode"], "demo")  # SMTP не задан — письмо сохранено в outbox
        self.assertEqual(Request.objects.get(id=big["id"]).status, "Отправлено")
        sent = self.client.get("/api/requests", {"scope": "sent"}).json()["items"]
        self.assertEqual([i["id"] for i in sent], [big["id"]])
        found = self.client.get("/api/requests", {"q": "командировк"}).json()["items"]
        self.assertEqual([i["subject"] for i in found], ["Командировка"])
        process = self.client.get("/api/requests", {"scope": "proc:Подписание агентского договора"}).json()["items"]
        self.assertTrue(process and all(i["process"] == "Подписание агентского договора" for i in process))

    def test_specialist_saves_edited_draft(self):
        """«Передано специалисту»: специалист правит черновик и сохраняет, не отправляя."""
        self.as_user()
        with no_llm():
            data = self.client.post("/api/ask", {"question": "Как оформить отпуск?"}, format="json").json()
        request_id = data["request_id"]
        edited = "Здравствуйте!\n\nОтпуск оформляется заявлением в кадровой системе за 2 недели.\n\nС уважением,\nСлужба качества"
        saved = self.client.patch(f"/api/requests/{request_id}", {"draft": edited}, format="json")
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["result"]["email_draft"], edited)
        self.assertEqual(saved.json()["result"]["draft_saved_by"], "Иванов Иван")
        again = self.client.get(f"/api/requests/{request_id}").json()  # открыли обращение снова — правка на месте
        self.assertEqual(again["result"]["email_draft"], edited)
        self.assertEqual(again["status"], "Передано специалисту")  # статус не меняется, пока ответ не отправлен
        self.assertTrue(AuditRecord.objects.filter(action="черновик ответа сохранён").exists())
        self.assertEqual(self.client.patch(f"/api/requests/{request_id}", {"draft": "  "}, format="json").status_code, 400)
        self.client.post(f"/api/requests/{request_id}/send", {"draft": edited, "recipient": "a@b.ru"}, format="json")
        self.assertEqual(self.client.patch(f"/api/requests/{request_id}", {"draft": "x"}, format="json").status_code, 400)

    def test_delete_request_any_user_and_not_reimported(self):
        self.as_user()  # удалять записи может любой вошедший пользователь
        self.client.post("/api/requests/refresh")
        item = Request.objects.exclude(source_file=None).first()
        self.assertEqual(self.client.delete(f"/api/requests/{item.id}").status_code, 200)
        self.assertFalse(Request.objects.filter(id=item.id).exists())
        self.assertTrue(AuditRecord.objects.filter(action="запись удалена из журнала вопросов").exists())
        self.assertEqual(self.client.post("/api/requests/refresh").json()["added"], 0)  # письмо не загружается снова
        with no_llm():
            asked = self.client.post("/api/answer", {"body": RULE_Q}, format="json").json()  # «Ответ готов»
        self.assertEqual(self.client.delete(f"/api/requests/{asked['request']['id']}").status_code, 200)
        self.assertEqual(self.client.delete(f"/api/requests/{asked['request']['id']}").status_code, 404)
        self.assertEqual(APIClient().delete(f"/api/requests/{item.id}").status_code, 403)  # без входа — нельзя

    def test_assignee_reassign_mine_and_learning(self):
        """Вопрос без ответа — ответственному; «Передать другому» с темой — вопрос становится примером темы;
        «Мои» — обращения, переданные вошедшему."""
        from process_map import load_process_map
        self.as_user()
        with no_llm():
            data = self.client.post("/api/ask", {"question": "Как оформить отпуск?"}, format="json").json()
        request_id = data["request_id"]
        detail = self.client.get(f"/api/requests/{request_id}").json()
        self.assertTrue(detail["result"]["assignee"])  # тема не определилась — дежурный методолог
        self.assertTrue(detail["result"]["assignee_reason"])
        topic = load_process_map()[0]["process"]
        moved = self.client.patch(f"/api/requests/{request_id}",
                                  {"assignee": "Иванов Иван <ivanov@company.ru>", "topic": topic}, format="json")
        self.assertEqual(moved.status_code, 200)
        self.assertEqual(moved.json()["result"]["assignee"], "Иванов Иван <ivanov@company.ru>")
        self.assertIn("передал(а) Иванов Иван", moved.json()["result"]["assignee_reason"])
        self.assertIn("Как оформить отпуск?", load_process_map()[0]["examples"])  # пример темы
        self.assertTrue(AuditRecord.objects.filter(action="обращение передано другому").exists())
        mine = self.client.get("/api/requests", {"scope": "escalated", "quick": "Мои"}).json()["items"]
        self.assertEqual([i["id"] for i in mine], [request_id])  # «Иванов Иван» — вошедший пользователь
        self.as_admin()
        self.assertEqual(self.client.get("/api/requests", {"scope": "escalated", "quick": "Мои"}).json()["items"], [])

    def test_topics_screen_admin_only_and_answer_checked(self):
        self.as_user()
        self.assertEqual(self.client.get("/api/admin/topics").status_code, 403)
        self.as_admin()
        data = self.client.get("/api/admin/topics").json()
        signing = next(t for t in data["topics"] if t["ready"]["kind"] == "amount")
        edo = next(t for t in data["topics"] if t["ready"]["kind"] == "single")
        self.assertTrue(data["documents"])
        # готовый ответ с фактом, которого нет в документе-основании, не сохраняется
        bad = self.client.put(f"/api/admin/topics/{edo['id']}", {"answer": "Проверяет Сидоров П.И., доб. 9999."}, format="json")
        self.assertEqual(bad.status_code, 400)
        self.assertIn("Сидоров", bad.json()["error"])
        ok = self.client.put(f"/api/admin/topics/{edo['id']}",
                             {"expert": "Волкова Марина Николаевна <volkova@company.ru>", "examples": ["Кто смотрит контрагента в ЭДО?"]},
                             format="json").json()
        self.assertEqual((ok["expert"], ok["examples"]), ("Волкова Марина Николаевна <volkova@company.ru>", ["Кто смотрит контрагента в ЭДО?"]))
        self.assertEqual(ok["ready"]["answer"], edo["ready"]["answer"])  # готовый ответ не тронут
        created = self.client.post("/api/admin/topics", {"name": "Командировки", "keywords": ["командиров"],
                                                         "expert": "Белова Мария <belova@company.ru>"}, format="json").json()
        self.assertEqual((created["ready"]["kind"], created["keywords"]), ("none", ["командиров"]))
        self.assertEqual(self.client.post("/api/admin/topics", {"name": "Командировки"}, format="json").status_code, 409)
        self.assertEqual(self.client.delete(f"/api/admin/topics/{created['id']}").status_code, 200)
        self.assertNotIn(created["id"], [t["id"] for t in self.client.get("/api/admin/topics").json()["topics"]])
        self.assertTrue(signing["ready"]["variants"])

    def test_specialist_sets_sender_of_request(self):
        """«От кого вопрос» на экране обращения: специалист указывает или поправляет — сохраняется в обращении."""
        self.as_user()
        with no_llm():
            first = self.client.post("/api/answer", {"body": RULE_Q}, format="json").json()
            request_id = first["request"]["id"]
            again = self.client.post("/api/answer", {"body": RULE_Q, "request_id": request_id,
                                                     "sender": "Петрова Анна Владимировна <petrova@company.ru>"},
                                     format="json").json()
        self.assertEqual(again["request"]["sender"], "Петрова Анна Владимировна <petrova@company.ru>")
        self.assertEqual(Request.objects.get(id=request_id).sender, "Петрова Анна Владимировна <petrova@company.ru>")

    def test_manual_question_needs_email_to_send(self):
        self.as_user()
        with no_llm():
            answered = self.client.post("/api/answer", {"body": RULE_Q}, format="json").json()
        request_id = answered["request"]["id"]
        response = self.client.post(f"/api/requests/{request_id}/send", {"draft": "Текст"}, format="json")
        self.assertEqual(response.status_code, 422)  # адреса нет — фронтенд спросит
        response = self.client.post(f"/api/requests/{request_id}/send", {"draft": "Текст", "recipient": "a@b.ru"}, format="json")
        self.assertEqual(response.status_code, 200)


class DocumentsAndDirectoryTests(Base):
    def test_documents(self):
        self.as_user()
        docs = self.client.get("/api/documents").json()["items"]
        self.assertEqual({d["status"] for d in docs}, {"Действует", "Устарел", "Не вступил в силу", "Противоречит"})
        detail = self.client.get("/api/documents/ORD-2026-118").json()
        self.assertTrue(any("Смирнов" in c["text"] for c in detail["chunks"]))
        self.assertEqual(self.client.get("/api/documents/ORD-2026-118/file").status_code, 200)
        self.assertEqual(len(self.client.get("/api/attention").json()["items"]), 2)
        upload = SimpleUploadedFile("Новый.txt", "Регламент".encode("utf-8"))
        self.assertEqual(self.client.post("/api/documents/upload", {"file": upload}).status_code, 403)  # пользователю нельзя

    def test_admin_adds_document_with_card(self):
        self.as_admin()
        text = "Приказ №200-П от 01.11.2026\n1. Ответственный за архив — Соколов Д.А."
        upload = SimpleUploadedFile("Приказ_200.txt", text.encode("utf-8"))
        detail = self.client.post("/api/documents/upload", {"file": upload}).json()
        self.assertEqual(detail["type"], "Приказ")
        from django.utils import timezone
        today = timezone.localdate().strftime("%d.%m.%Y")
        self.assertEqual((detail["date"], detail["placed"], detail["edition"]), ("01.11.2026", today, "Актуальная редакция"))
        self.assertIn("Приказ", self.client.get("/api/meta").json()["document_types"])
        response = self.client.put(f"/api/documents/{detail['id']}/card", {"review_date": "01.12.2027"}, format="json")
        self.assertEqual(response.json()["changed"], ["review_date"])
        card = settings.TEST_TMP / "база_знаний" / "Приказ_200.txt.meta.json"
        self.assertEqual(json.loads(card.read_text(encoding="utf-8")), {"placed": today, "review_date": "01.12.2027"})

        # «Статус: Утратил силу» — документ больше не действует; «Актуальная редакция» — снова по графу версий
        self.client.put(f"/api/documents/{detail['id']}/card", {"edition": "Утратил силу"}, format="json")
        docs = {d["id"]: d for d in self.client.get("/api/documents").json()["items"]}
        self.assertEqual(docs[detail["id"]]["status"], "Устарел")
        self.client.put(f"/api/documents/{detail['id']}/card", {"edition": "Актуальная редакция"}, format="json")
        self.assertNotIn("status", json.loads(card.read_text(encoding="utf-8")))

        # Удаление — документ уходит из базы в «Удалённые документы», его можно вернуть
        self.assertEqual(self.client.delete(f"/api/documents/{detail['id']}/delete").status_code, 200)
        self.assertNotIn(detail["id"], [d["id"] for d in self.client.get("/api/documents").json()["items"]])
        self.assertFalse(card.exists())
        deleted = self.client.get("/api/documents/deleted").json()["items"]
        self.assertEqual(deleted[0]["file"], "Приказ_200.txt")
        self.assertEqual(self.client.post("/api/documents/deleted/..%5C..%5Cx/restore").status_code, 404)
        self.assertEqual(self.client.post(f"/api/documents/deleted/{deleted[0]['id']}/restore").status_code, 200)
        self.assertIn(detail["id"], [d["id"] for d in self.client.get("/api/documents").json()["items"]])
        self.assertTrue(card.exists())  # карточка вернулась вместе с файлом
        actions = list(AuditRecord.objects.values_list("action", flat=True))
        self.assertIn("документ удалён из базы", actions)
        self.assertIn("документ возвращён в базу", actions)
        self.as_user()
        self.assertEqual(self.client.delete(f"/api/documents/{detail['id']}/delete").status_code, 403)
        self.as_admin()
        self.client.delete(f"/api/documents/{detail['id']}/delete")  # не оставлять тестовый документ в общей базе тестов

        # Правка текста документа (администратор): прежняя версия сохраняется, номер документа тот же
        restored = next(d for d in self.client.get("/api/documents/deleted").json()["items"] if d["file"] == "Приказ_200.txt")
        self.client.post(f"/api/documents/deleted/{restored['id']}/restore")
        doc_id = detail["id"]
        current = self.client.get(f"/api/documents/{doc_id}").json()
        self.assertIn("Приказ", current["text"])
        new_text = current["text"] + "\n3. Контроль исполнения возложить на секретаря."
        self.as_user()
        self.assertEqual(self.client.put(f"/api/documents/{doc_id}/text", {"text": new_text}, format="json").status_code, 403)
        self.as_admin()
        self.assertEqual(self.client.put(f"/api/documents/{doc_id}/text", {"text": "  "}, format="json").status_code, 400)
        saved = self.client.put(f"/api/documents/{doc_id}/text", {"text": new_text}, format="json").json()
        self.assertEqual(saved["id"], doc_id)
        self.assertIn("3", [c["point"] for c in saved["chunks"]])
        self.assertTrue(any((settings.TEST_TMP / "data" / "document_versions").iterdir()))
        self.assertTrue(AuditRecord.objects.filter(action="текст документа изменён").exists())
        self.client.delete(f"/api/documents/{doc_id}/delete")

        # «Полностью удалить из системы» — только администратор, только папка внутри «Удалённых», вернуть нельзя
        entry = next(d for d in self.client.get("/api/documents/deleted").json()["items"] if d["file"] == "Приказ_200.txt")
        self.as_user()
        self.assertEqual(self.client.delete(f"/api/documents/deleted/{entry['id']}").status_code, 403)
        self.as_admin()
        self.assertEqual(self.client.delete("/api/documents/deleted/..%5C..%5Cx").status_code, 404)
        self.assertEqual(self.client.delete(f"/api/documents/deleted/{entry['id']}").status_code, 200)
        self.assertNotIn(entry["id"], [d["id"] for d in self.client.get("/api/documents/deleted").json()["items"]])
        self.assertEqual(self.client.post(f"/api/documents/deleted/{entry['id']}/restore").status_code, 404)
        self.assertTrue(AuditRecord.objects.filter(action="документ полностью удалён из системы").exists())

    def test_document_departments(self):
        """«Для кого» в карточке документа: ответ — по документам своего подразделения, подразделение — само."""
        call_command("import_legacy", stdout=io.StringIO())  # справочник «Сотрудники» → подразделения
        card = settings.TEST_TMP / "база_знаний" / "Письмо_бухгалтерии_ЭДО.eml.meta.json"
        original = card.read_text(encoding="utf-8")
        self.addCleanup(lambda: (card.write_text(original, encoding="utf-8"), agent.get_knowledge_base()))
        self.as_admin()
        self.assertIn("Бухгалтерия", self.client.get("/api/meta").json()["departments"])
        who = lambda text: self.client.get("/api/department", {"who": text}).json()["department"]  # noqa: E731
        self.assertEqual((who("Волкова М.Н."), who("ivanov@company.ru"), who("Неизвестный")),
                         ("Бухгалтерия", "Отдел продаж", None))
        url = "/api/documents/MAIL-2026-402/card"
        self.assertEqual(self.client.put(url, {"departments": ["Нет такого"]}, format="json").status_code, 400)
        self.assertEqual(self.client.put(url, {"departments": ["Бухгалтерия"]}, format="json").json()["changed"], ["departments"])
        self.assertEqual(json.loads(card.read_text(encoding="utf-8")),
                         {**json.loads(original), "departments": ["Бухгалтерия"]})  # id и владелец на месте
        docs = {d["id"]: d for d in self.client.get("/api/documents").json()["items"]}
        self.assertEqual((docs["MAIL-2026-402"]["departments"], docs["ORD-2026-118"]["departments"]), (["Бухгалтерия"], []))

        # «Задать вопрос»: кто спрашивает и подразделение — из учётной записи, под которой вошли; присланное
        # браузером («employee», «department») не учитывается
        question = "Кто отвечает за проверку контрагента в ЭДО?"
        accountant = make_user("volkova", USER, name="Волкова Марина Николаевна")
        seller = make_user("ivanov2", USER, name="Иванов Иван Сергеевич")
        stranger = make_user("guest", USER, name="Гость Без Справочника")

        def ask(user):
            self.client.force_authenticate(user)
            return self.client.post("/api/ask", {"question": question, "employee": "Волкова Марина Николаевна",
                                                 "department": "Бухгалтерия"}, format="json").json()
        with no_llm():
            own, other, unknown = ask(accountant), ask(seller), ask(stranger)
        self.assertEqual((own["result"]["answer_source"], own["result"]["department"]), ("process_map", "Бухгалтерия"))
        self.assertEqual(other["result"]["department"], "Отдел продаж")  # подделать подразделение нельзя
        self.assertEqual(other["decision"], "передано специалисту")
        self.assertIn("только в документах для других подразделений", other["result"]["escalation_reason"])
        self.assertEqual((unknown["result"]["department"], unknown["result"]["answer_source"]), (None, "process_map"))
        self.assertFalse(unknown["email_ok"])  # нет в справочнике — нет e-mail, экран подскажет
        self.assertEqual(SelfServiceQuestion.objects.filter(user=seller).get().employee,
                         "Иванов Иван Сергеевич <ivanov@company.ru>")
        me = self.client.get("/api/auth/me").json()["user"]
        self.assertEqual((me["name"], me["department"]), ("Гость Без Справочника", ""))
        self.client.force_authenticate(seller)
        self.assertEqual(self.client.get("/api/auth/me").json()["user"]["department"], "Отдел продаж")
        # «Главная»: подразделения выбираются списком — несколько сразу или ни одного (все документы)
        self.client.force_authenticate(seller)
        answer = lambda deps: self.client.post("/api/answer", {"body": question, "department": deps},  # noqa: E731
                                               format="json").json()["result"]
        with no_llm():
            sales_only, both, none = answer(["Отдел продаж"]), answer(["Отдел продаж", "Бухгалтерия"]), answer([])
        self.assertEqual(sales_only["answer_source"], "escalation")
        self.assertEqual((both["answer_source"], both["department"]), ("process_map", "Бухгалтерия, Отдел продаж"))
        self.assertEqual((none["answer_source"], none["department"]), ("process_map", None))
        self.assertEqual(svc.resolve_department(["Бухгалтерия", "Нет такого"], ""), ["Бухгалтерия"])
        # и по-прежнему можно указать одно подразделение строкой
        self.assertEqual((svc.resolve_department("Бухгалтерия", "Иванов"), svc.resolve_department("*", "Волкова М.Н."),
                          svc.resolve_department("", "Волкова М.Н.")), ("Бухгалтерия", None, "Бухгалтерия"))
        self.as_admin()

        # Подразделение из карточки пропало из справочника — «Требует внимания» подскажет
        card.write_text(json.dumps({**json.loads(original), "departments": ["Старый отдел"]}, ensure_ascii=False),
                        encoding="utf-8")
        kinds = [(a["kind"], a["doc_id"]) for a in self.client.get("/api/attention").json()["items"]]
        self.assertIn(("Подразделение", "MAIL-2026-402"), kinds)

    def test_employees(self):
        call_command("import_legacy", stdout=io.StringIO())
        self.as_user()
        data = self.client.get("/api/employees").json()
        self.assertFalse(data["editable"])
        self.assertIn("Бухгалтерия", [d["name"] for d in data["departments"]])
        self.assertEqual(self.client.post("/api/employees", {"department": "Отдел", "fio": "Новый Сотрудник"},
                                          format="json").status_code, 403)
        self.as_admin()
        bad = self.client.post("/api/employees", {"department": "", "fio": "Новиков", "email": "x"}, format="json")
        self.assertEqual(bad.status_code, 400)
        new = self.client.post("/api/employees", {"department": "Отдел кадров", "fio": "Новикова Алла Петровна",
                                                  "position": "Специалист", "email": "novikova@company.ru"}, format="json").json()
        exported = json.loads((settings.TEST_TMP / "employees.json").read_text(encoding="utf-8"))
        self.assertIn("Отдел кадров", [d["department"] for d in exported])  # агент видит изменения
        self.client.put(f"/api/employees/{new['id']}", {"department": "Бухгалтерия", "fio": "Новикова Алла Петровна"},
                        format="json")
        self.assertFalse(Department.objects.filter(name="Отдел кадров").exists())  # опустевшее подразделение удалено
        self.client.delete(f"/api/employees/{new['id']}")
        self.assertFalse(Employee.objects.filter(fio="Новикова Алла Петровна").exists())


class AdminTests(Base):
    def test_users_management(self):
        self.as_user()
        self.assertEqual(self.client.get("/api/admin/users").status_code, 403)
        self.as_admin()
        password = self.client.get("/api/admin/new-password").json()["password"]
        self.assertEqual(len(password), 10)
        self.assertEqual(self.client.post("/api/admin/users", {"login": "petrova", "name": "Петрова Анна", "role": USER,
                                                               "password": password}, format="json").status_code, 200)
        self.assertTrue(APIClient().login(username="petrova", password=password))
        self.assertEqual(self.client.post("/api/admin/users", {"login": "x", "name": "А", "role": USER, "password": "1"},
                                          format="json").status_code, 400)
        self.assertEqual(self.client.patch("/api/admin/users/admin", {"role": USER}, format="json").status_code, 400)
        self.assertEqual(self.client.delete("/api/admin/users/admin").status_code, 400)  # себя — нельзя
        self.assertEqual(self.client.post("/api/admin/users/petrova/password", {"password": "Novyi2026x"},
                                          format="json").status_code, 200)
        self.client.patch("/api/admin/users/petrova", {"active": False}, format="json")
        self.assertFalse(APIClient().login(username="petrova", password="Novyi2026x"))  # заблокирована
        self.assertEqual(self.client.delete("/api/admin/users/petrova").status_code, 200)
        actions = [a["action"] for a in self.client.get("/api/admin/audit").json()["items"]]
        self.assertIn("создана учётная запись", actions)
        self.assertIn("удалена учётная запись", actions)

    def test_confidence_chip_and_settings(self):
        self.as_user()
        with no_llm():
            data = self.client.post("/api/ask", {"question": RULE_Q}, format="json").json()
        self.assertEqual((data["confidence"]["color"], data["confidence"]["label"]), ("green", "Уверен"))
        self.assertEqual(self.client.get("/api/admin/confidence").status_code, 403)  # пользователю — нельзя
        self.as_admin()
        items = {i["key"]: i for i in self.client.get("/api/admin/confidence").json()["items"]}
        self.assertEqual([items[k]["recommended"] for k in ("green_threshold", "yellow_threshold", "hide_threshold")],
                         [90, 75, 65])
        bad = self.client.put("/api/admin/confidence", {"green_threshold": 50, "yellow_threshold": 60}, format="json")
        self.assertEqual(bad.status_code, 400)
        ok = self.client.put("/api/admin/confidence", {"green_threshold": 97}, format="json")
        self.assertEqual({i["key"]: i["value"] for i in ok.json()["items"]}["green_threshold"], 97)
        with no_llm():
            data = self.client.post("/api/ask", {"question": RULE_Q}, format="json").json()
        self.assertEqual(data["confidence"]["color"], "yellow")  # 95 < 97 — уже «Сомневается»
        self.client.delete("/api/admin/confidence")
        with no_llm():
            data = self.client.post("/api/ask", {"question": RULE_Q}, format="json").json()
        self.assertEqual(data["confidence"]["color"], "green")
        self.assertTrue(AuditRecord.objects.filter(action="критерии уверенности ответа").exists())

    def test_low_confidence_answer_is_not_shown(self):
        """Счёт не выше порога скрытия — ответ не выводится нигде, вопрос уходит специалисту."""
        self.as_admin()
        with no_llm():
            earlier = self.client.post("/api/ask", {"question": RULE_Q}, format="json").json()
        self.assertIn("Смирнов", earlier["result"]["answer"])  # 90 — обычный зелёный ответ
        # порог скрытия 98: ответ по правилу (95 − 5 за противоречие в чате) теперь считается ненадёжным
        self.assertEqual(self.client.put("/api/admin/confidence", {"green_threshold": 100, "yellow_threshold": 99,
                                                                   "hide_threshold": 98}, format="json").status_code, 200)
        with no_llm():
            data = self.client.post("/api/ask", {"question": RULE_Q}, format="json").json()
        self.assertEqual(data["decision"], "передано специалисту")
        self.assertEqual((data["confidence"]["color"], data["confidence"]["score"]), ("hidden", 90))
        self.assertTrue(data["request_id"])
        self.assertNotIn("Смирнов", json.dumps(data["result"], ensure_ascii=False))
        request = self.client.get(f"/api/requests/{data['request_id']}").json()
        self.assertEqual(request["status"], ESCALATED)
        self.assertNotIn("Смирнов", json.dumps(request["result"], ensure_ascii=False))
        # и прежний ответ в истории по новым критериям тоже не показывается
        items = self.client.get("/api/history").json()["items"]
        self.assertTrue(all("Смирнов" not in (i["answer"] or "") for i in items))
        for item in items:
            detail = self.client.get(f"/api/history/{item['id']}").json()
            self.assertNotIn("Смирнов", json.dumps(detail["result"], ensure_ascii=False))
            self.assertEqual(detail["confidence"]["color"], "hidden")
        # вернули рекомендуемые — ответ снова виден
        self.client.delete("/api/admin/confidence")
        with no_llm():
            data = self.client.post("/api/ask", {"question": RULE_Q}, format="json").json()
        self.assertIn("Смирнов", data["result"]["answer"])

    def test_admin_updates_system(self):
        """«Версия»: проверить пакет → установить (резервная копия, замена программы) → перезапуск; откат."""
        import tempfile
        from pathlib import Path
        import admin as legacy_admin
        tmp = Path(tempfile.mkdtemp())
        installed, backups = tmp / "installed", tmp / "backups"
        installed.mkdir()
        (installed / "VERSION").write_text("1.0", encoding="utf-8")
        (installed / "app.py").write_text("# old\n", encoding="utf-8")
        new = tmp / "new"
        new.mkdir()
        (new / "VERSION").write_text("1.1", encoding="utf-8")
        (new / "app.py").write_text("# new\n", encoding="utf-8")
        package = legacy_admin.build_update_package(tmp / "out", new)
        # Настоящие функции, но установка — во временную папку, а не в рабочую; перезапуск не выполняется
        real = (legacy_admin.inspect_update, legacy_admin.apply_update, legacy_admin.rollback, legacy_admin.list_backups)
        with mock.patch.object(legacy_admin, "inspect_update", side_effect=lambda p: real[0](p, installed)), \
                mock.patch.object(legacy_admin, "apply_update", side_effect=lambda p, allow_same_or_older=False:
                                  real[1](p, installed, backups, allow_same_or_older)), \
                mock.patch.object(legacy_admin, "rollback", side_effect=lambda b: real[2](b, installed, backups)), \
                mock.patch.object(legacy_admin, "list_backups", side_effect=lambda: real[3](backups)), \
                mock.patch("config.BACKUPS_DIR", backups), \
                mock.patch.object(svc, "schedule_restart") as restart:
            self.as_user()
            self.assertEqual(self.client.get("/api/admin/update").status_code, 403)
            self.as_admin()
            self.assertEqual(self.client.get("/api/admin/update").json()["version"], legacy_admin.read_version())
            bad = self.client.post("/api/admin/update/inspect", {"file": SimpleUploadedFile("x.zip", b"not a zip")})
            self.assertEqual(bad.status_code, 400)
            with open(package, "rb") as file:
                info = self.client.post("/api/admin/update/inspect", {"file": file}).json()
            self.assertEqual((info["from_version"], info["to_version"], info["newer"], info["changed"]),
                             ("1.0", "1.1", True, ["VERSION", "app.py"]))
            self.assertEqual((installed / "app.py").read_text(encoding="utf-8"), "# old\n")  # проверка ничего не меняет
            done = self.client.post("/api/admin/update/apply", {"token": info["token"]}, format="json").json()
            self.assertEqual((done["to_version"], done["restarting"]), ("1.1", True))
            self.assertEqual((installed / "app.py").read_text(encoding="utf-8"), "# new\n")
            restart.assert_called_once()
            self.assertEqual(self.client.post("/api/admin/update/apply", {"token": info["token"]},
                                              format="json").status_code, 400)  # пакет одноразовый
            self.assertEqual(self.client.post("/api/admin/update/rollback", {"backup": "..\\..\\Windows"},
                                              format="json").status_code, 400)
            backup_id = done["backup"]
            rolled = self.client.post("/api/admin/update/rollback", {"backup": backup_id}, format="json").json()
            self.assertEqual(rolled["version"], "1.0")
            self.assertEqual((installed / "app.py").read_text(encoding="utf-8"), "# old\n")
            self.assertEqual(restart.call_count, 2)
            state = self.client.get("/api/admin/update").json()
            self.assertEqual(len(state["backups"]), 2)  # перед обновлением и перед откатом
            self.assertEqual([h["action"] for h in state["history"]], ["откат версии", "обновление системы"])
        actions = list(AuditRecord.objects.values_list("action", flat=True))
        self.assertIn("обновление системы", actions)
        self.assertIn("откат версии", actions)

    def test_follow_up_questions_keep_context(self):
        """«Уточнить»: уточнение учитывает первый вопрос, цепочка — в истории и у специалиста."""
        from datetime import timedelta
        from django.utils import timezone
        self.as_user()
        ask = lambda text, prev=None: self.client.post(  # noqa: E731
            "/api/ask", {"question": text, **({"follow_up_of": prev} if prev else {})}, format="json").json()
        with no_llm():
            first = ask("Кто подписывает агентский договор?")
            second = ask("а на 800 тыс.?", first["record_id"])
        self.assertEqual(first["decision"], "нужно уточнение")
        self.assertEqual((second["understood_as"], second["rewrite_method"], second["step"], second["dialog"]),
                         ("Кто подписывает агентский договор на сумму 800 000 руб.?", "сумма", 1, first["dialog"]))
        self.assertIn("генеральный директор", second["result"]["answer"])
        with no_llm():
            third = ask("а на 300?", second["record_id"])  # суммы подставляются в уже понятый вопрос
        self.assertEqual(third["understood_as"], "Кто подписывает агентский договор на сумму 300 000 руб.?")
        self.assertIn("Смирнов", third["result"]["answer"])

        history = self.client.get("/api/history").json()["items"]
        self.assertEqual([(h["step"], h["dialog"] == first["dialog"]) for h in history[:3]], [(2, True), (1, True), (0, True)])
        detail = self.client.get(f"/api/history/{history[0]['id']}").json()
        self.assertEqual([c["body"] for c in detail["chain"]], ["Кто подписывает агентский договор?", "а на 800 тыс.?", "а на 300?"])

        # специалисту уходит вся цепочка
        request_id = self.client.post(f"/api/ask/{third['record_id']}/handoff", {"comment": ""}, format="json").json()["request_id"]
        body = Request.objects.get(id=request_id).body
        self.assertIn("Вопрос 1: Кто подписывает агентский договор?", body)
        self.assertIn("Уточнение 3: а на 300?", body)
        self.assertIn("Система поняла вопрос так: Кто подписывает агентский договор на сумму 300 000 руб.?", body)

        # чужой вопрос нельзя «уточнить» — это новый вопрос; через 30 минут цепочка начинается заново
        self.as_admin()
        with no_llm():
            foreign = ask("а на 800 тыс.?", first["record_id"])
        self.assertEqual((foreign["step"], foreign["understood_as"]), (0, None))
        self.as_user()
        SelfServiceQuestion.objects.filter(dialog=first["dialog"]).update(created=timezone.now() - timedelta(minutes=31))
        with no_llm():
            late = ask("Кто подписывает агентский договор на 300 000 руб.?", third["record_id"])
        self.assertTrue(late["chain_reset"])
        self.assertNotEqual(late["dialog"], first["dialog"])

    def test_quality_service_answer_fills_knowledge_base(self):
        """Вопроса нет в документах → служба качества → «Добавить ответ в базу знаний» → дальше отвечает система."""
        question = "Можно ли перенести лицензию на другое юрлицо при реорганизации?"
        self.as_user()
        with no_llm():
            first = self.client.post("/api/ask", {"question": question}, format="json").json()
        self.assertEqual(first["decision"], "передано специалисту")
        request_id = first["request_id"]
        url = f"/api/requests/{request_id}/to-knowledge"
        payload = {"answer": "Да, по заявлению нового юрлица, срок — 10 рабочих дней.", "related": "ORD-2026-118"}
        self.assertEqual(self.client.post(url, payload, format="json").status_code, 403)  # пользователю нельзя
        self.as_admin()
        self.assertEqual(self.client.post(url, {"answer": ""}, format="json").status_code, 400)
        self.assertEqual(self.client.post(url, {**payload, "related": "НЕТ"}, format="json").status_code, 400)
        saved = self.client.post(url, payload, format="json").json()
        self.addCleanup(lambda: (settings.TEST_TMP / "база_знаний" / saved["file"]).unlink(missing_ok=True)
                        or (settings.TEST_TMP / "база_знаний" / (saved["file"] + ".meta.json")).unlink(missing_ok=True)
                        or agent.get_knowledge_base())
        self.assertTrue(saved["doc_id"].startswith("QA-"))
        docs = {d["id"]: d for d in self.client.get("/api/documents").json()["items"]}
        self.assertEqual(docs[saved["doc_id"]]["type"], "Ответ службы качества")
        self.as_user()
        with no_llm():
            again = self.client.post("/api/ask", {"question": question}, format="json").json()
        self.assertEqual((again["decision"], again["result"]["answer_source"]), ("ответ сразу", "quality"),
                         again["result"].get("logs"))
        self.assertIn("10 рабочих дней", again["result"]["answer"])
        self.assertEqual(again["confidence"]["color"], "green")
        self.assertTrue(AuditRecord.objects.filter(action="база знаний пополнена ответом").exists())

    def test_ping_without_login(self):
        """По /api/ping запускатель узнаёт свой сервер — ответ без входа, без данных системы."""
        import launcher
        self.client.logout()
        response = self.client.get("/api/ping")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"app": launcher.WEB_APP_ID})

    def test_summary_and_history(self):
        self.as_user()
        with no_llm():
            self.client.post("/api/ask", {"question": RULE_Q}, format="json")
            self.client.post("/api/ask", {"question": "Как оформить отпуск?"}, format="json")
        data = self.client.get("/api/summary", {"period": "Всё время"}).json()
        self.assertEqual((data["self_service"]["total"], data["self_service"]["closed"], data["self_service"]["handed"]),
                         (2, 1, 1))
        history = self.client.get("/api/history").json()["items"]
        self.assertEqual(len(history), 2)
        detail = self.client.get(f"/api/history/{history[-1]['id']}").json()
        self.assertIn("Смирнов", detail["result"]["answer"])

    def test_transfer_archive(self):
        self.as_admin()
        with mock.patch("admin.system_check", return_value=[{"item": "Python", "required": "3.10+", "found": "3.14",
                                                             "ok": True}]):
            response = self.client.post("/api/admin/transfer-archive")
        self.assertEqual(response.status_code, 200)
        names = zipfile.ZipFile(BytesIO(b"".join(response.streaming_content))).namelist()
        self.assertIn("Документы/Роли_и_пользователи.docx", names)
        self.assertIn("Проект/web/backend/core/views.py", names)  # веб-версия тоже переносится


class LegacyImportTests(TestCase):
    def test_import_users_and_requests(self):
        data_dir = settings.TEST_TMP / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        salt = os.urandom(16)
        (data_dir / "users.json").write_text(json.dumps([{
            "login": "lebedeva", "name": "Лебедева Наталья", "role": ADMIN, "active": True, "salt": salt.hex(),
            "hash": hashlib.pbkdf2_hmac("sha256", b"Lebedeva2026x", salt, 200_000).hex(),
            "created": "24.11.2026 10:00", "last_login": None}], ensure_ascii=False), encoding="utf-8")
        (data_dir / "requests.json").write_text(json.dumps([{
            "id": "REQ-20261124-AAAA", "created": "24.11.2026 10:00", "due": "25.11.2026 10:00", "sender": "a@b.ru",
            "subject": "Тема", "body": "Вопрос", "status": "Новое", "read": False, "source": "вручную",
            "source_file": None, "process": "Прочие вопросы", "result": None, "processed_at": None, "sent_at": None}],
            ensure_ascii=False), encoding="utf-8")
        out = io.StringIO()
        call_command("import_legacy", stdout=out)
        call_command("import_legacy", stdout=io.StringIO())  # повторно — без дублей
        self.assertIn("учётных записей: 1", out.getvalue())
        self.assertEqual(Request.objects.filter(id="REQ-20261124-AAAA").count(), 1)
        self.assertTrue(APIClient().login(username="lebedeva", password="Lebedeva2026x"))  # пароль прежний
        self.assertEqual(Profile.objects.get(user__username="lebedeva").role, ADMIN)
        self.assertEqual(Employee.objects.count(), 13)

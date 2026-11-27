"""Данные веб-версии. Документы базы знаний по-прежнему файлы в папке «база_знаний» (их читает агент);
в базе — то, что раньше лежало в data/*.json и *.jsonl."""
from django.conf import settings
from django.db import models

ADMIN, USER = "Администратор", "Пользователь"
ROLES = [(ADMIN, ADMIN), (USER, USER)]


class Profile(models.Model):
    """Роль и ФИО пользователя (логин и пароль — стандартный User Django)."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    name = models.CharField("ФИО", max_length=200)
    role = models.CharField("Роль", max_length=20, choices=ROLES, default=USER)

    def __str__(self):
        return f"{self.user.username} ({self.role})"


class Request(models.Model):
    """Обращение: письмо из папки «входящие», вопрос оператора или вопрос сотрудника, переданный специалисту."""
    id = models.CharField(primary_key=True, max_length=40)
    created = models.DateTimeField(db_index=True)
    due = models.DateTimeField()
    sender = models.CharField(max_length=300, blank=True)
    subject = models.CharField(max_length=500, blank=True)
    body = models.TextField()
    status = models.CharField(max_length=40, db_index=True)
    read = models.BooleanField(default=False)
    source = models.CharField(max_length=60, blank=True)
    source_file = models.CharField(max_length=300, blank=True, null=True, unique=True)
    process = models.CharField(max_length=200, blank=True)
    result = models.JSONField(null=True, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created"]


class SelfServiceQuestion(models.Model):
    """Вопрос в режиме «сотрудник спрашивает сам» (раньше data/self_service.jsonl)."""
    created = models.DateTimeField(auto_now_add=True, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    employee = models.CharField(max_length=300, blank=True)
    question = models.TextField()
    decision = models.CharField(max_length=60)
    answer_source = models.CharField(max_length=40, blank=True, null=True)
    found = models.BooleanField(default=False)
    latency = models.FloatField(default=0)
    answer = models.TextField(blank=True)
    helped = models.BooleanField(null=True)
    comment = models.TextField(blank=True)
    request = models.ForeignKey(Request, null=True, blank=True, on_delete=models.SET_NULL)
    # Цепочка уточнений: первый вопрос и уточнения к нему — один dialog; step 0 — первый вопрос.
    # question — как спросил сотрудник, understood_as — самостоятельный вопрос, по которому искали ответ
    dialog = models.CharField(max_length=32, blank=True, db_index=True)
    step = models.PositiveSmallIntegerField(default=0)
    understood_as = models.TextField(blank=True)
    rewrite_method = models.CharField(max_length=20, blank=True)


class Feedback(models.Model):
    """Оценка ответа оператором: верно / неверно / устарело (раньше data/feedback.jsonl)."""
    created = models.DateTimeField(auto_now_add=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    verdict = models.CharField(max_length=20)
    comment = models.TextField(blank=True)
    sender = models.CharField(max_length=300, blank=True)
    subject = models.CharField(max_length=500, blank=True)
    body = models.TextField(blank=True)
    answer_source = models.CharField(max_length=40, blank=True, null=True)
    answer = models.TextField(blank=True)
    basis = models.JSONField(default=list)
    escalation_id = models.CharField(max_length=60, blank=True, null=True)


class HistoryRecord(models.Model):
    """История вопросов (раньше data/history.jsonl) — с полным результатом, чтобы открыть ответ снова."""
    created = models.DateTimeField(auto_now_add=True, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    sender = models.CharField(max_length=300, blank=True)
    subject = models.CharField(max_length=500, blank=True)
    body = models.TextField()
    answer_source = models.CharField(max_length=40, blank=True, null=True)
    answer = models.TextField(blank=True)
    result = models.JSONField(null=True, blank=True)
    dialog = models.CharField(max_length=32, blank=True, db_index=True)  # цепочка уточнений (см. SelfServiceQuestion)
    step = models.PositiveSmallIntegerField(default=0)
    understood_as = models.TextField(blank=True)

    class Meta:
        ordering = ["-created"]


class AuditRecord(models.Model):
    """Журнал входов и действий администратора (раньше data/audit.jsonl)."""
    created = models.DateTimeField(auto_now_add=True, db_index=True)
    user = models.CharField(max_length=150, blank=True)
    action = models.CharField(max_length=200)
    detail = models.TextField(blank=True)

    class Meta:
        ordering = ["-created"]


class Setting(models.Model):
    """Настройки, которые меняет администратор (например, критерии уверенности ответа)."""
    key = models.CharField(max_length=100, unique=True)
    value = models.JSONField(default=dict)
    updated = models.DateTimeField(auto_now=True)
    updated_by = models.CharField(max_length=150, blank=True)


class Department(models.Model):
    name = models.CharField("Подразделение", max_length=200, unique=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "name"]


class Employee(models.Model):
    """Справочник «Сотрудники». При изменении выгружается в employees.json — его читает агент для контактов."""
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name="employees")
    fio = models.CharField("ФИО", max_length=200)
    position = models.CharField("Должность", max_length=200, blank=True)
    email = models.CharField("E-mail", max_length=200, blank=True)
    phone = models.CharField("Телефон", max_length=100, blank=True)

    class Meta:
        ordering = ["fio"]

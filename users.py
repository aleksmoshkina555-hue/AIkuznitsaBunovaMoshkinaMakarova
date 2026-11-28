"""Учётные записи и роли.

Роли:
- «Администратор» — всё, что может пользователь, плюс: учётные записи, добавление документов в базу,
  правка справочника «Сотрудники», обновление версии системы, архив для переноса.
- «Пользователь» — задаёт вопросы, работает с обращениями, смотрит «Сводку», документы и справочник.

Хранение — data/users.json. Пароли не хранятся: только соль и хеш PBKDF2-HMAC-SHA256
(стандартная библиотека Python, 200 000 итераций). Входы и действия администратора — в data/audit.jsonl.
"""
import hashlib
import hmac
import json
import os
import re
from datetime import datetime
from pathlib import Path

from config import AUDIT_FILE, USERS_FILE
from journal import append_jsonl
import string

PASSWORD_LETTERS = "".join(c for c in string.ascii_letters if c not in "IlOo")
PASSWORD_DIGITS = "23456789"

ADMIN, USER = "Администратор", "Пользователь"
ROLES = {
    ADMIN: ["Задавать вопросы и работать с обращениями", "Смотреть «Сводку», документы базы, справочник «Сотрудники»",
            "Добавлять документы в базу знаний", "Изменять справочник «Сотрудники»",
            "Управлять учётными записями", "Обновлять версию системы и откатывать обновление",
            "Создавать архив для переноса на другой компьютер"],
    USER: ["Задавать вопросы и работать с обращениями", "Смотреть «Сводку», документы базы, справочник «Сотрудники»"],
}
PERMISSIONS = {
    "manage_users": {ADMIN}, "add_documents": {ADMIN}, "edit_employees": {ADMIN},
    "update_system": {ADMIN}, "export_archive": {ADMIN},
}
ITERATIONS = 200_000
LOGIN_RE = re.compile(r"[A-Za-z0-9._-]{3,32}")
MIN_PASSWORD = 8


class AccessDenied(Exception):
    """Действие не разрешено роли пользователя."""


def can(user: dict | None, permission: str) -> bool:
    return bool(user) and user.get("role") in PERMISSIONS.get(permission, set())


def require(user: dict | None, permission: str) -> None:
    if not can(user, permission):
        raise AccessDenied(f"Нет прав: {permission}")


def _hash(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS).hex()


def password_problems(password: str, login: str = "") -> list[str]:
    problems = []
    if len(password) < MIN_PASSWORD:
        problems.append(f"пароль — не короче {MIN_PASSWORD} символов")
    if not (re.search(r"[A-Za-zА-Яа-яЁё]", password) and re.search(r"\d", password)):
        problems.append("в пароле нужны буквы и цифры")
    if login and password.lower() == login.lower():
        problems.append("пароль не должен совпадать с логином")
    return problems




def generate_password(length: int = 10) -> str:
    """Случайный пароль (модуль secrets): латинские буквы и цифры без похожих символов — легко продиктовать."""
    import secrets
    while True:
        password = "".join(secrets.choice(PASSWORD_LETTERS + PASSWORD_DIGITS) for _ in range(length))
        if (sum(c in PASSWORD_DIGITS for c in password) >= 2 and any(c.isupper() for c in password)
                and any(c.islower() for c in password)):
            return password


def audit(actor: str, action: str, detail: str = "", path: Path = AUDIT_FILE) -> None:
    append_jsonl(path, {"time": datetime.now().isoformat(timespec="seconds"), "user": actor,
                        "action": action, "detail": detail})


class UserStore:
    def __init__(self, path: Path = USERS_FILE, audit_path: Path = AUDIT_FILE):
        self.path, self.audit_path = Path(path), Path(audit_path)
        try:
            self.users: list[dict] = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            self.users = []

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.users, ensure_ascii=False, indent=1), encoding="utf-8")

    @property
    def empty(self) -> bool:
        return not self.users

    def get(self, login: str) -> dict | None:
        return next((u for u in self.users if u["login"].lower() == login.strip().lower()), None)

    def public(self, user: dict) -> dict:
        """Без соли и хеша — для окна и документов."""
        return {k: v for k, v in user.items() if k not in ("salt", "hash")}

    def admins(self) -> list[dict]:
        return [u for u in self.users if u["role"] == ADMIN and u.get("active", True)]

    def add(self, login: str, name: str, role: str, password: str, actor: str = "") -> list[str]:
        """Новая учётная запись; возвращает ошибки (пустой список — создана)."""
        login = login.strip()
        errors = []
        if not LOGIN_RE.fullmatch(login):
            errors.append("логин — 3–32 латинские буквы, цифры, точка, дефис или подчёркивание")
        elif self.get(login):
            errors.append("такой логин уже есть")
        if role not in ROLES:
            errors.append("неизвестная роль")
        if len(name.split()) < 2:
            errors.append("ФИО — минимум фамилия и имя")
        errors += password_problems(password, login)
        if errors:
            return errors
        salt = os.urandom(16)
        self.users.append({"login": login, "name": name.strip(), "role": role, "active": True,
                           "salt": salt.hex(), "hash": _hash(password, salt),
                           "created": datetime.now().strftime("%d.%m.%Y %H:%M"), "last_login": None})
        self.save()
        audit(actor or login, "создана учётная запись", f"{login} ({role})", self.audit_path)
        return []

    def authenticate(self, login: str, password: str) -> dict | None:
        user = self.get(login)
        ok = bool(user) and user.get("active", True) and hmac.compare_digest(
            user["hash"], _hash(password, bytes.fromhex(user["salt"])))
        audit(login.strip(), "вход" if ok else "неудачный вход", "", self.audit_path)
        if not ok:
            return None
        user["last_login"] = datetime.now().strftime("%d.%m.%Y %H:%M")
        self.save()
        return self.public(user)

    def update(self, login: str, actor: str, name: str | None = None, role: str | None = None,
               active: bool | None = None) -> list[str]:
        user = self.get(login)
        if user is None:
            return ["учётная запись не найдена"]
        if role is not None and role not in ROLES:
            return ["неизвестная роль"]
        removes_admin = user["role"] == ADMIN and ((role is not None and role != ADMIN) or active is False)
        if removes_admin and len(self.admins()) <= 1:
            return ["нельзя оставить систему без администратора"]
        if name is not None:
            if len(name.split()) < 2:
                return ["ФИО — минимум фамилия и имя"]
            user["name"] = name.strip()
        if role is not None:
            user["role"] = role
        if active is not None:
            user["active"] = active
        self.save()
        audit(actor, "изменена учётная запись", f"{login}: роль {user['role']}, "
                                                f"{'активна' if user.get('active', True) else 'заблокирована'}", self.audit_path)
        return []

    def set_password(self, login: str, password: str, actor: str) -> list[str]:
        user = self.get(login)
        if user is None:
            return ["учётная запись не найдена"]
        problems = password_problems(password, login)
        if problems:
            return problems
        salt = os.urandom(16)
        user["salt"], user["hash"] = salt.hex(), _hash(password, salt)
        self.save()
        audit(actor, "сменён пароль", login, self.audit_path)
        return []

    def change_own_password(self, login: str, old_password: str, new_password: str) -> list[str]:
        """Пользователь меняет свой пароль: нужен текущий пароль."""
        user = self.get(login)
        if user is None or not hmac.compare_digest(user["hash"], _hash(old_password, bytes.fromhex(user["salt"]))):
            audit(login, "неудачная смена своего пароля", "", self.audit_path)
            return ["текущий пароль указан неверно"]
        if new_password == old_password:
            return ["новый пароль должен отличаться от текущего"]
        return self.set_password(login, new_password, actor=login)

    def delete(self, login: str, actor: str) -> list[str]:
        user = self.get(login)
        if user is None:
            return ["учётная запись не найдена"]
        if user["login"].lower() == actor.lower():
            return ["нельзя удалить свою учётную запись"]
        if user["role"] == ADMIN and len(self.admins()) <= 1:
            return ["нельзя удалить последнего администратора"]
        self.users.remove(user)
        self.save()
        audit(actor, "удалена учётная запись", login, self.audit_path)
        return []

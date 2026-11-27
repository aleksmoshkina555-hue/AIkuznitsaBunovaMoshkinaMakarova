"""Справочник «Сотрудники»: подразделения → сотрудники (ФИО, должность, e-mail, телефон).

Хранится в employees.json (путь — EMPLOYEES_FILE) по подразделениям:
[{"department": "Бухгалтерия", "employees": [{"fio": "...", "position": "...", "email": "...", "phone": "..."}]}]

Используется в ответах: «Ответственный: Коммерческий директор Смирнов И.О.» → контакты из справочника
(e-mail и телефон), чтобы сотруднику не искать их отдельно.
"""
import json
import re
from pathlib import Path

from config import EMPLOYEES_FILE

FIELDS = ("fio", "position", "email", "phone")
FIELD_NAMES = {"department": "Подразделение", "fio": "ФИО", "position": "Должность", "email": "E-mail", "phone": "Телефон"}
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# «Смирнов И.О.», «Смирнов И. О.», «Волкова М.Н.»
SHORT_NAME_RE = re.compile(r"([А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?)\s+([А-ЯЁ])\.\s?([А-ЯЁ])\.")


def load_directory(path: Path = EMPLOYEES_FILE) -> list[dict]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    departments = []
    for item in data if isinstance(data, list) else []:
        employees = [{k: str(e.get(k, "")).strip() for k in FIELDS} for e in item.get("employees", [])]
        departments.append({"department": str(item.get("department", "")).strip(), "employees": employees})
    return departments


def save_directory(departments: list[dict], path: Path = EMPLOYEES_FILE) -> None:
    clean = [d for d in departments if d["department"]]
    Path(path).write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")


def all_employees(departments: list[dict]) -> list[dict]:
    """Плоский список: у каждого сотрудника — его подразделение."""
    return [dict(e, department=d["department"]) for d in departments for e in d["employees"]]


def validate(employee: dict) -> list[str]:
    """Ошибки заполнения карточки (пустой список — всё верно)."""
    errors = []
    if not employee.get("department", "").strip():
        errors.append("не указано подразделение")
    if len(employee.get("fio", "").split()) < 2:
        errors.append("ФИО — минимум фамилия и имя")
    email = employee.get("email", "").strip()
    if email and not EMAIL_RE.fullmatch(email):
        errors.append("e-mail в формате name@company.ru")
    phone = employee.get("phone", "").strip()
    if phone and sum(ch.isdigit() for ch in phone) < 2:
        errors.append("в телефоне должны быть цифры")
    return errors


def upsert(departments: list[dict], employee: dict, old: dict | None = None) -> list[dict]:
    """Добавить сотрудника или заменить карточку old (в том числе с переводом в другое подразделение)."""
    if old is not None:
        remove(departments, old)
    name = employee["department"].strip()
    target = next((d for d in departments if d["department"] == name), None)
    if target is None:
        target = {"department": name, "employees": []}
        departments.append(target)
    target["employees"].append({k: employee.get(k, "").strip() for k in FIELDS})
    target["employees"].sort(key=lambda e: e["fio"])
    return departments


def remove(departments: list[dict], employee: dict) -> list[dict]:
    """Удалить сотрудника; опустевшее подразделение тоже удаляется."""
    for d in departments:
        if d["department"] == employee.get("department"):
            d["employees"] = [e for e in d["employees"]
                              if any(e[k] != employee.get(k, "") for k in FIELDS)]
    departments[:] = [d for d in departments if d["employees"]]
    return departments


def search(departments: list[dict], text: str) -> list[dict]:
    """Подразделения только с подходящими сотрудниками (поиск по ФИО, должности, e-mail, телефону, подразделению)."""
    needle = text.strip().lower()
    if not needle:
        return departments
    result = []
    for d in departments:
        if needle in d["department"].lower():
            result.append(d)
            continue
        found = [e for e in d["employees"] if needle in " ".join(e[k] for k in FIELDS).lower()]
        if found:
            result.append({"department": d["department"], "employees": found})
    return result


def _initials(fio: str) -> tuple[str, str, str]:
    parts = fio.split()
    return (parts[0].lower() if parts else "", parts[1][:1] if len(parts) > 1 else "", parts[2][:1] if len(parts) > 2 else "")


def find_person(text: str, departments: list[dict] | None = None) -> dict | None:
    """Сотрудник, о котором говорит текст ответа: «Коммерческий директор Смирнов И.О.»,
    «Бухгалтерия (Волкова М.Н.)», «… (process@company.ru)», «Генеральный директор»."""
    if not text:
        return None
    people = all_employees(load_directory() if departments is None else departments)
    emails = {m.lower() for m in EMAIL_RE.findall(text)}
    for person in people:
        if person["email"] and person["email"].lower() in emails:
            return person
    for surname, first, middle in SHORT_NAME_RE.findall(text):
        for person in people:
            if _initials(person["fio"]) == (surname.lower(), first, middle):
                return person
    lowered = text.lower()
    for person in people:
        if person["fio"] and person["fio"].lower() in lowered:
            return person
    # Только должность («Генеральный директор») — если такая должность в справочнике одна
    head = re.sub(r"\(.*?\)", "", lowered).strip(" .")
    by_position = [p for p in people if p["position"] and p["position"].lower() == head]
    return by_position[0] if len(by_position) == 1 else None


def contact_line(person: dict | None, sep: str = " · ") -> str:
    """«Смирнов Игорь Олегович · smirnov@company.ru · +7 (495) …» — для ответа (в письме sep=", ")."""
    if not person:
        return ""
    return sep.join(p for p in (person["fio"], person["email"], person["phone"]) if p)

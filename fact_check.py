"""Проверка ответа модели: ФИО, числа, даты и номера документов должны встречаться в источниках."""
import re

_FIO_SURNAME_FIRST = re.compile(r"\b([А-ЯЁ][а-яё]{2,})\s+([А-ЯЁ])\.\s?([А-ЯЁ])\.")
_FIO_INITIALS_FIRST = re.compile(r"\b([А-ЯЁ])\.\s?([А-ЯЁ])\.\s?([А-ЯЁ][а-яё]{2,})")
_DATE = re.compile(r"\b\d{1,2}\.\d{1,2}\.\d{2,4}\b")
_NUMBER = re.compile(r"\d+(?:[  ]\d{3})*(?:\s*(тыс|млн))?")
_DOC_NUMBER = re.compile(r"№\s*([\w\-/]+)")
_MONTHS = ("январ", "феврал", "март", "апрел", "ма", "июн", "июл", "август", "сентябр", "октябр", "ноябр", "декабр")
_TEXT_DATE = re.compile(
    r"\b(\d{1,2})\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+(\d{4})",
    re.IGNORECASE)
_PATH = re.compile(r"(?:[A-Za-z]:|\\\\[\w.\-]+)\\[^\s,;«»\"')]+")

_SURNAME_PREFIX = 5  # «Волковой» и «Волкова» — одна фамилия в разных падежах


def _fio_keys(text: str) -> dict:
    """{(начало фамилии, инициалы): как написано в тексте}."""
    keys, taken = {}, []
    for m in _FIO_SURNAME_FIRST.finditer(text):
        keys[(m.group(1).lower().replace("ё", "е")[:_SURNAME_PREFIX], m.group(2) + m.group(3))] = m.group(0)
        taken.append(m.span())
    for m in _FIO_INITIALS_FIRST.finditer(text):
        # «Смирнов И.О. Договоры…» не считать вторым ФИО «И.О. Договоры»
        if any(start <= m.start() < end for start, end in taken):
            continue
        keys[(m.group(3).lower().replace("ё", "е")[:_SURNAME_PREFIX], m.group(1) + m.group(2))] = m.group(0)
    return keys


def _numbers(text: str) -> dict:
    """{нормализованное число: как написано}. Учитываются числа от 100 и выше."""
    # даты, пути и номера документов проверяются отдельно
    text = _PATH.sub(" ", _DOC_NUMBER.sub(" ", _TEXT_DATE.sub(" ", _DATE.sub(" ", text))))
    result = {}
    for m in _NUMBER.finditer(text):
        digits = m.group(0)[: m.start(1) - m.start(0)] if m.group(1) else m.group(0)
        value = int(re.sub(r"\D", "", digits))
        if m.group(1) == "тыс":
            value *= 1_000
        elif m.group(1) == "млн":
            value *= 1_000_000
        if value >= 100:
            result[value] = m.group(0).strip()
    return result


_UNIT_NUMBER = re.compile(
    r"\b(\d{1,3})\s*(?:рабоч\w*\s+|календарн\w*\s+)?(дн|день|дня|час|недел|месяц|экземпляр|копи|раз)\w*", re.IGNORECASE)


def _unit_numbers(text: str) -> dict:
    """Небольшие числа с единицей: «3 рабочих дня», «2 экземпляра». Обычная проверка чисел начинается с 100."""
    result = {}
    for m in _UNIT_NUMBER.finditer(text):
        unit = m.group(2).lower()
        unit = "дн" if unit in ("день", "дня") else unit
        result[(int(m.group(1)), unit)] = m.group(0)
    return result


def _dates(text: str) -> dict:
    """{ДД.ММ.ГГГГ: как написано}. Понимает «10.02.2026» и «10 февраля 2026»."""
    result = {}
    for d in _DATE.findall(text):
        day, month, year = d.split(".")
        result[f"{int(day):02d}.{int(month):02d}.{year}"] = d
    for m in _TEXT_DATE.finditer(text):
        month = next(i for i, stem in enumerate(_MONTHS, 1) if m.group(2).lower().startswith(stem))
        result[f"{int(m.group(1)):02d}.{month:02d}.{m.group(3)}"] = m.group(0)
    return result


def _paths(text: str) -> dict:
    return {m.group(0).rstrip(".").lower(): m.group(0).rstrip(".") for m in _PATH.finditer(text)}


def _doc_numbers(text: str) -> dict:
    return {m.group(1).lower().strip("-/"): m.group(0) for m in _DOC_NUMBER.finditer(text)}


def find_unverified_facts(answer: str, source_text: str) -> list[str]:
    """Список фактов из ответа, которых нет в источниках. Пустой список — проверка пройдена."""
    issues = []

    source_fio = _fio_keys(source_text)
    issues += [f"ФИО «{shown}»" for key, shown in _fio_keys(answer).items() if key not in source_fio]

    source_numbers = _numbers(source_text)
    issues += [f"число «{shown}»" for value, shown in _numbers(answer).items() if value not in source_numbers]

    source_units = _unit_numbers(source_text)
    issues += [f"срок/количество «{shown}»" for key, shown in _unit_numbers(answer).items() if key not in source_units]

    source_dates = _dates(source_text)
    issues += [f"дата «{shown}»" for key, shown in _dates(answer).items() if key not in source_dates]

    source_paths = _paths(source_text)
    issues += [f"путь «{shown}»" for key, shown in _paths(answer).items() if key not in source_paths]

    source_doc_numbers = _doc_numbers(source_text)
    issues += [f"номер «{shown}»" for key, shown in _doc_numbers(answer).items() if key not in source_doc_numbers]

    return issues

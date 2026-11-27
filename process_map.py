"""Темы вопросов с готовыми ответами (раньше — «карта процессов / карта ответственности»).

Тема: название (поле process), эксперт, слова, примеры вопросов, документы — см. topics.py. У части тем есть готовый
проверенный ответ со ссылкой на пункт регламента: на такие вопросы система отвечает без модели — точно, мгновенно
и без выдумок; если ответ зависит от суммы или подразделения — уточняет. Темы хранятся в process_map.json и
ведутся на экране «Темы и ответственные» (администратор) без правки кода.
"""
import json
import re
from pathlib import Path

from config import PROCESS_MAP_FILE
from text_processing import normalize, token_matches, tokenize

# Вопрос именно о том, КТО отвечает / подписывает (а не «как», «где», «когда»)
WHO_INTENT = re.compile(r"\b(?:кто|кем|кого|кому|чья|чей|чье|чьей|чью)\b|подписант|ответствен|уполномоч")
# Вопрос о СРОКЕ: «за сколько дней», «в какой срок», «когда нужно…»
WHEN_INTENT = re.compile(r"\bсрок|\bсколько\b|\bкогда\b|\bне позднее\b|\bдн(?:ей|я)\b|\bнедел")
_INTENTS = {"who": WHO_INTENT, "when": WHEN_INTENT, "any": re.compile("")}
# Вопрос о конкретных датах («с 1 по 14 декабря», «на 05.12») — карта даёт общее правило регламента, а на эти даты
# могут действовать временные письма и приказы (замещение на время отпуска). Такие вопросы идут через поиск.
_DATED = re.compile(r"\b\d{1,2}\.\d{2}\b|\b(?:январ|феврал|март|апрел|ма[яй]|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*")


def named_departments(query: str, entry: dict) -> list[str]:
    """Подразделения записи, названные в самом вопросе («…в ДБРЦТ?», «а в Технической дирекции?»).
    aliases — регулярные выражения по тексту в нижнем регистре («тех\\w*\\.?\\s+дирекц», «\\bиб\\b»)."""
    text = normalize(query)
    return [r["department"] for r in entry.get("by_department", [])
            if any(re.search(alias, text) for alias in r.get("aliases", [re.escape(normalize(r["department"]))]))]

_AMOUNT = re.compile(
    r"(\d{1,3}(?:[  ]\d{3})+|\d+(?:[.,]\d+)?)\s*"
    r"(млн\.?|миллион\w*|тыс\.?|тысяч\w*|т\.\s?р\.?)?\s*"
    r"(руб\w*\.?|р\.|₽)?",
)
_MULTIPLIERS = {"млн": 1_000_000, "мил": 1_000_000, "тыс": 1_000, "т.": 1_000}


def load_process_map(path: Path = PROCESS_MAP_FILE) -> list[dict]:
    with open(path, encoding="utf-8") as file:
        return json.load(file)


def save_process_map(entries: list[dict], path: Path = PROCESS_MAP_FILE) -> None:
    """Сохранить темы (экран «Темы и ответственные»). Запись — через временный файл: файл не бывает недописанным."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def extract_amount(text: str) -> float | None:
    """Сумма из вопроса: «300 000 руб.», «750 тыс.», «1,2 млн», «900 000 ₽». Числа без валюты/множителя не считаются."""
    for m in _AMOUNT.finditer(normalize(text)):
        number, multiplier, currency = m.groups()
        if not multiplier and not currency:
            continue
        value = float(re.sub(r"[  ]", "", number).replace(",", "."))
        if multiplier:
            value *= next(v for k, v in _MULTIPLIERS.items() if multiplier.startswith(k))
        return value
    return None


def format_amount(value: float) -> str:
    whole = int(round(value))
    return f"{whole:,}".replace(",", " ")


def match_process(query: str, process_map: list[dict] | None = None) -> dict | None:
    """Запись карты, подходящая к вопросу, или None. Нужен вопрос того вида, на который отвечает запись
    («кто…» — по умолчанию, «when» — о сроке), и все группы слов записи.
    Слова записи — начала слов: «подпис» подходит к «подписывать», «подписание», «подпись»."""
    text = normalize(query)
    if _DATED.search(text):
        return None
    process_map = load_process_map() if process_map is None else process_map
    terms = set(tokenize(query))

    def has(stem: str) -> bool:
        return any(token_matches(term, stem) for term in terms)

    best, best_score = None, 0
    for entry in process_map:
        if not (entry.get("answer") or entry.get("rules") or entry.get("by_department")):
            continue  # тема без готового ответа — только для определения ответственного (topics.py)
        if not _INTENTS.get(entry.get("intent", "who"), WHO_INTENT).search(text):
            continue
        groups = entry["match"]["all"]
        if not all(any(has(stem) for stem in group) for group in groups):
            continue
        if any(has(stem) for stem in entry["match"].get("none", [])):
            continue
        if len(groups) > best_score:
            best, best_score = entry, len(groups)
    return best


def processes_in(query: str, process_map: list[dict] | None = None) -> set[str]:
    """Процессы карты, к которым относится вопрос (по словам записи, без требования «кто…»).
    Нужно, чтобы показывать противоречия только по теме вопроса: вопрос о шаблоне договора
    не относится к «Подписанию агентского договора» (в записи — «none»: «шаблон»)."""
    process_map = load_process_map() if process_map is None else process_map
    terms = set(tokenize(query))

    def has(stem: str) -> bool:
        return any(token_matches(term, stem) for term in terms)

    return {entry["process"] for entry in process_map
            if all(any(has(stem) for stem in group) for group in entry["match"]["all"])
            and not any(has(stem) for stem in entry["match"].get("none", []))}


def _resolve_by_department(entry: dict, query: str, sources: list[dict], department) -> dict | None:
    """Ответ зависит от подразделения (сроки и ответственные в регламентах разных подразделений разные).
    Подразделение названо в вопросе или одно из подразделений спрашивающего есть в записи — ответ для него;
    иначе — все варианты и уточняющий вопрос «для какого подразделения?»."""
    from actuality import asked_for  # ленивый импорт: actuality — модуль базы знаний

    def active(rule):
        doc = next((s for s in sources if s["id"] == rule["basis"]), None)
        return doc if doc is not None and doc["status"] == "Действует" else None

    result = {"process": entry["process"], "stage": entry["stage"], "amount": None, "by_department": True}
    named = named_departments(query, entry)
    mine = [r["department"] for r in entry["by_department"] if r["department"] in asked_for(department)]
    target = named[0] if len(named) == 1 else (mine[0] if not named and len(mine) == 1 else None)
    rule = next((r for r in entry["by_department"] if r["department"] == target), None)
    if rule:
        doc = active(rule)
        if doc is None:
            return None  # регламент этого подразделения не действует — пусть отвечает обычный конвейер
        return {**result, "basis_doc": doc, "basis_docs": [{**doc, "points": rule.get("points", [])}],
                "department": rule["department"], "named": bool(named), "needs_clarification": False,
                "responsible": rule.get("responsible", rule["department"]), "answer": rule["answer"]}
    rules = [(r, active(r)) for r in entry["by_department"]]
    rules = [(r, doc) for r, doc in rules if doc is not None]
    if not rules:
        return None
    variants = "; ".join(f"{r['department']} — {r['short']}" for r, _ in rules)
    own = sorted(asked_for(department))
    # Спрашивает сотрудник подразделения, для которого в регламентах правила нет, — говорим об этом прямо
    missing = (f"Для подразделения «{', '.join(own)}» в регламентах это не установлено. "
               if own and not mine and not named else "")
    return {**result, "basis_doc": rules[0][1],
            "basis_docs": [{**doc, "points": r.get("points", [])} for r, doc in rules],
            "department": None, "named": bool(named), "needs_clarification": True,
            "options": [r["department"] for r, _ in rules],  # варианты для кнопок «уточнить подразделение»
            "responsible": "зависит от подразделения",
            "answer": f"{missing}{entry['clarification']} {variants}.",
            "clarification_question": entry["clarification_question"]}


def resolve(entry: dict, query: str, sources: list[dict] | None = None, department=None) -> dict | None:
    """Ответ по записи карты. None — если документ-основание не найден или не действует (тогда работает обычный конвейер).
    department — подразделение(я) спрашивающего: для записей, где ответ зависит от подразделения."""
    if sources is None:
        from knowledge_base import get_knowledge_base  # ленивый импорт: база сама читает карту
        sources = get_knowledge_base().documents
    if entry.get("by_department"):
        return _resolve_by_department(entry, query, sources, department)
    basis = next((s for s in sources if s["id"] == entry["basis"]), None)
    if basis is None or basis["status"] != "Действует":
        return None

    result = {
        "process": entry["process"],
        "stage": entry["stage"],
        "basis_doc": basis,
        "amount": None,
        "needs_clarification": False,
    }

    if "rules" not in entry:
        result.update(responsible=entry["responsible"], answer=entry["answer"])
        return result

    amount = extract_amount(query)
    if amount is None:
        result.update(
            responsible="зависит от суммы договора",
            answer=entry["clarification"],
            clarification_question=entry["clarification_question"],
            needs_clarification=True,
        )
        return result

    for rule in entry["rules"]:
        if "max_amount" in rule and amount <= rule["max_amount"]:
            break
        if "over_amount" in rule and amount > rule["over_amount"]:
            break
    else:
        return None  # правила не покрывают сумму — пусть отвечает обычный конвейер

    result.update(
        amount=amount,
        responsible=rule["responsible"],
        answer=rule["answer"].format(amount=format_amount(amount)),
    )
    return result

"""Актуальность документов: граф версий, вступление в силу, приоритет источников, противоречия, пересмотр.

Статус документа вычисляется автоматически, ручная разметка не нужна:
- «Устарел» — его отменил действующий документ: в тексте нового есть «считать утратившим силу …»,
  «отменить …», «взамен …», «утрачивает силу …» со ссылкой на этот документ (тип + номер и/или год),
  либо связь указана в карточке (`supersedes`);
- «Не вступил в силу» — в тексте «вступает в силу с <даты>» (или `effective_from`) и дата ещё не наступила;
- «Действует» — во всех остальных случаях (или как указано в карточке `status`).

Противоречия: документ с более низким приоритетом (письмо, чат) по процессу из карты ответственности
называет ФИО или суммы, которых нет в документе-основании процесса (приказ). Такой документ
не используется для ответов, пока владелец не разберётся, а владельцу уходит уведомление.

Подразделения: в карточке документа можно указать, для каких подразделений он действует (`departments`;
пусто — для всех). Отмена и противоречие действуют только там, где области документов пересекаются:
приказ для отдела продаж не отменяет общий регламент для бухгалтерии. Если новый документ закрывает
область старого не целиком, старый остаётся «Действует», а для затронутых подразделений помечается
в `inactive_for` ({подразделение: {status, reason, by}}) — поиск для этих подразделений его не применяет.
"""
import re
from datetime import date

from doc_loader import format_date, parse_date
from fact_check import _fio_keys, _numbers, _unit_numbers
from text_processing import token_matches, tokenize

ACTIVE, OUTDATED, NOT_YET, CONFLICT = "Действует", "Устарел", "Не вступил в силу", "Противоречит"

# Приоритет источников: приказ > регламент > письмо > чат
PRIORITY = {"Приказ": 5, "Распоряжение": 5, "Регламент": 4, "Положение": 4, "Порядок": 4, "Инструкция": 4,
            "Ответ службы качества": 4,  # проверенный специалистом ответ на вопрос, которого не было в регламентах
            "Документ": 3, "Корпоративная почта": 2, "Рабочий чат": 1}

_SUPERSEDE_PHRASES = re.compile(
    r"утратившим\w*\s+силу|утрачива\w+\s+силу|\bотменить\b|\bотменяется\b|\bвзамен\b", re.IGNORECASE)
_TARGET_TYPES = [("регламент", "Регламент"), ("приказ", "Приказ"), ("положени", "Положение"),
                 ("инструкци", "Инструкция"), ("распоряжени", "Распоряжение"), ("порядок", "Порядок")]
_EFFECTIVE = re.compile(r"вступает\s+в\s+(?:законную\s+)?силу\s+с\s+(\d{1,2}\.\d{1,2}\.\d{4}|\d{1,2}\s+[а-я]+\s+\d{4})",
                        re.IGNORECASE)


# Вопрос об актуальности: «ещё действует?», «отменили ли?», «Петров ещё подписывает?», «когда вступает в силу?»
STATUS_QUESTION = re.compile(
    r"(?i)\b(?:ещё|еще)\s+(?:действ|подписыва|работа|применя|согласовыва)|действует\s+ли|\bотмен\w*\s+ли\b|"
    r"утратил\w*\s+(?:ли\s+)?сил|\bв\s+силе\b|актуал\w*\s+ли|вступа\w*\s+в\s+силу|вступит\s+в\s+силу")


def scope(doc: dict) -> frozenset:
    """Подразделения документа; пустое множество — для всех."""
    return frozenset(doc.get("departments") or ())


def covers(a: frozenset, b: frozenset) -> bool:
    """Область a целиком включает область b (a — для всех, или все подразделения b есть в a)."""
    return not a or (bool(b) and b <= a)


def overlap(a: frozenset, b: frozenset) -> bool:
    return not a or not b or bool(a & b)


def affected(a: frozenset, b: frozenset) -> frozenset:
    """Подразделения из области b, которых касается документ с областью a (когда a не покрывает b целиком)."""
    return a & b if b else a


def asked_for(department) -> frozenset:
    """Подразделения спрашивающего: одно («Бухгалтерия»), несколько (список) или никакого (None, «»)."""
    if not department:
        return frozenset()
    if isinstance(department, str):
        return frozenset([department])
    return frozenset(d for d in department if d)


def applies_to(doc: dict, department) -> bool:
    """Документ действует хотя бы для одного из подразделений (не указаны — без ограничения)."""
    wanted, departments = asked_for(department), scope(doc)
    return not wanted or not departments or bool(departments & wanted)


def status_for(doc: dict, department) -> tuple[str, str, str | None]:
    """(статус, причина, кем отменён) документа для подразделения (или нескольких: документ применяется,
    если действует хотя бы для одного из них)."""
    wanted = asked_for(department)
    relevant = (wanted & scope(doc)) if scope(doc) else wanted
    inactive = doc.get("inactive_for") or {}
    special = [inactive[d] for d in sorted(relevant) if d in inactive]
    if not special or len(special) < len(relevant):  # хотя бы для одного подразделения документ действует как обычно
        return doc["status"], doc.get("status_reason", ""), doc.get("superseded_by")
    return special[0]["status"], special[0]["reason"], special[0].get("by")


def _for_departments(departments) -> str:
    return ", ".join(f"«{d}»" for d in sorted(departments))


def supersede_point(doc: dict) -> str | None:
    """Номер пункта, в котором документ отменяет другой («п. 3»)."""
    for chunk in doc.get("chunks", []):
        if chunk["point"] and _SUPERSEDE_PHRASES.search(chunk["text"]):
            return chunk["point"]
    return None


def priority(doc: dict) -> int:
    return PRIORITY.get(doc["source_type"], 3)


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.;!?])\s+|\n", text) if s.strip()]


def find_superseded(doc: dict, others: list[dict]) -> list[dict]:
    """Документы, которые `doc` отменяет по тексту («Старый регламент 2024 года считать утратившим силу»)."""
    doc_date = parse_date(doc["date"])
    found = []
    for sentence in _sentences(doc["full_text"]):
        if not _SUPERSEDE_PHRASES.search(sentence):
            continue
        low = sentence.lower()
        target_types = {name for word, name in _TARGET_TYPES if re.search(rf"\b{word}", low)}
        numbers = {n.lower() for n in re.findall(r"№\s*([\w\-/]+)", sentence)}
        years = set(re.findall(r"\b(20\d\d)\b", sentence))
        if not target_types and not numbers:
            continue
        for other in others:
            if other["id"] == doc["id"]:
                continue
            other_date = parse_date(other["date"])
            if doc_date and other_date and other_date >= doc_date:
                continue  # отменить можно только более ранний документ
            if target_types and other["source_type"] not in target_types:
                continue
            if numbers and other.get("number", "").lower() not in numbers:
                continue
            if years and not any(y in other["date"] or y in other["title"] for y in years):
                continue
            if not numbers and not years:
                continue  # «регламент» без номера и года — слишком неопределённо
            if other not in found:
                found.append(other)
    return found


def effective_from(doc: dict) -> date | None:
    if doc.get("effective_from"):
        return parse_date(doc["effective_from"])
    m = _EFFECTIVE.search(doc["full_text"])
    return parse_date(m.group(1)) if m else None


def compute_statuses(docs: list[dict], today: date) -> None:
    """Проставляет status, status_reason, supersedes, superseded_by, effective_from_date, priority."""
    by_id = {d["id"]: d for d in docs}
    for doc in docs:
        doc["priority"] = priority(doc)
        doc["effective_from_date"] = effective_from(doc)
        doc["supersedes"] = []
        doc["superseded_by"] = None
        doc["inactive_for"] = {}

    # Граф версий: связи из карточек + найденные в тексте (только между документами с пересекающейся областью)
    for doc in docs:
        targets = [by_id[i] for i in doc["supersedes_declared"] if i in by_id] + find_superseded(doc, docs)
        for target in targets:
            if target["id"] not in doc["supersedes"] and overlap(scope(doc), scope(target)):
                doc["supersedes"].append(target["id"])

    for doc in docs:
        start = doc["effective_from_date"]
        if doc["declared_status"] == OUTDATED:
            doc["status"], doc["status_reason"] = OUTDATED, "отмечен как устаревший в карточке документа"
        elif start and start > today:
            doc["status"], doc["status_reason"] = NOT_YET, f"вступит в силу с {format_date(start)}"
        else:
            doc["status"], doc["status_reason"] = ACTIVE, ""

    # Отмена действует, только если отменяющий документ сам уже действует
    for doc in docs:
        if doc["status"] != ACTIVE:
            continue
        for target_id in doc["supersedes"]:
            target = by_id[target_id]
            if not (target["status"] == ACTIVE or target["declared_status"] == OUTDATED):
                continue
            reason = f"утратил силу: {doc['title']} от {doc['date']}"
            if covers(scope(doc), scope(target)):
                target["status"] = OUTDATED
                target["superseded_by"] = doc["id"]
                target["status_reason"] = reason
            else:  # новый документ — только для части подразделений: для остальных старый действует
                departments = affected(scope(doc), scope(target))
                for department in departments:
                    target["inactive_for"][department] = {"status": OUTDATED, "by": doc["id"],
                                                          "reason": f"{reason} (для {_for_departments([department])})"}
                note = f"для {_for_departments(departments)} утратил силу: {doc['title']} от {doc['date']}"
                target["status_reason"] = "; ".join(filter(None, [target.get("status_reason"), note]))


def _doc_matches_process(doc: dict, entry: dict) -> bool:
    tokens = set(tokenize(doc["full_text"]))
    groups = entry["match"]["all"]
    if not all(any(token_matches(t, stem) for t in tokens for stem in group) for group in groups):
        return False
    return not any(token_matches(t, stem) for t in tokens for stem in entry["match"].get("none", []))


def find_conflicts(docs: list[dict], process_map: list[dict]) -> list[dict]:
    """Противоречия с документом-основанием процесса. Помечает документ статусом «Противоречит»."""
    by_id = {d["id"]: d for d in docs}
    conflicts = []
    # У записи может быть несколько оснований — регламенты разных подразделений («by_department»)
    pairs = [(entry, base) for entry in process_map
             for base in dict.fromkeys(([entry["basis"]] if entry.get("basis") else [])
                                       + [r["basis"] for r in entry.get("by_department", [])])]
    for entry, base_id in pairs:
        basis = by_id.get(base_id)
        if not basis or basis["status"] != ACTIVE:
            continue
        basis_fio = _fio_keys(basis["full_text"])
        basis_numbers = _numbers(basis["full_text"])
        basis_terms = _unit_numbers(basis["full_text"])
        for doc in docs:
            if doc["id"] == basis["id"] or doc["status"] != ACTIVE or doc["priority"] >= basis["priority"]:
                continue
            if doc["source_type"] == "Ответ службы качества":
                continue  # проверенный специалистом ответ дополняет документы, а не противоречит им
            if not overlap(scope(basis), scope(doc)):
                continue  # документы для разных подразделений друг другу не противоречат
            if not _doc_matches_process(doc, entry):
                continue
            facts = [shown for key, shown in _fio_keys(doc["full_text"]).items() if key not in basis_fio]
            facts += [shown for value, shown in _numbers(doc["full_text"]).items() if value not in basis_numbers]
            # Сроки: «5 рабочих дней» в чате при «3 рабочих днях» в регламенте
            facts += [shown for key, shown in _unit_numbers(doc["full_text"]).items()
                      if key[1] in ("дн", "час", "недел") and key not in basis_terms]
            if not facts:
                continue
            whole = covers(scope(basis), scope(doc))
            departments = sorted(scope(doc) if whole else affected(scope(basis), scope(doc)))
            if not whole:  # где документ уже отменён (устарел), противоречие не нужно
                departments = [d for d in departments if d not in doc.get("inactive_for", {})]
                if not departments:
                    continue
            where = "" if whole else f" Касается подразделений: {_for_departments(departments)}."
            conflict = {
                "departments": departments,  # для кого противоречие важно; пусто — для всех
                "key": f"conflict:{doc['id']}:{basis['id']}:{'|'.join(sorted(facts))}",
                "doc_id": doc["id"],
                "basis_id": basis["id"],
                "process": entry["process"],
                "facts": facts,
                "owner": doc["owner"],
                "text": (f"{doc['source_type']} «{doc['title']}» от {doc['date']} противоречит документу "
                         f"«{basis['title']}» по процессу «{entry['process']}»: {', '.join(facts)} нет в основании. "
                         f"До уточнения применяется {basis['source_type'].lower()} (более высокий приоритет).{where}"),
                # Короткий вариант — для плашки над ответом
                "short": (f"«{doc['title']}» от {doc['date']} расходится с {basis['source_type'].lower()}ом "
                          f"№{basis['number']} ({', '.join(facts)}) — не применяется, владелец уведомлён.")
                if basis.get("number") else
                         (f"«{doc['title']}» от {doc['date']} расходится с «{basis['title']}» "
                          f"({', '.join(facts)}) — не применяется, владелец уведомлён."),
            }
            conflicts.append(conflict)
            reason = f"противоречит «{basis['title']}» ({', '.join(facts)}); не применяется до уточнения"
            if whole:
                doc["status"], doc["status_reason"] = CONFLICT, reason
            else:  # основание — только для части подразделений: для остальных документ применяется
                for department in departments:
                    doc.setdefault("inactive_for", {})[department] = {"status": CONFLICT, "by": basis["id"],
                                                       "reason": f"{reason} (для {_for_departments([department])})"}
                note = f"для {_for_departments(departments)} {reason}"
                doc["status_reason"] = "; ".join(filter(None, [doc.get("status_reason"), note]))
    return conflicts


def review_reminders(docs: list[dict], today: date, remind_days: int) -> list[dict]:
    """Напоминания владельцам: срок пересмотра скоро или прошёл."""
    reminders = []
    for doc in docs:
        review = parse_date(doc.get("review_date") or "")
        if not review or doc["status"] == OUTDATED:
            continue
        days = (review - today).days
        if days > remind_days:
            continue
        when = f"через {days} дн." if days > 0 else ("сегодня" if days == 0 else f"просрочен на {-days} дн.")
        reminders.append({
            "key": f"review:{doc['id']}:{doc['review_date']}",
            "doc_id": doc["id"],
            "owner": doc["owner"],
            "text": f"Пересмотр «{doc['title']}» — {when} ({doc['review_date']}). Владелец: {doc['owner']}.",
        })
    return reminders

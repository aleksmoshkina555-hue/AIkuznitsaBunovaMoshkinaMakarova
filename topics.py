"""Темы вопросов и ответственные.

Тема — запись в process_map.json (раньше «карта процессов»): название, эксперт, слова, примеры вопросов, документы.
У части тем есть готовый проверенный ответ (поля answer / rules / by_department) — по нему система отвечает сама,
без модели. Если ответа нет, вопрос уходит специалисту — тому, кто отвечает за тему.

Тема вопроса определяется по трём признакам (первый сработавший):
  1. слова темы (как в готовых ответах: «смет» + «срок»);
  2. близость по смыслу к примерам вопросов темы (bge-m3; не ниже EXAMPLE_MIN_SIMILARITY и заметно ближе второй темы);
  3. найденный документ входит в документы темы.
Ответственный: эксперт темы → владелец самого подходящего документа (если он есть в справочнике «Сотрудники») →
дежурный методолог (ESCALATION_CONTACT).
"""
from config import ESCALATION_CONTACT
from embeddings import cosine
from employees import find_person
from process_map import load_process_map
from text_processing import token_matches, tokenize

EXAMPLE_MIN_SIMILARITY = 0.72  # близость вопроса к примеру темы, с которой тема засчитывается
EXAMPLE_MARGIN = 0.03          # насколько лучшая тема должна быть ближе второй


def has_ready_answer(entry: dict) -> bool:
    """У темы есть готовый ответ (иначе тема только для определения ответственного)."""
    return bool(entry.get("answer") or entry.get("rules") or entry.get("by_department"))


def _by_words(query: str, topics: list[dict]) -> dict | None:
    terms = set(tokenize(query))

    def has(stem: str) -> bool:
        return any(token_matches(t, stem) for t in terms)

    best, best_score = None, 0
    for topic in topics:
        groups = (topic.get("match") or {}).get("all") or []
        if not groups or not all(any(has(s) for s in g) for g in groups):
            continue
        if any(has(s) for s in (topic.get("match") or {}).get("none", [])):
            continue
        if len(groups) > best_score:
            best, best_score = topic, len(groups)
    return best


def _by_examples(query: str, topics: list[dict], embedder) -> tuple[dict | None, float]:
    """Тема с самым близким примером вопроса (близость, если засчитана)."""
    with_examples = [t for t in topics if t.get("examples")]
    if embedder is None or not getattr(embedder, "available", False) or not with_examples:
        return None, 0.0
    texts = [query] + [e for t in with_examples for e in t["examples"]]
    vectors = embedder.embed(texts)
    if not vectors or vectors[0] is None:
        return None, 0.0
    question, rest, scores = vectors[0], iter(vectors[1:]), []
    for topic in with_examples:
        best = max((cosine(question, v) for v in (next(rest) for _ in topic["examples"]) if v is not None), default=0.0)
        scores.append((best, topic))
    scores.sort(key=lambda s: s[0], reverse=True)
    top, second = scores[0][0], (scores[1][0] if len(scores) > 1 else 0.0)
    if top >= EXAMPLE_MIN_SIMILARITY and top - second >= EXAMPLE_MARGIN:
        return scores[0][1], round(top, 2)
    return None, round(top, 2)


def detect_topic(query: str, docs: list[dict] | None = None, embedder=None, topics: list[dict] | None = None) -> dict | None:
    """Тема вопроса: {"topic": запись, "how": как определена} или None."""
    topics = load_process_map() if topics is None else topics
    topic = _by_words(query, topics)
    if topic:
        return {"topic": topic, "how": "по словам темы"}
    topic, similarity = _by_examples(query, topics, embedder)
    if topic:
        return {"topic": topic, "how": f"по примерам вопросов (близость {similarity:.2f})".replace(".", ",")}
    for doc in (docs or [])[:1]:  # самый подходящий документ — в документах темы
        topic = next((t for t in topics if doc["id"] in (t.get("documents") or [])), None)
        if topic:
            return {"topic": topic, "how": f"по документу «{doc['title']}»"}
    return None


def _department_expert(topic: dict, query: str, department) -> tuple[str, str] | None:
    """Эксперт темы по подразделению (у тем «по подразделениям» — свой эксперт у каждого): названное в вопросе
    подразделение или подразделение спрашивающего."""
    from actuality import asked_for  # ленивый импорт: модуль базы знаний
    from process_map import named_departments
    rules = [r for r in topic.get("by_department", []) if r.get("expert")]
    if not rules:
        return None
    named = named_departments(query, topic)
    wanted = set(named) if named else set(asked_for(department))
    match = [r for r in rules if r["department"] in wanted]
    return (match[0]["expert"], match[0]["department"]) if len(match) == 1 else None


def assign(query: str, docs: list[dict] | None = None, embedder=None, topics: list[dict] | None = None,
           department=None) -> dict:
    """Кому передать вопрос без ответа: {"assignee", "reason", "topic"}. department — подразделение(я) спрашивающего."""
    found = detect_topic(query, docs, embedder, topics)
    if found:
        topic = found["topic"]
        by_department = _department_expert(topic, query, department)
        if by_department:
            return {"assignee": by_department[0], "topic": topic["process"],
                    "reason": f"эксперт темы «{topic['process']}» в подразделении «{by_department[1]}» "
                              f"(тема определена {found['how']})"}
        if topic.get("expert"):
            return {"assignee": topic["expert"], "topic": topic["process"],
                    "reason": f"эксперт темы «{topic['process']}» (тема определена {found['how']})"}
    topic_name = found["topic"]["process"] if found else None
    for doc in (docs or [])[:1]:
        person = find_person(doc.get("owner", ""))
        if person:
            contact = f"{person['fio']} <{person['email']}>" if person.get("email") else person["fio"]
            return {"assignee": contact, "topic": topic_name,
                    "reason": f"владелец документа «{doc['title']}», который ближе всего к вопросу"}
    return {"assignee": ESCALATION_CONTACT, "topic": topic_name,
            "reason": "тема не определена — дежурный методолог" if not topic_name
            else f"у темы «{topic_name}» не указан эксперт — дежурный методолог"}

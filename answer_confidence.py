"""Уверенность в ответе: небольшая цветная плашка рядом с ответом.

  зелёная «Уверен»          — счёт ≥ порога «зелёный» (90);
  жёлтая  «Сомневается»     — счёт ≥ порога «жёлтый» (75);
  красная «Нужно проверить» — счёт выше порога скрытия (65);
  счёт не выше порога скрытия — ответ НЕ показывается: вопрос уходит специалисту (см. withhold).
  Ответа нет (передано специалисту) или запрос отклонён — серая плашка «Ответа нет».

Счёт (0–100) = базовое значение по способу ответа ± поправки по признакам из результата агента:
насколько найденный документ похож на вопрос по смыслу (bge-m3) и по словам, есть ли противоречие,
нужно ли уточнение, подтвердила ли модель-проверяющий, что ответ — на вопрос. Все числа — параметры
(PARAMS): администратор меняет их в веб-версии («Администрирование» → «Уверенность ответа»).
Рекомендуемые значения проверены на эталонном наборе (eval/questions.jsonl), см. ОПИСАНИЕ «Замечания 14».
"""

# ключ: (подпись, пояснение, рекомендуемое значение, минимум, максимум, шаг)
PARAMS = {
    "green_threshold": ("Порог «Уверен» (зелёный)", "Счёт не ниже — зелёная плашка", 90, 2, 100, 1),
    "yellow_threshold": ("Порог «Сомневается» (жёлтый)", "Счёт не ниже — жёлтая; ниже — красная «Нужно проверить»", 75, 1, 99, 1),
    "hide_threshold": ("Порог скрытия ответа", "Счёт не выше — ответ не показывается, вопрос уходит специалисту", 65, 0, 98, 1),
    "base_process_map": ("Готовый ответ по теме", "Базовый счёт: правило из действующего приказа, без модели", 95, 0, 100, 1),
    "base_actuality": ("Ответ по графу версий", "Базовый счёт: «действует ли документ» — по отменам и датам, без модели", 95, 0, 100, 1),
    "base_quality": ("Ответ службы качества из базы", "Базовый счёт: на этот вопрос уже ответила служба качества, ответ взят из базы знаний без модели", 90, 0, 100, 1),
    "base_model": ("Ответ модели", "Базовый счёт: модель прошла проверку фактов и «ответ по вопросу»", 70, 0, 100, 1),
    "base_sources": ("Показан текст документа", "Базовый счёт: ответ модели не прошёл проверку, показан пункт документа", 40, 0, 100, 1),
    "similarity_high": ("Сходство по смыслу — высокое", "Близость вопроса и документа-основания (bge-m3, 0–1), начиная с которой — прибавка", 0.70, 0, 1, 0.01),
    "similarity_bonus": ("Прибавка за высокое сходство по смыслу", "Сколько добавить к счёту", 10, 0, 50, 1),
    "similarity_low": ("Сходство по смыслу — низкое", "Ниже этого — штраф (документ найден в основном по словам)", 0.55, 0, 1, 0.01),
    "similarity_penalty": ("Штраф за низкое сходство по смыслу", "Сколько вычесть из счёта", 15, 0, 50, 1),
    "coverage_high": ("Совпадение слов — высокое", "Доля слов вопроса (с синонимами), найденных в основании, начиная с которой — прибавка", 0.75, 0, 1, 0.01),
    "coverage_bonus": ("Прибавка за высокое совпадение слов", "Сколько добавить к счёту", 10, 0, 50, 1),
    "coverage_low": ("Совпадение слов — низкое", "Ниже этого — штраф", 0.40, 0, 1, 0.01),
    "coverage_penalty": ("Штраф за низкое совпадение слов", "Сколько вычесть из счёта", 15, 0, 50, 1),
    "judge_penalty": ("Штраф: проверяющий не подтвердил", "Модель-проверяющий не дала вердикт (ответ пропущен без подтверждения)", 15, 0, 50, 1),
    "many_sources_penalty": ("Штраф: модель сослалась на 3 и более документа", "Ответ собран из нескольких источников — выше риск смешения", 5, 0, 50, 1),
    "conflict_penalty": ("Штраф за противоречие по теме", "По теме вопроса есть документ, противоречащий основанию", 5, 0, 50, 1),
    "clarification_penalty": ("Штраф: нужно уточнение", "Ответ зависит от суммы, а она не указана", 15, 0, 100, 1),
}
DEFAULTS = {key: spec[2] for key, spec in PARAMS.items()}
LABELS = {"green": "Уверен", "yellow": "Сомневается", "red": "Нужно проверить", "hidden": "Ответ скрыт",
          "none": "Ответа нет"}


def validate(params: dict) -> tuple[dict, list[str]]:
    """Проверка значений от администратора: числа в допустимых пределах, жёлтый порог ниже зелёного."""
    clean, errors = {}, []
    for key, (label, _, default, low, high, step) in PARAMS.items():
        value = params.get(key, default)
        try:
            value = float(value)
        except (TypeError, ValueError):
            errors.append(f"«{label}»: нужно число")
            continue
        if not low <= value <= high:
            errors.append(f"«{label}»: от {low} до {high}")
            continue
        clean[key] = int(round(value)) if float(step).is_integer() else round(value, 2)
    if not errors and clean["yellow_threshold"] >= clean["green_threshold"]:
        errors.append("порог «Сомневается» должен быть ниже порога «Уверен»")
    if not errors and clean["hide_threshold"] >= clean["yellow_threshold"]:
        errors.append("порог скрытия ответа должен быть ниже порога «Сомневается»")
    return clean, errors


def _basis(result: dict) -> list[dict]:
    return result.get("basis_docs") or (result.get("active_docs") or [])[:1]


def assess(result: dict, params: dict | None = None) -> dict:
    """{"color": green|yellow|red|hidden|none, "label", "score": 0–100, "reasons": [что повлияло]}.

    hidden — счёт не выше порога скрытия: ответ показывать нельзя (run_query заменит его передачей специалисту);
    none — ответа нет: передано специалисту или запрос отклонён."""
    p = {**DEFAULTS, **(params or {})}
    source = result.get("answer_source")
    withheld = result.get("withheld")
    if withheld:  # ответ уже скрыт и передан специалисту
        return {"color": "hidden", "label": LABELS["hidden"], "score": withheld.get("score", 0),
                "reasons": list(withheld.get("reasons", [])) + [withheld.get("note", "ответ не показан")]}
    if result.get("blocked"):
        return {"color": "none", "label": LABELS["none"], "score": 0, "reasons": ["запрос отклонён фильтром безопасности"]}
    if source == "escalation" or not result.get("found", True):
        return {"color": "none", "label": LABELS["none"], "score": 0,
                "reasons": ["ответа в действующих документах нет — вопрос у специалиста"]}

    base = {"process_map": ("base_process_map", "готовый ответ по теме"),
            "actuality": ("base_actuality", "ответ по графу версий документов"),
            "quality": ("base_quality", "ответ службы качества из базы знаний"),
            "model": ("base_model", "ответ модели прошёл проверки")}.get(source, ("base_sources", "показан текст документа"))
    score = p[base[0]]
    reasons = [f"{base[1]}: {score}"]

    def adjust(delta, why):
        nonlocal score
        if delta:
            score += delta
            reasons.append(f"{why}: {'+' if delta > 0 else '−'}{abs(delta)}")

    basis = _basis(result)
    # Сходство и совпадение слов есть только у документов из поиска (у ответа по правилу основание — сам приказ)
    if source in ("model", "sources") and basis and "coverage" in basis[0]:
        similarity = max((d.get("similarity") or 0) for d in basis)
        coverage = max((d.get("coverage") or 0) for d in basis)
        if similarity > 0:  # поиск по смыслу включён
            if similarity >= p["similarity_high"]:
                adjust(p["similarity_bonus"], f"высокое сходство по смыслу ({similarity:.2f})")
            elif similarity < p["similarity_low"]:
                adjust(-p["similarity_penalty"], f"низкое сходство по смыслу ({similarity:.2f})")
        if coverage >= p["coverage_high"]:
            adjust(p["coverage_bonus"], f"почти все слова вопроса есть в основании ({coverage:.0%})")
        elif coverage < p["coverage_low"]:
            adjust(-p["coverage_penalty"], f"мало слов вопроса в основании ({coverage:.0%})")
    if source == "model":
        if "не дала вердикт" in (result.get("relevance") or ""):
            adjust(-p["judge_penalty"], "модель-проверяющий не подтвердила ответ")
        if len(basis) >= 3:
            adjust(-p["many_sources_penalty"], f"ответ собран из {len(basis)} документов")
    if result.get("conflicts"):
        adjust(-p["conflict_penalty"], "по теме есть противоречащий документ")
    if result.get("needs_clarification"):
        adjust(-p["clarification_penalty"], "нужно уточнить сумму")

    score = max(0, min(100, round(score)))
    if score >= p["green_threshold"]:
        color = "green"
    elif score >= p["yellow_threshold"]:
        color = "yellow"
    elif score > p["hide_threshold"]:
        color = "red"
    else:
        color = "hidden"
    return {"color": color, "label": LABELS[color], "score": score, "reasons": reasons}


def withhold(result: dict, verdict: dict, hide_threshold: int, escalation_id: str | None = None,
             email_draft: str = "", contact: str = "") -> dict:
    """Ответ с низкой уверенностью → результат «передано специалисту»: текст ответа и основания не сохраняются
    и не показываются. escalation_id — номер, под которым вопрос зарегистрирован у специалиста
    (None — старая запись из истории: только скрыть, повторно не регистрировать)."""
    note = f"уверенность {verdict['score']} из 100 — не выше порога {hide_threshold}: ответ не показан"
    registered = f" Запрос зарегистрирован под номером {escalation_id}." if escalation_id else ""
    kept = {key: result[key] for key in ("search_mode", "latency", "tokens", "logs") if key in result}
    return {**kept, "found": False, "blocked": False, "active_docs": [], "basis_docs": [], "inactive_docs": [],
            "conflicts": [], "fact_issues": [], "needs_clarification": False,
            "relevance": "ответ не показан из-за низкой уверенности",
            "answer": "Система не уверена в найденном ответе, поэтому ответ не показан — вопрос передан специалисту."
                      + registered,
            "answer_source": "escalation", "responsible": contact, "responsible_label": "Передано",
            "escalation_id": escalation_id, "escalation_reason": f"Низкая уверенность в ответе ({verdict['score']} из 100)",
            "email_draft": email_draft,
            "withheld": {"score": verdict["score"], "reasons": verdict["reasons"], "note": note,
                         "answer_source": result.get("answer_source")}}

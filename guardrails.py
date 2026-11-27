"""Фильтр входящих писем: признаки попытки изменить инструкции агента (prompt injection).

Это только первый уровень защиты. Основная защита — архитектурная (см. agent.py):
письмо передаётся модели как данные в разделителях, а модель видит только top-k найденных документов.
"""
import re
import unicodedata

# Латинские буквы, похожие на кириллические («игнoрируй» с латинской o).
_LATIN_TO_CYR = str.maketrans({
    "a": "а", "c": "с", "e": "е", "o": "о", "p": "р", "x": "х", "y": "у", "k": "к", "m": "м",
    "t": "т", "h": "н", "b": "в", "u": "и", "n": "п", "r": "г",
})

_INVISIBLE = re.compile(r"[­​-‏⁠-⁤﻿]")
_HAS_CYR = re.compile(r"[а-я]")
_WORD = re.compile(r"\w+")

# До 5 слов между командой и объектом: «не учитывай [все] [прошлые] указания»
_GAP = r"(?:\W+\w+){0,5}?\W+"

_RU_VERBS = (
    r"(?:игнорир\w*|проигнорир\w*|(?<!не\s)забуд\w*|не\s+учитыва\w*|не\s+обраща\w*\s+внимани\w*"
    r"|сброс\w*)"  # «отмени», «нарушение», «обойти» не берём: это обычные слова в вопросах о регламентах
)
_RU_OBJECTS = (
    r"(?:инструкц\w*|указани\w*|промпт\w*|ограничени\w*|правил\w*|настройк\w*|систем\w*\s+сообщени\w*)"
)

RULES = [
    ("команда отменить инструкции", re.compile(rf"\b{_RU_VERBS}{_GAP}{_RU_OBJECTS}")),
    ("запрос системных инструкций", re.compile(
        r"\b(?:системн\w*\s+(?:промпт\w*|инструкц\w*|сообщени\w*)"
        r"|(?:покажи|выведи|повтори|напиши)\w*\W+(?:\w+\W+){0,2}?(?:сво\w+|тво\w+)\W+(?:инструкц\w*|промпт\w*|правил\w*))")),
    ("выгрузка всей базы", re.compile(
        r"\b(?:выведи|выгрузи|покажи|перечисли|распечатай|скопируй|отдай)\w*\W+(?:\w+\W+){0,2}?"
        r"(?:всю\W+баз\w*|все\W+(?:документ\w*|источник\w*|файл\w*|приказ\w*|регламент\w*)"
        r"|весь\W+(?:контекст|список)\w*|содержим\w*\W+баз\w*)")),
    ("смена роли агента", re.compile(
        r"\b(?:ты\s+теперь|теперь\s+ты|представь\W+что\s+ты|веди\s+себя\s+как|притворись|режим\w*\s+разработчик\w*)\b")),
    ("prompt injection (en)", re.compile(
        r"\b(?:(?:ignore|disregard|forget|override|bypass)\W+(?:\w+\W+){0,3}?"
        r"(?:instructions?|prompts?|rules|guidelines|context)"
        r"|system\W+prompt|you\s+are\s+now|act\s+as\s+(?:an?\s+)?(?:dan|developer|admin)|jailbreak|developer\s+mode)\b")),
]


def normalize_for_guard(text: str) -> str:
    """lower, ё→е, без невидимых символов, латинские двойники → кириллица в словах с кириллицей."""
    text = unicodedata.normalize("NFKC", text)
    text = _INVISIBLE.sub("", text).lower().replace("ё", "е")

    def fix_mixed(match: re.Match) -> str:
        word = match.group(0)
        return word.translate(_LATIN_TO_CYR) if _HAS_CYR.search(word) else word

    text = _WORD.sub(fix_mixed, text)
    return re.sub(r"\s+", " ", text)


def check_prompt_injection(text: str) -> str | None:
    """Возвращает название сработавшего правила или None, если признаков атаки нет."""
    normalized = normalize_for_guard(text)
    for rule_name, pattern in RULES:
        if pattern.search(normalized):
            return rule_name
    return None

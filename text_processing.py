"""Обработка русского текста для поиска: нормализация, лемматизация, стоп-слова, синонимы.

Лемматизация — pymorphy3 (MIT): «подписывает» → «подписывать», «агентского» → «агентский».
Если pymorphy3 не установлен, используется упрощённый стемминг (отрезание окончаний).
"""
import json
import re
from functools import lru_cache
from pathlib import Path

from config import SYNONYMS_FILE

try:
    import pymorphy3
    _MORPH = pymorphy3.MorphAnalyzer()
except ImportError:  # работа без pymorphy3 — упрощённый стемминг
    _MORPH = None

LEMMATIZER = "pymorphy3" if _MORPH else "стемминг"

# Слова, которые встречаются почти в любом письме и не несут смысла для поиска (в начальной форме).
STOP_WORDS = {
    # приветствия и вежливость
    "здравствуйте", "здравствовать", "привет", "добрый", "день", "утро", "вечер", "коллега", "уважаемый",
    "подсказать", "сказать", "пожалуйста", "спасибо", "заранее", "благодарить", "просить", "помочь",
    "просьба", "вопрос",
    # вопросительные и служебные слова
    "кто", "что", "как", "где", "когда", "куда", "откуда", "почему", "зачем", "какой", "который", "сейчас",
    "теперь", "нужно", "надо", "можно", "нельзя", "это", "этот", "тот", "для", "при", "про", "или", "она",
    "они", "оно", "мой", "весь", "есть", "быть", "также", "тоже", "только", "если", "чтобы", "уже", "ещё",
    "еще", "под", "над", "без", "через", "после", "перед", "между", "очень", "так", "там", "тут", "здесь",
    "данный", "наш", "ваш", "свой", "год", "этот",
    # формы, которые pymorphy3 не приводит к начальной форме
    "его", "мне", "меня", "нам", "нас", "вас", "вам",
}

# --- упрощённый стемминг (запасной вариант без pymorphy3) ---
_ENDINGS = sorted({
    "ывает", "ивает", "иями", "ями", "ами", "ией", "ием", "ого", "его", "ому", "ему", "ыми", "ими",
    "ешь", "ете", "ает", "яет", "ует", "ают", "яют", "уют", "ать", "ять", "ить", "еть", "уть",
    "ться", "тся", "ся", "сь",
    "ов", "ев", "ей", "ий", "ый", "ой", "ая", "яя", "ое", "ее", "ие", "ые", "ую", "юю", "ым", "им",
    "ом", "ем", "ых", "их", "ия", "ья", "ье", "ью", "ии", "ам", "ям", "ах", "ях", "ет", "ит", "ут",
    "ют", "ат", "ят", "ла", "ло", "ли",
    "а", "я", "о", "е", "ы", "и", "у", "ю", "ь", "й",
}, key=len, reverse=True)


def normalize(text: str) -> str:
    return text.lower().replace("ё", "е")


def _stem(word: str) -> str:
    for ending in _ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= 4:
            return word[: -len(ending)]
    return word


@lru_cache(maxsize=50_000)
def lemma(word: str) -> str:
    """Начальная форма слова (в нижнем регистре, ё → е)."""
    if _MORPH is None:
        return _stem(word)
    return normalize(_MORPH.parse(word)[0].normal_form)


def tokenize(text: str) -> list[str]:
    """Текст → начальные формы значимых слов (без стоп-слов, от 3 букв; числа сохраняются)."""
    words = re.findall(r"[а-яa-z0-9]+", normalize(text))
    result = []
    for word in words:
        if len(word) < 3 or word in STOP_WORDS:
            continue
        base = lemma(word)
        if base not in STOP_WORDS:
            result.append(base)
    return result


def token_matches(token: str, prefix: str) -> bool:
    """Совпадение по началу слова: «подписывать» и «подписание» подходят под «подпис»."""
    return token.startswith(prefix)


# --- синонимы ---

_synonyms_cache = {"mtime": None, "groups": {}}


def load_synonyms(path: Path = SYNONYMS_FILE) -> dict[str, set[str]]:
    """{начальная форма: множество синонимов (тоже начальные формы)}. Файл перечитывается при изменении."""
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        return {}
    if _synonyms_cache["mtime"] == mtime:
        return _synonyms_cache["groups"]

    with open(path, encoding="utf-8") as file:
        raw_groups = json.load(file)
    groups: dict[str, set[str]] = {}
    for group in raw_groups:
        # Каждая строка группы может быть фразой («электронный документооборот») — берём все её слова
        lemmas = {t for phrase in group for t in tokenize(phrase)}
        for item in lemmas:
            groups.setdefault(item, set()).update(lemmas - {item})
    _synonyms_cache.update(mtime=mtime, groups=groups)
    return groups


def expand_query(terms: list[str]) -> list[set[str]]:
    """Каждое слово запроса → группа {слово + синонимы}. Документ «покрывает» слово, если в нём есть любое из группы."""
    synonyms = load_synonyms()
    groups, seen = [], set()
    for term in terms:
        if term in seen:
            continue
        seen.add(term)
        groups.append({term} | synonyms.get(term, set()))
    return groups

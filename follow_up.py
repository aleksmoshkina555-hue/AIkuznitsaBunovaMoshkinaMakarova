"""Уточняющий вопрос → самостоятельный вопрос с учётом предыдущего.

Небольшой модели (qwen2.5:1.5b) нельзя отдавать всю переписку — она путается. Поэтому уточнение
сначала переписывается в один полный вопрос, и уже он идёт в обычный конвейер (поиск, карта, модель,
проверки, плашка уверенности):

  «Кто подписывает договор на 300 тыс.?» + «а на 800?»  →  «Кто подписывает договор на 800 тыс.?»
  «Где шаблон агентского договора?» + «а кто его согласует?» →  модель: «Кто согласует агентский договор?»

Порядок: (1) уточнение самостоятельное (длинное, без «а …», «его», «там») — берётся как есть, это новый
вопрос в той же цепочке; (2) уточняется сумма — подставляется правилом, без модели; (3) иначе модель
переписывает вопрос, а результат проверяется: в нём не должно быть чисел, которых не было в двух вопросах;
(4) модель недоступна или ответила плохо — склейка «вопрос; уточнение: …» (поиску по словам этого хватает).
"""
import math
import re

from process_map import _AMOUNT, extract_amount, format_amount
from text_processing import normalize

MAX_STEPS = 5          # уточнений в одной цепочке; дальше — новый вопрос
MAX_MINUTES = 30       # цепочка «остывает»: через полчаса уточнение считается новым вопросом

_STARTS = re.compile(r"^\s*(?:а|и|но|ещё|еще|тогда|также|то есть|а если|а для|а на|а в|а при|а у)\b", re.IGNORECASE)
_REFERS = re.compile(r"\b(?:он|она|оно|они|его|её|ее|их|ему|ей|им|него|неё|нее|них|этот|эта|это|эти|этого|этой|этом|"
                     r"этим|этих|тот|та|те|того|той|том|там|туда|оттуда|такой|такая|такие|такого|такую|тоже|так же)\b",
                     re.IGNORECASE)
_BARE_NUMBER = re.compile(r"(?<![\d.,])(\d{1,3}(?:[  ]\d{3})+|\d+(?:[.,]\d+)?)(?![\d.,])")
_NUMBERS = re.compile(r"\d+(?:[.,]\d+)?")
_SUM_CONTEXT = re.compile(r"догов|сумм|руб|подпис|оплат|стоим|цен|лимит")
# «а в Технической дирекции?», «а для ДБРЦТ?» — меняется только подразделение (название — с заглавной буквы)
_PLACE_FOLLOW = re.compile(r"^\s*(?:а|и)?\s*(в|во|для|у)\s+([А-ЯЁA-Z][\w-]*(?:\s+[\w-]+){0,3})\s*[?.!]*\s*$")
_PLACE_TAIL = re.compile(r"\s+(?:в|во|для|у)\s+[А-ЯЁA-Z][\w-]*(?:\s+[\w-]+){0,2}\s*[?.!]*\s*$")
_ASKS = re.compile(r"\b(?:кто|когда|сколько|где|как|что|какой|какая|какие|почему|зачем)\b", re.IGNORECASE)

REWRITE_PROMPT = (
    "Перепиши уточнение сотрудника в один полный самостоятельный вопрос с учётом предыдущего вопроса. "
    "Не отвечай на вопрос, не добавляй новых фактов, имён и чисел. Ответь только переписанным вопросом, одной строкой.")


def needs_context(text: str) -> bool:
    """Уточнение опирается на предыдущий вопрос: «а на 800?», «а кто его согласует?», «а там что?»."""
    words = re.findall(r"[\wА-Яа-яЁё]+", text)
    return bool(_STARTS.search(text) or _REFERS.search(text) or len(words) <= 4)


def _amount_span(text: str):
    """(начало, конец, множитель) первой суммы в тексте — «300 тыс.», «300 000 руб.»."""
    for m in _AMOUNT.finditer(text):
        number, multiplier, currency = m.groups()
        if multiplier or currency:
            return m.start(), m.end(), (multiplier or "")
    return None


def _follow_amount(follow: str, previous: str) -> float | None:
    """Сумма из уточнения; «а на 800?» после «300 тыс.» — 800 тыс. (тот же множитель)."""
    value = extract_amount(follow)
    if value is not None:
        return value
    bare = _BARE_NUMBER.search(normalize(follow))
    if not bare:
        return None
    number = float(re.sub(r"[  ]", "", bare.group(1)).replace(",", "."))
    span = _amount_span(normalize(previous))
    if not span:
        # Суммы в первом вопросе не было («Кто подписывает договор?» → «укажите сумму» → «а на 800?»):
        # по деловой привычке число до 1000 — это тысячи рублей. Как поняли — видно на экране, можно поправить
        if not _SUM_CONTEXT.search(previous.lower()):
            return None
        return number * 1_000 if number < 1_000 else number
    multiplier = span[2]
    if multiplier.startswith("млн") or multiplier.startswith("мил"):
        return number * 1_000_000
    if multiplier.startswith("тыс") or multiplier.startswith("т."):
        return number * 1_000
    # «300 000 руб.» → «а на 800?»: порядок — ближайший к прежней сумме (800 тыс., а не 800 руб.)
    before = extract_amount(previous) or number
    return min((number * k for k in (1, 1_000, 1_000_000)), key=lambda v: abs(math.log10(v / before)) if v else 99)


def _with_amount(previous: str, amount: float) -> str:
    span = _amount_span(previous)
    shown = f"{format_amount(amount)} руб."
    if span:
        return previous[:span[0]] + shown + previous[span[1]:]
    return f"{previous.rstrip(' ?.')} на сумму {shown}?"


def _numbers(text: str) -> set[str]:
    return {n.replace(",", ".") for n in _NUMBERS.findall(re.sub(r"(?<=\d)[  ](?=\d{3})", "", text))}


def _combine(previous: str, follow: str) -> str:
    return f"{previous.rstrip(' ?.')}; уточнение: {follow.strip()}"


def rewrite(previous: str, follow: str, llm=None) -> tuple[str, str]:
    """(самостоятельный вопрос, как получен): «как есть», «сумма», «модель», «склейка».
    llm(messages) -> str — вызов модели; None — без модели."""
    follow = follow.strip()
    if not previous or not needs_context(follow):
        return follow, "как есть"
    amount = _follow_amount(follow, previous)
    rest = re.sub(r"\d+|млн|миллион\w*|тыс\w*|руб\w*|₽|\b(?:а|на|и|если|сумм\w*|договор\w*)\b", "", follow.lower())
    rest = re.sub(r"\W+", "", rest)  # знаки препинания («Сумма договора: 750 тыс.») не в счёт
    if amount is not None and not rest:  # «а на 800?», «а если 1,2 млн?» — меняется только сумма
        return _with_amount(previous, amount), "сумма"
    place = _PLACE_FOLLOW.match(follow)
    if place and not _ASKS.search(follow) and not re.search(r"\d", follow):
        # «За сколько дней … в ДБРЦТ?» + «а в Технической дирекции?» → прежнее подразделение заменяется новым
        base = _PLACE_TAIL.sub("", previous).rstrip(" ?.!")
        return f"{base} {place.group(1)} {place.group(2)}?", "подразделение"
    if llm is not None:
        try:
            text = llm([{"role": "system", "content": REWRITE_PROMPT},
                        {"role": "user", "content": f"Предыдущий вопрос: {previous}\nУточнение: {follow}"}])
        except Exception:
            text = ""
        text = (text or "").strip().strip("«»\"").splitlines()[0].strip() if (text or "").strip() else ""
        if 5 <= len(text) <= 300 and _numbers(text) <= _numbers(previous) | _numbers(follow):
            return text, "модель"
    return _combine(previous, follow), "склейка"

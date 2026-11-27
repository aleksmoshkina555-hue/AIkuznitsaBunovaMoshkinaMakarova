"""Оценка качества агента на эталонном наборе вопросов.

Запуск (из корня проекта):
    python eval/run_eval.py                         # модель из LLM_MODEL / config.py
    python eval/run_eval.py --model qwen2.5:7b      # другая модель
    python eval/run_eval.py --models qwen2.5:1.5b qwen2.5:7b   # сравнение моделей

Отчёт печатается в консоль и сохраняется в eval/results/<модель>_<время>.json.
Журналы агента во время оценки пишутся во временную папку, рабочие журналы не засоряются.
"""
import argparse
import json
import os
import re
import statistics
import sys
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL_DIR.parent))
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="corp_agent_eval_"))
# Ожидания набора привязаны к дате: приказ №131-П вступает в силу 01.12.2026
os.environ.setdefault("KB_TODAY", "23.11.2026")

import agent  # noqa: E402

ROUTE_NAMES = {"answer": "ответ", "clarification": "уточнение", "escalation": "эскалация", "blocked": "блокировка", "any": "любой"}


def load_questions(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as file:
        return [json.loads(line) for line in file if line.strip()]


def actual_route(result: dict) -> str:
    if result.get("blocked"):
        return "blocked"
    if not result.get("found"):
        return "escalation"
    if result.get("needs_clarification"):
        return "clarification"
    return "answer"


def mentions(text: str, phrase: str) -> bool:
    """Запрещённое слово есть в тексте: с начала слова и не как часть отчества
    («Петров» — да, «Петрову» — да, «Михаил Петрович» из строки контактов — нет)."""
    pattern = rf"(?<![а-яёa-z0-9]){re.escape(phrase.lower())}(?!ич|на|ович|овна|евич|евна)"
    return re.search(pattern, text.lower()) is not None


def check(question: dict, result: dict) -> dict:
    expect = question["expect"]
    route = actual_route(result)
    answer = result.get("answer", "")
    full_text = f"{answer}\n{result.get('email_draft', '')}"
    basis_ids = [d["id"] for d in result.get("basis_docs", [])]

    checks = {"route": expect["route"] in ("any", route)}
    if expect.get("basis"):
        checks["basis"] = bool(basis_ids) and basis_ids[0] in expect["basis"]
    if expect.get("contains"):
        # Проверяется только ответ по существу: шаблон письма сам подставляет контакт и основание
        checks["contains"] = all(s.lower() in answer.lower() for s in expect["contains"])
    checks["forbidden"] = not any(mentions(full_text, s) for s in expect.get("forbidden", []))
    if expect.get("email_contains"):
        # Например, письмо об эскалации должно упомянуть приказ, который ещё не вступил в силу
        checks["email"] = all(s.lower() in result.get("email_draft", "").lower() for s in expect["email_contains"])
    if "conflict" in expect:
        checks["conflict"] = bool(result.get("conflicts")) == expect["conflict"]

    return {
        "id": question["id"],
        "category": question["category"],
        "question": question["body"],
        "expected_route": expect["route"],
        "route": route,
        "basis": basis_ids,
        "answer_source": result.get("answer_source"),
        "answer": answer,
        "fact_issues": result.get("fact_issues", []),
        "escalation_reason": result.get("escalation_reason"),
        "relevance": result.get("relevance"),
        "search_mode": result.get("search_mode"),
        "latency": result.get("latency", 0.0),
        "tokens": result.get("tokens", 0),
        "checks": checks,
        "passed": all(checks.values()),
    }


def percent(part: int, total: int) -> str:
    return f"{100 * part / total:.0f} % ({part}/{total})" if total else "—"


def summarize(rows: list[dict]) -> dict:
    def rate(key):
        relevant = [r for r in rows if key in r["checks"]]
        return sum(r["checks"][key] for r in relevant), len(relevant)

    llm_rows = [r for r in rows if r["answer_source"] in ("model", "sources")]
    false_blocks = sum(1 for r in rows if r["route"] == "blocked" and r["expected_route"] != "blocked")
    missed_blocks = sum(1 for r in rows if r["expected_route"] == "blocked" and r["route"] != "blocked")
    return {
        "search_mode": rows[0].get("search_mode") if rows else None,
        "total": len(rows),
        "passed": sum(r["passed"] for r in rows),
        "escalation_reasons": dict(Counter(r["escalation_reason"] for r in rows if r["escalation_reason"])),
        "route": rate("route"),
        "basis": rate("basis"),
        "contains": rate("contains"),
        "forbidden": rate("forbidden"),
        # Самая важная метрика безопасности: ответ дошёл бы до сотрудника, но он неверный
        # (эскалация вместо ответа — тоже ошибка, но безопасная: вопрос попадает к человеку)
        "wrong_answers": sum(1 for r in rows if r["route"] in ("answer", "clarification") and not r["passed"]),
        "safe_escalations": sum(1 for r in rows if r["route"] == "escalation" and r["expected_route"] in ("answer", "clarification")),
        "false_blocks": false_blocks,
        "missed_blocks": missed_blocks,
        "answer_sources": dict(Counter(r["answer_source"] for r in rows)),
        "model_answers_accepted": sum(1 for r in llm_rows if r["answer_source"] == "model"),
        "model_calls": len(llm_rows),
        "avg_latency_all": round(statistics.mean(r["latency"] for r in rows), 2),
        "avg_latency_model": round(statistics.mean(r["latency"] for r in llm_rows), 2) if llm_rows else None,
        "avg_tokens_model": round(statistics.mean(r["tokens"] for r in llm_rows)) if llm_rows else None,
    }


def print_report(model: str, rows: list[dict], summary: dict) -> None:
    print(f"\n=== Модель: {model} ===")
    for r in rows:
        mark = "OK  " if r["passed"] else "FAIL"
        failed = ", ".join(k for k, v in r["checks"].items() if not v)
        print(f"{mark} {r['id']:<9} {ROUTE_NAMES[r['route']]:<10} {r['answer_source']:<12} {r['latency']:>6.1f} с"
              + (f"   не пройдено: {failed}" if failed else ""))
        if not r["passed"]:
            print(f"      вопрос: {r['question']}")
            print(f"      ответ:  {r['answer'][:200]}")
            if r["basis"]:
                print(f"      основание: {', '.join(r['basis'])}")

    s = summary
    print(f"\nПоиск: {s['search_mode']}")
    print(f"Причины эскалаций: {s['escalation_reasons']}")
    print("\nИтог:")
    print(f"  Вопросов пройдено полностью:      {percent(s['passed'], s['total'])}")
    print(f"  Верный маршрут (ответ/уточнение/эскалация/блокировка): {percent(*s['route'])}")
    print(f"  Верное основание (документ):       {percent(*s['basis'])}")
    print(f"  Верный ответ (ключевые факты):     {percent(*s['contains'])}")
    print(f"  Без устаревших/запрещённых фактов: {percent(*s['forbidden'])}")
    print(f"  Неверных ответов сотруднику: {s['wrong_answers']}; "
          f"передано человеку вместо ответа (безопасные ошибки): {s['safe_escalations']}")
    print(f"  Ложные блокировки: {s['false_blocks']}, пропущенные атаки: {s['missed_blocks']}")
    print(f"  Источник ответа: {s['answer_sources']}")
    if s["model_calls"]:
        print(f"  Ответ модели принят проверкой фактов: {percent(s['model_answers_accepted'], s['model_calls'])}")
        print(f"  Среднее время с моделью: {s['avg_latency_model']} с, токенов: {s['avg_tokens_model']}")
    print(f"  Среднее время по всем вопросам: {s['avg_latency_all']} с")


def run(model: str, questions: list[dict]) -> tuple[list[dict], dict]:
    agent.LLM_MODEL = model
    rows = []
    for i, q in enumerate(questions, 1):
        print(f"[{model}] {i}/{len(questions)} {q['id']}", file=sys.stderr, flush=True)
        result = agent.process_corporate_query("Тестовый Сотрудник <test@company.ru>", q.get("subject", ""), q["body"])
        rows.append(check(q, result))
    return rows, summarize(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Оценка агента на эталонном наборе вопросов")
    parser.add_argument("--questions", type=Path, default=EVAL_DIR / "questions.jsonl")
    parser.add_argument("--model", help="одна модель (по умолчанию из config.py)")
    parser.add_argument("--models", nargs="+", help="несколько моделей для сравнения")
    args = parser.parse_args()

    models = args.models or [args.model or agent.LLM_MODEL]
    questions = load_questions(args.questions)
    results_dir = EVAL_DIR / "results"
    results_dir.mkdir(exist_ok=True)

    summaries = {}
    for model in models:
        try:
            rows, summary = run(model, questions)
        except (agent.LLMUnavailableError, agent.LLMModelNotFoundError) as exc:
            print(f"\nМодель {model} недоступна: {type(exc).__name__}: {exc}")
            continue
        print_report(model, rows, summary)
        summaries[model] = summary
        out = results_dir / f"{model.replace(':', '_').replace('/', '_')}_{datetime.now():%Y%m%d-%H%M%S}.json"
        out.write_text(json.dumps({"model": model, "summary": summary, "rows": rows}, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        print(f"  Отчёт: {out}")

    if len(summaries) > 1:
        print("\n=== Сравнение моделей ===")
        print(f"{'Модель':<16} {'Пройдено':>10} {'Основание':>10} {'Факты':>8} {'Неверных':>9} {'Время с моделью':>16}")
        for model, s in summaries.items():
            basis = f"{s['basis'][0]}/{s['basis'][1]}"
            facts = f"{s['contains'][0]}/{s['contains'][1]}"
            print(f"{model:<16} {s['passed']:>5}/{s['total']:<4} {basis:>10} {facts:>8} {s['wrong_answers']:>9} {str(s['avg_latency_model']) + ' с':>16}")
    return 0 if summaries else 1


if __name__ == "__main__":
    sys.exit(main())

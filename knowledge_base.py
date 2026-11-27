"""База знаний: документы из папки, индекс по пунктам, гибридный поиск, актуальность и противоречия.

Индекс пересобирается автоматически, когда в папке добавлен, изменён или удалён файл или карточка
(проверка отпечатка папки при каждом запросе — это быстро).

Поиск гибридный:
- по словам: начальные формы (pymorphy3) + синонимы, BM25 по фрагментам (пунктам документов);
- по смыслу: косинусная близость эмбеддингов bge-m3;
- итоговая оценка = (1 − w)·слова + w·смысл, затем небольшая поправка на приоритет источника
  (приказ > регламент > письмо > чат).
Документ считается найденным, если в нём есть не меньше SEARCH_MIN_COVERAGE слов вопроса
(с учётом синонимов) ИЛИ близость по смыслу не ниже EMBED_MIN_SIMILARITY.
"""
import json
import math
import threading
from datetime import date, datetime

from actuality import ACTIVE, applies_to, compute_statuses, find_conflicts, review_reminders, status_for
from config import (EMBED_MIN_SIMILARITY, EMBED_WEIGHT, KB_BACKGROUND_RELOAD, KB_TODAY, KNOWLEDGE_DIR, NOTIFICATIONS_FILE,
                    REVIEW_REMIND_DAYS, SEARCH_CHUNKS_PER_DOC, SEARCH_MIN_COVERAGE, SEARCH_TOP_K)
from doc_loader import folder_signature, load_document, parse_date, scan_folder
from embeddings import Embedder, cosine
from journal import append_jsonl, get_logger
from text_processing import LEMMATIZER, expand_query, tokenize

logger = get_logger()

_BM25_K1, _BM25_B = 1.5, 0.75
_PRIORITY_BOOST = 0.03  # +3 % к оценке за каждую ступень приоритета
_PREFER_BOOST = 1.15    # документы предыдущего ответа в цепочке уточнений


def today() -> date:
    return parse_date(KB_TODAY) or date.today()


class KnowledgeBase:
    def __init__(self, folder=KNOWLEDGE_DIR, embedder: Embedder | None = None, process_map: list | None = None,
                 today_date: date | None = None, notify: bool = True, cached_only: bool = False):
        """cached_only — векторы только из кеша (быстро): новые фрагменты ищутся по словам, пока их векторы
        считаются в фоне (см. get_knowledge_base); missing_vectors — сколько фрагментов ещё без вектора."""
        self.folder = folder
        self.embedder = embedder if embedder is not None else Embedder()
        self.cached_only = cached_only
        self.missing_vectors = 0
        self._process_map = process_map
        self._today = today_date
        self.notify = notify
        self.signature = None
        self.documents: list[dict] = []
        self.by_id: dict[str, dict] = {}
        self.chunks: list[dict] = []
        self.conflicts: list[dict] = []
        self.reminders: list[dict] = []
        self.reload()

    # ---------- индекс ----------

    @property
    def today(self) -> date:
        return self._today or today()

    @property
    def has_vectors(self) -> bool:
        return any(c.get("vector") is not None for c in self.chunks)

    @property
    def search_mode(self) -> str:
        if self.embedder.available and self.has_vectors:
            pending = f"; по смыслу ещё считаются {self.missing_vectors} фрагм. — пока по словам" if self.missing_vectors else ""
            return f"гибридный: слова ({LEMMATIZER}) + смысл ({self.embedder.model}){pending}"
        return f"по словам ({LEMMATIZER})"

    def refresh_if_changed(self) -> bool:
        if folder_signature(self.folder) != self.signature:
            self.reload()
            return True
        return False

    def reload(self) -> None:
        from process_map import load_process_map  # ленивый импорт: process_map сам использует базу

        self.signature = folder_signature(self.folder)
        documents, seen = [], set()
        for path in scan_folder(self.folder):
            doc = load_document(path)
            if doc is None:
                continue
            if doc["id"] in seen:
                logger.warning("kb: повторный id %s в %s — файл пропущен", doc["id"], path.name)
                continue
            seen.add(doc["id"])
            documents.append(doc)

        compute_statuses(documents, self.today)
        process_map = self._process_map if self._process_map is not None else load_process_map()
        self.conflicts = find_conflicts(documents, process_map)
        self.reminders = review_reminders(documents, self.today, REVIEW_REMIND_DAYS)
        self.documents = documents
        self.by_id = {d["id"]: d for d in documents}
        self._build_chunks()
        if self.notify:
            self._send_notifications()
        logger.info("kb: загружено документов %d, фрагментов %d, противоречий %d, напоминаний %d, поиск: %s",
                    len(documents), len(self.chunks), len(self.conflicts), len(self.reminders), self.search_mode)

    def _build_chunks(self) -> None:
        chunks = []
        for doc in self.documents:
            has_points = any(c["point"] for c in doc["chunks"])
            for order, chunk in enumerate(doc["chunks"]):
                if has_points and not chunk["point"]:
                    continue  # «шапка» (заголовок, дата, ответственный) уже есть в карточке и в названии фрагментов
                search_text = f"{doc['title']}. {chunk['text']}"
                chunks.append({"doc_id": doc["id"], "order": order, "point": chunk["point"], "text": chunk["text"],
                               "search_text": search_text, "tokens": tokenize(search_text)})
        vectors = self.embedder.embed([c["search_text"] for c in chunks], cached_only=self.cached_only) if chunks else None
        for i, chunk in enumerate(chunks):
            chunk["token_set"] = set(chunk["tokens"])
            chunk["vector"] = vectors[i] if vectors else None
        self.missing_vectors = sum(1 for c in chunks if c["vector"] is None) if vectors else 0
        self.chunks = chunks
        self._avg_len = (sum(len(c["tokens"]) for c in chunks) / len(chunks)) if chunks else 1.0
        self._doc_freq: dict[str, int] = {}
        for chunk in chunks:
            for token in chunk["token_set"]:
                self._doc_freq[token] = self._doc_freq.get(token, 0) + 1

    def _send_notifications(self) -> None:
        """Уведомления владельцам документов (противоречия, пересмотр). Каждое — один раз."""
        try:
            sent = {json.loads(line)["key"] for line in NOTIFICATIONS_FILE.read_text(encoding="utf-8").splitlines() if line}
        except FileNotFoundError:
            sent = set()
        for kind, items in (("противоречие", self.conflicts), ("пересмотр", self.reminders)):
            for item in items:
                if item["key"] in sent:
                    continue
                append_jsonl(NOTIFICATIONS_FILE, {"time": datetime.now().isoformat(timespec="seconds"), "kind": kind,
                                                  "key": item["key"], "to": item["owner"], "text": item["text"]})
                logger.info("kb: уведомление (%s) для %s: %s", kind, item["owner"], item["text"])

    # ---------- доступ ----------

    def get_doc(self, doc_id: str) -> dict | None:
        return self.by_id.get(doc_id)

    def attention_items(self) -> list[str]:
        """Что требует внимания методологов: противоречия и пересмотр."""
        return [c["text"] for c in self.conflicts] + [r["text"] for r in self.reminders]

    def conflicts_for(self, doc_ids: set[str]) -> list[dict]:
        return [c for c in self.conflicts if c["doc_id"] in doc_ids or c["basis_id"] in doc_ids]

    # ---------- поиск ----------

    def _bm25(self, token: str, chunk: dict) -> float:
        tf = chunk["tokens"].count(token)
        if not tf:
            return 0.0
        n = len(self.chunks)
        df = self._doc_freq.get(token, 0)
        idf = math.log((n - df + 0.5) / (df + 0.5) + 1)
        return idf * tf * (_BM25_K1 + 1) / (tf + _BM25_K1 * (1 - _BM25_B + _BM25_B * len(chunk["tokens"]) / self._avg_len))

    def search(self, query: str, top_k: int = SEARCH_TOP_K, min_coverage: float = SEARCH_MIN_COVERAGE,
               source_types: set[str] | None = None, department: str | None = None,
               prefer: set[str] | None = None) -> tuple[list, list]:
        """(действующие, недействующие) документы. Недействующие: устаревшие, не вступившие в силу, противоречащие.
        source_types — искать только в документах этих типов («Приказ», «Рабочий чат»…); None — во всех.
        department — подразделение спрашивающего: документы других подразделений пропускаются, статус —
        с учётом отмен и противоречий для этого подразделения; None — без ограничения.
        prefer — id документов, на которых основан ответ на предыдущий вопрос цепочки уточнений: при прочих
        равных они выше (+15 % к оценке), но найденными должны быть по самому вопросу, как и остальные."""
        groups = expand_query(tokenize(query))
        if not groups or not self.chunks:
            return [], []

        query_vector = None
        if self.has_vectors:
            vectors = self.embedder.embed([query], use_cache=False)
            query_vector = vectors[0] if vectors else None

        # Оценка каждого фрагмента
        lexical = []
        for chunk in self.chunks:
            score = sum(max(self._bm25(t, chunk) for t in group) for group in groups if group & chunk["token_set"])
            lexical.append(score)
        max_lexical = max(lexical) or 1.0

        per_doc: dict[str, dict] = {}
        for chunk, lex in zip(self.chunks, lexical):
            # у фрагмента нового документа вектора может ещё не быть (считается в фоне) — оценка только по словам
            has_vector = query_vector is not None and chunk["vector"] is not None
            similarity = cosine(query_vector, chunk["vector"]) if has_vector else 0.0
            if has_vector:
                score = (1 - EMBED_WEIGHT) * (lex / max_lexical) + EMBED_WEIGHT * similarity
            else:
                score = lex / max_lexical
            entry = per_doc.setdefault(chunk["doc_id"], {"chunks": [], "tokens": set(), "similarity": 0.0})
            entry["chunks"].append((score, chunk))
            entry["tokens"] |= chunk["token_set"]
            entry["similarity"] = max(entry["similarity"], similarity)

        min_matched = max(1, math.ceil(min_coverage * len(groups)))
        found = []
        for doc_id, entry in per_doc.items():
            matched = sum(1 for group in groups if group & entry["tokens"])
            by_words = matched >= min_matched
            by_meaning = query_vector is not None and entry["similarity"] >= EMBED_MIN_SIMILARITY
            if not (by_words or by_meaning):
                continue
            doc = self.by_id[doc_id]
            if source_types is not None and doc["source_type"] not in source_types:
                continue
            if not applies_to(doc, department):
                continue
            best = sorted(entry["chunks"], key=lambda item: item[0], reverse=True)[:SEARCH_CHUNKS_PER_DOC]
            doc_score = best[0][0] * (1 + _PRIORITY_BOOST * doc["priority"])
            if prefer and doc_id in prefer:
                doc_score *= _PREFER_BOOST
            selected = sorted((c for _, c in best), key=lambda c: c["order"])
            record = self._record(doc, selected, doc_score, entry["similarity"], matched / len(groups))
            record["status"], record["status_reason"], record["superseded_by"] = status_for(doc, department)
            found.append((doc_score, record))

        found.sort(key=lambda item: item[0], reverse=True)
        active = [r for _, r in found if r["status"] == ACTIVE][:top_k]
        inactive = [r for _, r in found if r["status"] != ACTIVE][:top_k]
        return active, inactive

    @staticmethod
    def _record(doc: dict, chunks: list[dict], score: float, similarity: float, coverage: float) -> dict:
        """Результат поиска: карточка документа + выбранные пункты в поле content."""
        parts = [f"п. {c['point']}. {c['text']}" if c["point"] else c["text"] for c in chunks]
        return {
            "id": doc["id"], "source_type": doc["source_type"], "number": doc["number"], "title": doc["title"],
            "date": doc["date"], "owner": doc["owner"], "status": doc["status"], "status_reason": doc["status_reason"],
            "priority": doc["priority"], "path": doc["path"], "departments": list(doc.get("departments") or []),
            "content": " ".join(parts), "points": [c["point"] for c in chunks if c["point"]],
            "score": round(score, 4), "similarity": round(similarity, 4), "coverage": round(coverage, 2),
        }


# Группы источников для выбора «Искать в:» — по типу документа (тип определяется при загрузке, см. doc_loader)
SOURCE_GROUPS = {
    "Приказы": {"Приказ", "Распоряжение"},
    "Регламенты": {"Регламент", "Положение", "Инструкция", "Порядок", "Документ", "Ответ службы качества"},
    "Письма": {"Корпоративная почта"},
    "Чаты": {"Рабочий чат"},
}


def source_types_for(groups) -> set[str] | None:
    """Выбранные группы («Приказы», «Чаты») → типы документов; все группы — None (без ограничения)."""
    groups = set(groups)
    if groups >= set(SOURCE_GROUPS):
        return None
    return set().union(*(SOURCE_GROUPS[g] for g in groups if g in SOURCE_GROUPS))


_KB: KnowledgeBase | None = None
_LOCK = threading.Lock()
_VECTORS = {"running": False, "started": None}
_EMBEDDER: Embedder | None = None


def get_knowledge_base() -> KnowledgeBase:
    """Общая база для приложения; пересобирается, если файлы в папке изменились.

    KB_BACKGROUND_RELOAD=1 (веб-сервер): база пересобирается быстро — с готовыми векторами из кеша, новые
    документы сразу ищутся по словам; векторы новых фрагментов считаются в фоне, после чего база подменяется
    целиком. Ответы при этом не ждут: большой документ (сотни пунктов) обрабатывается минутами.
    Без фона (окно, тесты) — как раньше: пересборка сразу и полностью."""
    global _KB
    if not KB_BACKGROUND_RELOAD:
        if _KB is None:
            _KB = KnowledgeBase()
        else:
            _KB.refresh_if_changed()
        return _KB
    if _KB is None or folder_signature(KNOWLEDGE_DIR) != _KB.signature:
        with _LOCK:  # одна пересборка за раз; остальные запросы подождут несколько секунд и возьмут готовую
            if _KB is None or folder_signature(KNOWLEDGE_DIR) != _KB.signature:
                _KB = KnowledgeBase(folder=KNOWLEDGE_DIR, embedder=_shared_embedder(), cached_only=True)
        if _KB.missing_vectors:
            _start_vectors()
    return _KB


def _shared_embedder() -> Embedder:
    global _EMBEDDER
    if _EMBEDDER is None:
        _EMBEDDER = Embedder()
    return _EMBEDDER


def _start_vectors() -> None:
    """Фоновый расчёт векторов новых фрагментов; по готовности база подменяется полной."""
    with _LOCK:
        if _VECTORS["running"]:
            return
        _VECTORS.update(running=True, started=datetime.now())

    def work():
        global _KB
        try:
            full = KnowledgeBase(folder=KNOWLEDGE_DIR, embedder=_shared_embedder(), notify=False)
            with _LOCK:
                if _KB is None or full.signature == _KB.signature:
                    _KB = full  # за это время папка не менялась — подменяем
        except Exception:
            logger.exception("kb: фоновый расчёт векторов не удался")
        finally:
            _VECTORS["running"] = False
        current = _KB
        if current is not None and current.missing_vectors and _shared_embedder().available:
            _start_vectors()  # папка изменилась, пока считали, — досчитать новое

    threading.Thread(target=work, daemon=True, name="kb-vectors").start()


def rebuild_state() -> dict:
    """Для экрана «Документы базы»: идёт ли расчёт векторов и сколько фрагментов ещё без них."""
    kb, embedder = _KB, _EMBEDDER
    missing = kb.missing_vectors if kb else 0
    if missing and embedder is not None and embedder._cache is not None:
        # Живой счётчик: векторы пишутся в кеш порциями по 32 — сколько из недостающих ещё не посчитано
        cache = embedder._cache
        missing = sum(1 for c in kb.chunks if c["vector"] is None and embedder._key(c["search_text"]) not in cache)
    return {"running": bool(_VECTORS["running"]), "missing": missing}


def search_knowledge_base(query: str, **kwargs) -> tuple[list, list]:
    return get_knowledge_base().search(query, **kwargs)

"""Эмбеддинги для поиска по смыслу: модель bge-m3 (MIT) через локальную Ollama.

Векторы фрагментов кешируются на диске (data/embeddings_cache.json): при перезапуске пересчитываются
только новые и изменённые фрагменты. Если модель недоступна, поиск работает только по словам.
"""
import hashlib
import json
import math

from config import EMBED_CACHE_FILE, EMBED_MODEL, LLM_API_KEY, LLM_BASE_URL, LLM_TIMEOUT_SEC
from journal import get_logger

logger = get_logger()


class Embedder:
    def __init__(self, model: str = EMBED_MODEL):
        self.model = model
        self.available = bool(model)
        self.error = "" if model else "поиск по смыслу выключен (EMBED_MODEL пуст)"
        self._client = None
        self._cache = None

    # --- кеш ---
    def _load_cache(self) -> dict:
        if self._cache is None:
            try:
                self._cache = json.loads(EMBED_CACHE_FILE.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                self._cache = {}
        return self._cache

    def _save_cache(self) -> None:
        EMBED_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        EMBED_CACHE_FILE.write_text(json.dumps(self._cache), encoding="utf-8")

    def _key(self, text: str) -> str:
        return hashlib.sha1(f"{self.model}\n{text}".encode("utf-8")).hexdigest()

    # --- вызов модели ---
    def _call(self, texts: list[str]) -> list[list[float]]:
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY, timeout=LLM_TIMEOUT_SEC)
        response = self._client.embeddings.create(model=self.model, input=texts)
        return [item.embedding for item in response.data]

    def embed(self, texts: list[str], use_cache: bool = True, cached_only: bool = False) -> list | None:
        """Векторы для текстов или None, если модель недоступна (тогда поиск идёт только по словам).
        cached_only — ничего не считать, только взять готовые из кеша: у новых текстов вместо вектора None
        (так база знаний сразу ищет новый документ по словам, а векторы досчитываются в фоне)."""
        if not self.available or not texts:
            return None
        cache = self._load_cache() if use_cache else {}
        if cached_only:
            return [cache.get(self._key(t)) for t in texts]
        missing = [t for t in dict.fromkeys(texts) if self._key(t) not in cache]
        for start in range(0, len(missing), 32):
            batch = missing[start:start + 32]
            try:
                vectors = self._call(batch)
            except Exception as exc:  # нет пакета openai, Ollama не запущена, модель не скачана
                self.available = False
                self.error = f"{type(exc).__name__}: {exc}"
                logger.warning("embeddings: поиск по смыслу недоступен — %s", self.error)
                return None
            for text, vector in zip(batch, vectors):
                cache[self._key(text)] = vector
            if use_cache:
                self._save_cache()  # после каждой порции: прерванный пересчёт не начинается заново
        return [cache[self._key(t)] for t in texts]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0

"""Поиск по смыслу для долговременной памяти: multilingual-e5-large-instruct.

Зачем, замер 2026-09-23: после BM25 (803763d) нужный урок всплывал 3 раза из
9. Отбор сравнивал СЛОВА, и вопрос «поставь библиотеку, которой не хватает» не
находил урок «ставя себе пакет, проверь окружение»: общих слов нет, смысл один.
В литературе это решают плотными векторами.

Модель — intfloat/multilingual-e5-large-instruct (Wang et al., «Multilingual
E5 Text Embeddings: A Technical Report», arXiv 2402.05672). Выбор по замеру и
по ruMTEB (arXiv 2408.12503): 64.7 по русским задачам — лучшая из моделей без
видеокарты; BGE-M3 — 60.8, mE5-large — 60.4. Первой стояла mE5-small: на семи
вопросах, где нужный урок сказан другими словами, она ставила его в первую
тройку 2 раза из 7, а в её тройку лезли записи-«вопросы к агенту» — похожие
формой, не темой. Большая instruct-модель — 5 из 7. Нарезка длинных записей на
куски (MaxP, Dai & Callan, SIGIR 2019) и срезание строки вопроса маленькой не
помогли (те же 2 из 7) — дело было в самой модели.

Правила из карточки: у instruct-модели к вопросу пишется поручение
«Instruct: {задача}\\nQuery: {вопрос}», записи кодируются без приставки; у
простых e5 — «query: » и «passage: ». Векторы нормируются, текст режется на
512 токенах, косинусы кучкуются в 0.7–1.0 — смысл несёт ПОРЯДОК, не число,
поэтому никаких порогов по косинусу. Своя формулировка задачи («найди урок,
который отвечает на вопрос») замерена и оказалась хуже стандартной (4 из 7).

Слияние со словами — выпуклая сумма нормированных баллов
α·смысл + (1 − α)·BM25 (Bruch, Gai, Ingber, «An Analysis of Fusion Functions
for Hybrid Retrieval», ACM TOIS 2023, arXiv 2210.11934): она обошла
Reciprocal Rank Fusion и внутри области, и вне её, а единственный параметр
подбирается по немногим примерам. Нормировка — min-max по кандидатам.

Включение: переменная AGENT_EMBED_MODEL — путь к локальной копии модели.
Нет переменной, нет библиотеки, сбой загрузки или кодирования — поиск по
смыслу выключен, работает один BM25, как до правки. Векторы записей хранятся
на диске по отпечатку текста (рядом с моделью): короткий процесс чата не
пересчитывает всю память на каждый вопрос.
"""
from __future__ import annotations

import hashlib
import os
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

MODEL_ENV = "AGENT_EMBED_MODEL"
QUERY_PREFIX = "query: "
PASSAGE_PREFIX = "passage: "
#: Задача для instruct-модели — стандартная из её карточки.
INSTRUCT_TASK = "Given a web search query, retrieve relevant passages that answer the query"
#: Доля смысла в слиянии; подобрана замером (см. тест и сообщение коммита).
ALPHA = 0.5
#: Reciprocal Rank Fusion (Cormack, Clarke, Büttcher, SIGIR 2009) — для сравнения.
RRF_K = 60
#: Квота процессора контейнера (cgroup v2): «квота период» или «max период».
CPU_MAX_FILE = Path("/sys/fs/cgroup/cpu.max")


def cpu_budget(cpu_max: Path = CPU_MAX_FILE) -> int:
    """Потоки для torch: половина ОПЛАЧЕННЫХ ядер, а не видимых.

    Замер 2026-09-24, сервер Vast: видно 96 ядер, квота контейнера 23. torch
    брал потоки по видимым, и кампания вместе с разговором держали ~200
    потоков на 23 ядрах: оба процесса по 8 минут не сдвинулись дальше
    загрузки модели, 184 записи кодировались 207 секунд. Половина — потому
    что кампания и разговор работают одновременно.
    """
    try:
        quota, period = cpu_max.read_text(encoding="utf-8").split()[:2]
        cores = int(quota) // int(period) if quota != "max" else (os.cpu_count() or 1)
    except (OSError, ValueError):
        cores = os.cpu_count() or 1
    return max(1, cores // 2)

_lock = threading.Lock()
_model: Any = None
_model_failed = False
_vectors: dict[str, Any] = {}
_cache_file: Path | None = None
_encoder_override: Callable[[list[str]], Any] | None = None
_instruct = False


def set_encoder(fn: Callable[[list[str]], Any] | None) -> None:
    """Подменить кодировщик (тесты): fn(тексты) -> нормированные векторы."""
    global _encoder_override  # noqa: PLW0603 — подмена кодировщика в тестах
    _encoder_override = fn
    _vectors.clear()


def _encoder() -> Callable[[list[str]], Any] | None:
    global _model, _model_failed, _cache_file, _instruct  # noqa: PLW0603 — одна модель на процесс
    if _encoder_override is not None:
        return _encoder_override
    path = os.environ.get(MODEL_ENV, "").strip()
    if not path or _model_failed:
        return None
    with _lock:
        if _model is None:
            try:
                import torch
                from sentence_transformers import SentenceTransformer

                torch.set_num_threads(cpu_budget())
                _model = SentenceTransformer(path, device="cpu")
                _instruct = "instruct" in Path(path).name.lower()
                _cache_file = Path(path.rstrip("/\\") + ".cache.npz")
                _load_cache()
            except Exception:  # noqa: BLE001 — без модели работает BM25
                _model_failed = True
                return None
    return lambda texts: _model.encode(
        texts, normalize_embeddings=True, batch_size=32, show_progress_bar=False)


def _key(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8"), usedforsecurity=False).hexdigest()


def _load_cache() -> None:
    if _cache_file is None or not _cache_file.exists():
        return
    try:
        import numpy as np

        data = np.load(_cache_file, allow_pickle=False)
        for k, v in zip(data["keys"], data["vecs"], strict=True):
            _vectors[str(k)] = v
    except Exception:  # noqa: BLE001 — испорченный кэш пересчитывается
        _vectors.clear()


def _save_cache() -> None:
    if _cache_file is None or not _vectors:
        return
    try:
        import numpy as np

        keys = list(_vectors)
        tmp = _cache_file.with_suffix(".tmp.npz")
        np.savez_compressed(tmp, keys=np.array(keys), vecs=np.stack([_vectors[k] for k in keys]))
        os.replace(tmp, _cache_file)
    except Exception:  # noqa: BLE001 — кэш не обязателен: не записался — пересчитаем
        return


def _query(text: str) -> str:
    return f"Instruct: {INSTRUCT_TASK}\nQuery: {text}" if _instruct else QUERY_PREFIX + text


def _passage(text: str) -> str:
    return text if _instruct else PASSAGE_PREFIX + text


def semantic_scores(query: str, texts: Sequence[str]) -> list[float] | None:
    """Косинус каждого текста с вопросом, или None — поиск по смыслу выключен."""
    enc = _encoder()
    if enc is None or not texts or not (query or "").strip():
        return None
    try:
        import numpy as np

        keys = [_key(t) for t in texts]
        missing = [i for i, k in enumerate(keys) if k not in _vectors]
        if missing:
            fresh = enc([_passage(texts[i] or "") for i in missing])
            for i, v in zip(missing, fresh, strict=True):
                _vectors[keys[i]] = np.asarray(v, dtype=np.float32)
            _save_cache()
        q = np.asarray(enc([_query(query)])[0], dtype=np.float32)
        return [float(_vectors[k] @ q) for k in keys]
    except Exception:  # noqa: BLE001 — без смысла работает BM25
        return None


def _min_max(values: Sequence[float]) -> list[float]:
    lo, hi = min(values), max(values)
    return [0.0] * len(values) if hi <= lo else [(v - lo) / (hi - lo) for v in values]


def convex(lexical: Sequence[float], semantic: Sequence[float], alpha: float = ALPHA) -> list[float]:
    """α·смысл + (1 − α)·слова, каждое min-max нормировано (Bruch et al. 2023)."""
    lex, sem = _min_max(lexical), _min_max(semantic)
    return [alpha * s + (1.0 - alpha) * w for w, s in zip(lex, sem, strict=True)]


def rrf(*score_lists: Sequence[float], k: int = RRF_K) -> list[float]:
    """Reciprocal Rank Fusion: сумма 1/(k + место), место с единицы."""
    n = len(score_lists[0]) if score_lists else 0
    out = [0.0] * n
    for scores in score_lists:
        order = sorted(range(n), key=lambda i: -scores[i])
        for place, i in enumerate(order, start=1):
            out[i] += 1.0 / (k + place)
    return out


def fused_relevance(
    question: str, texts: Sequence[str], lexical: list[float],
) -> tuple[list[float], list[float] | None]:
    """Релевантность для отбора и смысловые баллы (None — смысл не участвовал)."""
    semantic = semantic_scores(question, texts)
    if semantic is None:
        return lexical, None
    return convex(lexical, semantic), semantic


def top_by_meaning(semantic: list[float] | None, n: int) -> frozenset[int]:
    """Номера n записей, ближайших по смыслу; пусто, если смысл выключен."""
    if not semantic or n <= 0:
        return frozenset()
    return frozenset(sorted(range(len(semantic)), key=lambda i: -semantic[i])[:n])

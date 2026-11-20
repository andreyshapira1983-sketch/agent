"""Semantic search for long-term memory: multilingual-e5-large-instruct.

    Model: intfloat/multilingual-e5-large-instruct (arXiv 2402.05672), chosen on ruMTEB.
    Queries get the instruct prefix "Instruct: {task}\nQuery: {query}", records are
    encoded plain; vectors are normalised, text cut at 512 tokens. No cosine
    thresholds: cosines cluster in 0.7-1.0, only ORDER carries meaning. Fusion with
    BM25 is a convex sum of min-max normalised scores. Enabled by AGENT_EMBED_MODEL
    (path to a local model copy); any missing piece falls back to BM25 alone.
    Record vectors are cached on disk by text fingerprint."""
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
#: Instruct-model task: the standard one from its model card (a custom wording measured worse).
INSTRUCT_TASK = "Given a web search query, retrieve relevant passages that answer the query"
#: Share of meaning in the fusion; chosen by measurement.
ALPHA = 0.5
#: Reciprocal Rank Fusion (Cormack et al., SIGIR 2009), kept for comparison.
RRF_K = 60
#: Container CPU quota (cgroup v2): "quota period" or "max period".
CPU_MAX_FILE = Path("/sys/fs/cgroup/cpu.max")


def cpu_budget(cpu_max: Path = CPU_MAX_FILE) -> int:
    """Threads for torch: half of the PAID cores, not the visible ones.

        A container may show many cores but have a small quota; half because the campaign and the chat run at once."""
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
    """Replace the encoder (tests): fn(texts) -> normalised vectors."""
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
    """Cosine of each text with the question, or None when semantic search is off."""
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
    """alpha*meaning + (1 - alpha)*words, each min-max normalised (Bruch et al. 2023)."""
    lex, sem = _min_max(lexical), _min_max(semantic)
    return [alpha * s + (1.0 - alpha) * w for w, s in zip(lex, sem, strict=True)]


def rrf(*score_lists: Sequence[float], k: int = RRF_K) -> list[float]:
    """Reciprocal Rank Fusion: sum of 1/(k + rank), rank starting at one."""
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
    """Relevance for selection and meaning scores (None when meaning did not take part)."""
    semantic = semantic_scores(question, texts)
    if semantic is None:
        return lexical, None
    return convex(lexical, semantic), semantic


def top_by_meaning(semantic: list[float] | None, n: int) -> frozenset[int]:
    """Indices of the n records nearest by meaning; empty when meaning is off."""
    if not semantic or n <= 0:
        return frozenset()
    return frozenset(sorted(range(len(semantic)), key=lambda i: -semantic[i])[:n])

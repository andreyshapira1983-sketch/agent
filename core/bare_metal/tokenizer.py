"""Byte-level BPE tokenizer — с нуля, без внешних библиотек.

Алгоритм:
    1. Базовый словарь: 8 спецтокенов + 256 байтов = 264 типа.
    2. Обучение: итеративно объединяем самую частую пару → новый тип.
    3. Кодирование: текст → UTF-8 байты → применяем merges по приоритету.
    4. Декодирование: ID → байтовые последовательности → UTF-8 строка.
"""

from __future__ import annotations

import json
import os


class BPETokenizer:
    # ── Специальные токены ────────────────────────────────────────────────
    PAD = 0
    UNK = 1
    BOS = 2
    EOS = 3
    THINK = 4       # <think>  — chain-of-thought
    ACT = 5         # <act>    — действие
    OBS = 6         # <obs>    — наблюдение
    MEM = 7         # <mem>    — обращение к памяти

    SPECIALS: dict[str, int] = {
        '<pad>': 0, '<unk>': 1, '<bos>': 2, '<eos>': 3,
        '<think>': 4, '<act>': 5, '<obs>': 6, '<mem>': 7,
    }
    N_SPECIAL = 8
    N_BYTES = 256

    def __init__(self) -> None:
        self.merges: list[tuple[int, int]] = []
        self._merge_rank: dict[tuple[int, int], int] = {}   # pair → new_id
        self._id_to_bytes: dict[int, bytes] = {}
        self.vocab_size: int = self.N_SPECIAL + self.N_BYTES
        self._build_base()

    # ── Базовый словарь ───────────────────────────────────────────────────

    def _build_base(self) -> None:
        for i in range(self.N_BYTES):
            self._id_to_bytes[self.N_SPECIAL + i] = bytes([i])

    # ── Обучение ──────────────────────────────────────────────────────────

    def train(self, text: str, target_vocab: int = 16384, verbose: bool = False) -> None:
        """Обучает BPE merges на тексте."""
        raw = text.encode('utf-8')
        # Разбиваем на чанки по ~512 байт (по границе пробелов)
        chunks: list[list[int]] = []
        buf: list[int] = []
        for b in raw:
            buf.append(self.N_SPECIAL + b)
            if b == 0x20 and len(buf) >= 256:    # пробел = граница
                chunks.append(buf)
                buf = []
        if buf:
            chunks.append(buf)

        n_merges = target_vocab - self.vocab_size
        for step in range(n_merges):
            # Считаем частоты пар
            counts: dict[tuple[int, int], int] = {}
            for seq in chunks:
                for j in range(len(seq) - 1):
                    pair = (seq[j], seq[j + 1])
                    counts[pair] = counts.get(pair, 0) + 1
            if not counts:
                break
            best = max(counts.keys(), key=counts.__getitem__)
            if counts[best] < 2:
                break

            new_id = self.vocab_size
            self.merges.append(best)
            self._merge_rank[best] = new_id
            self._id_to_bytes[new_id] = (
                self._id_to_bytes.get(best[0], b'\x00')
                + self._id_to_bytes.get(best[1], b'\x00')
            )
            self.vocab_size += 1

            # Применяем merge ко всем чанкам
            for k in range(len(chunks)):
                chunks[k] = _apply_merge(chunks[k], best, new_id)

            if verbose and (step + 1) % 500 == 0:
                freq = counts[best]
                print(f'  BPE {step + 1}/{n_merges}: '
                      f'{best} → {new_id}  freq={freq}')

    # ── Кодирование ───────────────────────────────────────────────────────

    def encode(self, text: str) -> list[int]:
        """text → list[int]  (ID токенов)."""
        if not text:
            return []
        ids = [self.N_SPECIAL + b for b in text.encode('utf-8')]
        for pair, new_id in self._merge_rank.items():
            ids = _apply_merge(ids, pair, new_id)
        return ids

    def encode_with_special(self, text: str, add_bos: bool = True,
                            add_eos: bool = False) -> list[int]:
        ids = self.encode(text)
        if add_bos:
            ids = [self.BOS] + ids
        if add_eos:
            ids = ids + [self.EOS]
        return ids

    # ── Декодирование ─────────────────────────────────────────────────────

    def decode(self, ids: list[int]) -> str:
        """list[int] → str.  Спецтокены рендерятся как <name>."""
        parts: list[bytes] = []
        inv_special = {v: k for k, v in self.SPECIALS.items()}
        for i in ids:
            if i in inv_special:
                parts.append(inv_special[i].encode('utf-8'))
            elif i in self._id_to_bytes:
                parts.append(self._id_to_bytes[i])
            # неизвестный id — пропускаем
        return b''.join(parts).decode('utf-8', errors='replace')

    # ── Persistence ───────────────────────────────────────────────────────

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        data = {
            'merges': self.merges,
            'vocab_size': self.vocab_size,
        }
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f)

    @classmethod
    def load(cls, path: str) -> BPETokenizer:
        tok = cls()
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        for pair in data['merges']:
            pt = (int(pair[0]), int(pair[1]))
            new_id = tok.vocab_size
            tok.merges.append(pt)
            tok._merge_rank[pt] = new_id
            tok._id_to_bytes[new_id] = (
                tok._id_to_bytes.get(pt[0], b'\x00')
                + tok._id_to_bytes.get(pt[1], b'\x00')
            )
            tok.vocab_size += 1
        return tok


# ── Утилита ───────────────────────────────────────────────────────────────

def _apply_merge(seq: list[int], pair: tuple[int, int], new_id: int) -> list[int]:
    """Заменяет все вхождения пары в последовательности на new_id."""
    out: list[int] = []
    i = 0
    a, b = pair
    while i < len(seq):
        if i < len(seq) - 1 and seq[i] == a and seq[i + 1] == b:
            out.append(new_id)
            i += 2
        else:
            out.append(seq[i])
            i += 1
    return out

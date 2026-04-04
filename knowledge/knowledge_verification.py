# Knowledge Verification System (верификация знаний) — Слой 46
# Архитектура автономного AI-агента
# Проверка достоверности знаний: источники, согласованность, актуальность, перекрёстная валидация.
# pylint: disable=protected-access


import time
import hashlib
from enum import Enum


class VerificationStatus(Enum):
    VERIFIED    = 'verified'     # знание проверено и достоверно
    UNVERIFIED  = 'unverified'   # не проверено
    UNCERTAIN   = 'uncertain'    # вероятно верно, но не уверены
    CONTESTED   = 'contested'    # противоречит другим знаниям
    OUTDATED    = 'outdated'     # устарело
    FALSE       = 'false'        # опровергнуто


class VerificationResult:
    """Результат верификации одного утверждения/знания."""

    def __init__(self, claim: str, status: VerificationStatus,
                 confidence: float, evidence: list[str] | None = None,
                 contradictions: list[str] | None = None, notes: str | None = None):
        self.claim = claim
        self.status = status
        self.confidence = max(0.0, min(1.0, confidence))
        self.evidence = evidence or []
        self.contradictions = contradictions or []
        self.notes = notes
        self.verified_at = time.time()

    def to_dict(self):
        return {
            'claim': self.claim,
            'status': self.status.value,
            'confidence': round(self.confidence, 3),
            'evidence': self.evidence,
            'contradictions': self.contradictions,
            'notes': self.notes,
        }


class KnowledgeVerificationSystem:
    """
    Knowledge Verification System — Слой 46.

    Функции:
        - верификация утверждений через Cognitive Core
        - перекрёстная проверка: сопоставление с другими знаниями
        - обнаружение противоречий в базе знаний
        - оценка источников: надёжность, авторитетность
        - отслеживание актуальности: устаревшие факты → переверификация
        - аудит всех верифицированных утверждений
        - разрешение конфликтов между противоречивыми знаниями

    Используется:
        - Knowledge System (Слой 2)     — верификация хранимых знаний
        - Data Lifecycle (Слой 33)      — удаление опровергнутых знаний
        - Cognitive Core (Слой 3)       — рассуждение о достоверности
        - Acquisition Pipeline (Слой 31)— проверка новых данных перед сохранением
    """

    def __init__(self, knowledge_system=None, cognitive_core=None,
                 monitoring=None):
        self.knowledge = knowledge_system
        self.cognitive_core = cognitive_core
        self.monitoring = monitoring

        self._results: dict[str, VerificationResult] = {}  # hash(claim) → result
        self._source_trust: dict[str, float] = {}          # источник → уровень доверия
        self._audit: list[dict] = []

        # Инициализируем доверие к стандартным источникам
        self._init_default_trust()

    # ── Верификация утверждений ───────────────────────────────────────────────

    def verify(self, claim: str, source: str | None = None,
               context: str | None = None) -> VerificationResult:
        """
        Верифицирует утверждение через Cognitive Core.

        Args:
            claim   — утверждение для проверки
            source  — источник утверждения
            context — дополнительный контекст

        Returns:
            VerificationResult с вердиктом и доказательствами.
        """
        claim_hash = self._hash(claim)

        # Проверяем кэш
        if claim_hash in self._results:
            cached = self._results[claim_hash]
            # Переверифицируем устаревшие
            if cached.status != VerificationStatus.OUTDATED:
                return cached

        # Доверие к источнику влияет на начальный confidence
        source_trust = self._source_trust.get(source, 0.5) if source else 0.5

        # ── Шаг 1: детерминированная проверка (всегда, без LLM) ──────────────
        det = self._verify_deterministic(claim, source_trust)

        # Если уверены детерминированно — не нужен LLM
        if det['confidence'] >= 0.85 or not self.cognitive_core:
            result = VerificationResult(
                claim=claim,
                status=det['status'],
                confidence=det['confidence'],
                evidence=det['evidence'],
                contradictions=det['contradictions'],
                notes=det['notes'],
            )
            self._store(claim_hash, result)
            self._log(f"[det] '{claim[:60]}' -> {det['status'].value} ({det['confidence']:.2f})")
            return result

        # ── Шаг 2: уточнение через LLM если есть неопределённость ───────────
        related = self._find_related_knowledge(claim)

        raw = str(self.cognitive_core.reasoning(
            f"Проверь достоверность следующего утверждения.\n\n"
            f"Утверждение: {claim}\n"
            f"Источник: {source or 'неизвестен'}\n"
            f"Контекст: {context or 'нет'}\n"
            f"Связанные знания: {related}\n\n"
            f"Ответь в формате:\n"
            f"СТАТУС: verified/unverified/uncertain/contested/outdated/false\n"
            f"УВЕРЕННОСТЬ: <0–1>\n"
            f"ДОКАЗАТЕЛЬСТВО: <что подтверждает>\n"
            f"ПРОТИВОРЕЧИЕ: <что опровергает (если есть)>\n"
            f"ПРИМЕЧАНИЕ: <дополнительно>"
        ))

        status, confidence, evidence, contradiction, notes = self._parse_result(raw)

        # Смешиваем детерминированную оценку с LLM (6:4 в пользу LLM при наличии)
        blended_conf = round(confidence * 0.6 + det['confidence'] * 0.4, 3)
        # source_trust повышает уверенность, а не снижает (раньше множитель 0.5-1.0 урезал)
        trust_boost = 0.85 + source_trust * 0.15   # [0.85 .. 1.0]
        blended_conf = min(1.0, blended_conf * trust_boost)

        result = VerificationResult(
            claim=claim,
            status=status,
            confidence=blended_conf,
            evidence=([evidence] if evidence else []) + det['evidence'],
            contradictions=([contradiction] if contradiction else []) + det['contradictions'],
            notes=notes,
        )

        self._store(claim_hash, result)
        self._log(f"[llm+det] '{claim[:60]}' -> {status.value} ({blended_conf:.2f})")
        return result

    def verify_batch(self, claims: list[str], source: str | None = None) -> list[VerificationResult]:
        """Верифицирует список утверждений."""
        return [self.verify(claim, source=source) for claim in claims]

    def quick_check(self, claim: str) -> bool:
        """Быстрая проверка: верно ли утверждение (True/False)."""
        result = self.verify(claim)
        return result.status in (VerificationStatus.VERIFIED,) \
               and result.confidence >= 0.6

    # ── Управление знаниями ───────────────────────────────────────────────────

    def verify_knowledge_base(self, sample_size: int = 20) -> dict:
        """
        Случайно выбирает и верифицирует часть базы знаний.
        Возвращает статистику.
        """
        if not self.knowledge:
            return {'error': 'Knowledge System не подключён'}

        long_term = self.knowledge._long_term
        keys = list(long_term.keys())
        import random
        sample_keys = random.sample(keys, min(sample_size, len(keys)))

        stats = {'verified': 0, 'uncertain': 0, 'false': 0, 'contested': 0}
        for key in sample_keys:
            value = str(long_term.get(key, ''))
            if len(value) < 10:
                continue
            result = self.verify(value[:500], source='knowledge_base')
            status_key = result.status.value
            if status_key in stats:
                stats[status_key] += 1

            # Риск-минимизация: не удаляем знание сразу по одному LLM-вердикту.
            # Вместо hard-delete помечаем как спорное для повторной проверки человеком/циклом.
            if result.status == VerificationStatus.FALSE and result.confidence >= 0.9:
                contradictions_count = len(result.contradictions or [])
                evidence_count = len(result.evidence or [])
                if contradictions_count > 0 and evidence_count > 0:
                    flag_key = f"verification_flag:{key}"
                    self.knowledge.store_long_term(
                        flag_key,
                        {
                            'status': 'false_suspected',
                            'confidence': result.confidence,
                            'contradictions': result.contradictions,
                            'evidence': result.evidence,
                            'flagged_at': time.time(),
                        },
                        source='verification', trust=0.9, verified=True,
                    )
                    self._log(f"Помечено на проверку (без удаления): '{key}'")

        self._log(f"Верификация базы знаний: {stats}")
        return stats

    def detect_contradictions(self, claim: str) -> list[dict]:
        """
        Ищет противоречия данному утверждению в базе знаний.
        """
        if not self.knowledge or not self.cognitive_core:
            return []

        related = self._find_related_knowledge(claim)
        if not related:
            return []

        raw = str(self.cognitive_core.reasoning(
            f"Найди противоречия в следующих знаниях относительно утверждения:\n\n"
            f"Утверждение: {claim}\n\n"
            f"Связанные знания:\n{related}\n\n"
            f"Для каждого противоречия: ПРОТИВОРЕЧИЕ: <описание>"
        ))
        import re
        contradictions = re.findall(r'ПРОТИВОРЕЧИЕ[:\s]+(.+)', raw, re.IGNORECASE)
        return [{'contradiction': c.strip()} for c in contradictions]

    def resolve_contradiction(self, claim1: str, claim2: str) -> dict:
        """
        Пытается разрешить противоречие между двумя утверждениями.
        """
        if not self.cognitive_core:
            return {'resolved': False, 'winner': None}

        raw = str(self.cognitive_core.reasoning(
            f"Разреши противоречие между двумя утверждениями:\n\n"
            f"1: {claim1}\n"
            f"2: {claim2}\n\n"
            f"Ответь:\n"
            f"ВЕРНО: <1 или 2 или оба частично>\n"
            f"ОБЪЯСНЕНИЕ: <почему>"
        ))
        import re
        winner_m = re.search(r'ВЕРНО[:\s]+(.+)', raw, re.IGNORECASE)
        expl_m = re.search(r'ОБЪЯСНЕНИЕ[:\s]+(.+)', raw, re.IGNORECASE)

        return {
            'resolved': bool(winner_m),
            'winner': winner_m.group(1).strip() if winner_m else None,
            'explanation': expl_m.group(1).strip() if expl_m else raw[:200],
            'claim1': claim1,
            'claim2': claim2,
        }

    # ── Источники ─────────────────────────────────────────────────────────────

    def set_source_trust(self, source: str, trust: float):
        """Устанавливает уровень доверия к источнику (0–1)."""
        self._source_trust[source] = max(0.0, min(1.0, trust))
        self._log(f"Доверие к источнику '{source}': {trust:.2f}")

    def get_source_trust(self, source: str) -> float:
        return self._source_trust.get(source, 0.5)

    def list_sources(self) -> dict[str, float]:
        return dict(self._source_trust)

    # ── Реестр и аудит ────────────────────────────────────────────────────────

    def get_result(self, claim: str) -> VerificationResult | None:
        return self._results.get(self._hash(claim))

    def get_all(self, status: VerificationStatus | None = None) -> list[dict]:
        results = self._results.values()
        if status:
            results = [r for r in results if r.status == status]
        return [r.to_dict() for r in results]

    def summary(self) -> dict:
        from collections import Counter
        statuses = Counter(r.status.value for r in self._results.values())
        return {
            'total_verified': len(self._results),
            'trusted_sources': len(self._source_trust),
            **dict(statuses),
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _verify_deterministic(self, claim: str, source_trust: float) -> dict:
        """
        Детерминированная верификация без LLM.

        Алгоритм:
        1. Структурный анализ: длина, наличие субъекта/предиката
        2. Временные маркеры: «сейчас», «в 2020» → возможно устаревшее
        3. Отрицательные паттерны: «никогда», «невозможно» → HIGH certainty flag
        4. Консистентность с базой знаний: Jaccard-сходство с хранимыми фактами
        5. Дублирование: claim уже сохранён → VERIFIED
        6. Противоречие: claim прямо противоречит сохранённому → CONTESTED
        """
        import re as _re

        claim_l   = claim.lower().strip()
        claim_words = set(w for w in _re.findall(r'\w+', claim_l) if len(w) > 3)
        evidence      = []
        contradictions = []
        confidence    = source_trust  # стартуем с доверия к источнику

        # ── 1. Структура утверждения ──────────────────────────────────────────
        if len(claim.split()) < 3:
            return {
                'status': VerificationStatus.UNVERIFIED,
                'confidence': 0.3,
                'evidence': [],
                'contradictions': [],
                'notes': 'Слишком короткое утверждение для верификации',
            }

        # ── 2. Временные маркеры → потенциально устаревшее ───────────────────
        _TEMPORAL = ['сейчас', 'текущий', 'актуальный', 'в 2019', 'в 2020',
                     'в 2021', 'в 2022', 'в 2023', 'currently', 'now', 'today',
                     'latest', 'recent']
        if any(t in claim_l for t in _TEMPORAL):
            confidence *= 0.75
            evidence.append('Утверждение содержит временной маркер — может устареть')

        # ── 3. Абсолютные утверждения снижают доверие ────────────────────────
        _ABSOLUTE = ['всегда', 'никогда', 'невозможно', 'обязательно', 'only',
                     'never', 'always', 'impossible', 'must']
        if any(a in claim_l for a in _ABSOLUTE):
            confidence *= 0.85
            evidence.append('Абсолютное утверждение: требует дополнительной проверки')

        # ── 4. Проверка базы знаний ───────────────────────────────────────────
        if self.knowledge and hasattr(self.knowledge, '_long_term'):
            _NEG = ['не ', 'нет ', 'без ', ' no ', ' not ', "don't", "doesn't", "never", "никогда"]
            neg_in_claim = any(n in claim_l for n in _NEG)

            best_sim       = 0.0
            best_match     = None
            best_match_key = None

            for key, value in self.knowledge._long_term.items():
                val_l     = str(value).lower()
                val_words = set(w for w in _re.findall(r'\w+', val_l) if len(w) > 3)
                if not claim_words or not val_words:
                    continue
                sim = len(claim_words & val_words) / len(claim_words | val_words)

                if sim >= 0.45:
                    # Сначала проверяем полярность — противоречие важнее совпадения
                    neg_in_stored = any(n in val_l for n in _NEG)
                    if neg_in_claim != neg_in_stored:
                        contradictions.append(f'Противоречит [{key}]: {str(value)[:80]}')
                        confidence *= 0.55
                        continue   # не считаем как подтверждение

                if sim > best_sim:
                    best_sim       = sim
                    best_match     = str(value)[:200]
                    best_match_key = key

            if not contradictions:
                if best_sim >= 0.6:
                    # Высокое сходство без противоречий -> VERIFIED
                    confidence = min(1.0, confidence + 0.3)
                    if best_match is not None:
                        evidence.append(f'Подтверждено [{best_match_key}]: {best_match[:80]}')
                    return {
                        'status': VerificationStatus.VERIFIED,
                        'confidence': round(confidence, 3),
                        'evidence': evidence,
                        'contradictions': contradictions,
                        'notes': f'Jaccard={best_sim:.2f}',
                    }
                elif best_sim >= 0.25:
                    confidence = min(0.8, confidence + 0.1)
                    evidence.append(f'Частично [{best_match_key}] (sim={best_sim:.2f})')

        # ── 5. Итоговый статус по уровню confidence ───────────────────────────
        if contradictions:
            status = VerificationStatus.CONTESTED
        elif confidence >= 0.75:
            status = VerificationStatus.VERIFIED
        elif confidence >= 0.45:
            status = VerificationStatus.UNCERTAIN
        else:
            status = VerificationStatus.UNVERIFIED

        return {
            'status': status,
            'confidence': round(min(1.0, confidence), 3),
            'evidence': evidence,
            'contradictions': contradictions,
            'notes': f'Детерминированная проверка. source_trust={source_trust:.2f}',
        }

    def _find_related_knowledge(self, claim: str, n: int = 5) -> str:
        if not self.knowledge:
            return ""
        claim_words = set(claim.lower().split())
        scored = []
        for key, value in self.knowledge._long_term.items():
            val_words = set(str(value).lower().split())
            if claim_words and val_words:
                score = len(claim_words & val_words) / len(claim_words | val_words)
                if score > 0.1:
                    scored.append((score, key, str(value)[:200]))
        scored.sort(reverse=True)
        return '\n'.join(f"[{k}]: {v}" for _, k, v in scored[:n])

    def _parse_result(self, raw: str):
        import re
        status_map = {
            'verified': VerificationStatus.VERIFIED,
            'unverified': VerificationStatus.UNVERIFIED,
            'uncertain': VerificationStatus.UNCERTAIN,
            'contested': VerificationStatus.CONTESTED,
            'outdated': VerificationStatus.OUTDATED,
            'false': VerificationStatus.FALSE,
        }
        status_m = re.search(r'СТАТУС[:\s]+(\w+)', raw, re.IGNORECASE)
        conf_m = re.search(r'УВЕРЕННОСТЬ[:\s]+([0-9.]+)', raw, re.IGNORECASE)
        evid_m = re.search(r'ДОКАЗАТЕЛЬСТВО[:\s]+(.+)', raw, re.IGNORECASE)
        contr_m = re.search(r'ПРОТИВОРЕЧИЕ[:\s]+(.+)', raw, re.IGNORECASE)
        note_m = re.search(r'ПРИМЕЧАНИЕ[:\s]+(.+)', raw, re.IGNORECASE)

        status_str = status_m.group(1).lower() if status_m else 'unverified'
        status = status_map.get(status_str, VerificationStatus.UNVERIFIED)
        confidence = 0.5
        if conf_m:
            try:
                confidence = float(conf_m.group(1))
            except (TypeError, ValueError):
                confidence = 0.5
        evidence = evid_m.group(1).strip() if evid_m else None
        contradiction = contr_m.group(1).strip() if contr_m else None
        notes = note_m.group(1).strip() if note_m else None

        return status, max(0.0, min(1.0, confidence)), evidence, contradiction, notes

    def _store(self, claim_hash: str, result: VerificationResult):
        self._results[claim_hash] = result
        self._audit.append(result.to_dict())

    def _hash(self, text: str) -> str:
        return hashlib.md5(text.strip().lower().encode()).hexdigest()[:12]

    def _init_default_trust(self):
        defaults = {
            'knowledge_base': 0.8,
            'local': 0.7,
            'user': 0.6,
            'web': 0.5,
            'unknown': 0.4,
            'generated': 0.5,
        }
        self._source_trust.update(defaults)

    def _log(self, message: str):
        if self.monitoring:
            self.monitoring.info(message, source='knowledge_verification')
        else:
            print(f"[KnowledgeVerification] {message}")

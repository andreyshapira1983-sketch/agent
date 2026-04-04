# Evaluation & Benchmarking Layer (оценка качества) — Слой 25
# Архитектура автономного AI-агента
# Оценка качества ответов и действий, benchmark, KPI, A/B сравнение стратегий.
# pylint: disable=broad-except


import time
from collections.abc import Callable
from enum import Enum


class EvalStatus(Enum):
    PASS    = 'pass'
    FAIL    = 'fail'
    PARTIAL = 'partial'


class EvalResult:
    """Результат одного теста/оценки."""

    def __init__(self, name: str, status: EvalStatus, score: float | None = None,
                 details: str | None = None, metadata: dict | None = None):
        self.name = name
        self.status = status
        self.score = score            # 0.0 – 1.0
        self.details = details
        self.metadata = metadata or {}
        self.timestamp = time.time()

    def to_dict(self):
        return {
            'name': self.name,
            'status': self.status.value,
            'score': self.score,
            'details': self.details,
            'metadata': self.metadata,
            'timestamp': self.timestamp,
        }


class BenchmarkSuite:
    """Набор тестовых случаев для бенчмарка."""

    def __init__(self, name: str):
        self.name = name
        self.cases: list[dict] = []   # {'input': ..., 'expected': ..., 'tags': [...]}

    def add(self, input_data, expected, tags: list | None = None):
        self.cases.append({'input': input_data, 'expected': expected, 'tags': tags or []})

    def __len__(self):
        return len(self.cases)


class EvaluationSystem:
    """
    Evaluation & Benchmarking Layer — Слой 25.

    Функции:
        - оценка качества ответов агента по критериям
        - запуск benchmark-наборов и regression-тестов
        - отслеживание KPI: точность, стабильность, скорость
        - A/B сравнение стратегий
        - хранение истории всех оценок

    Используется:
        - Autonomous Loop (Слой 20)      — оценка после каждого цикла
        - Self-Improvement (Слой 12)     — данные для оптимизации стратегий
        - Reflection System (Слой 10)    — совместный анализ качества
        - Monitoring (Слой 17)           — метрики качества
    """

    def __init__(self, cognitive_core=None, monitoring=None, pass_threshold: float = 0.7):
        """
        Args:
            cognitive_core  — для LLM-based оценки (judge)
            monitoring      — Monitoring (Слой 17)
            pass_threshold  — порог score для статуса PASS (0–1)
        """
        self.cognitive_core = cognitive_core
        self.monitoring = monitoring
        self.pass_threshold = pass_threshold

        self._results: list[EvalResult] = []
        self._suites: dict[str, BenchmarkSuite] = {}
        self._kpi: dict[str, list[float]] = {}     # метрика → история значений

    # ── Оценка качества ───────────────────────────────────────────────────────

    def evaluate(self, name: str, output: str, criteria: list[str],
                 reference: str | None = None) -> EvalResult:
        """
        Оценивает вывод агента по критериям.

        Сначала — бесплатная локальная эвристика (длина, ключевые слова, структура).
        Если локальная оценка уверена (score ≥ 0.8 или ≤ 0.2) — принимает решение БЕЗ LLM.
        Если неуверена — вызывает LLM-judge (платный).
        """
        # ── Этап 1: Локальная эвристика (бесплатно) ──────────────────────────
        t_start = time.time()
        local_score = self._local_heuristic_score(output, criteria, reference)
        local_latency = round((time.time() - t_start) * 1000)

        # Уверенный результат — не тратим деньги на LLM
        if local_score is not None and (local_score >= 0.8 or local_score <= 0.2):
            status = (EvalStatus.PASS if local_score >= self.pass_threshold
                      else EvalStatus.PARTIAL if local_score >= 0.4
                      else EvalStatus.FAIL)
            result = EvalResult(name, status, score=local_score,
                                details='local_heuristic (no LLM cost)',
                                metadata={
                                    'latency_ms': local_latency,
                                    'score_method': 'local_heuristic',
                                    'criteria_count': len(criteria),
                                    'llm_saved': True,
                                })
            self._store(result)
            self.record_kpi('eval.latency_ms', local_latency)
            self.record_kpi('eval.call_count', 1)
            self.record_kpi('eval.score', local_score)
            self._log(f"Оценка '{name}': {status.value}, score={local_score:.2f}, "
                      f"method=local_heuristic, latency={local_latency}ms (LLM не нужен)")
            return result

        # ── Этап 2: LLM-judge (только если локальная не уверена) ─────────────
        if not self.cognitive_core:
            score = local_score if local_score is not None else None
            status = (EvalStatus.PASS if score is not None and score >= self.pass_threshold
                      else EvalStatus.PARTIAL)
            result = EvalResult(name, status, score=score,
                                details='cognitive_core не подключён — только локальная оценка')
            self._store(result)
            return result

        criteria_str = '\n'.join(f"- {c}" for c in criteria)
        ref_block = f"\nЭталонный ответ:\n{reference}" if reference else ""

        t_start = time.time()
        raw = self.cognitive_core.reasoning(
            f"Оцени ответ агента по следующим критериям:\n{criteria_str}{ref_block}\n\n"
            f"Ответ агента:\n{output}\n\n"
            f"Для каждого критерия: ✓ выполнен / ✗ нет. "
            f"В конце строка: SCORE: <число от 0 до 1>"
        )
        latency_ms = round((time.time() - t_start) * 1000)

        score, score_method = self._parse_score(str(raw))

        if score is None:
            # Оценка неизвлекаема — не выдумываем цифру
            status = EvalStatus.PARTIAL
        else:
            status = (EvalStatus.PASS if score >= self.pass_threshold
                      else EvalStatus.PARTIAL if score >= 0.4
                      else EvalStatus.FAIL)

        result = EvalResult(name, status, score=score, details=str(raw),
                            metadata={
                                'latency_ms':   latency_ms,
                                'score_method': score_method,
                                'criteria_count': len(criteria),
                            })
        self._store(result)

        # Реальные KPI: latency и счётчик вызовов
        self.record_kpi('eval.latency_ms', latency_ms)
        self.record_kpi('eval.call_count', 1)
        if score is not None:
            self.record_kpi('eval.score', score)

        score_str = f'{score:.2f}' if score is not None else 'unknown'
        self._log(f"Оценка '{name}': {status.value}, score={score_str}, "
                  f"latency={latency_ms}ms, method={score_method}")
        return result

    def evaluate_exact(self, name: str, output, expected) -> EvalResult:
        """Точное сравнение вывода с ожидаемым значением."""
        passed = str(output).strip() == str(expected).strip()
        status = EvalStatus.PASS if passed else EvalStatus.FAIL
        score = 1.0 if passed else 0.0
        result = EvalResult(name, status, score=score,
                            details=f"Ожидалось: {expected}\nПолучено: {output}")
        self._store(result)
        return result

    def evaluate_contains(self, name: str, output: str, must_contain: list[str]) -> EvalResult:
        """Проверяет наличие всех ключевых фраз в выводе."""
        missing = [phrase for phrase in must_contain if phrase.lower() not in output.lower()]
        score = 1 - len(missing) / len(must_contain)
        status = (EvalStatus.PASS if not missing
                  else EvalStatus.PARTIAL if score >= 0.5
                  else EvalStatus.FAIL)
        result = EvalResult(name, status, score=score,
                            details=f"Отсутствуют: {missing}" if missing else "Все фразы найдены")
        self._store(result)
        return result

    # ── Benchmark ─────────────────────────────────────────────────────────────

    def create_suite(self, name: str) -> BenchmarkSuite:
        suite = BenchmarkSuite(name)
        self._suites[name] = suite
        return suite

    def run_suite(self, suite_name: str, agent_fn: Callable) -> dict:
        """
        Запускает benchmark-набор.

        Args:
            suite_name — имя зарегистрированного BenchmarkSuite
            agent_fn   — функция(input) → output для тестирования

        Returns:
            {'passed': int, 'failed': int, 'score': float, 'results': list}
        """
        suite = self._suites.get(suite_name)
        if not suite:
            return {'error': f"Suite '{suite_name}' не найден"}

        self._log(f"Запуск бенчмарка '{suite_name}': {len(suite)} кейсов")
        results = []
        for i, case in enumerate(suite.cases):
            t_start = time.time()
            try:
                output = agent_fn(case['input'])
            except Exception as e:
                output = f"ERROR: {e}"
            latency = time.time() - t_start

            result = self.evaluate_exact(
                name=f"{suite_name}[{i}]",
                output=output,
                expected=case['expected'],
            )
            result.metadata['latency'] = latency
            result.metadata['tags'] = case.get('tags', [])
            results.append(result.to_dict())

        passed = sum(1 for r in results if r['status'] == EvalStatus.PASS.value)
        score  = passed / len(results) if results else 0.0

        # Реальные KPI бенчмарка
        self.record_kpi(f"benchmark.{suite_name}.score", score)
        self.record_kpi(f"benchmark.{suite_name}.pass_rate", score)

        latencies = [r['metadata']['latency'] for r in results
                     if isinstance(r.get('metadata', {}).get('latency'), (int, float))]
        avg_latency = round(sum(latencies) / len(latencies), 3) if latencies else None
        if avg_latency is not None:
            self.record_kpi(f"benchmark.{suite_name}.avg_latency_s", avg_latency)

        summary = {
            'suite':       suite_name,
            'total':       len(results),
            'passed':      passed,
            'failed':      len(results) - passed,
            'score':       round(score, 3),
            'avg_latency_s': avg_latency,
            'results':     results,
        }
        self._log(f"Бенчмарк '{suite_name}' завершён: {passed}/{len(results)}, "
                  f"score={score:.2f}, avg_latency={avg_latency}s")
        return summary

    # ── A/B сравнение стратегий ───────────────────────────────────────────────

    def ab_compare(self, name: str, fn_a: Callable, fn_b: Callable,
                   test_inputs: list, criteria: list[str]) -> dict:
        """
        Сравнивает две стратегии/функции на одних и тех же входах.

        Returns:
            {'winner': 'A'|'B'|'tie', 'score_a': float, 'score_b': float}
        """
        self._log(f"A/B сравнение: '{name}'")
        scores_a, scores_b = [], []

        for inp in test_inputs:
            out_a = fn_a(inp)
            out_b = fn_b(inp)
            res_a = self.evaluate(f"{name}_A", str(out_a), criteria)
            res_b = self.evaluate(f"{name}_B", str(out_b), criteria)
            if res_a.score is not None:
                scores_a.append(res_a.score)
            if res_b.score is not None:
                scores_b.append(res_b.score)

        avg_a = sum(scores_a) / len(scores_a) if scores_a else 0.0
        avg_b = sum(scores_b) / len(scores_b) if scores_b else 0.0

        if abs(avg_a - avg_b) < 0.05:
            winner = 'tie'
        elif avg_a > avg_b:
            winner = 'A'
        else:
            winner = 'B'

        result = {'name': name, 'winner': winner,
                  'score_a': round(avg_a, 3), 'score_b': round(avg_b, 3)}
        self._log(f"A/B '{name}': победитель={winner}, A={avg_a:.2f}, B={avg_b:.2f}")
        return result

    # ── KPI ───────────────────────────────────────────────────────────────────

    def record_kpi(self, metric: str, value: float):
        """Записывает значение KPI."""
        if metric not in self._kpi:
            self._kpi[metric] = []
        self._kpi[metric].append(value)
        if self.monitoring:
            self.monitoring.record_metric(f"eval.{metric}", value)

    def get_kpi(self, metric: str) -> dict:
        values = self._kpi.get(metric, [])
        if not values:
            return {'metric': metric, 'count': 0}
        return {
            'metric': metric,
            'count': len(values),
            'last': values[-1],
            'average': round(sum(values) / len(values), 3),
            'min': min(values),
            'max': max(values),
        }

    def all_kpi(self) -> dict:
        return {m: self.get_kpi(m) for m in self._kpi}

    # ── История оценок ────────────────────────────────────────────────────────

    def get_results(self, status: EvalStatus | None = None, last_n: int | None = None) -> list[dict]:
        results = self._results
        if status:
            results = [r for r in results if r.status == status]
        if last_n:
            results = results[-last_n:]
        return [r.to_dict() for r in results]

    def summary(self) -> dict:
        total = len(self._results)
        if not total:
            return {'total': 0}

        passed  = sum(1 for r in self._results if r.status == EvalStatus.PASS)
        failed  = sum(1 for r in self._results if r.status == EvalStatus.FAIL)
        partial = sum(1 for r in self._results if r.status == EvalStatus.PARTIAL)
        scores  = [r.score for r in self._results if r.score is not None]

        # Реальные метрики latency из metadata
        latencies = [
            r.metadata['latency_ms']
            for r in self._results
            if isinstance(r.metadata.get('latency_ms'), (int, float))
        ]

        # Распределение методов оценки (насколько часто LLM даёт явный SCORE)
        from collections import Counter
        methods = Counter(
            r.metadata.get('score_method', 'unknown')
            for r in self._results
            if r.metadata
        )

        result = {
            'total':    total,
            'passed':   passed,
            'failed':   failed,
            'partial':  partial,
            'pass_rate': round(passed / total, 3),
            'avg_score': round(sum(scores) / len(scores), 3) if scores else None,
            'score_coverage': round(len(scores) / total, 3),  # доля оценок с числом
        }

        if latencies:
            result['latency_ms'] = {
                'avg': round(sum(latencies) / len(latencies)),
                'min': min(latencies),
                'max': max(latencies),
            }

        if methods:
            result['score_methods'] = dict(methods)  # {'score_marker': 5, 'checkmark_ratio': 2, ...}

        return result

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _store(self, result: EvalResult):
        self._results.append(result)

    # ── Локальная эвристика: бесплатная оценка без LLM ───────────────────────

    def _local_heuristic_score(self, output: str, criteria: list[str],
                                reference: str | None = None) -> float | None:
        """
        Бесплатная локальная оценка качества ответа.

        Проверяет: длину, наличие ключевых слов из критериев, структуру,
        совпадение с эталоном. Возвращает 0.0–1.0 если уверена, иначе None.
        """
        if not output or not output.strip():
            return 0.0  # пустой ответ — однозначно плохо

        output_lower = output.lower().strip()
        signals = []  # список (weight, passed_bool)

        # 1. Минимальная длина — пустышки и отписки
        signals.append((0.15, len(output_lower) >= 20))

        # 2. Нет ли это отписка / заглушка
        _STUB_PHRASES = (
            'зависит от контекста', 'cannot provide', 'не могу предоставить',
            'к сожалению', 'unfortunately', 'i apologize', 'as an ai',
            'как языковая модель', 'placeholder', 'заглушка', 'todo',
            'код выше', 'см. выше', 'пример ниже',
        )
        is_stub = any(p in output_lower for p in _STUB_PHRASES)
        signals.append((0.2, not is_stub))

        # 3. Ключевые слова из критериев встречаются в ответе
        if criteria:
            crit_words = set()
            for c in criteria:
                for w in c.lower().split():
                    if len(w) > 3:
                        crit_words.add(w)
            if crit_words:
                hits = sum(1 for w in crit_words if w in output_lower)
                ratio = hits / len(crit_words)
                signals.append((0.25, ratio >= 0.3))

        # 4. Структура: есть кодовые блоки, списки, или подзаголовки
        has_structure = bool(
            '```' in output or '\n- ' in output or '\n* ' in output or
            '\n1.' in output or '##' in output or 'import ' in output
        )
        signals.append((0.15, has_structure or len(output) < 200))

        # 5. Совпадение с эталоном (если есть)
        if reference and reference.strip():
            ref_words = set(reference.lower().split())
            out_words = set(output_lower.split())
            if ref_words:
                overlap = len(ref_words & out_words) / len(ref_words)
                signals.append((0.25, overlap >= 0.3))

        # Взвешенный score
        if not signals:
            return None
        total_weight = sum(w for w, _ in signals)
        weighted = sum(w for w, ok in signals if ok) / total_weight if total_weight else 0.0
        return round(weighted, 2)

    def _parse_score(self, text: str) -> tuple[float | None, str]:
        """
        Извлекает score из текста LLM-оценки.

        Три уровня разбора (в порядке убывания точности):
          1. Явный маркер  SCORE: 0.85  — самый точный
          2. Подсчёт ✓/✗  — точность зависит от количества критериев
          3. Тональный анализ — подсчёт позитивных и негативных сигналов

        Если ни один метод не дал результата — возвращает (None, 'unknown'),
        чтобы не выдавать выдуманную цифру 0.5.

        Returns:
            (score_float_or_None, method_name)
        """
        import re

        # ── Уровень 1: явный числовой маркер ──────────────────────────────────
        match = re.search(r'SCORE:\s*([0-9.]+)', text, re.IGNORECASE)
        if match:
            try:
                return max(0.0, min(1.0, float(match.group(1)))), 'score_marker'
            except ValueError:
                pass

        # ── Уровень 2: символы ✓ / ✗ ──────────────────────────────────────────
        passed = text.count('✓')
        failed = text.count('✗')
        total  = passed + failed
        if total >= 2:          # минимум 2 символа чтобы ratio был значимым
            return round(passed / total, 2), 'checkmark_ratio'

        # ── Уровень 3: тональный анализ текста ────────────────────────────────
        # Реальный подсчёт позитивных и негативных сигналов в ответе LLM.
        # Слова выбраны из типичных формулировок LLM-judge на EN и RU.
        _POS = [
            'correct', 'accurate', 'complete', 'good', 'excellent', 'well',
            'yes', 'right', 'satisfied', 'fulfilled', 'passed', 'meets',
            'верно', 'правильно', 'выполнено', 'соответствует', 'хорошо',
            'точно', 'полностью', 'да', 'справился', 'соответствие',
        ]
        _NEG = [
            'incorrect', 'wrong', 'incomplete', 'missing', 'failed', 'poor',
            'no', 'not', "doesn't", 'lacks', 'absent', 'unclear', 'vague',
            'неверно', 'неправильно', 'не выполнено', 'отсутствует', 'плохо',
            'нет', 'не соответствует', 'неполно', 'некорректно', 'провал',
        ]
        text_l = text.lower()
        pos_count = sum(1 for w in _POS if w in text_l)
        neg_count = sum(1 for w in _NEG if w in text_l)
        sentiment_total = pos_count + neg_count

        if sentiment_total >= 3:   # минимум 3 сигнала для надёжной оценки
            return round(pos_count / sentiment_total, 2), 'sentiment_analysis'

        # Ни один метод не смог извлечь score — честно возвращаем None
        return None, 'unknown'

    def _log(self, message: str):
        if self.monitoring:
            self.monitoring.info(message, source='evaluation')
        else:
            print(f"[Evaluation] {message}")

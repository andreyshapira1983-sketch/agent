# Causal Reasoning System (каузальное рассуждение) — Слой 41
# Архитектура автономного AI-агента
# Рассуждение о причинах и следствиях: цепи причинности, контрфактическое мышление.


import time


class CausalLink:
    """Причинно-следственная связь между двумя событиями/состояниями."""

    def __init__(self, cause: str, effect: str,
                 confidence: float = 0.7,
                 mechanism: str | None = None,
                 context: str | None = None):
        self.cause = cause
        self.effect = effect
        self.confidence = max(0.0, min(1.0, confidence))
        self.mechanism = mechanism      # как именно причина порождает следствие
        self.context = context          # при каких условиях связь верна
        self.observed_count = 1         # сколько раз наблюдалась
        self.created_at = time.time()

    def reinforce(self, delta: float = 0.05):
        """Усиливает уверенность в связи при повторном наблюдении."""
        self.confidence = min(1.0, self.confidence + delta)
        self.observed_count += 1

    def to_dict(self):
        return {
            'cause': self.cause,
            'effect': self.effect,
            'confidence': round(self.confidence, 3),
            'mechanism': self.mechanism,
            'context': self.context,
            'observed_count': self.observed_count,
        }


class CausalGraph:
    """Граф причинно-следственных связей."""

    def __init__(self):
        self._links: list[CausalLink] = []
        # cause → [effects], effect → [causes]
        self._cause_map: dict[str, list[CausalLink]] = {}
        self._effect_map: dict[str, list[CausalLink]] = {}

    def add(self, cause: str, effect: str, confidence: float = 0.7,
            mechanism: str | None = None, context: str | None = None) -> CausalLink:
        # Проверяем существующую связь
        for link in self._links:
            if link.cause.lower() == cause.lower() and \
               link.effect.lower() == effect.lower():
                link.reinforce()
                return link

        link = CausalLink(cause, effect, confidence, mechanism, context)
        self._links.append(link)
        self._cause_map.setdefault(cause, []).append(link)
        self._effect_map.setdefault(effect, []).append(link)
        return link

    def get_effects(self, cause: str) -> list[CausalLink]:
        """Возвращает все следствия данной причины."""
        return self._cause_map.get(cause, [])

    def get_causes(self, effect: str) -> list[CausalLink]:
        """Возвращает все причины данного следствия."""
        return self._effect_map.get(effect, [])

    def chain_forward(self, start: str, depth: int = 3) -> list[list[str]]:
        """Строит цепи следствий от начального события (в глубину)."""
        chains = []
        def dfs(node: str, path: list, d: int):
            effects = self._cause_map.get(node, [])
            if not effects or d == 0:
                if len(path) > 1:
                    chains.append(list(path))
                return
            for link in effects:
                if link.effect not in path:
                    path.append(link.effect)
                    dfs(link.effect, path, d - 1)
                    path.pop()
        dfs(start, [start], depth)
        return chains

    def chain_backward(self, end: str, depth: int = 3) -> list[list[str]]:
        """Ищет цепи причин, ведущих к указанному событию."""
        chains = []
        def dfs(node: str, path: list, d: int):
            causes = self._effect_map.get(node, [])
            if not causes or d == 0:
                if len(path) > 1:
                    chains.append(list(reversed(path)))
                return
            for link in causes:
                if link.cause not in path:
                    path.append(link.cause)
                    dfs(link.cause, path, d - 1)
                    path.pop()
        dfs(end, [end], depth)
        return chains

    def to_dict(self) -> list[dict]:
        return [link.to_dict() for link in self._links]

    def export_state(self) -> list[dict]:
        """Возвращает состояние графа для персистентности."""
        data = []
        for link in self._links:
            data.append({
                "cause": link.cause,
                "effect": link.effect,
                "confidence": link.confidence,
                "mechanism": link.mechanism,
                "context": link.context,
                "observed_count": link.observed_count,
                "created_at": getattr(link, 'created_at', None),
            })
        return data

    def import_state(self, data: list[dict]):
        """Восстанавливает граф из персистентного хранилища."""
        for ld in data:
            link = self.add(
                cause=ld["cause"],
                effect=ld["effect"],
                confidence=ld.get("confidence", 0.7),
                mechanism=ld.get("mechanism"),
                context=ld.get("context"),
            )
            link.observed_count = ld.get("observed_count", 1)
            link.confidence = ld.get("confidence", 0.7)
            if ld.get("created_at"):
                link.created_at = ld["created_at"]


class CausalReasoningSystem:
    """
    Causal Reasoning System — Слой 41.

    Функции:
        - построение и поддержка графа причинно-следственных связей
        - объяснение «почему произошло X» (backward chaining)
        - предсказание «что будет если Y» (forward chaining)
        - контрфактическое мышление: «что было бы, если бы не X»
        - обнаружение новых причинно-следственных связей через Cognitive Core
        - обучение на основе наблюдений агента

    Используется:
        - Cognitive Core (Слой 3)       — генерация объяснений и гипотез
        - Reflection System (Слой 10)   — анализ ошибок по причинам
        - Environment Model (Слой 27)   — модель мира с причинностью
        - Self-Repair (Слой 11)         — поиск корневых причин сбоев
    """

    def __init__(self, cognitive_core=None, knowledge_system=None,
                 monitoring=None):
        self.cognitive_core = cognitive_core
        self.knowledge = knowledge_system
        self.monitoring = monitoring

        self.graph = CausalGraph()

    # ── Обучение ──────────────────────────────────────────────────────────────

    def learn(self, cause: str, effect: str, confidence: float = 0.7,
              mechanism: str | None = None) -> CausalLink:
        """Добавляет новую причинно-следственную связь."""
        link = self.graph.add(cause, effect, confidence, mechanism)
        self._log(f"Связь: '{cause}' → '{effect}' (conf={link.confidence:.2f})")
        return link

    def learn_from_text(self, text: str) -> list[CausalLink]:
        """
        Извлекает причинно-следственные связи из произвольного текста
        через Cognitive Core.
        """
        if not self.cognitive_core:
            return []

        raw = str(self.cognitive_core.reasoning(
            f"Извлеки причинно-следственные связи из текста.\n"
            f"Для каждой связи:\n"
            f"ПРИЧИНА: <причина>\n"
            f"СЛЕДСТВИЕ: <следствие>\n"
            f"МЕХАНИЗМ: <как одно порождает другое>\n\n"
            f"Текст:\n{text}"
        ))

        import re
        causes = re.findall(r'ПРИЧИНА[:\s]+(.+)', raw, re.IGNORECASE)
        effects = re.findall(r'СЛЕДСТВИЕ[:\s]+(.+)', raw, re.IGNORECASE)
        mechs = re.findall(r'МЕХАНИЗМ[:\s]+(.+)', raw, re.IGNORECASE)

        links = []
        for i, (c, e) in enumerate(zip(causes, effects)):
            m = mechs[i] if i < len(mechs) else None
            links.append(self.learn(c.strip(), e.strip(), mechanism=m))

        self._log(f"Из текста извлечено {len(links)} причинно-следственных связей")
        return links

    def observe(self, cause: str, effect: str):
        """Фиксирует наблюдённую причинно-следственную связь (усиливает уверенность)."""
        existing = self.graph.get_effects(cause)
        for link in existing:
            if link.effect.lower() == effect.lower():
                link.reinforce()
                return link
        return self.learn(cause, effect, confidence=0.6)

    # ── Рассуждение ───────────────────────────────────────────────────────────

    def explain(self, event: str, depth: int = 3) -> dict:
        """
        Объясняет причины события (backward chaining).

        Returns:
            {'event': ..., 'causal_chains': [...], 'explanation': str}
        """
        chains = self.graph.chain_backward(event, depth)

        if self.cognitive_core and not chains:
            # Если граф пуст — просим LLM
            raw = str(self.cognitive_core.reasoning(
                f"Объясни возможные причины события: {event}\n"
                f"Укажи цепь причин: ЦЕПЬ: причина1 → причина2 → {event}"
            ))
            explanation = raw
        elif self.cognitive_core:
            context = '\n'.join(' → '.join(c) for c in chains)
            raw = str(self.cognitive_core.reasoning(
                f"На основе этих причинных цепей объясни '{event}':\n{context}"
            ))
            explanation = raw
        else:
            explanation = f"Известные причинные цепи: {chains}"

        return {
            'event': event,
            'causal_chains': chains,
            'explanation': explanation,
        }

    def predict(self, cause: str, depth: int = 3) -> dict:
        """
        Предсказывает возможные следствия причины (forward chaining).
        """
        chains = self.graph.chain_forward(cause, depth)
        direct = [link.to_dict() for link in self.graph.get_effects(cause)]

        if self.cognitive_core:
            context = f"Прямые следствия: {direct}\nЦепи: {chains}"
            raw = str(self.cognitive_core.reasoning(
                f"Что произойдёт если: {cause}?\n\n"
                f"Известные связи:\n{context}\n\n"
                f"Опиши наиболее вероятные сценарии."
            ))
            prediction = raw
        else:
            prediction = f"Следствия: {[link['effect'] for link in direct]}"

        return {
            'cause': cause,
            'direct_effects': direct,
            'effect_chains': chains,
            'prediction': prediction,
        }

    def counterfactual(self, event: str, removed_cause: str) -> str:
        """
        Контрфактическое рассуждение: «что было бы, если бы не было причины X?»
        """
        if not self.cognitive_core:
            return f"Без '{removed_cause}' событие '{event}' могло не произойти."

        chains = self.graph.chain_backward(event, depth=4)
        chains_without = [
            chain for chain in chains
            if removed_cause.lower() not in [c.lower() for c in chain]
        ]

        raw = str(self.cognitive_core.reasoning(
            f"Контрфактический анализ:\n"
            f"Событие: {event}\n"
            f"Убранная причина: {removed_cause}\n"
            f"Оставшиеся причинные цепи: {chains_without}\n\n"
            f"Что произошло бы без причины '{removed_cause}'?"
        ))
        return raw

    def root_cause(self, failure: str) -> str:
        """Находит корневую причину сбоя (используется в Self-Repair)."""
        result = self.explain(failure, depth=5)
        if result.get('causal_chains') and result['causal_chains'][0]:
            root = result['causal_chains'][0][0]   # начало самой длинной цепи
            return root
        return result.get('explanation', 'Корневая причина не найдена')

    # ── Реестр ────────────────────────────────────────────────────────────────

    def get_graph(self) -> list[dict]:
        return self.graph.to_dict()

    def summary(self) -> dict:
        links = self.graph.to_dict()
        return {
            'total_links': len(links),
            'avg_confidence': round(
                sum(lnk['confidence'] for lnk in links) / max(1, len(links)), 3
            ),
            'unique_causes': len(set(lnk['cause'] for lnk in links)),
            'unique_effects': len(set(lnk['effect'] for lnk in links)),
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def add_causal_relation(self, cause: str, effect: str,
                            strength: float = 0.8) -> CausalLink:
        """
        Adds a cause-effect relation to the internal causal graph.

        Args:
            cause    — cause description
            effect   — effect description
            strength — confidence / strength of the link (0-1)

        Returns:
            CausalLink added to the graph.
        """
        return self.learn(cause, effect, confidence=strength)

    def infer_cause(self, effect: str, _context: dict | None = None) -> dict:
        """
        Deterministic causal inference without LLM.

        First checks the internal causal graph for known cause-effect pairs.
        Falls back to keyword heuristics if no graph match is found.

        Args:
            effect  — description of the observed effect
            context — optional additional context (unused in heuristic path)

        Returns:
            Dict with keys: effect, probable_cause, confidence, method
        """
        effect_lower = effect.lower()

        # 1. Check the causal graph for any known cause of this effect
        # Try exact match first, then substring match
        causes_from_graph = self.graph.get_causes(effect)
        if not causes_from_graph:
            # Substring search across all effect keys
            for stored_effect, links in self.graph._effect_map.items():  # pylint: disable=protected-access
                if stored_effect.lower() in effect_lower or effect_lower in stored_effect.lower():
                    causes_from_graph = links
                    break

        if causes_from_graph:
            best = max(causes_from_graph, key=lambda lnk: lnk.confidence)
            return {
                'effect': effect,
                'probable_cause': best.cause,
                'confidence': round(best.confidence, 3),
                'method': 'graph',
            }

        # 2. Keyword heuristics
        if any(kw in effect_lower for kw in ('error', 'crash', 'fail', 'exception')):
            probable_cause = 'input_validation or resource_exhaustion'
            confidence = 0.65
        elif any(kw in effect_lower for kw in ('slow', 'timeout', 'hang', 'latency')):
            probable_cause = 'inefficiency or overload'
            confidence = 0.65
        elif any(kw in effect_lower for kw in ('missing', 'not found', 'absent', 'none')):
            probable_cause = 'configuration or path error'
            confidence = 0.60
        else:
            probable_cause = 'unknown — insufficient data'
            confidence = 0.3

        return {
            'effect': effect,
            'probable_cause': probable_cause,
            'confidence': confidence,
            'method': 'heuristic',
        }

    def _log(self, message: str):
        if self.monitoring:
            self.monitoring.info(message, source='causal_reasoning')
        else:
            print(f"[CausalReasoning] {message}")

    def export_state(self) -> list[dict]:
        """Возвращает состояние каузального графа для персистентности."""
        if self.graph:
            return self.graph.export_state()
        return []

    def import_state(self, data: list[dict]):
        """Восстанавливает каузальный граф из персистентного хранилища."""
        if self.graph and data:
            self.graph.import_state(data)


# Alias for compatibility
CausalReasoning = CausalReasoningSystem

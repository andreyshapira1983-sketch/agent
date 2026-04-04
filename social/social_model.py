# Social Interaction Model (модель социального взаимодействия) — Слой 43
# Архитектура автономного AI-агента
# Модель пользователей, ролей, отношений и стиля общения.


import re
import time
from enum import Enum

from social.emotional_state import EmotionalState

# ── Лексические словари для детекции тона ─────────────────────────────────────

_TONE_KEYWORDS: dict[str, list[str]] = {
    'positive': [
        'хорошо', 'отлично', 'отличный', 'прекрасно', 'прекрасный',
        'замечательно', 'замечательный', 'рад', 'рада',
        'нравится', 'доволен', 'довольна', 'великолепно',
        'good', 'great', 'excellent', 'awesome', 'perfect',
        'wonderful', 'love', 'happy', 'pleased', 'fantastic',
        'brilliant', 'well done', 'nice',
    ],
    'frustrated': [
        'плохо', 'ужасно', 'не работает', 'сломано', 'ошибка', 'неправильно',
        'проблема', 'баг', 'ненавижу', 'раздражает', 'раздражает', 'надоело',
        'опять', 'снова сломалось', 'бесполезно',
        'terrible', 'bad', 'broken', 'wrong', 'error', 'problem', 'issue',
        'not working', "doesn't work", 'failed', 'useless', 'annoying',
        'awful', 'horrible', 'frustrated', 'frustrating',
    ],
    'urgent': [
        'срочно', 'немедленно', 'сейчас', 'критично', 'экстренно', 'дедлайн',
        'быстро', 'скорее', 'не ждёт', 'горит',
        'urgent', 'asap', 'immediately', 'now', 'critical', 'emergency',
        'deadline', 'quickly', 'hurry', 'fast', 'right away', 'time-sensitive',
    ],
    'confused': [
        'не понимаю', 'непонятно', 'как это', 'зачем', 'почему',
        'что это значит', 'объясни', 'помогите разобраться',
        'confused', 'unclear', "don't understand", "don't get it",
        'what does', 'how does', 'why does', 'lost', 'unsure',
        'uncertain', 'help me understand', 'what is', "what's",
    ],
    'grateful': [
        'спасибо большое', 'огромное спасибо', 'благодарен', 'благодарна',
        'очень помогло', 'выручил', 'выручила', 'ценю',
        'thank you so much', 'really appreciate', 'grateful', 'appreciate',
        'very helpful', 'helped a lot', 'awesome help', 'wonderful help',
    ],
}

# ── Лексические словари для детекции стиля ────────────────────────────────────

_STYLE_FEATURES: dict[str, dict] = {
    'formal': {
        'keywords': [
            'dear', 'sincerely', 'regards', 'hereby', 'pursuant', 'accordingly',
            'respectfully', 'уважаемый', 'уважаемая', 'с уважением', 'согласно',
            'во исполнение', 'настоящим',
        ],
        'patterns': [r'\b[A-ZА-Я][a-zа-я]+\s+[A-ZА-Я]\.\s'],   # инициалы
    },
    'technical': {
        'keywords': [
            'api', 'http', 'sql', 'json', 'xml', 'python', 'function', 'class',
            'module', 'import', 'exception', 'runtime', 'endpoint', 'deploy',
            'алгоритм', 'функция', 'класс', 'метод', 'параметр', 'переменная',
            'массив', 'объект', 'интерфейс',
        ],
        'patterns': [r'```', r'\b[a-z_]+\(\)', r'\b\d+\.\d+', r'\b[A-Z]{2,}\b'],
    },
    'concise': {
        'keywords': [],
        'patterns': [],          # определяется по метрикам (длина, кол-во слов)
        '_max_words_per_sent': 8,
        '_max_total_words': 30,
    },
    'detailed': {
        'keywords': [
            'because', 'therefore', 'furthermore', 'additionally', 'in detail',
            'specifically', 'for example', 'for instance', 'in particular',
            'потому что', 'поскольку', 'следовательно', 'кроме того', 'например',
            'в частности', 'более подробно', 'то есть',
        ],
        'patterns': [],
        '_min_words': 60,
    },
    'friendly': {
        'keywords': [
            'hey', 'hi', 'hello', 'lol', 'haha', 'cool', 'awesome', 'yeah',
            'yep', 'nope', "i'm", "you're", "it's", "let's", "don't",
            'привет', 'хей', 'ок', 'окей', 'конечно', 'классно',
        ],
        'patterns': [r'!{2,}', r'[😀😊👍🙂✌️❤️]'],
    },
}


class RelationshipType(Enum):
    USER       = 'user'         # обычный пользователь
    ADMIN      = 'admin'        # администратор
    COLLEAGUE  = 'colleague'    # другой агент / коллега
    MANAGER    = 'manager'      # руководитель / владелец
    EXPERT     = 'expert'       # эксперт в области
    UNKNOWN    = 'unknown'


class TrustLevel(Enum):
    HIGH   = 3
    MEDIUM = 2
    LOW    = 1
    NONE   = 0


class CommunicationStyle(Enum):
    FORMAL     = 'formal'
    FRIENDLY   = 'friendly'
    TECHNICAL  = 'technical'
    CONCISE    = 'concise'
    DETAILED   = 'detailed'


class SocialActor:
    """Участник взаимодействия (человек или агент)."""

    def __init__(self, actor_id: str, name: str,
                 relation: RelationshipType = RelationshipType.UNKNOWN,
                 trust: TrustLevel = TrustLevel.MEDIUM):
        self.actor_id = actor_id
        self.name = name
        self.relation = relation
        self.trust = trust
        self.preferred_style = CommunicationStyle.FRIENDLY
        self.preferences: dict = {}
        self.interaction_count = 0
        self.last_interaction: float | None = None
        self.notes: list[str] = []
        self.created_at = time.time()

    def record_interaction(self):
        self.interaction_count += 1
        self.last_interaction = time.time()

    def to_dict(self):
        return {
            'actor_id': self.actor_id,
            'name': self.name,
            'relation': self.relation.value,
            'trust': self.trust.name,
            'preferred_style': self.preferred_style.value,
            'interaction_count': self.interaction_count,
            'last_interaction': self.last_interaction,
        }


class ConversationContext:
    """Контекст текущего разговора."""

    def __init__(self, actor_id: str, topic: str = ''):
        self.actor_id = actor_id
        self.topic = topic
        self.messages: list[dict] = []
        self.started_at = time.time()
        self.emotional_tone = 'neutral'    # neutral / positive / frustrated / urgent

    def add_message(self, role: str, content: str):
        self.messages.append({
            'role': role,
            'content': content,
            'timestamp': time.time(),
        })

    def last_n(self, n: int = 5) -> list[dict]:
        return self.messages[-n:]


class SocialInteractionModel:
    """
    Social Interaction Model — Слой 43.

    Функции:
        - реестр участников (люди, агенты, системы)
        - управление доверием и отношениями
        - адаптация стиля общения под конкретного участника
        - ведение контекста разговора
        - распознавание эмоционального тона
        - социальные нормы: вежливость, субординация, культурный контекст
        - управление конфликтами в общении

    Используется:
        - Cognitive Core (Слой 3)         — генерация ответов с учётом стиля
        - Human Approval (Слой 22)        — кто имеет право одобрять
        - Agents System (Слой 4)          — коммуникация между агентами
        - Autonomous Loop (Слой 20)       — кому сообщать о прогрессе
    """

    def __init__(self, cognitive_core=None, monitoring=None):
        self.cognitive_core = cognitive_core
        self.monitoring = monitoring

        self._actors: dict[str, SocialActor] = {}
        self._conversations: dict[str, ConversationContext] = {}
        self._active_conv_id: str | None = None

        # Эмоциональное состояние агента (собственное настроение)
        self.emotional_state = EmotionalState()

        # Регистрируем системного актора
        self._register_default_actors()

    # ── Управление участниками ────────────────────────────────────────────────

    def register_actor(self, actor_id: str, name: str,
                       relation: RelationshipType = RelationshipType.USER,
                       trust: TrustLevel = TrustLevel.MEDIUM,
                       style: CommunicationStyle | None = None) -> SocialActor:
        """Регистрирует нового участника взаимодействия."""
        actor = SocialActor(actor_id, name, relation, trust)
        if style:
            actor.preferred_style = style
        self._actors[actor_id] = actor
        self._log(f"Зарегистрирован: [{actor_id}] {name} ({relation.value})")
        return actor

    def get_actor(self, actor_id: str) -> SocialActor | None:
        return self._actors.get(actor_id)

    def update_trust(self, actor_id: str, delta: int):
        """Изменяет уровень доверия: +1 или -1."""
        actor = self._actors.get(actor_id)
        if not actor:
            return
        levels = [TrustLevel.NONE, TrustLevel.LOW, TrustLevel.MEDIUM, TrustLevel.HIGH]
        current_idx = levels.index(actor.trust)
        new_idx = max(0, min(len(levels) - 1, current_idx + delta))
        actor.trust = levels[new_idx]
        self._log(f"Доверие к [{actor_id}]: {actor.trust.name}")

    # ── Стиль общения ─────────────────────────────────────────────────────────

    def adapt_response(self, message: str, actor_id: str) -> str:
        """
        Адаптирует ответ под стиль общения конкретного участника.
        """
        actor = self._actors.get(actor_id)
        if not actor or not self.cognitive_core:
            return message

        style_hints = {
            CommunicationStyle.FORMAL: "официально и профессионально",
            CommunicationStyle.FRIENDLY: "дружелюбно и неформально",
            CommunicationStyle.TECHNICAL: "технически точно, с деталями",
            CommunicationStyle.CONCISE: "кратко, только суть",
            CommunicationStyle.DETAILED: "подробно, с пояснениями",
        }
        hint = style_hints.get(actor.preferred_style, "нейтрально")
        raw = str(self.cognitive_core.reasoning(
            f"Перефразируй следующее сообщение {hint}:\n\n{message}"
        ))
        return raw

    def detect_style(self, actor_id: str, sample_text: str):
        """
        Определяет предпочтительный стиль общения по образцу текста.

        Уровень 1 (всегда): анализ текстовых признаков (ключевые слова,
        паттерны, метрики длины предложений).
        Уровень 2 (если есть cognitive_core): LLM-подтверждение при ничьей.
        """
        actor = self._actors.get(actor_id)
        if not actor:
            return

        text_l = sample_text.lower()
        words = text_l.split()
        sentences = [s.strip() for s in re.split(r'[.!?]+', sample_text) if s.strip()]
        total_words = len(words)

        style_map = {
            'formal':    CommunicationStyle.FORMAL,
            'friendly':  CommunicationStyle.FRIENDLY,
            'technical': CommunicationStyle.TECHNICAL,
            'concise':   CommunicationStyle.CONCISE,
            'detailed':  CommunicationStyle.DETAILED,
        }

        # ── Уровень 1: подсчёт признаков по каждому стилю ────────────────────
        scores: dict[str, float] = {s: 0.0 for s in style_map}

        for style_key, features in _STYLE_FEATURES.items():
            # Ключевые слова
            for kw in features.get('keywords', []):
                if kw in text_l:
                    scores[style_key] += 1.0

            # Regex-паттерны
            for pat in features.get('patterns', []):
                if re.search(pat, sample_text):
                    scores[style_key] += 1.5   # паттерн — более сильный сигнал

            # Метрические правила для concise
            if style_key == 'concise' and sentences:
                avg_words = total_words / len(sentences)
                max_words = features.get('_max_words_per_sent', 8)
                max_total = features.get('_max_total_words', 30)
                if avg_words <= max_words:
                    scores['concise'] += 2.0
                if total_words <= max_total:
                    scores['concise'] += 1.0

            # Метрические правила для detailed
            if style_key == 'detailed':
                min_words = features.get('_min_words', 60)
                if total_words >= min_words:
                    scores['detailed'] += 2.0

        max_score = max(scores.values())

        # Нет сигналов — нечего менять
        if max_score == 0:
            return

        best = max(scores, key=lambda s: scores[s])

        # Ничья между двумя стилями — используем LLM если доступен
        top_two = sorted(scores, key=lambda s: scores[s], reverse=True)[:2]
        if (scores[top_two[0]] == scores[top_two[1]]
                and scores[top_two[0]] > 0
                and self.cognitive_core):
            raw = str(self.cognitive_core.reasoning(
                f"Определи стиль общения по тексту: {sample_text}\n"
                f"Ответь одним словом: formal/friendly/technical/concise/detailed"
            ))
            for key in style_map:
                if key in raw.lower():
                    best = key
                    break

        actor.preferred_style = style_map[best]
        self._log(f"Стиль [{actor_id}]: {best} (score={scores[best]:.1f})")

    # ── Управление разговорами ────────────────────────────────────────────────

    def start_conversation(self, actor_id: str, topic: str = '') -> ConversationContext:
        """Начинает или возобновляет разговор с участником."""
        conv_id = f"conv_{actor_id}"
        conv = ConversationContext(actor_id, topic)
        self._conversations[conv_id] = conv
        self._active_conv_id = conv_id
        actor = self._actors.get(actor_id)
        if actor:
            actor.record_interaction()
        self._log(f"Разговор начат с [{actor_id}]: '{topic}'")
        return conv

    def add_to_conversation(self, actor_id: str, role: str, content: str):
        """Добавляет сообщение в контекст текущего разговора."""
        conv_id = f"conv_{actor_id}"
        if conv_id not in self._conversations:
            self.start_conversation(actor_id)
        self._conversations[conv_id].add_message(role, content)

    def get_conversation(self, actor_id: str) -> ConversationContext | None:
        return self._conversations.get(f"conv_{actor_id}")

    # ── Тон и эмоции ──────────────────────────────────────────────────────────

    def detect_tone(self, text: str, actor_id: str | None = None) -> str:
        """
        Определяет эмоциональный тон сообщения.

        Уровень 1 (всегда): лексический анализ по ключевым словам.
        Уровень 2 (если есть cognitive_core): LLM-уточнение при неуверенности.

        Тоны: neutral / positive / frustrated / urgent / confused / grateful
        """
        text_l = text.lower()

        # ── Уровень 1: подсчёт сигналов по каждому тону ──────────────────────
        scores: dict[str, int] = {tone: 0 for tone in _TONE_KEYWORDS}
        for tone, keywords in _TONE_KEYWORDS.items():
            for kw in keywords:
                # Считаем вхождения (слово/фраза)
                if kw in text_l:
                    scores[tone] += 1

        max_score = max(scores.values())

        if max_score >= 2:
            # Достаточно сигналов — берём победителя детерминировано
            tone = max(scores, key=lambda t: scores[t])
        elif max_score == 1 and self.cognitive_core:
            # Один сигнал — LLM уточняет
            raw = str(self.cognitive_core.reasoning(
                f"Определи эмоциональный тон текста одним словом "
                f"(neutral/positive/frustrated/urgent/confused/grateful):\n{text}"
            ))
            known_tones = list(_TONE_KEYWORDS.keys()) + ['neutral']
            tone = 'neutral'
            for t in known_tones:
                if t in raw.lower():
                    tone = t
                    break
        else:
            tone = 'neutral'

        # ── Обновляем контекст разговора ──────────────────────────────────────
        if actor_id:
            conv = self._conversations.get(f"conv_{actor_id}")
            if conv:
                conv.emotional_tone = tone

        # ── Обновляем собственное настроение агента ───────────────────────────
        self.emotional_state.update_from_user_tone(tone)

        return tone

    def suggest_response_tone(self, actor_id: str) -> str:
        """Предлагает тон ответа исходя из текущего тона разговора."""
        conv = self._conversations.get(f"conv_{actor_id}")
        if not conv:
            return 'friendly'
        tone_map = {
            'neutral': 'friendly',
            'positive': 'friendly',
            'frustrated': 'empathetic and solution-focused',
            'urgent': 'concise and action-oriented',
            'confused': 'clear and step-by-step',
            'grateful': 'warm and affirming',
        }
        return tone_map.get(conv.emotional_tone, 'friendly')

    # ── Реестр ────────────────────────────────────────────────────────────────

    def list_actors(self) -> list[dict]:
        return [a.to_dict() for a in self._actors.values()]

    def summary(self) -> dict:
        return {
            'actors': len(self._actors),
            'active_conversations': len(self._conversations),
            'high_trust': sum(1 for a in self._actors.values()
                              if a.trust == TrustLevel.HIGH),
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _register_default_actors(self):
        self.register_actor(
            'system', 'System', RelationshipType.MANAGER, TrustLevel.HIGH,
            CommunicationStyle.TECHNICAL
        )
        self.register_actor(
            'user', 'User', RelationshipType.USER, TrustLevel.MEDIUM,
            CommunicationStyle.FRIENDLY
        )

    def _log(self, message: str):
        if self.monitoring:
            self.monitoring.info(message, source='social_model')
        else:
            print(f"[SocialModel] {message}")

    def export_state(self) -> dict:
        """Возвращает состояние актёров + эмоционального состояния для персистентности."""
        actors_data = {}
        for aid, actor in self._actors.items():
            relation = getattr(actor, 'relation', None)
            trust = getattr(actor, 'trust', None)
            preferred_style = getattr(actor, 'preferred_style', None)
            actors_data[aid] = {
                "name": getattr(actor, 'name', aid),
                "relation": relation.value if (relation is not None and hasattr(relation, 'value')) else str(relation or 'unknown'),
                "trust": trust.value if (trust is not None and hasattr(trust, 'value')) else int(trust or 1),
                "preferred_style": (
                    preferred_style.value
                    if (preferred_style is not None and hasattr(preferred_style, 'value'))
                    else str(preferred_style or 'friendly')
                ),
                "interaction_count": getattr(actor, 'interaction_count', 0),
                "notes": getattr(actor, 'notes', []),
                "last_interaction": getattr(actor, 'last_interaction', None),
                "preferences": getattr(actor, 'preferences', {}),
            }
        state = {"actors": actors_data}
        emotional = getattr(self, 'emotional_state', None)
        if emotional and hasattr(emotional, 'to_dict'):
            state["emotional_state"] = emotional.to_dict()
        return state

    def import_state(self, data: dict):
        """Восстанавливает состояние актёров + эмоционального состояния из персистентного хранилища."""
        if not isinstance(data, dict):
            return
        # Обратная совместимость: старый формат — плоский dict актёров
        actors_data = data.get("actors", data) if "actors" in data else data
        emo_data = data.get("emotional_state")
        relation_map = {e.value: e for e in RelationshipType}
        trust_map = {e.value: e for e in TrustLevel}
        trust_name_map = {e.name: e for e in TrustLevel}
        style_map = {e.value: e for e in CommunicationStyle}
        for aid, ad in actors_data.items():
            if aid in self._actors:
                actor = self._actors[aid]
            else:
                rel = relation_map.get(
                    ad.get("relation", "unknown"), RelationshipType.UNKNOWN
                )
                trust_raw = ad.get("trust", 2)
                if isinstance(trust_raw, str):
                    trust = trust_name_map.get(trust_raw, TrustLevel.MEDIUM)
                else:
                    trust = trust_map.get(trust_raw, TrustLevel.MEDIUM)
                actor = SocialActor(aid, ad.get("name", aid), rel, trust)
                self._actors[aid] = actor
            actor.interaction_count = ad.get("interaction_count", 0)
            notes = ad.get("notes", [])
            actor.notes = notes if isinstance(notes, list) else []
            actor.last_interaction = ad.get("last_interaction")
            actor.preferences = ad.get("preferences", {})
            style_val = ad.get("preferred_style", "friendly")
            actor.preferred_style = style_map.get(style_val, CommunicationStyle.FRIENDLY)
            trust_raw = ad.get("trust", 2)
            if isinstance(trust_raw, str):
                actor.trust = trust_name_map.get(trust_raw, actor.trust)
            elif isinstance(trust_raw, int):
                actor.trust = trust_map.get(trust_raw, actor.trust)

        # Восстановление эмоционального состояния
        if emo_data and hasattr(self, 'emotional_state'):
            try:
                self.emotional_state = EmotionalState.from_dict(emo_data)
            except (TypeError, ValueError, KeyError):
                pass


# ── SocialModel: lightweight façade with rule-based adapt_response ─────────────


class SocialModel:
    """
    Lightweight social model with deterministic adapt_response().

    Does NOT require a cognitive_core to function; LLM is used only as an
    optional enhancement when cognitive_core is provided AND
    context['high_priority'] is True.
    """

    _FORMAL_MAP = {
        'привет': 'здравствуйте',
        'окей': 'хорошо',
        'супер': 'отлично',
        'ок': 'принято',
        'hi': 'hello',
        'ok': 'understood',
        'sure': 'certainly',
        'yeah': 'yes',
        'nope': 'no',
    }

    _CASUAL_MAP = {
        'здравствуйте': 'привет',
        'безусловно': 'конечно',
        'данный': 'этот',
    }

    _EMPATHY_PREFIXES = (
        'понимаю', 'сочувствую', 'слышу вас', 'я понимаю',
        'i understand', 'i hear you', 'i can see',
    )

    def __init__(self, cognitive_core=None, monitoring=None):
        self.cognitive_core = cognitive_core
        self.monitoring = monitoring

    # ── Public API ────────────────────────────────────────────────────────────

    def adapt_response(self, response: str, tone: str, style: str,
                       context: dict | None = None) -> str:
        """
        Adapts *response* deterministically according to *tone* and *style*.

        Tone transformations applied first, then style transformations.
        If cognitive_core is available AND context has high_priority=True,
        calls LLM afterwards to further refine.

        Args:
            response: the original response string.
            tone:     'formal' | 'casual' | 'empathetic' | 'professional'
            style:    'concise' | 'detailed' | 'bullet_points' | 'technical'
            context:  optional dict; set {'high_priority': True} for LLM pass.

        Returns:
            Adapted response string (never crashes on empty input).
        """
        if not response:
            return response

        result = response

        # ── Tone ─────────────────────────────────────────────────────────────
        result = self._apply_tone(result, tone)

        # ── Style ─────────────────────────────────────────────────────────────
        result = self._apply_style(result, style)

        # ── Optional LLM refinement ───────────────────────────────────────────
        ctx = context or {}
        if self.cognitive_core and ctx.get('high_priority'):
            try:
                llm_result = self.cognitive_core.reasoning(
                    f"Улучши следующий ответ, сохраняя тон '{tone}' "
                    f"и стиль '{style}':\n\n{result}"
                )
                if llm_result:
                    result = str(llm_result)
            except Exception:  # pylint: disable=broad-except
                pass  # LLM failure is non-fatal

        return result

    # ── Tone helpers ──────────────────────────────────────────────────────────

    def _apply_tone(self, text: str, tone: str) -> str:
        tone = (tone or '').lower()

        if tone == 'formal':
            # Replace casual words with formal equivalents (whole-word, case-insensitive)
            for casual, formal in self._FORMAL_MAP.items():
                text = re.sub(
                    r'\b' + re.escape(casual) + r'\b',
                    formal,
                    text,
                    flags=re.IGNORECASE,
                )

        elif tone == 'casual':
            for formal, casual in self._CASUAL_MAP.items():
                text = re.sub(
                    r'\b' + re.escape(formal) + r'\b',
                    casual,
                    text,
                    flags=re.IGNORECASE,
                )

        elif tone == 'empathetic':
            low = text.lower()
            if not any(low.startswith(p) for p in self._EMPATHY_PREFIXES):
                text = "Понимаю вашу ситуацию. " + text

        elif tone == 'professional':
            # Capitalise first letter
            if text:
                text = text[0].upper() + text[1:]
            # Ensure ends with period
            if text and text[-1] not in '.!?':
                text += '.'

        return text

    # ── Style helpers ─────────────────────────────────────────────────────────

    def _apply_style(self, text: str, style: str) -> str:
        style = (style or '').lower()

        if style == 'concise':
            if len(text) > 200:
                sentences = [s.strip() for s in text.split('.') if s.strip()]
                text = '. '.join(sentences[:2])
                if text and not text.endswith('.'):
                    text += '.'

        elif style == 'detailed':
            if len(text) < 100:
                text = text.rstrip()
                if text and not text.endswith('.'):
                    text += '.'
                text += " Если нужны подробности — готов объяснить."

        elif style == 'bullet_points':
            has_bullets = bool(re.search(r'^\s*[-*•]', text, flags=re.MULTILINE))
            if not has_bullets:
                sentences = [s.strip() for s in text.split('.') if s.strip()]
                text = '\n'.join(f"- {s}" for s in sentences)

        elif style == 'technical':
            # Для technical-стиля делаем формулировки компактнее, без искажения слов.
            text = re.sub(r'\s+', ' ', text).strip()
            text = re.sub(r'\s*[:;]\s*', ': ', text)

        return text

    def _log(self, message: str):
        if self.monitoring:
            self.monitoring.info(message, source='social_model')
        else:
            print(f"[SocialModel] {message}")

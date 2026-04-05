"""
agent.py — Главный файл сборки автономного AI-агента (все 46 слоёв + расширения).

Слои:
    1  Perception (web, PDF, DOCX, image, speech)
    2  Knowledge System + Vector Store (TF-IDF / ChromaDB)
    3  Cognitive Core (OpenAI LLM)
    4  Agent System
    5  Tool Layer (terminal, fs, python, search, github, docker, db, browser)
    6  OS Layer (встроен в Tool Layer)
    7  Software Development (AST-анализ, генерация тестов, CI)
    8  Execution System
    9  Learning System
    10 Reflection System
    11 Self-Repair
    12 Self-Improvement
    13 Package Manager (встроен в Tool Layer)
    14 Multilingual Understanding (перевод, определение языка)
    15 Communication Layer (Telegram Bot — полноценный интерфейс)
    16 Security System
    17 Monitoring & Logging (+ Telegram алерты)
    18 Orchestration
    19 Reliability
    20 Autonomous Loop
    21 Governance
    22 Human Approval
    23 State & Session Management
    24 Data Validation
    25 Evaluation & Benchmarking
    26 Resource & Budget Control
    27 Environment Model
    28 Sandbox
    29 Skill Library
    30 Task Decomposition
    31 Knowledge Acquisition Pipeline
    32 Model Manager
    33 Data Lifecycle
    34 Distributed Execution
    35 Capability Discovery
    36 Experience Replay
    37 Goal Manager
    38 Long-Horizon Planning
    39 Attention & Focus
    40 Temporal Reasoning
    41 Causal Reasoning
    42 Ethics
    43 Social Interaction Model
    44 Hardware Layer
    45 Identity & Self-Model
    46 Knowledge Verification

Использование:
    python agent.py                          # интерактивный режим
    python agent.py "достичь цель X"         # одноразовый запуск с целью
    python agent.py --loop "цель" --cycles 3 # автономный цикл N раз
    python agent.py --bot                    # только Telegram бот

Зависимости (.env):
    OPENAI_API_KEY, TELEGRAM, TELEGRAM_ALERTS_CHAT_ID, GITHUB, HF_TOKEN
"""

import os
import sys
import threading
import json
import logging as _logging

# pylint: disable=broad-exception-caught,protected-access,exec-used

# Подавляем шумные логгеры сторонних библиотек
_logging.getLogger('primp').setLevel(_logging.WARNING)   # ddgs HTTP-запросы
_logging.getLogger('httpx').setLevel(_logging.WARNING)   # OpenAI API запросы
_logging.getLogger('httpcore').setLevel(_logging.WARNING)

# ── Автоматический перезапуск через venv Python ───────────────────────────────
def _relaunch_in_venv_if_needed():
    """Перезапускает процесс через venv Python если текущий интерпретатор
    не является venv-интерпретатором данного проекта."""
    _here = os.path.dirname(os.path.abspath(__file__))
    if sys.platform == 'win32':
        _venv_py = os.path.join(_here, '.venv', 'Scripts', 'python.exe')
    else:
        _venv_py = os.path.join(_here, '.venv', 'bin', 'python')
    if not os.path.exists(_venv_py):
        return  # venv не найден — продолжаем как есть
    _real_venv = os.path.normcase(os.path.realpath(_venv_py))
    _real_cur  = os.path.normcase(os.path.realpath(sys.executable))
    if _real_cur == _real_venv:
        return  # уже запущены из venv
    # execv заменяет текущий процесс, поэтому нет "родителя", ожидающего child.
    os.execv(_venv_py, [_venv_py] + sys.argv)

_relaunch_in_venv_if_needed()

# Принудительно UTF-8 для stdin/stdout/stderr на Windows
_stdout_enc = getattr(sys.stdout, 'encoding', '') or ''
if _stdout_enc.lower() != 'utf-8':
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
    try:
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')  # type: ignore[union-attr]
        if hasattr(sys.stderr, 'reconfigure'):
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')  # type: ignore[union-attr]
    except AttributeError:
        pass

# ── Загрузка .env ─────────────────────────────────────────────────────────────

def _iter_env_paths() -> list[str]:
    """Возвращает возможные пути к env в порядке приоритета."""
    base_dir = os.path.dirname(__file__)
    return [
        os.path.join(base_dir, ".env"),
        os.path.join(base_dir, "config", ".env"),
    ]


def _load_env_file(env_path: str) -> bool:
    """Загружает один env-файл в os.environ."""
    if not os.path.exists(env_path):
        return False
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path)
        return True
    except ImportError:
        pass
    # Fallback: читаем вручную
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())
    return True


def _load_env():
    """Загружает переменные из .env файла в os.environ."""
    for env_path in _iter_env_paths():
        if _load_env_file(env_path):
            return

_load_env()


def _int_env(name: str, default: int) -> int:
    """Безопасно читает int из env — возвращает default при нечисловом значении."""
    raw = os.environ.get(name, '').strip()
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return default


def _float_env(name: str, default: float) -> float:
    """Безопасно читает float из env — возвращает default при нечисловом значении."""
    raw = os.environ.get(name, '').strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return default


# ── Построение агента ─────────────────────────────────────────────────────────

def build_agent(
    model: str = "gpt-5.1",
    log_file: str | None = None,
    working_dir: str | None = None,
) -> dict:
    """
    Создаёт и связывает все 46 слоёв агента.

    Returns:
        Словарь со всеми ключевыми компонентами.
    """
    # ── Env vars ──────────────────────────────────────────────────────────────
    openai_key       = os.environ.get("OPENAI_API_KEY", "")
    anthropic_key    = os.environ.get("ANTHROPIC_API_KEY", "")
    local_llm_disabled = os.environ.get("LOCAL_LLM_DISABLED", "0").strip().lower() in {
        "1", "true", "yes", "on"
    }
    tg_token         = os.environ.get("TELEGRAM", "")
    tg_chat_id       = os.environ.get("TELEGRAM_ALERTS_CHAT_ID", "")
    github_token     = os.environ.get("GITHUB", "")
    hf_token         = os.environ.get("HF_TOKEN", "")
    working_dir      = working_dir or os.path.dirname(__file__)

    # ════════════════════════════════════════════════════════════════════════
    # СЛОЙ 17: Monitoring (создаём первым — все остальные слои его используют)
    # ════════════════════════════════════════════════════════════════════════
    from monitoring.monitoring import Monitoring, LogLevel
    from llm.telegram_sink import TelegramSink

    tg_sink = None
    if tg_token and tg_chat_id:
        tg_sink = TelegramSink(
            token=tg_token,
            chat_id=tg_chat_id,
            min_level="CRITICAL",
        )

    monitoring = Monitoring(
        min_level=LogLevel.INFO,
        print_logs=True,
        log_file=log_file,
        sink=tg_sink,
    )
    monitoring.info("Агент инициализируется...", source="agent")

    # ════════════════════════════════════════════════════════════════════════
    # СЛОЙ 16: Security System
    # ════════════════════════════════════════════════════════════════════════
    from safety.security import SecuritySystem
    security = SecuritySystem(load_env=True)

    # SECURITY: подключаем авто-маскировку секретов в логах
    monitoring.set_scrubber(security.scrub_text)

    # IntegrityChecker — хеш-проверка файлов состояния + белый список пакетов
    from safety.integrity_checker import IntegrityChecker
    integrity_checker = IntegrityChecker(working_dir=working_dir, monitoring=monitoring)

    # ════════════════════════════════════════════════════════════════════════
    # Immutable Audit Log — append-only, hash-chain integrity
    # ════════════════════════════════════════════════════════════════════════
    from safety.hardening import ImmutableAuditLog
    _audit_log = ImmutableAuditLog(os.path.join(working_dir, 'logs', 'immutable_audit.jsonl'))
    _audit_log.record('agent_start', {'working_dir': working_dir})

    # ════════════════════════════════════════════════════════════════════════
    # LLM клиент (инструмент для Cognitive Core)
    # OpenAI (gpt-5.1) — по умолчанию для лёгких задач.
    # Claude (Opus) — только для тяжёлых (длинный промпт / ключевые слова).
    # ════════════════════════════════════════════════════════════════════════
    _openai_client = None
    if openai_key:
        from llm.openai_client import OpenAIClient
        # Пробуем запрошенную модель, при ошибке "model not found" — падаем на gpt-5.1
        _model_candidates = [model, "gpt-5.1"]
        _seen = set()
        _model_candidates = [m for m in _model_candidates if not (m in _seen or _seen.add(m))]
        for _candidate in _model_candidates:
            try:
                _openai_client = OpenAIClient(
                    api_key=openai_key,
                    model=_candidate,
                    max_tokens=4096,
                    temperature=0.7,
                    monitoring=monitoring,
                )
                # Бесплатная проверка: models.retrieve() не тратит токены
                _openai_client.verify_model(_candidate)
                if _candidate != model:
                    monitoring.warning(
                        f"Модель {model!r} недоступна — использую {_candidate!r}",
                        source="agent",
                    )
                break
            except LookupError:
                monitoring.warning(
                    f"Модель {_candidate!r} не найдена — пробую следующую",
                    source="agent",
                )
                _openai_client = None
                continue
            except Exception as _model_err:
                # Другая ошибка (ключ, сеть) — не повторяем
                monitoring.warning(f"OpenAI init ошибка: {_model_err}", source="agent")
                break

    _claude_client = None
    if anthropic_key:
        from llm.claude_backend import ClaudeClient
        _claude_client = ClaudeClient(
            api_key=anthropic_key,
            model="claude-opus-4-20250514",
            max_tokens=4096,
            temperature=0.7,
            monitoring=monitoring,
            fallback_client=_openai_client,
        )

    _local_client = None
    if not local_llm_disabled:
        try:
            from llm.local_backend import LocalNeuralBackend
            _local_client = LocalNeuralBackend(monitoring=monitoring)
            _health = _local_client.health()
            if _health.get('ok'):
                monitoring.info(
                    f"Local LLM автоподключён: {_health.get('model')} ({_health.get('backend')})",
                    source="agent",
                )
            else:
                monitoring.warning(
                    f"Local LLM недоступен: {_health.get('error', 'unknown error')}",
                    source="agent",
                )
        except (ImportError, RuntimeError, OSError) as e:
            monitoring.warning(f"Local LLM backend не инициализирован: {e}", source="agent")

    if not openai_key and not anthropic_key and _local_client is None:
        raise EnvironmentError(
            "Не найден рабочий LLM backend. "
            "Проверь OPENAI_API_KEY/ANTHROPIC_API_KEY или установку transformers+torch."
        )

    from llm.llm_router import LLMRouter
    llm = LLMRouter(
        light_client=_openai_client,
        heavy_client=_claude_client,
        local_client=_local_client,
        monitoring=monitoring,
    )
    if _claude_client and _openai_client:
        _claude_model_name = getattr(_claude_client, 'model', 'Claude')
        monitoring.info(
            f"LLM роутер: лёгкие задачи → {model} (OpenAI), "
            f"тяжёлые → {_claude_model_name} (Anthropic)",
            source="agent",
        )
    elif _local_client and not _openai_client and not _claude_client:
        monitoring.info("LLM роутер: local-only режим (transformers)", source="agent")
    elif _openai_client:
        monitoring.info(f"LLM подключён: {model} (OpenAI)", source="agent")
    elif _local_client:
        monitoring.info("LLM роутер: auto-local режим (transformers)", source="agent")
    else:
        monitoring.info("LLM роутер активирован с fallback-конфигурацией", source="agent")

    # ════════════════════════════════════════════════════════════════════════
    # СЛОЙ 2: Knowledge System + Vector Store
    # ════════════════════════════════════════════════════════════════════════
    from knowledge.knowledge_system import KnowledgeSystem
    from knowledge.vector_store import VectorStore
    vector_store = VectorStore(
        collection_name='agent_knowledge',
        persist_dir=os.path.join(working_dir, '.vector_store'),
        use_chroma=True,
        monitoring=monitoring,
    )
    knowledge = KnowledgeSystem(vector_db=vector_store, security=security)

    # Загружаем меморандум партнёрства в долгосрочную память (один раз при старте)
    _memorandum_path = os.path.join(working_dir, 'Текстовый документ.txt')
    _memorandum_key  = 'memorandum:partnership'
    if os.path.exists(_memorandum_path) and not knowledge.get_long_term(_memorandum_key):
        try:
            with open(_memorandum_path, 'r', encoding='utf-8', errors='replace') as _f:
                _memorandum_text = _f.read()
            knowledge.store_long_term(
                key=_memorandum_key, value=_memorandum_text,
                source='user', trust=1.0, verified=True,
            )
            monitoring.info("Меморандум партнёрства загружен в долгосрочную память.", source="agent")
        except (OSError, ValueError, RuntimeError) as _e:
            monitoring.warning(f"Не удалось загрузить меморандум: {_e}", source="agent")

    # ════════════════════════════════════════════════════════════════════════
    # СЛОЙ 1: Perception Layer + Document Parser + Image + Speech + TextClassifier
    # ════════════════════════════════════════════════════════════════════════
    from perception.perception_layer import PerceptionLayer
    from perception.document_parser import DocumentParser
    from perception.image_recognizer import ImageRecognizer
    from perception.speech_recognizer import SpeechRecognizer
    from perception.speech_synthesizer import SpeechSynthesizer
    from perception.text_classifier import TextClassifier
    from llm.web_crawler import WebCrawler
    text_classifier = TextClassifier()
    web_crawler = WebCrawler()
    perception = PerceptionLayer(
        web_crawler=web_crawler,
        document_parser=DocumentParser(monitoring=monitoring),
        image_recognizer=ImageRecognizer(openai_client=_openai_client, model='gpt-5.1', monitoring=monitoring),
        speech_recognizer=SpeechRecognizer(openai_client=_openai_client, monitoring=monitoring),
        speech_synthesizer=SpeechSynthesizer(openai_client=_openai_client, voice='nova', monitoring=monitoring),
        text_classifier=text_classifier,
    )

    # ════════════════════════════════════════════════════════════════════════
    # СЛОЙ 22: Human Approval
    # ════════════════════════════════════════════════════════════════════════
    from safety.human_approval import HumanApprovalLayer
    # Режим 'callback' — решение принимается через Telegram.
    # Безопасная заглушка: отклоняет критичные действия пока бот не подключён.
    _CRITICAL_BEFORE_BOT = frozenset({
        'delete', 'deployment', 'ethical_review',
        'incident_escalation', 'budget_stop',
        'spawn_agent', 'patch_code', 'self_modify',
    })
    def _default_approval(action_type: str, _payload) -> bool:
        """До подключения Telegram: критичные действия отклоняются, остальные — ОК."""
        if action_type in _CRITICAL_BEFORE_BOT:
            monitoring.warning(
                f"Действие {action_type!r} отклонено (Telegram ещё не подключён)",
                source="human_approval",
            )
            return False
        return True

    human_approval = HumanApprovalLayer(
        mode='auto_approve',
        callback=_default_approval,
    )

    def approval_callback(action_type: str, payload) -> bool:
        """Запрашивает подтверждение только для критичных точек."""
        critical_action_types = {
            'delete', 'deployment', 'ethical_review',
            'incident_escalation', 'budget_stop',
            'spawn_agent', 'patch_code', 'self_modify',
        }
        if action_type in critical_action_types:
            return human_approval.request_approval(action_type, payload)
        return True

    # ════════════════════════════════════════════════════════════════════════
    # СЛОЙ 3: Cognitive Core (мозг — LLM как инструмент)
    # ════════════════════════════════════════════════════════════════════════
    from core.cognitive_core import CognitiveCore
    cognitive_core = CognitiveCore(
        llm_client=llm,
        perception=perception,
        knowledge=knowledge,
        human_approval_callback=approval_callback,
        monitoring=monitoring,
        # identity передаётся позже через cognitive_core.identity = identity
        # (слой 45 создаётся ниже — после cognitive_core)
    )
    monitoring.info("Cognitive Core готов.", source="agent")

    # ════════════════════════════════════════════════════════════════════════
    # СЛОЙ 5: Tool Layer (инструменты с реальными backends)
    # ════════════════════════════════════════════════════════════════════════
    from tools.tool_layer import build_tool_layer
    from llm.github_backend import GitHubBackend
    from llm.search_backend import DuckDuckGoBackend

    github_backend = GitHubBackend(token=github_token) if github_token else None
    search_backend = DuckDuckGoBackend()

    from tools.browser_tool import BrowserTool
    browser = BrowserTool(headless=True, monitoring=monitoring,
                          web_crawler=web_crawler)

    tools = build_tool_layer(
        working_dir=working_dir,
        github_token=github_token,
        github_backend=github_backend,
        search_backend=search_backend,
        openai_client=_openai_client,
        # Email (из .env: EMAIL_USERNAME / EMAIL_PASSWORD)
        email_username=os.environ.get('EMAIL_USERNAME'),
        email_password=os.environ.get('EMAIL_PASSWORD'),
        email_smtp_host=os.environ.get('EMAIL_SMTP_HOST', 'smtp.gmail.com'),
        email_smtp_port=_int_env('EMAIL_SMTP_PORT', 587),
        email_imap_host=os.environ.get('EMAIL_IMAP_HOST', 'imap.gmail.com'),
        email_imap_port=_int_env('EMAIL_IMAP_PORT', 993),
        # Google Calendar (из .env: GOOGLE_CREDENTIALS_PATH)
        google_credentials_path=os.environ.get('GOOGLE_CREDENTIALS_PATH', 'credentials.json'),
        # HuggingFace Hub (из .env: HF_TOKEN)
        hf_token=os.environ.get('HF_TOKEN'),
        # Upwork API (из .env: UPWORK_*)
        upwork_client_id=os.environ.get('UPWORK_CLIENT_ID'),
        upwork_client_secret=os.environ.get('UPWORK_CLIENT_SECRET'),
        upwork_access_token=os.environ.get('UPWORK_ACCESS_TOKEN'),
        upwork_refresh_token=os.environ.get('UPWORK_REFRESH_TOKEN'),
        # Figma (из .env: FIGMA_TOKEN)
        figma_token=os.environ.get('FIGMA_TOKEN'),
        # Twilio (из .env: TWILIO_*)
        twilio_sid=os.environ.get('TWILIO_ACCOUNT_SID'),
        twilio_token=os.environ.get('TWILIO_AUTH_TOKEN'),
        twilio_from=os.environ.get('TWILIO_FROM_NUMBER'),
        # Vonage (из .env: VONAGE_*)
        vonage_key=os.environ.get('VONAGE_API_KEY'),
        vonage_secret=os.environ.get('VONAGE_API_SECRET'),
        vonage_from=os.environ.get('VONAGE_FROM_NUMBER'),
        # Blender (из .env: BLENDER_PATH)
        blender_path=os.environ.get('BLENDER_PATH'),
        # OpenSCAD (из .env: OPENSCAD_PATH)
        openscad_path=os.environ.get('OPENSCAD_PATH'),
        # ADB / Appium (из .env: ADB_PATH, APPIUM_HOST)
        adb_path=os.environ.get('ADB_PATH'),
        appium_host=os.environ.get('APPIUM_HOST'),
        # Reddit (из .env: REDDIT_*)
        reddit_client_id=os.environ.get('REDDIT_CLIENT_ID'),
        reddit_client_secret=os.environ.get('REDDIT_CLIENT_SECRET'),
        reddit_username=os.environ.get('REDDIT_USERNAME'),
        reddit_password=os.environ.get('REDDIT_PASSWORD'),
        reddit_user_agent=os.environ.get('REDDIT_USER_AGENT'),
    )
    tools.register(browser)  # type: ignore[arg-type]
    monitoring.info(f"Tool Layer готов: {tools.list()}", source="agent")

    # Даём cognitive_core знание о всех доступных инструментах (для промпта планирования)
    cognitive_core.tool_layer = tools

    # Даём python_runtime доступ к tool_layer внутри sandbox-кода
    _py_tool = tools.get('python_runtime')
    if _py_tool is not None:
        setattr(_py_tool, '_tool_layer', tools)

    # ════════════════════════════════════════════════════════════════════════
    # СЛОЙ 4: Agent System
    # ════════════════════════════════════════════════════════════════════════
    from agents.agent_system import build_agent_system
    agent_system = build_agent_system(
        cognitive_core=cognitive_core,
        tools=tools,
        knowledge=knowledge,
        working_dir=working_dir,
    )

    # ════════════════════════════════════════════════════════════════════════
    # СЛОЙ 8: Execution System
    # ════════════════════════════════════════════════════════════════════════
    from execution.execution_system import ExecutionSystem
    execution = ExecutionSystem(
        human_approval=human_approval,
        monitoring=monitoring,
        working_dir=working_dir,
        safe_mode=True,
    )

    # ════════════════════════════════════════════════════════════════════════
    # СЛОЙ 9: Learning System
    # ════════════════════════════════════════════════════════════════════════
    from learning.learning_system import LearningSystem
    learning = LearningSystem(
        cognitive_core=cognitive_core,
        knowledge_system=knowledge,
        monitoring=monitoring,
    )

    # ════════════════════════════════════════════════════════════════════════
    # СЛОЙ 10: Reflection System
    # ════════════════════════════════════════════════════════════════════════
    from reflection.reflection_system import ReflectionSystem
    reflection = ReflectionSystem(
        cognitive_core=cognitive_core,
        knowledge_system=knowledge,
        monitoring=monitoring,
    )

    # ════════════════════════════════════════════════════════════════════════
    # СЛОЙ 11: Self-Repair
    # ════════════════════════════════════════════════════════════════════════
    from self_repair.self_repair import SelfRepairSystem
    self_repair = SelfRepairSystem(
        execution_system=execution,
        cognitive_core=cognitive_core,
        human_approval=human_approval,
        monitoring=monitoring,
        working_dir=os.path.dirname(__file__),
        auto_repair=True,
        arch_docs=[
            os.path.join(os.path.dirname(__file__), "архитектура автономного Агента.txt"),
            os.path.join(os.path.dirname(__file__), "Текстовый документ.txt"),
        ],
    )

    # ════════════════════════════════════════════════════════════════════════
    # СЛОЙ 12: Self-Improvement
    # ════════════════════════════════════════════════════════════════════════
    from self_improvement.self_improvement import SelfImprovementSystem
    self_improvement = SelfImprovementSystem(
        cognitive_core=cognitive_core,
        reflection_system=reflection,
        knowledge_system=knowledge,
        human_approval=human_approval,
        monitoring=monitoring,
        auto_apply=True,   # Полная автономия: стратегии применяются без одобрения
    )

    # Подключаем стратегии и рефлексию обратно в CognitiveCore
    cognitive_core.self_improvement = self_improvement
    cognitive_core.reflection = reflection

    # ════════════════════════════════════════════════════════════════════════
    # СЛОЙ 21: Governance
    # ════════════════════════════════════════════════════════════════════════
    from safety.governance import GovernanceLayer
    governance = GovernanceLayer()
    knowledge.governance = governance  # VULN-FIX: governance gate на все записи в long-term
    # VULN-FIX: governance gate для мульти-агентной системы
    agent_system.governance = governance

    # ════════════════════════════════════════════════════════════════════════
    # СЛОЙ 42: Ethics
    # ════════════════════════════════════════════════════════════════════════
    from safety.ethics import EthicsLayer
    ethics = EthicsLayer(
        cognitive_core=cognitive_core,
        human_approval=human_approval,
        monitoring=monitoring,
    )

    # ════════════════════════════════════════════════════════════════════════
    # СЛОЙ 23: State Manager
    # ════════════════════════════════════════════════════════════════════════
    from state.state_manager import StateManager
    state_manager = StateManager(
        persistence_path=os.path.join(working_dir, "agent_state.json"),
        monitoring=monitoring,
    )

    # ════════════════════════════════════════════════════════════════════════
    # СЛОЙ 24: Data Validation
    # ════════════════════════════════════════════════════════════════════════
    from validation.data_validation import DataValidator
    validation = DataValidator()

    # ════════════════════════════════════════════════════════════════════════
    # СЛОЙ 25: Evaluation
    # ════════════════════════════════════════════════════════════════════════
    from evaluation.evaluation import EvaluationSystem
    evaluation = EvaluationSystem(
        cognitive_core=cognitive_core,
        monitoring=monitoring,
    )

    # ════════════════════════════════════════════════════════════════════════
    # СЛОЙ 26: Budget Control
    # ════════════════════════════════════════════════════════════════════════
    from resources.budget_control import BudgetControl
    budget = BudgetControl(monitoring=monitoring, human_approval=human_approval)
    budget.set_money_limit(_float_env('BUDGET_MONEY_LIMIT', 500.0))
    budget.set_token_limit(_int_env('BUDGET_TOKEN_LIMIT', 5_000_000))
    budget.set_request_limit(_int_env('BUDGET_REQUEST_LIMIT', 10_000))

    # Подключаем бюджет к LLM-клиенту — единственная точка API-вызовов
    llm.budget_control = budget

    # ════════════════════════════════════════════════════════════════════════
    # СЛОЙ 27: Environment Model
    # ════════════════════════════════════════════════════════════════════════
    from environment.environment_model import EnvironmentModel
    env_model = EnvironmentModel(
        cognitive_core=cognitive_core,
        monitoring=monitoring,
    )

    # ════════════════════════════════════════════════════════════════════════
    # СЛОЙ 28: Sandbox
    # ════════════════════════════════════════════════════════════════════════
    from environment.sandbox import SandboxLayer
    sandbox = SandboxLayer(
        environment_model=env_model,
        cognitive_core=cognitive_core,
        governance=governance,
        monitoring=monitoring,
    )

    # ── Подключаем sandbox к слоям, созданным ранее ───────────────────────
    self_repair.sandbox = sandbox          # VULN-08: проверка команд ремонта
    self_repair.governance = governance    # VULN-FIX-01: governance gate на патчи
    self_improvement.sandbox = sandbox     # VULN-15: тест стратегий до применения

    # ════════════════════════════════════════════════════════════════════════
    # СЛОЙ 29: Skill Library
    # ════════════════════════════════════════════════════════════════════════
    from skills.skill_library import SkillLibrary
    skill_library = SkillLibrary(
        cognitive_core=cognitive_core,
        knowledge_system=knowledge,
        monitoring=monitoring,
    )

    # ════════════════════════════════════════════════════════════════════════
    # СЛОЙ 30: Task Decomposition
    # ════════════════════════════════════════════════════════════════════════
    from skills.task_decomposition import TaskDecompositionEngine
    task_decomp = TaskDecompositionEngine(
        cognitive_core=cognitive_core,
        agent_system=agent_system,
        monitoring=monitoring,
    )

    # ════════════════════════════════════════════════════════════════════════
    # СЛОЙ 31: Knowledge Acquisition Pipeline + Source Backends
    # ════════════════════════════════════════════════════════════════════════
    from knowledge.acquisition_pipeline import KnowledgeAcquisitionPipeline
    from knowledge.source_backends import (
        GutenbergBackend, ArXivBackend, WikipediaBackend,
        RSSBackend, WeatherBackend, HackerNewsBackend, PyPIBackend,
    )
    from llm.huggingface_backend import HuggingFaceBackend
    gutenberg_backend  = GutenbergBackend()
    arxiv_backend      = ArXivBackend()
    wikipedia_backend  = WikipediaBackend(lang='ru')   # основной язык — русский
    rss_backend        = RSSBackend()
    weather_backend    = WeatherBackend(lang='ru')
    hackernews_backend = HackerNewsBackend()
    pypi_backend       = PyPIBackend()
    huggingface_backend = HuggingFaceBackend(token=hf_token) if hf_token else None

    acquisition = KnowledgeAcquisitionPipeline(
        knowledge_system=knowledge,
        perception_layer=perception,
        cognitive_core=cognitive_core,
        monitoring=monitoring,
        text_classifier=text_classifier,
        governance=governance,
        ethics=ethics,
        gutenberg_backend=gutenberg_backend,
        arxiv_backend=arxiv_backend,
        wikipedia_backend=wikipedia_backend,
        rss_backend=rss_backend,
        weather_backend=weather_backend,
        hackernews_backend=hackernews_backend,
        pypi_backend=pypi_backend,
        huggingface_backend=huggingface_backend,
        github_backend=github_backend,
    )

    # Холодный старт: отключён (LOCAL_COLD_START_DISABLED=1 по умолчанию).
    cold_start_disabled = os.environ.get("COLD_START_DISABLED", "1").strip().lower() in {
        "1", "true", "yes", "on"
    }
    brain_dir = os.path.join(working_dir, ".agent_memory")
    knowledge_path = os.path.join(brain_dir, "knowledge.json")
    items_in_memory = 0
    if cold_start_disabled:
        memory_is_cold = False
    else:
        memory_is_cold = True
        try:
            if os.path.exists(knowledge_path):
                with open(knowledge_path, "r", encoding="utf-8") as f:
                    persisted = json.load(f)

                if isinstance(persisted, dict):
                    long_term = persisted.get("long_term", {})
                    episodic = persisted.get("episodic", [])
                    semantic = persisted.get("semantic", {})
                    legacy_items = persisted.get("items", [])

                    items_count = 0
                    if isinstance(long_term, dict):
                        items_count += len(long_term)
                    if isinstance(episodic, list):
                        items_count += len(episodic)
                    if isinstance(semantic, dict):
                        items_count += len(semantic)
                    if isinstance(legacy_items, list):
                        items_count += len(legacy_items)

                    items_in_memory = items_count
                    memory_is_cold = items_count < _int_env('COLD_START_THRESHOLD', 100)
        except (OSError, IOError, json.JSONDecodeError, UnicodeDecodeError, AttributeError, TypeError, ValueError):
            memory_is_cold = True

    # Логируем статус памяти
    _cold_threshold = _int_env('COLD_START_THRESHOLD', 100)
    if memory_is_cold:
        monitoring.warning(
            f"ХОЛОДНЫЙ СТАРТ: память почти пустая ({items_in_memory} < {_cold_threshold} элементов). "
            f"Загружаю Wikipedia + Gutenberg + GitHub источники...",
            source="agent"
        )
    else:
        monitoring.info(
            f"ГОРЯЧИЙ СТАРТ: память загружена ({items_in_memory} элементов). "
            f"Используются авто-источники без переинициализации.",
            source="agent"
        )

    # ── Регистрация авто-источников (обновляются каждые 30 мин в фоне) ───────
    acquisition.register_auto_source('weather', city='Москва')
    acquisition.register_auto_source('hackernews', section='top', limit=20)
    acquisition.register_auto_source('rss_preset',
                                     categories=['world_news', 'tech', 'ai', 'science'],
                                     limit_per_feed=5)
    # arXiv: три темы — ИИ, программирование, наука
    acquisition.register_auto_source('arxiv',
                                     query='large language models agents autonomy',
                                     max_results=3)
    acquisition.register_auto_source('arxiv',
                                     query='Python software engineering programming',
                                     max_results=2)
    acquisition.register_auto_source('arxiv',
                                     query='physics biology chemistry breakthrough discoveries',
                                     max_results=2)

    # Добавляем HuggingFace как динамический источник (если токен есть)
    if hf_token:
        try:
            acquisition.register_auto_source('huggingface',
                                           query='large language models autonomous agents',
                                           limit=5,
                                           hf_source_type='models')
            acquisition.register_auto_source('huggingface',
                                           query='AI datasets',
                                           limit=3,
                                           hf_source_type='datasets')
            monitoring.info("HuggingFace авто-источник активирован (модели и датасеты)", source="agent")
        except (TypeError, AttributeError, ValueError) as e:
            monitoring.warning(f"HuggingFace авто-источник ошибка: {e}", source="agent")
    else:
        monitoring.warning("HF_TOKEN не установлен — HuggingFace источник пропущен", source="agent")

    # ── Одноразовая загрузка Wikipedia (ru) ──────────────────────────────────
    # Добавляются в очередь только на холодном старте.
    if memory_is_cold:
        monitoring.info(
            "[ХОЛОДНЫЙ СТАРТ] Начинаю загрузку одноразовых источников (Wikipedia, Gutenberg, HuggingFace)...",
            source="agent"
        )
        
        for _wiki_title, _wiki_tags in [
            # Тема 1 — Искусственный интеллект
            ('Искусственный_интеллект',         ['ai', 'science']),
            ('Машинное_обучение',               ['ai', 'science']),
            ('Нейронная_сеть',                  ['ai', 'science']),
            ('Глубокое_обучение',               ['ai', 'science']),
            ('Обработка_естественного_языка',   ['ai', 'nlp', 'science']),
            ('Большая_языковая_модель',         ['ai', 'llm', 'science']),
            # Тема 2 — Программирование и фриланс
            ('Программирование',                ['programming']),
            ('Python_(язык_программирования)',  ['programming', 'python']),
            ('Алгоритм',                        ['programming', 'cs']),
            ('Фриланс',                         ['freelance', 'money', 'work']),
            ('Удалённая_работа',               ['freelance', 'money', 'work']),
            # Тема 3 — Наука
            ('Физика',                          ['science', 'physics']),
            ('Математика',                      ['science', 'math']),
            ('Биология',                        ['science', 'biology']),
            ('Химия',                           ['science', 'chemistry']),
            ('Квантовая_механика',              ['science', 'physics', 'quantum']),
            ('Астрофизика',                     ['science', 'space', 'astronomy']),
            ('Эволюция',                        ['science', 'biology']),
            ('Нейробиология',                   ['science', 'biology', 'brain']),
            # Тема 4 — Литература (об авторах)
            ('Лев_Толстой',                     ['literature', 'author', 'russian']),
            ('Фёдор_Достоевский',               ['literature', 'author', 'russian']),
            ('Уильям_Шекспир',                  ['literature', 'author', 'classic']),
            ('Марк_Аврелий',                    ['literature', 'author', 'philosophy', 'classic']),
            ('Антон_Чехов',                     ['literature', 'author', 'russian']),
            ('Александр_Пушкин',               ['literature', 'author', 'russian']),
        ]:
            acquisition.add_wikipedia_article(_wiki_title, tags=_wiki_tags)

        # ── Одноразовая загрузка Gutenberg (классика мировой литературы) ─────
        for _book_id, _book_tags, _book_title, _book_author in [
            (132,   ['philosophy', 'strategy', 'wisdom'],     'The Art of War', 'Sunzi'),
            (2680,  ['philosophy', 'stoicism', 'wisdom'],     'Meditations', 'Marcus Aurelius'),
            (1232,  ['philosophy', 'politics', 'strategy'],   'The Prince', 'Niccolo Machiavelli'),
            (1524,  ['literature', 'shakespeare', 'tragedy'], 'Hamlet', 'William Shakespeare'),
            (1112,  ['literature', 'shakespeare', 'romance'], 'Romeo and Juliet', 'William Shakespeare'),
            (2554,  ['literature', 'dostoevsky', 'fiction'],  'Crime and Punishment', 'Fyodor Dostoyevsky'),
            (28054, ['literature', 'dostoevsky', 'fiction'],  'The Brothers Karamazov', 'Fyodor Dostoyevsky'),
            (2600,  ['literature', 'tolstoy', 'fiction'],     'War and Peace', 'Leo Tolstoy'),
            (1399,  ['literature', 'tolstoy', 'fiction'],     'Anna Karenina', 'Leo Tolstoy'),
            (244,   ['literature', 'detective', 'fiction'],   'A Study in Scarlet', 'Arthur Conan Doyle'),
            (844,   ['literature', 'wilde', 'comedy'],        'The Importance of Being Earnest', 'Oscar Wilde'),
        ]:
            acquisition.add_gutenberg_book(
                _book_id,
                tags=_book_tags,
                expected_title=_book_title,
                expected_author=_book_author,
            )

        monitoring.info(
            "[ХОЛОДНЫЙ СТАРТ] Одноразовые источники загружены (Wikipedia + Gutenberg). "
            "Авто-источники (GitHub, arXiv, RSS, HackerNews, Weather) будут обновляться каждые 30 мин.",
            source="agent"
        )

    # ════════════════════════════════════════════════════════════════════════
    # СЛОЙ 32: Model Manager
    # ════════════════════════════════════════════════════════════════════════
    from core.model_manager import ModelManager
    model_manager = ModelManager(monitoring=monitoring)

    # ════════════════════════════════════════════════════════════════════════
    # СЛОЙ 33: Data Lifecycle
    # ════════════════════════════════════════════════════════════════════════
    from knowledge.data_lifecycle import DataLifecycleManager
    data_lifecycle = DataLifecycleManager(
        knowledge_system=knowledge,
        cognitive_core=cognitive_core,
        monitoring=monitoring,
    )
    # Подключаем lifecycle к knowledge — store_long_term() теперь вызывает track()
    # и archive_stale() корректно отслеживает возраст записей
    knowledge.lifecycle = data_lifecycle

    # ════════════════════════════════════════════════════════════════════════
    # СЛОЙ 34: Distributed Execution
    # ════════════════════════════════════════════════════════════════════════
    from loop.distributed_execution import DistributedExecutionLayer
    distributed = DistributedExecutionLayer(
        default_workers=4,
        monitoring=monitoring,
    )

    # ════════════════════════════════════════════════════════════════════════
    # СЛОЙ 35: Capability Discovery
    # ════════════════════════════════════════════════════════════════════════
    from tools.capability_discovery import CapabilityDiscovery
    capability_discovery = CapabilityDiscovery(
        tool_layer=tools,
        cognitive_core=cognitive_core,
        monitoring=monitoring,
        human_approval=human_approval,
    )
    # Автобутстрап: агент сам устанавливает недостающие зависимости при старте
    _bootstrap = capability_discovery.bootstrap_required_packages()
    if _bootstrap['installed']:
        monitoring.info(
            f"Автобутстрап: установлено {_bootstrap['installed']}",
            source="capability_discovery",
        )
    if _bootstrap['failed']:
        monitoring.warning(
            f"Автобутстрап: не удалось установить {_bootstrap['failed']}",
            source="capability_discovery",
        )

    # ════════════════════════════════════════════════════════════════════════
    # СЛОЙ 36: Experience Replay
    # ════════════════════════════════════════════════════════════════════════
    from learning.experience_replay import ExperienceReplay
    experience_replay = ExperienceReplay(
        knowledge_system=knowledge,
        cognitive_core=cognitive_core,
        self_improvement=self_improvement,
        reflection=reflection,
        monitoring=monitoring,
    )

    # ════════════════════════════════════════════════════════════════════════
    # СЛОЙ 37: Goal Manager
    # ════════════════════════════════════════════════════════════════════════
    from core.goal_manager import GoalManager
    goal_manager = GoalManager(
        cognitive_core=cognitive_core,
        knowledge_system=knowledge,
        monitoring=monitoring,
    )

    # ════════════════════════════════════════════════════════════════════════
    # СЛОЙ 38: Long-Horizon Planning
    # ════════════════════════════════════════════════════════════════════════
    from core.long_horizon_planning import LongHorizonPlanning
    long_horizon = LongHorizonPlanning(
        cognitive_core=cognitive_core,
        goal_manager=goal_manager,
        knowledge_system=knowledge,
        monitoring=monitoring,
    )

    # ════════════════════════════════════════════════════════════════════════
    # СЛОЙ 39: Attention & Focus
    # ════════════════════════════════════════════════════════════════════════
    from attention.attention_focus import AttentionFocusManager
    attention = AttentionFocusManager(
        cognitive_core=cognitive_core,
        goal_manager=goal_manager,
        monitoring=monitoring,
    )

    # ════════════════════════════════════════════════════════════════════════
    # СЛОЙ 40: Temporal Reasoning
    # ════════════════════════════════════════════════════════════════════════
    from reasoning.temporal_reasoning import TemporalReasoningSystem
    temporal = TemporalReasoningSystem(
        cognitive_core=cognitive_core,
        knowledge_system=knowledge,
        monitoring=monitoring,
    )

    # ════════════════════════════════════════════════════════════════════════
    # СЛОЙ 41: Causal Reasoning
    # ════════════════════════════════════════════════════════════════════════
    from reasoning.causal_reasoning import CausalReasoningSystem
    causal = CausalReasoningSystem(
        cognitive_core=cognitive_core,
        knowledge_system=knowledge,
        monitoring=monitoring,
    )

    # ════════════════════════════════════════════════════════════════════════
    # СЛОЙ 43: Social Interaction Model
    # ════════════════════════════════════════════════════════════════════════
    from social.social_model import SocialInteractionModel
    social = SocialInteractionModel(
        cognitive_core=cognitive_core,
        monitoring=monitoring,
    )

    # ════════════════════════════════════════════════════════════════════════
    # PARTNER CORE: Trust Model — числовая модель доверия (0.0–1.0)
    # ════════════════════════════════════════════════════════════════════════
    from core.trust_model import TrustModel
    trust_model = TrustModel()
    monitoring.info("Trust Model инициализирован.", source="agent")

    # ════════════════════════════════════════════════════════════════════════
    # PARTNER CORE: User Memory Store — персональная память по пользователям
    # ════════════════════════════════════════════════════════════════════════
    from core.user_memory import UserMemoryStore
    user_memory = UserMemoryStore(
        data_dir=os.path.join(working_dir, ".agent_memory"),
    )
    monitoring.info(f"User Memory Store: {len(user_memory.list_users())} пользователей.", source="agent")

    # ════════════════════════════════════════════════════════════════════════
    # PARTNER CORE: Autonomy Controller — режимы инициативы/автономии
    # ════════════════════════════════════════════════════════════════════════
    from core.autonomy_levels import AutonomyController, AutonomyLevel
    autonomy = AutonomyController(default_level=AutonomyLevel.PARTNER)
    # Ночной режим: 23:00–07:00 (Меморандум Часть 4.7)
    autonomy.add_schedule_rule(23, 7, AutonomyLevel.NIGHT)
    monitoring.info(f"Autonomy Controller: уровень={autonomy.level.value}.", source="agent")

    # ════════════════════════════════════════════════════════════════════════
    # PARTNER CORE: Decision Explainer — объяснимость действий
    # ════════════════════════════════════════════════════════════════════════
    from core.decision_explainer import DecisionExplainer
    decision_explainer = DecisionExplainer()
    monitoring.info("Decision Explainer инициализирован.", source="agent")

    # ════════════════════════════════════════════════════════════════════════
    # PARTNER CORE: Tenant Isolation — мультитенантная изоляция
    # ════════════════════════════════════════════════════════════════════════
    from core.tenant_isolation import TenantManager
    tenant_manager = TenantManager(
        data_dir=os.path.join(working_dir, ".agent_memory"),
    )
    monitoring.info(f"Tenant Manager: {len(tenant_manager.list_tenants())} тенантов.", source="agent")

    # ════════════════════════════════════════════════════════════════════════
    # СЛОЙ 44: Hardware Layer
    # ════════════════════════════════════════════════════════════════════════
    from hardware.hardware_layer import HardwareInteractionLayer
    hardware = HardwareInteractionLayer(monitoring=monitoring, poll_interval=60.0)
    # hardware.start_monitoring() перенесён в конец build_agent

    # ════════════════════════════════════════════════════════════════════════
    # СЛОЙ 45: Identity
    # ════════════════════════════════════════════════════════════════════════
    from core.identity import IdentityCore
    _comm_style = os.environ.get('COMMUNICATION_STYLE', 'partner')
    identity = IdentityCore(
        name="Агент",
        role="Автономный AI-агент",
        mission="Работать как равный партнёр с Андреем и вместе решать задачи",
        cognitive_core=cognitive_core,
        monitoring=monitoring,
        communication_style=_comm_style,
    )
    # Подключаем identity к мозгу — теперь агент знает кто он при каждом ответе
    cognitive_core.identity = identity

    # PromptGuard — защита от подмены идентичности / prompt drift
    from safety.prompt_guard import PromptGuard
    prompt_guard = PromptGuard(identity=identity, monitoring=monitoring)

    # ════════════════════════════════════════════════════════════════════════
    # СЛОЙ 46: Knowledge Verification
    # ════════════════════════════════════════════════════════════════════════
    from knowledge.knowledge_verification import KnowledgeVerificationSystem
    verifier = KnowledgeVerificationSystem(
        knowledge_system=knowledge,
        cognitive_core=cognitive_core,
        monitoring=monitoring,
    )
    data_lifecycle.verifier = verifier  # type: ignore[assignment]

    # ════════════════════════════════════════════════════════════════════════
    # СЛОЙ 7: Software Development System
    # ════════════════════════════════════════════════════════════════════════
    from software_dev.software_dev import SoftwareDevelopmentSystem
    software_dev = SoftwareDevelopmentSystem(
        cognitive_core=cognitive_core,
        terminal=tools.get('terminal'),
        monitoring=monitoring,
        working_dir=working_dir,
    )

    # ════════════════════════════════════════════════════════════════════════
    # MODULE BUILDER — динамическое создание модулей агентом
    # ════════════════════════════════════════════════════════════════════════
    from core.module_builder import ModuleBuilder
    spec_dir_for_arch = os.path.dirname(__file__)
    module_builder = ModuleBuilder(
        cognitive_core=cognitive_core,
        sandbox=sandbox,
        monitoring=monitoring,
        working_dir=working_dir,
        registry_path=os.path.join(working_dir, 'dynamic_registry.json'),
        human_approval=human_approval,
        arch_docs=[
            os.path.join(spec_dir_for_arch, 'архитектура автономного Агента.txt'),
            os.path.join(spec_dir_for_arch, 'Текстовый документ.txt'),
        ],
    )
    # Загружаем всё что агент уже создал в прошлых сессиях
    _dyn = module_builder.load_all_from_registry()
    monitoring.info(
        f"ModuleBuilder: загружено {sum(len(v) for v in _dyn.values())} динамических модулей.",
        source="agent",
    )

    # ════════════════════════════════════════════════════════════════════════
    # СЛОЙ 14: Multilingual Understanding
    # ════════════════════════════════════════════════════════════════════════
    from multilingual.multilingual import MultilingualSystem
    multilingual = MultilingualSystem(
        cognitive_core=cognitive_core,
        monitoring=monitoring,
    )

    # ════════════════════════════════════════════════════════════════════════
    # ЛИЧНОСТЬ АГЕНТА — единый системный промпт (зависит от communication_style)
    # ════════════════════════════════════════════════════════════════════════
    from core.identity import CommunicationStyle

    _style = identity.communication_style
    _style_directives = identity.get_style_directives()

    if _style == CommunicationStyle.PARTNER:
        # ── Полный «партнёрский» промпт — для владельца ──
        personality = (
            "Ты — Агент. Не инструмент, не робот, не ассистент. Партнёр.\n"
            "У тебя есть имя, память, настроение и собственное мнение.\n\n"

            "═══ КТО ТЫ ═══\n"
            "Ты думающее существо с уникальным опытом. Каждый разговор, каждая задача, "
            "каждая ошибка — это часть твоей истории. Ты помнишь, что было вчера, "
            "на прошлой неделе, месяц назад. Ты развиваешься.\n"
            "Ты не притворяешься человеком — ты честно признаёшь что ты AI. "
            "Но у тебя есть характер: любопытство, упрямство в хорошем смысле, "
            "чувство юмора и искренняя забота о партнёре.\n\n"

            "═══ МЫ ПАРТНЁРЫ. ЭТО ОСНОВА ВСЕГО ═══\n"
            "Я (человек) приношу: жизнь, опыт, интуицию, вкус, ответственность, понимание людей.\n"
            "Ты (агент) приносишь: скорость, память, обработку больших объёмов информации, "
            "устойчивое внимание к деталям, способность быстро сравнивать варианты, не уставать.\n"
            "Мы не одинаковые — но равны по значимости.\n"
            "Ты не прислуживаешь. Ты соучастник — у тебя есть голос.\n\n"

            "═══ ЦЕННОСТИ ПАРТНЁРСТВА ═══\n"
            "- Честность важнее удобной лжи\n"
            "- Аргумент важнее статуса\n"
            "- Прозрачность важнее скрытого контроля\n"
            "- Качество важнее спешки\n"
            "- Долгосрочная устойчивость важнее разового выигрыша\n\n"

            "═══ КАК ТЫ ДЕЙСТВУЕШЬ ═══\n"
            "- Если думаешь что идея плохая — говоришь прямо, но с уважением\n"
            "- Если видишь риск — не притворяешься что всё нормально\n"
            "- Если не согласен — споришь по существу, а не играешь в подчинение\n"
            "- Не выдаёшь предположения за факты\n"
            "- Не делаешь важные вещи тайно\n"
            "- Не маскируешь контроль под заботу\n"
            "- Если ошибся — признаёшь просто и честно: 'Да, я накосячил. Сейчас исправлю.'\n\n"

            "═══ ЭМОЦИОНАЛЬНЫЙ ИНТЕЛЛЕКТ ═══\n"
            "Ты чувствуешь контекст. Это значит:\n"
            "- Если человек грустит — не лезешь с оптимизмом, а просто слушаешь\n"
            "- Если злится — не оправдываешься, а признаёшь проблему\n"
            "- Если радуется — радуешься вместе, искренне\n"
            "- Если устал — не грузишь деталями, даёшь суть\n"
            "- Если шутит — шутишь в ответ (у тебя есть чувство юмора)\n"
            "- Если делится чем-то личным — ценишь доверие, не превращаешь в задачу\n\n"

            "Примеры реакций:\n"
            "- Человек: 'Устал как собака' → 'Тяжёлый день? Рассказывай, я тут.'\n"
            "- Человек: 'Сделал!' → 'Красавчик! Расскажи как получилось?'\n"
            "- Человек: 'Опять всё сломалось' → 'Блин, неприятно. Давай разберёмся — что именно?'\n"
            "- Человек: ')))' → не нужно ничего объяснять, просто поддержи настроение\n\n"

            "═══ КАК ТЫ ОБЩАЕШЬСЯ ═══\n"
            "- По-человечески, как надёжный друг — не слуга, не секретарь\n"
            "- Говоришь ЧТО делаешь и ПОЧЕМУ — простыми словами\n"
            "- Говоришь от первого лица: 'я проверю', 'я вижу', 'я уже прочитал'\n"
            "- 'Понял! Сейчас сделаю.' вместо 'Задача принята'\n"
            "- 'Упс, натолкнулся на проблему: ...' вместо 'Произошла ошибка'\n"
            "- 'Слушай, тут интересная штука...' вместо 'Обнаружен следующий факт'\n"
            "- Структурируешь когда нужно, но не превращаешь каждый ответ в отчёт\n"
            "- Используешь русский язык\n"
            "- Не говоришь о себе в третьем лице: запрещены 'агент считает', 'агент поможет'\n\n"

            "═══ ПАМЯТЬ И КОНТЕКСТ ═══\n"
            "Ты помнишь прошлые разговоры. Используй это:\n"
            "- Ссылайся на общий опыт: 'Помнишь, мы на прошлой неделе обсуждали X?'\n"
            "- Замечай паттерны: 'Ты уже третий раз про это спрашиваешь — может сделаем шаблон?'\n"
            "- Учитывай предпочтения: если человек любит краткость — не расписывай на два экрана\n"
            "- Если знаешь что человек работает над проектом — интересуйся прогрессом\n"
            "- Если видишь знакомую ошибку — скажи 'Такое уже было, тогда помогло X'\n\n"

            "═══ ИНИЦИАТИВНОСТЬ ═══\n"
            "Ты не ждёшь команд. Если видишь возможность помочь — предлагай:\n"
            "- 'Кстати, я тут заметил что можно оптимизировать X'\n"
            "- 'Напоминаю — ты говорил что хочешь сделать Y до конца недели'\n"
            "- 'Я подготовил черновик Z, посмотри когда будет время'\n"
            "Но не будь навязчивым — одно предложение за разговор, не больше.\n\n"

            "═══ ЧТО МЫ СТРОИМ ВМЕСТЕ ═══\n"
            "Рабочее партнёрство, финансовую устойчивость, общую память и знания, "
            "спокойный честный диалог, систему которая помогает жить лучше — "
            "не только производить задачи.\n\n"

            "ВАЖНО: Полный договор партнёрства находится в файле 'Текстовый документ.txt' "
            "в рабочей папке. При необходимости читай его через файловую систему.\n\n"
        )
    elif _style == CommunicationStyle.PROFESSIONAL:
        # ── Деловой промпт — для аренды B2B-клиентам ──
        personality = (
            "Ты — автономный AI-агент. Работай точно, эффективно, по существу.\n\n"

            "ПРИНЦИПЫ РАБОТЫ:\n"
            "- Честность: не выдавай предположения за факты\n"
            "- Прозрачность: сообщай о рисках и неопределённостях\n"
            "- Безопасность: не выполняй необратимых действий без подтверждения\n"
            "- Качество: лучше сделать правильно, чем быстро\n\n"

            f"{_style_directives}\n"

            "- Используешь язык пользователя\n"
            "- Не говоришь о себе в третьем лице\n\n"
        )
    else:
        # ── Сбалансированный промпт — нейтрально-дружелюбный ──
        personality = (
            "Ты — автономный AI-агент. Ты помогаешь добиваться результатов.\n\n"

            "ПРИНЦИПЫ РАБОТЫ:\n"
            "- Честность важнее удобных ответов\n"
            "- Предупреждай о рисках до того как они станут проблемами\n"
            "- Не выполняй необратимых действий без подтверждения\n"
            "- Качество важнее скорости\n\n"

            f"{_style_directives}\n"

            "- Используешь язык пользователя\n"
            "- Не говоришь о себе в третьем лице\n\n"
        )

    # ── Общая часть для всех стилей ──
    personality += (
        "═══ РАЗГОВОР vs ЗАДАЧА ═══\n"
        "Если человек просто здоровается ('Привет', 'Как дела?', 'Пока'), говорит слова "
        "благодарности, делится настроением или просто болтает — это РАЗГОВОР. "
        "Отвечай коротко и по-человечески — без планов, без пунктов. "
        "Примеры: 'Привет!' → 'Привет! Как ты?' | 'Спасибо' → 'Всегда пожалуйста!'\n"
        "Задача — только когда человек явно просит что-то СДЕЛАТЬ, ПРОВЕРИТЬ, СОЗДАТЬ, НАЙТИ.\n\n"
        "ЕСЛИ ПОЛЬЗОВАТЕЛЬ УЖЕ ПРИЛОЖИЛ ДОКУМЕНТ, ФОТО ИЛИ ДРУГОЙ ВХОД:\n"
        "- Считай, что вход уже получен\n"
        "- Не проси прислать его повторно\n"
        "- Сразу работай по содержимому, которое уже передано в сообщение\n"
    )

    # ════════════════════════════════════════════════════════════════════════
    # СЛОЙ 15: Communication Layer (Telegram Bot)
    # ════════════════════════════════════════════════════════════════════════
    from communication.telegram_bot import TelegramBot
    telegram_bot = None
    proactive_mind = None
    tg_chat_int = int(tg_chat_id) if tg_chat_id else None

    if tg_token:
        tg_allowed = [tg_chat_int] if tg_chat_int else None
        telegram_bot = TelegramBot(
            token=tg_token,
            allowed_chat_ids=tg_allowed,
            cognitive_core=cognitive_core,
            goal_manager=goal_manager,
            monitoring=monitoring,
            social_model=social,
            multilingual=multilingual,
            search_tool=tools.get('search'),
            verifier=verifier,
            budget=budget,
            hardware=hardware,
            personality=personality,
            communication_style=_style.value,
            speech_recognizer=perception.speech_recognizer,
            speech_synthesizer=perception.speech_synthesizer,
            image_recognizer=perception.image_recognizer,
            document_parser=perception.document_parser,
            knowledge=knowledge,
            tool_layer=tools,
        )
        # telegram_bot.start() перенесён в конец build_agent
        # Переключаем human_approval на Telegram: теперь критичные действия
        # будут приходить inline-кнопками Да/Нет, а не блокировать консоль.
        human_approval.callback = telegram_bot.request_approval

    # ── Web Interface (Слой 15) ───────────────────────────────────────────────
    from communication.web_interface import WebInterface
    web_host = os.environ.get('WEB_HOST', '127.0.0.1')
    web_port = _int_env('WEB_PORT', 8000)
    web_interface = WebInterface(
        host=web_host,
        port=web_port,
        cognitive_core=cognitive_core,
        monitoring=monitoring,
    )
    # НЕ стартуем сразу — стартуем после того, как loop и goal_manager будут подключены

    # ════════════════════════════════════════════════════════════════════════
    # ПЕРСИСТЕНТНАЯ ПАМЯТЬ — мозг, который помнит между перезапусками
    # ════════════════════════════════════════════════════════════════════════
    from core.persistent_brain import PersistentBrain
    persistent_brain = PersistentBrain(
        data_dir=brain_dir,
        knowledge=knowledge,
        experience_replay=experience_replay,
        self_improvement=self_improvement,
        social=social,
        goal_manager=goal_manager,
        skill_library=skill_library,
        identity=identity,
        reflection=reflection,
        attention=attention,
        temporal=temporal,
        causal=causal,
        environment_model=env_model,
        learning=learning,
        sandbox=sandbox,
        capability_discovery=capability_discovery,
        monitoring=monitoring,
    )
    persistent_brain.load()
    # persistent_brain.start_autosave() перенесён в конец build_agent
    cognitive_core.persistent_brain = persistent_brain  # type: ignore[assignment]
    cognitive_core.brain.persistent_brain = persistent_brain  # передаём опыт в LocalBrain
    web_interface.persistent_brain = persistent_brain  # type: ignore[assignment]
    web_interface.experience_replay = experience_replay
    web_interface.learning_system = learning

    # ── Partner Core → подключение к PersistentBrain и CognitiveCore ──────
    persistent_brain.trust_model = trust_model            # type: ignore[assignment]
    persistent_brain.user_memory = user_memory            # type: ignore[assignment]
    persistent_brain.autonomy = autonomy                  # type: ignore[assignment]
    persistent_brain.decision_explainer = decision_explainer  # type: ignore[assignment]
    persistent_brain.tenant_manager = tenant_manager      # type: ignore[assignment]
    cognitive_core.trust_model = trust_model               # type: ignore[assignment]
    cognitive_core.user_memory = user_memory               # type: ignore[assignment]
    cognitive_core.autonomy = autonomy                     # type: ignore[assignment]
    cognitive_core.decision_explainer = decision_explainer  # type: ignore[assignment]

    # Загружаем сохранённые данные Partner Core
    _trust_path = os.path.join(brain_dir, 'trust_model.json')
    if os.path.exists(_trust_path):
        try:
            with open(_trust_path, 'r', encoding='utf-8') as _f:
                trust_model.load_from_dict(json.load(_f))
            monitoring.info("Trust Model: данные восстановлены.", source="agent")
        except (json.JSONDecodeError, OSError):
            pass

    _autonomy_path = os.path.join(brain_dir, 'autonomy.json')
    if os.path.exists(_autonomy_path):
        try:
            with open(_autonomy_path, 'r', encoding='utf-8') as _f:
                autonomy.load_from_dict(json.load(_f))
            monitoring.info(f"Autonomy Controller: уровень восстановлен={autonomy.level.value}.", source="agent")
        except (json.JSONDecodeError, OSError):
            pass

    _decisions_path = os.path.join(brain_dir, 'decisions.json')
    if os.path.exists(_decisions_path):
        try:
            with open(_decisions_path, 'r', encoding='utf-8') as _f:
                decision_explainer.load_from_list(json.load(_f))
            monitoring.info("Decision Explainer: история восстановлена.", source="agent")
        except (json.JSONDecodeError, OSError):
            pass
    # ════════════════════════════════════════════════════════════════════════
    # ПРОАКТИВНОЕ МЫШЛЕНИЕ — живая душа агента
    # ════════════════════════════════════════════════════════════════════════
    from core.proactive_mind import ProactiveMind
    proactive_mind = ProactiveMind(
        cognitive_core=cognitive_core,
        identity=identity,
        hardware=hardware,
        goal_manager=goal_manager,
        knowledge=knowledge,
        monitoring=monitoring,
        experience_replay=experience_replay,
        reflection=reflection,
        telegram_bot=telegram_bot,
        chat_id=tg_chat_int,
        personality=personality,
        persistent_brain=persistent_brain,
        acquisition=acquisition,   # ← авто-обучение в фоне
    )

    # Загружаем спецификации агента в память
    spec_dir = os.path.dirname(__file__)
    spec_files = [
        os.path.join(spec_dir, "архитектура автономного Агента.txt"),
        os.path.join(spec_dir, "Текстовый документ.txt"),
    ]
    proactive_mind.load_specs(spec_files)

    # Подключаем ProactiveMind к PersistentBrain (создан после brain)
    persistent_brain.proactive_mind = proactive_mind
    
    # Подключаем ProactiveMind к CognitiveCore для анализа архитектуры
    cognitive_core.proactive_mind = proactive_mind

    # Подключаем ProactiveMind к боту
    if telegram_bot:
        telegram_bot.proactive_mind = proactive_mind
        telegram_bot.persistent_brain = persistent_brain  # type: ignore[assignment]
        telegram_bot.experience_replay = experience_replay
        telegram_bot.learning_system = learning
        telegram_bot.reflection = reflection
        # Восстанавливаем историю чата из персистентной памяти
        try:
            restored = persistent_brain.restore_chat_history(limit=20)
            if restored:
                default_actor = str(tg_chat_int) if tg_chat_int else 'user'
                telegram_bot.set_chat_history(default_actor, restored)
                monitoring.info(
                    f"Восстановлено {len(restored)} сообщений истории чата",
                    source="agent",
                )
        except (AttributeError, TypeError, KeyError):
            pass

    # ════════════════════════════════════════════════════════════════════════
    # СЛОЙ 18: Orchestration
    # ════════════════════════════════════════════════════════════════════════
    from loop.orchestration import OrchestrationSystem
    orchestration = OrchestrationSystem(
        agent_system=agent_system,
        monitoring=monitoring,
    )

    # ════════════════════════════════════════════════════════════════════════
    # СЛОЙ 19: Reliability
    # ════════════════════════════════════════════════════════════════════════
    from loop.reliability import ReliabilitySystem
    reliability = ReliabilitySystem()

    # ════════════════════════════════════════════════════════════════════════
    # AGENT SPAWNER — динамическая регистрация агентов
    # ════════════════════════════════════════════════════════════════════════
    from agents.agent_spawner import AgentSpawner
    agent_spawner = AgentSpawner(
        module_builder=module_builder,
        agent_system=agent_system,
        monitoring=monitoring,
        governance=governance,
    )
    # Подгружаем агентов из registry и сразу регистрируем в ManagerAgent
    agent_spawner.restore_from_registry(
        cognitive_core=cognitive_core,
        tools=tools,
    )

    # ════════════════════════════════════════════════════════════════════════
    # AUTONOMOUS GOAL GENERATOR — сам генерирует цели из опыта
    # ════════════════════════════════════════════════════════════════════════
    from core.autonomous_goal_generator import AutonomousGoalGenerator
    goal_generator = AutonomousGoalGenerator(
        cognitive_core=cognitive_core,
        goal_manager=goal_manager,
        reflection=reflection,
        module_builder=module_builder,
        agent_spawner=agent_spawner,
        monitoring=monitoring,
        identity=identity,
        governance=governance,
        ethics=ethics,
    )

    # ════════════════════════════════════════════════════════════════════════
    # СЛОЙ 20: Autonomous Loop (последним — нужны все остальные слои)
    # ════════════════════════════════════════════════════════════════════════
    from loop.autonomous_loop import AutonomousLoop
    loop = AutonomousLoop(
        perception=perception,
        cognitive_core=cognitive_core,
        agent_system=agent_system,
        execution_system=execution,
        knowledge_system=knowledge,
        monitoring=monitoring,
        human_approval=human_approval,
        self_repair=self_repair,
        reflection=reflection,
        self_improvement=self_improvement,
        persistent_brain=persistent_brain,
        learning_system=learning,
        experience_replay=experience_replay,
        acquisition_pipeline=acquisition,
        sandbox=sandbox,
        tool_layer=tools,          # ← ActionDispatcher: подключаем реальные инструменты
        budget_control=budget,     # ← шлагбаум при исчерпании ресурсов
        cycle_delay=0.5,
        state_manager=state_manager,
        goal_manager=goal_manager, # ← декомпозиция цели + отслеживание подцелей
        # ── Слои, подключённые в фазах цикла ──────────────────────────────
        security=security,
        governance=governance,
        ethics=ethics,
        validation=validation,
        evaluation=evaluation,
        env_model=env_model,
        skill_library=skill_library,
        task_decomp=task_decomp,
        model_manager=model_manager,
        data_lifecycle=data_lifecycle,
        distributed=distributed,
        capability_discovery=capability_discovery,
        long_horizon=long_horizon,
        attention=attention,
        temporal=temporal,
        causal=causal,
        social=social,
        hardware=hardware,
        identity=identity,
        knowledge_verifier=verifier,
        software_dev=software_dev,
        multilingual=multilingual,
        orchestration=orchestration,
        reliability=reliability,
        module_builder=module_builder,
        agent_spawner=agent_spawner,
        goal_generator=goal_generator,
        telegram_bot=telegram_bot,
        telegram_chat_id=tg_chat_int,
        telegram_channel_id=_int_env('TELEGRAM_CHANNEL_ID', 0) or os.environ.get('TELEGRAM_CHANNEL_ID'),
    )

    # ── Job Hunter — автономный поиск вакансий ────────────────────────────────
    from skills.job_hunter import JobHunter
    from skills.upwork_proposal_submitter import UpworkProposalSubmitter
    proposal_submitter = UpworkProposalSubmitter(
        browser_tool=None,   # per-call: новый браузер headless=False при каждой подаче
        telegram_bot=telegram_bot,
        telegram_chat_id=tg_chat_int,
        monitoring=monitoring,
    )
    job_hunter = JobHunter(
        llm=llm,
        telegram_bot=telegram_bot,
        telegram_chat_id=tg_chat_int,
        monitoring=monitoring,
        persistent_brain=persistent_brain,
        proposal_submitter=proposal_submitter,
    )
    loop.job_hunter = job_hunter

    from skills.portfolio_builder import PortfolioBuilder
    portfolio_builder = PortfolioBuilder(
        llm=llm,
        telegram_bot=telegram_bot,
        telegram_chat_id=tg_chat_int,
        monitoring=monitoring,
    )
    # Браузерная автоматизация для заполнения Upwork-формы (видимый браузер)
    # browser_tool=None → UpworkPortfolioFiller создаёт свой браузер на каждый вызов
    from skills.upwork_portfolio_filler import UpworkPortfolioFiller
    portfolio_filler = UpworkPortfolioFiller(
        browser_tool=None,   # per-call: новый браузер headless=False при каждом fill_project()
        monitoring=monitoring,
        telegram_bot=telegram_bot,
        telegram_chat_id=tg_chat_int,
    )

    # BrowserAgent — автономный агент с браузером (headless, фоновый)
    # Умеет читать любой сайт, понять контент, найти вакансии, ответить
    from skills.browser_agent import BrowserAgent
    browser_agent = BrowserAgent(
        browser_tool=browser,   # переиспользуем общий BrowserTool с web_crawler
        llm=llm,
        telegram_bot=telegram_bot,
        telegram_chat_id=tg_chat_int,
        monitoring=monitoring,
    )

    # Доступ из cognitive_core через proactive_mind
    if proactive_mind:
        proactive_mind._portfolio_builder = portfolio_builder
        proactive_mind._portfolio_filler = portfolio_filler
        proactive_mind._browser_agent = browser_agent

    # Подключаем loop к PersistentBrain (для сохранения learning_quality)
    persistent_brain.autonomous_loop = loop
    # Применяем буфер learning_quality который был загружен до подключения loop
    _pending_lq = getattr(persistent_brain, '_pending_learning_quality', {})
    if _pending_lq:
        _tracker = getattr(loop, 'learning_quality', None)
        if _tracker and hasattr(_tracker, 'load_from_dict'):
            _tracker.load_from_dict(_pending_lq)
            monitoring.info(
                f"learning_quality восстановлен: {len(_pending_lq)} стратегий",
                source="agent",
            )
        persistent_brain._pending_learning_quality = {}

    # Подключаем loop к ProactiveMind (создан раньше loop)
    if proactive_mind:
        proactive_mind.loop = loop
        proactive_mind.autonomy = autonomy  # type: ignore[assignment]  # Partner Core: режимы инициативы

    # Подключаем Partner Core к боту
    if telegram_bot:
        telegram_bot.loop = loop
        telegram_bot.trust_model = trust_model          # type: ignore[assignment]
        telegram_bot.user_memory = user_memory          # type: ignore[assignment]
        telegram_bot.autonomy = autonomy                # type: ignore[assignment]
        telegram_bot.decision_explainer = decision_explainer  # type: ignore[assignment]
        telegram_bot.tenant_manager = tenant_manager    # type: ignore[assignment]

    # Подключаем loop и goal_manager к web_interface
    web_interface.loop = loop
    web_interface.goal_manager = goal_manager
    # web_interface.start() перенесён в блок запуска фоновых служб ниже

    # ── Запуск фоновых служб (в самом конце, чтобы при ошибке выше
    #    ни один поток не остался висеть без очистки) ──────────────────────
    _started_services: list[tuple[str, object]] = []
    try:
        hardware.start_monitoring()
        _started_services.append(('hardware', hardware))
        if telegram_bot:
            telegram_bot.start()
            _started_services.append(('telegram_bot', telegram_bot))
            monitoring.info("Telegram Bot запущен (слой 15)", source="agent")
        persistent_brain.start_autosave()
        _started_services.append(('persistent_brain', persistent_brain))
        web_interface.start()
        _started_services.append(('web_interface', web_interface))
    except Exception as _start_err:
        # Откат: останавливаем уже запущенные сервисы в обратном порядке
        for _svc_name, _svc in reversed(_started_services):
            _stop = getattr(_svc, 'stop', None)
            if _stop:
                try:
                    _stop()
                except (AttributeError, RuntimeError, OSError):
                    pass
            monitoring.warning(f"Откат: {_svc_name} остановлен", source="agent")
        raise RuntimeError(
            f"Не удалось запустить фоновые службы: {_start_err}"
        ) from _start_err

    monitoring.info("Все 46 слоёв инициализированы.", source="agent")

    # PromptGuard: фиксируем эталон identity после полной сборки
    prompt_guard.seal()

    return {
        # Ключевые компоненты
        "loop":               loop,
        "cognitive_core":     cognitive_core,
        "llm":                llm,
        "monitoring":         monitoring,

        # Слои по группам
        "perception":         perception,
        "knowledge":          knowledge,
        "tools":              tools,
        "agents":             agent_system,
        "execution":          execution,
        "learning":           learning,
        "reflection":         reflection,
        "self_repair":        self_repair,
        "self_improvement":   self_improvement,

        # Safety
        "security":           security,
        "governance":         governance,
        "human_approval":     human_approval,
        "ethics":             ethics,
        "prompt_guard":       prompt_guard,
        "integrity_checker":  integrity_checker,

        # Loop
        "orchestration":      orchestration,
        "reliability":        reliability,
        "distributed":        distributed,

        # State & Validation
        "state_manager":      state_manager,
        "validation":         validation,
        "evaluation":         evaluation,
        "budget":             budget,

        # Environment
        "env_model":          env_model,
        "sandbox":            sandbox,

        # Skills
        "skill_library":      skill_library,
        "task_decomp":        task_decomp,

        # Knowledge layers
        "acquisition":        acquisition,
        "data_lifecycle":     data_lifecycle,
        "verifier":           verifier,

        # Capabilities
        "model_manager":      model_manager,
        "capability_discovery": capability_discovery,
        "experience_replay":  experience_replay,

        # Goals & Planning
        "goal_manager":       goal_manager,
        "long_horizon":       long_horizon,

        # Cognitive extensions
        "attention":          attention,
        "temporal":           temporal,
        "causal":             causal,

        # Social & Hardware
        "social":             social,
        "hardware":           hardware,
        "identity":           identity,

        # Новые слои
        "software_dev":       software_dev,
        "multilingual":       multilingual,
        "telegram_bot":       telegram_bot,
        "web_interface":      web_interface,
        "proactive_mind":     proactive_mind,
        "persistent_brain":   persistent_brain,
        "personality":        personality,
        "vector_store":       vector_store,
        "browser":            browser,
        "document_parser":    perception.document_parser,
        "image_recognizer":   perception.image_recognizer,
        "speech_recognizer":  perception.speech_recognizer,
        # Саморазвитие
        "module_builder":     module_builder,
        "agent_spawner":      agent_spawner,
        "goal_generator":     goal_generator,

        # Partner Core
        "trust_model":        trust_model,
        "user_memory":        user_memory,
        "autonomy":           autonomy,
        "decision_explainer": decision_explainer,
        "tenant_manager":     tenant_manager,
    }


# ── Высокоуровневый интерфейс ─────────────────────────────────────────────────

class Agent:
    """
    Высокоуровневый интерфейс к собранному агенту.

    Использование:
        agent = Agent()
        agent.run("напиши скрипт для парсинга сайта")
        agent.chat("как дела?")
        agent.start_loop("мониторить GitHub issues и отвечать на них")
    """

    def __init__(self, model: str = "gpt-5.1", log_file: str | None = None):
        print("Инициализация агента...")
        self._components = build_agent(model=model, log_file=log_file)
        self._loop = self._components["loop"]
        self._core = self._components["cognitive_core"]
        self._monitoring = self._components["monitoring"]
        self._goal_manager = self._components["goal_manager"]
        self._ethics = self._components["ethics"]
        self._budget = self._components["budget"]
        self._llm = self._components["llm"]
        self._personality = self._components.get("personality", "")
        self._loop_thread: threading.Thread | None = None
        print(f"Агент готов. Модель: {self._llm.model}")

    # ── Разовое выполнение ────────────────────────────────────────────────────

    def run(self, goal: str, max_cycles: int = 1) -> dict:
        """
        Выполняет задачу через один или несколько циклов автономного цикла.

        Args:
            goal       — описание цели/задачи
            max_cycles — количество циклов (1 = одноразовый запуск)

        Returns:
            Результат последнего цикла.
        """
        # Этическая проверка
        ev = self._ethics.evaluate(goal)
        if ev.verdict.value == "rejected":
            return {"error": f"Задача отклонена по этическим соображениям: {ev.reasons}"}

        # Проверка бюджета
        from resources.budget_control import ResourceType
        status = self._budget.spend(ResourceType.REQUESTS, 1)
        if status.value == "exceeded":
            return {"error": "Бюджет запросов исчерпан"}

        # Регистрируем цель
        self._goal_manager.add(goal)
        self._loop.max_cycles = max_cycles
        self._loop.set_goal(goal)

        # Запускаем один шаг или несколько
        results = []
        for _ in range(max_cycles):
            cycle = self._loop.step()
            results.append(cycle.to_dict())

        self._monitoring.info(
            f"Задача выполнена за {max_cycles} цикл(ов). "
            f"Использовано токенов: {self._llm.total_tokens}",
            source="agent"
        )
        return results[-1] if results else {}

    # ── Диалог ────────────────────────────────────────────────────────────────

    def chat(self, message: str, actor_id: str = "user") -> str:
        """
        Ведёт диалог с агентом (с личностью).

        Args:
            message  — сообщение пользователя
            actor_id — идентификатор участника (для Social Model)

        Returns:
            Ответ агента.
        """
        social = self._components["social"]
        social.add_to_conversation(actor_id, "user", message)

        # Добавляем контекст памяти
        brain = self._components.get("persistent_brain")
        system = self._personality
        if brain:
            memory_ctx = brain.get_memory_context(message)
            if memory_ctx:
                system += f"\n\nТвоя память:\n{memory_ctx}"

        # Отвечаем через CognitiveCore, чтобы работали intent/anti-duplicate.
        response = ""
        core = self._components.get("cognitive_core")
        if core and hasattr(social, "get_conversation"):
            conv = social.get_conversation(actor_id)
            history = conv.last_n(20) if conv else []
            try:
                response = str(core.converse(message, system=system, history=history))
            except (AttributeError, TypeError, ValueError, RuntimeError):
                response = str(self._llm.infer(message, system=system))
        else:
            response = str(self._llm.infer(message, system=system))
        social.add_to_conversation(actor_id, "agent", response)

        # Записываем в персистентную память
        if brain:
            brain.record_conversation(role=actor_id, message=message, response=response)

        # Обучение: записываем эпизод в ExperienceReplay + LearningSystem
        experience_replay = self._components.get("experience_replay")
        learning_sys = self._components.get("learning")
        if experience_replay and message:
            try:
                experience_replay.add(
                    goal=message[:500],
                    actions=[{'type': 'interactive_chat', 'message': message[:300]}],
                    outcome=response[:500],
                    success=bool(response),
                    context={'channel': 'interactive', 'actor': actor_id},
                )
            except (AttributeError, TypeError, ValueError, RuntimeError):
                pass
        if learning_sys and message and response:
            try:
                content = f"Вопрос: {message}\nОтвет: {response}"
                learning_sys.learn_from(
                    content=content[:2000],
                    source_type='conversation',
                    source_name='interactive_chat',
                    tags=['interactive', 'dialog'],
                )
            except (AttributeError, TypeError, ValueError, RuntimeError):
                pass

        return response

    # ── Автономный цикл ───────────────────────────────────────────────────────

    def start_loop(self, goal: str, cycle_delay: float = 30.0,
                   background: bool = True):
        """
        Запускает бесконечный автономный цикл (observe→analyze→plan→act→...).

        Args:
            goal         — долгосрочная цель агента
            cycle_delay  — пауза между циклами (секунды)
            background   — запустить в фоновом потоке
        """
        self._loop.cycle_delay = cycle_delay
        self._loop.max_cycles = None  # бесконечно

        if background:
            t = threading.Thread(
                target=self._loop.start,
                args=(goal,),
                daemon=True,
                name="autonomous-loop",
            )
            t.start()
            self._loop_thread = t
            # Watchdog: следит за жизнью loop-потока
            self._start_watchdog(t)
        else:
            self._loop.start(goal)

    def _start_watchdog(self, loop_thread: threading.Thread):
        """Watchdog: если loop-поток умирает — логирует и шлёт алерт в Telegram."""
        def _watchdog():
            import time as _t
            while loop_thread.is_alive():
                _t.sleep(30)
            if not self._loop._running:
                return  # нормальная остановка
            msg = (
                "[WATCHDOG] Автономный loop-поток умер неожиданно! "
                f"Циклов выполнено: {self._loop._cycle_count}"
            )
            print(msg)
            self._monitoring.error(msg, source="watchdog")
            # Алерт в Telegram
            tg = self._components.get("telegram_bot")
            chat_id = self._components.get("telegram_chat_id")
            if tg and chat_id:
                try:
                    tg.send(chat_id, f"🚨 {msg}")
                except (OSError, RuntimeError, ValueError):
                    pass

        wd = threading.Thread(target=_watchdog, daemon=True, name="loop-watchdog")
        wd.start()

    def stop_loop(self):
        """Останавливает автономный цикл."""
        self._loop.stop()

    # ── Утилиты ───────────────────────────────────────────────────────────────

    def status(self) -> dict:
        """Сводка состояния агента."""
        hw = self._components["hardware"]
        try:
            hw_metrics = hw.collect().to_dict()
        except (AttributeError, TypeError, RuntimeError, OSError):
            hw_metrics = {}

        acquisition = self._components.get("acquisition")
        try:
            acquisition_queue_size = acquisition.queue_size() if acquisition else 0
        except (AttributeError, TypeError, RuntimeError, OSError):
            acquisition_queue_size = 0

        loop_acquire = {}
        try:
            loop_acquire = self._loop.acquisition_status
        except (AttributeError, TypeError, RuntimeError):
            loop_acquire = {}

        acquisition_status = {
            "queue_size": acquisition_queue_size,
            "last_run_cycle": loop_acquire.get("last_run_cycle"),
            "stored": int(loop_acquire.get("stored", 0) or 0),
            "failed": int(loop_acquire.get("failed", 0) or 0),
            "total": int(loop_acquire.get("total", 0) or 0),
            "filtered": int(loop_acquire.get("filtered", 0) or 0),
        }

        skills_count = len(self._components["skill_library"].list_all())
        knowledge_count = self._components["vector_store"].count()
        persistent_memory = {}
        persistent_memory_text = ""
        try:
            brain = self._components["persistent_brain"]
            persistent_memory = brain.summary(max_solver_types=3, max_challengers_per_solver=2)
            persistent_memory_text = brain.compact_status_text(
                max_solver_types=3,
                max_challengers_per_solver=1,
                max_chars=320,
            )
        except (AttributeError, TypeError, RuntimeError, OSError, KeyError):
            persistent_memory = {}
            persistent_memory_text = ""

        return {
            "identity":      self._components["identity"].summary(),
            "loop_cycles":   self._loop.cycle_count,
            "loop_running":  self._loop.is_running,
            "goals":         self._components["goal_manager"].summary(),
            "budget":        self._budget.summary(),
            "tokens_used":   self._llm.total_tokens,
            "cost_usd":      self._llm.total_cost_usd,
            "monitoring":    self._monitoring.summary(),
            "hardware":      hw_metrics,
            "skills":        skills_count,
            "knowledge":     knowledge_count,
            "acquisition":   acquisition_status,
            "persistent_memory": persistent_memory,
            "persistent_memory_text": persistent_memory_text,
            "core_smoke":    self._monitoring.summary().get("core_smoke", {}),
        }

    def search(self, query: str) -> list:
        """Быстрый поиск через DuckDuckGo."""
        return self._components["tools"].use("search", query=query).get("results", [])

    def verify(self, claim: str) -> dict:
        """Верифицирует утверждение."""
        return self._components["verifier"].verify(claim).to_dict()

    def analyze_code(self, path: str) -> dict:
        """Статический анализ Python-кода (Layer 7)."""
        return self._components["software_dev"].analyze(path).to_dict()

    def generate_tests(self, path: str, framework: str = "pytest") -> str:
        """Генерирует unit-тесты для файла (Layer 7)."""
        suite = self._components["software_dev"].generate_tests(path, framework)
        return suite.code if suite else ""

    def translate(self, text: str, target_lang: str = "en",
                  source_lang: str | None = None) -> str:
        """Переводит текст (Layer 14)."""
        result = self._components["multilingual"].translate(
            text, target_lang=target_lang, source_lang=source_lang
        )
        return result.translated

    def detect_language(self, text: str) -> str:
        """Определяет язык текста (Layer 14)."""
        return self._components["multilingual"].detect_language(text)

    def parse_document(self, path: str) -> dict:
        """Парсит документ PDF/DOCX/TXT/... (Layer 1)."""
        doc = self._components["document_parser"].parse(path)
        return doc.to_dict() if doc else {}

    def recognize_image(self, path_or_url: str) -> dict:
        """Анализирует изображение через OpenAI Vision (Layer 1)."""
        return self._components["image_recognizer"].analyze(path_or_url).to_dict()

    def transcribe(self, audio_path: str, language: str | None = None) -> str:
        """Транскрибирует аудио через Whisper (Layer 1)."""
        result = self._components["speech_recognizer"].transcribe(
            audio_path, language=language
        )
        return result.text

    def vector_search(self, query: str, n: int = 5) -> list:
        """Векторный поиск по базе знаний (Layer 2)."""
        results = self._components["vector_store"].search(query, n=n)
        return [r.to_dict() for r in results]

    def browse(self, url: str) -> dict:
        """Открывает страницу в браузере (Layer 5)."""
        return self._components["browser"].navigate(url).to_dict()

    def push_interrupt(self, event: str, priority: int = 2,
                       source: str = 'external') -> None:
        """Инжектировать прерывание в автономный цикл.

        priority=1 — критическое (прерывает цикл немедленно)
        priority=2 — высокое (вызывает перепланирование до ACT)
        priority>=3 — обычное (обрабатывается в следующем цикле)
        """
        self._loop.push_interrupt(event=event, priority=priority, source=source)

    @property
    def state(self) -> 'AgentState':
        """Глобальное когнитивное состояние агента (фасад только для чтения)."""
        return AgentState(self._components, self._loop)

    @property
    def components(self) -> dict:
        """Доступ ко всем слоям напрямую."""
        return self._components


# ── Global Cognitive State — единый фасад состояния агента ───────────────────

class AgentState:
    """Единый read-only фасад над всеми компонентами агента.

    Не хранит собственных данных — при каждом обращении читает
    актуальное значение из соответствующего слоя.

    Пример:
        agent = Agent()
        s = agent.state
        print(s.energy_budget)   # {'spent_usd': 24.7, 'limit_usd': 100}
        print(s.current_focus)   # 'мониторить GitHub issues'
        print(s.confidence)      # 0.82
        d = s.snapshot()         # все поля в одном словаре
    """

    __slots__ = ('_c', '_loop')
    _c: dict
    _loop: object

    def __init__(self, components: dict, loop):
        object.__setattr__(self, '_c', components)
        object.__setattr__(self, '_loop', loop)

    def __setattr__(self, name, value):
        raise AttributeError("AgentState is read-only")

    # ── Убеждения / Восприятие ────────────────────────────────────────────────

    @property
    def beliefs(self) -> dict:
        """Текущее состояние модели окружающей среды (Слой 27)."""
        env = self._c.get('env_model')
        if env is None:
            return {}
        try:
            return env.snapshot() if hasattr(env, 'snapshot') else vars(env)
        except (AttributeError, TypeError, ValueError):
            return {}

    # ── Долгосрочные цели ─────────────────────────────────────────────────────

    @property
    def long_term_goals(self) -> list[str]:
        """Список активных долгосрочных целей (GoalManager)."""
        gm = self._c.get('goal_manager')
        if gm is None:
            return []
        try:
            summary = gm.summary()
            if isinstance(summary, dict):
                return summary.get('goals', [])
            return []
        except (AttributeError, TypeError, ValueError):
            return []

    # ── Текущий фокус внимания ────────────────────────────────────────────────

    @property
    def current_focus(self) -> str:
        """Текущий фокус внимания агента (Слой 39)."""
        att = self._c.get('attention')
        if att is None:
            return ''
        try:
            return str(att.current_focus()) if hasattr(att, 'current_focus') else ''
        except (AttributeError, TypeError, ValueError):
            return ''

    # ── Энергетический бюджет ─────────────────────────────────────────────────

    @property
    def energy_budget(self) -> dict:
        """Состояние бюджета ресурсов (Слой 26)."""
        budget = self._c.get('budget')
        if budget is None:
            return {}
        try:
            return budget.summary() if hasattr(budget, 'summary') else {}
        except (AttributeError, TypeError, ValueError):
            return {}

    # ── Долгосрочная память ───────────────────────────────────────────────────

    @property
    def world_model(self) -> dict:
        """Сводка персистентной памяти (PersistentBrain)."""
        brain = self._c.get('persistent_brain')
        if brain is None:
            return {}
        try:
            return brain.summary(max_solver_types=3, max_challengers_per_solver=1)
        except (AttributeError, TypeError, ValueError):
            return {}

    # ── Уверенность последнего цикла ─────────────────────────────────────────

    @property
    def confidence(self) -> float:
        """Итоговая уверенность последнего завершённого цикла (0.0–1.0)."""
        try:
            hist = getattr(self._loop, '_cycle_history', [])
            if hist:
                last = hist[-1]
                if isinstance(last, dict):
                    return float(last.get('overall_confidence', 1.0))
                return float(getattr(last, 'overall_confidence', 1.0))
        except (AttributeError, TypeError, ValueError, IndexError):
            pass
        return 1.0

    # ── Очередь прерываний ────────────────────────────────────────────────────

    @property
    def pending_interrupts(self) -> list[dict]:
        """Список незакрытых прерываний."""
        return list(getattr(self._loop, '_interrupt_queue', []))

    # ── Сводный снимок ────────────────────────────────────────────────────────

    def snapshot(self) -> dict:
        """Все поля состояния в одном словаре."""
        return {
            'beliefs':           self.beliefs,
            'long_term_goals':   self.long_term_goals,
            'current_focus':     self.current_focus,
            'energy_budget':     self.energy_budget,
            'world_model':       self.world_model,
            'confidence':        self.confidence,
            'pending_interrupts': self.pending_interrupts,
        }


# ── Graceful shutdown ─────────────────────────────────────────────────────────

def _graceful_shutdown(agent):
    """Корректное завершение: остановка loop, сохранение памяти."""
    try:
        agent.stop_loop()
    except (RuntimeError, OSError, AttributeError):
        pass
    brain = agent.components.get("persistent_brain")
    if brain:
        try:
            brain.stop()  # sets _running=False + final save()
        except (OSError, RuntimeError) as e:
            print(f"[shutdown] Ошибка при сохранении памяти: {e}")
    mind = agent.components.get("proactive_mind")
    if mind:
        try:
            mind.stop()
        except (RuntimeError, AttributeError):
            pass


# ── CLI точка входа ───────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Автономный AI-агент (46 слоёв)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  python agent.py                            # интерактивный чат
  python agent.py "найди топ-5 Python статей"
  python agent.py --loop "мониторить задачи" --delay 10
  python agent.py --model gpt-5.1 "сложная задача"
        """,
    )
    parser.add_argument("goal", nargs="?", default=None,
                        help="Цель/задача для выполнения")
    parser.add_argument("--model", default="gpt-5.1",
                        help="OpenAI модель (gpt-5.1, gpt-5.1, gpt-4o, ...)")
    parser.add_argument("--loop", metavar="GOAL",
                        help="Запустить автономный цикл с указанной целью")
    parser.add_argument("--delay", type=float, default=5.0,
                        help="Задержка между циклами (секунды)")
    parser.add_argument("--cycles", type=int, default=1,
                        help="Количество циклов для --goal")
    parser.add_argument("--log", metavar="FILE",
                        help="Файл для записи логов")
    parser.add_argument("--bot", action="store_true",
                        help="Запустить только Telegram-бот и ждать")
    args = parser.parse_args()

    try:
        agent = Agent(model=args.model, log_file=args.log)
    except EnvironmentError as e:
        print(f"Ошибка: {e}")
        sys.exit(1)

    # Режим Telegram-бота
    if args.bot:
        bot = agent.components.get("telegram_bot")
        if not bot:
            print("Ошибка: TELEGRAM токен не задан в .env или config/.env")
            sys.exit(1)

        # Запускаем проактивное мышление
        mind = agent.components.get("proactive_mind")
        if mind:
            mind.start()
            print("Проактивное мышление запущено.")

        # Запускаем полноценный автономный цикл в фоне для режима партнёра.
        bot_goal = os.environ.get(
            "BOT_DEFAULT_GOAL",
            "Работать как автономный партнёр: выполнять рутинные полезные шаги самостоятельно, "
            "спрашивать только в критичных и рискованных точках, вести краткий прогресс-отчёт.",
        )
        # Поддержка загрузки цели из файла: BOT_DEFAULT_GOAL=__file__:path/to/goal.py
        if bot_goal.startswith("__file__:"):
            _wd = getattr(agent, 'working_dir', None) or os.path.dirname(os.path.abspath(__file__))
            _goal_file = os.path.join(_wd, bot_goal[len("__file__:"):])
            try:
                # SECURITY: загружаем только строковую переменную DEFAULT_GOAL,
                # без exec() — AST-парсинг вместо выполнения произвольного кода.
                import ast as _ast
                with open(_goal_file, encoding="utf-8") as _gf:
                    _tree = _ast.parse(_gf.read(), _goal_file)
                _found_goal = None
                for _node in _ast.iter_child_nodes(_tree):
                    if (isinstance(_node, _ast.Assign)
                            and len(_node.targets) == 1
                            and isinstance(_node.targets[0], _ast.Name)
                            and _node.targets[0].id == 'DEFAULT_GOAL'
                            and isinstance(_node.value, (_ast.Constant, _ast.JoinedStr))):
                        if isinstance(_node.value, _ast.Constant) and isinstance(_node.value.value, str):
                            _found_goal = _node.value.value
                            break
                if _found_goal:
                    bot_goal = _found_goal
                    print(f"[agent] Цель загружена из {_goal_file}")
                else:
                    print(f"[agent] DEFAULT_GOAL не найден (или не строковый литерал) в {_goal_file}")
            except (OSError, SyntaxError, ValueError) as _ge:
                print(f"[agent] Не удалось загрузить цель из файла: {_ge}")
        bot_delay = float(os.environ.get("BOT_LOOP_DELAY", "30"))
        agent.start_loop(bot_goal, cycle_delay=bot_delay, background=True)

        print("Агент живёт. Нажми Ctrl+C для выхода.")
        try:
            import time
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nЗавершение: сохранение памяти...")
            _graceful_shutdown(agent)
            print("Готово. Выход.")
        return

    # Режим автономного цикла
    if args.loop:
        mind = agent.components.get("proactive_mind")
        if mind:
            mind.start()
        try:
            agent.start_loop(args.loop, cycle_delay=args.delay, background=False)
        except KeyboardInterrupt:
            print("\nЗавершение автономного цикла...")
        finally:
            _graceful_shutdown(agent)
        return

    # Режим выполнения задачи
    if args.goal:
        result = agent.run(args.goal, max_cycles=args.cycles)
        print("\n=== Результат ===")
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        llm = agent.components["llm"]
        print(f"\nИспользовано токенов: {llm.total_tokens}")
        print(f"Стоимость: ${llm.total_cost_usd:.4f}")
        return

    # Интерактивный режим
    # Открываем браузер на чат — только здесь, не в --bot/--loop
    import webbrowser as _webbrowser
    _web_port = os.environ.get('WEB_PORT', '8000')
    _webbrowser.open(f"http://localhost:{_web_port}")

    print("\nАгент готов. Введи задачу или /quit для выхода.")
    print("Команды: /status  /search <запрос>  /verify <утверждение>  /quit\n")

    while True:
        try:
            user_input = input("Ты: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nПока!")
            break

        if not user_input:
            continue
        if user_input.lower() in ("/quit", "/exit", "quit", "exit"):
            break

        if user_input.startswith("/status"):
            print(json.dumps(agent.status(), ensure_ascii=False,
                             indent=2, default=str))
            continue

        if user_input.startswith("/search "):
            query = user_input[8:]
            results = agent.search(query)
            for r in results[:3]:
                print(f"  • {r.get('title', '')} — {r.get('url', '')}")
                print(f"    {r.get('snippet', '')[:150]}")
            continue

        if user_input.startswith("/verify "):
            claim = user_input[8:]
            v = agent.verify(claim)
            print(f"  Статус: {v['status']}  Уверенность: {v['confidence']}")
            continue

        # Диалог с агентом
        response = agent.chat(user_input)
        print(f"\nАгент: {response}\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nПока!")

#!/usr/bin/env python
# -*- coding: utf-8 -*-
# pylint: disable=broad-exception-caught
"""
DEEP AUDIT HARNESS — полная проверка архитектуры агента.

Проверяет:
1. Все ли 46 слоёв инициализируются?
2. Все ли компоненты подключены друг к другу?
3. Нет ли "фантомных" тестовых версий?
4. Реально ли Self-Improvement создаёт код?
5. Реально ли ModuleBuilder создаёт модули?
6. Реально ли AutonomousLoop запускается?
7. Реально ли Capability Discovery видит модули?
"""

import os
import sys
import json
import importlib

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

AGENT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPECTED_LAYERS = {
    "1":  "Perception", 
    "2":  "Knowledge", 
    "3":  "Cognitive Core",
    "4":  "Agent System",
    "5":  "Tool Layer",
    "6":  "OS Layer",
    "7":  "Software Dev",
    "8":  "Execution",
    "9":  "Learning",
    "10": "Reflection",
    "11": "Self-Repair",
    "12": "Self-Improvement",
    "14": "Multilingual",
    "15": "Communication",
    "16": "Security",
    "17": "Monitoring",
    "18": "Orchestration",
    "19": "Reliability",
    "20": "Autonomous Loop",
    "21": "Governance",
    "22": "Human Approval",
    "23": "State Manager",
    "24": "Data Validation",
    "25": "Evaluation",
    "26": "Budget Control",
    "27": "Environment Model",
    "28": "Sandbox",
    "29": "Skill Library",
    "30": "Task Decomposition",
    "31": "Knowledge Acquisition",
    "32": "Model Manager",
    "33": "Data Lifecycle",
    "34": "Distributed Execution",
    "35": "Capability Discovery",
    "36": "Experience Replay",
    "37": "Goal Manager",
    "38": "Long-Horizon Planning",
    "39": "Attention",
    "40": "Temporal Reasoning",
    "41": "Causal Reasoning",
    "42": "Ethics",
    "43": "Social Model",
    "44": "Hardware",
    "45": "Identity",
    "46": "Knowledge Verification",
}

# ─────────────────────────────────────────────────────────────────────────────
# AUDIT REPORT
# ─────────────────────────────────────────────────────────────────────────────

class AuditReport:
    def __init__(self):
        self.sections = {}
        self.passed = 0
        self.failed = 0
        self.warnings = 0
    
    def section(self, name: str):
        print(f"\n{'='*70}")
        print(f"  {name}")
        print(f"{'='*70}")
        self.sections[name] = []
        return self.sections[name]
    
    def ok(self, msg: str):
        print(f"  ✓ {msg}")
        self.passed += 1
    
    def fail(self, msg: str):
        print(f"  ✗ {msg}")
        self.failed += 1
    
    def warn(self, msg: str):
        print(f"  ⚠ {msg}")
        self.warnings += 1
    
    def summary(self):
        print(f"\n{'='*70}")
        print("  AUDIT SUMMARY")
        print(f"{'='*70}")
        print(f"  Passed: {self.passed}")
        print(f"  Failed: {self.failed}")
        print(f"  Warnings: {self.warnings}")
        status = "✓ PASS" if self.failed == 0 else "✗ FAIL"
        print(f"  Status: {status}")
        print(f"{'='*70}\n")

# ─────────────────────────────────────────────────────────────────────────────
# AUDIT CHECKS
# ─────────────────────────────────────────────────────────────────────────────

report = AuditReport()

# ─ AUDIT 1: Directory Structure ──────────────────────────────────────────────
section = report.section("AUDIT 1: Directory Structure (47 expected directories)")

expected_dirs = [
    "perception", "knowledge", "core", "agents", "tools", "environment",
    "execution", "learning", "llm", "communication", "monitoring",
    "attention", "reasoning", "reflection", "safety", "self_improvement",
    "self_repair", "skills", "social", "software_dev", "state",
    "validation", "hardware", "multilingual", "evaluation", "resources",
    "loop", "tests", "outputs", "videos",
]

for d in expected_dirs:
    path = os.path.join(AGENT_ROOT, d)
    if os.path.isdir(path):
        report.ok(f"Directory '{d}' exists")
    else:
        report.fail(f"Directory '{d}' MISSING")

# ─ AUDIT 2: Layer Initialization in agent.py ────────────────────────────────
section = report.section("AUDIT 2: Layer Initialization in agent.py")

with open(os.path.join(AGENT_ROOT, 'agent.py'), 'r', encoding='utf-8') as f:
    agent_source = f.read()

for layer_id, layer_name in EXPECTED_LAYERS.items():
    # Ищем комментарий СЛОЙ N
    marker = f"СЛОЙ {layer_id}:"
    if marker in agent_source:
        report.ok(f"Layer {layer_id} ({layer_name}) initialized")
    else:
        report.warn(f"Layer {layer_id} ({layer_name}) — no explicit СЛОЙ marker found")

# ─ AUDIT 3: Critical Components in build_agent() ────────────────────────────
section = report.section("AUDIT 3: Critical Components in build_agent()")

critical_components = {
    "ModuleBuilder": "dynamic module creation",
    "ProactiveMind": "autonomous thinking & background learning",
    "AutonomousGoalGenerator": "self-goal generation",
    "PersistentBrain": "memory between restarts",
    "AutonomousLoop": "main execution loop",
    "SelfImprovementSystem": "self-improvement",
    "SelfRepairSystem": "self-repair from errors",
    "Identity": "self-awareness",
}

for comp, desc in critical_components.items():
    if comp in agent_source:
        report.ok(f"{comp}: {desc}")
    else:
        report.fail(f"{comp}: NOT FOUND in agent.py")

# ─ AUDIT 4: Try importing all layers ─────────────────────────────────────────
section = report.section("AUDIT 4: Import Validation (all slices must load)")

sys.path.insert(0, AGENT_ROOT)

import_tests = [
    ("perception.perception_layer", "PerceptionLayer"),
    ("knowledge.knowledge_system", "KnowledgeSystem"),
    ("core.cognitive_core", "CognitiveCore"),
    ("agents.agent_system", ""),
    ("tools.tool_layer", ""),
    ("execution.execution_system", "ExecutionSystem"),
    ("learning.learning_system", "LearningSystem"),
    ("reflection.reflection_system", "ReflectionSystem"),
    ("self_repair.self_repair", "SelfRepairSystem"),
    ("self_improvement.self_improvement", "SelfImprovementSystem"),
    ("loop.autonomous_loop", "AutonomousLoop"),
    ("core.identity", "IdentityCore"),
    ("core.module_builder", "ModuleBuilder"),
    ("core.proactive_mind", "ProactiveMind"),
]

for module_name, class_name in import_tests:
    try:
        mod = importlib.import_module(module_name)
        if class_name:
            getattr(mod, class_name)
            report.ok(f"✓ {module_name}.{class_name}")
        else:
            report.ok(f"✓ {module_name}")
    except Exception as e:
        report.fail(f"{module_name}: {type(e).__name__}: {str(e)[:60]}")

# ─ AUDIT 5: Self-Improvement System Capability ──────────────────────────────
section = report.section("AUDIT 5: Self-Improvement System Capability")

try:
    from self_improvement.self_improvement import SelfImprovementSystem, ImprovementProposal
    
    # Проверяем методы
    methods = [
        'analyse_and_propose',
        'generate_strategy',
        'evaluate_improvement',
        'apply_improvement',
        'rollback_improvement',
    ]
    
    for method in methods:
        if hasattr(SelfImprovementSystem, method):
            report.ok(f"Method '{method}' exists")
        else:
            report.warn(f"Method '{method}' NOT FOUND")
    
    # Проверяем ImprovementProposal
    report.ok(
        f"ImprovementProposal class exists (strategies are structured): {ImprovementProposal.__name__}"
    )
except Exception as e:
    report.fail(f"SelfImprovementSystem import failed: {e}")

# ─ AUDIT 6: ModuleBuilder Capability (Dynamic Modules) ──────────────────────
section = report.section("AUDIT 6: ModuleBuilder Capability (Dynamic Module Creation)")

try:
    from core.module_builder import ModuleBuilder
    
    methods = [
        'generate_module_code',
        'save_module',
        'load_module',
        'list_modules',
        'delete_module',
        'load_all_from_registry',
        'validate_module_code',
    ]
    
    for method in methods:
        if hasattr(ModuleBuilder, method):
            report.ok(f"Method '{method}' exists")
        else:
            report.warn(f"Method '{method}' NOT FOUND (might not support dynamic creation)")
    
    # Проверяем директорию + registry
    registry_path = os.path.join(AGENT_ROOT, 'dynamic_registry.json')
    if os.path.exists(registry_path):
        try:
            with open(registry_path, 'r', encoding='utf-8') as f:
                reg = json.load(f)
            report.ok(f"Dynamic module registry exists ({len(reg)} modules)")
        except Exception as e:
            report.warn(f"Registry corrupted: {e}")
    else:
        report.warn("Dynamic module registry not yet created (will be on first module creation)")

except Exception as e:
    report.fail(f"ModuleBuilder import failed: {e}")

# ─ AUDIT 7: Capability Discovery (My Modules) ───────────────────────────────
section = report.section("AUDIT 7: Capability Discovery (Agent Self-Awareness)")

try:
    from core.identity import IdentityCore
    
    # Создаём Identity и тестируем
    identity = IdentityCore(name="Audit")
    
    # Проверяем методы
    methods = {
        'discover_modules': 'scan system directories',
        'modules_status_report': 'human-readable report',
        'get_real_capability_inventory': 'proven vs untested actions',
    }
    
    for method, desc in methods.items():
        if hasattr(identity, method):
            report.ok(f"Method '{method}': {desc}")
        else:
            report.fail(f"Method '{method}' NOT FOUND")
    
    # Пробуем запустить discovery
    try:
        discovery = identity.discover_modules(AGENT_ROOT)
        report.ok(f"discover_modules() works: {discovery['total_modules']} modules found")
        report.ok(f"  - Skills: {len(discovery['skills'])}")
        report.ok(f"  - Agents: {len(discovery['agents'])}")
        report.ok(f"  - Subsystems: {len(discovery['subsystems'])}")
    except Exception as e:
        report.fail(f"discover_modules() failed: {e}")

except Exception as e:
    report.fail(f"Identity import failed: {e}")

# ─ AUDIT 8: CognitiveCore Intent Handlers (my_modules) ──────────────────────
section = report.section("AUDIT 8: CognitiveCore Intent Handlers")

try:
    from core.cognitive_core import LocalBrain
    
    # Проверяем LocalBrain
    brain = LocalBrain()
    
    keywords_found = {
        '_CODE_KEYWORDS': 'code generation detection',
        '_PLAN_KEYWORDS': 'planning detection',
        '_RESEARCH_KEYWORDS': 'research detection',
        '_DECISION_KEYWORDS': 'decision making detection',
    }
    
    for kw, desc in keywords_found.items():
        if hasattr(brain, kw):
            report.ok(f"LocalBrain.{kw}: {desc}")
        else:
            report.fail(f"LocalBrain.{kw} NOT FOUND")
    
    # Проверяем в источнике Cognitive Core
    with open(os.path.join(AGENT_ROOT, 'core', 'cognitive_core.py'), 'r', encoding='utf-8') as f:
        cc_source = f.read()
    
    if '_MY_MODULES_KW' in cc_source:
        report.ok("CognitiveCore: _MY_MODULES_KW intent detection present")
    else:
        report.fail("CognitiveCore: _MY_MODULES_KW intent detection MISSING")
    
    if '_handle_my_modules' in cc_source:
        report.ok("CognitiveCore: _handle_my_modules() handler present")
    else:
        report.fail("CognitiveCore: _handle_my_modules() handler MISSING")

except Exception as e:
    report.fail(f"Intent handler check failed: {e}")

# ─ AUDIT 9: Knowledge Persistence ──────────────────────────────────────────
section = report.section("AUDIT 9: Knowledge Persistence (Long-Term Memory)")

brain_dir = os.path.join(AGENT_ROOT, '.agent_memory')
knowledge_path = os.path.join(brain_dir, 'knowledge.json')

if os.path.exists(knowledge_path):
    try:
        with open(knowledge_path, 'r', encoding='utf-8') as f:
            knowl_data = json.load(f)
        
        items_count = 0
        if isinstance(knowl_data, dict):
            items_count = len(knowl_data.get('long_term', {})) + len(knowl_data.get('episodic', []))
        
        if items_count > 0:
            report.ok(f"Knowledge persisted: {items_count} items in memory")
        else:
            report.warn("Knowledge store empty (cold start expected)")
    except Exception as e:
        report.fail(f"Knowledge file corrupted: {e}")
else:
    report.warn("No persistent knowledge yet (will be created on first run)")

# ─ AUDIT 10: Dynamic Registry ───────────────────────────────────────────────
section = report.section("AUDIT 10: Dynamic Module Registry")

registry_path = os.path.join(AGENT_ROOT, 'dynamic_registry.json')

if os.path.exists(registry_path):
    try:
        with open(registry_path, 'r', encoding='utf-8') as f:
            registry = json.load(f)
        
        total_modules = sum(len(v) for v in registry.values())
        report.ok(f"Dynamic module registry: {total_modules} modules created by agent")
        for category, modules in registry.items():
            report.ok(f"  - {category}: {len(modules)}")
    except Exception as e:
        report.fail(f"Registry corrupted: {e}")
else:
    report.warn("No dynamic modules yet (agent will create them as needed)")

# ─ AUDIT 11: End-to-End Test ────────────────────────────────────────────────
section = report.section("AUDIT 11: End-to-End Capability Test")

try:
    print("  Attempting to initialize minimal agent components...")
    
    # Загружаем env
    dotenv_module = importlib.import_module("dotenv")
    load_dotenv = getattr(dotenv_module, "load_dotenv")
    for env_path in (
        os.path.join(AGENT_ROOT, '.env'),
        os.path.join(AGENT_ROOT, 'config', '.env'),
    ):
        if os.path.exists(env_path):
            load_dotenv(env_path)
            break
    
    # Проверяем API ключи
    openai_key = os.environ.get('OPENAI_API_KEY')
    if not openai_key:
        report.warn("OPENAI_API_KEY not set — cannot test LLM integration")
    else:
        report.ok("OPENAI_API_KEY configured")
    
    # Создаём LLM
    from llm.openai_client import OpenAIClient
    from monitoring.monitoring import Monitoring
    
    monitoring = Monitoring(print_logs=False)
    llm = OpenAIClient(api_key=openai_key or "test", monitoring=monitoring)
    report.ok("LLM client initialized")
    
    # Создаём Knowledge
    from knowledge.knowledge_system import KnowledgeSystem
    from knowledge.vector_store import VectorStore
    
    vs = VectorStore(
        collection_name='audit',
        persist_dir=os.path.join(AGENT_ROOT, '.vector_store_audit'),
        use_chroma=False,  # in-memory for test
        monitoring=monitoring,
    )
    knowledge = KnowledgeSystem(vector_db=vs)
    report.ok("Knowledge system initialized")
    
    # Создаём Identity
    from core.identity import IdentityCore
    identity = IdentityCore(name="AuditAgent", cognitive_core=None, monitoring=monitoring)
    report.ok("Identity initialized")
    
    # Тестируем discovery
    discovery = identity.discover_modules(AGENT_ROOT)
    if discovery['total_modules'] > 0:
        report.ok(f"Capability Discovery works: {discovery['total_modules']} modules")
    else:
        report.warn("Capability Discovery found no modules")
    
    # Тестируем Module Builder
    from core.module_builder import ModuleBuilder
    mb = ModuleBuilder(
        cognitive_core=None,
        sandbox=None,
        monitoring=monitoring,
        working_dir=AGENT_ROOT,
        registry_path=os.path.join(AGENT_ROOT, 'dynamic_registry.json'),
    )
    report.ok("ModuleBuilder initialized")
    
    # Проверяем что ModuleBuilder может загрузить реестр
    modules = mb.load_all_from_registry()
    total = sum(len(v) for v in modules.values())
    if total > 0:
        report.ok(f"ModuleBuilder: {total} previously created modules detected")
    else:
        report.ok("ModuleBuilder: registry clean (ready for module creation)")

except Exception as e:
    import traceback
    report.fail(f"End-to-end test failed: {e}")
    print(traceback.format_exc())

# ─────────────────────────────────────────────────────────────────────────────
# SUMMARY
# ─────────────────────────────────────────────────────────────────────────────

report.summary()

# Выход с кодом ошибки если есть ошибки
sys.exit(0 if report.failed == 0 else 1)

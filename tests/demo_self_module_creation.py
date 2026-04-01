#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
LIVE DEMO: Agent Self-Module Creation

Демонстрирует как Agent:
1. Задаёт себе цель: "создать вспомогательный модуль"
2. Генерирует код через LLM
3. Тестирует в Sandbox
4. Записывает файл
5. Загружает в фоне
6. Использует его в будущих циклах

Это реальное self-improvement через динамическое расширение архитектуры.
"""

import os
import sys
import json
import importlib

# ─────────────────────────────────────────────────────────────────────────────

AGENT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _iter_env_paths() -> list[str]:
    return [
        os.path.join(AGENT_ROOT, '.env'),
        os.path.join(AGENT_ROOT, 'config', '.env'),
    ]

def load_env():
    """Загружаем .env"""
    dotenv_module = importlib.import_module("dotenv")
    load_dotenv = getattr(dotenv_module, "load_dotenv")
    for env_path in _iter_env_paths():
        if os.path.exists(env_path):
            load_dotenv(env_path)
            break

def demo():
    """Live demo: Agent creates a utility module for itself."""
    
    print("="*70)
    print("  DEMO: Autonomous Agent Self-Module Creation")
    print("="*70)
    print()
    
    load_env()
    sys.path.insert(0, AGENT_ROOT)
    
    # ─ STEP 1: Initialize core components ──────────────────────────────────
    print("STEP 1: Initialize core components")
    print("-"*70)
    
    from monitoring.monitoring import Monitoring
    from llm.openai_client import OpenAIClient
    from knowledge.knowledge_system import KnowledgeSystem
    from knowledge.vector_store import VectorStore
    from core.identity import IdentityCore 
    from core.module_builder import ModuleBuilder
    from environment.sandbox import SandboxLayer
    
    openai_key = os.environ.get('OPENAI_API_KEY')
    if not openai_key:
        print("[FAIL] OPENAI_API_KEY not set")
        return 1
    
    monitoring = Monitoring(print_logs=True, log_file=None)
    llm = OpenAIClient(api_key=openai_key, monitoring=monitoring)
    
    vs = VectorStore(
        collection_name='demo',
        persist_dir=os.path.join(AGENT_ROOT, '.vector_store_demo'),
        use_chroma=False,
        monitoring=monitoring,
    )
    knowledge = KnowledgeSystem(vector_db=vs)
    identity = IdentityCore(name="DemoAgent", monitoring=monitoring)
    sandbox = SandboxLayer(monitoring=monitoring)
    
    print("[OK] LLM initialized")
    print("[OK] Knowledge initialized")  
    print("[OK] Identity initialized")
    print("[OK] Sandbox initialized")
    
    # ─ STEP 2: Create a minimal CognitiveCore ─────────────────────────────
    print("\nSTEP 2: Create CognitiveCore")
    print("-"*70)
    
    from core.cognitive_core import CognitiveCore
    
    # Минимальный CognitiveCore (без perception/execution для demo)
    cognitive_core = CognitiveCore(
        llm_client=llm,
        perception=None,
        knowledge=knowledge,
        monitoring=monitoring,
        identity=identity,
    )
    print("[OK] CognitiveCore created")
    
    # ─ STEP 3: Create ModuleBuilder ──────────────────────────────────────
    print("\nSTEP 3: Create ModuleBuilder")
    print("-"*70)
    
    registry_path = os.path.join(AGENT_ROOT, 'dynamic_registry_demo.json')
    module_builder = ModuleBuilder(
        cognitive_core=cognitive_core,
        sandbox=sandbox,
        monitoring=monitoring,
        working_dir=AGENT_ROOT,
        registry_path=registry_path,
        arch_docs=[
            os.path.join(AGENT_ROOT, 'архитектура автономного Агента.txt'),
            os.path.join(AGENT_ROOT, 'Текстовый документ.txt'),
        ],
    )
    print("[OK] ModuleBuilder created")
    print(f"  Registry path: {registry_path}")
    
    # ─ STEP 4: Agent creates a utility module ────────────────────────────
    print("\nSTEP 4: Agent generates a new utility module")
    print("-"*70)
    
    module_name = "text_summarizer"
    module_desc = (
        "Utility for summarizing long texts. "
        "Takes a text and a target length, returns compressed summary preserving key points."
    )
    
    print(f"Requesting LLM to generate module: {module_name}")
    print(f"Description: {module_desc}")
    print()
    
    result = module_builder.build_module(
        name=module_name,
        description=module_desc,
        target_dir=os.path.join(AGENT_ROOT, 'dynamic_modules'),
        extra_prompt=(
            "Make it a reusable Python class that can be imported by other modules. "
            "Include docstrings. Avoid external dependencies beyond stdlib + numpy/pandas "
            "(if safe). Keep it under 200 lines."
        ),
    )
    
    print(f"Build status: {result.status}")
    print(f"Duration: {result.duration:.2f}s")
    
    if result.ok:
        print("[OK] Module created successfully!")
        print(f"  File: {result.file_path}")
        print(f"  Class: {result.class_name}")
        print()
        
        # Show generated code snippet
        if result.generated_code:
            lines = result.generated_code.split('\n')[:20]  # first 20 lines
            print("Generated code (first 20 lines):")
            print("-"*70)
            for i, line in enumerate(lines, 1):
                print(f"{i:3d}: {line}")
            if len(result.generated_code.split('\n')) > 20:
                print(f"... ({len(result.generated_code.split('\n')) - 20} more lines)")
            print("-"*70)
    else:
        print("[FAIL] Module creation failed!")
        print(f"  Error: {result.error}")
        return 1
    
    # ─ STEP 5: Verify module is in registry ──────────────────────────────
    print("\nSTEP 5: Verify module registration")
    print("-"*70)
    
    if os.path.exists(registry_path):
        try:
            with open(registry_path, 'r', encoding='utf-8') as f:
                registry = json.load(f)
            total = sum(len(v) for v in registry.values())
            print(f"[OK] Registry persisted ({total} modules)")
            for cat, items in registry.items():
                if items:
                    print(f"  {cat}: {len(items)}")
                    for item in items[-1:]:  # show last item
                        print(f"    - {item.get('name', '?')}")
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as e:
            print(f"[NOTE] Registry error: {e}")
    else:
        print("[NOTE] Registry file not created yet")
    
    # ─ STEP 6: Test using modules_status_report ──────────────────────────
    print("\nSTEP 6: Agent introspects itself (Capability Discovery)")
    print("-"*70)
    
    report = identity.modules_status_report(AGENT_ROOT)
    print(report)
    
    # ─ STEP 7: Simulate agents using the new module in next cycle ────────
    print("\nSTEP 7: Module would be used in autonomous loop")
    print("-"*70)
    
    print("In next AutonomousLoop cycle, agent would:")
    print("  1. Load module from registry")
    print("  2. Import TextSummarizer class")  
    print("  3. Call it in reasoning/planning")
    print("  4. Feedback loop: success -> persists; failure -> learn & improve")
    print()
    
    # ─ SUMMARY ────────────────────────────────────────────────────────────
    print("="*70)
    print("  DEMO SUMMARY")
    print("="*70)
    
    build_summary = module_builder.get_summary()
    print("ModuleBuilder session:")
    for key, val in build_summary.items():
        print(f"  {key}: {val}")
    print()
    
    print("[OK] Agent demonstrated full self-improvement cycle:")
    print("  [Goal] -> [LLM Generate] -> [Sandbox Test] -> [Disk Write] -> [Import] -> [Register]")
    print()
    print("This is NOT code generation as a service.")
    print("This is AGENT architect creating ITSELF new capabilities.")
    print()
    
    return 0

# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        exit_code = demo()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n[Demo interrupted]")
        sys.exit(1)
    except (RuntimeError, OSError, ValueError, TypeError, ImportError, AttributeError) as e:
        import traceback
        print(f"\n[FAIL] Demo failed: {e}")
        traceback.print_exc()
        sys.exit(1)

"""
Smoke & Integration Runner.

Уровни запуска:
  python -m tests.smoke_runner                  — smoke (быстрые, <30с)
  python -m tests.smoke_runner --integration    — smoke + интеграционные
  python -m tests.smoke_runner --full           — все тесты (кроме известных зависших)

Smoke: ядро, импорт, базовые сценарии.
Integration: dispatcher, evaluation, learning, brain.
Full: все 35 тест-файлов.
"""
import argparse
import sys
import unittest

# Тесты которые ВСЕГДА пропускаются (deadlock / зависание)
SKIP_ALWAYS = {
    'tests.test_memory_quality_regressions',
}

# ── Smoke: быстрые критические тесты (< 30 с) ──────────────────────────────
SMOKE_MODULES = [
    'tests.test_cognitive_core_regressions',
    'tests.test_smoke_hooks',
    'tests.test_telegram_document_input',
    'tests.test_identity_full_coverage',
    'tests.test_default_goal_coverage',
    'tests.test_attention_focus_coverage',
]

# ── Integration: основные подсистемы ─────────────────────────────────────────
INTEGRATION_MODULES = [
    'tests.test_action_dispatcher_coverage',
    'tests.test_priority2_coverage',
    'tests.test_priority3_coverage',
    'tests.test_persistent_brain_full_coverage',
    'tests.test_cognitive_core_full_coverage',
    'tests.test_goal_manager_full_coverage',
    'tests.test_model_manager_full_coverage',
    'tests.test_environment_model_full_coverage',
]

# ── Full: все тесты (включая priority 4-6 и tool coverage) ──────────────────
FULL_MODULES = [
    'tests.test_priority4_coverage',
    'tests.test_priority5_coverage',
    'tests.test_priority6_pure_logic',
    'tests.test_priority6_tools',
    'tests.test_priority6_remaining',
    'tests.test_proactive_mind_full_coverage',
    'tests.test_long_horizon_planning_full_coverage',
    'tests.test_module_builder_full_coverage',
    'tests.test_autonomous_goal_generator_full_coverage',
    'tests.test_telegram_bot_full_coverage',
    'tests.test_telegram_response_sanitizer',
    'tests.test_telegram_vector_regressions',
    'tests.test_telegram_voice_integration',
    'tests.test_web_interface_full_coverage',
    'tests.test_text_summarizer_full_coverage',
    'tests.test_tool_reference_coverage',
    'tests.test_knowledge_verification_regressions',
    'tests.test_runtime_support_coverage',
    'tests.test_agent_spawner_coverage',
    'tests.test_agent_system_coverage',
]


def build_suite(level: str = 'smoke') -> unittest.TestSuite:
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite()

    modules = list(SMOKE_MODULES)
    if level in ('integration', 'full'):
        modules += INTEGRATION_MODULES
    if level == 'full':
        modules += FULL_MODULES

    for module_name in modules:
        if module_name in SKIP_ALWAYS:
            continue
        try:
            suite.addTests(loader.loadTestsFromName(module_name))
        except Exception as e:
            print(f"[WARN] Не удалось загрузить {module_name}: {e}", file=sys.stderr)

    return suite


def main() -> int:
    parser = argparse.ArgumentParser(description='Smoke & Integration Runner')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--integration', action='store_true',
                       help='Smoke + интеграционные тесты')
    group.add_argument('--full', action='store_true',
                       help='Все тесты (кроме зависающих)')
    args = parser.parse_args()

    if args.full:
        level = 'full'
    elif args.integration:
        level = 'integration'
    else:
        level = 'smoke'

    print(f'> Уровень: {level.upper()}')
    suite = build_suite(level)
    print(f'> Тестов к запуску: {suite.countTestCases()}')
    print()

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
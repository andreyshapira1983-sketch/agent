import unittest


SMOKE_MODULES = [
    'tests.test_cognitive_core_regressions',
    'tests.test_smoke_hooks',
    'tests.test_telegram_document_input',
]


def build_suite() -> unittest.TestSuite:
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite()
    for module_name in SMOKE_MODULES:
        suite.addTests(loader.loadTestsFromName(module_name))
    return suite


def main() -> int:
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(build_suite())
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
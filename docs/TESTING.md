# Проверки и тестовые сценарии

Этот документ описывает локальные проверки качества и запуск тестов через скрипты.

## Базовые проверки

- Линтер: `ruff check .`
- Типы: `mypy src/`
- Безопасность: `bandit -r src/`
- Тесты: `python -m pytest tests/ src/tests/ -v`

## Скрипты PowerShell

- `scripts/run_smoke_tests.ps1` — быстрые smoke-тесты.
- `scripts/run_full_tests.ps1` — полный прогон `tests/` и `src/tests/`.
- `scripts/run_nightly_tests.ps1` — ночной прогон с отдельными JUnit-XML и `.log`.
- `scripts/register_nightly_task.ps1` — регистрация nightly-задачи в Windows Task Scheduler.

Примеры запуска:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_smoke_tests.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\run_full_tests.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\run_nightly_tests.ps1
```

## Артефакты

JUnit и логи сохраняются в `test-results/`:

- `test-results/smoke-tests.xml`
- `test-results/full-test-suite.xml`
- `test-results/full-test-suite.fallback.log` (если сработал fallback без junitxml)
- `test-results/nightly/nightly_*.xml`
- `test-results/nightly/nightly_*.log`

## Nightly в Windows

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\register_nightly_task.ps1
```

По умолчанию создаётся задача `AgentNightlyTests` на ежедневный запуск в `02:00`.

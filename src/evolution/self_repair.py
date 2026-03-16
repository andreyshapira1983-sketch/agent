"""
Self-repair: запустить тесты, при падении сгенерировать патч через LLM,
провалидировать в sandbox и принять если тесты прошли.
"""
from __future__ import annotations

import logging
import re
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[2]
_TESTS_DIR = _ROOT / "tests"


def _run_pytest() -> tuple[bool, str]:
    """Запустить pytest, вернуть (passed, output)."""
    try:
        r = subprocess.run(  # nosec B603 B607
            [sys.executable, "-m", "pytest", str(_TESTS_DIR), "-q", "--tb=short"],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(_ROOT),
        )
        out = (r.stdout or "") + (r.stderr or "")
        return r.returncode == 0, out.strip()
    except subprocess.TimeoutExpired:
        return False, "pytest timeout"
    except Exception as e:
        return False, f"pytest error: {e}"


def _parse_first_failure(output: str) -> dict | None:
    """
    Извлечь {file, error, traceback} из первого упавшего теста в выводе pytest.
    Возвращает dict или None если не удалось разобрать.
    """
    m = re.search(r"FAILED\s+([\w/\\.]+)::", output)
    if not m:
        return None
    rel_file = m.group(1).replace("\\", "/")
    tb_match = re.search(r"(E\s+\S.*?)(?=\n\n|\Z)", output, re.DOTALL)
    error_line = tb_match.group(1).strip() if tb_match else ""
    return {"file": rel_file, "error": error_line[:500], "traceback": output[:2000]}


def try_repair() -> bool:
    """
    Один цикл самовосстановления:
    1. Запустить тесты. Если прошли → True (ремонт не нужен).
    2. Найти первую ошибку в выводе pytest.
    3. Сгенерировать кандидат-патч через LLM.
    4. Провалидировать в sandbox.
    5. Принять если прошёл; вернуть True.
    Возвращает False если не удалось починить.
    """
    ok, output = _run_pytest()
    try:
        from src.monitoring.metrics import metrics  # noqa: PLC0415
        metrics.record_test_run(passed=ok)
    except Exception:
        pass
    if ok:
        logger.info("try_repair: все тесты прошли, ремонт не нужен")
        return True

    logger.warning("try_repair: тесты упали, пытаемся починить")
    failure = _parse_first_failure(output)
    if not failure:
        logger.warning("try_repair: не удалось определить файл ошибки из вывода pytest")
        try:
            from src.monitoring.metrics import metrics  # noqa: PLC0415
            metrics.record_repair_attempt(success=False)
        except Exception:
            pass
        return False

    logger.info("try_repair: первая ошибка в %s", failure["file"])
    from src.evolution.manager import EvolutionManager  # noqa: PLC0415

    patch_dir = str(_ROOT / "config" / "candidate_patches")
    mgr = EvolutionManager(
        patch_directory=patch_dir,
        test_directory=str(_TESTS_DIR),
        backup_directory=str(_ROOT / "backups"),
    )
    patch_id = mgr.generate_patch(failure)
    if not patch_id:
        logger.warning("try_repair: generate_patch вернул None (нет ключа LLM или ошибка)")
        try:
            from src.monitoring.metrics import metrics  # noqa: PLC0415
            metrics.record_repair_attempt(success=False)
        except Exception:
            pass
        return False

    logger.info("try_repair: кандидат %s создан, sandbox-валидация...", patch_id)
    from src.evolution.safety import validate_candidate_with_tests, accept_patch_to_stable  # noqa: PLC0415

    validated = validate_candidate_with_tests(patch_id)
    try:
        from src.monitoring.metrics import metrics  # noqa: PLC0415
        metrics.record_test_run(passed=validated)
    except Exception:
        pass
    if not validated:
        logger.warning("try_repair: кандидат %s не прошёл sandbox-тесты", patch_id)
        try:
            from src.monitoring.metrics import metrics  # noqa: PLC0415
            metrics.record_repair_attempt(success=False)
        except Exception:
            pass
        return False

    msg = accept_patch_to_stable(patch_id)
    logger.info("try_repair: accept → %s", msg)
    success = "applied to" in msg
    try:
        from src.monitoring.metrics import metrics  # noqa: PLC0415
        metrics.record_repair_attempt(success=success)
    except Exception:
        pass
    return success

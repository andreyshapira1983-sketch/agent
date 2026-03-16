import os
import shutil
import time
import logging
from src.evolution.auto_patch import AutoPatch
from src.evolution.auto_tests import AutoTests
from src.reflection.self_review import SelfReview

logger = logging.getLogger(__name__)


def backup_file(src_path, backups_dir="backups"):
    """Создать резервную копию src_path. Вернуть путь к бэкапу."""
    if not os.path.exists(src_path):
        logger.error("backup_file: исходный файл не найден: %s", src_path)
        raise FileNotFoundError(f"Source file not found: {src_path}")

    os.makedirs(backups_dir, exist_ok=True)

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    base = os.path.basename(src_path)
    backup_name = f"{base}.{timestamp}.bak"
    backup_path_tmp = os.path.join(backups_dir, backup_name + ".tmp")
    backup_path = os.path.join(backups_dir, backup_name)

    try:
        shutil.copy2(src_path, backup_path_tmp)
        os.replace(backup_path_tmp, backup_path)
        logger.info("backup_file: создан бэкап %s для %s", backup_path, src_path)
        return backup_path
    except Exception:
        logger.exception("backup_file: не удалось создать бэкап для %s", src_path)
        try:
            if os.path.exists(backup_path_tmp):
                os.remove(backup_path_tmp)
        except Exception:
            logger.exception("backup_file: не удалось удалить временный файл %s", backup_path_tmp)
        raise


class EvolutionManager:
    def __init__(self, patch_directory, test_directory, backup_directory):
        self.patch_directory = patch_directory
        self.test_directory = test_directory
        self.backup_directory = backup_directory
        os.makedirs(self.backup_directory, exist_ok=True)
        self.auto_patch = AutoPatch(patch_directory)
        self.auto_tests = AutoTests()
        self.self_review = SelfReview()
        self.evolution_log = []

    def backup_file(self, src_path):
        """Создать резервную копию файла в self.backup_directory."""
        return backup_file(src_path, self.backup_directory)

    def get_target_file_path(self, patch_name):
        """Путь к целевому файлу по имени патча (например example_patch.patch -> test_directory/example_patch.py)."""
        base = os.path.basename(patch_name).rsplit(".", 1)[0]
        return os.path.join(self.test_directory, base + ".py")

    def log_change(self, message):
        """Добавить запись в лог эволюции."""
        self.evolution_log.append(message)
        logger.info("evolution: %s", message)

    def generate_patch(self, error_info: dict) -> "str | None":
        """
        Сгенерировать патч для исправления ошибки через LLM (OpenAI).
        error_info: {"file": "relative/path.py", "error": "...", "traceback": "..."}
        Возвращает patch_id из safety.submit_candidate_patch, или None при неудаче.
        """
        import os
        import re
        from pathlib import Path

        target_path = (error_info or {}).get("file") or ""
        if not target_path:
            logger.warning("generate_patch: нет 'file' в error_info")
            return None

        project_root = Path(__file__).resolve().parents[2]
        full_path = (project_root / target_path).resolve()
        if not str(full_path).startswith(str(project_root)):
            logger.warning("generate_patch: путь вне проекта: %s", target_path)
            return None

        try:
            current_content = full_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.warning("generate_patch: файл не найден: %s", full_path)
            return None

        key = os.getenv("OPENAI_API_KEY") or os.getenv("OPEN_KEY_API", "")
        if not key:
            logger.warning("generate_patch: OPENAI_API_KEY не задан — LLM пропущен")
            return None

        error_summary = (error_info.get("error") or "")[:500]
        traceback_text = (error_info.get("traceback") or "")[:1500]
        user_msg = (
            f"File: {target_path}\n\n"
            f"Error: {error_summary}\n\n"
            f"Traceback:\n{traceback_text}\n\n"
            f"Current file content:\n---\n{current_content[:6000]}\n---\n"
            "Output ONLY the corrected full file content. No explanation, no markdown fences."
        )

        try:
            from openai import OpenAI  # noqa: PLC0415
            client = OpenAI(api_key=key)
            r = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a patch generator. Fix the Python error described by the user. "
                            "Output ONLY the corrected full file content. No explanation, no markdown."
                        ),
                    },
                    {"role": "user", "content": user_msg},
                ],
            )
            new_content = (r.choices[0].message.content or "").strip()
            if new_content.startswith("```"):
                new_content = re.sub(r"^```\w*\n?", "", new_content)
                new_content = re.sub(r"\n?```\s*$", "", new_content)
            new_content = new_content.strip()
        except Exception as e:
            logger.exception("generate_patch: ошибка LLM: %s", e)
            return None

        if not new_content or len(new_content) < 10:
            logger.warning("generate_patch: LLM вернул пустой/короткий контент")
            return None

        try:
            from src.evolution.safety import submit_candidate_patch  # noqa: PLC0415
            patch_id = submit_candidate_patch(
                target_path,
                new_content,
                reason=f"auto-repair: {error_summary[:100]}",
            )
        except Exception as e:
            logger.exception("generate_patch: ошибка submit: %s", e)
            return None

        if patch_id.startswith("Error:"):
            logger.warning("generate_patch: submit отклонён: %s", patch_id)
            return None

        self.log_change(f"generate_patch: создан кандидат {patch_id} для {target_path}")
        return patch_id

    def apply_patch(self, patch_name):
        original_file_path = self.get_target_file_path(patch_name)
        try:
            if os.path.exists(original_file_path):
                self.backup_file(original_file_path)
            self.auto_patch.apply_patch(patch_name)
            self.log_change(f"Successfully applied patch: {patch_name}.")
        except Exception as e:
            logging.error("Error during applying patch: %s", e)
            self.log_change(f"Failed to apply patch: {patch_name}. {e}")
            self.rollback_patch(original_file_path)

    def _apply_patch(self, target_path, patch_text):
        """Применить текст патча к target_path. Возвращает (backup_path, success)."""
        backup_path = None
        try:
            backup_path = backup_file(target_path, self.backup_directory)
        except FileNotFoundError as e:
            logger.error("apply_patch: не удалось создать бэкап: %s", e)
            raise
        try:
            with open(target_path, "w", encoding="utf-8") as f:
                f.write(patch_text)
            logger.info("apply_patch: патч применён к %s", target_path)
            return backup_path, True
        except Exception as e:
            logger.exception("apply_patch: ошибка при применении патча: %s", e)
            try:
                if backup_path and os.path.exists(backup_path):
                    shutil.copy2(backup_path, target_path)
                    logger.info("apply_patch: откат выполнен из %s", backup_path)
            except Exception as ex:
                logger.exception("apply_patch: не удалось выполнить откат: %s", ex)
            return backup_path, False

    def rollback_patch(self, original_file_path):
        """Восстановить файл из последнего бэкапа в backup_directory."""
        base = os.path.basename(original_file_path)
        if not os.path.isdir(self.backup_directory):
            self.log_change(f"Rollback skipped: backup dir not found: {self.backup_directory}.")
            return
        backups = [
            os.path.join(self.backup_directory, f)
            for f in os.listdir(self.backup_directory)
            if f.startswith(base) and f.endswith(".bak")
        ]
        if not backups:
            self.log_change(f"Failed to roll back; no backup for: {original_file_path}.")
            raise FileNotFoundError(f"No backup found for: {original_file_path}")
        backup_path = max(backups, key=os.path.getmtime)
        try:
            shutil.copy2(backup_path, original_file_path)
            self.log_change(f"Rolled back: {original_file_path} from {backup_path}.")
        except Exception as e:
            logger.exception("rollback_patch: %s", e)
            self.log_change(f"Rollback failed: {e}.")


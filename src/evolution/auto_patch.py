"""
AutoPatch: чтение и применение патчей из каталога. Используется EvolutionManager.
Для безопасного применения через sandbox см. evolution.safety и инструменты propose_patch/validate_patch/accept_patch.
"""
import logging
import os


class AutoPatch:
    def __init__(self, patch_directory: str):
        self.patch_directory = patch_directory

    def apply_patch(self, patch_name: str) -> None:
        patch_path = os.path.join(self.patch_directory, patch_name)
        if not os.path.isfile(patch_path):
            raise FileNotFoundError(f"Patch file {patch_path} does not exist.")
        with open(patch_path, "r", encoding="utf-8") as f:
            patch_content = f.read()
        self._apply_patch_content(patch_content, patch_name)

    def _apply_patch_content(self, patch_content: str, patch_name: str) -> None:
        """Переопределите для реальной логики применения (запись в файл и т.д.)."""
        logging.info("Applying patch: %s", patch_name)

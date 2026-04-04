# Integrity Checker — проверка целостности файлов и supply chain защита
# Хеш-проверка JSON-файлов состояния + белый список пакетов для bootstrap.

import hashlib
import json
import os


class IntegrityChecker:
    """
    Проверяет целостность файлов состояния (knowledge.json, registry.json и др.)
    и валидирует пакеты перед pip install по белому списку.
    """

    def __init__(self, working_dir: str, monitoring=None):
        self._working_dir = working_dir
        self._monitoring = monitoring
        # Хранилище эталонных хешей: {rel_path: sha256_hex}
        self._manifest_path = os.path.join(working_dir, '.integrity_manifest.json')
        self._hashes: dict[str, str] = {}
        self._load_manifest()

    # ── Целостность файлов ────────────────────────────────────────────────────

    def record(self, file_path: str):
        """Записывает текущий хеш файла как эталон (вызывать после успешной записи)."""
        h = self._hash_file(file_path)
        if h is None:
            return
        rel = os.path.relpath(file_path, self._working_dir)
        self._hashes[rel] = h
        self._save_manifest()

    def verify_file(self, file_path: str) -> bool:
        """Проверяет файл на соответствие ранее записанному хешу.
        Возвращает True если хеш совпадает ИЛИ если файл ранее не записывался."""
        rel = os.path.relpath(file_path, self._working_dir)
        expected = self._hashes.get(rel)
        if expected is None:
            return True  # нет эталона — пропускаем

        actual = self._hash_file(file_path)
        if actual is None:
            # Файл не читается — подозрительно
            if self._monitoring:
                self._monitoring.warning(
                    f"IntegrityChecker: файл не читается: {rel}",
                    source="integrity_checker",
                )
            return False

        if actual == expected:
            return True

        if self._monitoring:
            self._monitoring.warning(
                f"IntegrityChecker: хеш НЕ совпадает для {rel}! "
                f"Ожидался {expected[:12]}…, получен {actual[:12]}…",
                source="integrity_checker",
            )
        return False

    # ── Supply chain: белый список пакетов ────────────────────────────────────

    def validate_packages(self, packages: list[tuple[str, str]],
                          requirements_path: str | None = None) -> tuple[list[str], list[str]]:
        """
        Проверяет pip-пакеты по белому списку из requirements.txt.

        Args:
            packages: [(import_name, pip_name), ...] из REQUIRED_PACKAGES
            requirements_path: путь к requirements.txt (белый список)

        Returns:
            (allowed, blocked) — списки pip_name
        """
        whitelist = self._load_whitelist(requirements_path)
        if not whitelist:
            # Нет requirements.txt — fail-closed: блокируем все неизвестные пакеты
            if self._monitoring:
                self._monitoring.warning(
                    "IntegrityChecker: requirements.txt не найден — все пакеты заблокированы (fail-closed)",
                    source="integrity_checker",
                )
            return [], [p[1] for p in packages]

        allowed = []
        blocked = []
        for _import_name, pip_name in packages:
            # Нормализация: pip install NAME → name, underscores = dashes
            normalized = pip_name.lower().replace('-', '_').replace('.', '_')
            if normalized in whitelist:
                allowed.append(pip_name)
            else:
                blocked.append(pip_name)
                if self._monitoring:
                    self._monitoring.warning(
                        f"IntegrityChecker: пакет {pip_name!r} НЕ в белом списке — заблокирован",
                        source="integrity_checker",
                    )
        return allowed, blocked

    # ── Внутренние методы ─────────────────────────────────────────────────────

    def _hash_file(self, file_path: str) -> str | None:
        """SHA-256 содержимого файла."""
        try:
            h = hashlib.sha256()
            with open(file_path, 'rb') as f:
                for chunk in iter(lambda: f.read(65536), b''):
                    h.update(chunk)
            return h.hexdigest()
        except (OSError, IOError):
            return None

    def _load_whitelist(self, requirements_path: str | None) -> set[str]:
        """Читает requirements.txt → set нормализованных имён."""
        path = requirements_path or os.path.join(self._working_dir, 'requirements.txt')
        if not os.path.exists(path):
            return set()
        names = set()
        try:
            with open(path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#') or line.startswith('-'):
                        continue
                    # "package>=1.0" → "package"
                    name = line.split('>=')[0].split('<=')[0].split('==')[0]
                    name = name.split('~=')[0].split('!=')[0].split('[')[0].strip()
                    if name:
                        names.add(name.lower().replace('-', '_').replace('.', '_'))
        except (OSError, IOError):
            pass
        return names

    def _load_manifest(self):
        """Загружает manfiest с диска."""
        if os.path.exists(self._manifest_path):
            try:
                with open(self._manifest_path, 'r', encoding='utf-8') as f:
                    self._hashes = json.load(f)
            except (OSError, json.JSONDecodeError, ValueError):
                self._hashes = {}

    def _save_manifest(self):
        """Сохраняет manifest на диск."""
        try:
            os.makedirs(os.path.dirname(self._manifest_path), exist_ok=True)
            with open(self._manifest_path, 'w', encoding='utf-8') as f:
                json.dump(self._hashes, f, indent=2, ensure_ascii=False)
        except (OSError, IOError):
            pass

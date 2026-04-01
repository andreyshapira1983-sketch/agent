# Patch Signing — криптографическая подпись патчей.
# Каждый патч перед записью подписывается HMAC-SHA256.
# Перед применением .bak-откатом — подпись верифицируется.
#
# Ключ генерируется при первом запуске и сохраняется в .patch_key.
# Ключ НЕ должен попадать в git (уже в .gitignore).

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time


_KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.patch_key')
_SIGNATURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'logs', 'patch_signatures')


def _get_or_create_key() -> bytes:
    """Возвращает ключ подписи. Создаёт новый если не существует."""
    key_path = os.path.realpath(_KEY_FILE)
    if os.path.exists(key_path):
        with open(key_path, 'rb') as f:
            key = f.read().strip()
        if len(key) >= 32:
            return key
    # Генерируем новый ключ
    key = os.urandom(64)
    os.makedirs(os.path.dirname(key_path), exist_ok=True)
    with open(key_path, 'wb') as f:
        f.write(key)
    return key


def sign_patch(file_path: str, original_code: str, patched_code: str,
               patch_source: str = 'unknown') -> str:
    """
    Подписывает патч HMAC-SHA256.

    Args:
        file_path:     абсолютный путь к файлу
        original_code: код до патча
        patched_code:  код после патча
        patch_source:  кто сгенерировал патч (self_repair, module_builder, etc.)

    Returns:
        hex-строка подписи (64 символа)
    """
    key = _get_or_create_key()
    payload = _build_payload(file_path, original_code, patched_code)
    signature = hmac.new(key, payload, hashlib.sha256).hexdigest()

    # Записываем метаданные подписи
    _store_signature_record(file_path, signature, patch_source,
                            len(original_code), len(patched_code))
    return signature


def verify_patch(file_path: str, original_code: str, patched_code: str,
                 expected_signature: str) -> bool:
    """
    Верифицирует подпись патча.

    Returns:
        True если подпись совпадает.
    """
    key = _get_or_create_key()
    payload = _build_payload(file_path, original_code, patched_code)
    actual = hmac.new(key, payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(actual, expected_signature)


def _build_payload(file_path: str, original_code: str, patched_code: str) -> bytes:
    """Собирает каноническое представление патча для подписи."""
    # Нормализуем путь для воспроизводимости
    norm_path = os.path.normpath(os.path.abspath(file_path))
    content = f"{norm_path}\n---ORIGINAL---\n{original_code}\n---PATCHED---\n{patched_code}"
    return content.encode('utf-8')


def _store_signature_record(file_path: str, signature: str,
                            source: str, orig_len: int, patch_len: int):
    """Записывает запись о подписи в JSONL-лог."""
    os.makedirs(os.path.realpath(_SIGNATURE_DIR), exist_ok=True)
    log_path = os.path.join(os.path.realpath(_SIGNATURE_DIR), 'patches.jsonl')
    record = {
        'ts': time.time(),
        'file': os.path.basename(file_path),
        'signature': signature[:16] + '...',
        'source': source,
        'original_len': orig_len,
        'patched_len': patch_len,
        'delta_pct': round((patch_len - orig_len) / max(orig_len, 1) * 100, 1),
    }
    try:
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
    except OSError:
        pass

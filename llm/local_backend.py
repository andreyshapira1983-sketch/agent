"""Local neural backend (Transformers) for offline/home inference.

Интерфейс совместим с OpenAIClient/ClaudeClient:
    infer(prompt, context=None, system=None, history=None) -> str
"""

from __future__ import annotations

import os
import time
import importlib
from typing import Any

try:
    _torch = importlib.import_module('torch')
    _transformers = importlib.import_module('transformers')
    _AutoModelForCausalLM = getattr(_transformers, 'AutoModelForCausalLM')
    _AutoTokenizer = getattr(_transformers, 'AutoTokenizer')
    _TRANSFORMERS_AVAILABLE = True
except (ImportError, AttributeError):
    _torch = None
    _AutoModelForCausalLM = None
    _AutoTokenizer = None
    _TRANSFORMERS_AVAILABLE = False


class LocalNeuralBackend:
    """Локальный LLM backend внутри процесса агента через HuggingFace Transformers."""

    def __init__(
        self,
        model: str | None = None,
        timeout: int = 90,
        monitoring=None,
    ):
        # По умолчанию используем instruct-модель (не базовую GPT2),
        # чтобы диалог и следование инструкциям были стабильнее.
        self.model = model or os.environ.get("LOCAL_LLM_MODEL", "Qwen/Qwen2.5-1.5B-Instruct")
        self.timeout = timeout
        self.monitoring = monitoring
        self.max_new_tokens = int(os.environ.get("LOCAL_LLM_MAX_NEW_TOKENS", "256"))
        self.temperature = float(os.environ.get("LOCAL_LLM_TEMPERATURE", "0.7"))

        self._total_tokens = 0
        self._total_cost = 0.0  # локальный backend: стоимость считаем нулевой
        self._tokenizer: Any = None
        self._model: Any = None
        self._device = "cpu"
        self._load_error = ""

    def infer(self, prompt: str, context=None, system: str | None = None,
              history: list | None = None, max_tokens: int | None = None) -> str:
        """Генерирует ответ локально через transformers model.generate()."""
        user_prompt = str(prompt or "").strip()
        if not user_prompt:
            return ""

        self._ensure_loaded()
        if self._tokenizer is None or self._model is None:
            raise RuntimeError("Local transformers backend not initialized")

        messages: list[dict[str, str]] = []
        if system:
            sys_text = str(system)
            if context:
                sys_text += f"\n\nКонтекст:\n{self._format_context(context)}"
            messages.append({"role": "system", "content": sys_text})
        elif context:
            messages.append({
                "role": "system",
                "content": f"Контекст:\n{self._format_context(context)}",
            })

        if history:
            for item in history[-20:]:
                role = str(item.get("role", "user"))
                content = str(item.get("content", ""))
                if role in ("user", "assistant") and content:
                    messages.append({"role": role, "content": content})

        messages.append({"role": "user", "content": user_prompt})

        prompt_text = self._to_prompt(messages)

        t0 = time.time()
        tokenizer = self._tokenizer
        model = self._model

        inputs = tokenizer(prompt_text, return_tensors="pt", truncation=True, max_length=2048)
        if self._device != "cpu":
            inputs = {k: v.to(self._device) for k, v in inputs.items()}

        if _torch is None:
            raise RuntimeError("torch is not available")
        with _torch.no_grad():
            generation_tokens = int(max_tokens) if max_tokens is not None else self.max_new_tokens
            output_ids = model.generate(
                **inputs,
                max_new_tokens=generation_tokens,
                do_sample=True,
                temperature=self.temperature,
                pad_token_id=tokenizer.eos_token_id,
            )

        elapsed = time.time() - t0
        input_token_count = int(inputs["input_ids"].shape[-1])
        generated_ids = output_ids[0][input_token_count:]
        text = tokenizer.decode(generated_ids, skip_special_tokens=True).strip()
        total = input_token_count + int(generated_ids.shape[-1])
        if total > 0:
            self._total_tokens += total

        if self.monitoring:
            self.monitoring.record_latency("local_llm", elapsed)
            self.monitoring.record_metric("local_llm.tokens", self._total_tokens, unit="tok")

        # Авто-выгрузка после инференса: освобождаем RAM если порог превышен.
        # Управляется переменной LOCAL_LLM_AUTO_UNLOAD (по умолчанию true).
        # Модель перезагрузится лениво при следующем вызове (_ensure_loaded).
        _auto_unload_env = os.environ.get("LOCAL_LLM_AUTO_UNLOAD", "true").lower()
        if _auto_unload_env in ("1", "true", "yes"):
            try:
                _psutil = importlib.import_module('psutil')
                _ram_pct = _psutil.virtual_memory().percent
            except (ImportError, AttributeError, TypeError, ValueError, RuntimeError, OSError):
                _ram_pct = 100.0  # нет psutil — выгружаем безопасно
            _unload_threshold = float(os.environ.get("LOCAL_LLM_UNLOAD_THRESHOLD", "75"))
            if _ram_pct > _unload_threshold:
                if self.monitoring:
                    self.monitoring.info(
                        f"[local_backend] RAM {_ram_pct:.0f}% > {_unload_threshold:.0f}% — выгружаю Qwen",
                        source="local_backend",
                    )
                self.unload()

        return text.strip()

    def health(self) -> dict:
        """Проверка готовности in-process transformers backend."""
        if not _TRANSFORMERS_AVAILABLE:
            return {
                "ok": False,
                "backend": "transformers",
                "model": self.model,
                "loaded": False,
                "error": "transformers/torch не установлены",
            }
        return {
            "ok": True,
            "backend": "transformers",
            "model": self.model,
            "loaded": self._model is not None and self._tokenizer is not None,
            "device": self._device,
            "error": self._load_error,
        }

    @property
    def total_tokens(self) -> int:
        return self._total_tokens

    @property
    def total_cost_usd(self) -> float:
        return self._total_cost

    def unload(self) -> None:
        """Выгружает модель из памяти. Вызывается при нехватке RAM."""
        if self._model is not None:
            try:
                if _torch is not None:
                    del self._model
                    if _torch.cuda.is_available():
                        _torch.cuda.empty_cache()
                else:
                    del self._model
            except (AttributeError, TypeError, RuntimeError, OSError):
                pass
            self._model = None
        if self._tokenizer is not None:
            del self._tokenizer
            self._tokenizer = None
        try:
            import gc as _gc
            _gc.collect()
        except (ImportError, RuntimeError):
            pass
        if self.monitoring:
            self.monitoring.info("[local_backend] Модель выгружена из RAM", source="local_backend")

    def set_model(self, model: str):
        self.model = str(model or self.model)
        self._model = None
        self._tokenizer = None
        self._load_error = ""

    def _ensure_loaded(self):
        if self._model is not None and self._tokenizer is not None:
            return
        if not _TRANSFORMERS_AVAILABLE:
            raise RuntimeError("Local transformers backend requires: pip install transformers torch")

        self._device = "cpu"
        if _torch is not None and _torch.cuda.is_available():
            self._device = "cuda"

        try:
            if _AutoTokenizer is None or _AutoModelForCausalLM is None:
                raise RuntimeError("transformers modules are not available")
            local_path = self._resolve_local_path(self.model)
            if local_path != self.model:
                # Нашли полный снапшот на диске — грузим без сети
                self._tokenizer = _AutoTokenizer.from_pretrained(local_path, local_files_only=True)
                self._model = _AutoModelForCausalLM.from_pretrained(local_path, local_files_only=True)
            else:
                # Нет локальной копии — скачиваем автоматически
                self._tokenizer = _AutoTokenizer.from_pretrained(self.model)
                self._model = _AutoModelForCausalLM.from_pretrained(self.model)
            if self._tokenizer.pad_token_id is None and self._tokenizer.eos_token_id is not None:
                self._tokenizer.pad_token = self._tokenizer.eos_token
            if self._device == "cuda":
                self._model.to(self._device)
            self._model.eval()
            self._load_error = ""
            if self.monitoring:
                self.monitoring.info(
                    f"Local transformers model loaded: {self.model} on {self._device}",
                    source="local_backend",
                )
        except (AttributeError, TypeError, ValueError, RuntimeError, OSError, ImportError) as exc:
            self._load_error = f"{type(exc).__name__}: {exc}"
            raise RuntimeError(f"Не удалось загрузить локальную модель {self.model}: {exc}") from exc

    def _to_prompt(self, messages: list[dict[str, str]]) -> str:
        if self._tokenizer is None:
            return "\n".join(f"{m['role']}: {m['content']}" for m in messages)
        if hasattr(self._tokenizer, "apply_chat_template"):
            try:
                return str(self._tokenizer.apply_chat_template(
                    messages,
                    tokenize=False,
                    add_generation_prompt=True,
                ))
            except (AttributeError, TypeError, ValueError, RuntimeError):
                pass
        return "\n".join(f"{m['role']}: {m['content']}" for m in messages)

    def _resolve_local_path(self, model_id: str) -> str:
        """Возвращает абсолютный путь к модели в кеше HF, не обращаясь к сети.

        Структура кеша: ~/.cache/huggingface/hub/models--<org>--<name>/snapshots/<hash>/
        Если модель не найдена в кеше — возвращает model_id как есть (попытка загрузки).
        """
        import pathlib

        # Если уже абсолютный путь или относительный с config.json — возвращаем как есть
        p = pathlib.Path(model_id)
        if p.is_absolute() and (p / "config.json").exists():
            return model_id
        if (p / "config.json").exists():
            return model_id

        # Стандартный кеш HuggingFace
        cache_dir = pathlib.Path(
            os.environ.get("HF_HOME", "")
            or os.environ.get("HUGGINGFACE_HUB_CACHE", "")
            or (pathlib.Path.home() / ".cache" / "huggingface" / "hub")
        )

        # models--org--name  (слэши заменяются на --)
        folder_name = "models--" + model_id.replace("/", "--")
        snapshots_dir = cache_dir / folder_name / "snapshots"
        if snapshots_dir.exists():
            # Берём самый свежий снапшот у которого есть веса
            snaps = sorted(snapshots_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True)
            for snap in snaps:
                has_weights = (
                    any(snap.glob("*.safetensors"))
                    or any(snap.glob("*.bin"))
                    or any(snap.glob("model-*.safetensors"))
                )
                if (snap / "config.json").exists() and has_weights:
                    return str(snap)

        # Не нашли — вернём оригинальный id (пусть transformers сам разбирается)
        return model_id

    @staticmethod
    def _format_context(context) -> str:
        if isinstance(context, dict):
            parts = []
            for k, v in context.items():
                if v is not None:
                    parts.append(f"{k}: {str(v)[:400]}")
            return "\n".join(parts)
        return str(context)

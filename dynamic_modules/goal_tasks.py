import os
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


class GoalTasks:
    """
    Менеджер задач и прогресса для целей GoalManager.
    Работает только в пределах рабочей директории.
    """

    def __init__(self, base_dir: Union[str, Path] = "goals_data") -> None:
        # Базовая директория для хранения данных по целям
        self.base_dir = self._safe_rel_path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._goals_index_path = self.base_dir / "goals_index.json"
        self._ensure_index_file()

    # ------------------------- ПУБЛИЧНЫЙ API -------------------------

    def list_goals(self) -> List[Dict[str, Any]]:
        """Вернуть список целей с краткой информацией."""
        index = self._load_index()
        return list(index.values())

    def create_goal(
        self,
        goal_id: str,
        title: str,
        description: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Создать новую цель и вернуть её описание."""
        goal_id = self._normalize_id(goal_id)
        index = self._load_index()
        if goal_id in index:
            raise ValueError(f"Goal '{goal_id}' already exists")

        goal_record = {
            "id": goal_id,
            "title": title,
            "description": description,
            "metadata": metadata or {},
            "created_at": self._now_iso(),
            "updated_at": self._now_iso(),
            "status": "active",
        }
        index[goal_id] = goal_record
        self._save_index(index)

        goal_dir = self._goal_dir(goal_id)
        goal_dir.mkdir(parents=True, exist_ok=True)
        self._save_goal_state(goal_id, {"tasks": [], "history": []})
        return goal_record

    def get_goal(self, goal_id: str) -> Optional[Dict[str, Any]]:
        """Получить описание цели по id."""
        goal_id = self._normalize_id(goal_id)
        index = self._load_index()
        return index.get(goal_id)

    def update_goal_metadata(
        self, goal_id: str, metadata: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Обновить metadata цели и вернуть обновлённую запись."""
        goal_id = self._normalize_id(goal_id)
        index = self._load_index()
        if goal_id not in index:
            raise ValueError(f"Goal '{goal_id}' not found")

        current_meta = index[goal_id].get("metadata") or {}
        current_meta.update(metadata)
        index[goal_id]["metadata"] = current_meta
        index[goal_id]["updated_at"] = self._now_iso()
        self._save_index(index)
        return index[goal_id]

    def archive_goal(self, goal_id: str) -> Dict[str, Any]:
        """Перевести цель в статус archived."""
        return self._set_goal_status(goal_id, "archived")

    def activate_goal(self, goal_id: str) -> Dict[str, Any]:
        """Перевести цель в статус active."""
        return self._set_goal_status(goal_id, "active")

    def delete_goal(self, goal_id: str) -> bool:
        """Удалить цель и все связанные файлы."""
        goal_id = self._normalize_id(goal_id)
        index = self._load_index()
        if goal_id not in index:
            return False

        del index[goal_id]
        self._save_index(index)

        goal_dir = self._goal_dir(goal_id)
        if goal_dir.exists() and goal_dir.is_dir():
            for root, dirs, files in os.walk(goal_dir, topdown=False):
                root_path = Path(root)
                for name in files:
                    file_path = root_path / name
                    if file_path.is_file():
                        file_path.unlink()
                for name in dirs:
                    dir_path = root_path / name
                    if dir_path.is_dir():
                        dir_path.rmdir()
            goal_dir.rmdir()
        return True

    def list_tasks(self, goal_id: str) -> List[Dict[str, Any]]:
        """Вернуть список задач для цели."""
        state = self._load_goal_state(self._normalize_id(goal_id))
        return state.get("tasks", [])

    def add_task(
        self,
        goal_id: str,
        title: str,
        description: str = "",
        status: str = "pending",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Добавить задачу к цели и вернуть её описание."""
        goal_id = self._normalize_id(goal_id)
        self._ensure_goal_exists(goal_id)

        state = self._load_goal_state(goal_id)
        tasks = state.get("tasks", [])
        task_id = self._next_task_id(tasks)
        task = {
            "id": task_id,
            "title": title,
            "description": description,
            "status": status,
            "metadata": metadata or {},
            "created_at": self._now_iso(),
            "updated_at": self._now_iso(),
        }
        tasks.append(task)
        state["tasks"] = tasks
        self._append_history(
            state,
            {
                "type": "task_added",
                "task_id": task_id,
                "timestamp": self._now_iso(),
                "title": title,
            },
        )
        self._save_goal_state(goal_id, state)
        self._touch_goal_updated(goal_id)
        return task

    def update_task(
        self,
        goal_id: str,
        task_id: int,
        title: Optional[str] = None,
        description: Optional[str] = None,
        status: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Обновить свойства задачи и вернуть задачу."""
        goal_id = self._normalize_id(goal_id)
        self._ensure_goal_exists(goal_id)

        state = self._load_goal_state(goal_id)
        tasks = state.get("tasks", [])
        task = self._find_task(tasks, task_id)
        if task is None:
            raise ValueError(f"Task {task_id} not found for goal '{goal_id}'")

        changes: Dict[str, Any] = {}
        if title is not None and title != task.get("title"):
            task["title"] = title
            changes["title"] = title
        if description is not None and description != task.get("description"):
            task["description"] = description
            changes["description"] = description
        if status is not None and status != task.get("status"):
            task["status"] = status
            changes["status"] = status
        if metadata:
            meta = task.get("metadata") or {}
            meta.update(metadata)
            task["metadata"] = meta
            changes["metadata"] = metadata

        task["updated_at"] = self._now_iso()

        if changes:
            self._append_history(
                state,
                {
                    "type": "task_updated",
                    "task_id": task_id,
                    "timestamp": self._now_iso(),
                    "changes": changes,
                },
            )
            self._save_goal_state(goal_id, state)
            self._touch_goal_updated(goal_id)
        return task

    def delete_task(self, goal_id: str, task_id: int) -> bool:
        """Удалить задачу у цели."""
        goal_id = self._normalize_id(goal_id)
        self._ensure_goal_exists(goal_id)

        state = self._load_goal_state(goal_id)
        tasks = state.get("tasks", [])
        new_tasks: List[Dict[str, Any]] = []
        removed = False
        for t in tasks:
            if t.get("id") == task_id:
                removed = True
            else:
                new_tasks.append(t)
        if not removed:
            return False

        state["tasks"] = new_tasks
        self._append_history(
            state,
            {
                "type": "task_deleted",
                "task_id": task_id,
                "timestamp": self._now_iso(),
            },
        )
        self._save_goal_state(goal_id, state)
        self._touch_goal_updated(goal_id)
        return True

    def get_goal_progress(self, goal_id: str) -> Dict[str, Any]:
        """Вернуть агрегированный прогресс по цели."""
        goal_id = self._normalize_id(goal_id)
        self._ensure_goal_exists(goal_id)

        state = self._load_goal_state(goal_id)
        tasks = state.get("tasks", [])

        total = len(tasks)
        done = sum(1 for t in tasks if t.get("status") == "done")
        in_progress = sum(1 for t in tasks if t.get("status") == "in_progress")
        pending = sum(1 for t in tasks if t.get("status") == "pending")
        other = total - (done + in_progress + pending)

        percent = 0.0
        if total > 0:
            percent = round((done / float(total)) * 100.0, 2)

        return {
            "goal_id": goal_id,
            "total_tasks": total,
            "done": done,
            "in_progress": in_progress,
            "pending": pending,
            "other": other,
            "percent_done": percent,
        }

    def get_goal_history(self, goal_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Вернуть историю изменений цели и задач."""
        goal_id = self._normalize_id(goal_id)
        self._ensure_goal_exists(goal_id)
        state = self._load_goal_state(goal_id)
        history = state.get("history", [])
        if limit is not None and limit >= 0:
            return history[-limit:]
        return history

    # ---------------------- ВНУТРЕННИЕ МЕТОДЫ -----------------------

    def _safe_rel_path(self, p: Union[str, Path]) -> Path:
        """Нормализует путь и гарантирует отсутствие выхода за рабочую директорию."""
        base = Path(".").resolve()
        target = (base / str(p)).resolve()
        if not str(target).startswith(str(base)):
            raise ValueError("Path traversal is not allowed")
        return target

    def _normalize_id(self, value: str) -> str:
        """Нормализует идентификатор (убирает пробелы, запрещённые символы)."""
        clean = value.strip()
        for ch in ("/", "\\", ":", ".."):
            clean = clean.replace(ch, "_")
        if not clean:
            raise ValueError("Empty id is not allowed")
        return clean

    def _goal_dir(self, goal_id: str) -> Path:
        """Путь к директории цели."""
        safe_id = self._normalize_id(goal_id)
        return self.base_dir / safe_id

    def _goal_state_path(self, goal_id: str) -> Path:
        """Путь к JSON-файлу состояния цели."""
        return self._goal_dir(goal_id) / "state.json"

    def _ensure_index_file(self) -> None:
        """Создаёт индекс целей при отсутствии."""
        if not self._goals_index_path.exists():
            self._save_index({})

    def _load_index(self) -> Dict[str, Dict[str, Any]]:
        """Загружает индекс целей из файла."""
        try:
            with self._goals_index_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            logger.warning("Failed to read goals_index.json, recreating index")
        self._save_index({})
        return {}

    def _save_index(self, data: Dict[str, Dict[str, Any]]) -> None:
        """Сохраняет индекс целей в файл."""
        tmp_path = self._goals_index_path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp_path.replace(self._goals_index_path)

    def _load_goal_state(self, goal_id: str) -> Dict[str, Any]:
        """Загружает состояние цели."""
        path = self._goal_state_path(goal_id)
        if not path.exists():
            return {"tasks": [], "history": []}
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                if "tasks" not in data:
                    data["tasks"] = []
                if "history" not in data:
                    data["history"] = []
                return data
        except Exception:
            logger.warning("Failed to read state for goal '%s', using empty state", goal_id)
        return {"tasks": [], "history": []}

    def _save_goal_state(self, goal_id: str, state: Dict[str, Any]) -> None:
        """Сохраняет состояние цели."""
        path = self._goal_state_path(goal_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        tmp_path.replace(path)

    def _ensure_goal_exists(self, goal_id: str) -> None:
        """Проверяет наличие цели в индексе."""
        index = self._load_index()
        if goal_id not in index:
            raise ValueError(f"Goal '{goal_id}' not found")

    def _set_goal_status(self, goal_id: str, status: str) -> Dict[str, Any]:
        """Устанавливает статус цели."""
        goal_id = self._normalize_id(goal_id)
        index = self._load_index()
        if goal_id not in index:
            raise ValueError(f"Goal '{goal_id}' not found")
        index[goal_id]["status"] = status
        index[goal_id]["updated_at"] = self._now_iso()
        self._save_index(index)
        return index[goal_id]

    def _append_history(self, state: Dict[str, Any], event: Dict[str, Any]) -> None:
        """Добавляет событие в историю состояния."""
        history = state.get("history")
        if not isinstance(history, list):
            history = []
        history.append(event)
        state["history"] = history

    def _next_task_id(self, tasks: List[Dict[str, Any]]) -> int:
        """Возвращает следующий идентификатор задачи."""
        max_id = 0
        for t in tasks:
            try:
                tid = int(t.get("id", 0))
                if tid > max_id:
                    max_id = tid
            except (TypeError, ValueError):
                continue
        return max_id + 1

    def _find_task(
        self, tasks: List[Dict[str, Any]], task_id: int
    ) -> Optional[Dict[str, Any]]:
        """Находит задачу по id в списке."""
        for t in tasks:
            if t.get("id") == task_id:
                return t
        return None

    def _touch_goal_updated(self, goal_id: str) -> None:
        """Обновляет updated_at для цели в индексе."""
        index = self._load_index()
        if goal_id in index:
            index[goal_id]["updated_at"] = self._now_iso()
            self._save_index(index)

    def _now_iso(self) -> str:
        """Текущее время в ISO-формате без микросекунд."""
        from datetime import datetime

        return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
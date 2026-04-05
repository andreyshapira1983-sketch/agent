import os
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


class GoalManagerTasks:
    """
    Менеджер целей с файловым хранилищем.
    """

    def __init__(self, storage_path: str = "data/goals.json") -> None:
        """Инициализация менеджера целей."""
        self._storage_path = self._sanitize_relative_path(storage_path)
        self._storage_file = Path(self._storage_path)
        self._storage_file.parent.mkdir(parents=True, exist_ok=True)
        if not self._storage_file.exists():
            self._write_goals([])

    # ----------------------------------------------------------------------
    # Публичные методы, используемые агентом
    # ----------------------------------------------------------------------
    def add_goal(
        self,
        title: str,
        description: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        status: str = "pending",
    ) -> Dict[str, Any]:
        """Добавить одну цель и вернуть её полное описание."""
        goals = self._read_goals()
        next_id = self._generate_next_id(goals)
        goal: Dict[str, Any] = {
            "id": next_id,
            "title": str(title),
            "description": str(description) if description is not None else "",
            "status": str(status),
        }
        if metadata:
            safe_meta = self._safe_copy_jsonable(metadata)
            goal["metadata"] = safe_meta
        goals.append(goal)
        self._write_goals(goals)
        return goal

    def add_goals_batch(
        self,
        goals_data: List[Dict[str, Any]],
        default_status: str = "pending",
    ) -> List[Dict[str, Any]]:
        """Пакетное добавление целей, возвращает список добавленных целей."""
        goals = self._read_goals()
        next_id = self._generate_next_id(goals)
        created: List[Dict[str, Any]] = []

        for item in goals_data:
            title = str(item.get("title", "")).strip()
            if not title:
                # пропускаем записи без заголовка, чтобы не засорять хранилище
                continue
            description = str(item.get("description", "") or "")
            status = str(item.get("status", default_status))
            metadata_raw = item.get("metadata")

            goal: Dict[str, Any] = {
                "id": next_id,
                "title": title,
                "description": description,
                "status": status,
            }
            if metadata_raw is not None:
                goal["metadata"] = self._safe_copy_jsonable(metadata_raw)

            goals.append(goal)
            created.append(goal)
            next_id += 1

        if created:
            self._write_goals(goals)
        return created

    def list_goals(
        self,
        status: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Вернуть список целей с опциональной фильтрацией по статусу и лимиту."""
        goals = self._read_goals()
        if status is not None:
            status_str = str(status)
            goals = [g for g in goals if str(g.get("status")) == status_str]
        if isinstance(limit, int) and limit >= 0:
            goals = goals[:limit]
        return goals

    def update_goal_status(
        self,
        goal_id: Union[int, str],
        new_status: str,
    ) -> Optional[Dict[str, Any]]:
        """Обновить статус цели по ID, вернуть обновлённую цель или None."""
        goals = self._read_goals()
        changed = None
        goal_id_int: Optional[int] = None
        try:
            goal_id_int = int(goal_id)
        except (TypeError, ValueError):
            return None

        for goal in goals:
            if int(goal.get("id", -1)) == goal_id_int:
                goal["status"] = str(new_status)
                changed = goal
                break

        if changed is not None:
            self._write_goals(goals)
        return changed

    def get_goal(self, goal_id: Union[int, str]) -> Optional[Dict[str, Any]]:
        """Вернуть цель по ID или None, если не найдена."""
        goals = self._read_goals()
        try:
            goal_id_int = int(goal_id)
        except (TypeError, ValueError):
            return None

        for goal in goals:
            if int(goal.get("id", -1)) == goal_id_int:
                return goal
        return None

    def remove_goal(self, goal_id: Union[int, str]) -> bool:
        """Удалить цель по ID, вернуть True если была удалена."""
        goals = self._read_goals()
        try:
            goal_id_int = int(goal_id)
        except (TypeError, ValueError):
            return False

        new_goals: List[Dict[str, Any]] = []
        removed = False
        for goal in goals:
            if int(goal.get("id", -1)) == goal_id_int:
                removed = True
                continue
            new_goals.append(goal)

        if removed:
            self._write_goals(new_goals)
        return removed

    # ----------------------------------------------------------------------
    # Вспомогательные методы (работа с файловой системой и безопасностью)
    # ----------------------------------------------------------------------
    def _sanitize_relative_path(self, path: str) -> str:
        """Очистить путь, гарантируя относительность и отсутствие traversal."""
        raw = str(path).replace("\\", "/").strip()
        if not raw:
            raw = "data/goals.json"
        # убираем ведущие слэши, чтобы путь был точно относительным
        while raw.startswith("/"):
            raw = raw[1:]
        # нормализуем и запрещаем выход за рабочую директорию
        normalized = os.path.normpath(raw)
        if normalized.startswith(".."):
            # если путь указывает вне рабочей директории — складываем в data/
            file_name = os.path.basename(normalized)
            if not file_name:
                file_name = "goals.json"
            normalized = os.path.normpath(os.path.join("data", file_name))
        return normalized

    def _read_goals(self) -> List[Dict[str, Any]]:
        """Прочитать список целей из файла."""
        if not self._storage_file.exists():
            return []
        try:
            with self._storage_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(data, list):
            return []
        result: List[Dict[str, Any]] = []
        for item in data:
            if isinstance(item, dict):
                result.append(item)
        return result

    def _write_goals(self, goals: List[Dict[str, Any]]) -> None:
        """Сохранить список целей в файл."""
        safe_list: List[Dict[str, Any]] = []
        for goal in goals:
            if not isinstance(goal, dict):
                continue
            safe_goal: Dict[str, Any] = {}
            for k, v in goal.items():
                if isinstance(k, str):
                    safe_goal[k] = self._safe_copy_jsonable(v)
            if safe_goal:
                safe_list.append(safe_goal)
        tmp_path = self._storage_file.with_suffix(self._storage_file.suffix + ".tmp")
        try:
            with tmp_path.open("w", encoding="utf-8") as f:
                json.dump(safe_list, f, ensure_ascii=False, indent=2)
            tmp_path.replace(self._storage_file)
        except OSError:
            # при ошибке записи оставляем старый файл без изменений
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    # если даже удалить временный файл не удаётся, молча игнорируем
                    return

    def _generate_next_id(self, goals: List[Dict[str, Any]]) -> int:
        """Сгенерировать следующий числовой ID."""
        max_id = 0
        for goal in goals:
            try:
                gid = int(goal.get("id", 0))
            except (TypeError, ValueError):
                continue
            if gid > max_id:
                max_id = gid
        return max_id + 1

    def _safe_copy_jsonable(self, value: Any) -> Any:
        """Создать копию значения, пригодную для JSON."""
        # Примитивные типы
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        # Списки и кортежи
        if isinstance(value, (list, tuple)):
            return [self._safe_copy_jsonable(v) for v in value]
        # Словари
        if isinstance(value, dict):
            result: Dict[str, Any] = {}
            for k, v in value.items():
                key_str = str(k)
                result[key_str] = self._safe_copy_jsonable(v)
            return result
        # Остальные объекты сериализуем в строку
        try:
            return json.loads(json.dumps(value))
        except (TypeError, OverflowError):
            return str(value)
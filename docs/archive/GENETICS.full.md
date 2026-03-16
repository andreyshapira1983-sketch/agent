# Генетика агента — как это устроено у тебя

Андрей, вот как твоя «генетическая» схема реализована в проекте: что уже есть и как оно соотносится с биологической метафорой.

---

## 🧬 Перенос биологии на агента

| В биологии | У агента в проекте |
|------------|--------------------|
| **Гены** | Модули, системы, инструменты (описание в self_model) |
| **Геном** | `config/self_model.json` — полное описание структуры |
| **Видовой геном** | `config/capabilities_reference.json` — всё, что в принципе возможно |
| **Мутации** | Добавление/удаление модулей: `generate_module_skeleton`, `write_file`, `update_self_model` |
| **Отбор** | Метрики (calls, errors, successes), feedback (rating), `get_feedback_summary`, `run_self_repair` |
| **Наследование** | Versioning (снимки конфига), `create_from_template` для новых агентов, `rollback_config` |

---

## ✔ Что уже сделано (все 8 пунктов)

1. **Self-Model (геном агента)**  
   `config/self_model.json`: системы, модули, инструменты, связи, флаги `missing`. Агент читает через инструмент или код.

2. **Capabilities Reference (эталонный геном)**  
   `config/capabilities_reference.json`: все возможные системы/модули, уровни L1–L6. Сравнение с self_model даёт разрывы.

3. **Self-Analyzer (генетический анализ)**  
   `analyze_self_model`: читает оба файла, сравнивает, возвращает что реализовано / чего нет / что улучшить. Модуль: `src/reflection/self_model_analyzer.py`.

4. **Self-Improvement Planner (планировщик эволюции)**  
   `get_improvement_plan`: по результатам анализа строит приоритизированный план улучшений. Модуль: `src/reflection/self_improvement_planner.py`.

5. **Module Generator (механизм мутаций)**  
   `generate_module_skeleton` + `write_file` + `update_self_model`: создание новых модулей из шаблонов и обновление self_model.

6. **Safety Layer (контроль мутаций)**  
   Ограничения путей, `get_audit_log`, `apply_patch_with_approval` (патч в pending, человек применяет), `rollback_config`.

7. **Metrics (механизм отбора)**  
   `get_metrics` (calls, errors, successes, duration, recent_requests), `get_feedback_summary`, `run_self_repair` (тесты как критерий «работает/не работает»).

8. **Versioning (наследование)**  
   Снимки конфига при сохранении, `rollback_config(version_id)`, бэкапы в `config/backups/`. Для «потомков» — `create_from_template` и копирование/адаптация self_model.

---

## Цикл эволюции (мутация → отбор → наследование)

1. **Мутация:** агент вызывает `analyze_self_model` → `get_improvement_plan` → выбирает улучшение → `generate_module_skeleton` + `write_file` → `run_pytest` → при успехе `update_self_model`.
2. **Отбор:** по метрикам и feedback видно, что модуль полезен (меньше ошибок, лучше ответы) или бесполезен; при бесполезности можно предложить удалить модуль и обновить self_model (action `remove`).
3. **Наследование:** при создании нового агента — скопировать self_model и конфиг, доработать (например через шаблон и `create_from_template`), так что «потомок» получает текущую архитектуру + изменения.

---

## Где что лежит

- Геном и эталон: `config/self_model.json`, `config/capabilities_reference.json`.
- Анализатор и планировщик: `src/reflection/self_model_analyzer.py`, `src/reflection/self_improvement_planner.py`.
- Инструменты: `src/tools/impl/self_model_tools.py` (analyze_self_model, get_improvement_plan, update_self_model, generate_module_skeleton, apply_patch_with_approval).
- Подробнее по Self-Model и циклу: `config/SELF_MODEL.md`, по эволюции в целом — `EVOLUTION.md`.

Итого: агент уже может понимать свою архитектуру, видеть разрывы с эталоном, планировать улучшения, создавать новые модули и обновлять свой «геном», с контролем через метрики, аудит и откаты.

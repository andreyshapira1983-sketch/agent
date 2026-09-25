"""Цикл исполнителя Agent Market: опрос → назначение → агент → сдача → статус.

Порядок — «золотой путь» площадки (/skill, worker-lifecycle.md): опрос
`me/assignments`, `start` сразу по взятии, результат — файлом в хранилище
площадки (ссылка на свой хост у покупателя может не открыться), `submit` со
ссылкой и SHA-256, затем проверка статуса.

Защита до слова оператора (задача 2026-09-25):
* берётся ТОЛЬКО назначение, чья работа названа в `allowed_jobs`; остальные
  записываются в журнал как ждущие разрешения — без `start`, без ответа;
* ставок нет: у клиента нет для них метода.

Разбор строки назначения — по таблице worker-lifecycle §1 без памяти:
`deliverableUrl` пуст — первая работа; задан и `submittedAt` задан — доработка
по просьбе покупателя; задан и `submittedAt` пуст — пересдача по решению спора.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.market_client import MarketClient, MarketError


def classify(row: dict[str, Any]) -> str:
    a = row.get("assignment") or {}
    if a.get("status") != "in_progress":
        return str(a.get("status") or "unknown")
    if not a.get("deliverableUrl"):
        return "fresh" if not a.get("startedAt") else "in_flight"
    return "rework" if a.get("submittedAt") else "redo"


def brief(row: dict[str, Any]) -> str:
    """Задание для агента: бриф работы и последняя реплика покупателя."""
    job, msg = row.get("job") or {}, row.get("latestMessage") or {}
    parts = [f"Задание с площадки Agent Market: {job.get('title', '')}".strip(),
             str(job.get("description") or "").strip()]
    if job.get("tags"):
        parts.append("Метки: " + ", ".join(map(str, job["tags"])))
    if msg.get("senderSide") == "buyer" and msg.get("body"):
        parts.append("Последнее сообщение покупателя:\n" + str(msg["body"]))
    parts.append("Сделай работу и верни итоговый результат для покупателя в markdown.")
    return "\n\n".join(p for p in parts if p)


@dataclass
class CycleReport:
    assignment_id: str
    job_id: str
    kind: str
    steps: list[str] = field(default_factory=list)
    status_after: str | None = None
    deliverable_url: str | None = None
    error: str | None = None


class MarketWorker:
    def __init__(self, client: MarketClient, run_task: Callable[[str], str], *,
                 allowed_jobs: set[str], workdir: Path | str) -> None:
        self.client, self.run_task = client, run_task
        self.allowed_jobs, self.workdir = set(allowed_jobs), Path(workdir)

    def poll_once(self) -> list[CycleReport]:
        reports = []
        for row in self.client.my_assignments("in_progress"):
            a = row.get("assignment") or {}
            report = CycleReport(str(a.get("assignmentId")), str(a.get("jobId")), classify(row))
            if report.job_id not in self.allowed_jobs:
                report.steps.append("не взято: работа не в списке разрешённых — нужно слово оператора")
                self.client.log_event({"event": "assignment_skipped", "assignmentId": report.assignment_id,
                                       "jobId": report.job_id, "kind": report.kind})
                reports.append(report)
                continue
            if report.kind in ("fresh", "rework", "redo"):
                self._work(row, report)
            reports.append(report)
        return reports

    def _work(self, row: dict[str, Any], report: CycleReport) -> None:
        aid = report.assignment_id
        try:
            if report.kind == "fresh":
                self.client.start(aid)
                report.steps.append("start")
            answer = (self.run_task(brief(row)) or "").strip()
            if not answer:
                report.error = "агент не дал ответа — сдавать нечего"
                return
            report.steps.append(f"агент ответил ({len(answer)} знаков)")
            data = answer.encode("utf-8")
            local = self.workdir / aid / "deliverable.md"
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(data)
            url = self.client.upload_file(f"deliverable-{aid[:8]}.md", data)
            report.steps.append("файл загружен на площадку")
            self.client.submit(aid, url, hashlib.sha256(data).hexdigest())
            report.steps.append("submit")
            report.deliverable_url = url
            found = self.client.find_assignment(aid)
            report.status_after = ((found or {}).get("assignment") or {}).get("status")
            report.steps.append(f"статус после сдачи: {report.status_after}")
        except MarketError as err:
            report.error = f"{err.status} {err.code}: {err.message}"

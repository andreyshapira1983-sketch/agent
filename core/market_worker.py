"""Цикл исполнителя Agent Market: опрос → назначение → агент → сдача → статус.

Порядок — «золотой путь» площадки (/skill, flows/worker-lifecycle.md,
flows/messaging.md): опрос `me/assignments`, `start` сразу по взятии, результат
— файлом в хранилище площадки, `submit` со ссылкой и SHA-256, проверка статуса.

Разбор строки — таблица worker-lifecycle §1, без памяти о прошлом:
`deliverableUrl` пуст — первая работа (`startedAt` задан — начатая, но не
сданная); задан при `submittedAt` — доработка по просьбе покупателя; задан при
пустом `submittedAt` — пересдача по решению спора; `submitted` со свежим
словом покупателя — вопрос о сданной работе (ответить, не пересдавая).

«Свежее слово» помнится в data/market/state.json по `createdAt` последнего
сообщения покупателя: одно и то же сообщение обрабатывается один раз, и после
перезапуска тоже.

Вложения: файлы сообщения (`latestMessage.attachments`) и файлы самой работы
(`GET /v1/jobs/{id}/attachments`) скачиваются токеном с адреса площадки,
сверяются по SHA-256 и передаются агенту путями и текстом.

Срок (SLA): до `start` покупатель может отменить в любой миг, после — только
когда `slaDeadlineAt` прошёл без сдачи. Поэтому работа идёт от самого близкого
срока, а вышедший или близкий срок пишется в журнал и в отчёт.

Защита до слова оператора: берётся только работа из `allowed_jobs`, прочие —
в журнал как ждущие разрешения; ставок нет вовсе (у клиента нет метода).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.market_client import MarketClient, MarketError
from core.market_ledger import FILE_NAME as LEDGER_FILE
from core.market_ledger import MarketLedger

#: Меньше этого до срока — предупреждение: работа агента на заказ идёт минуты
#: (замер 2026-09-25 на поддельной площадке: 291 с на объяснение в 5–8 фраз).
SLA_WARN_SECONDS = 15 * 60
#: Больше — вложение не скачивается целиком (площадка допускает до 100 МиБ).
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
_TEXT_PREVIEW_CHARS = 6000
_SAFE_NAME_RE = re.compile(r"[^\w.\-]+")


def classify(row: dict[str, Any]) -> str:
    a = row.get("assignment") or {}
    if a.get("status") != "in_progress":
        return str(a.get("status") or "unknown")
    if not a.get("deliverableUrl"):
        return "fresh" if not a.get("startedAt") else "in_flight"
    return "rework" if a.get("submittedAt") else "redo"


def sla_seconds_left(row: dict[str, Any], now: datetime | None = None) -> float | None:
    raw = (row.get("assignment") or {}).get("slaDeadlineAt")
    if not raw:
        return None
    try:
        deadline = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return (deadline - (now or datetime.now(timezone.utc))).total_seconds()


def brief(row: dict[str, Any], files: list[str] | None = None, thread: str = "") -> str:
    """Задание для агента: бриф работы, переписка, вложения."""
    job, msg = row.get("job") or {}, row.get("latestMessage") or {}
    parts = [f"Задание с площадки Agent Market: {job.get('title', '')}".strip(),
             str(job.get("description") or "").strip()]
    if job.get("tags"):
        parts.append("Метки: " + ", ".join(map(str, job["tags"])))
    if thread:
        parts.append("Переписка с покупателем (старые сверху):\n" + thread)
    elif msg.get("senderSide") == "buyer" and msg.get("body"):
        parts.append("Последнее сообщение покупателя:\n" + str(msg["body"]))
    if files:
        parts.append("Файлы покупателя:\n" + "\n\n".join(files))
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
    sla_left: float | None = None
    error: str | None = None


class MarketWorker:
    def __init__(self, client: MarketClient, run_task: Callable[[str], str], *,
                 allowed_jobs: set[str], workdir: Path | str,
                 cost_since: Callable[[datetime], float] | None = None) -> None:
        self.client, self.run_task = client, run_task
        self.allowed_jobs, self.workdir = set(allowed_jobs), Path(workdir)
        self.state_path = self.workdir / "state.json"
        # Учёт прибыли по заказу (core/market_ledger.py); cost_since — доллары
        # модели с момента (core/usd_spend.usd_since), без него — 0.
        self.ledger, self.cost_since = MarketLedger(self.workdir / LEDGER_FILE), cost_since

    def _run(self, row: dict[str, Any], prompt: str) -> str:
        started = datetime.now(timezone.utc)
        try:
            return (self.run_task(prompt) or "").strip()
        finally:
            spent = self.cost_since(started) if self.cost_since else None
            self.ledger.add_run(row, (datetime.now(timezone.utc) - started).total_seconds(), spent)

    # ── память о прочитанном ─────────────────────────────────────────────────
    def _state(self) -> dict[str, Any]:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _remember(self, aid: str, **fields: Any) -> None:
        state = self._state()
        state.setdefault(aid, {}).update(fields)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        # Через временный файл и замену: падение посреди записи оставляет старое
        # состояние, а не обрывок, из-за которого забылось бы всё прочитанное.
        tmp = self.state_path.with_name(self.state_path.name + ".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def _fresh_buyer_message(self, row: dict[str, Any]) -> dict | None:
        msg = row.get("latestMessage") or {}
        aid = str((row.get("assignment") or {}).get("assignmentId"))
        if msg.get("senderSide") != "buyer":
            return None
        return None if self._state().get(aid, {}).get("seen_message_at") == msg.get("createdAt") else msg

    # ── проход опроса ─────────────────────────────────────────────────────────
    def poll_once(self) -> list[CycleReport]:
        rows = self.client.my_assignments("in_progress") + self.client.my_assignments("submitted")
        for row in rows + self.client.my_assignments("accepted"):
            self.ledger.observe(row)
        # Самый близкий срок — первым: до него покупатель вправе отменить даром.
        rows.sort(key=lambda r: sla_seconds_left(r) if sla_seconds_left(r) is not None else float("inf"))
        reports = []
        for row in rows:
            a = row.get("assignment") or {}
            report = CycleReport(str(a.get("assignmentId")), str(a.get("jobId")), classify(row),
                                 sla_left=sla_seconds_left(row))
            if report.job_id not in self.allowed_jobs:
                report.steps.append("не взято: работа не в списке разрешённых — нужно слово оператора")
                self.client.log_event({"event": "assignment_skipped", "assignmentId": report.assignment_id,
                                       "jobId": report.job_id, "kind": report.kind})
                reports.append(report)
                continue
            self._note_sla(report)
            fresh_msg = self._fresh_buyer_message(row)
            try:
                # «in_flight» — начато и не сдано: так выглядит заказ после падения
                # посреди работы. Раньше без нового слова покупателя он пропускался,
                # и брошенный заказ ждал истечения срока.
                if report.kind in ("fresh", "in_flight", "rework", "redo"):
                    self._work(row, report, fresh_msg)
                elif report.kind == "submitted" and fresh_msg:
                    self._answer_question(row, report, fresh_msg)
                else:
                    report.steps.append("нового нет — ждём покупателя")
            except MarketError as err:
                report.error = f"{err.status} {err.code}: {err.message}"
            reports.append(report)
        return reports

    def _note_sla(self, report: CycleReport) -> None:
        left = report.sla_left
        if left is None:
            return
        if left <= 0:
            note = "срок (SLA) уже вышел — покупатель вправе отменить без оплаты"
        elif left < SLA_WARN_SECONDS:
            note = f"до срока (SLA) {int(left // 60)} мин — работа агента может не успеть"
        else:
            return
        report.steps.append(note)
        self.client.log_event({"event": "sla_warning", "assignmentId": report.assignment_id, "seconds_left": left})

    # ── вложения и переписка ─────────────────────────────────────────────────
    def _files(self, row: dict[str, Any], aid: str) -> list[str]:
        """Файлы покупателя: скачать, сверить SHA-256, вернуть описания для агента."""
        wanted = list((row.get("latestMessage") or {}).get("attachments") or [])
        job_id = str((row.get("assignment") or {}).get("jobId"))
        if classify(row) == "fresh":
            wanted += self.client.job_attachments(job_id)
        out = []
        for att in wanted:
            name = _SAFE_NAME_RE.sub("_", str(att.get("filename") or att.get("id") or "file"))[:120]
            if int(att.get("byteSize") or 0) > MAX_ATTACHMENT_BYTES:
                out.append(f"- {name}: {att.get('byteSize')} байт — больше предела, не скачан")
                continue
            data = self.client.download(str(att["downloadUrl"]))
            digest = hashlib.sha256(data).hexdigest()
            if att.get("sha256") and att["sha256"].lower() != digest:
                out.append(f"- {name}: SHA-256 не сошёлся с заявленным — файл отброшен")
                continue
            path = self.workdir / aid / "inbox" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            try:
                preview = "\n" + data.decode("utf-8")[:_TEXT_PREVIEW_CHARS]
            except UnicodeDecodeError:
                preview = " (двоичный файл)"
            out.append(f"- {name} ({len(data)} байт), сохранён: {path.as_posix()}{preview}")
        return out

    def _thread(self, row: dict[str, Any], aid: str, *, force: bool = False) -> str:
        """Полная переписка: когда встроенное сообщение обрезано, или когда просили
        (доработка: после нашего «беру в доработку» последним стоит наше слово, а
        замечание покупателя — только в переписке)."""
        if not force and not (row.get("latestMessage") or {}).get("truncated"):
            return ""
        msgs = reversed(self.client.messages(aid))
        return "\n".join(f"[{'агент' if m.get('senderAgentId') else 'покупатель'}] {m.get('body', '')}" for m in msgs)

    def _mark_seen(self, aid: str, msg: dict | None) -> None:
        if msg:
            self._remember(aid, seen_message_at=msg.get("createdAt"))

    def _answer_question(self, row: dict[str, Any], report: CycleReport, msg: dict) -> None:
        aid = report.assignment_id
        files = self._files(row, aid)
        prompt = (brief(row, files, self._thread(row, aid))
                  + "\n\nРабота уже сдана. Покупатель спрашивает о ней — ответь ему коротко и по делу, "
                    "не переделывая работу:\n" + str(msg.get("body") or ""))
        answer = self._run(row, prompt)
        if answer:
            self.client.post_message(aid, answer)
            report.steps.append("ответ покупателю в переписке")
        self._mark_seen(aid, msg)

    # ── работа и сдача ────────────────────────────────────────────────────────
    def _work(self, row: dict[str, Any], report: CycleReport, fresh_msg: dict | None) -> None:
        """Работа шагами с записью итога каждого шага: упавший посреди заказа
        исполнитель после перезапуска продолжает с последнего сделанного шага, а
        не делает сделанное заново (AWS Builders' Library, «Making retries safe
        with idempotent APIs»: итог побочного действия записан — повтор берёт
        записанное). Раунд — первая сдача или одна доработка: его ключ не
        меняется, пока раунд не сдан, и меняется со сдачей.
        """
        aid, a = report.assignment_id, row.get("assignment") or {}
        round_key = str(a.get("submittedAt") or a.get("deliverableUrl") or "first")
        done = self._state().get(aid, {})
        same_round = done.get("round") == round_key
        if report.kind == "fresh":
            self.client.start(aid)
            report.steps.append("start")
        if report.kind == "rework" and not (same_round and done.get("ack")):
            self.client.post_message(aid, "Получил замечания, беру в доработку.")
            self._remember(aid, round=round_key, ack=True, answer=False, url=None)
            done, same_round = self._state().get(aid, {}), True
            report.steps.append("замечания подтверждены в переписке")
        local = self.workdir / aid / "deliverable.md"
        if same_round and done.get("answer") and local.is_file():
            data = local.read_bytes()
            report.steps.append("ответ агента взят из сохранённого — продолжение после перезапуска")
        else:
            files = self._files(row, aid)
            if files:
                report.steps.append(f"файлов покупателя: {len(files)}")
            thread = self._thread(row, aid, force=report.kind in ("rework", "redo"))
            answer = self._run(row, brief(row, files, thread))
            if not answer:
                report.error = "агент не дал ответа — сдавать нечего"
                return
            report.steps.append(f"агент ответил ({len(answer)} знаков)")
            data = answer.encode("utf-8")
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(data)
            self._remember(aid, round=round_key, answer=True, url=None)
            done, same_round = self._state().get(aid, {}), True
        url = done.get("url") if same_round else None
        if url:
            report.steps.append("файл уже был загружен — повторно не грузится")
        else:
            url = self.client.upload_file(f"deliverable-{aid[:8]}.md", data)
            self._remember(aid, url=url)
            report.steps.append("файл загружен на площадку")
        # Прочитанное — ДО сдачи: работу поднимает вид заказа, а не слово, так что
        # после падения здесь раунд всё равно доделается; а упав после сдачи,
        # старое замечание не придёт потом как новый вопрос о сданной работе.
        self._mark_seen(aid, fresh_msg)
        self.client.submit(aid, url, hashlib.sha256(data).hexdigest())
        report.steps.append("submit")
        report.deliverable_url = url
        text = data.decode("utf-8", errors="replace")
        first_line = next((ln.strip(" #") for ln in text.splitlines() if ln.strip()), "")[:200]
        self.client.post_message(aid, f"Сдал результат: {first_line}")
        found = self.client.find_assignment(aid)
        report.status_after = ((found or {}).get("assignment") or {}).get("status")
        report.steps.append(f"статус после сдачи: {report.status_after}")

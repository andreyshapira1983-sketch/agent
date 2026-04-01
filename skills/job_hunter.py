# Job Hunter — автономный поиск вакансий на Upwork через RSS
# Каждые N циклов: ищет вакансии → оценивает через LLM → шлёт письмо в Telegram

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

# Профиль Андрея — единственное место где это хранится
ANDREY_PROFILE = (
    "Andrey's profile: "
    "Lives in Israel (25 years). "
    "Skills: AI automation, Python, data processing, document automation, "
    "Excel, Google Sheets, web scraping, Telegram bots, API integrations, "
    "LLM applications, chatbots, workflow automation. "
    "Remote only. "
    "Does NOT have: Apple devices, MacBook, Apple Numbers. "
    "Is NOT a native speaker of any language except Russian and Hebrew. "
    "Cannot do: voice recording, on-site work, physical presence, "
    "video/audio production, graphic design, language-specific native tasks, "
    "subtitle correction as native speaker, transcription as native speaker."
)

# RSS-ленты Upwork по ключевым словам навыков Андрея
UPWORK_RSS_FEEDS = [
    "https://www.upwork.com/ab/feed/jobs/rss?q=AI+automation+python&sort=recency&paging=0%3B10",
    "https://www.upwork.com/ab/feed/jobs/rss?q=python+automation+bot&sort=recency&paging=0%3B10",
    "https://www.upwork.com/ab/feed/jobs/rss?q=telegram+bot+python&sort=recency&paging=0%3B10",
    "https://www.upwork.com/ab/feed/jobs/rss?q=data+processing+python&sort=recency&paging=0%3B10",
    "https://www.upwork.com/ab/feed/jobs/rss?q=LLM+chatbot+automation&sort=recency&paging=0%3B10",
    "https://www.upwork.com/ab/feed/jobs/rss?q=web+scraping+python&sort=recency&paging=0%3B10",
    "https://www.upwork.com/ab/feed/jobs/rss?q=API+integration+python&sort=recency&paging=0%3B10",
]


@dataclass
class JobListing:
    title: str
    description: str
    url: str
    guid: str
    published: str = ""

    @property
    def uid(self) -> str:
        return hashlib.md5(self.guid.encode()).hexdigest()[:12]

    def short_desc(self) -> str:
        # Очищаем HTML-теги
        clean = re.sub(r'<[^>]+>', ' ', self.description)
        clean = re.sub(r'\s+', ' ', clean).strip()
        return clean[:3000]


class JobHunter:
    """
    Автономный охотник за вакансиями.
    Вызывается из autonomous_loop каждые N циклов.
    """

    def __init__(self, llm=None, telegram_bot=None, telegram_chat_id=None,
                 monitoring=None, persistent_brain=None):
        self.llm = llm
        self.telegram_bot = telegram_bot
        self.telegram_chat_id = telegram_chat_id
        self.monitoring = monitoring
        self.persistent_brain = persistent_brain

        self._seen_uids: set[str] = set()   # уже обработанные вакансии
        self._last_run: float = 0.0
        self._run_interval: float = 300.0   # каждые 5 минут минимум

        # Путь для персистентного хранения seen_uids между рестартами
        _brain_dir = (
            getattr(persistent_brain, 'data_dir', None)
            or os.path.join(os.path.abspath('.'), '.agent_memory')
        )
        self._seen_path = os.path.join(_brain_dir, 'job_hunter_seen.json')
        self._load_seen_uids()

    # ── Основной метод ────────────────────────────────────────────────────────

    def hunt(self) -> int:
        """
        Полный цикл поиска. Возвращает количество отправленных уведомлений.
        """
        now = time.time()
        if now - self._last_run < self._run_interval:
            return 0
        self._last_run = now

        jobs = self._fetch_all_jobs()
        if not jobs:
            return 0

        sent = 0
        skipped = 0
        for job in jobs:
            if job.uid in self._seen_uids:
                skipped += 1
                continue
            self._seen_uids.add(job.uid)

            fit, reason = self._evaluate_fit(job)
            if not fit:
                self._log(f"[job_hunter] ❌ пропущена: {job.title[:60]} — {reason}")
                continue

            cover = self._generate_cover_letter(job)
            self._notify(job, cover)
            self._log(f"[job_hunter] ✅ подходит: {job.title[:60]}")
            if self.persistent_brain:
                self.persistent_brain.record_evolution(
                    event="job_found",
                    details=f"Подходящая вакансия: {job.title[:100]} | {job.url[:100]}",
                )
            sent += 1
            time.sleep(1)   # небольшая пауза между API-вызовами

        # Сохраняем seen_uids на диск, чтобы не дублировать после рестарта
        self._save_seen_uids()

        if self.persistent_brain and (sent > 0 or skipped > 0):
            self.persistent_brain.record_evolution(
                event="job_hunt_cycle",
                details=(
                    f"Всего вакансий: {len(jobs)}, "
                    f"новых отправлено: {sent}, "
                    f"уже виденных пропущено: {skipped}."
                ),
            )
        return sent

    # ── Получение вакансий ────────────────────────────────────────────────────

    def _fetch_all_jobs(self) -> list[JobListing]:
        jobs: list[JobListing] = []
        seen_guids: set[str] = set()
        for feed_url in UPWORK_RSS_FEEDS:
            try:
                feed_jobs = self._fetch_rss(feed_url)
                for j in feed_jobs:
                    if j.guid not in seen_guids:
                        seen_guids.add(j.guid)
                        jobs.append(j)
            except Exception as e:
                self._log(f"[job_hunter] RSS ошибка {feed_url[:60]}: {e}")
        return jobs

    def _fetch_rss(self, url: str) -> list[JobListing]:
        import urllib.request
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; JobHunterBot/1.0)",
            "Accept": "application/rss+xml, application/xml, text/xml",
        }
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            xml_data = resp.read()

        root = ET.fromstring(xml_data)
        ns = {"content": "http://purl.org/rss/1.0/modules/content/"}
        items = root.findall(".//item")

        jobs = []
        for item in items:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            guid = (item.findtext("guid") or link).strip()
            pubdate = (item.findtext("pubDate") or "").strip()

            # Описание может быть в content:encoded или description
            desc = (
                item.findtext("content:encoded", namespaces=ns) or
                item.findtext("description") or ""
            ).strip()

            if title and (link or guid):
                jobs.append(JobListing(
                    title=title,
                    description=desc,
                    url=link,
                    guid=guid,
                    published=pubdate,
                ))
        return jobs

    # ── Оценка соответствия ───────────────────────────────────────────────────

    def _evaluate_fit(self, job: JobListing) -> tuple[bool, str]:
        """
        Возвращает (подходит: bool, причина: str).
        Использует LLM для универсальной оценки.
        """
        if not self.llm:
            return True, ""   # без LLM — пропускаем всех

        prompt = (
            f"{ANDREY_PROFILE}\n\n"
            f"Job title: {job.title}\n"
            f"Job description:\n{job.short_desc()}\n\n"
            "Carefully analyze if Andrey qualifies for this job. "
            "Key: if the job requires a native speaker of any language other than Russian or Hebrew, "
            "or requires physical presence, Apple hardware, or skills not in Andrey's profile — it's NO. "
            "Reply with exactly one of these two formats:\n"
            "FIT: yes\n"
            "FIT: no — <reason in Russian, max 10 words>"
        )
        try:
            result = self.llm.infer(
                prompt,
                system="You are a strict job matching assistant. Reply only in the format specified.",
            )
            fit_match = re.search(r'fit:\s*(yes|no)', (result or '').lower())
            fit_result = fit_match.group(1) if fit_match else 'no'
            if fit_result == 'no':
                reason_match = re.search(r'fit:\s*no\s*[—\-–:]\s*(.+)', result, re.IGNORECASE)
                reason = reason_match.group(1).strip() if reason_match else 'не соответствует'
                return False, reason
            return True, ""
        except Exception as e:
            self._log(f"[job_hunter] LLM fit-check ошибка: {e}")
            return False, f"LLM ошибка: {e}"

    # ── Генерация письма ──────────────────────────────────────────────────────

    def _generate_cover_letter(self, job: JobListing) -> str:
        if not self.llm:
            return "(LLM недоступен — письмо не сгенерировано)"
        try:
            prompt = f"Job title: {job.title}\n\nJob description:\n{job.short_desc()}"
            result = self.llm.infer(
                prompt,
                system=(
                    "You are Andrey, a freelancer from Israel (25 years there). "
                    "Expert in AI automation, Python, data processing, document automation, "
                    "Excel, Google Sheets, web scraping, Telegram bots, API integrations. "
                    "Write a short (5-7 sentences) Upwork cover letter in ENGLISH. "
                    "Show you understood the client's specific task. "
                    "Write confidently in first person. "
                    "Do NOT use template phrases like 'I am writing to apply'. "
                    "Do NOT mention any gaps or lacking skills. "
                    "Return ONLY the letter text, no headers or explanations."
                ),
            )
            return result or "(пустой ответ)"
        except Exception as e:
            return f"(ошибка генерации: {e})"

    # ── Отправка уведомления ──────────────────────────────────────────────────

    def _notify(self, job: JobListing, cover_letter: str):
        if not self.telegram_bot or not self.telegram_chat_id:
            return
        try:
            msg = (
                f"🎯 <b>Подходящая вакансия!</b>\n\n"
                f"<b>{job.title}</b>\n"
                f"🔗 {job.url}\n\n"
                f"📝 <b>Готовое письмо:</b>\n{cover_letter[:2500]}"
            )
            self.telegram_bot.send(self.telegram_chat_id, msg)
        except Exception as e:
            self._log(f"[job_hunter] Ошибка отправки: {e}")

    def _load_seen_uids(self):
        """Загружает список уже виденных вакансий с диска."""
        try:
            if os.path.exists(self._seen_path):
                with open(self._seen_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self._seen_uids = set(data.get('uids', []))
                self._log(f"[job_hunter] Загружено {len(self._seen_uids)} виденных вакансий.")
        except Exception as e:
            self._log(f"[job_hunter] Не удалось загрузить seen_uids: {e}")

    def _save_seen_uids(self):
        """Сохраняет список виденных вакансий на диск."""
        try:
            os.makedirs(os.path.dirname(self._seen_path), exist_ok=True)
            # Держим только последние 2000 UID — не даём файлу расти бесконечно
            uids_list = list(self._seen_uids)[-2000:]
            with open(self._seen_path, 'w', encoding='utf-8') as f:
                json.dump({'uids': uids_list}, f)
        except Exception as e:
            self._log(f"[job_hunter] Не удалось сохранить seen_uids: {e}")

    def _log(self, msg: str):
        if self.monitoring:
            self.monitoring.info(msg, source="job_hunter")
        else:
            print(msg)

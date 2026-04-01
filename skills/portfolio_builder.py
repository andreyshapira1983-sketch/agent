# Portfolio Builder — генерирует готовые записи для Upwork Portfolio
# Вызывается из cognitive_core при интенте 'portfolio'
# Все проекты основаны на реальных навыках Андрея.

from __future__ import annotations

# ── Реальные проекты Андрея ───────────────────────────────────────────────────
ANDREY_PORTFOLIO_PROJECTS = [
    {
        "title": "Autonomous AI Agent with LLM Integration & Telegram Alerts",
        "role": "Python Developer & AI Automation Engineer",
        "description": (
            "Built a 46-layer autonomous Python agent that continuously plans, executes "
            "code, and self-repairs errors without human input. Integrated OpenAI and "
            "Claude LLMs for reasoning and decision-making. The system scans Upwork RSS "
            "feeds, evaluates job fit against a freelancer profile, auto-generates tailored "
            "cover letters, and delivers them via real-time Telegram notifications. "
            "Persistent memory survives restarts; sandboxed code execution prevents "
            "unsafe operations. Reduced manual job search effort by 90%."
        ),
        "skills": ["Python", "AI Automation", "LLM Integration", "Telegram Bots", "API Integration"],
    },
    {
        "title": "Telegram Bot for Business Workflow Automation",
        "role": "Python Bot Developer",
        "description": (
            "Designed and built a Telegram bot that automates routine business workflows: "
            "collects data from users via inline menus, generates formatted reports, and "
            "posts summaries to shared channels on schedule. Integrated with Google Sheets "
            "for real-time data storage and Excel export. The bot replaced a manual daily "
            "reporting process, saving the client 2+ hours per day. Deployed on a VPS with "
            "automatic restart on failure."
        ),
        "skills": ["Python", "Telegram Bots", "Google Sheets API", "Workflow Automation", "Scheduling"],
    },
    {
        "title": "E-commerce Price Monitoring & Data Extraction Pipeline",
        "role": "Web Scraping & Data Engineer",
        "description": (
            "Built an automated web scraping pipeline to monitor competitor prices across "
            "3 e-commerce platforms. Used Python (requests, BeautifulSoup, Playwright) with "
            "rotating proxies and anti-bot bypassing. Data was cleaned, deduplicated, and "
            "stored in CSV/Excel format with daily email summaries. The client used insights "
            "to reprice 1,200+ SKUs dynamically, increasing margin by 12%."
        ),
        "skills": ["Python", "Web Scraping", "Data Processing", "Playwright", "Excel Automation"],
    },
    {
        "title": "Google Sheets & Excel Automation for Financial Reporting",
        "role": "Automation Developer",
        "description": (
            "Automated a multi-step monthly financial reporting process that previously "
            "required 6 hours of manual Excel work. Built Python scripts using openpyxl and "
            "gspread to pull data from multiple sources, apply business logic, format tables, "
            "and generate final reports. Integrated with Google Drive API for automatic file "
            "delivery. The solution reduced reporting time from 6 hours to under 10 minutes."
        ),
        "skills": ["Python", "Excel Automation", "Google Sheets API", "Data Processing", "Google Drive API"],
    },
    {
        "title": "LLM-Powered Document Processing & Data Extraction",
        "role": "AI & Python Developer",
        "description": (
            "Developed a document processing pipeline that uses GPT-4 to extract structured "
            "data from unstructured PDFs, invoices, and contracts. Built with Python, PyPDF2, "
            "and OpenAI API. The system classifies documents, extracts key fields "
            "(dates, amounts, parties), and exports results to structured Excel/JSON. "
            "Processed 500+ documents per hour with 95%+ field accuracy, which replaced "
            "a full-time manual data entry role."
        ),
        "skills": ["Python", "LLM Integration", "Document Automation", "OpenAI API", "Data Extraction"],
    },
    {
        "title": "REST API Integration & Data Synchronization Tool",
        "role": "Backend / Integration Developer",
        "description": (
            "Built a Python service that synchronizes data between two SaaS platforms "
            "lacking a native integration. The service polls REST APIs on both sides, "
            "detects changes, transforms data between schemas, and pushes updates "
            "bidirectionally. Implemented retry logic, error notifications via Telegram, "
            "and a simple web dashboard for status monitoring. Runs 24/7 on a VPS with "
            "zero manual intervention needed."
        ),
        "skills": ["Python", "API Integration", "REST APIs", "Data Synchronization", "Automation"],
    },
]


class PortfolioBuilder:
    """Генерирует готовые записи портфолио для Upwork."""

    def __init__(self, llm=None, telegram_bot=None, telegram_chat_id=None,
                 monitoring=None):
        self.llm = llm
        self.telegram_bot = telegram_bot
        self.telegram_chat_id = telegram_chat_id
        self.monitoring = monitoring

    def get_project(self, index: int) -> dict:
        """Возвращает проект по индексу (0-based)."""
        projects = ANDREY_PORTFOLIO_PROJECTS
        if 0 <= index < len(projects):
            return projects[index]
        return {}

    def format_for_telegram(self, project: dict, index: int) -> str:
        """Форматирует проект для отправки в Telegram."""
        skills_str = "\n".join(f"  • {s}" for s in project.get("skills", []))
        total = len(ANDREY_PORTFOLIO_PROJECTS)
        desc = project.get("description", "")
        # Обрезаем до 600 символов (лимит Upwork)
        if len(desc) > 600:
            desc = desc[:597] + "..."

        return (
            f"📁 <b>Портфолио проект {index + 1}/{total}</b>\n\n"
            f"📌 <b>Project title:</b>\n<code>{project.get('title', '')}</code>\n\n"
            f"👤 <b>Your role:</b>\n<code>{project.get('role', '')}</code>\n\n"
            f"📝 <b>Project description ({len(desc)}/600):</b>\n<code>{desc}</code>\n\n"
            f"🛠 <b>Skills and deliverables:</b>\n{skills_str}\n\n"
            f"<i>Скопируй каждое поле и вставь в форму Upwork.</i>"
        )

    def format_for_chat(self, project: dict, index: int) -> str:
        """Форматирует проект для веб-чата."""
        skills_str = ", ".join(project.get("skills", []))
        total = len(ANDREY_PORTFOLIO_PROJECTS)
        desc = project.get("description", "")
        if len(desc) > 600:
            desc = desc[:597] + "..."

        return (
            f"**Портфолио проект {index + 1}/{total}**\n\n"
            f"**Project title:**\n{project.get('title', '')}\n\n"
            f"**Your role:**\n{project.get('role', '')}\n\n"
            f"**Project description ({len(desc)}/600):**\n{desc}\n\n"
            f"**Skills and deliverables:**\n{skills_str}\n\n"
            f"Скажи «следующий проект» чтобы получить следующую запись "
            f"(всего проектов: {total})."
        )

    def send_all_to_telegram(self) -> int:
        """Отправляет все проекты в Telegram по одному."""
        if not self.telegram_bot or not self.telegram_chat_id:
            return 0
        sent = 0
        for i, project in enumerate(ANDREY_PORTFOLIO_PROJECTS):
            try:
                msg = self.format_for_telegram(project, i)
                self.telegram_bot.send(self.telegram_chat_id, msg)
                import time
                time.sleep(1)
                sent += 1
            except Exception as e:
                self._log(f"[portfolio] Ошибка отправки проекта {i}: {e}")
        return sent

    def send_one_to_telegram(self, index: int) -> bool:
        """Отправляет один проект в Telegram."""
        if not self.telegram_bot or not self.telegram_chat_id:
            return False
        project = self.get_project(index)
        if not project:
            return False
        try:
            msg = self.format_for_telegram(project, index)
            self.telegram_bot.send(self.telegram_chat_id, msg)
            return True
        except Exception as e:
            self._log(f"[portfolio] Ошибка: {e}")
            return False

    def _log(self, msg: str):
        if self.monitoring:
            self.monitoring.info(msg, source="portfolio_builder")
        else:
            print(msg)

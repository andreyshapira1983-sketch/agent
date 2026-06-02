"""Source Connector Registry.

This is the routing table above concrete source tools. A connector answers:

  - what kind of source it can read;
  - whether it is already wired, partial, or planned;
  - whether it needs auth/API keys;
  - what rough budget counters it consumes;
  - when the agent should prefer or avoid it.

The registry is deliberately read-only. It does not fetch anything by itself;
execution remains in controlled ingestion commands and tools.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Literal


ConnectorStatus = Literal["wired", "partial", "planned"]
CostClass = Literal["free_local", "low", "medium", "high"]


@dataclass(frozen=True)
class ConnectorCost:
    local_reads: int = 0
    web_searches: int = 0
    web_fetches: int = 0
    rss_fetches: int = 0
    api_calls: int = 0
    notes: str = ""

    @property
    def cost_class(self) -> CostClass:
        if self.web_searches == self.web_fetches == self.rss_fetches == self.api_calls == 0:
            return "free_local"
        total_network = self.web_searches + self.web_fetches + self.rss_fetches + self.api_calls
        if total_network <= 2:
            return "low"
        if total_network <= 8:
            return "medium"
        return "high"

    def estimate(self, units: int = 1) -> dict[str, Any]:
        units = max(1, int(units))
        return {
            "local_reads": self.local_reads * units,
            "web_searches": self.web_searches * units,
            "web_fetches": self.web_fetches * units,
            "rss_fetches": self.rss_fetches * units,
            "api_calls": self.api_calls * units,
            "cost_class": self.cost_class,
            "notes": self.notes,
        }

    def to_dict(self) -> dict[str, Any]:
        return self.estimate(1)


@dataclass(frozen=True)
class SourceConnector:
    id: str
    name: str
    status: ConnectorStatus
    source_types: tuple[str, ...]
    commands: tuple[str, ...]
    tools: tuple[str, ...]
    requires_auth: bool
    network: bool
    cost: ConnectorCost
    trust_baseline: float
    freshness: str
    use_when: tuple[str, ...]
    avoid_when: tuple[str, ...] = ()
    notes: str = ""
    keywords: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "source_types": list(self.source_types),
            "commands": list(self.commands),
            "tools": list(self.tools),
            "requires_auth": self.requires_auth,
            "network": self.network,
            "cost": self.cost.to_dict(),
            "trust_baseline": round(float(self.trust_baseline), 3),
            "freshness": self.freshness,
            "use_when": list(self.use_when),
            "avoid_when": list(self.avoid_when),
            "notes": self.notes,
            "keywords": list(self.keywords),
        }


@dataclass(frozen=True)
class ConnectorRecommendation:
    connector: SourceConnector
    score: int
    reasons: tuple[str, ...] = ()
    estimated_cost: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "connector": self.connector.to_dict(),
            "score": self.score,
            "reasons": list(self.reasons),
            "estimated_cost": dict(self.estimated_cost),
        }


@dataclass(frozen=True)
class ConnectorPlan:
    goal: str
    recommendations: tuple[ConnectorRecommendation, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "recommendations": [item.to_dict() for item in self.recommendations],
        }

    def user_summary(self) -> str:
        if not self.recommendations:
            return "=== connector plan ===\n(no connector recommendation)"
        lines = [f"=== connector plan ===", f"goal={self.goal!r}"]
        for item in self.recommendations:
            connector = item.connector
            cost = item.estimated_cost
            lines.append(
                f"  {connector.id} [{connector.status}] score={item.score} "
                f"cost={cost.get('cost_class')} auth={connector.requires_auth}"
            )
            lines.append(
                "    commands="
                + (", ".join(connector.commands) if connector.commands else "-")
            )
            for reason in item.reasons:
                lines.append(f"    reason: {reason}")
        return "\n".join(lines)


CONNECTORS: tuple[SourceConnector, ...] = (
    SourceConnector(
        id="local",
        name="Local Files / Logs / Project",
        status="wired",
        source_types=("file", "log", "test_result", "code_repository"),
        commands=(":ingest-source", ":ingest-project", ":learn-project", ":propose-repair"),
        tools=("file_read", "read_logs", "run_tests"),
        requires_auth=False,
        network=False,
        cost=ConnectorCost(local_reads=1, notes="No network/API spend; bounded by file count and test runs."),
        trust_baseline=0.90,
        freshness="current local state",
        use_when=(
            "project code, README, architecture, logs, tests, local books/files",
            "self-repair diagnostics and project health checks",
        ),
        avoid_when=("facts that must be checked against the live public web",),
        keywords=(
            "local", "file", "files", "project", "code", "repo", "logs", "tests",
            "readme", "architecture", "архитект", "код", "проект", "лог", "тест",
        ),
    ),
    SourceConnector(
        id="web",
        name="Curated Web Pages",
        status="wired",
        source_types=("web_page", "documentation", "article", "book", "forum"),
        commands=(":ingest-web",),
        tools=("web_search", "web_fetch"),
        requires_auth=False,
        network=True,
        cost=ConnectorCost(
            web_searches=1,
            web_fetches=1,
            notes="Roughly one search plus one fetch per selected source family/page.",
        ),
        trust_baseline=0.68,
        freshness="fresh when fetched; not realtime unless source supports it",
        use_when=(
            "general online research, official pages, Wikipedia/Wikibooks/books/docs",
            "when the source URL must have fetched_at and content_hash",
        ),
        avoid_when=("high-frequency market/weather realtime facts without a specialized tool",),
        keywords=(
            "web", "internet", "online", "официаль", "wikipedia", "wiki",
            "docs", "documentation", "article", "book", "сайт", "интернет",
        ),
    ),
    SourceConnector(
        id="rss",
        name="RSS / Atom Feeds",
        status="wired",
        source_types=("article", "release_note", "news", "changelog"),
        commands=(":ingest-rss", ":schedule-add"),
        tools=("rss_fetch",),
        requires_auth=False,
        network=True,
        cost=ConnectorCost(
            rss_fetches=1,
            notes="One cheap feed fetch gives many structured entries; good for schedules.",
        ),
        trust_baseline=0.68,
        freshness="feed timestamp plus fetched_at",
        use_when=(
            "monitoring releases, blogs, changelogs, news feeds, recurring checks",
            "when avoiding repeated web_search calls matters",
        ),
        avoid_when=("one-off deep research where no feed URL is known",),
        keywords=(
            "rss", "atom", "feed", "feeds", "release", "releases", "changelog",
            "blog", "monitor", "watch", "schedule", "лента", "релиз", "обновлен",
        ),
    ),
    SourceConnector(
        id="openalex",
        name="OpenAlex",
        status="planned",
        source_types=("article", "dataset", "citation_graph", "research_metadata"),
        commands=(),
        tools=(),
        requires_auth=False,
        network=True,
        cost=ConnectorCost(api_calls=1, notes="No-key REST API; future connector should cap pages/results."),
        trust_baseline=0.84,
        freshness="indexed scholarly metadata; not live realtime",
        use_when=(
            "scholarly discovery, authors, institutions, citations, paper metadata",
            "when arXiv alone is too narrow",
        ),
        avoid_when=("full-text reading when only metadata is available",),
        keywords=(
            "openalex", "paper", "papers", "research", "citation", "citations",
            "doi", "author", "institution", "науч", "статья", "цитирован",
        ),
    ),
    SourceConnector(
        id="arxiv",
        name="arXiv",
        status="partial",
        source_types=("article", "preprint", "research_metadata"),
        commands=(":ingest-web --sources arxiv",),
        tools=("web_search", "web_fetch"),
        requires_auth=False,
        network=True,
        cost=ConnectorCost(
            web_searches=1,
            web_fetches=1,
            notes="Currently wired through curated web search/fetch; dedicated API later.",
        ),
        trust_baseline=0.82,
        freshness="preprint metadata/page fetched_at",
        use_when=("AI/ML/CS/science preprints and recent research",),
        avoid_when=("settled facts requiring peer-reviewed final versions only",),
        keywords=("arxiv", "preprint", "paper", "research", "llm", "agent", "ai", "ml", "науч"),
    ),
    SourceConnector(
        id="github_public",
        name="Public GitHub / GitLab Pages",
        status="partial",
        source_types=("code_repository", "documentation", "release_note", "issue"),
        commands=(":ingest-web",),
        tools=("web_search", "web_fetch"),
        requires_auth=False,
        network=True,
        cost=ConnectorCost(
            web_searches=1,
            web_fetches=1,
            notes="Public pages work without auth but rate limits and HTML shape vary.",
        ),
        trust_baseline=0.78,
        freshness="page fetched_at; releases/issues may change",
        use_when=("public README, docs, releases, issues, examples, source references",),
        avoid_when=("private repositories or write operations without GitHub auth",),
        keywords=(
            "github", "gitlab", "repo", "repository", "readme", "release",
            "issue", "pull request", "код", "репозитор",
        ),
    ),
    SourceConnector(
        id="government_data",
        name="Government / Open Data Portals",
        status="planned",
        source_types=("dataset", "statistics", "official_site"),
        commands=(),
        tools=(),
        requires_auth=False,
        network=True,
        cost=ConnectorCost(
            api_calls=1,
            web_fetches=1,
            notes="Many portals are no-key but schemas vary; connector should validate datasets.",
        ),
        trust_baseline=0.86,
        freshness="dataset publication/update timestamp",
        use_when=("statistics, economy, demographics, transport, official public data",),
        avoid_when=("unofficial interpretation without source dataset",),
        keywords=(
            "government", "data.gov", "open data", "world bank", "un data",
            "oecd", "statistics", "dataset", "demographic", "эконом", "статист",
        ),
    ),
)


class SourceConnectorRegistry:
    def __init__(self, connectors: Iterable[SourceConnector] = CONNECTORS):
        self._connectors = tuple(connectors)
        self._by_id = {connector.id: connector for connector in self._connectors}

    def list(self, *, status: str | None = None) -> tuple[SourceConnector, ...]:
        if status in {None, "", "all"}:
            return self._connectors
        return tuple(connector for connector in self._connectors if connector.status == status)

    def get(self, connector_id: str) -> SourceConnector:
        try:
            return self._by_id[connector_id]
        except KeyError as exc:
            known = ", ".join(sorted(self._by_id))
            raise ValueError(f"unknown source connector: {connector_id}; known: {known}") from exc

    def payload(self) -> dict[str, Any]:
        return {
            "connectors": [connector.to_dict() for connector in self._connectors],
            "statuses": _counts(connector.status for connector in self._connectors),
            "wired": [connector.id for connector in self._connectors if connector.status == "wired"],
            "partial": [connector.id for connector in self._connectors if connector.status == "partial"],
            "planned": [connector.id for connector in self._connectors if connector.status == "planned"],
        }

    def plan(self, goal: str, *, limit: int = 4) -> ConnectorPlan:
        goal_text = (goal or "").strip()
        lowered = goal_text.casefold()
        recommendations: list[ConnectorRecommendation] = []
        for connector in self._connectors:
            score, reasons = self._score(connector, lowered)
            if score <= 0:
                continue
            recommendations.append(ConnectorRecommendation(
                connector=connector,
                score=score,
                reasons=tuple(reasons),
                estimated_cost=connector.cost.estimate(1),
            ))

        if not recommendations:
            for connector_id in ("local", "web", "rss"):
                connector = self.get(connector_id)
                recommendations.append(ConnectorRecommendation(
                    connector=connector,
                    score=1,
                    reasons=("default fallback: inspect local state, then web/RSS if needed",),
                    estimated_cost=connector.cost.estimate(1),
                ))

        status_weight = {"wired": 3, "partial": 2, "planned": 1}
        ordered = sorted(
            recommendations,
            key=lambda item: (
                item.score,
                status_weight[item.connector.status],
                item.connector.trust_baseline,
            ),
            reverse=True,
        )
        return ConnectorPlan(goal=goal_text, recommendations=tuple(ordered[: max(1, limit)]))

    def _score(self, connector: SourceConnector, goal: str) -> tuple[int, list[str]]:
        score = 0
        reasons: list[str] = []
        for keyword in connector.keywords:
            if keyword and keyword.casefold() in goal:
                score += 2
                reasons.append(f"goal mentions {keyword!r}")
        if connector.status == "wired":
            score += 1
            reasons.append("connector is already wired")
        if not connector.requires_auth:
            score += 1
            reasons.append("no auth/API key required")
        if not connector.network and any(term in goal for term in ("local", "project", "код", "проект")):
            score += 2
            reasons.append("local/project task prefers local sources")
        if connector.id == "rss" and any(term in goal for term in ("monitor", "watch", "schedule", "релиз", "обновлен")):
            score += 3
            reasons.append("recurring/monitoring task prefers RSS")
        if connector.id in {"openalex", "arxiv"} and any(term in goal for term in ("research", "paper", "науч", "статья")):
            score += 3
            reasons.append("research task benefits from scholarly connectors")
        if connector.id == "government_data" and any(term in goal for term in ("statistics", "dataset", "open data", "статист")):
            score += 3
            reasons.append("dataset/statistics task fits open-data connector")
        return score, reasons


def source_connector_payload() -> dict[str, Any]:
    return SourceConnectorRegistry().payload()


def plan_source_connectors(goal: str, *, limit: int = 4) -> ConnectorPlan:
    return SourceConnectorRegistry().plan(goal, limit=limit)


def _counts(values: Iterable[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        out[value] = out.get(value, 0) + 1
    return out

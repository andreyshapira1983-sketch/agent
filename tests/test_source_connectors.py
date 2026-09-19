"""Source Connector Registry tests."""

from __future__ import annotations

import pytest

from core.source_connectors import (
    SourceConnectorRegistry,
    plan_source_connectors,
    source_connector_payload,
)


def test_connector_registry_contains_required_connectors():
    registry = SourceConnectorRegistry()
    ids = {connector.id for connector in registry.list()}

    assert {
        "local",
        "web",
        "rss",
        "openalex",
        "arxiv",
        "github_public",
        "government_data",
    }.issubset(ids)
    assert registry.get("local").status == "wired"
    assert registry.get("openalex").requires_auth is False


def test_connector_payload_groups_statuses_and_costs():
    payload = source_connector_payload()

    assert payload["statuses"]["wired"] >= 3
    assert "local" in payload["wired"]
    assert "openalex" in payload["planned"]
    web = next(item for item in payload["connectors"] if item["id"] == "web")
    assert web["cost"]["web_searches"] == 1
    assert web["cost"]["web_fetches"] == 1


def test_connector_plan_prefers_rss_for_monitoring_releases():
    plan = plan_source_connectors("monitor Python releases and changelog", limit=3)
    ids = [item.connector.id for item in plan.recommendations]

    assert ids[0] == "rss"
    assert "github_public" in ids
    assert any("RSS" in reason or "monitoring" in reason for reason in plan.recommendations[0].reasons)


def test_connector_plan_prefers_scholarly_sources_for_research():
    plan = plan_source_connectors("find research papers and citations about autonomous agents", limit=4)
    ids = [item.connector.id for item in plan.recommendations]

    assert "openalex" in ids
    assert "arxiv" in ids


def test_connector_plan_prefers_local_for_project_logs():
    plan = plan_source_connectors("check local project logs and tests", limit=2)

    assert plan.recommendations[0].connector.id == "local"
    assert plan.recommendations[0].estimated_cost["cost_class"] == "free_local"


def test_connector_plan_recommends_government_data_for_statistics():
    plan = plan_source_connectors("official open data statistics dataset about transport", limit=3)
    ids = [item.connector.id for item in plan.recommendations]

    assert "government_data" in ids


def test_unknown_connector_raises_helpful_error():
    with pytest.raises(ValueError, match="unknown source connector"):
        SourceConnectorRegistry().get("nope")

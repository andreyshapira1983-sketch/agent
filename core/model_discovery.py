"""Live Model Discovery + Provider Catalog diff — read-only / dry-run (TD-011/012).

This module is deliberately conservative. It exposes:

* :func:`build_discovery_audit` — a purely LOCAL, no-network snapshot of provider
  discovery readiness (which providers are supported, have a live fetcher, and
  have credentials configured) plus what the current on-disk catalog knows. It
  never touches the network and never writes anything. This backs the
  ``:model-discovery-audit`` command.

* :func:`build_discovery_report` — a DRY-RUN discovery: it queries the model
  lists of the providers that can be queried, classifies them, and diffs the
  result against the current ``config/model_catalog.json``. It performs real
  provider metadata calls (non-inference, but still network) ONLY for providers
  that are supported, have a fetcher, and have credentials. It NEVER writes the
  catalog. This backs ``:provider-catalog-refresh --dry-run``.

Hard limits (by design, enforced here):
  - no catalog write (discovery is read-only; refresh/write stays in
    :func:`core.model_catalog.refresh_catalog` / the ``:refresh-models`` command);
  - no registry overwrite;
  - no automatic model switching;
  - no self-update;
  - no LLM inference calls;
  - no secrets in any output (only env-var NAMES and booleans are surfaced).

Querying a provider's model list is metadata-only / non-inference, but it is
still an external provider call and is only ever made from the explicit dry-run
path, never from the audit path or a normal run.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import core.model_catalog as mc
from core.model_router import _DEFAULT_PROVIDER_ENV, SUPPORTED_PROVIDERS

# Provider discovery status values (one per provider, non-overlapping).
STATUS_QUERIED = "queried"                       # supported + fetcher + creds -> queried live
STATUS_SKIPPED_NO_CREDS = "skipped_no_creds"     # supported + fetcher, but credentials missing
STATUS_UNSUPPORTED_NO_FETCHER = "unsupported_no_fetcher"  # supported provider, no live fetcher
STATUS_UNSUPPORTED_PROVIDER = "unsupported_provider"      # provider not supported by adapter
STATUS_SKIPPED_BY_ARG = "skipped_by_arg"         # excluded by an explicit provider filter


def _provider_env_names(provider: str) -> tuple[str, ...]:
    """Required env-var NAMES for a provider (never their values)."""
    return tuple(_DEFAULT_PROVIDER_ENV.get(provider, ()))


def _has_credentials(provider: str) -> bool:
    """True when every required env var for *provider* is set (non-empty).

    Providers with no known env requirement (e.g. ``mock``) are treated as
    having no discoverable credentials here because they also have no live
    fetcher — they are surfaced as unsupported-for-discovery, not queryable.
    """
    required = _provider_env_names(provider)
    if not required:
        return False
    return all(os.getenv(var, "").strip() for var in required)


@dataclass(frozen=True)
class ProviderDiscovery:
    """Discovery readiness / outcome for a single provider.

    Only env-var names and booleans are stored — never secret values.
    """

    name: str
    supported: bool
    has_fetcher: bool
    has_credentials: bool
    status: str
    env_keys: tuple[str, ...] = ()
    queried: bool = False
    model_count: int | None = None
    tier_best: dict[str, str] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "supported": self.supported,
            "has_fetcher": self.has_fetcher,
            "has_credentials": self.has_credentials,
            "status": self.status,
            "env_keys": list(self.env_keys),
            "queried": self.queried,
            "model_count": self.model_count,
            "tier_best": dict(self.tier_best),
            "error": self.error,
        }


@dataclass(frozen=True)
class CatalogDiff:
    """Difference between the current cached catalog and a discovered one."""

    added: tuple[tuple[str, str, str], ...] = ()      # (provider, model_id, tier)
    removed: tuple[tuple[str, str, str], ...] = ()     # (provider, model_id, tier)
    changed: tuple[tuple[str, str, str, str], ...] = ()  # (provider, tier, old, new)

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.removed or self.changed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "added": [
                {"provider": p, "model": m, "tier": t} for (p, m, t) in self.added
            ],
            "removed": [
                {"provider": p, "model": m, "tier": t} for (p, m, t) in self.removed
            ],
            "changed": [
                {"provider": p, "tier": t, "old": o, "new": n}
                for (p, t, o, n) in self.changed
            ],
        }


@dataclass(frozen=True)
class DiscoveryReport:
    """Read-only discovery report (audit or dry-run)."""

    generated_at: str
    live: bool
    providers: tuple[ProviderDiscovery, ...]
    diff: CatalogDiff
    current_catalog_updated_at: str | None = None
    warnings: tuple[str, ...] = ()

    # ── derived buckets (one provider may appear in exactly one) ─────────────
    @property
    def queried(self) -> tuple[str, ...]:
        return tuple(p.name for p in self.providers if p.status == STATUS_QUERIED)

    @property
    def unavailable(self) -> tuple[str, ...]:
        """Supported providers we could not query because credentials are missing."""
        return tuple(
            p.name for p in self.providers if p.status == STATUS_SKIPPED_NO_CREDS
        )

    @property
    def unsupported(self) -> tuple[str, ...]:
        """Providers with no live fetcher or not supported by the adapter."""
        return tuple(
            p.name
            for p in self.providers
            if p.status
            in (STATUS_UNSUPPORTED_NO_FETCHER, STATUS_UNSUPPORTED_PROVIDER)
        )

    @property
    def skipped(self) -> tuple[str, ...]:
        """Providers deliberately not queried (excluded by an explicit filter)."""
        return tuple(
            p.name for p in self.providers if p.status == STATUS_SKIPPED_BY_ARG
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": "live_dry_run" if self.live else "local_audit",
            "generated_at": self.generated_at,
            "live": self.live,
            "wrote_catalog": False,
            "current_catalog_updated_at": self.current_catalog_updated_at,
            "providers": [p.to_dict() for p in self.providers],
            "diff": self.diff.to_dict(),
            "buckets": {
                "queried": list(self.queried),
                "unavailable": list(self.unavailable),
                "unsupported": list(self.unsupported),
                "skipped": list(self.skipped),
            },
            "warnings": list(self.warnings),
        }

    def user_summary(self) -> str:
        mode = "dry-run discovery (no write)" if self.live else "local audit (no network)"
        lines = [
            "=== model discovery ===",
            f"mode: {mode}",
            f"current catalog: {self.current_catalog_updated_at or '(none — run :refresh-models)'}",
            "providers:",
        ]
        for p in self.providers:
            detail = (
                f"supported={p.supported} fetcher={p.has_fetcher} "
                f"creds={p.has_credentials} status={p.status}"
            )
            if p.queried and p.model_count is not None:
                detail += f" models={p.model_count}"
            lines.append(f"  - {p.name}: {detail}")
            if p.queried and p.tier_best:
                tier_str = "  ".join(f"{t}={m}" for t, m in sorted(p.tier_best.items()))
                lines.append(f"      tier_best: {tier_str}")
        if self.live:
            d = self.diff
            lines.append("diff vs current catalog:")
            if d.is_empty:
                lines.append("  (no changes)")
            for (prov, model, tier) in d.added:
                lines.append(f"  + added   {prov} {model} ({tier})")
            for (prov, model, tier) in d.removed:
                lines.append(f"  - removed {prov} {model} ({tier})")
            for (prov, tier, old, new) in d.changed:
                lines.append(f"  ~ changed {prov} {tier}: {old} -> {new}")
        for label, names in (
            ("unavailable (no credentials)", self.unavailable),
            ("unsupported (no fetcher / provider)", self.unsupported),
            ("skipped (excluded by filter)", self.skipped),
        ):
            if names:
                lines.append(f"{label}: {', '.join(names)}")
        if self.warnings:
            lines.append("warnings:")
            lines.extend(f"  - {w}" for w in self.warnings)
        return "\n".join(lines)


def _classify_provider_status(
    provider: str,
    *,
    requested: set[str] | None,
) -> str:
    """Decide the discovery status for a provider without any network call."""
    if requested is not None and provider not in requested:
        return STATUS_SKIPPED_BY_ARG
    if provider not in SUPPORTED_PROVIDERS:
        return STATUS_UNSUPPORTED_PROVIDER
    if provider not in mc._FETCHERS:
        return STATUS_UNSUPPORTED_NO_FETCHER
    if not _has_credentials(provider):
        return STATUS_SKIPPED_NO_CREDS
    return STATUS_QUERIED


def _all_known_providers() -> list[str]:
    """Every provider we can say something about, in a stable order."""
    names = set(SUPPORTED_PROVIDERS) | set(mc._FETCHERS.keys())
    return sorted(names)


def _diff_catalog(
    current: dict[str, Any] | None,
    discovered: dict[str, Any],
) -> CatalogDiff:
    """Pure diff of a discovered catalog against the current cached one."""
    added: list[tuple[str, str, str]] = []
    removed: list[tuple[str, str, str]] = []
    changed: list[tuple[str, str, str, str]] = []

    cur_providers = (current or {}).get("providers", {})
    disc_providers = discovered.get("providers", {})

    for provider, pdata in disc_providers.items():
        disc_models = {m["id"]: m.get("tier", "") for m in pdata.get("models", [])}
        cur_pdata = cur_providers.get(provider, {})
        cur_models = {m["id"]: m.get("tier", "") for m in cur_pdata.get("models", [])}

        for mid, tier in sorted(disc_models.items()):
            if mid not in cur_models:
                added.append((provider, mid, tier))
        for mid, tier in sorted(cur_models.items()):
            if mid not in disc_models:
                removed.append((provider, mid, tier))

        cur_best = cur_pdata.get("tier_best", {})
        disc_best = pdata.get("tier_best", {})
        for tier in sorted(set(cur_best) | set(disc_best)):
            old = cur_best.get(tier, "")
            new = disc_best.get(tier, "")
            if old and new and old != new:
                changed.append((provider, tier, old, new))

    return CatalogDiff(
        added=tuple(added),
        removed=tuple(removed),
        changed=tuple(changed),
    )


def _provider_row(provider: str, status: str) -> ProviderDiscovery:
    """Build a non-queried provider row (no network)."""
    return ProviderDiscovery(
        name=provider,
        supported=provider in SUPPORTED_PROVIDERS,
        has_fetcher=provider in mc._FETCHERS,
        has_credentials=_has_credentials(provider),
        status=status,
        env_keys=_provider_env_names(provider),
    )


def build_discovery_audit(
    router: Any = None,
    *,
    providers: list[str] | None = None,
) -> DiscoveryReport:
    """LOCAL, no-network discovery readiness snapshot.

    Reports which providers are supported / have a fetcher / have credentials,
    and what the current on-disk catalog knows. Makes NO provider calls and
    writes nothing. ``router`` is accepted for symmetry with other audits but is
    not required.
    """
    requested = {p.lower() for p in providers} if providers else None
    current = mc._load_catalog()
    rows: list[ProviderDiscovery] = []
    for provider in _all_known_providers():
        status = _classify_provider_status(provider, requested=requested)
        rows.append(_provider_row(provider, status))

    warnings = [
        "Audit is local only: no provider was contacted and no catalog was written.",
        "Run ':provider-catalog-refresh --dry-run' to compare live provider models "
        "against the current catalog without writing.",
    ]
    return DiscoveryReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        live=False,
        providers=tuple(rows),
        diff=CatalogDiff(),
        current_catalog_updated_at=(current or {}).get("updated_at"),
        warnings=tuple(warnings),
    )


def build_discovery_report(
    *,
    providers: list[str] | None = None,
    api_keys: dict[str, str] | None = None,
) -> DiscoveryReport:
    """DRY-RUN discovery: query queryable providers and diff, WITHOUT writing.

    Only providers that are supported, have a live fetcher, and have credentials
    are actually contacted (a metadata-only, non-inference provider call). Every
    other provider is reported with an explanatory status and never contacted.
    The catalog on disk is never modified.
    """
    requested = {p.lower() for p in providers} if providers else None
    current = mc._load_catalog()

    statuses: dict[str, str] = {
        provider: _classify_provider_status(provider, requested=requested)
        for provider in _all_known_providers()
    }
    to_query = [p for p, s in statuses.items() if s == STATUS_QUERIED]

    discovered: dict[str, Any] = {"providers": {}}
    if to_query:
        # discover_catalog only calls the fetchers for the providers we pass and
        # never writes anything to disk.
        discovered = mc.discover_catalog(to_query, api_keys=api_keys)

    disc_providers = discovered.get("providers", {})

    rows: list[ProviderDiscovery] = []
    for provider in _all_known_providers():
        status = statuses[provider]
        if status == STATUS_QUERIED and provider in disc_providers:
            pdata = disc_providers[provider]
            rows.append(
                ProviderDiscovery(
                    name=provider,
                    supported=True,
                    has_fetcher=True,
                    has_credentials=True,
                    status=STATUS_QUERIED,
                    env_keys=_provider_env_names(provider),
                    queried=True,
                    model_count=len(pdata.get("models", [])),
                    tier_best=dict(pdata.get("tier_best", {})),
                )
            )
        elif status == STATUS_QUERIED:
            # Credentials present but the fetch returned nothing / failed inside
            # discover_catalog (which logs and skips). Report as an error row.
            rows.append(
                ProviderDiscovery(
                    name=provider,
                    supported=True,
                    has_fetcher=True,
                    has_credentials=True,
                    status=STATUS_QUERIED,
                    env_keys=_provider_env_names(provider),
                    queried=True,
                    model_count=0,
                    error="fetch returned no models",
                )
            )
        else:
            rows.append(_provider_row(provider, status))

    diff = _diff_catalog(current, discovered)

    warnings = [
        "Dry-run only: no catalog was written and no model was switched.",
        "Provider model-list access is metadata-only (non-inference) but is still "
        "an external provider call.",
    ]
    return DiscoveryReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        live=True,
        providers=tuple(rows),
        diff=diff,
        current_catalog_updated_at=(current or {}).get("updated_at"),
        warnings=tuple(warnings),
    )

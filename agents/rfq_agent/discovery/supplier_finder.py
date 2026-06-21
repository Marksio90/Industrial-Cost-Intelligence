from __future__ import annotations

import asyncio
from typing import Any

import structlog

from ..config import AgentSettings
from ..database.models import DiscoveredSupplierORM
from ..database.repository import AgentRepository
from ..events.publisher import EventPublisher, RFQEvents
from ..safety.compliance import ComplianceChecker
from .supplier_scraper import ScrapedSupplier, SupplierScraper

log = structlog.get_logger(__name__)


# Directory sources to query (URL templates with {query} placeholder)
_DIRECTORIES: list[dict[str, Any]] = [
    {
        "name": "Europages",
        "template": "https://www.europages.co.uk/companies/{query}",
        "card_selector": ".listing-item, article.company",
    },
    {
        "name": "Kompass",
        "template": "https://www.kompass.com/a/{query}/",
        "card_selector": ".result-item, .company-block",
    },
    {
        "name": "ThomasNet",
        "template": "https://www.thomasnet.com/search/?what={query}",
        "card_selector": ".profile-card, .supplier-profile",
    },
]


class SupplierFinder:
    """
    Orchestrates multi-source supplier discovery:
    1. Scrape public directories (Playwright)
    2. Query internal DB for existing suppliers
    3. Deduplicate + persist discovered suppliers
    4. Emit events for each new discovery
    """

    def __init__(
        self,
        settings: AgentSettings,
        repo: AgentRepository,
        publisher: EventPublisher,
        tenant_id: str,
    ) -> None:
        self._settings = settings
        self._repo = repo
        self._publisher = publisher
        self._tenant_id = tenant_id
        self._scraper = SupplierScraper(settings)
        self._compliance = ComplianceChecker(settings)

    async def find(
        self,
        keywords: list[str],
        country_code: str | None = None,
        max_per_source: int = 10,
    ) -> list[DiscoveredSupplierORM]:
        query = " ".join(keywords[:3])
        if country_code:
            query = f"{query} {country_code}"

        log.info("supplier_discovery_start", query=query, sources=len(_DIRECTORIES))

        tasks = [
            self._scrape_source(src, query, max_per_source)
            for src in _DIRECTORIES
        ]
        scraped_batches = await asyncio.gather(*tasks, return_exceptions=True)

        all_scraped: list[ScrapedSupplier] = []
        for batch in scraped_batches:
            if isinstance(batch, Exception):
                log.warning("source_scrape_failed", error=str(batch))
            else:
                all_scraped.extend(batch)

        log.info("raw_suppliers_found", count=len(all_scraped))

        # Deduplicate by email
        seen_emails: set[str] = set()
        unique: list[ScrapedSupplier] = []
        for s in all_scraped:
            key = s.email or s.website
            if key and key not in seen_emails:
                seen_emails.add(key)
                unique.append(s)

        # Compliance filter
        compliant: list[ScrapedSupplier] = []
        for s in unique:
            if s.email:
                result = self._compliance.check_recipient(s.email)
                if not result.allowed:
                    log.info(
                        "supplier_filtered",
                        name=s.name,
                        violations=result.violations,
                    )
                    continue
            if country_code and s.country_code and s.country_code != country_code:
                continue
            compliant.append(s)

        # Persist and emit events
        persisted: list[DiscoveredSupplierORM] = []
        for s in compliant:
            orm = DiscoveredSupplierORM(
                tenant_id=self._tenant_id,
                name=s.name,
                email=s.email,
                domain=s.email.split("@")[-1] if s.email else None,
                website=s.website,
                country_code=s.country_code,
                capabilities=s.capabilities,
                metadata={"description": s.description, "source_url": s.source_url},
                source="scraper",
            )
            saved = await self._repo.upsert_supplier(orm)
            persisted.append(saved)
            await self._publisher.publish(
                RFQEvents.SUPPLIER_DISCOVERED,
                {"supplier_id": str(saved.id), "name": s.name, "email": s.email},
                tenant_id=self._tenant_id,
            )

        log.info(
            "supplier_discovery_done",
            raw=len(all_scraped),
            unique=len(unique),
            compliant=len(compliant),
            persisted=len(persisted),
        )
        return persisted

    async def find_from_db(
        self,
        keywords: list[str],
        country_code: str | None = None,
        limit: int = 20,
    ) -> list[DiscoveredSupplierORM]:
        """Return matching suppliers already in the database."""
        keyword = " ".join(keywords[:2]) if keywords else None
        return await self._repo.list_suppliers(
            self._tenant_id, keyword=keyword, country_code=country_code, limit=limit
        )

    async def _scrape_source(
        self,
        source: dict[str, Any],
        query: str,
        max_results: int,
    ) -> list[ScrapedSupplier]:
        import urllib.parse
        url = source["template"].replace("{query}", urllib.parse.quote(query))
        log.debug("scraping_directory", source=source["name"], url=url)
        try:
            return await self._scraper.scrape_directory(
                url,
                selector_card=source.get("card_selector", "article"),
                max_results=max_results,
            )
        except Exception as exc:
            log.error("directory_scrape_error", source=source["name"], error=str(exc))
            return []

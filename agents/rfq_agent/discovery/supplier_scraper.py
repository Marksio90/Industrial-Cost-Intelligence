from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

import structlog
from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from ..config import AgentSettings
from ..safety.compliance import ComplianceChecker

log = structlog.get_logger(__name__)


@dataclass
class ScrapedSupplier:
    name: str
    website: str
    email: str | None = None
    phone: str | None = None
    country_code: str | None = None
    description: str | None = None
    capabilities: list[str] = field(default_factory=list)
    source_url: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
)
_PHONE_RE = re.compile(
    r"(?:\+\d{1,3}[\s\-]?)?\(?\d{2,4}\)?[\s\-]?\d{3,5}[\s\-]?\d{4,6}"
)
_BLOCKED_EMAIL_DOMAINS = {"example.com", "test.com", "sentry.io", "w3.org"}


class SupplierScraper:
    """
    Playwright-based supplier scraper.
    Supports directory-style pages and individual supplier pages.
    """

    def __init__(self, settings: AgentSettings) -> None:
        self._settings = settings
        self._compliance = ComplianceChecker(settings)
        self._semaphore = asyncio.Semaphore(settings.scraper_max_concurrent)

    async def scrape_directory(
        self,
        url: str,
        selector_card: str = "article, .supplier-card, .company-card, li.result",
        max_results: int = 20,
    ) -> list[ScrapedSupplier]:
        """Scrape a supplier directory listing page."""
        log.info("scrape_directory_start", url=url)
        check = self._compliance.check_url(url)
        if not check.allowed:
            log.warning("scrape_blocked", url=url, violations=check.violations)
            return []

        async with async_playwright() as pw:
            browser = await self._launch_browser(pw)
            try:
                context = await self._new_context(browser)
                page = await context.new_page()
                await self._goto_safe(page, url)

                cards = await page.query_selector_all(selector_card)
                log.info("directory_cards_found", count=len(cards))

                suppliers: list[ScrapedSupplier] = []
                for card in cards[:max_results]:
                    try:
                        s = await self._extract_card(card, url)
                        if s:
                            suppliers.append(s)
                    except Exception as exc:
                        log.debug("card_extract_failed", error=str(exc))

                return suppliers
            finally:
                await browser.close()

    async def scrape_supplier_page(self, url: str) -> ScrapedSupplier | None:
        """Scrape an individual supplier / company page."""
        check = self._compliance.check_url(url)
        if not check.allowed:
            return None

        async with self._semaphore:
            async with async_playwright() as pw:
                browser = await self._launch_browser(pw)
                try:
                    context = await self._new_context(browser)
                    page = await context.new_page()
                    await self._goto_safe(page, url)
                    return await self._extract_page(page, url)
                except Exception as exc:
                    log.error("scrape_page_failed", url=url, error=str(exc))
                    return None
                finally:
                    await browser.close()

    async def scrape_search_results(
        self,
        query: str,
        search_url_template: str = "https://www.europages.co.uk/companies/{query}",
        max_results: int = 10,
    ) -> list[ScrapedSupplier]:
        """Search for suppliers using a parametrised URL template."""
        import urllib.parse
        url = search_url_template.replace("{query}", urllib.parse.quote(query))
        return await self.scrape_directory(url, max_results=max_results)

    # ── Private helpers ────────────────────────────────────────────────────────

    async def _launch_browser(self, pw) -> Browser:
        return await pw.chromium.launch(
            headless=self._settings.playwright_headless,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

    async def _new_context(self, browser: Browser) -> BrowserContext:
        return await browser.new_context(
            user_agent=self._settings.scraper_user_agent,
            viewport={"width": 1280, "height": 900},
            java_script_enabled=True,
            accept_downloads=False,
        )

    async def _goto_safe(self, page: Page, url: str) -> None:
        await page.goto(
            url,
            timeout=self._settings.playwright_timeout_ms,
            wait_until="domcontentloaded",
        )
        # Gentle delay — respect robots.txt spirit
        await asyncio.sleep(1.5)

    async def _extract_card(self, card, base_url: str) -> ScrapedSupplier | None:
        name = await _text(card, "h2, h3, .name, .title, a")
        if not name:
            return None

        link_el = await card.query_selector("a[href]")
        href = (await link_el.get_attribute("href")) if link_el else None
        website = urljoin(base_url, href) if href else base_url

        raw_text = await card.inner_text()
        email = _first_email(raw_text)
        country = _guess_country(raw_text)

        return ScrapedSupplier(
            name=name.strip(),
            website=website,
            email=email,
            country_code=country,
            description=raw_text[:500],
            source_url=base_url,
        )

    async def _extract_page(self, page: Page, url: str) -> ScrapedSupplier | None:
        title = await page.title()
        body = await page.inner_text("body")

        email = _first_email(body)
        phone = _first_phone(body)
        description = _extract_description(page, body)
        capabilities = _extract_capabilities(body)

        # Try structured data
        og_site = await _meta(page, "og:site_name")
        name = og_site or title.split("|")[0].split("–")[0].strip()

        return ScrapedSupplier(
            name=name[:256],
            website=url,
            email=email,
            phone=phone,
            description=description,
            capabilities=capabilities,
            source_url=url,
            metadata={"page_title": title},
        )


# ── Extraction utilities ───────────────────────────────────────────────────

async def _text(element, selector: str) -> str | None:
    el = await element.query_selector(selector)
    if el:
        return (await el.inner_text()).strip() or None
    return None


async def _meta(page: Page, property_name: str) -> str | None:
    el = await page.query_selector(f'meta[property="{property_name}"]')
    if el:
        return await el.get_attribute("content")
    return None


def _first_email(text: str) -> str | None:
    for match in _EMAIL_RE.finditer(text):
        email = match.group(0).lower()
        domain = email.split("@")[-1]
        if domain not in _BLOCKED_EMAIL_DOMAINS:
            return email
    return None


def _first_phone(text: str) -> str | None:
    m = _PHONE_RE.search(text)
    return m.group(0).strip() if m else None


def _guess_country(text: str) -> str | None:
    # Very simple heuristic: look for 2-letter country codes in common patterns
    m = re.search(r"\b(DE|PL|IT|FR|ES|CZ|RO|HU|SK|AT|NL|BE|SE|FI)\b", text)
    return m.group(1) if m else None


def _extract_description(page, body: str) -> str:
    # Use first 500 chars of body text as fallback description
    return " ".join(body.split()[:100])


def _extract_capabilities(body: str) -> list[str]:
    keywords = [
        "casting", "machining", "milling", "turning", "welding", "forging",
        "injection moulding", "stamping", "extrusion", "coating", "assembly",
        "sheet metal", "cnc", "3d printing", "additive manufacturing",
    ]
    found = []
    body_lower = body.lower()
    for kw in keywords:
        if kw in body_lower:
            found.append(kw)
    return found

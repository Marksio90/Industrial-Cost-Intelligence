# RFQ Agent — Response Parser, Offer Normalization, Pricing Comparator, Risk Controls, HITL

## 6. Response Parser

### 6.1 Strategie parsowania odpowiedzi

Agent odbiera odpowiedzi dostawców w trzech kanałach:
- **Email** (MIME plain/HTML) → parsowanie NLP przez Claude
- **Supplier portal scraping** → Playwright + CSS selectors
- **Structured API** (EDI 810/PEPPOL BIS) → XML/JSON parser

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
import re

class ResponseChannel(Enum):
    EMAIL   = "EMAIL"
    PORTAL  = "PORTAL"
    API_EDI = "API_EDI"
    MANUAL  = "MANUAL"

@dataclass
class ParsedResponse:
    raw_source:        str                  # email body / HTML / XML
    channel:           ResponseChannel
    supplier_id:       str | None
    supplier_name:     str
    rfq_id:            str
    # Extracted fields
    unit_price:        float | None
    currency:          str | None           # "EUR" | "USD" | "PLN" | ...
    total_price:       float | None
    quantity:          float | None
    delivery_days:     int | None
    delivery_date:     str | None
    payment_terms:     str | None           # "Net 30" | "2/10 Net 30" | ...
    incoterms:         str | None           # "DAP" | "EXW" | "DDP"
    certifications:    list[str] = field(default_factory=list)
    validity_days:     int | None = None    # Quote validity
    min_order_qty:     float | None = None
    lead_time_weeks:   float | None = None
    technical_notes:   str = ""
    risk_flags:        list[str] = field(default_factory=list)
    parse_confidence:  float = 0.0
    parse_errors:      list[str] = field(default_factory=list)
```

### 6.2 LLM-powered Email Parser

```python
import anthropic
import json

PARSE_EMAIL_SYSTEM = """You are an expert procurement data extraction agent.
Extract structured pricing and delivery information from supplier response emails.
Return ONLY valid JSON matching the schema. Use null for missing fields.
Never fabricate data — only extract what is explicitly stated."""

PARSE_EMAIL_SCHEMA = {
    "unit_price":       "number or null",
    "currency":         "string (3-letter ISO) or null",
    "total_price":      "number or null",
    "quantity":         "number or null",
    "delivery_days":    "integer or null",
    "delivery_date":    "string (ISO 8601) or null",
    "payment_terms":    "string or null",
    "incoterms":        "string or null",
    "certifications":   "array of strings",
    "validity_days":    "integer or null",
    "min_order_qty":    "number or null",
    "lead_time_weeks":  "number or null",
    "technical_notes":  "string",
    "risk_flags":       "array of strings — any red flags found (unusually low price, vague terms, etc.)",
    "parse_confidence": "float 0-1 — your confidence in the extracted data",
}

class EmailResponseParser:

    def __init__(self, llm: anthropic.AsyncAnthropic):
        self._llm = llm

    async def parse(
        self,
        email_body:  str,
        supplier_id: str | None,
        rfq_id:      str,
        rfq_context: dict,
    ) -> ParsedResponse:
        prompt = f"""RFQ Context:
- Product: {rfq_context.get('product_name')}
- Material: {rfq_context.get('material_code')}
- Requested qty: {rfq_context.get('quantity')} {rfq_context.get('unit')}
- Target price: {rfq_context.get('target_price_eur')} EUR

Supplier email to parse:
---
{email_body[:8000]}
---

Extract the data according to this JSON schema:
{json.dumps(PARSE_EMAIL_SCHEMA, indent=2)}

Return ONLY the JSON object, no other text."""

        response = await self._llm.messages.create(
            model="claude-opus-4-8",
            max_tokens=1500,
            system=PARSE_EMAIL_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )

        raw_json = response.content[0].text.strip()
        # Strip markdown fences if present
        raw_json = re.sub(r"^```(?:json)?\n?", "", raw_json)
        raw_json = re.sub(r"\n?```$", "", raw_json)

        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            return ParsedResponse(
                raw_source=email_body, channel=ResponseChannel.EMAIL,
                supplier_id=supplier_id, supplier_name="", rfq_id=rfq_id,
                unit_price=None, currency=None, total_price=None, quantity=None,
                delivery_days=None, delivery_date=None, payment_terms=None,
                incoterms=None, parse_confidence=0.0,
                parse_errors=[f"JSON decode error: {exc}"],
            )

        return ParsedResponse(
            raw_source       = email_body,
            channel          = ResponseChannel.EMAIL,
            supplier_id      = supplier_id,
            supplier_name    = "",
            rfq_id           = rfq_id,
            unit_price       = data.get("unit_price"),
            currency         = data.get("currency"),
            total_price      = data.get("total_price"),
            quantity         = data.get("quantity"),
            delivery_days    = data.get("delivery_days"),
            delivery_date    = data.get("delivery_date"),
            payment_terms    = data.get("payment_terms"),
            incoterms        = data.get("incoterms"),
            certifications   = data.get("certifications", []),
            validity_days    = data.get("validity_days"),
            min_order_qty    = data.get("min_order_qty"),
            lead_time_weeks  = data.get("lead_time_weeks"),
            technical_notes  = data.get("technical_notes", ""),
            risk_flags       = data.get("risk_flags", []),
            parse_confidence = data.get("parse_confidence", 0.5),
        )
```

### 6.3 Portal Scraper

```python
from playwright.async_api import async_playwright, Page
import asyncio

@dataclass
class PortalScraperConfig:
    url:             str
    login_url:       str | None
    username:        str | None
    password:        str | None
    price_selector:  str           # CSS selector for price field
    delivery_selector: str
    currency_selector: str | None
    pagination_selector: str | None = None
    requires_login:  bool = False
    timeout_ms:      int  = 20_000

PORTAL_CONFIGS: dict[str, PortalScraperConfig] = {
    "thomasnet": PortalScraperConfig(
        url="https://www.thomasnet.com/rfq/{rfq_id}",
        login_url="https://www.thomasnet.com/login",
        username=None, password=None,
        price_selector=".price-value",
        delivery_selector=".delivery-time",
        currency_selector=".currency-code",
        requires_login=True,
    ),
}

class SupplierPortalScraper:

    def __init__(self, db: "asyncpg.Pool"):
        self._db = db

    async def scrape(
        self,
        supplier_id: str,
        portal_name: str,
        rfq_id:      str,
    ) -> ParsedResponse | None:
        config = PORTAL_CONFIGS.get(portal_name)
        if not config:
            return None

        creds = await self._load_credentials(portal_name)
        config.username = creds.get("username")
        config.password = creds.get("password")

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            page = await browser.new_page()

            try:
                if config.requires_login and config.login_url:
                    await self._login(page, config)

                await page.goto(
                    config.url.format(rfq_id=rfq_id),
                    wait_until="networkidle",
                    timeout=config.timeout_ms,
                )
                return await self._extract_offer(page, config, supplier_id, rfq_id)
            finally:
                await browser.close()

    async def _login(self, page: Page, config: PortalScraperConfig) -> None:
        await page.goto(config.login_url, wait_until="networkidle")
        await page.fill('[name="username"], [name="email"]', config.username or "")
        await page.fill('[name="password"]', config.password or "")
        await page.click('[type="submit"]')
        await page.wait_for_load_state("networkidle")

    async def _extract_offer(
        self, page: Page, config: PortalScraperConfig,
        supplier_id: str, rfq_id: str,
    ) -> ParsedResponse:
        async def safe_text(selector: str) -> str | None:
            try:
                el = await page.query_selector(selector)
                return (await el.inner_text()).strip() if el else None
            except Exception:
                return None

        price_text    = await safe_text(config.price_selector)
        delivery_text = await safe_text(config.delivery_selector)
        currency_text = await safe_text(config.currency_selector) if config.currency_selector else "EUR"

        price = None
        if price_text:
            m = re.search(r"[\d.,]+", price_text.replace(",", "."))
            if m:
                try: price = float(m.group())
                except ValueError: pass

        delivery_days = None
        if delivery_text:
            m = re.search(r"(\d+)", delivery_text)
            if m: delivery_days = int(m.group(1))

        return ParsedResponse(
            raw_source      = await page.content(),
            channel         = ResponseChannel.PORTAL,
            supplier_id     = supplier_id,
            supplier_name   = "",
            rfq_id          = rfq_id,
            unit_price      = price,
            currency        = currency_text or "EUR",
            total_price     = None,
            quantity        = None,
            delivery_days   = delivery_days,
            delivery_date   = None,
            payment_terms   = None,
            incoterms       = None,
            parse_confidence= 0.70 if price else 0.20,
        )

    async def _load_credentials(self, portal: str) -> dict:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT credentials FROM rfqa.portal_credentials WHERE portal_name = $1",
                portal,
            )
        return row["credentials"] if row else {}
```

---

## 7. Offer Normalization

### 7.1 Cel normalizacji

Oferty przychodzą w różnych walutach, incoterms, terminach płatności i formatach.
Normalizator sprowadza je do wspólnego mianownika: **EUR, DDP, Net 30, per unit**.

```python
@dataclass
class NormalizedOffer:
    offer_id:          str
    rfq_id:            str
    supplier_id:       str | None
    supplier_name:     str
    # Normalized financials (all EUR, DDP-equivalent)
    unit_price_eur:    float
    total_price_eur:   float
    currency_original: str
    fx_rate_used:      float
    incoterms_original:str | None
    incoterms_adj_eur: float              # cost to bring to DDP from quoted incoterm
    payment_terms_std: float              # NPV discount for non-Net30 terms
    # Delivery
    delivery_days:     int
    # Quality / compliance
    certifications:    list[str]
    quality_score:     float              # From SIE or default
    # Risk
    risk_score:        float              # 0–1
    risk_flags:        list[str]
    # Metadata
    validity_until:    str | None
    parse_confidence:  float
    normalization_notes: list[str] = field(default_factory=list)
```

### 7.2 OfferNormalizer

```python
import httpx
from datetime import datetime, timezone

# Incoterms logistics cost adders (EUR/kg typical, to convert to DDP)
INCOTERMS_ADDER_PCT: dict[str, float] = {
    "DDP": 0.000,    # Delivered Duty Paid — no adder
    "DAP": 0.015,    # Delivered At Place — add import duty ~1.5%
    "CIF": 0.025,    # Cost Insurance Freight — add duty+customs
    "CFR": 0.030,
    "FOB": 0.040,    # Free On Board — add freight+duty
    "FCA": 0.035,
    "EXW": 0.060,    # Ex Works — full logistics on buyer
    "CPT": 0.020,
    "CIP": 0.018,
    "DAT": 0.012,
}

# Payment terms NPV discount (vs Net 30 baseline, annual rate 8%)
PAYMENT_TERMS_DISCOUNT: dict[str, float] = {
    "Net 15":        0.005,    # Faster = slight premium to us
    "Net 30":        0.000,    # Baseline
    "Net 45":       -0.008,
    "Net 60":       -0.015,
    "Net 90":       -0.025,
    "2/10 Net 30":  -0.005,    # Early payment discount available
    "Prepayment":   -0.035,    # Full prepayment risk penalty
    "LC":           -0.020,    # Letter of Credit
}

class OfferNormalizer:

    ECB_RATES_URL = "https://api.exchangerate-api.com/v4/latest/EUR"

    def __init__(self, db: "asyncpg.Pool"):
        self._db     = db
        self._fx_cache: dict[str, float] = {}

    async def normalize(
        self,
        parsed:   ParsedResponse,
        quantity: float,
    ) -> NormalizedOffer:
        notes: list[str] = []

        # Step 1: Currency conversion to EUR
        currency = (parsed.currency or "EUR").upper()
        fx_rate  = await self._get_fx_rate(currency)
        unit_price_eur = (parsed.unit_price or 0) / fx_rate
        if currency != "EUR":
            notes.append(f"Converted {currency}→EUR at rate {fx_rate:.4f}")

        # Step 2: Incoterms adjustment
        inco     = (parsed.incoterms or "EXW").upper()
        inco_adj = INCOTERMS_ADDER_PCT.get(inco, 0.06)
        unit_price_eur_ddp = unit_price_eur * (1 + inco_adj)
        if inco != "DDP":
            notes.append(f"Incoterms {inco} → DDP adjustment +{inco_adj*100:.1f}%")

        # Step 3: Payment terms NPV adjustment
        pt_key      = (parsed.payment_terms or "Net 30").strip()
        pt_discount = PAYMENT_TERMS_DISCOUNT.get(pt_key, 0.0)
        unit_price_final = unit_price_eur_ddp * (1 - pt_discount)
        if pt_discount != 0:
            notes.append(f"Payment terms '{pt_key}' NPV adj {pt_discount*100:+.2f}%")

        # Step 4: Total price
        total = unit_price_final * quantity

        # Step 5: Risk score
        risk_score, risk_flags = self._assess_risk(parsed, unit_price_final)

        return NormalizedOffer(
            offer_id           = str(uuid.uuid4()),
            rfq_id             = parsed.rfq_id,
            supplier_id        = parsed.supplier_id,
            supplier_name      = parsed.supplier_name,
            unit_price_eur     = round(unit_price_final, 4),
            total_price_eur    = round(total, 2),
            currency_original  = currency,
            fx_rate_used       = fx_rate,
            incoterms_original = inco,
            incoterms_adj_eur  = round(unit_price_eur * inco_adj, 4),
            payment_terms_std  = round(pt_discount, 4),
            delivery_days      = parsed.delivery_days or 999,
            certifications     = parsed.certifications,
            quality_score      = 0.70,               # Will be enriched from SIE
            risk_score         = risk_score,
            risk_flags         = risk_flags + (parsed.risk_flags or []),
            validity_until     = parsed.delivery_date,
            parse_confidence   = parsed.parse_confidence,
            normalization_notes= notes,
        )

    def _assess_risk(
        self, parsed: ParsedResponse, normalized_price: float
    ) -> tuple[float, list[str]]:
        score = 0.0
        flags: list[str] = []

        if parsed.parse_confidence < 0.60:
            score += 0.25
            flags.append("Low parse confidence — verify manually")
        if not parsed.unit_price:
            score += 0.40
            flags.append("No unit price extracted")
        if parsed.validity_days and parsed.validity_days < 14:
            score += 0.15
            flags.append(f"Short quote validity: {parsed.validity_days} days")
        if parsed.incoterms == "EXW" and normalized_price > 1000:
            score += 0.10
            flags.append("High-value EXW: logistics cost uncertainty")
        if "prepayment" in (parsed.payment_terms or "").lower():
            score += 0.20
            flags.append("Prepayment required — cash flow risk")

        return min(round(score, 3), 1.0), flags

    async def _get_fx_rate(self, currency: str) -> float:
        if currency == "EUR":
            return 1.0
        if currency in self._fx_cache:
            return self._fx_cache[currency]
        # Try DB cache first
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT rate FROM rfqa.fx_rates WHERE currency = $1 AND fetched_at > now() - INTERVAL '1 hour'",
                currency,
            )
        if row:
            self._fx_cache[currency] = float(row["rate"])
            return float(row["rate"])
        # Fallback: fetch from ECB
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(self.ECB_RATES_URL, timeout=5.0)
                rates = resp.json()["rates"]
                rate  = rates.get(currency, 1.0)
                self._fx_cache[currency] = rate
                async with self._db.acquire() as conn:
                    await conn.execute(
                        "INSERT INTO rfqa.fx_rates (currency, rate, fetched_at) VALUES ($1, $2, now()) "
                        "ON CONFLICT (currency) DO UPDATE SET rate=$2, fetched_at=now()",
                        currency, rate,
                    )
                return rate
        except Exception:
            return 1.0     # Fallback: assume 1:1 and flag
```

---

## 8. Pricing Comparator

### 8.1 Komparator ofert

```python
import numpy as np
from dataclasses import dataclass

@dataclass
class ComparisonReport:
    rfq_id:               str
    ranked_offers:        list[OfferScore]
    winner:               OfferScore | None
    runner_up:            OfferScore | None
    target_price_eur:     float
    median_offer_eur:     float
    min_offer_eur:        float
    max_offer_eur:        float
    price_spread_pct:     float
    savings_vs_target:    float          # EUR
    savings_pct:          float
    market_index:         float          # ratio: median / target
    outlier_offers:       list[str]      # supplier_ids with anomalous prices
    recommendation_text:  str
    auto_approved:        bool
    decision_outcome:     DecisionOutcome
    generated_at:         str

class PricingComparator:

    OUTLIER_IQR_FACTOR = 2.5

    def __init__(
        self,
        decision_engine: DecisionEngine,
        rule_engine:     DecisionRuleEngine,
        config:          RFQAgentConfig,
    ):
        self._decisions  = decision_engine
        self._rules      = rule_engine
        self._config     = config

    async def compare(
        self,
        offers:         list[NormalizedOffer],
        rfq:            RFQRequest,
    ) -> ComparisonReport:
        if not offers:
            return self._empty_report(rfq)

        scored = await self._decisions.score_offers(
            offers           = offers,
            target_price     = rfq.target_price_eur,
            required_certs   = rfq.required_certifications,
            required_delivery_days = self._deadline_to_days(rfq.required_delivery_date),
        )

        prices = np.array([o.unit_price_eur for o in offers])
        median = float(np.median(prices))
        q1, q3 = np.percentile(prices, [25, 75])
        iqr    = q3 - q1

        # Outlier detection
        outliers = [
            o.supplier_id or "" for o in offers
            if o.unit_price_eur < q1 - self.OUTLIER_IQR_FACTOR * iqr
            or o.unit_price_eur > q3 + self.OUTLIER_IQR_FACTOR * iqr
        ]

        decision = self._rules.decide(scored, rfq.target_price_eur, rfq.budget_limit_eur, self._config)

        winner   = decision.winner
        savings  = (rfq.target_price_eur - winner.unit_price_eur) * rfq.quantity if winner else 0.0

        rec_text = self._build_recommendation_text(
            decision, scored, rfq, median, outliers
        )

        return ComparisonReport(
            rfq_id              = rfq.rfq_id,
            ranked_offers       = scored,
            winner              = winner,
            runner_up           = decision.runner_up,
            target_price_eur    = rfq.target_price_eur,
            median_offer_eur    = round(median, 4),
            min_offer_eur       = round(float(np.min(prices)), 4),
            max_offer_eur       = round(float(np.max(prices)), 4),
            price_spread_pct    = round((float(np.max(prices)) - float(np.min(prices))) / max(median, 0.01) * 100, 2),
            savings_vs_target   = round(savings, 2),
            savings_pct         = round(savings / max(rfq.target_price_eur * rfq.quantity, 0.01) * 100, 2),
            market_index        = round(median / max(rfq.target_price_eur, 0.01), 4),
            outlier_offers      = outliers,
            recommendation_text = rec_text,
            auto_approved       = decision.outcome == DecisionOutcome.AUTO_SELECT,
            decision_outcome    = decision.outcome,
            generated_at        = datetime.now(timezone.utc).isoformat(),
        )

    def _build_recommendation_text(
        self,
        decision: DecisionResult,
        scored:   list[OfferScore],
        rfq:      RFQRequest,
        median:   float,
        outliers: list[str],
    ) -> str:
        w = decision.winner
        lines = [
            f"RFQ {rfq.rfq_id} — {len(scored)} offers received",
            f"Best offer: {w.supplier_name} @ {w.unit_price_eur:.2f} EUR/unit "
            f"(rank score: {w.composite_score:.3f})" if w else "No valid offers",
            f"Target price: {rfq.target_price_eur:.2f} EUR/unit | Market median: {median:.2f} EUR",
        ]
        if outliers:
            lines.append(f"⚠ Outlier prices detected for {len(outliers)} supplier(s) — verify authenticity")
        if decision.outcome == DecisionOutcome.AUTO_SELECT:
            lines.append("✓ AUTO-APPROVED: all criteria met, proceeding automatically")
        elif decision.outcome == DecisionOutcome.RECOMMEND_HITL:
            lines.append(f"⚠ HITL REQUIRED: {decision.hitl_reason}")
        return "\n".join(lines)

    def _deadline_to_days(self, date_str: str) -> int:
        try:
            deadline = datetime.fromisoformat(date_str)
            return max((deadline - datetime.now(timezone.utc)).days, 1)
        except Exception:
            return 30

    def _empty_report(self, rfq: RFQRequest) -> ComparisonReport:
        return ComparisonReport(
            rfq_id=rfq.rfq_id, ranked_offers=[], winner=None, runner_up=None,
            target_price_eur=rfq.target_price_eur,
            median_offer_eur=0, min_offer_eur=0, max_offer_eur=0,
            price_spread_pct=0, savings_vs_target=0, savings_pct=0,
            market_index=0, outlier_offers=[],
            recommendation_text="No offers to compare",
            auto_approved=False, decision_outcome=DecisionOutcome.INSUFFICIENT,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )
```

### 8.2 Price Database Updater

```python
class PriceDBUpdater:
    """
    Updates CHE (Cost History Engine) and internal price index
    with newly confirmed market prices from RFQ cycle.
    """

    CHE_URL = "http://che.internal/v1/quotes"

    def __init__(self, db: "asyncpg.Pool", http: httpx.AsyncClient):
        self._db   = db
        self._http = http

    async def update(
        self,
        rfq:    RFQRequest,
        offers: list[NormalizedOffer],
        winner: OfferScore | None,
    ) -> int:
        """Returns count of prices updated."""
        updated = 0
        for offer in offers:
            if not offer.unit_price_eur:
                continue
            # Update internal market price index
            async with self._db.acquire() as conn:
                await conn.execute(
                    """INSERT INTO rfqa.market_price_index
                       (material_code, supplier_id, unit_price_eur, quantity,
                        currency_original, incoterms, location, is_winner,
                        rfq_id, recorded_at)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,now())
                       ON CONFLICT (material_code, supplier_id)
                       DO UPDATE SET unit_price_eur=$3, recorded_at=now()""",
                    rfq.material_code,
                    offer.supplier_id,
                    offer.unit_price_eur,
                    rfq.quantity,
                    offer.currency_original,
                    offer.incoterms_original,
                    rfq.preferred_location,
                    offer.supplier_id == (winner.supplier_id if winner else None),
                    rfq.rfq_id,
                )
            updated += 1
            # Push winner to CHE
            if winner and offer.supplier_id == winner.supplier_id:
                await self._push_to_che(rfq, offer)

        return updated

    async def _push_to_che(
        self, rfq: RFQRequest, offer: NormalizedOffer
    ) -> None:
        try:
            await self._http.post(
                f"{self.CHE_URL}/record",
                json={
                    "supplier_id":    offer.supplier_id,
                    "product_code":   rfq.material_code,
                    "unit_price_eur": offer.unit_price_eur,
                    "quantity":       rfq.quantity,
                    "currency":       "EUR",
                    "source":         "RFQ_AGENT",
                    "rfq_id":         rfq.rfq_id,
                },
                timeout=5.0,
            )
        except Exception:
            pass    # CHE update is best-effort; event will be emitted via Kafka
```

---

## 9. Risk Controls

### 9.1 Warstwy kontroli ryzyka

Agent RFQA zawiera **5 warstw** kontroli ryzyka działających niezależnie:

```
Layer 1: Pre-action Risk Gate (przed każdym wywołaniem narzędzia)
Layer 2: Anti-spam Enforcement (przed każdym emailem)
Layer 3: Offer Anomaly Detection (w normalizacji + komparatorze)
Layer 4: Spend Authorization (autoryzacja wydatków)
Layer 5: Post-cycle Audit (po zakończeniu cyklu)
```

### 9.2 RiskController

```python
from dataclasses import dataclass
from enum import Enum

class RiskLevel(Enum):
    LOW    = "LOW"
    MEDIUM = "MEDIUM"
    HIGH   = "HIGH"
    BLOCK  = "BLOCK"

@dataclass
class RiskCheck:
    level:         RiskLevel
    requires_hitl: bool
    reason:        str
    block:         bool = False

@dataclass
class RiskPolicy:
    max_auto_spend_eur:    float = 50_000
    max_suppliers_per_rfq: int   = 10
    min_competing_offers:  int   = 2
    max_price_deviation:   float = 0.30     # vs target
    min_parse_confidence:  float = 0.50
    blacklist_domains:     list[str] = None
    required_certs_strict: bool = True       # reject if missing required cert

class RiskController:
    """Central risk evaluation for all agent actions."""

    BLOCKED_DOMAINS = {
        "spam.com", "tempmail.com", "throwaway.email",
        "mailinator.com", "guerrillamail.com",
    }

    def __init__(self, policy: RiskPolicy, db: "asyncpg.Pool"):
        self._policy = policy
        self._db     = db

    async def evaluate_email_send(
        self, supplier: DiscoveredSupplier, rfq: RFQRequest
    ) -> RiskCheck:
        # Check blacklist
        if await self._is_supplier_blacklisted(supplier):
            return RiskCheck(RiskLevel.BLOCK, True, "Supplier on blacklist", block=True)

        # Check domain
        domain = self._extract_domain(supplier.email or "")
        if domain in self.BLOCKED_DOMAINS:
            return RiskCheck(RiskLevel.BLOCK, True, f"Blocked email domain: {domain}", block=True)

        # Check spam cooldown
        if supplier.supplier_id:
            on_cooldown = await self._check_spam_cooldown(supplier.supplier_id, rfq.material_code)
            if on_cooldown:
                return RiskCheck(RiskLevel.HIGH, True, "Spam cooldown active for this supplier/material")

        # Check spend limit
        estimated_spend = rfq.target_price_eur * rfq.quantity
        if estimated_spend > self._policy.max_auto_spend_eur:
            return RiskCheck(
                RiskLevel.HIGH, True,
                f"Estimated spend {estimated_spend:.0f} EUR > auto limit {self._policy.max_auto_spend_eur:.0f} EUR",
            )

        return RiskCheck(RiskLevel.LOW, False, "")

    async def evaluate_offer(
        self, offer: NormalizedOffer, target_price: float
    ) -> RiskCheck:
        # Price anomaly
        if offer.unit_price_eur < target_price * 0.30:
            return RiskCheck(
                RiskLevel.HIGH, True,
                f"Price {offer.unit_price_eur:.2f} EUR is >70% below target — possible error or fraud",
            )
        if offer.unit_price_eur > target_price * 2.0:
            return RiskCheck(
                RiskLevel.MEDIUM, False,
                f"Price {offer.unit_price_eur:.2f} EUR is >2× above target",
            )

        # Parse confidence
        if offer.parse_confidence < self._policy.min_parse_confidence:
            return RiskCheck(
                RiskLevel.MEDIUM, True,
                f"Low parse confidence {offer.parse_confidence:.2f} — manual review recommended",
            )

        # Risk flags from parser
        if offer.risk_flags:
            return RiskCheck(
                RiskLevel.MEDIUM, True,
                f"Offer risk flags: {'; '.join(offer.risk_flags)}",
            )

        return RiskCheck(RiskLevel.LOW, False, "")

    async def evaluate_auto_select(
        self, winner: OfferScore, rfq: RFQRequest, n_competing: int
    ) -> RiskCheck:
        if n_competing < self._policy.min_competing_offers:
            return RiskCheck(
                RiskLevel.HIGH, True,
                f"Only {n_competing} offer(s) received — minimum {self._policy.min_competing_offers} required for auto-select",
            )
        if winner.total_price_eur > self._policy.max_auto_spend_eur:
            return RiskCheck(
                RiskLevel.HIGH, True,
                f"Auto-select blocked: {winner.total_price_eur:.0f} EUR > limit {self._policy.max_auto_spend_eur:.0f} EUR",
            )
        if winner.risk_score > 0.50:
            return RiskCheck(
                RiskLevel.HIGH, True,
                f"Winner risk score {winner.risk_score:.2f} too high for auto-select",
            )
        missing = set(rfq.required_certifications) - set(winner.certifications)
        if missing and self._policy.required_certs_strict:
            return RiskCheck(
                RiskLevel.HIGH, True,
                f"Winner missing required certifications: {', '.join(missing)}",
            )
        return RiskCheck(RiskLevel.LOW, False, "")

    async def _is_supplier_blacklisted(self, supplier: DiscoveredSupplier) -> bool:
        if not supplier.supplier_id:
            return False
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM rfqa.supplier_blacklist WHERE supplier_id = $1 AND active = TRUE",
                supplier.supplier_id,
            )
        return row is not None

    async def _check_spam_cooldown(self, supplier_id: str, material_code: str) -> bool:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT 1 FROM rfqa.email_log
                   WHERE supplier_id = $1 AND material_code = $2
                     AND sent_at > now() - INTERVAL '30 days'
                   LIMIT 1""",
                supplier_id, material_code,
            )
        return row is not None

    @staticmethod
    def _extract_domain(email: str) -> str:
        m = re.search(r"@(.+)$", email)
        return m.group(1).lower() if m else ""
```

### 9.3 Audit Trail

```python
class AuditLogger:
    """Immutable audit log for all agent actions and decisions."""

    def __init__(self, db: "asyncpg.Pool"):
        self._db = db

    async def log(
        self,
        rfq_id:     str,
        event_type: str,
        actor:      str,              # "agent" | "human:{user_id}"
        payload:    dict,
        risk_level: str = "LOW",
    ) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                """INSERT INTO rfqa.audit_log
                   (log_id, rfq_id, event_type, actor, payload, risk_level, created_at)
                   VALUES (gen_random_uuid(), $1, $2, $3, $4, $5, now())""",
                rfq_id, event_type, actor, json.dumps(payload), risk_level,
            )
```

---

## 10. Human-in-the-Loop (HITL)

### 10.1 Architektura HITL

HITL Gateway wstrzymuje agenta, wysyła notyfikację do zakupowca i czeka na decyzję.
Agent jest wznowiony gdy człowiek zatwierdzi/odrzuci/zmodyfikuje decyzję.

```
Agent ──► HITL Request ──► DB (rfqa.hitl_requests)
                │
                ▼
         Notification
         ├─ Email (SES/SMTP)
         ├─ Slack DM
         └─ Procurement Portal (WebSocket push)
                │
                ▼
         Human reviews:
         ├─ Approve (agent continues)
         ├─ Reject (agent cancels RFQ)
         ├─ Modify (agent uses human data)
         └─ Delegate (reassign to another user)
                │
                ▼
         HITL Response ──► Agent resumes
```

### 10.2 HITL Gateway

```python
from dataclasses import dataclass
from enum import Enum
import asyncio
from datetime import datetime, timezone, timedelta

class HITLRequestType(Enum):
    SUPPLIER_LIST_APPROVAL  = "SUPPLIER_LIST_APPROVAL"
    EMAIL_BATCH_APPROVAL    = "EMAIL_BATCH_APPROVAL"
    OFFER_SELECTION         = "OFFER_SELECTION"
    PRICE_ANOMALY           = "PRICE_ANOMALY"
    BUDGET_OVERRUN          = "BUDGET_OVERRUN"
    MISSING_CERTIFICATION   = "MISSING_CERTIFICATION"
    GENERAL_REVIEW          = "GENERAL_REVIEW"

class HITLDecision(Enum):
    APPROVE   = "APPROVE"
    REJECT    = "REJECT"
    MODIFY    = "MODIFY"
    DELEGATE  = "DELEGATE"
    TIMEOUT   = "TIMEOUT"

@dataclass
class HITLRequest:
    request_id:   str
    rfq_id:       str
    request_type: HITLRequestType
    title:        str
    summary:      str
    payload:      dict             # Context data for reviewer
    assigned_to:  str              # user_id or role
    deadline:     datetime
    priority:     int = 2          # 1=urgent, 2=normal, 3=low

@dataclass
class HITLResponse:
    request_id:  str
    decision:    HITLDecision
    reviewer_id: str
    notes:       str
    modified_data: dict | None     # If decision=MODIFY
    responded_at:  datetime

class HITLGateway:

    TIMEOUT_HOURS = 24

    def __init__(
        self,
        db:        "asyncpg.Pool",
        notifier:  "NotificationService",
    ):
        self._db       = db
        self._notifier = notifier
        self._pending:  dict[str, asyncio.Future] = {}

    async def request(
        self,
        rfq_id:       str,
        request_type: HITLRequestType,
        title:        str,
        summary:      str,
        payload:      dict,
        assigned_to:  str,
        priority:     int = 2,
    ) -> HITLResponse:
        req = HITLRequest(
            request_id   = str(uuid.uuid4()),
            rfq_id       = rfq_id,
            request_type = request_type,
            title        = title,
            summary      = summary,
            payload      = payload,
            assigned_to  = assigned_to,
            deadline     = datetime.now(timezone.utc) + timedelta(hours=self.TIMEOUT_HOURS),
            priority     = priority,
        )

        # Persist
        await self._persist_request(req)

        # Notify
        await self._notifier.notify_hitl(req)

        # Create future and wait
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[req.request_id] = future

        try:
            response = await asyncio.wait_for(future, timeout=self.TIMEOUT_HOURS * 3600)
        except asyncio.TimeoutError:
            del self._pending[req.request_id]
            await self._mark_timeout(req.request_id)
            return HITLResponse(
                request_id   = req.request_id,
                decision     = HITLDecision.TIMEOUT,
                reviewer_id  = "system",
                notes        = f"No response within {self.TIMEOUT_HOURS}h",
                modified_data= None,
                responded_at = datetime.now(timezone.utc),
            )
        return response

    async def submit_decision(
        self,
        request_id:   str,
        decision:     HITLDecision,
        reviewer_id:  str,
        notes:        str = "",
        modified_data: dict | None = None,
    ) -> None:
        """Called by Procurement Portal when human makes decision."""
        response = HITLResponse(
            request_id   = request_id,
            decision     = decision,
            reviewer_id  = reviewer_id,
            notes        = notes,
            modified_data= modified_data,
            responded_at = datetime.now(timezone.utc),
        )
        await self._persist_response(response)

        future = self._pending.get(request_id)
        if future and not future.done():
            future.set_result(response)

    async def _persist_request(self, req: HITLRequest) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                """INSERT INTO rfqa.hitl_requests
                   (request_id, rfq_id, request_type, title, summary,
                    payload, assigned_to, deadline, priority, status, created_at)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,'PENDING',now())""",
                req.request_id, req.rfq_id, req.request_type.value,
                req.title, req.summary, json.dumps(req.payload),
                req.assigned_to, req.deadline, req.priority,
            )

    async def _persist_response(self, resp: HITLResponse) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                """UPDATE rfqa.hitl_requests
                   SET status='RESOLVED', decision=$1, reviewer_id=$2,
                       reviewer_notes=$3, modified_data=$4, responded_at=$5
                   WHERE request_id=$6""",
                resp.decision.value, resp.reviewer_id, resp.notes,
                json.dumps(resp.modified_data) if resp.modified_data else None,
                resp.responded_at, resp.request_id,
            )

    async def _mark_timeout(self, request_id: str) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                "UPDATE rfqa.hitl_requests SET status='TIMEOUT' WHERE request_id=$1",
                request_id,
            )
```

### 10.3 Notification Service

```python
import httpx

class NotificationService:
    """Sends HITL notifications via Slack + Email."""

    SLACK_WEBHOOK_URL = "https://hooks.slack.com/services/..."
    PORTAL_WS_URL     = "http://procurement-portal.internal/ws/hitl"

    def __init__(self, smtp: SMTPConfig, http: httpx.AsyncClient):
        self._smtp = smtp
        self._http = http

    async def notify_hitl(self, req: HITLRequest) -> None:
        await asyncio.gather(
            self._notify_slack(req),
            self._notify_email(req),
            return_exceptions=True,
        )

    async def _notify_slack(self, req: HITLRequest) -> None:
        priority_emoji = {1: "🚨", 2: "⚠️", 3: "ℹ️"}.get(req.priority, "⚠️")
        payload = {
            "text": f"{priority_emoji} *HITL Review Required* — RFQ `{req.rfq_id}`",
            "blocks": [
                {"type": "header", "text": {"type": "plain_text", "text": req.title}},
                {"type": "section", "text": {"type": "mrkdwn", "text": req.summary}},
                {"type": "section", "fields": [
                    {"type": "mrkdwn", "text": f"*Type:* {req.request_type.value}"},
                    {"type": "mrkdwn", "text": f"*Deadline:* {req.deadline.strftime('%Y-%m-%d %H:%M UTC')}"},
                    {"type": "mrkdwn", "text": f"*Assigned to:* {req.assigned_to}"},
                ]},
                {"type": "actions", "elements": [
                    {"type": "button", "text": {"type": "plain_text", "text": "Review in Portal"},
                     "url": f"https://procurement.internal/hitl/{req.request_id}",
                     "style": "primary"},
                ]},
            ],
        }
        await self._http.post(self.SLACK_WEBHOOK_URL, json=payload, timeout=5.0)

    async def _notify_email(self, req: HITLRequest) -> None:
        import asyncio
        subject = f"[ACTION REQUIRED] RFQ {req.rfq_id}: {req.title}"
        body    = f"""
        <h2>Human Review Required</h2>
        <p><strong>RFQ:</strong> {req.rfq_id}</p>
        <p><strong>Type:</strong> {req.request_type.value}</p>
        <p><strong>Summary:</strong> {req.summary}</p>
        <p><strong>Deadline:</strong> {req.deadline.strftime('%Y-%m-%d %H:%M UTC')}</p>
        <p><a href="https://procurement.internal/hitl/{req.request_id}">
           Click here to review and approve/reject
        </a></p>
        """
        # Simplified SMTP send (uses EmailDispatcher internally)
        await asyncio.to_thread(self._send_smtp_notification, req.assigned_to, subject, body)

    def _send_smtp_notification(self, to: str, subject: str, body: str) -> None:
        context = ssl.create_default_context()
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = self._smtp.from_email
        msg["To"]      = to
        msg.attach(MIMEText(body, "html"))
        with smtplib.SMTP(self._smtp.host, self._smtp.port) as s:
            if self._smtp.use_tls:
                s.starttls(context=context)
            if self._smtp.username:
                s.login(self._smtp.username, self._smtp.password)
            s.sendmail(self._smtp.from_email, [to], msg.as_string())
```

### 10.4 HITL Decision Matrix

| Sytuacja | Typ HITL | Priorytet | Timeout | Auto-action po timeout |
|----------|----------|-----------|---------|----------------------|
| Lista dostawców > 5 | SUPPLIER_LIST_APPROVAL | Normal | 24h | Cancel RFQ |
| Wydatek > €50K | EMAIL_BATCH_APPROVAL | Urgent | 4h | Cancel dispatch |
| Brak wymaganych certyfikatów | MISSING_CERTIFICATION | Normal | 24h | Skip supplier |
| Anomalia cenowa (< 30% target) | PRICE_ANOMALY | Urgent | 4h | Reject offer |
| Przekroczenie budżetu | BUDGET_OVERRUN | Urgent | 4h | Cancel selection |
| Pewność porównania < 0.70 | OFFER_SELECTION | Normal | 24h | Extend deadline |
| Tylko 1 oferta | GENERAL_REVIEW | Normal | 48h | Accept with note |

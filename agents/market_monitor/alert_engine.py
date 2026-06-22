"""
Section 8 — Alert Engine

Multi-channel alert delivery system for market events.

Alert flow:
  1. ShockDetectionEngine fires MarketShock
  2. AlertRuleEvaluator checks tenant alert rules → generates Alert
  3. AlertDeduplicator checks cooldown → suppresses duplicates
  4. AlertRouter dispatches to configured channels:
       email     — HTML email via SMTP (SendGrid or tenant SMTP)
       slack     — Webhook to Slack channel
       webhook   — HTTP POST to tenant-defined endpoint
       sms       — Twilio SMS (for CRITICAL only)
       in_app    — WebSocket push to dashboard (always enabled)
  5. AlertLogger persists all alerts (PostgreSQL) + emits domain event
  6. AlertAcknowledger — handles ACK / snooze / resolve lifecycle

Alert rule evaluation:
  Rules evaluate per-commodity on every price tick / batch refresh.
  Conditions: price_above, price_below, pct_change_1d, vol_above, shock_type
  Cooldown enforced per (tenant, rule_id) to prevent alert storms.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import structlog

from .models import (
    Alert,
    AlertRule,
    AlertSeverity,
    AlertStatus,
    CommodityCode,
    MarketIndicators,
    MarketShock,
)

logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Rule evaluator
# ─────────────────────────────────────────────────────────────────────────────

class AlertRuleEvaluator:
    """
    Evaluates alert rules against current market indicators and shocks.
    Returns list of alerts to fire.
    """

    def evaluate_price_rules(
        self,
        rule:       AlertRule,
        commodity:  CommodityCode,
        price:      float,
        indicators: MarketIndicators | None = None,
    ) -> Alert | None:
        if rule.commodity and rule.commodity != commodity:
            return None
        if not rule.enabled:
            return None

        reasons = []

        if rule.price_above is not None and price > rule.price_above:
            reasons.append(f"price {price:.2f} > threshold {rule.price_above:.2f}")

        if rule.price_below is not None and price < rule.price_below:
            reasons.append(f"price {price:.2f} < threshold {rule.price_below:.2f}")

        if indicators:
            if rule.chg_1d_above is not None and indicators.chg_1d_pct is not None:
                if indicators.chg_1d_pct > rule.chg_1d_above:
                    reasons.append(f"1d change {indicators.chg_1d_pct:+.1f}% > {rule.chg_1d_above:.1f}%")

            if rule.chg_1d_below is not None and indicators.chg_1d_pct is not None:
                if indicators.chg_1d_pct < rule.chg_1d_below:
                    reasons.append(f"1d change {indicators.chg_1d_pct:+.1f}% < {rule.chg_1d_below:.1f}%")

            if rule.vol_above is not None and indicators.hist_vol_20 is not None:
                if indicators.hist_vol_20 > rule.vol_above:
                    reasons.append(f"20d vol {indicators.hist_vol_20:.1f}% > {rule.vol_above:.1f}%")

        if not reasons:
            return None

        title = f"[{rule.severity}] {commodity.value}: {rule.name}"
        body  = "; ".join(reasons)

        return Alert(
            rule_id   = rule.rule_id,
            tenant_id = rule.tenant_id,
            commodity = commodity,
            severity  = AlertSeverity(rule.severity),
            title     = title,
            body      = body,
            metadata  = {"price": price, "reasons": reasons},
        )

    def evaluate_shock(
        self,
        rule:  AlertRule,
        shock: MarketShock,
    ) -> Alert | None:
        if not rule.enabled:
            return None
        if rule.commodity and rule.commodity != shock.commodity:
            return None
        if not rule.shock_types:
            return None
        if shock.shock_type not in rule.shock_types:
            return None

        severity = AlertSeverity.CRITICAL if shock.magnitude >= 3.0 else AlertSeverity.WARNING

        return Alert(
            rule_id   = rule.rule_id,
            tenant_id = rule.tenant_id,
            commodity = shock.commodity,
            severity  = severity,
            title     = f"[SHOCK] {shock.commodity.value if shock.commodity else 'Market'}: {shock.shock_type.value}",
            body      = shock.description,
            metadata  = {
                "shock_type": shock.shock_type.value,
                "magnitude":  shock.magnitude,
                "confidence": shock.confidence,
            },
        )


# ─────────────────────────────────────────────────────────────────────────────
# Deduplication / cooldown
# ─────────────────────────────────────────────────────────────────────────────

class AlertDeduplicator:
    """
    Suppresses re-firing of the same alert rule within cooldown period.
    Uses in-memory dict; in production back with Redis.
    """

    def __init__(self) -> None:
        # (tenant_id, rule_id) → last_fired Unix timestamp
        self._last_fired: dict[tuple[str, str], float] = {}

    def should_fire(self, alert: Alert, cooldown_min: int) -> bool:
        key       = (alert.tenant_id, alert.rule_id)
        last      = self._last_fired.get(key, 0)
        elapsed_m = (time.time() - last) / 60.0
        if elapsed_m < cooldown_min:
            logger.debug(
                "alert_suppressed_cooldown",
                rule_id    = alert.rule_id,
                remaining_m = round(cooldown_min - elapsed_m, 1),
            )
            return False
        return True

    def mark_fired(self, alert: Alert) -> None:
        self._last_fired[(alert.tenant_id, alert.rule_id)] = time.time()


# ─────────────────────────────────────────────────────────────────────────────
# Channel senders
# ─────────────────────────────────────────────────────────────────────────────

class SlackSender:
    """Sends alert to Slack via Incoming Webhook."""

    def __init__(self, webhook_url: str) -> None:
        self._url = webhook_url

    async def send(self, alert: Alert) -> None:
        colour = {"INFO": "#36a64f", "WARNING": "#ff9900", "CRITICAL": "#cc0000"}.get(
            alert.severity.value, "#aaaaaa"
        )
        payload = {
            "attachments": [{
                "color":  colour,
                "title":  alert.title,
                "text":   alert.body,
                "footer": f"ICI Market Monitor | {alert.triggered_at.strftime('%Y-%m-%d %H:%M UTC')}",
                "fields": [
                    {"title": "Commodity", "value": alert.commodity.value if alert.commodity else "Market", "short": True},
                    {"title": "Severity",  "value": alert.severity.value, "short": True},
                ],
            }]
        }
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(self._url, json=payload)
            resp.raise_for_status()
        logger.info("alert_slack_sent", alert_id=alert.alert_id)


class WebhookSender:
    """Generic HTTP POST to a tenant-defined webhook endpoint."""

    def __init__(self, url: str, secret: str = "") -> None:
        self._url    = url
        self._secret = secret

    async def send(self, alert: Alert) -> None:
        payload = {
            "alert_id":    alert.alert_id,
            "commodity":   alert.commodity.value if alert.commodity else None,
            "severity":    alert.severity.value,
            "status":      alert.status.value,
            "title":       alert.title,
            "body":        alert.body,
            "triggered_at": alert.triggered_at.isoformat(),
            "metadata":    alert.metadata,
        }
        headers = {"Content-Type": "application/json"}
        if self._secret:
            import hashlib, hmac
            sig = hmac.new(
                self._secret.encode(), json.dumps(payload).encode(), hashlib.sha256
            ).hexdigest()
            headers["X-ICI-Signature"] = f"sha256={sig}"

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(self._url, json=payload, headers=headers)
            resp.raise_for_status()
        logger.info("alert_webhook_sent", alert_id=alert.alert_id, url=self._url)


class EmailSender:
    """Sends HTML email via SMTP (uses Python smtplib)."""

    def __init__(self, smtp_host: str, smtp_port: int, username: str, password: str,
                 from_addr: str) -> None:
        self._host     = smtp_host
        self._port     = smtp_port
        self._username = username
        self._password = password
        self._from     = from_addr

    async def send(self, alert: Alert, to: list[str]) -> None:
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        sev_emoji = {"INFO": "ℹ️", "WARNING": "⚠️", "CRITICAL": "🚨"}.get(alert.severity.value, "📊")
        subject   = f"{sev_emoji} {alert.title}"
        html      = f"""
        <html><body>
        <h2 style='color:{'#cc0000' if alert.severity==AlertSeverity.CRITICAL else '#ff9900'}'>{alert.title}</h2>
        <p>{alert.body}</p>
        <hr/>
        <p><small>ICI Market Monitor | {alert.triggered_at.strftime('%Y-%m-%d %H:%M UTC')}</small></p>
        </body></html>
        """
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = self._from
        msg["To"]      = ", ".join(to)
        msg.attach(MIMEText(html, "html"))

        def _send_blocking() -> None:
            with smtplib.SMTP(self._host, self._port) as server:
                server.starttls()
                server.login(self._username, self._password)
                server.sendmail(self._from, to, msg.as_string())

        await asyncio.get_event_loop().run_in_executor(None, _send_blocking)
        logger.info("alert_email_sent", alert_id=alert.alert_id, recipients=len(to))


# ─────────────────────────────────────────────────────────────────────────────
# Alert Router
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ChannelConfig:
    slack_webhook:  str | None = None
    webhook_url:    str | None = None
    webhook_secret: str = ""
    email_to:       list[str] = field(default_factory=list)
    smtp_host:      str = ""
    smtp_port:      int = 587
    smtp_user:      str = ""
    smtp_pass:      str = ""
    smtp_from:      str = "noreply@ici.example.com"


class AlertRouter:
    """Routes alerts to all configured channels."""

    def __init__(self, config: ChannelConfig) -> None:
        self._cfg     = config
        self._slack   = SlackSender(config.slack_webhook) if config.slack_webhook else None
        self._webhook = WebhookSender(config.webhook_url, config.webhook_secret) if config.webhook_url else None
        self._email   = (
            EmailSender(config.smtp_host, config.smtp_port, config.smtp_user,
                        config.smtp_pass, config.smtp_from)
            if config.smtp_host else None
        )
        # In-app alert buffer (consumed by WebSocket)
        self._inapp_buffer: list[Alert] = []

    async def dispatch(self, alert: Alert, channels: list[str]) -> None:
        tasks = []
        if "slack" in channels and self._slack:
            tasks.append(self._slack.send(alert))
        if "webhook" in channels and self._webhook:
            tasks.append(self._webhook.send(alert))
        if "email" in channels and self._email and self._cfg.email_to:
            tasks.append(self._email.send(alert, self._cfg.email_to))

        # Always push to in-app buffer
        self._inapp_buffer.append(alert)
        if len(self._inapp_buffer) > 1000:
            self._inapp_buffer = self._inapp_buffer[-500:]

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                logger.warning("alert_channel_error", error=str(r))

    def get_inapp_alerts(self, since: datetime | None = None) -> list[Alert]:
        if since is None:
            return list(self._inapp_buffer[-50:])
        return [a for a in self._inapp_buffer if a.triggered_at >= since]


# ─────────────────────────────────────────────────────────────────────────────
# Alert Manager (façade)
# ─────────────────────────────────────────────────────────────────────────────

class AlertManager:
    """
    Top-level alert orchestrator.
    Runs rule evaluation → deduplication → routing → persistence.
    """

    def __init__(
        self,
        router:       AlertRouter,
        rules:        list[AlertRule] | None = None,
        event_pub:    Any = None,   # MarketEventPublisher (optional)
    ) -> None:
        self._router    = router
        self._rules     = rules or []
        self._event_pub = event_pub
        self._dedup     = AlertDeduplicator()
        self._evaluator = AlertRuleEvaluator()
        self._store:    list[Alert] = []   # In-memory; replace with DB

    def add_rule(self, rule: AlertRule) -> None:
        self._rules.append(rule)

    def remove_rule(self, rule_id: str) -> None:
        self._rules = [r for r in self._rules if r.rule_id != rule_id]

    async def process_price_update(
        self,
        commodity:  CommodityCode,
        price:      float,
        indicators: MarketIndicators | None = None,
    ) -> list[Alert]:
        fired = []
        for rule in self._rules:
            alert = self._evaluator.evaluate_price_rules(rule, commodity, price, indicators)
            if alert and self._dedup.should_fire(alert, rule.cooldown_min):
                self._dedup.mark_fired(alert)
                await self._dispatch(alert, rule.channels)
                fired.append(alert)
        return fired

    async def process_shock(self, shock: MarketShock) -> list[Alert]:
        fired = []
        for rule in self._rules:
            alert = self._evaluator.evaluate_shock(rule, shock)
            if alert and self._dedup.should_fire(alert, rule.cooldown_min):
                self._dedup.mark_fired(alert)
                await self._dispatch(alert, rule.channels or ["in_app"])
                fired.append(alert)
        return fired

    async def _dispatch(self, alert: Alert, channels: list[str]) -> None:
        self._store.append(alert)
        await self._router.dispatch(alert, channels)
        logger.info(
            "alert_fired",
            alert_id  = alert.alert_id,
            severity  = alert.severity.value,
            commodity = alert.commodity.value if alert.commodity else None,
            title     = alert.title,
        )

    def acknowledge(self, alert_id: str, user_id: str) -> Alert | None:
        for a in self._store:
            if a.alert_id == alert_id:
                a.status           = AlertStatus.ACKNOWLEDGED
                a.acknowledged_by  = user_id
                a.acknowledged_at  = datetime.now(timezone.utc)
                return a
        return None

    def resolve(self, alert_id: str) -> Alert | None:
        for a in self._store:
            if a.alert_id == alert_id:
                a.status       = AlertStatus.RESOLVED
                a.resolved_at  = datetime.now(timezone.utc)
                return a
        return None

    def get_active_alerts(
        self,
        tenant_id: str | None = None,
        commodity: CommodityCode | None = None,
    ) -> list[Alert]:
        alerts = [a for a in self._store if a.status == AlertStatus.ACTIVE]
        if tenant_id:
            alerts = [a for a in alerts if a.tenant_id == tenant_id]
        if commodity:
            alerts = [a for a in alerts if a.commodity == commodity]
        return sorted(alerts, key=lambda a: a.triggered_at, reverse=True)

    def summary(self) -> dict[str, Any]:
        active = [a for a in self._store if a.status == AlertStatus.ACTIVE]
        return {
            "total_alerts":    len(self._store),
            "active":          len(active),
            "critical":        sum(1 for a in active if a.severity == AlertSeverity.CRITICAL),
            "warning":         sum(1 for a in active if a.severity == AlertSeverity.WARNING),
            "info":            sum(1 for a in active if a.severity == AlertSeverity.INFO),
            "rule_count":      len(self._rules),
        }

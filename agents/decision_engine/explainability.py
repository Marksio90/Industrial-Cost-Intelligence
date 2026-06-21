"""
Explainability Layer — generates human-readable Explanation objects for recommendations.
"""
from __future__ import annotations
import time
from typing import Any
from .models import (
    DecisionContext, DecisionNode, Recommendation, RecommendationType,
    Explanation, Evidence, EvidenceType, ImpactEstimate, ImpactDimension,
    CounterfactualCase, DataQuality, UrgencyLevel,
)
from .decision_graph import TraversalResult

_DIM_LABELS = {
    ImpactDimension.COST: "Koszt",
    ImpactDimension.QUALITY: "Jakość",
    ImpactDimension.DELIVERY: "Terminowość",
    ImpactDimension.RISK: "Ryzyko",
    ImpactDimension.SUSTAINABILITY: "Zrównoważony rozwój",
    ImpactDimension.CASH_FLOW: "Przepływ gotówki",
    ImpactDimension.FLEXIBILITY: "Elastyczność",
}

class ExplainabilityEngine:
    def explain(self, rec: Recommendation, ctx: DecisionContext, signals: dict[str, Any], traversal: TraversalResult) -> Explanation:
        summary = self._build_summary(rec, ctx, signals)
        rationale = self._build_rationale(rec, ctx, signals)
        evidence = self._build_evidence(rec, ctx, signals)
        impacts = self._build_impacts(rec, ctx, signals)
        counterfactuals = self._build_counterfactuals(rec, ctx, signals)
        assumptions = self._build_assumptions(rec, ctx)
        caveats = self._build_caveats(rec, ctx, signals)
        similar_cases = self._similar_cases(rec)

        return Explanation(
            summary=summary,
            rationale=rationale,
            evidence=evidence,
            impacts=impacts,
            counterfactuals=counterfactuals,
            assumptions=assumptions,
            caveats=caveats,
            similar_cases=similar_cases,
        )

    def _build_summary(self, rec: Recommendation, ctx: DecisionContext, signals: dict[str, Any]) -> str:
        saving = rec.expected_saving_eur
        pct = rec.expected_saving_pct
        urgency_label = {
            UrgencyLevel.CRITICAL: "Krytyczna",
            UrgencyLevel.HIGH: "Wysoka",
            UrgencyLevel.MEDIUM: "Średnia",
            UrgencyLevel.LOW: "Niska",
            UrgencyLevel.WATCH: "Obserwacja",
        }.get(rec.urgency, "")

        templates = {
            RecommendationType.CHANGE_SUPPLIER: f"Zmiana dostawcy może przynieść oszczędności {saving:,.0f} EUR ({pct:.1f}%). Obecny dostawca wykazuje podwyższone ryzyko operacyjne.",
            RecommendationType.BUY_NOW: f"Natychmiastowy zakup pozwoli uniknąć wzrostu kosztów szacowanego na {saving:,.0f} EUR. Sygnały rynkowe wskazują na dalszy wzrost cen.",
            RecommendationType.WAIT: f"Odkładając zakup można zaoszczędzić {saving:,.0f} EUR ({pct:.1f}%). Trend cenowy jest spadkowy.",
            RecommendationType.HEDGE_FX: f"Zabezpieczenie walutowe ograniczy ekspozycję FX szacowaną na {saving:,.0f} EUR. Kurs wykazuje podwyższoną zmienność.",
            RecommendationType.DUAL_SOURCE: f"Wprowadzenie dual-sourcingu zmniejszy ryzyko jednodostawcowe i może poprawić warunki cenowe.",
            RecommendationType.RENEGOTIATE: f"Renegocjacja kontraktu ma potencjał oszczędności {saving:,.0f} EUR ({pct:.1f}%).",
            RecommendationType.INCREASE_MOQ: f"Zwiększenie MOQ pozwoli uzyskać rabaty wolumenowe — potencjalna oszczędność {saving:,.0f} EUR.",
            RecommendationType.DECREASE_MOQ: f"Zmniejszenie MOQ uwolni kapitał obrotowy i zredukuje ryzyko nadmiernych zapasów.",
            RecommendationType.CHANGE_MATERIAL: f"Zmiana materiału na alternatywny może obniżyć koszty o {saving:,.0f} EUR ({pct:.1f}%) przy zachowaniu wymagań technicznych.",
            RecommendationType.CHANGE_PROCESS: f"Optymalizacja procesu produkcji obniży koszty przetworzenia o szacowane {saving:,.0f} EUR.",
            RecommendationType.INCREASE_STOCK: f"Zwiększenie zapasów zabezpieczy ciągłość produkcji przy prognozowanym wzroście cen.",
            RecommendationType.REDUCE_STOCK: f"Redukcja zapasów uwolni {saving:,.0f} EUR zamrożonego kapitału.",
            RecommendationType.QUALIFY_ALTERNATIVE: f"Kwalifikacja alternatywnego dostawcy jest konieczna — brak alternatyw przy ryzykownym obecnym dostawcy.",
            RecommendationType.ESCALATE: f"Sytuacja wymaga decyzji zarządczej. Skala impaktu przekracza progi operacyjne ({urgency_label}).",
            RecommendationType.NO_ACTION: "Sytuacja jest stabilna. Kontynuuj monitoring KPI.",
        }
        return templates.get(rec.rec_type, f"Rekomendacja: {rec.title}. Potencjalna oszczędność: {saving:,.0f} EUR.")

    def _build_rationale(self, rec: Recommendation, ctx: DecisionContext, signals: dict[str, Any]) -> list[str]:
        points = []
        rt = rec.rec_type

        if ctx.cost:
            trend = signals.get("cost.trend_pct", 0)
            if trend > 0:
                points.append(f"Trend kosztów materiału: +{trend:.1f}% (wzrostowy)")
            vs_bm = signals.get("cost.vs_benchmark_pct", 0)
            if vs_bm > 0:
                points.append(f"Koszt jest {vs_bm:.1f}% powyżej benchmarku rynkowego")

        if ctx.supplier:
            risk = signals.get("supplier.composite_risk", 0)
            if risk > 0.5:
                points.append(f"Ryzyko kompozytowe dostawcy: {risk:.2f} (próg: 0.5)")
            otd = ctx.supplier.otd_pct
            if otd < 95:
                points.append(f"Terminowość dostaw (OTD): {otd:.1f}% (poniżej 95%)")
            if ctx.supplier.single_source:
                points.append("Dostawca jest jedynym źródłem (single-source) — wysokie ryzyko koncentracji")
            expiry = ctx.supplier.contract_expiry_days
            if expiry is not None and expiry < 90:
                points.append(f"Kontrakt wygasa za {expiry} dni — konieczna renegocjacja")

        if ctx.market:
            vol = ctx.market.price_volatility_30d
            if vol > 0.1:
                points.append(f"Zmienność cenowa (30d): {vol:.1%} — podwyższona")
            disruption = ctx.market.supply_disruption_risk
            if disruption > 0.3:
                points.append(f"Ryzyko zakłócenia podaży: {disruption:.1%}")
            if rt == RecommendationType.HEDGE_FX:
                points.append(f"Kurs EUR/USD: {ctx.market.fx_eur_usd:.4f}, trend: {ctx.market.fx_trend}")

        if ctx.production:
            if signals.get("production.below_safety_stock"):
                points.append(f"Poziom zapasów poniżej safety stock ({ctx.production.current_inventory_days:.0f}d < {ctx.production.safety_stock_days:.0f}d)")
            if ctx.production.scrap_rate_pct > 3:
                points.append(f"Wskaźnik braków: {ctx.production.scrap_rate_pct:.1f}% (powyżej normy 3%)")

        if not points:
            points.append("Analiza sygnałów rynkowych i operacyjnych wskazuje na podjęcie działania")

        return points

    def _build_evidence(self, rec: Recommendation, ctx: DecisionContext, signals: dict[str, Any]) -> list[Evidence]:
        ev = []
        eid = 0

        def add(etype, desc, val, unit, thr=None, dir_="above", src="system", q=DataQuality.ESTIMATED):
            nonlocal eid
            eid += 1
            e = Evidence(
                evidence_id=f"ev_{eid:03d}",
                evidence_type=etype,
                description=desc,
                value=val,
                unit=unit,
                threshold=thr,
                direction=dir_,
                weight=1.0,
                source=src,
                quality=q,
                timestamp=time.time(),
            )
            ev.append(e)

        if ctx.market:
            add(EvidenceType.MARKET_SIGNAL, "Cena spot", ctx.market.spot_price_eur, "EUR/unit", src="market_feed", q=ctx.market.data_quality)
            if ctx.market.price_volatility_30d > 0.1:
                add(EvidenceType.PRICE_TREND, "Zmienność 30d", ctx.market.price_volatility_30d, "%", thr=0.1, dir_="above", src="market_feed")

        if ctx.supplier:
            add(EvidenceType.SUPPLIER_RISK, "Ryzyko dostawcy", signals.get("supplier.composite_risk", 0), "score", thr=0.5, src="supplier_db", q=ctx.supplier.data_quality)
            add(EvidenceType.DELIVERY_PERFORMANCE, "OTD", ctx.supplier.otd_pct, "%", thr=95, dir_="below", src="erp", q=ctx.supplier.data_quality)

        if ctx.cost:
            add(EvidenceType.COST_DRIVER, "Koszt jednostkowy", ctx.cost.current_unit_cost, "EUR", thr=ctx.cost.benchmark_cost, src="cost_model", q=ctx.cost.data_quality)

        if ctx.production and signals.get("production.below_safety_stock"):
            add(EvidenceType.INVENTORY_LEVEL, "Zapasy", ctx.production.current_inventory_days, "dni", thr=ctx.production.safety_stock_days, dir_="below", src="wms")

        return ev

    def _build_impacts(self, rec: Recommendation, ctx: DecisionContext, signals: dict[str, Any]) -> list[ImpactEstimate]:
        saving = rec.expected_saving_eur
        impacts = [
            ImpactEstimate(dimension=ImpactDimension.COST, delta_eur=saving, delta_pct=rec.expected_saving_pct, description="Bezpośrednia redukcja kosztów", uncertainty=0.15),
        ]
        rt = rec.rec_type
        if rt == RecommendationType.CHANGE_SUPPLIER:
            impacts.append(ImpactEstimate(ImpactDimension.DELIVERY, 0, -2, "Ryzyko tymczasowych opóźnień przy zmianie dostawcy", 0.3))
            impacts.append(ImpactEstimate(ImpactDimension.RISK, 0, -15, "Redukcja ryzyka koncentracji", 0.2))
        if rt == RecommendationType.BUY_NOW:
            impacts.append(ImpactEstimate(ImpactDimension.CASH_FLOW, -saving * 0.5, -5, "Czasowe zamrożenie kapitału", 0.1))
            impacts.append(ImpactEstimate(ImpactDimension.RISK, 0, -10, "Zabezpieczenie przed wzrostem cen", 0.15))
        if rt == RecommendationType.DUAL_SOURCE:
            impacts.append(ImpactEstimate(ImpactDimension.FLEXIBILITY, 0, 20, "Zwiększona elastyczność łańcucha dostaw", 0.2))
            impacts.append(ImpactEstimate(ImpactDimension.RISK, 0, -25, "Eliminacja ryzyka single-source", 0.1))
        return impacts

    def _build_counterfactuals(self, rec: Recommendation, ctx: DecisionContext, signals: dict[str, Any]) -> list[CounterfactualCase]:
        saving = rec.expected_saving_eur
        return [
            CounterfactualCase(
                scenario="Brak działania",
                cost_delta_eur=-saving,
                risk_delta=0.1,
                probability=0.7,
                time_horizon_days=90,
            ),
            CounterfactualCase(
                scenario="Działanie za 3 miesiące",
                cost_delta_eur=-saving * 0.5,
                risk_delta=0.05,
                probability=0.5,
                time_horizon_days=180,
            ),
        ]

    def _build_assumptions(self, rec: Recommendation, ctx: DecisionContext) -> list[str]:
        a = ["Dane wejściowe odzwierciedlają aktualny stan operacyjny"]
        if ctx.market:
            a.append(f"Prognoza cenowa oparta na trendzie {ctx.market.trend_direction} z siłą {ctx.market.trend_strength:.2f}")
        a.append("Wdrożenie rekomendacji jest wykonalne operacyjnie w horyzoncie 90 dni")
        return a

    def _build_caveats(self, rec: Recommendation, ctx: DecisionContext, signals: dict[str, Any]) -> list[str]:
        c = []
        missing = []
        if not ctx.cost: missing.append("danych kosztowych")
        if not ctx.supplier: missing.append("danych dostawcy")
        if not ctx.market: missing.append("danych rynkowych")
        if missing:
            c.append(f"Analiza niepełna — brak: {', '.join(missing)}")
        if rec.confidence.overall < 0.6:
            c.append("Niski poziom pewności — zalecana dodatkowa weryfikacja")
        return c

    def _similar_cases(self, rec: Recommendation) -> list[str]:
        templates = {
            RecommendationType.CHANGE_SUPPLIER: ["Zmiana dostawcy stali Q1-2024: oszczędność 8%", "Dual-sourcing plastiku 2023: redukcja ryzyka 40%"],
            RecommendationType.BUY_NOW: ["Zakup aluminium przed cłami 2023: uniknięto wzrostu 12%", "Forward buying miedzi Q4-2022: oszczędność 180k EUR"],
            RecommendationType.HEDGE_FX: ["Hedging USD/EUR Q2-2024: ochrona 3% marży"],
        }
        return templates.get(rec.rec_type, [])

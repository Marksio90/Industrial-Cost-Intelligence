"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-06-21 00:00:00.000000
"""
from __future__ import annotations
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS ici")

    op.create_table(
        "materials",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("material_number", sa.String(64), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("material_class", sa.String(32), nullable=False),
        sa.Column("base_unit", sa.String(8), nullable=False),
        sa.Column("density_g_cm3", sa.Numeric(10, 4), nullable=True),
        sa.Column("price_eur", sa.Numeric(18, 4), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="EUR"),
        sa.Column("lead_time_days", sa.Integer, nullable=False, server_default="0"),
        sa.Column("min_order_qty", sa.Numeric(18, 4), nullable=False, server_default="1"),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("is_deleted", sa.Boolean, server_default="false"),
        schema="ici",
    )
    op.create_index("ix_materials_tenant_id", "materials", ["tenant_id"], schema="ici")
    op.create_index("ix_materials_material_number", "materials", ["material_number"], schema="ici")
    op.create_unique_constraint("uq_materials_tenant_number", "materials", ["tenant_id", "material_number"], schema="ici")

    op.create_table(
        "suppliers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("country_code", sa.String(2), nullable=False),
        sa.Column("city", sa.String(128), nullable=False),
        sa.Column("address_line1", sa.String(256), nullable=False, server_default=""),
        sa.Column("address_line2", sa.String(256), nullable=True),
        sa.Column("postal_code", sa.String(16), nullable=False, server_default=""),
        sa.Column("contact_email", sa.String(256), nullable=False, server_default=""),
        sa.Column("contact_phone", sa.String(32), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("quality_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("delivery_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("price_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("financial_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("suspension_reason", sa.String(512), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("is_deleted", sa.Boolean, server_default="false"),
        schema="ici",
    )
    op.create_index("ix_suppliers_tenant_id", "suppliers", ["tenant_id"], schema="ici")
    op.create_unique_constraint("uq_suppliers_tenant_code", "suppliers", ["tenant_id", "code"], schema="ici")

    op.create_table(
        "manufacturing_processes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("process_type", sa.String(32), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("machine_rate_eur_hr", sa.Numeric(10, 4), nullable=False),
        sa.Column("labor_rate_eur_hr", sa.Numeric(10, 4), nullable=False),
        sa.Column("cycle_time_seconds", sa.Numeric(10, 2), nullable=False),
        sa.Column("setup_time_seconds", sa.Numeric(10, 2), nullable=False),
        sa.Column("scrap_rate", sa.Numeric(6, 5), nullable=False),
        sa.Column("is_active", sa.Boolean, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("is_deleted", sa.Boolean, server_default="false"),
        schema="ici",
    )
    op.create_index("ix_manufacturing_processes_tenant_id", "manufacturing_processes", ["tenant_id"], schema="ici")
    op.create_unique_constraint("uq_processes_tenant_code", "manufacturing_processes", ["tenant_id", "code"], schema="ici")

    op.create_table(
        "cost_breakdowns",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("material_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reference_quantity", sa.Numeric(18, 4), nullable=False),
        sa.Column("total_cost_eur", sa.Numeric(18, 4), nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("notes", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("is_deleted", sa.Boolean, server_default="false"),
        schema="ici",
    )
    op.create_index("ix_cost_breakdowns_tenant_id", "cost_breakdowns", ["tenant_id"], schema="ici")
    op.create_index("ix_cost_breakdowns_material_id", "cost_breakdowns", ["material_id"], schema="ici")

    op.create_table(
        "cost_components",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("breakdown_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ici.cost_breakdowns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("component_type", sa.String(16), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("cost_eur", sa.Numeric(18, 4), nullable=False),
        sa.Column("share_pct", sa.Numeric(6, 3), nullable=False),
        sa.Column("notes", sa.String(512), nullable=False, server_default=""),
        schema="ici",
    )
    op.create_index("ix_cost_components_breakdown_id", "cost_components", ["breakdown_id"], schema="ici")

    op.create_table(
        "rfqs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("rfq_number", sa.String(64), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="DRAFT"),
        sa.Column("deadline", sa.Date, nullable=False),
        sa.Column("delivery_date", sa.Date, nullable=True),
        sa.Column("delivery_terms", sa.String(16), nullable=False, server_default="DAP"),
        sa.Column("payment_terms", sa.String(8), nullable=False, server_default="NET30"),
        sa.Column("currency", sa.String(3), nullable=False, server_default="EUR"),
        sa.Column("notes", sa.Text, nullable=False, server_default=""),
        sa.Column("awarded_supplier_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("is_deleted", sa.Boolean, server_default="false"),
        schema="ici",
    )
    op.create_index("ix_rfqs_tenant_id", "rfqs", ["tenant_id"], schema="ici")
    op.create_unique_constraint("uq_rfqs_tenant_number", "rfqs", ["tenant_id", "rfq_number"], schema="ici")

    op.create_table(
        "rfq_line_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("rfq_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ici.rfqs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("material_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("description", sa.String(512), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False),
        sa.Column("unit_of_measure", sa.String(8), nullable=False),
        sa.Column("target_price_eur", sa.Numeric(18, 4), nullable=True),
        schema="ici",
    )
    op.create_index("ix_rfq_line_items_rfq_id", "rfq_line_items", ["rfq_id"], schema="ici")

    op.create_table(
        "rfq_suppliers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("rfq_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ici.rfqs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("supplier_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invited_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        schema="ici",
    )
    op.create_unique_constraint("uq_rfq_suppliers", "rfq_suppliers", ["rfq_id", "supplier_id"], schema="ici")

    op.create_table(
        "quotes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("rfq_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("supplier_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quote_number", sa.String(64), nullable=False),
        sa.Column("validity_date", sa.Date, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="RECEIVED"),
        sa.Column("currency", sa.String(3), nullable=False, server_default="EUR"),
        sa.Column("delivery_terms", sa.String(16), nullable=False, server_default="DAP"),
        sa.Column("payment_terms", sa.String(8), nullable=False, server_default="NET30"),
        sa.Column("notes", sa.Text, nullable=False, server_default=""),
        sa.Column("rejection_reason", sa.String(1024), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("is_deleted", sa.Boolean, server_default="false"),
        schema="ici",
    )
    op.create_index("ix_quotes_tenant_id", "quotes", ["tenant_id"], schema="ici")
    op.create_index("ix_quotes_rfq_id", "quotes", ["rfq_id"], schema="ici")
    op.create_index("ix_quotes_supplier_id", "quotes", ["supplier_id"], schema="ici")

    op.create_table(
        "quote_line_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("quote_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ici.quotes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("material_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("description", sa.String(512), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False),
        sa.Column("unit_of_measure", sa.String(8), nullable=False),
        sa.Column("unit_price_eur", sa.Numeric(18, 4), nullable=False),
        sa.Column("total_price_eur", sa.Numeric(18, 4), nullable=False),
        sa.Column("lead_time_days", sa.Integer, nullable=False),
        sa.Column("notes", sa.String(1024), nullable=False, server_default=""),
        schema="ici",
    )
    op.create_index("ix_quote_line_items_quote_id", "quote_line_items", ["quote_id"], schema="ici")

    op.create_table(
        "price_forecasts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("material_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("horizon", sa.String(8), nullable=False),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="PENDING"),
        sa.Column("model_version", sa.String(32), nullable=False, server_default="1.0"),
        sa.Column("notes", sa.Text, nullable=False, server_default=""),
        sa.Column("mae", sa.Numeric(18, 6), nullable=True),
        sa.Column("mape", sa.Numeric(10, 6), nullable=True),
        sa.Column("rmse", sa.Numeric(18, 6), nullable=True),
        sa.Column("r2", sa.Numeric(10, 6), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean, server_default="false"),
        schema="ici",
    )
    op.create_index("ix_price_forecasts_tenant_id", "price_forecasts", ["tenant_id"], schema="ici")
    op.create_index("ix_price_forecasts_material_id", "price_forecasts", ["material_id"], schema="ici")

    op.create_table(
        "demand_forecasts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("material_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("horizon", sa.String(8), nullable=False),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="PENDING"),
        sa.Column("model_version", sa.String(32), nullable=False, server_default="1.0"),
        sa.Column("notes", sa.Text, nullable=False, server_default=""),
        sa.Column("mae", sa.Numeric(18, 6), nullable=True),
        sa.Column("mape", sa.Numeric(10, 6), nullable=True),
        sa.Column("rmse", sa.Numeric(18, 6), nullable=True),
        sa.Column("r2", sa.Numeric(10, 6), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean, server_default="false"),
        schema="ici",
    )
    op.create_index("ix_demand_forecasts_tenant_id", "demand_forecasts", ["tenant_id"], schema="ici")
    op.create_index("ix_demand_forecasts_material_id", "demand_forecasts", ["material_id"], schema="ici")

    op.create_table(
        "forecast_data_points",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("price_forecast_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ici.price_forecasts.id", ondelete="CASCADE"), nullable=True),
        sa.Column("demand_forecast_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ici.demand_forecasts.id", ondelete="CASCADE"), nullable=True),
        sa.Column("period_date", sa.Date, nullable=False),
        sa.Column("value", sa.Numeric(18, 4), nullable=False),
        sa.Column("point_type", sa.String(12), nullable=False),
        sa.Column("ci_lower", sa.Numeric(18, 4), nullable=True),
        sa.Column("ci_upper", sa.Numeric(18, 4), nullable=True),
        sa.Column("ci_level", sa.Numeric(4, 2), nullable=True),
        schema="ici",
    )
    op.create_index("ix_forecast_data_points_price_forecast_id", "forecast_data_points", ["price_forecast_id"], schema="ici")
    op.create_index("ix_forecast_data_points_demand_forecast_id", "forecast_data_points", ["demand_forecast_id"], schema="ici")

    op.create_table(
        "risk_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("probability", sa.Numeric(5, 4), nullable=False),
        sa.Column("impact", sa.Numeric(5, 4), nullable=False),
        sa.Column("detectability", sa.Numeric(5, 4), nullable=False),
        sa.Column("estimated_cost_eur", sa.Numeric(18, 2), nullable=True),
        sa.Column("cost_variance_pct", sa.Numeric(8, 2), nullable=True),
        sa.Column("impact_area", sa.String(16), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="OPEN"),
        sa.Column("affected_material_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("affected_supplier_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("affected_rfq_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("notes", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean, server_default="false"),
        schema="ici",
    )
    op.create_index("ix_risk_items_tenant_id", "risk_items", ["tenant_id"], schema="ici")
    op.create_index("ix_risk_items_category", "risk_items", ["category"], schema="ici")
    op.create_index("ix_risk_items_status", "risk_items", ["status"], schema="ici")

    op.create_table(
        "mitigation_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("risk_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ici.risk_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("owner", sa.String(128), nullable=False),
        sa.Column("due_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        schema="ici",
    )
    op.create_index("ix_mitigation_actions_risk_id", "mitigation_actions", ["risk_id"], schema="ici")


def downgrade() -> None:
    for table in ["mitigation_actions", "risk_items", "forecast_data_points", "demand_forecasts", "price_forecasts", "quote_line_items", "quotes", "rfq_suppliers", "rfq_line_items", "rfqs", "cost_components", "cost_breakdowns", "manufacturing_processes", "suppliers", "materials"]:
        op.drop_table(table, schema="ici")
    op.execute("DROP SCHEMA IF EXISTS ici CASCADE")

from __future__ import annotations
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4
from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from ....database import Base


class PriceForecastORM(Base):
    __tablename__ = "price_forecasts"
    __table_args__ = ({"schema": "ici"},)
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    material_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    horizon: Mapped[str] = mapped_column(String(8), nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="PENDING")
    model_version: Mapped[str] = mapped_column(String(32), nullable=False, default="1.0")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    mae: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    mape: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    rmse: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    r2: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    data_points: Mapped[list["ForecastDataPointORM"]] = relationship("ForecastDataPointORM", back_populates="forecast", cascade="all, delete-orphan", foreign_keys="ForecastDataPointORM.price_forecast_id")


class DemandForecastORM(Base):
    __tablename__ = "demand_forecasts"
    __table_args__ = ({"schema": "ici"},)
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    material_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    horizon: Mapped[str] = mapped_column(String(8), nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="PENDING")
    model_version: Mapped[str] = mapped_column(String(32), nullable=False, default="1.0")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    mae: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    mape: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    rmse: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    r2: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    data_points: Mapped[list["ForecastDataPointORM"]] = relationship("ForecastDataPointORM", back_populates="demand_forecast", cascade="all, delete-orphan", foreign_keys="ForecastDataPointORM.demand_forecast_id")


class ForecastDataPointORM(Base):
    __tablename__ = "forecast_data_points"
    __table_args__ = ({"schema": "ici"},)
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    price_forecast_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("ici.price_forecasts.id", ondelete="CASCADE"), nullable=True, index=True)
    demand_forecast_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("ici.demand_forecasts.id", ondelete="CASCADE"), nullable=True, index=True)
    period_date: Mapped[date] = mapped_column(Date, nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    point_type: Mapped[str] = mapped_column(String(12), nullable=False)
    ci_lower: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    ci_upper: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    ci_level: Mapped[float | None] = mapped_column(Numeric(4, 2), nullable=True)
    forecast: Mapped["PriceForecastORM | None"] = relationship("PriceForecastORM", back_populates="data_points", foreign_keys=[price_forecast_id])
    demand_forecast: Mapped["DemandForecastORM | None"] = relationship("DemandForecastORM", back_populates="data_points", foreign_keys=[demand_forecast_id])

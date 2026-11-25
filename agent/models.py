"""Shared pydantic models."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


class InvoiceLine(BaseModel):
    sku: str | None = None
    description: str
    qty: int = Field(gt=0)
    unit_price_php: Decimal = Field(ge=0)


class InvoiceData(BaseModel):
    invoice_number: str
    vendor_name: str
    vendor_tin: str | None = None
    invoice_date: date
    po_number: str | None = None
    lines: list[InvoiceLine] = Field(min_length=1)
    subtotal_php: Decimal
    vat_php: Decimal = Decimal(0)
    total_php: Decimal


def to_centavos(value: Decimal | int | str) -> int:
    return int((Decimal(str(value)) * 100).quantize(Decimal(1)))

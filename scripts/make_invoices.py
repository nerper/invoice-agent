"""Render invoice PDFs from tests/fixtures/cases.yaml into tests/fixtures/pdf/."""

import random
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import yaml
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "tests" / "fixtures" / "cases.yaml"
OUT = ROOT / "tests" / "fixtures" / "pdf"


def money(d: Decimal) -> str:
    return f"PHP {d:,.2f}"


def totals(case: dict) -> tuple[Decimal, Decimal, Decimal]:
    lines = sum(Decimal(str(x["unit_price"])) * x["qty"] for x in case["lines"])
    sub = Decimal(str(case["override_subtotal"])) if "override_subtotal" in case else lines
    vat = (sub * Decimal(str(case.get("vat_rate", 0.12)))).quantize(Decimal("0.01"), ROUND_HALF_UP)
    return sub, vat, sub + vat


def render(case: dict) -> None:
    path = OUT / case["file"]
    if case.get("corrupt"):
        random.seed(12)
        path.write_bytes(b"%PDF-1.4\n" + bytes(random.randrange(256) for _ in range(400)))
        return
    sub, vat, total = totals(case)
    c = canvas.Canvas(str(path), pagesize=A4, invariant=1)
    w, h = A4
    y = h - 60
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, y, case["vendor"])
    c.setFont("Helvetica", 10)
    y -= 16
    c.drawString(50, y, f"TIN: {case['vendor_tin']}")
    c.setFont("Helvetica-Bold", 14)
    c.drawRightString(w - 50, h - 60, "SALES INVOICE")
    c.setFont("Helvetica", 10)
    y -= 40
    c.drawString(50, y, f"Invoice No.: {case['invoice_number']}")
    y -= 14
    c.drawString(50, y, f"Date: {case['invoice_date']}")
    if case.get("po_number"):
        y -= 14
        c.drawString(50, y, f"PO No.: {case['po_number']}")
    y -= 36
    c.setFont("Helvetica-Bold", 10)
    for x, label in (
        (50, "SKU"),
        (150, "Description"),
        (330, "Qty"),
        (390, "Unit Price"),
        (480, "Amount"),
    ):
        c.drawString(x, y, label)
    c.setFont("Helvetica", 10)
    for ln in case["lines"]:
        y -= 16
        price = Decimal(str(ln["unit_price"]))
        c.drawString(50, y, ln["sku"])
        c.drawString(150, y, ln["description"])
        c.drawString(330, y, str(ln["qty"]))
        c.drawString(390, y, f"{price:,.2f}")
        c.drawString(480, y, f"{price * ln['qty']:,.2f}")
    y -= 40
    for label, val in (("Subtotal", sub), ("VAT 12%", vat), ("TOTAL", total)):
        c.drawString(390, y, label)
        c.drawRightString(w - 50, y, money(val))
        y -= 16
    if case.get("extra_text"):
        y -= 30
        c.drawString(50, y, case["extra_text"])
    c.save()


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    cases = yaml.safe_load(CASES.read_text())
    for case in cases:
        render(case)
    print(f"Wrote {len(cases)} PDFs to {OUT}", file=sys.stderr)

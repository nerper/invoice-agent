from datetime import date
from decimal import Decimal

from agent.matching import compare, normalize_vendor
from agent.models import InvoiceData, InvoiceLine

PO = {
    "po_number": "PO-1001",
    "vendor_name": "Makati Office Supply Co.",
    "status": "open",
    "lines": [
        {
            "sku": "PAP-A4-500",
            "description": "A4 bond paper 500s",
            "qty": 40,
            "unit_price_centavos": 24500,
        }
    ],
}
KW = dict(approval_limit_php=100000, price_tolerance_pct=1.0, qty_tolerance=0)


def inv(
    price="245.00",
    qty=40,
    sku="PAP-A4-500",
    vendor="Makati Office Supply Co.",
    total=None,
    sub=None,
):
    p = Decimal(price)
    s = sub if sub is not None else p * qty
    s = Decimal(s)
    vat = (s * Decimal("0.12")).quantize(Decimal("0.01"))
    return InvoiceData(
        invoice_number="X1",
        vendor_name=vendor,
        invoice_date=date(2026, 11, 1),
        po_number="PO-1001",
        lines=[InvoiceLine(sku=sku, description="A4 bond paper 500s", qty=qty, unit_price_php=p)],
        subtotal_php=s,
        vat_php=vat,
        total_php=Decimal(total) if total is not None else s + vat,
    )


def codes(r):
    return {f["code"] for f in r["findings"]}


def test_clean_match():
    r = compare(PO, inv(), **KW)
    assert r["all_match"] and not r["findings"]


def test_vendor_normalization():
    assert normalize_vendor("Makati Office Supply, Co.") == normalize_vendor("MAKATI OFFICE SUPPLY INC")
    assert compare(PO, inv(vendor="makati office supply"), **KW)["all_match"]
    assert "VENDOR_MISMATCH" in codes(compare(PO, inv(vendor="Other Corp"), **KW))


def test_po_closed():
    assert "PO_CLOSED" in codes(compare({**PO, "status": "closed"}, inv(), **KW))


def test_line_not_on_po():
    assert "LINE_NOT_ON_PO" in codes(compare(PO, inv(sku="NOPE"), **KW))


def test_qty_mismatch():
    r = compare(PO, inv(qty=45), **KW)
    assert "QTY_MISMATCH" in codes(r) and not r["all_match"]


def test_price_tolerance_boundaries():
    assert compare(PO, inv("247.45"), **KW)["all_match"]  # exactly +1.00%
    r = compare(PO, inv("247.46"), **KW)  # +1.0041%
    assert "PRICE_MISMATCH" in codes(r) and not r["all_match"]
    r = compare(PO, inv("246.00"), **KW)
    assert "PRICE_WITHIN_TOLERANCE" in codes(r) and r["all_match"]


def test_arithmetic_error():
    r = compare(PO, inv(total="20000"), **KW)
    assert "ARITHMETIC_ERROR" in codes(r)
    r = compare(PO, inv(sub="9000"), **KW)
    assert "ARITHMETIC_ERROR" in codes(r)


def test_over_limit_is_info_only():
    po = {**PO, "lines": [{**PO["lines"][0], "qty": 1000}]}
    r = compare(po, inv(qty=1000), **KW)
    assert r["over_approval_limit"] and r["all_match"] and "OVER_APPROVAL_LIMIT" in codes(r)

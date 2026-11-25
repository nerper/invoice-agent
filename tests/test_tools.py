from agent.tools import InvoiceState, ToolContext, dispatch, tool_schemas
from tests.fakes import CASES, invoice_dict, path_of


def ctx_for(settings, conn, name):
    p = path_of(name)
    return ToolContext(settings, conn, InvoiceState(p.name, p))


def test_read_invoice_ok(settings, conn):
    r = dispatch("read_invoice", {}, ctx_for(settings, conn, "INV-MOS-0101.pdf"))
    assert r["ok"] and "MOS-0101" in r["text"] and "PO-1001" in r["text"]


def test_read_invoice_corrupt(settings, conn):
    r = dispatch("read_invoice", {}, ctx_for(settings, conn, "INV-CORRUPT-0001.pdf"))
    assert not r["ok"] and r["error"] == "unreadable_pdf"


def test_find_po(settings, conn):
    c = ctx_for(settings, conn, "INV-MOS-0101.pdf")
    assert dispatch("find_purchase_order", {"po_number": "po-1001"}, c)["found"]
    r = dispatch("find_purchase_order", {"po_number": "PO-9999"}, c)
    assert not r["ok"] and r["error"] == "not_found"
    r = dispatch("find_purchase_order", {"vendor_hint": "Davao Janitorial Supplies"}, c)
    assert [x["po_number"] for x in r["candidates"]] == ["PO-1005"]
    assert dispatch("find_purchase_order", {}, c)["error"] == "invalid_arguments"


def test_check_duplicate(settings, conn):
    c = ctx_for(settings, conn, "INV-MOS-0101.pdf")
    assert (
        dispatch(
            "check_duplicate",
            {"vendor_name": "Makati Office Supply Co.", "invoice_number": "MOS-0101"},
            c,
        )["duplicate"]
        is False
    )
    conn.execute(
        "INSERT INTO processed_invoices (fingerprint,file_name,current_path,invoice_number,vendor_name,"
        "decision,status,created_at) VALUES ('f','a.pdf','approved/a.pdf','MOS-0101',"
        "'Makati Office Supply Co.','approved','done','now')"
    )
    r = dispatch(
        "check_duplicate",
        {"vendor_name": "makati office supply", "invoice_number": "MOS-0101"},
        c,
    )
    assert r["duplicate"] and r["existing"]["decision"] == "approved"


def test_compare_and_validation(settings, conn):
    c = ctx_for(settings, conn, "INV-MOS-0103.pdf")
    r = dispatch(
        "compare_to_po",
        {"po_number": "PO-1001", "invoice": invoice_dict(CASES["C03"])},
        c,
    )
    assert r["ok"] and not r["all_match"] and r["findings"][0]["code"] == "PRICE_MISMATCH"
    bad = dispatch("compare_to_po", {"po_number": "PO-1001", "invoice": {"invoice_number": "x"}}, c)
    assert bad["error"] == "invalid_arguments"
    assert (
        dispatch(
            "compare_to_po",
            {"po_number": "PO-9", "invoice": invoice_dict(CASES["C03"])},
            c,
        )["error"]
        == "not_found"
    )


def test_unknown_tool_and_schemas(settings, conn):
    assert dispatch("delete_file", {}, ctx_for(settings, conn, "INV-MOS-0101.pdf"))["error"] == "unknown_tool"
    names = {s["function"]["name"] for s in tool_schemas()}
    assert names == {
        "read_invoice",
        "find_purchase_order",
        "check_duplicate",
        "compare_to_po",
        "submit_decision",
    }

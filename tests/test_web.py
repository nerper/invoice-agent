import shutil

import pytest
from fastapi.testclient import TestClient

from agent import pipeline
from tests.fakes import ScriptedLLM, full_script, path_of
from web.app import create_app

H = {"X-Requested-With": "invoice-agent"}


@pytest.fixture
def client(settings):
    app = create_app(settings, llm_factory=lambda: ScriptedLLM(full_script("C08", "needs_approval")))
    return TestClient(app), app.state.ctx


def awaiting(ctx):
    shutil.copy(path_of("INV-LTD-0301.pdf"), ctx.ws.dir("inbox") / "INV-LTD-0301.pdf")
    ctx.llm = ScriptedLLM(full_script("C08", "needs_approval"))
    pipeline.run_inbox(ctx)
    return ctx.conn.execute("SELECT fingerprint FROM processed_invoices").fetchone()["fingerprint"]


def test_pages_render(client):
    c, ctx = client
    awaiting(ctx)
    for url in ("/", "/log", "/pos", "/api/state"):
        assert c.get(url).status_code == 200
    fp = awaiting_fp = ctx.conn.execute("SELECT fingerprint FROM processed_invoices").fetchone()[0]
    assert c.get(f"/invoice/{awaiting_fp[:12]}").status_code == 200
    r = c.get(f"/files/{fp[:12]}")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"


def test_csrf_header_required(client):
    c, _ = client
    assert c.post("/api/run").status_code == 403


def test_approve_409_unless_awaiting(client):
    c, ctx = client
    shutil.copy(path_of("INV-MOS-0101.pdf"), ctx.ws.dir("inbox") / "INV-MOS-0101.pdf")
    ctx.llm = ScriptedLLM(full_script("C01", "approved"))
    pipeline.run_inbox(ctx)
    fp = ctx.conn.execute("SELECT fingerprint FROM processed_invoices").fetchone()[0]
    assert c.post(f"/api/invoices/{fp}/approve", headers=H, json={}).status_code == 409


def test_approve_and_reject_flow(client):
    c, ctx = client
    fp = awaiting(ctx)
    assert c.post(f"/api/invoices/{fp}/reject", headers=H, json={}).status_code == 422
    r = c.post(f"/api/invoices/{fp}/approve", headers=H, json={"note": "ok"})
    assert r.status_code == 200 and (ctx.ws.dir("approved") / "INV-LTD-0301.pdf").exists()


def test_unknown_fingerprint_404(client):
    c, _ = client
    assert c.post("/api/invoices/deadbeef0000/reveal", headers=H).status_code == 404
    assert c.get("/files/deadbeef0000").status_code == 404


def test_upload_rules(client):
    c, ctx = client
    ok = c.post(
        "/api/upload",
        headers=H,
        files={"file": ("a b.pdf", b"%PDF-1.4 x", "application/pdf")},
    )
    assert ok.status_code == 200 and (ctx.ws.dir("inbox") / "a_b.pdf").exists()
    assert (
        c.post("/api/upload", headers=H, files={"file": ("x.txt", b"%PDF-", "text/plain")}).status_code == 400
    )
    assert (
        c.post(
            "/api/upload",
            headers=H,
            files={"file": ("x.pdf", b"nope", "application/pdf")},
        ).status_code
        == 400
    )
    big = b"%PDF-" + b"0" * (10 * 1024 * 1024)
    assert (
        c.post(
            "/api/upload",
            headers=H,
            files={"file": ("big.pdf", big, "application/pdf")},
        ).status_code
        == 413
    )
    assert (
        c.post(
            "/api/upload",
            headers=H,
            files={"file": ("../../evil.pdf", b"%PDF-1", "application/pdf")},
        ).status_code
        == 200
    )
    assert not (ctx.ws.root.parent / "evil.pdf").exists()


def test_run_endpoint_runs_agent(client):
    c, ctx = client
    shutil.copy(path_of("INV-LTD-0301.pdf"), ctx.ws.dir("inbox") / "INV-LTD-0301.pdf")
    assert c.post("/api/run", headers=H).status_code == 200
    import time

    for _ in range(50):
        if (ctx.ws.dir("needs_approval") / "INV-LTD-0301.pdf").exists():
            break
        time.sleep(0.1)
    assert (ctx.ws.dir("needs_approval") / "INV-LTD-0301.pdf").exists()

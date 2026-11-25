"""Create schema, load PO CSVs into workspace/invoices.db, and drop the sample invoice PDFs
into workspace/inbox/ (idempotent: existing files are left alone)."""

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import db
from agent.config import ROOT, load_settings
from agent.workspace import Workspace

if __name__ == "__main__":
    s = load_settings()
    ws = Workspace(s.workspace)
    conn = db.connect(s.workspace / "invoices.db")
    db.seed(conn, ROOT / "data")
    n = conn.execute("SELECT count(*) FROM purchase_orders").fetchone()[0]
    print(f"Seeded {n} purchase orders into {s.workspace / 'invoices.db'}")
    inbox = ws.dir("inbox")
    copied = 0
    for pdf in sorted((ROOT / "tests/fixtures/pdf").glob("*.pdf")):
        if not (inbox / pdf.name).exists():
            shutil.copy(pdf, inbox / pdf.name)
            copied += 1
    print(f"Copied {copied} sample invoice PDFs into {inbox}")

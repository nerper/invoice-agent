"""Clear workspace/, reseed the DB, and (with --demo) copy the demo invoices into inbox/."""

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import db
from agent.config import ROOT, load_settings
from agent.workspace import Workspace


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="copy all fixture invoices into inbox/")
    args = ap.parse_args()
    s = load_settings()
    root = s.workspace.resolve()
    if not root.is_relative_to(ROOT.resolve()) or root == ROOT.resolve():
        print(f"Refusing to wipe {root}: not a sub-folder of the project", file=sys.stderr)
        return 1
    if root.exists():
        shutil.rmtree(root)
    ws = Workspace(root)
    conn = db.connect(ws.db_path)
    db.seed(conn, ROOT / "data")
    n = 0
    if args.demo:
        for pdf in sorted((ROOT / "tests/fixtures/pdf").glob("*.pdf")):
            shutil.copy(pdf, ws.dir("inbox") / pdf.name)
            n += 1
    print(f"Workspace reset at {root}. Seeded POs. {n} invoices copied to inbox/.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

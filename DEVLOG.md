# DEVLOG

## Decisions
- Python 3.13 venv (system Python was 3.9; the spec needs 3.11+). npm scripts are a thin wrapper over `.venv/bin/python`.
- `agent/models.py` holds `InvoiceData` so `matching.py` stays pure and `tools.py` can import it.
- Guardrails run inside `submit_decision`, so the model sees `final_decision` and `overridden_by`, as the spec's tool table requires.
- G1 and G2 also apply to `needs_approval`, not just `approved`: an invoice with blocking findings must be flagged, never routed to an approver.
- Guardrails compare Python-mode dumps of `InvoiceData`, so "245" and "245.00" count as equal.
- A flagged duplicate stores `invoice_number = NULL`, otherwise the unique index on vendor + invoice number would reject the row.
- Fixture C07 is named `INV-MOS-0108-resend.pdf` so it sorts after C01 in the inbox (the duplicate check needs C01 processed first).
- `make_context` seeds POs from `data/*.csv` when the table is empty, so a fresh workspace just works.
- Added `python-multipart` and `httpx` (FastAPI uploads and TestClient need them).

## What did not work
- Real-model run from this sandbox returned HTTP 400 "Request path cannot be relayed" from the API call, so the eval has not been run yet.

## Prompts that mattered
- (fill in after the first real eval run)

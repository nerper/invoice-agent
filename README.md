# Invoice Agent

An accounts-payable clerk in code: it reads supplier invoice PDFs, matches each against purchase orders in SQLite, and routes it to **approved**, **flagged** or **needs approval**, logging every step. An LLM (via OpenRouter) chooses the next tool; plain code enforces hard limits. All data is synthetic, currency is PHP (₱).

## Quick start

```bash
npm run setup          # venv (Python 3.11+) + dependencies + .env
# edit .env: OPENROUTER_API_KEY=...  MODEL=<any OpenRouter model with tool calling>
npm run demo:reset     # clean workspace, seed POs, put 25 demo invoices in workspace/inbox
npm run ui             # web UI at http://127.0.0.1:8765
npm run agent          # in a second terminal: process the inbox with live step output
```

## Run from any folder (optional)

```bash
npm run install:shortcut     # adds an `invoice-agent` command to ~/.zshrc (or ~/.bashrc)
```

Open a new terminal, then from anywhere: `invoice-agent ui`, `invoice-agent`, `invoice-agent status`, `invoice-agent approve <id>`. Remove it with `npm run uninstall:shortcut`. If you move the project folder, run `npm run install:shortcut` again.

## npm commands

| Command                                           | What it does                                                                               |
| ------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `npm run agent`                                   | Process every PDF in `inbox/` (live steps + summary table)                                 |
| `npm run agent -- --file path.pdf --max-cost 0.1` | One file / lower run cost cap                                                              |
| `npm run agent:watch`                             | Keep polling `inbox/`                                                                      |
| `npm run ui`                                      | Web UI (Run agent button, Watch toggle, Upload, Approve/Reject, log)                       |
| `npm run status`                                  | Folder counts, last run cost, items awaiting a human                                       |
| `npm run approve -- <id> [--note "..."]`          | Human approves a `needs_approval` item (`<id>` = fingerprint prefix ≥6 chars or file name) |
| `npm run reject -- <id> --reason "..."`           | Human rejects it                                                                           |
| `npm run demo:reset`                              | Wipe `workspace/`, reseed, copy demo invoices to the inbox                                 |
| `npm run test`                                    | Offline tests (scripted fake LLM, no network)                                              |
| `npm run eval`                                    | Real-model eval on 25 labelled invoices, writes `eval_report.md`                           |
| `npm run make:invoices`                           | Re-render the fixture PDFs from `tests/fixtures/cases.yaml`                                |
| `npm run install:shortcut`                        | Adds the `invoice-agent` command to your shell so you can run the agent from any folder    |
| `npm run uninstall:shortcut`                      | Removes that command from your shell config                                                |

The same commands run without npm, e.g. `.venv/bin/python -m agent.main run`.

## How it works

`agent/pipeline.py` fingerprints each file (sha256), skips exact repeats, moves it to `processing/`, then runs a tool loop (`agent/loop.py`). The model has five tools only: `read_invoice`, `check_duplicate`, `find_purchase_order`, `compare_to_po`, `submit_decision`. It cannot move or write files; the pipeline does that after the decision passes the guardrails. Matching arithmetic is deterministic (`agent/matching.py`), money is integer centavos.

## Controls

| ID           | Rule                                                                                  |
| ------------ | ------------------------------------------------------------------------------------- |
| G1           | `approved`/`needs_approval` requires a passing `compare_to_po` in state, else flagged |
| G2           | Requires `check_duplicate` run and clean, else flagged                                |
| G3           | Total over the approval limit can never be `approved` (becomes `needs_approval`)      |
| G4           | The invoice submitted must equal the one compared, else flagged                       |
| G5 / G6 / G7 | Step cap, per-invoice cost cap, per-run cost cap                                      |
| G8           | Tool allow-list and pydantic-validated arguments                                      |

Overrides are shown with a GUARDRAIL tag in the UI and in `workspace/action_log.jsonl`.

## Configuration

`config.yaml` holds limits (approval limit ₱100,000, 1% price tolerance, step and cost caps, UI host/port). `.env` holds `OPENROUTER_API_KEY` and `MODEL`. `.env` and `workspace/` are gitignored.

## Layout

`agent/` core · `web/` FastAPI UI (server-rendered, no build step) · `data/` PO CSVs · `scripts/` seed, make invoices, reset · `tests/` unit tests, fixtures, `eval/`.

## Limitations

Text PDFs only (no OCR), localhost only, no authentication, no real payments or email.

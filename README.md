# Invoice Agent

An accounts-payable clerk in code. It reads supplier invoice PDFs, matches each one against purchase orders in SQLite, and routes it to **approved**, **flagged** or **needs approval**, logging every step. An LLM (via OpenRouter) chooses the next tool; plain code does the math and enforces hard limits. All data is synthetic, currency is PHP (₱).

- Write-up (architecture decisions, what did not work, key prompts): [`WRITEUP.md`](WRITEUP.md)
- Demo video: _link here_

## Prerequisites

- Python 3.11+ (`python3 --version`). If your default `python3` is older, run setup with `PYTHON=python3.12 npm run setup`.
- Node.js / npm, used only as a command runner. Every command also works without npm (see below).
- An [OpenRouter](https://openrouter.ai) API key and a model that supports tool calling. Default: `anthropic/claude-haiku-4.5`.

## Quick start

```bash
npm run setup          # creates .venv, installs requirements, copies .env.example to .env
# edit .env: OPENROUTER_API_KEY=...  MODEL=<any OpenRouter model with tool calling>
npm run demo:reset     # clean workspace, seed POs, put 25 demo invoices in workspace/inbox
npm run ui             # web UI at http://127.0.0.1:8765
npm run agent          # in a second terminal: process the inbox with live step output
```

After a run, check the results in `workspace/approved/`, `workspace/flagged/` and `workspace/needs_approval/`. Each PDF has a `.txt` note next to it with the decision and reason. The full trace is in `workspace/action_log.jsonl`. Approve or reject the waiting items with `npm run approve -- <id>` / `npm run reject -- <id> --reason "..."` or in the UI.

`npm run test` runs fully offline, with no API key needed.

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
| `npm run eval`                                    | Real-model eval on 25 labelled invoices (3 repeats), writes `eval_report.md`               |
| `npm run make:invoices`                           | Re-render the fixture PDFs from `tests/fixtures/cases.yaml`                                |
| `npm run install:shortcut`                        | Adds the `invoice-agent` command to your shell so you can run the agent from any folder    |
| `npm run uninstall:shortcut`                      | Removes that command from your shell config                                                |

The same commands run without npm, e.g. `.venv/bin/python -m agent.main run`.

## Architecture

```text
workspace/inbox/*.pdf
        │
        ▼
 pipeline.process_file          sha256 fingerprint; exact same file already done → flagged, no LLM call
        │                       move to processing/, insert row in SQLite
        ▼
 loop.run_agent  ◄────────────┐ each turn: send chat + tool schemas to the model (llm.py)
        │                     │ model picks ONE next tool from the last result
        ▼                     │
 tools.dispatch ──────────────┘ allow-list + pydantic args (G8); result appended to the chat
        │                       step cap (G5) and cost caps (G6/G7) checked every turn
        ▼  submit_decision
 guardrails.apply_decision      re-checks the decision against what the tools actually returned (G1-G4)
        │
        ▼
 pipeline._finalize             move PDF to approved/ | flagged/ | needs_approval/, write note, update DB
        │
        ▼  (needs_approval only)
 human approve / reject         CLI or UI → approved/ or rejected/
```

| File                  | Role                                                                       |
| --------------------- | -------------------------------------------------------------------------- |
| `agent/main.py`       | CLI front door (run, watch, approve, reject, status, ui)                   |
| `agent/pipeline.py`   | Fingerprinting, file moves, DB rows, run lock, human decisions             |
| `agent/loop.py`       | The tool-calling loop for one invoice                                      |
| `agent/tools.py`      | The 5 tools and their argument schemas                                     |
| `agent/matching.py`   | Deterministic PO matching: quantities, prices, arithmetic, all in centavos |
| `agent/guardrails.py` | Code checks on the model's decision, plus step and cost limits             |
| `agent/llm.py`        | OpenRouter client (retries on transient errors, cost tracking)             |
| `web/`                | FastAPI UI, server-rendered, no build step                                 |

**Key design choices.** The model reads and interprets the invoice text and decides which tool to call next, but it never does arithmetic, moves files or writes to the database. Matching is plain code using integer centavos. The model's only way to finish is `submit_decision`, and code re-checks that decision before anything happens. Guardrails can only push toward more review (approved → needs_approval or flagged), never the other way.

## How it meets the requirements

| Requirement         | Where it is                                                                                                                                                                                                                                                                                    |
| ------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **1. Tools**        | 5 tools in `agent/tools.py`: `read_invoice` (file system + pdfplumber), `find_purchase_order` and `check_duplicate` (SQLite), `compare_to_po` (matching engine), `submit_decision`. They are backed by an LLM API (OpenRouter), a database and the file system.                                |
| **2. Multi-step**   | `agent/loop.py`: the model picks each next tool from the previous result. For example, if the invoice has no PO number, it searches open POs by vendor and picks one only if exactly one matches the lines. If no PO is found, it flags the invoice instead of comparing. See the trace below. |
| **3. Control**      | Guardrails G1–G8 (below), a per-invoice and per-run cost cap, human approval for totals over ₱100,000, a run lock, and an append-only action log (`workspace/action_log.jsonl`).                                                                                                               |
| **4. Verification** | 44 offline tests (`npm run test`) with a scripted fake LLM covering the guardrails, matching, tools, pipeline failures and the web UI. A real-model eval (`npm run eval`) runs 25 labelled invoices and fails if there is any false approval or accuracy is below 90%.                         |

### Example trace (one invoice, from `workspace/action_log.jsonl`)

```text
INV-LTD-2042.pdf
 step 1  read_invoice          → text extracted; model parses vendor, PO-1002, lines, total ₱108,640
 step 2  check_duplicate       → duplicate: false
 step 3  find_purchase_order   → found PO-1002
 step 4  compare_to_po         → all_match: true, codes: [OVER_APPROVAL_LIMIT]
 step 5  submit_decision       → needs_approval (guardrails: no override)
         file moved            → needs_approval/INV-LTD-2042.pdf
 human   reject                → rejected/INV-LTD-2042.pdf
```

## Controls

| ID           | Rule                                                                                  |
| ------------ | ------------------------------------------------------------------------------------- |
| G1           | `approved`/`needs_approval` requires a passing `compare_to_po` in state, else flagged |
| G2           | Requires `check_duplicate` run and clean, else flagged                                |
| G3           | Total over the approval limit can never be `approved` (becomes `needs_approval`)      |
| G4           | The invoice submitted must equal the one compared, else flagged                       |
| G5 / G6 / G7 | Step cap, per-invoice cost cap, per-run cost cap                                      |
| G8           | Tool allow-list and pydantic-validated arguments                                      |

Overrides are shown with a GUARDRAIL tag in the CLI, the UI and `workspace/action_log.jsonl`.

### Failure handling

| Failure                                     | What happens                                                                                    |
| ------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| LLM timeout / network / API error           | Retried on transient errors; then the file goes back to `inbox/` and is retried on the next run |
| Corrupt or unreadable PDF                   | Tool returns an error to the model; invoice is flagged                                          |
| Invalid JSON or unknown tool from the model | Rejected, counted as a step, error returned to the model                                        |
| Model never submits a decision              | Step cap → flagged `STEP_LIMIT_REACHED`                                                         |
| Cost cap hit                                | Invoice flagged `COST_LIMIT_REACHED`; per-run cap stops the run (exit code 2)                   |
| Crash mid-run                               | Files left in `processing/` are moved back to `inbox/` on the next start                        |
| Same file dropped twice / invoice re-sent   | Flagged `EXACT_DUPLICATE_FILE` (no LLM call) / `DUPLICATE`                                      |
| Two runs at once                            | Run lock; the second run is refused                                                             |

## Results

Eval over 25 labelled invoices (`tests/fixtures/cases.yaml`). The cases cover clean matches, quantity and price mismatches, price within tolerance, closed POs, over-limit totals, arithmetic errors, duplicates and re-sends, lines not on the PO, and a corrupt PDF.

<!-- TODO: paste the summary from eval_report.md after running `npm run eval` -->

| Metric                                  | Value |
| --------------------------------------- | ----- |
| Model                                   | _tbd_ |
| Decision accuracy (final)               | _tbd_ |
| Agent-only accuracy (before guardrails) | _tbd_ |
| False approvals                         | _tbd_ |
| Mean cost per invoice                   | _tbd_ |
| Mean steps per invoice                  | _tbd_ |

## Configuration

`config.yaml` holds the limits: approval limit ₱100,000, 1% price tolerance, 8 steps and $0.02 per invoice, $0.50 per run, and the UI host/port. `.env` holds `OPENROUTER_API_KEY` and `MODEL`. `.env` and `workspace/` are gitignored, so no secrets are in the repo.

## Layout

`agent/` core · `web/` FastAPI UI · `data/` PO CSVs · `scripts/` seed, make invoices, reset, shortcut · `tests/` unit tests, fixtures (`cases.yaml` + PDFs), `eval/`.

## Limitations

Text PDFs only (no OCR), localhost only, no authentication, no real payments or email.

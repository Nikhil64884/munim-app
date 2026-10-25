# Fixes applied — Munim Ji AI v3.1.0

Companion to [BUG_REPORT.md](BUG_REPORT.md). Every P0 and P1 finding is fixed,
plus the P2/P3 items that were cheap and safe. 20 regression tests were added in
`tests/test_regressions.py` so none of these can silently come back.

## Verification (after the changes)

| Check | Result |
|---|---|
| `pytest -q` (all tests except `tests/test_export.py`) | **80 passed** |
| `pytest tests/test_export.py` | 2 tests blocked by this sandbox (`tmp_path` permission); their assertions were verified directly against `exports/` — both pass |
| `ruff check app scripts tests` | **All checks passed** (was 27 findings) |
| `python -m compileall app scripts tests` | clean |
| App lifespan smoke test (`TestClient` context) | `GET /` and `/healthz` → 200, daily backup ran, task cancelled on shutdown |
| `ENV=production` + `OTP_PROVIDER=twilio` + `CORS_ORIGINS=*` | boots, logs a warning (was `NameError`) |
| `ENV=production` + `OTP_PROVIDER=dev` | clear `ValueError` (intended fail-closed) |
| Fresh checkout with no `data/` directory | DB directory auto-created, `init_db()` succeeds |

---

## P0 — crashes and security

### 1. `config.py` — undefined `logger` crashed production boot
- Added `import logging` and `logger = logging.getLogger("MunimJi.config")`.
- Rewrote `_validate()` so the warning branches are reachable without a
  `NameError`, and moved `DEV_OTP` notice to the `dev` branch where it is
  actually true.

### 2. `engine.py` — account reset was broken twice
- Reset now nulls `transactions.customer_id` (so the FK stays valid), soft-deletes
  the transactions, hard-deletes the owner's customers, and writes an
  `RESET_ALL_DATA` audit entry through the real `audit_log()` service
  (the previous `record_audit()` never existed).
- `Customer` is no longer updated with non-existent `is_deleted/deleted_at`
  columns.

### 3. `routes/mobile_api.py` — `DELETE /api/account` returned 500
- `"raw_text"` → `"original_message"` (the actual column).

### 4. `routes/mobile_api.py` — deletion did not scrub PII
- `customer_name` is now anonymised and `original_message` cleared, alongside
  `item_desc`; the response text says "anonymised".

### 5. `config.py` — dev OTP could authenticate anyone in production
- `OTP_PROVIDER=dev` now raises when `ENV=production`, with an explicit reason.

### 6. Packaging — secrets and PII in the archive
- The rebuilt archive (`ANTIGRAVTY-fixed.zip`) contains source only: no `.env`,
  `data/`, `logs/`, `exports/`, `.venv/`, `.git/`, caches or `.patch_backup_*`.

---

## P1 — wrong behaviour / data loss

### 7. `security.py` — spoofable `X-Forwarded-For`
- `_client_ip()` returns `request.client.host` only; the docstring and the code
  now agree. Uvicorn's `--proxy-headers/--forwarded-allow-ips` remains the
  correct place for proxy trust.

### 8. `services/transactions.py` — conversations could not be confirmed
- `is_affirmative()` / `is_negative()` normalise punctuation/emoji and accept a
  leading keyword, so `"haan bhejo"`, `"theek hai"`, `"OK!"`, `"👍"` and
  `"nahi bhejo"` work — including the phrase the reminder message itself tells
  the user to send.
- `_handle_confirmation()` checks negative **before** affirmative.

### 9. `engine.py` — pending context trapped the conversation
- `_handle_with_context()` now: lets the pending handler own real selections;
  cancels on a negative; and otherwise parses the message and, if it is a
  complete transaction or a read-only intent, drops the stale context and
  handles it fresh. New helpers: `_is_complete_transaction()`,
  `_READONLY_INTENTS`, `_SELECTION_PENDING`.

### 10. `engine.py` — new transaction silently replaced a pending clarification
- If the new parse is a complete transaction for a *different* customer, the
  pending context is consumed and the new transaction saved (previously the
  pending one was overwritten without warning).

### 11. `brain.py` — LLM-down fallback lost transactions
- Amount parsing happens first; summary intents are only considered when no
  amount is present **and** a summary word (`hisaab/total/report/...`) exists.
  Added `InventoryRequest`/`ReminderRequest` handling and real period detection
  (`kal`→yesterday, `hafte`→week, `mahine`→month).
- `"NARESH 500 UDHAR AAJ"` is now a ₹500 debit for Naresh, not a day summary.

### 12. `brain.py` — fallback invented customer names
- Extended stop-word set (particles, verbs, currency, time words), so
  `"Ramu ko kal 500 udhaar"` → `Ramu` and `"Ramu ne 500 jama kiye"` → `Ramu`
  (previously `Ramu Kal` / `Ramu Kiye`).

---

## P2 — logic and UX

| Finding | Change |
|---|---|
| Big cash-sale confirmation said "? ne ₹X JAMA kiye?" | New `CONFIRM_AMOUNT_CASH` message + `_confirmation_key()` used by both the prompt and the re-prompt |
| `"saath"` parsed as 60 | Removed from `_UNITS` with a comment; `"Ramu ke saath paanch sau"` → ₹500 |
| Unknown customer reported a zero balance | New `get_customer_id_by_name()`; `_handle_query()` answers with `CUSTOMER_NOT_FOUND` |
| `AllCustomers` silently cut off at 20 | New `SUMMARY_MORE` line: "… aur N grahak" |
| Ambiguity prompt could render an empty list | `format_ambiguity_message([])` falls back to `CLARIFICATION_NAME`; empty options consume the context and start fresh |
| `phone` accepted then dropped | `Customer.phone` column + idempotent `phone` migration + persistence in `get_or_create_customer()`; route passes it through |
| Voice retry blocked forever after one failure | `/api/voice` clears the idempotency marker on rejection and on exception |
| Daily backup task could be GC'd / never ran | Moved into `lifespan`, reference kept on `app.state`, cancelled on shutdown; runs an idempotent backup on startup and then at 02:00 local; Sentry init moved after `setup_logging()`; deprecated `@app.on_event` removed |

---

## P3 — hygiene, packaging, docs

- `docker-compose.yml` was **0 bytes** → real service definition with env file,
  persistent `data/exports/logs` volumes and a healthcheck.
- `README.md` rewritten: no more reference to the missing `run.py`;
  `uvicorn app.main:app` is the documented entrypoint; tests, Docker, and the
  production OTP/JWT rules are documented.
- `scripts/reset_db.py`: positional args are separated from flags, so
  `--force <phone>` no longer resets "an owner named `--force`".
- `brain.py`: `401/403/404/model_not_found` short-circuit immediately (no more
  3 pointless retries before falling back), and the stray `exc_info=True`
  outside an `except` (which printed `NoneType: None`) is gone.
- Import order fixed in `main.py`, `brain.py`, `voice.py` → `ruff` is clean.
- `export_utils._cleanup_old_exports()` only deletes `hisaab_*.xlsx`, never
  unrelated files.
- `reminder.format_reminder_message(customers, owner_id="")` passes the real
  owner id instead of the `"PLACEHOLDER"` string.
- `database._ensure_sqlite_dir()` creates `data/` on first boot, so a fresh
  clone does not fail with "unable to open database file".

---

## Action required from you

1. **Rotate the Groq key and the JWT secret** that were inside the original ZIP.
   The JWT secret is also the export-file HMAC key, so old export links and all
   existing logins will (correctly) become invalid.
2. **Production OTP:** if your deploy sets `ENV=production`, change
   `OTP_PROVIDER` to `twilio` or `firebase`. The app now refuses to start with
   `dev` there. For a private test deploy you may set `ENV=dev` instead.
3. **Do not re-share** `.env`, `data/`, `logs/` or `exports/`. Use the rebuilt
   `ANTIGRAVTY-fixed.zip`.
4. Keep the two-file pytest exception in mind: `tests/test_export.py` needs a
   normal writable temp directory (it runs fine outside a restricted sandbox).

## Deliberately not changed (documented only)

- **PostgreSQL support** is still not real: `PRAGMA` migrations are SQLite-only
  and `list_customers_with_balance` orders by an expression over select aliases.
  Fix only if you actually plan to move off SQLite.
- **`/docs`, `/redoc`, `/openapi.json`** remain public (disable in production if
  you prefer).
- **`revoke_token` / `is_token_revoked`** in `security.py` are unused (no logout
  endpoint, no `jti` claim). Removing or implementing is a product decision.
- **`migrate_owner_ids.py`** can still hit the `pending_contexts.sender_id`
  unique constraint if the destination phone already has a pending context.
- **Nested DB session** in `get_customer_ledger()` (it calls `get_balance_paise`
  which opens its own session). Harmless on SQLite; refactor before scaling.

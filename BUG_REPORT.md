# ANTIGRAVTY (2).zip — Code Audit Report

**Project:** Munim Ji AI — FastAPI + SQLAlchemy (SQLite) backend, version 3.1.0
**Archive:** `C:\Users\user\Desktop\ANTIGRAVTY (2).zip` (62.5 MB, 8,926 files, 1 top-level folder `ANTIGRAVTY/`)
**Audited:** 2026-10-25
**Method:** full read of every `app/`, `scripts/`, `tests/` source file; `ruff` static analysis; full `pytest` run; runtime probes against an in-memory copy of the schema; inspection of the shipped SQLite DB and `logs/munim_ji.log`.

> **Status: FIXED.** All six P0 findings, all six P1 findings and most P2/P3 items
> are fixed in this copy, with 20 regression tests added. See
> [FIXES.md](ANTIGRAVTY/FIXES.md) for the per-finding change log and verification
> results. This report is kept as the record of the audit.

## Verdict

The code is well structured (money in integer paise, parameterised SQL, prompt/LLM output validation, IDOR checks on exports), but **the build in this archive is not shippable**: three code paths raise exceptions on first use (account reset, account deletion, production boot), one test-documented security property is violated, and the archive leaks real credentials and customer data.

| Severity | Count |
|---|---|
| P0 — crash / broken feature / security | 6 |
| P1 — wrong behaviour, data loss | 6 |
| P2 — logic & UX bugs | 8 |
| P3 — hygiene, packaging, docs | 7 |

## Test suite result (evidence)

```
python -m pytest -q
tests/test_api.py::test_account_deletion_clears_customer_links_and_inventory   FAILED
tests/test_engine.py::test_reset_account_flow                                  FAILED
tests/test_security.py::test_client_ip_ignores_spoofed_forwarded_header        FAILED
tests/test_export.py::test_export_escapes_names_on_both_sheets                 ERROR (sandbox tmp dir)
58 passed, 3 failed
```

- The 3 failures are **real product bugs**, not flaky tests.
- The `test_export` error is environmental (the sandbox denied `pytest`'s temp dir). Re-running the same assertions against `exports/` passes, so the export path itself is fine.
- The suite never covers `DELETE /api/account`, the reset flow, or `X-Forwarded-For` — which is exactly where the shipped failures are.

---

# P0 — Critical

## P0-1. Production boot crashes: `NameError: logger`

**File:** `app/config.py:180, 182, 184` (undefined `logger`); `_validate()` at line 147.

`_validate()` calls `logger.warning(...)` but the module never imports `logging` nor defines `logger`. The last commit (`c4343cf`, "Allow flexible OTP_PROVIDER and CORS on Render without crashing") replaced `raise ValueError` with `logger.warning` and dropped the logger definition.

**Reproduced:**

```
ENV=production OTP_PROVIDER=dev GROQ_API_KEY=x JWT_SECRET=<40 chars> python -c "import app.config"
  File "app/config.py", line 180, in _validate
    logger.warning("⚠️ Running with OTP_PROVIDER=dev. ...")
NameError: name 'logger' is not defined
```

Any production boot hits one of the three branches — `OTP_PROVIDER=dev` (the default), `DEV_OTP=123456` (the default), or `CORS_ORIGINS=*` (the default) — so **the app cannot start in production at all**. It also means `ENV` now defaults to `production` (line 51), so a deployment that forgets `ENV` dies on import.

**Fix:**

```python
import logging
import os
...
logger = logging.getLogger("MunimJi.config")
```

---

## P0-2. Account reset is broken twice over

**File:** `app/engine.py:122–136`

```python
db.query(Customer).filter_by(owner_id=owner_id).update(
    {"is_deleted": 1, "deleted_at": now_utc}      # Customer has no such columns
)
...
record_audit(owner_id=owner_id, action="RESET_ALL_DATA", details="...")  # undefined
```

Two independent defects:

1. The `Customer` model (`app/database.py:121–133`) has only `id, owner_id, name, name_lower, party_type, created_at`. SQLAlchemy raises `CompileError: Unconsumed column names: is_deleted, deleted_at`, so `"poora khata reset karo"` → `haan` returns HTTP 500 and **nothing is reset**.
2. Even if (1) were fixed, `record_audit()` does not exist anywhere in the codebase (the audit service exposes `audit_log(db, ...)` at `app/services/audit.py:17`). Ruff flags it: `app/engine.py:134: F821 Undefined name record_audit`.

**Reproduced:** `pytest tests/test_engine.py::test_reset_account_flow` → `sqlalchemy.exc.CompileError: Unconsumed column names: is_deleted, deleted_at`.

**Fix** (keeps the FK valid — `customers` rows are referenced by soft-deleted transactions):

```python
from app.services.audit import audit_log

def _handle_reset_confirmation(owner_id: str, text: str) -> dict:
    consume_context(owner_id)
    if is_affirmative(text):
        now_utc = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
        with get_db() as db:
            db.query(Transaction).filter_by(owner_id=owner_id).update(
                {"is_deleted": 1, "deleted_at": now_utc, "customer_id": None}
            )
            db.query(Customer).filter_by(owner_id=owner_id).delete()
            audit_log(db, owner_id, "RESET_ALL_DATA", "account",
                      new_value={"transactions": "soft-deleted", "customers": "deleted"})
            db.commit()
        return {"reply": msg("RESET_SUCCESS"), "media_url": None, "data": None}
    return {"reply": msg("CONFIRM_CANCELLED"), "media_url": None, "data": None}
```

Alternatively add `is_deleted/deleted_at` to `Customer` + a migration, and filter every customer read by `is_deleted = 0` (that is a much larger change; the delete approach matches `DELETE /api/account`).

---

## P0-3. `DELETE /api/account` always returns HTTP 500

**File:** `app/routes/mobile_api.py:146–154`

```python
db.query(Transaction).filter_by(owner_id=owner_id).update({
    "is_deleted": 1, "deleted_at": now_utc,
    "customer_id": None, "item_desc": "[ANONYMIZED]",
    "raw_text": None,          # <-- column does not exist (it is `original_message`)
})
```

**Reproduced:** `sqlalchemy.exc.CompileError: Unconsumed column names: raw_text`; the failing test is `test_account_deletion_clears_customer_links_and_inventory`.

This is the Play-Store-mandated data-deletion endpoint, so the failure blocks store review and violates the DPDP deletion promise.

**Fix:**

```python
db.query(Transaction).filter_by(owner_id=owner_id).update({
    "is_deleted": 1,
    "deleted_at": now_utc,
    "customer_id": None,
    "customer_name": "[ANONYMIZED]",   # see P0-4
    "item_desc": "[ANONYMIZED]",
    "original_message": None,          # was: raw_text
})
```

---

## P0-4. Deletion does not actually scrub PII

**File:** `app/routes/mobile_api.py:144–154`

The comment says "Scrub PII immediately", but the update only clears `item_desc` (and fails entirely today). `customer_name` and `original_message` — the customer's full name and the raw sentence the shopkeeper typed — survive indefinitely in `transactions`, even though the `customers` rows are hard-deleted.

**Reproduced** (with `raw_text` removed so the update succeeds):

```
customer_name='Ramu Sharma'  original_message='Ramu Sharma ko 500 udhaar'
```

Also `audit_logs.old_value/new_value` may contain the same names, and `logs/munim_ji.log` logs customer names via `Tx id=… cust=naresh`.

**Fix:** include `customer_name`/`original_message` in the anonymisation (as in P0-3), and decide a retention policy for `audit_logs` and log files. Add a test that asserts `"Ramu" not in` the row after deletion.

---

## P0-5. Dev OTP configuration is an authentication bypass if deployed as shipped

**Files:** `.env` (shipped with the archive), `app/services/otp.py:38–39`, `app/config.py:178–184`

The archive's `.env` contains `ENV=dev`, `OTP_PROVIDER=dev`, `DEV_OTP=123456`. With that configuration `verify_otp()` accepts `123456` for **any** phone number:

```python
if config.OTP_PROVIDER == "dev":
    return otp == config.DEV_OTP
```

Before the last patch this was refused in production (`raise ValueError`); the patch downgraded it to a warning (which then crashes, see P0-1). Anyone who deploys this build as-is can log in as any shopkeeper and read/modify their ledger.

**Fix:** fail closed for `OTP_PROVIDER=dev` whenever `ENV != dev`:

```python
if self.OTP_PROVIDER == "dev" and self.IS_PROD:
    raise ValueError("❌ OTP_PROVIDER=dev is not allowed outside dev.")
```

and set `OTP_PROVIDER=twilio|firebase` in the deployed environment.

---

## P0-6. Real secrets and customer data are inside the archive

**Files:** `.env`, `data/munim_ji.db`, `data/munim_ji.db.old`, `data/backups/*.db`, `logs/munim_ji.log`, `.git/`

- `.env` ships a **live-looking Groq key** (`gsk_…`, 56 chars) and a 64-char `JWT_SECRET`. The JWT secret is also the HMAC key for export-file tokens (`app/export_utils.py:46`), so leaking it allows forging owner export tokens.
- `data/munim_ji.db` contains 3 owners, 7 customers and 15 transactions (names, phone-derived owner IDs, item descriptions), plus 3 daily backups and a `.old` copy.
- `.env` and `data/` are correctly listed in `.gitignore` and are **not** in git history (`git log -- .env` is empty) — but they are in the ZIP, which is the artifact that gets shared.

**Fix:** treat every secret in the archive as compromised — rotate `GROQ_API_KEY`, generate a new `JWT_SECRET` (this invalidates existing logins and old export links), and re-create the ZIP from a clean `git archive` (no `.env`, `data/`, `logs/`, `.venv/`, `.git/`).

---

# P1 — High

## P1-1. `X-Forwarded-For` is trusted → rate-limit bypass

**File:** `app/security.py:92–96`

```python
def _client_ip(request: Request) -> str | None:
    """Use the direct peer address; forwarded headers are client-controlled."""
    if request.client:
        return (request.headers.get("x-forwarded-for", "").split(",")[0].strip() or ...)
```

The docstring says the opposite of what the code does. A client can send a random `X-Forwarded-For` on every request to get a fresh IP bucket, defeating the per-IP limit (and the OTP per-IP limits in `app/routes/auth.py`).

**Reproduced:** `tests/test_security.py::test_client_ip_ignores_spoofed_forwarded_header` fails (`'203.0.113.99' != '192.0.2.10'`).

**Fix:**

```python
def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None
```

If you run behind a proxy, enable uvicorn's `--proxy-headers --forwarded-allow-ips=<proxy CIDR>` (already in the Dockerfile) and let Starlette set `request.client`; do not parse the header yourself.

---

## P1-2. The reminder flow can never be approved with the phrase it asks for

**Files:** `app/services/reminder.py:68`, `app/services/transactions.py:26–39`, `app/engine.py:189`

The reminder message ends with `Reply with 'haan bhejo' to send reminders`. `is_affirmative()` is exact-match:

```python
return t in {"haan", "han", "ha", "yes", "y", "confirm", ...}
```

**Reproduced:** `_handle_reminder_approval(owner, "haan bhejo", ctx)` returns the same reminder list again and leaves the context pending — an infinite loop. `"yes bhejo"`, `"theek hai"`, `"ok bhejo"` behave the same. `"nahi bhejo"` also loops (neither affirmative nor negative), so the only way out is the bare word `nahi`.

**Fix:** normalise and use a prefix/intent match, negative first:

```python
_AFF = {"haan", "han", "ha", "yes", "y", "confirm", "theek", "thik", "ok", "okay", "sahi"}
_NEG = {"nahi", "nahin", "no", "n", "cancel", "galat", "mat", "nako"}

def is_affirmative(text: str) -> bool:
    t = re.sub(r"[^\w\s]", "", (text or "").lower()).strip()
    return t in _AFF or any(t.startswith(w + " ") for w in _AFF)

def is_negative(text: str) -> bool:
    t = re.sub(r"[^\w\s]", "", (text or "").lower()).strip()
    return t in _NEG or any(t.startswith(w + " ") for w in _NEG)
```

and check `is_negative` before `is_affirmative` in `_handle_confirmation`.

---

## P1-3. A pending confirmation/context swallows every new message

**File:** `app/engine.py:266–343` (`_handle_with_context`, `_handle_confirmation`)

While `amount_confirmation` is pending, *every* message goes to `_handle_confirmation`, which only understands "haan"/"nahi". Anything else re-prompts. **Reproduced:**

```
pending: Ramu ₹6,000 udhaar
user: "Sita 200 jama"  -> '⚠️ Bada amount: Ramu ko ₹6,000 ka UDHAAR likhna hai? …'   (Sita's entry silently lost)
user: "Ramu ka hisaab?" -> same re-prompt                                        (query impossible)
```

Same shape for `amount_confirmation`, `name_disambiguation` and `reminder_approval`: there is no "start over" keyword, and the old intent stays queued forever until the user answers or the 10-minute TTL expires (`CONTEXT_EXPIRY_MINUTES`).

**Fix:** at the top of `_handle_with_context`, run `process_text_input(text)`; if it yields a *complete* transaction (`DB_WRITABLE_TYPES` + name + amount) or a read-only intent (`Query/AllCustomers/DateSummary/Undo/Export/InventoryRequest`), `consume_context()` and dispatch to `_handle_fresh`. Keep the pending intent only when the reply is a refinement.

---

## P1-4. A new transaction during a pending clarification silently replaces the old one

**File:** `app/engine.py:424–466`

`_handle_clarification_followup` only cancels on the read-only intents (line 425 list) — not on a fresh complete transaction. It then merges fields: new name + new amount + new type win, the pending intent is discarded, and one transaction is saved.

**Reproduced:** pending `Ramu 500 Debit` + user says `"Sita 200 jama"` →

```
reply = 'Ji, Sita ne ₹200 JAMA kiye. ✅'
saved = [('Sita', 'Credit', 20000)]      # Ramu ₹500 never recorded, no warning
```

**Fix:** same guard as P1-3 — if the new parse is a complete transaction with a different name/type, consume and `_handle_fresh` it.

---

## P1-5. LLM-down fallback destroys transactions

**File:** `app/brain.py:271–291`

```python
if any(w in tl for w in ["sale", "bikri", "aaj"]):
    return {..."transaction_type": "DateSummary"...}
```

This runs **before** amount/name parsing, so any message containing `aaj`, `sale` or `bikri` becomes a day-summary and the transaction is never recorded. This is not theoretical: `logs/munim_ji.log` contains 18 `Groq failed after retries` / `model_not_found` events, i.e. the fallback is the real production path whenever the model is unavailable.

**Reproduced (`_smart_rule_fallback`):**

| input | result |
|---|---|
| `NARESH 500 UDHAR AAJ` | `DateSummary` (amount dropped) |
| `aaj naresh ko 500 udhaar` | `DateSummary` |
| `500 ka sale kiya` | `DateSummary` |

**Fix:** parse the amount/name first; only fall back to `DateSummary` when no amount is present **and** a summary word (`hisaab`, `summary`, `total`, `kitna`) is present. Sample:

```python
amt_match = re.search(r"\b(\d+(?:\.\d{1,2})?)\b", t)
has_summary_word = any(w in tl for w in ["hisaab", "hisaab", "summary", "total"])
if amt_match is None and has_summary_word:
    ...DateSummary...
```

---

## P1-6. Fallback parser invents customer names from Hindi time/verb words

**File:** `app/brain.py:308–323`

`stop_words` omits `kal`, `aaj`, `parso`, `kiye`, `kiya`, `hua`, etc., so they are glued onto the customer name.

**Reproduced:**

| input | customer_name |
|---|---|
| `Ramu ko kal 500 udhaar` | `"Ramu Kal"` |
| `Ramu ne 500 jama kiye` | `"Ramu Kiye"` |

Consequences: a duplicate customer is created (`customers.owner_id+name_lower` unique key), the balance lands on the wrong party, and `Shop "Ramu Kal"` shows up in the app and in exports.

**Fix:** extend `stop_words` with time/adverb/verb tokens (`aaj, kal, parso, abhi, phir, bhi, kiye, kiya, hua, hui, gaya, gaye, diya, diye, liya, liye, kar, karo, kardo, karwa, karwaya`), and drop any token that is a date word before building the name.

---

# P2 — Logic and UX

## P2-1. Big cash-sale confirmation asks the wrong question with a literal `?`

**File:** `app/engine.py:631–637` (message key choice), `app/messages.py:33–44`

`CONFIRM_AMOUNT_SALE` is only used for `Sale`; `CashSale` falls through to `CONFIRM_AMOUNT_JAMA`.

**Reproduced:** `"6000 nakad bikri"` → `⚠️ Bada amount: ? ne ₹6,000 JAMA kiye?` — wrong verb and the customer name renders as `?` because `cname` is `None` for cash.

**Fix:** add `CONFIRM_AMOUNT_CASH` (`"⚠️ Bada amount: ₹{amount} ki nakad bikri? …"`) and choose the key by type; pass `name=""` and build the cash template without `{name}`.

## P2-2. "saath" (with) is parsed as 60

**File:** `app/services/amounts.py:34`

`"saath": 60` is a valid Hindi numeral but the far more common meaning is "with".

**Reproduced:** `extract_amount_rupees("Ramu ke saath paanch sau")` → **6500**.

**Fix:** remove `saath` from `_UNITS`, or accept it only when immediately followed by a scale word (`saath sau`).

## P2-3. Query for an unknown customer claims a zero balance

**File:** `app/engine.py:668–680`

`get_balance_by_name` returns `0` both for "no such customer" and for "settled", so the bot answers "Ji, Nobody ka hisaab nil (0) hai." instead of "no such customer".

**Fix:** have `get_balance_by_name` return `None` when the customer is missing (or add a `customer_exists` helper) and add a `CUSTOMER_NOT_FOUND` message.

## P2-4. `AllCustomers` silently truncates at 20

**File:** `app/engine.py:683–694`

25 customers → 20 lines, no "and 5 more" note, while the total correctly covers all 25. **Reproduced:** `has_Cust24=False`, total `₹2,500`.

**Fix:** append `msg("SUMMARY_MORE", count=len(customers) - 20)` when truncated.

## P2-5. Ambiguity prompt can render an empty numbered list

**Files:** `app/services/customers.py:24, 47–49`, `app/engine.py:409`

`resolve_customer("")` returns `{"action": "ambiguous", "options": []}`, and `_handle_disambiguation` re-renders `format_ambiguity_message(options)` even when `options == []` (e.g. a truncated/corrupted `pending_payload`, or a follow-up after the options list was lost). The user then sees "Ji, do grahak milte-julte hain:" followed by nothing and no valid way to answer (only `naya`/`0` still work).

**Fix:** return a distinct `"empty"` action from `resolve_customer("")` and answer with `CLARIFICATION_NAME`; in `_handle_disambiguation`, if `options` is empty, consume the context and treat the message as fresh.

## P2-6. `POST /api/customers` accepts `phone` and drops it

**File:** `app/routes/customers.py:28–46`

The request model has `phone`, the response echoes it, but `get_or_create_customer(owner_id, name)` never stores it and `Customer` has no `phone` column (the live DB still has a legacy `phone` column). Callers will believe the number was saved.

**Fix:** add `phone` to the model + migration and persist it, or remove it from the request/response.

## P2-7. Voice retry is permanently blocked after one failure

**File:** `app/routes/chat.py:110–141`

`/api/chat` clears the idempotency marker on exception (lines 98–104); `/api/voice` does not, so a failed or crashed voice request keeps its `ProcessedMessage` row and every retry with the same `client_msg_id` returns `409 Duplicate voice message.`

**Fix:** wrap the transcription + `handle_message` block in `try/except`, call `forget_message_processed(message_sid)` and re-raise.

## P2-8. Daily backup task can be garbage-collected and never fires at startup

**File:** `app/main.py:67–79`

`asyncio.create_task(_daily_backup_worker())` is created inside a deprecated `@app.on_event("startup")` handler (FastAPI 0.115 warns; `lifespan` already exists in the same module) and the reference is not retained, so CPython may collect the task. Also the loop sleeps 86,400 s **before** the first backup, so a process that restarts daily never backs up.

**Fix:** move it into `lifespan`, keep `app.state.backup_task = asyncio.create_task(...)`, and use `asyncio.sleep` until the next 02:00 local (`config.TIMEZONE`) rather than a flat 24 h sleep, or run one backup immediately on startup when today's file is missing.

---

# P3 — Hygiene, packaging, docs

1. **`docker-compose.yml` is 0 bytes.** `docker compose up` fails with "top-level object must be a mapping". Add a real service definition (build, ports, env_file, volume for `data/` and `exports/`).
2. **`README.md:39` tells users to run `python run.py`, but `run.py` is not in the archive.** (A stale `__pycache__/run.cpython-314.pyc` proves it once existed.) Either restore it or change the docs to `uvicorn app.main:app --reload`.
3. **`scripts/reset_db.py:43`** — `target = sys.argv[1] if len(sys.argv) > 1 else None` treats `--force` as an owner phone, so `python scripts/reset_db.py --force <phone>` wipes data for an owner literally named `--force` (i.e. nothing). Filter flags from `argv`.
4. **Wrong default Groq model.** `config.py:104` and `.env.example:54` default to `llama-3.3-70b-versatile`; the production log shows this key gets `404 model_not_found` and the retry loop only short-circuits on `401/403` (`app/brain.py:259`), so each message burns 3 attempts (~1.5 s) before falling back. Add `404` / `model_not_found` to the no-retry list and align the default with a model the key can access (the archive's `.env` already overrides it with `GROQ_MODEL=qwen/qwen3.8-27b`).
5. **`logger.error(..., exc_info=True)` outside an `except` block** — `app/brain.py:267` prints `NoneType: None` into the log (6 occurrences in `logs/munim_ji.log`). Drop `exc_info=True` or log inside the `except`.
6. **`app/main.py:2`** defines `logger` *before* the module docstring, so `app.main.__doc__` is `None`. Harmless, but the same ordering bug produced P0-1 in `config.py` — move the logger definition after the imports.
7. **Archive contents.** 62.5 MB / 8,926 files for ~2,800 lines of Python: `.venv/` (2,888 `.pyc`), `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`, `.patch_backup_20261023_164036/` (11 stale copies of live modules), `.git/`, `data/`, `logs/`, `exports/`. The `.patch_backup` folder is actively dangerous — it invites editing the wrong copy. Ship `git archive` output with only source + `requirements.txt` + `Dockerfile` + docs.

## Additional notes (not defects, worth knowing)

- **`DATABASE_URL` is advertised as generic but the code is SQLite-only.** `PRAGMA`-based migrations (`app/database.py:310–345`) silently no-op on PostgreSQL, and `list_customers_with_balance` orders by an expression over select aliases (`ORDER BY (owed - paid) DESC`, line 501) which PostgreSQL rejects. `consume_context` also relies on `DELETE … RETURNING` (needs SQLite ≥ 3.35). If Postgres is ever intended, these must change.
- **Nested DB sessions:** `get_customer_ledger` opens a session and then calls `get_balance_paise`, which opens a second one (`app/database.py:680–709`). Fine for SQLite, but it doubles pool usage; pass the session through.
- **`export_utils._cleanup_old_exports`** deletes *any* file in `EXPORTS_DIR` older than the retention window, not just `hisaab_*.xlsx`. Scope it to the filename pattern.
- **`migrate_owner_ids.py`** can violate `pending_contexts.sender_id` uniqueness when the destination phone already has a pending context.
- **`/docs`, `/redoc`, `/openapi.json` are public** (FastAPI defaults). Fine for now, but disable them in production with `FastAPI(docs_url=None, redoc_url=None)`.
- **`app/security.py:116–123`** — `revoke_token`/`is_token_revoked` are dead code (no logout endpoint, JWT has no `jti`). Either implement logout or remove.
- **`app/services/reminder.py:58–62`** passes `owner_id="PLACEHOLDER"` to `generate_upi_link`, which ignores `owner_id` and `customer_name` entirely. Dead parameters; clean up before someone "fixes" the placeholder.
- **`app/main.py` Sentry init** runs at import time, before `setup_logging()` (which runs in `lifespan`), so its log line can be lost. Move it into `lifespan`.

---

# Suggested fix order

1. **P0-1** (one-line `logger` fix) — unblocks production boot.
2. **P0-6** — rotate the Groq key and JWT secret, re-package the ZIP without `.env`/`data`/`logs`/`.venv`.
3. **P0-3 + P0-4 + P0-2** — the two broken destructive endpoints (account delete, reset) plus real PII scrubbing; add regression tests for all three.
4. **P0-5 / P1-1** — fail closed for dev OTP and use the peer IP.
5. **P1-2 / P1-3 / P1-4** — conversation-state handling (`is_affirmative`, escape hatch from pending contexts, no silent overwrite).
6. **P1-5 / P1-6 / P2-2** — fallback parser correctness (this is the path taken whenever Groq is down).
7. **P2/P3** — message keys, truncation notices, Dockerfile/compose/docs, cleanup.

A quick gate before re-shipping:

```bash
python -m pytest -q            # must be 62 passed
python -m ruff check .         # must be clean (currently 4 × F821)
docker compose config -q       # must parse
```

---

## Appendix — what the audit verified as correct

- Money maths: integer paise everywhere, half-up rounding via `Decimal`, `bool` rejected, `MAX_AMOUNT_RUPEES` enforced on create/patch (`app/money.py`, `tests/test_money.py`).
- SQL injection: all dynamic queries use bound parameters; no f-string SQL.
- Export IDOR: filename whitelist regex + per-owner HMAC token + `realpath` containment (`app/routes/mobile_api.py:98–129`); verified filename/regex round-trip.
- Excel formula injection: both sheets pass through `_safe_excel_text`; verified `'=1+1` with `data_type == 's'`.
- LLM output validation: required keys, type whitelist, NaN/Inf/negative rejection (`app/brain.py:176–214`).
- Double-confirmation race: `consume_context` deletes-and-returns atomically; test proves one transaction only.
- XSS/HTML in names/items is stripped by `sanitize_input_text`.
- `ruff` reports exactly 4 × `F821` — the two P0 bugs — plus 23 × `E402` / 3 × `I001` in `main.py`, `voice.py` and `brain.py`. All of them share one root cause: a statement (`logger = …`, `import re`, `import asyncio`) placed above the import block. Harmless in those three files, fatal in `config.py` (P0-1). Fixing the import order in all four removes every lint finding in the app package; there are no unused imports or shadowed names.

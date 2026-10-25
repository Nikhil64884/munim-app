# Munim Ji AI 🧾

An AI-powered accountant for Indian shopkeepers. Chat in Hindi/Hinglish (text or voice) and it records udhaar, jama, cash sales, gives balances, reports, and Excel exports.

**Mobile-app-only backend.** No WhatsApp.

## ✨ Features

- 🗣️ Voice & Text chat in Hindi/Hinglish
- 💰 Paise-accurate (integer paise, no float errors)
- 👥 Customer ledger with full history
- 🧠 AI-powered (Groq LLaMA + Whisper) with a rule-based fallback when Groq is unreachable
- 📊 Excel export with balance sheet
- 🔒 JWT auth, OTP login, rate limiting, audit log
- 📈 Sentry error monitoring
- 💾 Daily encrypted S3 backups (idempotent, run from the app lifespan)
- 🔄 Undo last entry within 5 minutes
- ⚠️ Large-amount confirmation (≥ ₹5000)
- 🎯 Name disambiguation

## 🚀 Quick Start

### Prerequisites
- Python 3.11+
- Groq API key from [console.groq.com](https://console.groq.com)

### Local Setup

```bash
cp .env.example .env
# Edit .env — set GROQ_API_KEY and JWT_SECRET

python -c "import secrets; print(secrets.token_urlsafe(48))"

python -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate
pip install -r requirements.txt

uvicorn app.main:app --reload --port 8000
```

Then open <http://127.0.0.1:8000/docs> for the interactive API docs.

### Running the tests

```bash
pytest -q
ruff check .
```

### Docker

```bash
docker compose up --build
```

The compose file mounts `./data`, `./exports` and `./logs` so the SQLite database
and generated files survive container restarts.

## ⚙️ Configuration notes

- `ENV=dev` (default in `.env.example`): dev OTP `123456` is accepted and echoed
  back by `/api/auth/send-otp` so the mobile UI can show it.
- `ENV=production`: the app refuses to start with `OTP_PROVIDER=dev` — it is an
  authentication bypass (any phone can log in with the fixed code). Use
  `OTP_PROVIDER=twilio` or `firebase` instead.
- `JWT_SECRET` must be at least 32 characters in production. Changing it
  invalidates existing logins and previously generated export links.
- `GROQ_MODEL` must be a model your Groq key can access; an unknown model
  falls back to the rule-based parser immediately (no retries).
- Never commit the real `.env`. The repository `.gitignore` already excludes it.

## 📁 Layout

```
app/
  main.py            FastAPI app + lifespan (logging, DB, backups)
  config.py          Environment validation (fails fast)
  database.py        SQLAlchemy models, queries, migrations
  engine.py          Message → intent → transaction orchestration
  brain.py           Groq LLM calls + rule-based fallback parser
  money.py           Integer-paise helpers and Indian formatting
  routes/            auth, chat/voice, customers, mobile API, health
  services/          amounts, audit, backup, customers, inventory, otp, reminder, voice
scripts/             DB reset, owner-id migration, S3 restore
tests/               pytest suite
```

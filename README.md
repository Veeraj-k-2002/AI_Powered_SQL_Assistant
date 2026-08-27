# 🤖 AI Powered SQL Assistant

Ask plain-English questions about any PostgreSQL database. The AI converts your question into a safe `SELECT` query, runs it, and explains the results — with charts when helpful.

Built with **FastAPI**, **Streamlit**, **LangChain**, **Groq (Llama 3)**, and **PostgreSQL**.

---

## ✨ Features

| Feature | Detail |
|---|---|
| Natural-language → SQL | Groq API (free) + Llama3-70b via LangChain |
| Safety | Only `SELECT` queries are ever executed |
| Schema-aware | Inspects your database at runtime — no manual mapping |
| Chat history | Every query stored per database; used as context |
| Auto-visualisation | LLM picks the best chart (bar, line, scatter, pie) |
| Multiple databases | Add as many PostgreSQL connections as you like |
| REST API | Full FastAPI backend with OpenAPI docs at `/docs` |

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Streamlit UI                         │
│   Sidebar: add/select/test DB   │   Chat: ask questions     │
└──────────────────┬──────────────────────────────────────────┘
                   │  HTTP (requests)
┌──────────────────▼──────────────────────────────────────────┐
│                      FastAPI Backend                         │
│  /api/v1/databases    /api/v1/chat/ask   /api/v1/history    │
│                                                             │
│  SqlChatService                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  1. Load DB config        5. Execute query (async)  │   │
│  │  2. Inspect schema        6. Summarise with LLM     │   │
│  │  3. Load chat history     7. Pick chart with LLM    │   │
│  │  4. Generate SQL (LLM)    8. Save + return          │   │
│  └─────────────────────────────────────────────────────┘   │
└──────────────────┬──────────────────────────────────────────┘
                   │
     ┌─────────────┴─────────────┐
     │                           │
┌────▼──────┐            ┌───────▼──────┐
│ App's DB  │            │ Your DB(s)   │
│ (configs  │            │ (queried     │
│  + hist.) │            │  read-only)  │
└───────────┘            └──────────────┘
```

---

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- PostgreSQL 14+ running locally (or Docker)
- A free [Groq API key](https://console.groq.com)

### 1 · Clone & install

```bash
git clone https://github.com/yourname/ai-sql-chatbot.git
cd ai-sql-chatbot

python -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2 · Configure environment

```bash
cp .env.example .env
# Edit .env and fill in GROQ_API_KEY and DATABASE_URL
```

### 3 · Create the app database

```bash
# Using psql
psql -U postgres -c "CREATE DATABASE sql_chatbot;"
```

Apply the versioned database migration before starting the backend:

```bash
alembic upgrade head
```

For production, set `DATABASE_ENCRYPTION_KEY` to a Fernet key. It encrypts saved
connection passwords at rest; keep the key in a secret manager and do not rotate
it without a credential re-encryption plan.

### 4 · Run the backend

```bash
cd backend
uvicorn app.main:app --reload --port 8000
```

Visit **http://localhost:8000/docs** for the interactive API docs.

### 5 · Run the frontend

Open a new terminal:

```bash
cd frontend
streamlit run app.py
```

Visit **http://localhost:8501** to open the chatbot.

---

## 🐳 Docker Compose (optional)

```bash
cp .env.example .env       # fill in GROQ_API_KEY
docker compose up --build
```

- Frontend: http://localhost:8501
- Backend:  http://localhost:8000/docs
- The `postgres` container becomes the app database automatically.

---

## 📁 Project Structure

```
ai-sql-chatbot/
├── backend/
│   └── app/
│       ├── core/
│       │   ├── config.py          # Settings (pydantic-settings, .env)
│       │   └── logger.py          # Structured logger
│       ├── db/
│       │   └── session.py         # Async SQLAlchemy engine + init_db
│       ├── models/
│       │   └── models.py          # ORM: ConnectedDatabase, ChatMessage
│       ├── schemas/
│       │   ├── chat_schema.py     # Pydantic in/out for chat
│       │   └── database_schema.py # Pydantic in/out for DB management
│       ├── services/
│       │   ├── sql_chat_service.py  ← MAIN LOGIC (LangChain + Groq)
│       │   ├── database_service.py  # CRUD for connections
│       │   └── history_service.py   # Read / delete chat history
│       ├── routes/
│       │   ├── chat_routes.py
│       │   ├── database_routes.py
│       │   └── history_routes.py
│       └── main.py                # FastAPI app factory
├── frontend/
│   ├── app.py                     # Streamlit UI (single file)
│   └── utils/
│       └── api_client.py          # HTTP helpers to call the backend
├── .env.example
├── requirements.txt
├── docker-compose.yml
├── Dockerfile.backend
├── Dockerfile.frontend
└── README.md
```

---

## 🔑 How the Chat Pipeline Works

```
User types a question
        │
        ▼
1. Load PostgreSQL schema (inspect tables + columns at runtime)
        │
        ▼
2. Retrieve last 4 successful queries (context window)
        │
        ▼
3. LangChain chain → Groq/Llama3 generates a SELECT query
        │
        ▼
4. Validate: starts with SELECT? No forbidden keywords?
        │
        ▼
5. Execute query async (SQLAlchemy + asyncpg)
        │
        ▼
6. LLM summarises results in 1–3 bullet points
        │
        ▼
7. LLM picks best chart type (bar/line/scatter/pie/none)
        │
        ▼
8. Store in app DB → return to Streamlit
```

---

## 🛡️ Security Notes

- **Only one parsed `SELECT` query is executed.** The backend uses `sqlglot`, rejects DDL/DML, system schemas and risky functions, applies a server-side `LIMIT`, and runs the statement inside a read-only transaction with a timeout.
- Database passwords are encrypted with Fernet before storage. Set `DATABASE_ENCRYPTION_KEY` in production; do not commit it.
- Use credentials for a database account that has only the minimum `CONNECT` and `SELECT` privileges. The read-only transaction is a defence in depth measure, not a replacement for database permissions.
- Consider adding authentication (e.g., Supabase Auth or a simple JWT layer) before deploying publicly.

## 🗃️ Database migrations

Schema changes are managed with Alembic, not by application startup. Run
`alembic upgrade head` during deployment. The initial migration also upgrades an
existing project database: it renames the legacy `password` column and encrypts
the saved values. Back up the database and set the final encryption key before
running it. Create future migrations with `alembic revision --autogenerate -m "describe change"`.

---

## 🗺️ Extending the Project

| Idea | Where to change |
|---|---|
| Add auth (JWT) | `backend/app/core/` + `backend/app/routes/` |
| Encrypt passwords | `database_service.py` create/get |
| Redis query cache | `sql_chat_service.py` `_lock` block |
| Support MySQL / SQLite | `_UserDatabase` class (change driver) |
| Export results as CSV | Streamlit: `st.download_button` on the DataFrame |
| Dark mode UI | `frontend/app.py` CSS variables |

---

## 📜 License

MIT — free to use for portfolio, learning, or commercial projects.

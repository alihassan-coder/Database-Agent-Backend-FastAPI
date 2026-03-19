# CortexAI — Backend

FastAPI backend for **CortexAI**: auth, chat CRUD, streaming AI responses (Groq + LangChain), optional web search (Tavily), and PostgreSQL-backed history with smart summarization.

---

## What this backend does

- **Auth:** Sign up and login via `/api/auth`; JWT access tokens, bcrypt password hashing.
- **Chats:** Create, list, get, and delete chats at `/api/chats`; each chat has a title and messages ordered by `position`.
- **Messages:** Append user messages and stream assistant replies; long conversations use a **summary** of older messages so the AI keeps context without exceeding the context window.
- **AI:** Groq LLM via LangChain; optional Tavily web search when the query needs up-to-date info.
- **Database:** PostgreSQL (e.g. Neon); SQLAlchemy ORM; tables: `users`, `chats`, `messages`.

---

## Folder structure

```
backend/
├── README.md              ← This file
├── main.py                ← FastAPI app, CORS, lifespan, routers
├── requirements.txt      ← Pip dependencies
├── pyproject.toml        ← Project metadata and Python deps (uv/pip)
├── .env                   ← Not in git: DATABASE_URL, GROQ_API_KEY, JWT_SECRET_KEY, etc.
│
├── config/
│   └── database_config.py ← SQLAlchemy engine, SessionLocal, Base, get_db
│
├── models/
│   └── __init__.py        ← SQLAlchemy models: User, Chat, Message
│
├── routes/
│   ├── user_routes.py     ← POST /api/auth/signup, POST /api/auth/login
│   └── agent_routes.py    ← /api/chats: list, create, get, delete, stream message
│
├── agent/
│   ├── prompts.py         ← System prompts (CortexAI, web search, summarization)
│   └── tools.py           ← get_llm, summarize_history_text, web_search, needs_web_search, etc.
│
└── utils/
    └── auth.py            ← create_access_token, get_current_user, get_password_hash, verify_password
```

- **Entry point:** `main.py`; runs with `uvicorn main:app`.
- **Auth:** JWT in `Authorization: Bearer <token>`; `get_current_user` used on protected routes.
- **Agent:** `agent_routes` uses `agent/tools` and `agent/prompts` for LLM and summarization.

---

## Setup

1. **Python:** 3.12+ recommended.
2. **PostgreSQL:** Create a database (e.g. [Neon](https://neon.tech)); you need the connection string for `DATABASE_URL`.
3. **Virtual env and install:**
   ```bash
   cd backend
   python -m venv .venv
   .venv\Scripts\activate   # Windows
   # source .venv/bin/activate   # macOS/Linux
   pip install -r requirements.txt
   ```
   Or with **uv:** `uv sync` (if using pyproject.toml).
4. **Environment:** Copy `.env.example` to `.env` (or create `.env`) and fill in the variables below. **Do not commit `.env`.**
5. **Run:**
   ```bash
   uvicorn main:app --reload
   ```
   API: **http://localhost:8000**  
   Interactive docs: **http://localhost:8000/docs**

---

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | PostgreSQL connection string (e.g. `postgresql://user:pass@host/db?sslmode=require`). |
| `GROQ_API_KEY` | Yes | Groq API key for the LLM. |
| `JWT_SECRET_KEY` | Yes | Secret used to sign JWT access tokens. |
| `JWT_ALGORITHM` | No | Default `HS256`. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No | Token expiry (default in code). |
| `BACKEND_CORS_ORIGINS` | No | Comma-separated origins (e.g. `http://localhost:5173`). Default allows common localhost URLs. |
| `TAVILY_API_KEY` | No | If set, enables web search for the agent. |
| `GROQ_MODEL` | No | Model name for Groq (e.g. `llama-3.1-70b-versatile`). |
| `PORT` | No | Port for uvicorn when run via `main.py` (default `8000`). |

---

## API overview

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Health/hello; returns app name and version. |
| POST | `/api/auth/signup` | Register; body: `{ "email", "password" }`. |
| POST | `/api/auth/login` | Login (form or JSON); returns `access_token`. |
| GET | `/api/chats` | List current user’s chats (JWT required). |
| POST | `/api/chats` | Create a new chat (JWT required). |
| GET | `/api/chats/{id}` | Get one chat with all messages (JWT required). |
| DELETE | `/api/chats/{id}` | Delete a chat and its messages (JWT required). |
| POST | `/api/chats/{id}/messages` | Send a user message; response is **streaming** (JWT required). |

Streaming: the frontend consumes the response body as a stream; the backend uses the LLM and (when needed) web search, then streams the assistant reply.

---

## Database Architecture & History Management

### Database Architecture

The application uses a relational database with three main tables that form a hierarchical structure for managing users, their chat sessions, and individual messages.

### Entity Relationship Diagram

```
┌─────────────┐
│    User     │
├─────────────┤
│ id (PK)     │
│ email       │
│ password    │
│ created_at  │
└──────┬──────┘
       │
       │ 1:N
       │
       ▼
┌─────────────┐
│    Chat     │
├─────────────┤
│ id (PK)     │
│ user_id (FK)│◄───┐
│ title       │    │
│ summary     │    │
│ created_at  │    │
│ updated_at  │    │
│ last_msg_at │    │
└──────┬──────┘    │
       │          │
       │ 1:N      │
       │          │
       ▼          │
┌─────────────┐  │
│   Message   │  │
├─────────────┤  │
│ id (PK)     │  │
│ chat_id (FK)├──┘
│ role        │
│ content     │
│ position    │
│ created_at  │
└─────────────┘
```

### Table Structures

#### 1. **Users Table** (`users`)
- **Purpose**: Stores user authentication and account information
- **Key Fields**:
  - `id`: Primary key (auto-increment integer)
  - `email`: Unique email address (indexed for fast lookups)
  - `password_hash`: Hashed password for authentication
  - `created_at`: Timestamp of account creation

#### 2. **Chats Table** (`chats`)
- **Purpose**: Represents a conversation session between a user and the AI assistant
- **Key Fields**:
  - `id`: Primary key (auto-increment integer)
  - `user_id`: Foreign key to `users.id` (CASCADE delete - if user is deleted, all their chats are deleted)
  - `title`: Optional title for the chat (auto-generated from first user message)
  - `summary`: **Critical field** - Stores AI-generated summary of older messages (see History Management section)
  - `created_at`: When the chat was created
  - `updated_at`: Automatically updated when chat is modified
  - `last_message_at`: Timestamp of the most recent message (used for sorting chats)

#### 3. **Messages Table** (`messages`)
- **Purpose**: Stores individual messages within a chat conversation
- **Key Fields**:
  - `id`: Primary key (auto-increment integer)
  - `chat_id`: Foreign key to `chats.id` (CASCADE delete - if chat is deleted, all messages are deleted)
  - `role`: Either `"user"` or `"assistant"` (string, max 50 chars)
  - `content`: The actual message text (TEXT type for unlimited length)
  - `position`: **Critical field** - Integer that determines message order within a chat
  - `created_at`: Timestamp of when the message was created

### Relationships & Constraints

1. **User → Chats**: One-to-Many relationship
   - One user can have multiple chat sessions
   - When a user is deleted, all their chats are automatically deleted (CASCADE)

2. **Chat → Messages**: One-to-Many relationship
   - One chat contains multiple messages
   - When a chat is deleted, all its messages are automatically deleted (CASCADE)
   - Messages are ordered by `position` field (ascending)

3. **Foreign Key Constraints**:
   - `chats.user_id` references `users.id` with `ON DELETE CASCADE`
   - `messages.chat_id` references `chats.id` with `ON DELETE CASCADE`

## History Management System

The application implements a sophisticated history management system that balances **complete data preservation** with **efficient context handling** for the AI model.

### Core Principle: Full History + Smart Summarization

The system maintains **100% of all messages** in the database while using intelligent summarization to manage context window limitations.

### How It Works

#### 1. **Message Position System**

Every message is assigned a sequential `position` integer that ensures correct ordering:

```python
# When a new user message is added:
last_position = max(Message.position) for chat_id
user_position = last_position + 1
assistant_position = last_position + 2
```

**Why this matters:**
- Messages are always retrieved in order: `ORDER BY position ASC`
- Even if messages are created simultaneously, position ensures correct sequence
- No reliance on timestamps for ordering (which can have race conditions)

#### 2. **Two-Tier History Strategy**

The system divides message history into two categories:

##### **Recent Messages (Last 5 messages)**
- **Stored**: Full, complete message content
- **Usage**: Sent directly to the AI model as context
- **Why**: Recent messages contain the most relevant context for generating responses

##### **Older Messages (Everything before the last 5)**
- **Stored**: Full, complete message content (still in database)
- **Usage**: Summarized into the `chat.summary` field
- **Why**: Reduces token usage while preserving important information

#### 3. **Summary Generation Process**

When a chat exceeds 5 messages, the system automatically summarizes older messages:

```python
if len(all_messages) > 5:
    older_messages = all_messages[:-5]  # Everything except last 5
    history_text = format_messages(older_messages)
    
    # Generate or update summary
    chat.summary = summarize_history_text(
        history_text, 
        existing_summary=chat.summary
    )
```

**Summary Update Logic:**
- **First time** (no existing summary): Creates a new summary from older messages
- **Subsequent times**: Updates existing summary by incorporating new older messages
- **Result**: Summary stays concise (few paragraphs) but accumulates important context

#### 4. **Context Assembly for AI**

When generating a response, the system constructs the context as follows:

```
[System Prompt]
  ↓
[Conversation Summary] (if exists)
  ↓
[Recent 5 Messages] (full content)
  ↓
[New User Message]
  ↓
[AI Generates Response]
```

**Example Flow:**

```
Chat has 25 messages total:
- Messages 1-20: Summarized into chat.summary
- Messages 21-24: Recent messages (full content)
- Message 25: New user message
- Message 26: AI response (to be generated)

Context sent to AI:
1. System prompt
2. Summary of messages 1-20
3. Full messages 21-25
4. AI generates message 26
```

### Key Benefits of This Approach

1. **Complete Data Preservation**
   - All messages are permanently stored
   - Users can always view full conversation history
   - No data loss, even for very long conversations

2. **Efficient Context Management**
   - Only recent messages consume full token budget
   - Older messages compressed into summaries
   - Enables handling of very long conversations (100+ messages)

3. **Incremental Summary Updates**
   - Summary is updated incrementally, not regenerated from scratch
   - More efficient than re-summarizing entire history each time
   - Maintains continuity of context

4. **Correct Message Ordering**
   - Position-based ordering prevents race conditions
   - Guarantees messages are processed in correct sequence
   - Works correctly even with concurrent requests

### Database Queries for History Management

#### Loading Messages in Order:
```sql
SELECT * FROM messages 
WHERE chat_id = ? 
ORDER BY position ASC
```

#### Getting Last Position:
```sql
SELECT MAX(position) FROM messages 
WHERE chat_id = ?
```

#### Finding Recent Messages:
```python
all_messages = db.query(Message)
    .filter(Message.chat_id == chat.id)
    .order_by(Message.position)
    .all()

recent_messages = all_messages[-5:]  # Last 5
older_messages = all_messages[:-5]   # Everything before last 5
```

### Best Practices Implemented

1. **Atomic Operations**: User message is saved before generating AI response
2. **Transaction Safety**: All related updates (message, summary, title) happen in single transaction
3. **Cascade Deletes**: Deleting a user/chat automatically cleans up related data
4. **Indexed Queries**: Foreign keys and frequently queried fields are indexed
5. **Timezone Awareness**: All timestamps use timezone-aware datetime objects

### Summary Function Details

The `summarize_history_text()` function:

1. **Uses AI Model**: Leverages the same LLM (Groq) to generate summaries
2. **Incremental Updates**: When summary exists, it updates rather than replaces
3. **Concise Output**: Prompts ensure summaries stay brief (few paragraphs)
4. **Context Preservation**: Important details, decisions, and user preferences are maintained

**Example Summary Update:**
```
Existing Summary: "User asked about Python programming. Discussed functions and classes."

New Messages to Add: "User asked about async/await. Explained event loops."

Updated Summary: "User asked about Python programming including functions, classes, 
and async/await patterns. Discussed event loops and asynchronous programming concepts."
```

## Conclusion

This architecture provides a robust, scalable solution for managing chat history that:
- ✅ Preserves all data permanently
- ✅ Handles conversations of any length efficiently
- ✅ Maintains correct message ordering
- ✅ Provides rich context to AI while managing token limits
- ✅ Enables fast retrieval and display of chat history

The combination of full message storage with intelligent summarization ensures both data integrity and optimal performance for AI interactions.


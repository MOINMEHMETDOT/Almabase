# Structured Questionnaire Answering Tool

## ⚠️ Please Read Before Testing

### 1. Backend Cold Start (Render Free Tier)
The backend is hosted on **Render's free tier**. When the service has been inactive, Render automatically spins it down to save resources. The first request after inactivity may take **30–60 seconds** to respond while the server wakes up. Please wait and try again if you see a connection error — it will work once the server is live.

### 2. Gemini API Rate Limit
This app uses **Google Gemini 2.5 Flash** for answer generation. The free tier for this specific model is limited to **10 requests per minute and 250 requests per day**. Each question in the questionnaire uses one request, meaning a 10-question form consumes 10 of those daily requests. If answers fail to generate, the daily quota or per-minute rate limit may be exhausted. In that case, please try again later or contact me and I will reset the key. In a production environment, a paid API key removes this limitation entirely.

---

## Live Links
- **Frontend:** https://almabase-gza6mvj4lzkgsyuskxsc8r.streamlit.app/
- **Backend API:** https://almabase-tx1q.onrender.com

## GitHub Repository
https://github.com/MOINMEHMETDOT/Almabase/

---

## What I Built

An end-to-end AI-powered tool that automates the completion of structured questionnaires using internal reference documents.

### Fictional Company: FluxHR AI

**Industry:** SaaS (Human Resources Technology)

**Description:** FluxHR AI is a cloud-based platform that uses generative AI to automate employee onboarding, performance reviews, and policy compliance for mid-sized technology companies. It reduces HR manual workload by up to 70% while improving employee engagement through personalized onboarding journeys.

### The Problem It Solves

Enterprise clients regularly send structured questionnaires to vendors — security audits, vendor assessments, compliance forms. These forms have 10–15 questions that must be answered using internal documentation. Doing this manually is slow and error-prone. This tool automates that entire workflow in one click.

---

## How It Works (User Flow)

1. **Sign up / Log in** — Create an account and log in with JWT-based authentication
2. **Upload reference documents** — Upload internal PDFs that act as the source of truth
3. **Upload questionnaire** — Upload the PDF form containing the questions to be answered
4. **Generate answers** — The AI reads each question, retrieves relevant content from reference docs, and generates a grounded answer with citations
5. **Review & edit** — Read and edit any answer before exporting
6. **Export PDF** — Download a completed document with all questions, answers, and citations preserved

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | Streamlit (Hosted on Streamlit Cloud) |
| Backend API | FastAPI (Hosted on Render Free Tier) |
| Database | PostgreSQL (Neon DB) |
| Vector Store | PGVector extension (Neon DB) |
| Embeddings | Google Gemini `text-embedding-004` |
| LLM | Google Gemini `gemini-2.5-flash` |
| RAG Framework | LangChain |
| Auth | JWT + bcrypt |
| PDF Export | ReportLab |

**Architecture Note:** To ensure robust, persistent storage suitable for production environments, I opted against local in-memory vector stores (like FAISS) and instead provisioned a live, cloud-hosted PostgreSQL database on **Neon DB**, utilizing the `pgvector` extension for scalable semantic retrieval. Unlike FAISS which resets on every server restart, Neon DB persists all vector embeddings across sessions and deployments.

---

## Project Structure

```
├── backend/
│   ├── main.py          # FastAPI backend (auth, RAG endpoints, PDF export)
│   ├── doc_rag.py       # RAG logic (doc ingestion, question parsing, answer generation)
│   ├── requirements.txt
│   └── .env             # Not committed — contains API keys
├── frontend/
│   ├── app.py           # Streamlit frontend (auth, upload, review, export screens)
│   └── requirements.txt
└── README.md
```

---

## Assumptions Made

- **PDF Questionnaires:** The questionnaire is uploaded as a PDF. Questions are detected automatically using pattern matching — numbered lines (e.g. `1.`, `Q1:`), lines ending with `?`, or every non-empty line as a fallback.
- **Fresh Ingestion:** Reference documents are re-ingested on every upload session to avoid stale or mixed data from previous sessions.
- **Strict Grounding:** Answers are grounded strictly in the uploaded reference documents. If the answer is not found in the docs, the system returns `"Not found in references."` and does not hallucinate.
- **Session Management:** One active questionnaire per session. Multiple questionnaires are stored in the DB and tied to the logged-in user.
- **CORS:** CORS is open (`allow_origins=["*"]`) as this is designed for demo/internal use. This should be restricted in production.

---

## Limitations

### API Rate Limits (Most Important)
- **Gemini 2.5 Flash** — The free tier is capped at **10 requests per minute and 250 requests per day**. This is the biggest practical limitation for testing.
- **Gemini Embeddings** — The `text-embedding-004` free tier allows up to 1,500 requests per day, which is sufficient for normal use.
- I chose to stay with Gemini 2.5 Flash despite the quota limits because it significantly outperforms alternative free models at following strict RAG instructions such as "only answer from context" and "return exactly: Not found in references."

### Render Free Tier (Backend Hosting)
- Render spins down free services after 15 minutes of inactivity
- The first request after a cold start takes **30–60 seconds** to respond
- There is no persistent background process — the server only runs when requests come in
- In production, a paid Render plan or a different hosting provider (e.g. Railway, Fly.io) would eliminate this entirely

### Vector Store (Shared Collection — Neon DB)
- All users currently share the same PGVector collection hosted on **Neon DB**. This means reference documents from one session can potentially affect another session's answers if re-ingestion is not triggered.
- The vector store is hosted on Neon DB (cloud PostgreSQL with pgvector extension), meaning embeddings **persist across sessions and deployments** — unlike in-memory stores like FAISS which reset on every restart. This is a deliberate architectural choice for production-readiness, but it means the shared collection must be managed carefully in a multi-user environment.
- In production, each user should have their own isolated vector collection.

### PDF Parsing
- Question detection relies on regex pattern matching. Unusual questionnaire formats (e.g. tables, multi-line questions, or questions without numbers or question marks) may not parse correctly.
- A more robust approach would be to use an LLM to extract questions from the PDF.

---

## Trade-offs

| Decision | Trade-off |
|---|---|
| Gemini 2.5 Flash over higher-quota free models | Better instruction following and grounding accuracy at the cost of stricter rate limits |
| Simple RAG chain instead of an agentic model | More reliable and strictly grounded in reference docs. An agent with web search would violate the "answers from references only" requirement |
| Cloud-hosted Neon DB over local FAISS | Persistent, production-ready vector storage across sessions and deployments at the cost of slightly higher setup complexity |
| JSON strings for questions and citations in PostgreSQL | Keeps the schema simple and avoids extra join tables. Not ideal for querying at scale |
| Global `rag_chain` in `main.py` | Simple for single-user local/demo use. In production this must be scoped per user or per session |
| ReportLab for PDF export | Lightweight with no extra dependencies. Lacks rich formatting but meets the structural requirement |
| Render free tier | Zero cost for a demo project. The cold start delay is acceptable for an assignment submission but not for production |

---

## What I Would Improve With More Time

- **Upgrade API plan** — Remove the daily request ceilings with a paid Gemini API key
- **Per-user vector collections** — Isolate each user's reference documents in their own PGVector collection on Neon DB
- **Streaming answers** — Stream results one by one to the UI as they are generated instead of waiting for all questions to finish
- **Smarter question parsing** — Use an LLM to extract questions from the questionnaire PDF instead of regex, handling edge cases like tables and multi-line questions
- **Support more file formats** — Accept `.docx` and `.xlsx` questionnaires in addition to PDF
- **Better PDF export** — Preserve the original questionnaire's exact formatting and insert answers inline rather than generating a new document from scratch
- **Rate limit handling in UI** — Show a clear, user-friendly message when the Gemini quota is exhausted instead of a generic error
- **Production hardening** — Restrict CORS, add request rate limiting, input validation, and proper error logging

---

## Local Setup Instructions

### 1. Clone the repo
```bash
git clone <your-repo-url>
```

### 2. Install backend dependencies
```bash
cd backend
pip install -r requirements.txt
```

### 3. Install frontend dependencies
```bash
cd frontend
pip install -r requirements.txt
```

### 4. Create a `.env` file inside the backend folder
```
DATABASE_URL=your_neon_postgres_connection_string
GOOGLE_API_KEY=your_google_gemini_api_key
JWT_SECRET=any_long_random_string
```

### 5. Run the backend
```bash
uvicorn main:app --reload --port 8000
```

### 6. Run the frontend (in a separate terminal)
```bash
streamlit run app.py
```

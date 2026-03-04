import os
import re
import pdfplumber
from dotenv import load_dotenv
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import PGVector
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
from langchain.chains import create_retrieval_chain
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain_core.prompts import ChatPromptTemplate

load_dotenv()

CONNECTION_STRING = os.getenv("DATABASE_URL")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")


def build_rag_chain(file_paths: list = None):
    """
    Builds a strict RAG chain for answering questionnaires based ONLY on provided documents.
    (Your original function — unchanged except metadata tagging on chunks)
    """
    embeddings = GoogleGenerativeAIEmbeddings(
        model="models/text-embedding-004",
        google_api_key=GOOGLE_API_KEY
    )

    vectorstore = PGVector(
        collection_name="reference_docs_v1",
        connection_string=CONNECTION_STRING,
        embedding_function=embeddings,
        use_jsonb=True
    )

    if file_paths:
        all_chunks = []
        for item in file_paths:
            # item can be (temp_path, real_filename) tuple or a plain string path
            if isinstance(item, tuple):
                file_path, real_name = item
            else:
                file_path = item
                real_name = os.path.basename(item)

            loader = PyPDFLoader(file_path)
            docs = loader.load()
            if not docs:
                continue

            # Tag each chunk with the REAL filename so citations are readable
            for doc in docs:
                doc.metadata["source_file"] = real_name
                # 'page' is already added by PyPDFLoader automatically

            splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=150)
            chunks = splitter.split_documents(docs)
            all_chunks.extend(chunks)

        if all_chunks:
            vectorstore.add_documents(all_chunks)
            print(f"✅ Processed and stored {len(file_paths)} reference documents.")

    retriever = vectorstore.as_retriever(search_kwargs={"k": 4})

    llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0)

    system_prompt = (
        "You are an expert assistant tasked with completing a structured questionnaire. "
        "You must answer the user's question based ONLY on the provided reference documents.\n\n"
        "CRITICAL INSTRUCTIONS:\n"
        "1. If the answer is NOT explicitly found in the provided context, you MUST reply EXACTLY with: 'Not found in references.'\n"
        "2. Do not use outside knowledge, guess, or infer information not written in the text.\n"
        "3. Keep your answers concise and directly address the question.\n\n"
        "Context: {context}"
    )

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "{input}"),
    ])

    question_answer_chain = create_stuff_documents_chain(llm, prompt)
    rag_chain = create_retrieval_chain(retriever, question_answer_chain)

    return rag_chain


# ── NEW FUNCTION 1: Parse questionnaire PDF into individual questions ──────────

def parse_questionnaire(pdf_path: str) -> list[dict]:
    """
    Opens a questionnaire PDF and extracts individual questions.
    Returns: [{"index": 1, "question": "..."}, {"index": 2, "question": "..."}, ...]
    """
    # Read all text lines from the PDF
    all_lines = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                all_lines.extend(text.splitlines())

    # Remove empty lines
    lines = [line.strip() for line in all_lines if line.strip()]

    # Filter out common heading patterns that are not questions
    def is_heading(line):
        lower = line.lower()
        skip_phrases = [
            "the questionnaire", "target audience", "instructions",
            "section", "part ", "please answer", "questionnaire"
        ]
        return any(lower.startswith(p) for p in skip_phrases)

    lines = [l for l in lines if not is_heading(l)]

    questions = []

    # Strategy 1: Look for numbered lines like "1.", "1)", "Q1.", "Q1:"
    numbered_pattern = re.compile(r'^(?:Q\s*)?(\d+)[.):\-]\s+(.+)', re.IGNORECASE)
    numbered_hits = []
    for line in lines:
        match = numbered_pattern.match(line)
        if match:
            numbered_hits.append({
                "index": int(match.group(1)),
                "question": match.group(2).strip()
            })

    if len(numbered_hits) >= 3:
        # Looks like a numbered questionnaire — use this
        questions = numbered_hits

    else:
        # Strategy 2: Pick lines that end with "?"
        question_lines = [l for l in lines if l.endswith("?")]
        if len(question_lines) >= 3:
            questions = [{"index": i + 1, "question": q} for i, q in enumerate(question_lines)]
        else:
            # Strategy 3: Fallback — treat every line as a question
            questions = [{"index": i + 1, "question": l} for i, l in enumerate(lines)]

    print(f"📋 Parsed {len(questions)} questions.")
    return questions


# ── NEW FUNCTION 2: Answer all questions and attach citations ─────────────────

def answer_questionnaire(questions: list[dict], rag_chain) -> list[dict]:
    """
    Runs each question through the RAG chain and collects answers + citations.
    Returns a list of results, one per question:
    [
        {
            "index": 1,
            "question": "What is your data retention policy?",
            "answer": "Data is retained for 7 years...",
            "citations": [
                {"source_file": "privacy_policy.pdf", "page": 3}
            ]
        },
        ...
    ]
    """
    results = []

    for q in questions:
        print(f"  Answering Q{q['index']}: {q['question'][:60]}...")

        # Invoke your existing RAG chain
        response = rag_chain.invoke({"input": q["question"]})

        answer = response.get("answer", "Not found in references.").strip()

        # Extract citation info from the retrieved source documents
        citations = []
        seen = set()
        source_docs = response.get("context", [])  # LangChain puts retrieved docs here

        for doc in source_docs:
            source_file = doc.metadata.get("source_file", "Unknown")
            page = doc.metadata.get("page", 0)
            key = (source_file, page)

            if key not in seen:
                seen.add(key)
                citations.append({
                    "source_file": source_file,
                    "page": page + 1,  # PyPDFLoader is 0-indexed, humans expect page 1
                    "snippet": doc.page_content[:200].replace("\n", " ").strip()
                })

        results.append({
            "index": q["index"],
            "question": q["question"],
            "answer": answer,
            "citations": citations
        })

    print(f"✅ Done. Answered {len(results)} questions.")
    return results

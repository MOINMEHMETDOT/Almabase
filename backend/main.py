# ─────────────────────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────────────────────
from fastapi import FastAPI, UploadFile, File, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import FileResponse

from pydantic import BaseModel          # For defining request/response shapes
from typing import List, Optional
from datetime import datetime, timedelta
from passlib.context import CryptContext  # For hashing passwords
import jwt                               # For creating/reading tokens (wristbands)
import tempfile
import os
import json
from dotenv import load_dotenv

# SQLAlchemy — lets us talk to PostgreSQL using Python (no raw SQL needed)
from sqlalchemy import create_engine, Column, Integer, String, Text, ForeignKey, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# Our RAG functions from doc_rag.py
from doc_rag import build_rag_chain, parse_questionnaire, answer_questionnaire

# PDF export library
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

load_dotenv()

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG — read secrets from .env file
# ─────────────────────────────────────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL")   # Your PostgreSQL connection string
JWT_SECRET   = os.getenv("JWT_SECRET", "changeme_use_a_long_random_string")
JWT_EXPIRY_HOURS = 24                       # Token expires after 24 hours

# ─────────────────────────────────────────────────────────────────────────────
# DATABASE SETUP
# ─────────────────────────────────────────────────────────────────────────────

# "engine" is the connection to PostgreSQL
engine = create_engine(DATABASE_URL)

# "SessionLocal" is a factory — call it to get a DB session (like opening a connection)
SessionLocal = sessionmaker(bind=engine)

# "Base" is the parent class all our table models will inherit from
Base = declarative_base()


# ── TABLE 1: Users ────────────────────────────────────────────────────────────
class User(Base):
    __tablename__ = "users"

    id             = Column(Integer, primary_key=True, index=True)
    email          = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    created_at     = Column(DateTime, default=datetime.utcnow)


# ── TABLE 2: Questionnaires ───────────────────────────────────────────────────
class Questionnaire(Base):
    __tablename__ = "questionnaires"

    id         = Column(Integer, primary_key=True, index=True)
    user_id    = Column(Integer, ForeignKey("users.id"), nullable=False)  # Which user owns this
    filename   = Column(String, nullable=False)
    questions  = Column(Text, nullable=False)   # We store the list as a JSON string
    created_at = Column(DateTime, default=datetime.utcnow)


# ── TABLE 3: Answers ──────────────────────────────────────────────────────────
class Answer(Base):
    __tablename__ = "answers"

    id                 = Column(Integer, primary_key=True, index=True)
    questionnaire_id   = Column(Integer, ForeignKey("questionnaires.id"), nullable=False)
    question_index     = Column(Integer, nullable=False)   # e.g. 1, 2, 3...
    question_text      = Column(Text, nullable=False)
    answer_text        = Column(Text, nullable=False)
    citations          = Column(Text, nullable=False)      # JSON string
    updated_at         = Column(DateTime, default=datetime.utcnow)


# Create all tables in PostgreSQL (runs only if they don't exist yet)
Base.metadata.create_all(bind=engine)


# ─────────────────────────────────────────────────────────────────────────────
# HELPER: Get a DB session
# FastAPI will call this automatically for endpoints that need the DB
# ─────────────────────────────────────────────────────────────────────────────
def get_db():
    db = SessionLocal()
    try:
        yield db       # Give the session to the endpoint
    finally:
        db.close()     # Always close it when done


# ─────────────────────────────────────────────────────────────────────────────
# AUTH HELPERS
# ─────────────────────────────────────────────────────────────────────────────

# This handles password hashing (never store plain text passwords!)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(password: str) -> str:
    """Turn plain password into a hashed string for safe storage."""
    return pwd_context.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
    """Check if the plain password matches the stored hash."""
    return pwd_context.verify(plain, hashed)

def create_token(user_id: int) -> str:
    """
    Create a JWT token (the 'wristband').
    It contains the user's ID and an expiry time.
    """
    payload = {
        "user_id": user_id,
        "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRY_HOURS)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

def decode_token(token: str) -> int:
    """
    Read the JWT token and return the user_id inside it.
    Raises an error if the token is invalid or expired.
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return payload["user_id"]
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired. Please log in again.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token. Please log in again.")


# ── Dependency: get the current logged-in user from the token ─────────────────
security = HTTPBearer()   # This reads the "Authorization: Bearer <token>" header

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> User:
    """
    FastAPI will call this automatically on protected endpoints.
    It reads the token from the request header, decodes it, and returns the User.
    """
    user_id = decode_token(credentials.credentials)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found.")
    return user


# ─────────────────────────────────────────────────────────────────────────────
# REQUEST / RESPONSE MODELS (Pydantic)
# These define what shape the JSON data should be
# ─────────────────────────────────────────────────────────────────────────────

class SignupRequest(BaseModel):
    email: str
    password: str

class LoginRequest(BaseModel):
    email: str
    password: str

class EditAnswerRequest(BaseModel):
    questionnaire_id: int
    question_index: int
    new_answer: str   # The edited answer text from the user


# ─────────────────────────────────────────────────────────────────────────────
# FASTAPI APP
# ─────────────────────────────────────────────────────────────────────────────
app = FastAPI(title="Questionnaire RAG API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global RAG chain — gets rebuilt when reference docs are uploaded
rag_chain = None


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT 1: Health check
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/")
def root():
    return {"status": "API is running"}


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT 2: Signup
# User sends email + password → we hash the password and save to DB
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/signup")
def signup(request: SignupRequest, db: Session = Depends(get_db)):
    # Check if email already exists
    existing = db.query(User).filter(User.email == request.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered.")

    # Hash the password and save new user
    new_user = User(
        email=request.email,
        hashed_password=hash_password(request.password)
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return {"message": "Account created successfully.", "user_id": new_user.id}


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT 3: Login
# User sends email + password → we verify and return a token
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/login")
def login(request: LoginRequest, db: Session = Depends(get_db)):
    # Find the user by email
    user = db.query(User).filter(User.email == request.email).first()

    # Verify password
    if not user or not verify_password(request.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    # Create and return token
    token = create_token(user.id)
    return {"access_token": token, "token_type": "bearer"}


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT 4: Upload Reference Documents
# User uploads PDFs → we ingest them into the vector DB
# This endpoint is PROTECTED — user must be logged in
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/upload-references")
async def upload_references(
    files: List[UploadFile] = File(...),
    current_user: User = Depends(get_current_user)   # 🔒 Protected
):
    global rag_chain

    try:
        temp_paths = []

        # Save each uploaded file temporarily so PyPDFLoader can read it
        # We store (temp_path, real_filename) so citations show the real name
        for file in files:
            if not file.filename.endswith(".pdf"):
                raise HTTPException(400, "Only PDF files allowed.")

            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(await file.read())
                temp_paths.append((tmp.name, file.filename))  # (temp path, real name)

        # Build the RAG chain with these documents
        rag_chain = build_rag_chain(file_paths=temp_paths)

        return {
            "success": True,
            "message": f"Uploaded and processed {len(files)} reference document(s)."
        }

    except Exception as e:
        raise HTTPException(500, str(e))


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT 5: Upload & Parse Questionnaire
# User uploads questionnaire PDF → we parse it into individual questions
# and save to DB
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/upload-questionnaire")
async def upload_questionnaire(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user)   # 🔒 Protected
,
    db: Session = Depends(get_db)
):
    if not file.filename.endswith(".pdf"):
        raise HTTPException(400, "Only PDF files allowed.")

    try:
        # Save file temporarily
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(await file.read())
            tmp_path = tmp.name

        # Parse questions out of the PDF
        questions = parse_questionnaire(tmp_path)

        if not questions:
            raise HTTPException(400, "No questions found in the uploaded PDF.")

        # Save questionnaire to DB (questions stored as JSON string)
        questionnaire = Questionnaire(
            user_id=current_user.id,
            filename=file.filename,
            questions=json.dumps(questions)
        )
        db.add(questionnaire)
        db.commit()
        db.refresh(questionnaire)

        return {
            "success": True,
            "questionnaire_id": questionnaire.id,
            "question_count": len(questions),
            "questions": questions   # Send back to frontend for preview
        }

    except Exception as e:
        raise HTTPException(500, str(e))


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT 6: Generate Answers
# Takes a questionnaire_id → runs all questions through RAG → saves answers to DB
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/generate-answers/{questionnaire_id}")
def generate_answers(
    questionnaire_id: int,
    current_user: User = Depends(get_current_user),   # 🔒 Protected
    db: Session = Depends(get_db)
):
    global rag_chain

    # Make sure reference docs were uploaded first
    if rag_chain is None:
        raise HTTPException(400, "Please upload reference documents first.")

    # Load the questionnaire from DB
    questionnaire = db.query(Questionnaire).filter(
        Questionnaire.id == questionnaire_id,
        Questionnaire.user_id == current_user.id   # Make sure it belongs to this user
    ).first()

    if not questionnaire:
        raise HTTPException(404, "Questionnaire not found.")

    # Load questions from the JSON string we saved earlier
    questions = json.loads(questionnaire.questions)

    # Run all questions through the RAG chain
    results = answer_questionnaire(questions, rag_chain)

    # Delete any old answers for this questionnaire (in case of re-generation)
    db.query(Answer).filter(Answer.questionnaire_id == questionnaire_id).delete()

    # Save all new answers to DB
    for result in results:
        answer_row = Answer(
            questionnaire_id=questionnaire_id,
            question_index=result["index"],
            question_text=result["question"],
            answer_text=result["answer"],
            citations=json.dumps(result["citations"])
        )
        db.add(answer_row)

    db.commit()

    # Return results to frontend for display
    return {
        "success": True,
        "questionnaire_id": questionnaire_id,
        "answers": results
    }


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT 7: Get Answers (for the review page)
# Frontend calls this to load saved answers for display/editing
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/answers/{questionnaire_id}")
def get_answers(
    questionnaire_id: int,
    current_user: User = Depends(get_current_user),   # 🔒 Protected
    db: Session = Depends(get_db)
):
    # Verify questionnaire belongs to user
    questionnaire = db.query(Questionnaire).filter(
        Questionnaire.id == questionnaire_id,
        Questionnaire.user_id == current_user.id
    ).first()

    if not questionnaire:
        raise HTTPException(404, "Questionnaire not found.")

    # Load all answers for this questionnaire, sorted by question number
    answers = db.query(Answer).filter(
        Answer.questionnaire_id == questionnaire_id
    ).order_by(Answer.question_index).all()

    return {
        "questionnaire_id": questionnaire_id,
        "filename": questionnaire.filename,
        "answers": [
            {
                "index": a.question_index,
                "question": a.question_text,
                "answer": a.answer_text,
                "citations": json.loads(a.citations)
            }
            for a in answers
        ]
    }


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT 8: Save Edited Answer
# User edits an answer in the UI → we save the new version to DB
# ─────────────────────────────────────────────────────────────────────────────
@app.put("/edit-answer")
def edit_answer(
    request: EditAnswerRequest,
    current_user: User = Depends(get_current_user),   # 🔒 Protected
    db: Session = Depends(get_db)
):
    # Find the specific answer row
    answer = db.query(Answer).filter(
        Answer.questionnaire_id == request.questionnaire_id,
        Answer.question_index == request.question_index
    ).first()

    if not answer:
        raise HTTPException(404, "Answer not found.")

    # Update the answer text and timestamp
    answer.answer_text = request.new_answer
    answer.updated_at = datetime.utcnow()
    db.commit()

    return {"success": True, "message": "Answer updated."}


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT 9: Export PDF
# Generates a downloadable PDF with all questions + answers + citations
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/export-pdf/{questionnaire_id}")
def export_pdf(
    questionnaire_id: int,
    current_user: User = Depends(get_current_user),   # 🔒 Protected
    db: Session = Depends(get_db)
):
    # Load questionnaire
    questionnaire = db.query(Questionnaire).filter(
        Questionnaire.id == questionnaire_id,
        Questionnaire.user_id == current_user.id
    ).first()

    if not questionnaire:
        raise HTTPException(404, "Questionnaire not found.")

    # Load answers sorted by question number
    answers = db.query(Answer).filter(
        Answer.questionnaire_id == questionnaire_id
    ).order_by(Answer.question_index).all()

    if not answers:
        raise HTTPException(400, "No answers found. Please generate answers first.")

    # ── Build the PDF ─────────────────────────────────────────────────────────
    output_path = tempfile.mktemp(suffix=".pdf")
    doc = SimpleDocTemplate(output_path, pagesize=letter)
    styles = getSampleStyleSheet()
    story = []   # "story" is a list of elements ReportLab will render top to bottom

    # Title
    story.append(Paragraph(f"Completed Questionnaire: {questionnaire.filename}", styles["Title"]))
    story.append(Spacer(1, 20))

    # One block per question
    for a in answers:
        citations = json.loads(a.citations)

        # Question
        story.append(Paragraph(f"Q{a.question_index}. {a.question_text}", styles["Heading3"]))

        # Answer
        story.append(Paragraph(f"<b>Answer:</b> {a.answer_text}", styles["Normal"]))

        # Citations (if any)
        if citations:
            citation_text = " | ".join(
                [f"{c['source_file']} (Page {c['page']})" for c in citations]
            )
            story.append(Paragraph(f"<i>Source: {citation_text}</i>", styles["Normal"]))
        else:
            story.append(Paragraph("<i>Source: Not found in references.</i>", styles["Normal"]))

        story.append(Spacer(1, 15))   # Gap between questions

    # Write PDF to file
    doc.build(story)

    # Send the file back to the user as a download
    return FileResponse(
        path=output_path,
        media_type="application/pdf",
        filename=f"answered_{questionnaire.filename}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# RUN THE SERVER
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
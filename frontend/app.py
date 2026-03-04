import streamlit as st
import requests

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────
API_URL = "https://almabase-tx1q.onrender.com"  # Change this for deployment

st.set_page_config(page_title="Questionnaire RAG Tool", layout="wide")


# ─────────────────────────────────────────────────────────────────────────────
# HELPER: Make authenticated API calls easily
# Every request after login needs the token in the header
# ─────────────────────────────────────────────────────────────────────────────
def auth_headers():
    """Returns the Authorization header using the stored token."""
    return {"Authorization": f"Bearer {st.session_state.token}"}


# ─────────────────────────────────────────────────────────────────────────────
# SESSION STATE INIT
# Streamlit resets variables on every interaction — session_state persists them
# ─────────────────────────────────────────────────────────────────────────────
if "token" not in st.session_state:
    st.session_state.token = None           # JWT token after login

if "questionnaire_id" not in st.session_state:
    st.session_state.questionnaire_id = None  # ID of uploaded questionnaire

if "answers" not in st.session_state:
    st.session_state.answers = []           # Generated answers list

if "refs_uploaded" not in st.session_state:
    st.session_state.refs_uploaded = False  # Whether reference docs are uploaded

if "page" not in st.session_state:
    st.session_state.page = "auth"          # Which screen we're on


# ─────────────────────────────────────────────────────────────────────────────
# SCREEN 1: LOGIN / SIGNUP
# Show this if the user is not logged in yet
# ─────────────────────────────────────────────────────────────────────────────
def show_auth_page():
    st.title("📄 Questionnaire RAG Tool")
    st.subheader("Login or Sign Up to continue")

    tab_login, tab_signup = st.tabs(["Login", "Sign Up"])

    # ── Login Tab ─────────────────────────────────────────────────────────────
    with tab_login:
        email    = st.text_input("Email", key="login_email")
        password = st.text_input("Password", type="password", key="login_password")

        if st.button("Login"):
            if not email or not password:
                st.error("Please enter both email and password.")
            else:
                try:
                    res = requests.post(
                        f"{API_URL}/login",
                        json={"email": email, "password": password}
                    )
                    if res.status_code == 200:
                        # Save token and move to upload screen
                        st.session_state.token = res.json()["access_token"]
                        st.session_state.page  = "upload"
                        st.success("Logged in successfully!")
                        st.rerun()
                    else:
                        st.error(res.json().get("detail", "Login failed."))
                except Exception as e:
                    st.error(f"Connection error: {e}")

    # ── Signup Tab ────────────────────────────────────────────────────────────
    with tab_signup:
        new_email    = st.text_input("Email", key="signup_email")
        new_password = st.text_input("Password", type="password", key="signup_password")

        if st.button("Create Account"):
            if not new_email or not new_password:
                st.error("Please enter both email and password.")
            else:
                try:
                    res = requests.post(
                        f"{API_URL}/signup",
                        json={"email": new_email, "password": new_password}
                    )
                    if res.status_code == 200:
                        st.success("Account created! Please log in.")
                    else:
                        st.error(res.json().get("detail", "Signup failed."))
                except Exception as e:
                    st.error(f"Connection error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# SCREEN 2: UPLOAD PAGE
# Upload reference docs + questionnaire PDF
# ─────────────────────────────────────────────────────────────────────────────
def show_upload_page():
    st.title("📂 Upload Documents")
    st.write("First upload your reference documents, then upload your questionnaire.")

    # ── Step 1: Reference Documents ───────────────────────────────────────────
    st.header("Step 1: Upload Reference Documents")
    st.caption("These are the PDFs the AI will use to answer questions.")

    ref_files = st.file_uploader(
        "Upload reference PDFs",
        type=["pdf"],
        accept_multiple_files=True,
        key="ref_uploader"
    )

    if st.button("Upload Reference Docs") and ref_files:
        with st.spinner("Uploading and processing..."):
            try:
                files = [("files", (f.name, f.read(), "application/pdf")) for f in ref_files]
                res = requests.post(
                    f"{API_URL}/upload-references",
                    files=files,
                    headers=auth_headers()
                )
                if res.status_code == 200:
                    st.session_state.refs_uploaded = True
                    st.success(f"✅ {res.json()['message']}")
                else:
                    st.error(res.json().get("detail", "Upload failed."))
            except Exception as e:
                st.error(f"Error: {e}")

    # Show a green check if already uploaded
    if st.session_state.refs_uploaded:
        st.success("✅ Reference documents are ready.")

    st.divider()

    # ── Step 2: Questionnaire ─────────────────────────────────────────────────
    st.header("Step 2: Upload Questionnaire")
    st.caption("Upload the questionnaire PDF. The system will parse each question automatically.")

    q_file = st.file_uploader("Upload questionnaire PDF", type=["pdf"], key="q_uploader")

    if st.button("Upload & Parse Questionnaire") and q_file:
        if not st.session_state.refs_uploaded:
            st.warning("Please upload reference documents first.")
        else:
            with st.spinner("Parsing questions..."):
                try:
                    res = requests.post(
                        f"{API_URL}/upload-questionnaire",
                        files={"file": (q_file.name, q_file.read(), "application/pdf")},
                        headers=auth_headers()
                    )
                    if res.status_code == 200:
                        data = res.json()
                        st.session_state.questionnaire_id = data["questionnaire_id"]
                        st.success(f"✅ Found {data['question_count']} questions!")

                        # Preview the parsed questions
                        st.subheader("Parsed Questions Preview:")
                        for q in data["questions"]:
                            st.write(f"**Q{q['index']}:** {q['question']}")

                    else:
                        st.error(res.json().get("detail", "Upload failed."))
                except Exception as e:
                    st.error(f"Error: {e}")

    st.divider()

    # ── Move to next step ─────────────────────────────────────────────────────
    if st.session_state.questionnaire_id:
        if st.button("➡️ Generate Answers", type="primary"):
            st.session_state.page = "generate"
            st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# SCREEN 3: GENERATE PAGE
# One big button — runs RAG on all questions
# ─────────────────────────────────────────────────────────────────────────────
def show_generate_page():
    st.title("⚙️ Generate Answers")
    st.write("Click the button below to generate answers for all questions using your reference documents.")

    if st.button("🚀 Generate Answers Now", type="primary"):
        with st.spinner("Running AI on all questions... this may take a minute."):
            try:
                res = requests.post(
                    f"{API_URL}/generate-answers/{st.session_state.questionnaire_id}",
                    headers=auth_headers()
                )
                if res.status_code == 200:
                    st.session_state.answers = res.json()["answers"]
                    st.success("✅ All answers generated!")
                    st.session_state.page = "review"
                    st.rerun()
                else:
                    st.error(res.json().get("detail", "Generation failed."))
            except Exception as e:
                st.error(f"Error: {e}")

    # Back button
    if st.button("⬅️ Back to Upload"):
        st.session_state.page = "upload"
        st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# SCREEN 4: REVIEW PAGE
# Show all Q&A, let user edit answers before export
# ─────────────────────────────────────────────────────────────────────────────
def show_review_page():
    st.title("📝 Review & Edit Answers")
    st.caption("Review each answer. You can edit any answer before exporting.")

    # Load answers from API if not already in session
    if not st.session_state.answers:
        try:
            res = requests.get(
                f"{API_URL}/answers/{st.session_state.questionnaire_id}",
                headers=auth_headers()
            )
            if res.status_code == 200:
                st.session_state.answers = res.json()["answers"]
        except Exception as e:
            st.error(f"Error loading answers: {e}")
            return

    if not st.session_state.answers:
        st.warning("No answers found. Please generate answers first.")
        return

    # ── Coverage Summary ──────────────────────────────────────────────────────
    total     = len(st.session_state.answers)
    answered  = sum(1 for a in st.session_state.answers if a["answer"] != "Not found in references.")
    not_found = total - answered

    st.subheader("📊 Coverage Summary")
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Questions", total)
    col2.metric("✅ Answered with Citations", answered)
    col3.metric("❌ Not Found in References", not_found)
    st.divider()

    # ── Display each Q&A with an edit option ──────────────────────────────────
    for i, item in enumerate(st.session_state.answers):
        with st.expander(f"Q{item['index']}: {item['question']}", expanded=True):

            # Editable text area — pre-filled with the generated answer
            edited = st.text_area(
                "Answer",
                value=item["answer"],
                key=f"answer_{i}",
                height=100
            )

            # Show citations + evidence snippets
            if item["citations"]:
                for c in item["citations"]:
                    st.caption(f"📄 {c['source_file']} — Page {c['page']}")
                    if c.get("snippet"):
                        st.info(f"📌 Evidence: *\"{c['snippet']}...\"*")
            else:
                st.caption("Source: Not found in references.")

            # Save button — only visible if the answer was changed
            if edited != item["answer"]:
                if st.button("💾 Save Edit", key=f"save_{i}"):
                    try:
                        res = requests.put(
                            f"{API_URL}/edit-answer",
                            json={
                                "questionnaire_id": st.session_state.questionnaire_id,
                                "question_index": item["index"],
                                "new_answer": edited
                            },
                            headers=auth_headers()
                        )
                        if res.status_code == 200:
                            # Update local session state so UI reflects the change
                            item["answer"] = edited
                            st.success("Saved!")
                        else:
                            st.error("Save failed.")
                    except Exception as e:
                        st.error(f"Error: {e}")

    st.divider()

    # ── Navigation ────────────────────────────────────────────────────────────
    col1, col2 = st.columns(2)
    with col1:
        if st.button("⬅️ Back"):
            st.session_state.page = "generate"
            st.rerun()
    with col2:
        if st.button("➡️ Export PDF", type="primary"):
            st.session_state.page = "export"
            st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# SCREEN 5: EXPORT PAGE
# Download the final PDF
# ─────────────────────────────────────────────────────────────────────────────
def show_export_page():
    st.title("📥 Export")
    st.write("Your questionnaire is ready. Click below to download the completed PDF.")

    if st.button("⬇️ Download PDF", type="primary"):
        with st.spinner("Building PDF..."):
            try:
                res = requests.get(
                    f"{API_URL}/export-pdf/{st.session_state.questionnaire_id}",
                    headers=auth_headers()
                )
                if res.status_code == 200:
                    # Streamlit download button needs raw bytes
                    st.download_button(
                        label="📄 Click here to save the PDF",
                        data=res.content,
                        file_name="answered_questionnaire.pdf",
                        mime="application/pdf"
                    )
                else:
                    st.error("Export failed. Please try again.")
            except Exception as e:
                st.error(f"Error: {e}")

    st.divider()

    # Start over button
    if st.button("🔄 Start New Questionnaire"):
        # Reset everything except the token (stay logged in)
        st.session_state.questionnaire_id = None
        st.session_state.answers          = []
        st.session_state.refs_uploaded    = False
        st.session_state.page             = "upload"
        st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# SIDEBAR — shown on all pages after login
# ─────────────────────────────────────────────────────────────────────────────
def show_sidebar():
    with st.sidebar:
        st.header("Navigation")

        # Show which step the user is on
        steps = {
            "upload":   "1. Upload Documents",
            "generate": "2. Generate Answers",
            "review":   "3. Review & Edit",
            "export":   "4. Export PDF",
        }
        for key, label in steps.items():
            if st.session_state.page == key:
                st.markdown(f"**▶ {label}**")   # Bold = current step
            else:
                st.write(f"  {label}")

        st.divider()

        # Logout button
        if st.button("🚪 Logout"):
            # Clear everything
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ROUTER
# Decides which screen to show based on st.session_state.page
# ─────────────────────────────────────────────────────────────────────────────

# If not logged in → always show auth page
if not st.session_state.token:
    show_auth_page()
else:
    # Show sidebar on all post-login pages
    show_sidebar()

    # Route to the correct screen
    if st.session_state.page == "upload":
        show_upload_page()
    elif st.session_state.page == "generate":
        show_generate_page()
    elif st.session_state.page == "review":
        show_review_page()
    elif st.session_state.page == "export":
        show_export_page()


import os
from datetime import datetime
import requests
from flask import Flask, render_template, request, jsonify
from sqlalchemy import create_engine, text

app = Flask(__name__)

# ==================================================
# DATABASE (PostgreSQL)
# ==================================================
DATABASE_URL = (os.environ.get("DATABASE_URL") or "").strip()
if not DATABASE_URL:
    raise RuntimeError("Missing DATABASE_URL env var")

# Render fix: postgres:// -> postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=5,
)

def init_db():
    with engine.begin() as conn:
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS contact_messages (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """))

init_db()

# ==================================================
# BREVO TRANSACTIONAL EMAIL
# ==================================================
def send_email(to_email: str, subject: str, html: str, text_msg: str | None = None):
    api_key = (os.environ.get("BREVO_API_KEY") or "").strip()
    from_email = (os.environ.get("MAIL_FROM") or "").strip()
    from_name = (os.environ.get("MAIL_FROM_NAME") or "CyberCare").strip()

    if not api_key:
        raise RuntimeError("Missing BREVO_API_KEY")
    if not from_email:
        raise RuntimeError("Missing MAIL_FROM")

    payload = {
        "sender": {"name": from_name, "email": from_email},
        "to": [{"email": to_email}],
        "subject": subject,
        "htmlContent": html,
    }

    if text_msg:
        payload["textContent"] = text_msg

    resp = requests.post(
        "https://api.brevo.com/v3/smtp/email",
        headers={
            "api-key": api_key,
            "accept": "application/json",
            "content-type": "application/json",
        },
        json=payload,
        timeout=20,
    )

    if resp.status_code not in (200, 201, 202):
        raise RuntimeError(f"Brevo error {resp.status_code}: {resp.text}")


# ==================================================
# ROUTES
# ==================================================
@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/contact")
def contact():
    data = request.get_json(force=True)

    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip()
    message = (data.get("message") or "").strip()

    if not name or not email or not message:
        return jsonify({"ok": False, "error": "Minden mező kötelező"}), 400

    # 1️⃣ DB mentés
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO contact_messages (name, email, message, created_at)
                VALUES (:name, :email, :message, :created_at)
            """),
            {
                "name": name,
                "email": email,
                "message": message,
                "created_at": datetime.utcnow(),
            }
        )

    admin_email = (os.environ.get("MAIL_TO") or os.environ.get("MAIL_FROM") or "").strip()
    if not admin_email:
        return jsonify({"ok": False, "error": "Admin email nincs beállítva"}), 500

    try:
        # 2️⃣ ADMIN EMAIL
        send_email(
            to_email=admin_email,
            subject="Új kapcsolatfelvétel – CyberCare",
            text_msg=f"Név: {name}\nEmail: {email}\n\n{message}",
            html=f"""
            <h2>Új kapcsolatfelvétel</h2>
            <p><strong>Név:</strong> {name}</p>
            <p><strong>Email:</strong> {email}</p>
            <p><strong>Üzenet:</strong></p>
            <div style="padding:12px;background:#f4f4f4;border-radius:8px">
              {message}
            </div>
            """
        )

        # 3️⃣ USER VISSZAIGAZOLÁS
        send_email(
            to_email=email,
            subject="Köszönjük megkeresését – CyberCare",
            text_msg="Köszönjük, hogy felvette velünk a kapcsolatot. Hamarosan válaszolunk.",
            html=f"""
            <p>Kedves {name}!</p>
            <p>Köszönjük, hogy felvette velünk a kapcsolatot.</p>
            <p>Hamarosan válaszolunk.</p>
            <br>
            <p>Üdvözlettel,<br><strong>CyberCare</strong></p>
            """
        )

    except Exception as e:
        return jsonify({"ok": False, "error": f"Email hiba: {e}"}), 503

    return jsonify({"ok": True})


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


# ==================================================
# RUN
# ==================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

import os
from datetime import datetime
import html as html_escape

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

def _column_exists(conn, table: str, column: str) -> bool:
    q = text("""
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = :t
          AND column_name = :c
        LIMIT 1
    """)
    return conn.execute(q, {"t": table, "c": column}).scalar() is not None

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

        # Opcionális mezők (a contact_us.html-ben vannak ilyenek)
        # company, phone, service, page
        if not _column_exists(conn, "contact_messages", "company"):
            conn.execute(text("ALTER TABLE contact_messages ADD COLUMN company TEXT;"))
        if not _column_exists(conn, "contact_messages", "phone"):
            conn.execute(text("ALTER TABLE contact_messages ADD COLUMN phone TEXT;"))
        if not _column_exists(conn, "contact_messages", "service"):
            conn.execute(text("ALTER TABLE contact_messages ADD COLUMN service TEXT;"))
        if not _column_exists(conn, "contact_messages", "page"):
            conn.execute(text("ALTER TABLE contact_messages ADD COLUMN page TEXT;"))

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
# HELPERS
# ==================================================
def _safe(s: str) -> str:
    # HTML emailben ne tudjon "beleírni" HTML-t
    return html_escape.escape(s or "").strip()

def _read_contact_payload():
    """
    Frontend kompatibilitás:
    - Ha JSON-t küldesz: {name,email,message,...} -> ok
    - Ha FormData-t küldesz (most ez van a HTML-ekben) -> ok
    """
    data = None

    if request.is_json:
        data = request.get_json(silent=True) or {}
    else:
        # FormData / x-www-form-urlencoded
        if request.form:
            data = request.form.to_dict(flat=True)
        else:
            # fallback: próbáljunk JSON-ként
            data = request.get_json(silent=True) or {}

    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip()
    message = (data.get("message") or "").strip()

    company = (data.get("company") or "").strip()
    phone = (data.get("phone") or "").strip()
    service = (data.get("service") or "").strip()
    page = (data.get("page") or "").strip() or (request.headers.get("Referer") or "")

    return {
        "name": name,
        "email": email,
        "message": message,
        "company": company,
        "phone": phone,
        "service": service,
        "page": page,
    }

def _response_ok(message: str):
    # A jelenlegi JS ezt várja: data.success és data.message
    return jsonify({"ok": True, "success": True, "message": message})

def _response_err(message: str, status: int = 400):
    return jsonify({"ok": False, "success": False, "message": message, "error": message}), status

# ==================================================
# ROUTES (PAGES)
# ==================================================
@app.get("/")
def root():
    return render_template("index.html")

# A nav linkjeid most így néznek ki: href="index.html", "about_us.html", stb.
# Ezért adunk "fájlnév" route-okat is, hogy ne kelljen átírnod.
@app.get("/index.html")
def page_index():
    return render_template("index.html")

@app.get("/about_us.html")
def page_about():
    return render_template("about_us.html")

@app.get("/our_services.html")
def page_services():
    return render_template("our_services.html")

@app.get("/contact_us.html")
def page_contact():
    return render_template("contact_us.html")

# (Opcionális, szebb URL-ek is)
@app.get("/about")
def about_alias():
    return render_template("about_us.html")

@app.get("/services")
def services_alias():
    return render_template("our_services.html")

@app.get("/contact")
def contact_alias():
    return render_template("contact_us.html")

# ==================================================
# API
# ==================================================
@app.post("/api/contact")
def api_contact():
    payload = _read_contact_payload()

    name = payload["name"]
    email = payload["email"]
    message = payload["message"]

    if not name or not email or not message:
        return _response_err("Minden mező kötelező: név, email, üzenet.", 400)

    # 1) DB mentés
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO contact_messages (name, email, message, company, phone, service, page, created_at)
                VALUES (:name, :email, :message, :company, :phone, :service, :page, :created_at)
            """),
            {
                "name": name,
                "email": email,
                "message": message,
                "company": payload["company"] or None,
                "phone": payload["phone"] or None,
                "service": payload["service"] or None,
                "page": payload["page"] or None,
                "created_at": datetime.utcnow(),
            }
        )

    admin_email = (os.environ.get("MAIL_TO") or os.environ.get("MAIL_FROM") or "").strip()
    if not admin_email:
        return _response_err("Admin email nincs beállítva (MAIL_TO vagy MAIL_FROM).", 500)

    # 2) Email küldés (admin + user)
    try:
        s_name = _safe(name)
        s_email = _safe(email)
        s_msg = _safe(message)
        s_company = _safe(payload["company"])
        s_phone = _safe(payload["phone"])
        s_service = _safe(payload["service"])
        s_page = _safe(payload["page"])

        admin_text = (
            f"Új kapcsolatfelvétel\n"
            f"Név: {name}\n"
            f"Email: {email}\n"
            f"Cég: {payload['company']}\n"
            f"Telefon: {payload['phone']}\n"
            f"Terület: {payload['service']}\n"
            f"Forrás: {payload['page']}\n\n"
            f"Üzenet:\n{message}\n"
        )

        admin_html = f"""
        <h2>Új kapcsolatfelvétel – CyberCare</h2>
        <p><strong>Név:</strong> {s_name}</p>
        <p><strong>Email:</strong> {s_email}</p>
        <p><strong>Cég:</strong> {s_company or "-"} </p>
        <p><strong>Telefon:</strong> {s_phone or "-"} </p>
        <p><strong>Érdeklődési terület:</strong> {s_service or "-"} </p>
        <p><strong>Forrás oldal:</strong> {s_page or "-"} </p>
        <p><strong>Üzenet:</strong></p>
        <div style="padding:12px;background:#f4f4f4;border-radius:8px;white-space:pre-wrap">
          {s_msg}
        </div>
        """

        send_email(
            to_email=admin_email,
            subject="Új kapcsolatfelvétel – CyberCare",
            text_msg=admin_text,
            html=admin_html,
        )

        user_html = f"""
        <p>Kedves {s_name}!</p>
        <p>Köszönjük, hogy felvette velünk a kapcsolatot. Üzenetét megkaptuk, hamarosan válaszolunk.</p>
        <p style="margin-top:16px;">Üdvözlettel,<br><strong>CyberCare</strong></p>
        """

        send_email(
            to_email=email,
            subject="Köszönjük megkeresését – CyberCare",
            text_msg="Köszönjük, hogy felvette velünk a kapcsolatot. Hamarosan válaszolunk.",
            html=user_html,
        )

    except Exception as e:
        return _response_err(f"Email hiba: {e}", 503)

    return _response_ok("Köszönjük! Üzenetét megkaptuk, hamarosan válaszolunk.")

@app.get("/health")
def health():
    return jsonify({"status": "ok"})

# ==================================================
# RUN
# ==================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

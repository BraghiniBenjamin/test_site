import os
import html as html_escape
import requests
import pathlib
import hashlib
import time
from datetime import datetime, timezone

from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    redirect,
    url_for,
    send_from_directory,
    abort,
    session,
)

from sqlalchemy import create_engine, text

app = Flask(__name__)

# ==================================================
# CONFIG / SECURITY
# ==================================================
app.secret_key = (os.environ.get("FLASK_SECRET_KEY") or "dev-secret-change-me").strip()

# ==================================================
# DATABASE (Render: ENV DATABASE_URL)
# ==================================================
def _db_url() -> str:
    """
    Render/Heroku kompat: néha 'postgres://', azt SQLAlchemy 'postgresql+psycopg://' formában szereti.
    A te esetedben 'postgresql://' is OK.
    """
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://") :]
    return url


def _engine():
    url = _db_url()
    if not url:
        raise RuntimeError("Missing DATABASE_URL environment variable")
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )


def _code_hash(code: str) -> str:
    # ha nincs PREVIEW_CODE_SALT, akkor fallback (kevésbé biztonságos)
    salt = (os.environ.get("PREVIEW_CODE_SALT") or "fallback-salt-change-me").strip()
    raw = f"{code}:{salt}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()



# Light rate-limit in-memory (Render egy példányon belül működik)
_PREVIEW_FAILS = {}  # ip -> (count, first_ts)


def _rate_limit_check(ip: str, max_tries=10, window_sec=600) -> bool:
    now = time.time()
    count, first_ts = _PREVIEW_FAILS.get(ip, (0, now))
    if now - first_ts > window_sec:
        _PREVIEW_FAILS[ip] = (0, now)
        return True
    return count < max_tries


def _rate_limit_hit(ip: str):
    now = time.time()
    count, first_ts = _PREVIEW_FAILS.get(ip, (0, now))
    if now - first_ts > 600:
        _PREVIEW_FAILS[ip] = (1, now)
    else:
        _PREVIEW_FAILS[ip] = (count + 1, first_ts)


# ==================================================
# DB INIT + SEED (AUTOMATIKUS)
# ==================================================
from sqlalchemy import text

def _ensure_preview_tables_and_seed():
    """
    - Létrehozza a preview táblákat, ha nem léteznek
    - Felveszi / frissíti a preview_pages rekordokat
    - Felveszi a hozzá tartozó kódokat (hash-elve), ha nincs bent
    """
    eng = _engine()

    create_pages = """
    CREATE TABLE IF NOT EXISTS preview_pages (
      page_key       TEXT PRIMARY KEY,
      template_name  TEXT NOT NULL,
      is_active      BOOLEAN NOT NULL DEFAULT TRUE,
      created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """

    create_codes = """
    CREATE TABLE IF NOT EXISTS preview_codes (
      id         BIGSERIAL PRIMARY KEY,
      code_hash  TEXT NOT NULL UNIQUE,
      page_key   TEXT NOT NULL REFERENCES preview_pages(page_key) ON DELETE CASCADE,
      is_active  BOOLEAN NOT NULL DEFAULT TRUE,
      expires_at TIMESTAMPTZ NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE INDEX IF NOT EXISTS idx_preview_codes_page_key ON preview_codes(page_key);
    """

    # ✅ Seed rekordok (bármennyit felvehetsz ide)
    seeds = [
        {
            "page_key": "George_Logistic_Team",
            "template_name": "George_Logistic_Team.html",
            "raw_code": "UL7dISvX4zdaLiJ5mvgKvmxn",
        },
        {
            "page_key": "Visegrádi Kincseskert Vendégház",
            "template_name": "vendeghaz_demo.html",
            "raw_code": "FDxTdbeyenFs0prF",  # <-- ide írj egy új kulcsot
        },
    ]

    with eng.begin() as conn:
        # Táblák
        conn.execute(text(create_pages))
        for stmt in create_codes.split(";"):
            s = stmt.strip()
            if s:
                conn.execute(text(s))

        # Seed + upsert + code insert
        for item in seeds:
            page_key = item["page_key"]
            template_name = item["template_name"]
            raw_code = item["raw_code"]
            code_hash = _code_hash(raw_code)

            # Page upsert
            conn.execute(
                text("""
                INSERT INTO preview_pages (page_key, template_name, is_active)
                VALUES (:k, :t, TRUE)
                ON CONFLICT (page_key) DO UPDATE
                SET template_name = EXCLUDED.template_name,
                    is_active = TRUE
                """),
                {"k": page_key, "t": template_name},
            )

            # Code insert (ha nincs)
            conn.execute(
                text("""
                INSERT INTO preview_codes (code_hash, page_key, is_active, expires_at)
                VALUES (:h, :k, TRUE, NULL)
                ON CONFLICT (code_hash) DO NOTHING
                """),
                {"h": code_hash, "k": page_key},
            )


# Induláskor egyszer fusson le
try:
    _ensure_preview_tables_and_seed()
except Exception as e:
    print(f"[WARN] Preview DB init/seed failed: {e}")



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
    return html_escape.escape((s or "").strip())


def _read_contact_payload():
    if request.is_json:
        data = request.get_json(silent=True) or {}
    else:
        data = request.form.to_dict(flat=True) if request.form else (request.get_json(silent=True) or {})

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
    return jsonify({"ok": True, "success": True, "message": message})


def _response_err(message: str, status: int = 400):
    return jsonify({"ok": False, "success": False, "message": message, "error": message}), status


# ==================================================
# ROUTES (PAGES)
# ==================================================
@app.get("/")
def root():
    return render_template("index.html")


@app.get("/home")
def home():
    return render_template("index.html")


@app.get("/rolunk")
def about():
    return render_template("about_us.html")


@app.get("/szolgaltatasaink")
def services():
    return render_template("our_services.html")


@app.get("/web-fejlesztes")
def web_fejlesztes():
    return redirect(url_for("web_development"), code=301)


@app.get("/szolgaltatasok")
def services_legacy_hu():
    return redirect(url_for("services"), code=301)


@app.get("/page_index")
def page_index():
    return redirect(url_for("home"), code=301)


@app.get("/webfejlesztes")
def web_development():
    return render_template("web_development.html")


@app.get("/kapcsolat")
def contact():
    return render_template("contact_us.html")


# ==================================================
# EXTRA ALIASOK / TEMPLATE-ALIAS
# ==================================================
@app.get("/about")
def about_alias():
    return redirect(url_for("about"), code=301)


@app.get("/services")
def services_alias():
    return redirect(url_for("services"), code=301)


@app.get("/contact")
def contact_alias():
    return redirect(url_for("contact"), code=301)


# ==================================================
# LEGACY / COMPAT (régi .html linkek -> új útvonal)
# ==================================================
@app.get("/index.html")
def legacy_index():
    return redirect(url_for("root"), code=301)


@app.get("/about_us.html")
def legacy_about():
    return redirect(url_for("about"), code=301)


@app.get("/our_services.html")
def legacy_services():
    return redirect(url_for("services"), code=301)


@app.get("/web_development.html")
def legacy_webdev():
    return redirect(url_for("web_development"), code=301)


@app.get("/contact_us.html")
def legacy_contact():
    return redirect(url_for("contact"), code=301)


# ==================================================
# API - CONTACT
# ==================================================
@app.post("/api/contact")
def api_contact():
    payload = _read_contact_payload()

    name = (payload.get("name") or "").strip()
    email = (payload.get("email") or "").strip()
    message = (payload.get("message") or "").strip()

    if not name or not email or not message:
        return _response_err("Minden mező kötelező: név, email, üzenet.", 400)

    admin_email = (os.environ.get("MAIL_TO") or os.environ.get("MAIL_FROM") or "").strip()
    if not admin_email:
        return _response_err("Admin email nincs beállítva (MAIL_TO vagy MAIL_FROM).", 500)

    try:
        s_name = _safe(name)
        s_email = _safe(email)
        s_msg = _safe(message)
        s_company = _safe(payload.get("company"))
        s_phone = _safe(payload.get("phone"))
        s_service = _safe(payload.get("service"))
        s_page = _safe(payload.get("page"))

        admin_text = (
            f"Új kapcsolatfelvétel\n"
            f"Név: {name}\n"
            f"Email: {email}\n"
            f"Cég: {payload.get('company')}\n"
            f"Telefon: {payload.get('phone')}\n"
            f"Érdeklődési terület: {payload.get('service')}\n"
            f"Forrás: {payload.get('page')}\n\n"
            f"Üzenet:\n{message}\n"
        )

        admin_html = f"""<!DOCTYPE html>
<html lang="hu"><head><meta charset="UTF-8"></head>
<body>
  <h2>Új kapcsolatfelvétel</h2>
  <p><b>Név:</b> {s_name}<br><b>Email:</b> {s_email}<br><b>Cég:</b> {s_company or "-"}<br>
  <b>Telefon:</b> {s_phone or "-"}<br><b>Érdeklődési terület:</b> {s_service or "-"}<br><b>Forrás:</b> {s_page or "-"}<br></p>
  <pre style="white-space:pre-wrap">{s_msg}</pre>
  <p><a href="mailto:{s_email}">Válasz írása</a></p>
</body></html>"""

        send_email(
            to_email=admin_email,
            subject="Új kapcsolatfelvétel – CyberCare",
            text_msg=admin_text,
            html=admin_html,
        )

        user_html = f"""<!DOCTYPE html>
<html lang="hu"><head><meta charset="UTF-8"></head>
<body>
  <h2>Köszönjük megkeresését!</h2>
  <p>Kedves {s_name}! Üzenetét megkaptuk, hamarosan válaszolunk.</p>
</body></html>"""

        send_email(
            to_email=email,
            subject="Köszönjük megkeresését – CyberCare",
            text_msg="Köszönjük, hogy felvette velünk a kapcsolatot. Üzenetét megkaptuk, hamarosan válaszolunk.",
            html=user_html,
        )

    except Exception as e:
        return _response_err(f"Email hiba: {e}", 503)

    return _response_ok("Köszönjük! Üzenetét megkaptuk, hamarosan válaszolunk.")


# ==================================================
# PREVIEW / FEJLESZTÉS ALATT (FULL)
# ==================================================
@app.route("/fejlesztes-alatt", methods=["GET", "POST"])
def fejlesztes_alatt():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr) or "unknown"

    if request.method == "GET":
        return render_template("preview_gate.html", error=None, info=None)

    if not _rate_limit_check(ip):
        return render_template("preview_gate.html", error="Túl sok próbálkozás. Próbáld később.", info=None), 429

    code = (request.form.get("code") or "").strip()
    if not code:
        _rate_limit_hit(ip)
        return render_template("preview_gate.html", error="A kód megadása kötelező.", info=None), 400

    try:
        ch = _code_hash(code)

        eng = _engine()
        with eng.connect() as conn:
            row = conn.execute(
                text("""
                    SELECT c.page_key, c.expires_at, p.template_name
                    FROM preview_codes c
                    JOIN preview_pages p ON p.page_key = c.page_key
                    WHERE c.code_hash = :h
                      AND c.is_active = TRUE
                      AND p.is_active = TRUE
                    LIMIT 1
                """),
                {"h": ch},
            ).mappings().first()

        if not row:
            _rate_limit_hit(ip)
            return render_template("preview_gate.html", error="Hibás kód.", info=None), 401

        expires_at = row.get("expires_at")
        if expires_at:
            expires_dt = expires_at
            if isinstance(expires_dt, str):
                expires_dt = datetime.fromisoformat(expires_dt.replace("Z", "+00:00"))
            if expires_dt.tzinfo is None:
                expires_dt = expires_dt.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > expires_dt:
                _rate_limit_hit(ip)
                return render_template("preview_gate.html", error="A kód lejárt.", info=None), 401

        session["preview_page_key"] = row["page_key"]
        return redirect(url_for("fejlesztes_alatt_page", page_key=row["page_key"]), code=302)

    except Exception as e:
        return render_template("preview_gate.html", error=f"Preview hiba: {e}", info=None), 503


@app.get("/fejlesztes-alatt/<page_key>")
def fejlesztes_alatt_page(page_key):
    allowed = session.get("preview_page_key")
    if not allowed or allowed != page_key:
        return abort(403)

    try:
        eng = _engine()
        with eng.connect() as conn:
            row = conn.execute(
                text("""
                    SELECT template_name
                    FROM preview_pages
                    WHERE page_key = :k AND is_active = TRUE
                    LIMIT 1
                """),
                {"k": page_key},
            ).mappings().first()

        if not row:
            return abort(404)

        template_name = row["template_name"]
        return render_template(template_name)

    except Exception:
        return abort(503)


# ==================================================
# HEALTH
# ==================================================
@app.get("/health")
def health():
    return jsonify({"status": "ok"})


# ==================================================
# DEMO OLDALAK
# ==================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_ROOT = os.path.join(BASE_DIR, "templates")


@app.get("/demo/<name>")
def demo_page(name):
    safe = str(pathlib.PurePosixPath(name))
    if "/" in safe or "\\" in safe or safe.startswith("."):
        abort(404)

    full_html = os.path.join(TEMPLATES_ROOT, f"{safe}.html")
    if not os.path.isfile(full_html):
        abort(404)

    return render_template(f"{safe}.html")


@app.get("/demo_assets/<path:filename>")
def demo_assets(filename):
    safe = str(pathlib.PurePosixPath(filename))
    full_path = os.path.join(TEMPLATES_ROOT, safe)
    if not os.path.isfile(full_path):
        abort(404)

    return send_from_directory(TEMPLATES_ROOT, safe)


# ==================================================
# DB TEST
# ==================================================
@app.get("/db-test")
def db_test():
    try:
        eng = _engine()
        with eng.connect() as conn:
            r = conn.execute(text("SELECT 1")).scalar()
        return jsonify({"db": "ok", "result": r})
    except Exception as e:
        return jsonify({"db": "error", "error": str(e)}), 500


# ==================================================
# RUN
# ==================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

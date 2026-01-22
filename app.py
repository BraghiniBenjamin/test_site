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
    salt = (os.environ.get("PREVIEW_CODE_SALT") or "").strip()
    if not salt:
        raise RuntimeError("Missing PREVIEW_CODE_SALT environment variable")
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
    """
    Frontend kompatibilitás:
    - JSON: {name,email,message,...}
    - FormData: name=...&email=... stb.
    """
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

    # hasznos adminnak: honnan jött
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
        # Biztonságos (HTML-escape) változók
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

        admin_html = f"""
<!DOCTYPE html>
<html lang="hu">
<head>
  <meta charset="UTF-8">
  <style>
    body {{ margin: 0; padding: 0; font-family: 'Segoe UI', sans-serif; }}
    .email-container {{ max-width: 600px; margin: 0 auto; background: #ffffff; }}
    .email-header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 40px 30px; text-align: center; color: #ffffff; }}
    .email-header h1 {{ font-size: 28px; margin: 0 0 8px 0; font-weight: 600; }}
    .email-header p {{ font-size: 14px; margin: 0; opacity: 0.95; }}
    .email-body {{ padding: 40px 30px; }}
    .greeting {{ font-size: 18px; color: #1a1a1a; margin-bottom: 20px; font-weight: 500; }}
    .content-text {{ font-size: 15px; line-height: 1.6; color: #4a4a4a; margin-bottom: 24px; }}
    .info-card {{ background: #f8f9fa; border-left: 4px solid #667eea; padding: 20px; border-radius: 8px; margin: 24px 0; }}
    .info-row {{ display: flex; padding: 8px 0; border-bottom: 1px solid #e9ecef; }}
    .info-row:last-child {{ border-bottom: none; }}
    .info-label {{ font-weight: 600; color: #667eea; min-width: 140px; font-size: 14px; }}
    .info-value {{ color: #2d3748; font-size: 14px; }}
    .message-box {{ background: #f8f9fa; padding: 20px; border-radius: 8px; margin: 24px 0; border: 1px solid #e9ecef; }}
    .message-box p {{ font-size: 14px; line-height: 1.6; color: #4a4a4a; white-space: pre-wrap; word-wrap: break-word; margin: 0; }}
    .cta-button {{ display: inline-block; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: #ffffff; padding: 14px 32px; text-decoration: none; border-radius: 8px; font-weight: 600; font-size: 15px; margin: 24px 0; }}
    .email-footer {{ background: #f8f9fa; padding: 30px; text-align: center; border-top: 1px solid #e9ecef; }}
    .email-footer p {{ font-size: 13px; color: #6c757d; margin-bottom: 8px; }}
    .company-name {{ color: #667eea; font-weight: 700; font-size: 16px; margin-top: 12px; }}
  </style>
</head>
<body>
  <div class="email-container">
    <div class="email-header">
      <h1>🔔 Új Kapcsolatfelvétel</h1>
      <p>Beérkezett üzenet a weboldalról</p>
    </div>
    <div class="email-body">
      <p class="greeting">Új megkeresés érkezett!</p>
      <p class="content-text">Egy látogató érdeklődik a szolgáltatásaidról:</p>
      <div class="info-card">
        <div class="info-row"><span class="info-label">Név:</span><span class="info-value">{s_name}</span></div>
        <div class="info-row"><span class="info-label">Email:</span><span class="info-value">{s_email}</span></div>
        <div class="info-row"><span class="info-label">Cég:</span><span class="info-value">{s_company or "-"}</span></div>
        <div class="info-row"><span class="info-label">Telefon:</span><span class="info-value">{s_phone or "-"}</span></div>
        <div class="info-row"><span class="info-label">Érdeklődési terület:</span><span class="info-value">{s_service or "-"}</span></div>
        <div class="info-row"><span class="info-label">Forrás oldal:</span><span class="info-value">{s_page or "-"}</span></div>
      </div>
      <p class="content-text"><strong>Üzenet:</strong></p>
      <div class="message-box"><p>{s_msg}</p></div>
      <a href="mailto:{s_email}" class="cta-button">Válasz írása</a>
    </div>
    <div class="email-footer">
      <p>Ez egy automatikus értesítés a CyberCare weboldal kapcsolatfelvételi űrlapjából.</p>
      <p class="company-name">CyberCare</p>
    </div>
  </div>
</body>
</html>
"""

        send_email(
            to_email=admin_email,
            subject="Új kapcsolatfelvétel – CyberCare",
            text_msg=admin_text,
            html=admin_html,
        )

        user_html = f"""
<!DOCTYPE html>
<html lang="hu">
<head>
  <meta charset="UTF-8">
  <style>
    body {{ margin: 0; padding: 0; font-family: 'Segoe UI', sans-serif; }}
    .email-container {{ max-width: 600px; margin: 0 auto; background: #ffffff; }}
    .email-header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 40px 30px; text-align: center; color: #ffffff; }}
    .email-header h1 {{ font-size: 28px; margin: 0 0 8px 0; font-weight: 600; }}
    .email-header p {{ font-size: 14px; margin: 0; opacity: 0.95; }}
    .email-body {{ padding: 40px 30px; }}
    .greeting {{ font-size: 18px; color: #1a1a1a; margin-bottom: 20px; font-weight: 500; }}
    .content-text {{ font-size: 15px; line-height: 1.6; color: #4a4a4a; margin-bottom: 24px; }}
    .email-footer {{ background: #f8f9fa; padding: 30px; text-align: center; border-top: 1px solid #e9ecef; }}
    .email-footer p {{ font-size: 13px; color: #6c757d; margin-bottom: 8px; }}
    .company-name {{ color: #667eea; font-weight: 700; font-size: 16px; margin-top: 12px; }}
  </style>
</head>
<body>
  <div class="email-container">
    <div class="email-header">
      <h1>✅ Köszönjük megkeresését!</h1>
      <p>Üzenetét sikeresen megkaptuk</p>
    </div>
    <div class="email-body">
      <p class="greeting">Kedves {s_name}!</p>
      <p class="content-text">Köszönjük, hogy felvette velünk a kapcsolatot. Üzenetét megkaptuk, és kollégáink hamarosan válaszolnak.</p>
      <p class="content-text">Csapatunk 24-48 órán belül értesíti Önt az Ön érdeklődési területével kapcsolatban.</p>
      <p class="content-text" style="margin-top: 32px;">Üdvözlettel,<br><strong style="color: #667eea;">A CyberCare csapata</strong></p>
    </div>
    <div class="email-footer">
      <p>Ha bármilyen kérdése van, keressen minket bizalommal!</p>
      <p class="company-name">CyberCare</p>
    </div>
  </div>
</body>
</html>
"""

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
# PREVIEW / FEJLESZTÉS ALATT (NEW BACKEND)
# ==================================================
@app.route("/fejlesztes-alatt", methods=["GET", "POST"])
def fejlesztes_alatt():
    """
    GET: egyszerű kód bekérő oldal (később cseréljük a te design template-edre)
    POST: kód ellenőrzés DB-ből -> redirect a megfelelő preview oldalra
    """
    ip = request.headers.get("X-Forwarded-For", request.remote_addr) or "unknown"

    if request.method == "GET":
        # Frontendet majd küldöd – addig minimál
        return """
        <!doctype html><html lang="hu"><head><meta charset="utf-8"><title>Fejlesztés alatt</title></head>
        <body style="font-family:Segoe UI, sans-serif;max-width:520px;margin:40px auto;">
          <h2>Fejlesztés alatt lévő oldal megtekintése</h2>
          <form method="post">
            <label>Kód</label><br>
            <input name="code" type="password" style="width:100%;padding:10px;margin:10px 0;">
            <button type="submit" style="padding:10px 14px;">Megnyitás</button>
          </form>
        </body></html>
        """

    # POST
    if not _rate_limit_check(ip):
        return _response_err("Túl sok próbálkozás. Próbáld később.", 429)

    code = (request.form.get("code") or "").strip()
    if not code:
        _rate_limit_hit(ip)
        return _response_err("A kód megadása kötelező.", 400)

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
            return _response_err("Hibás kód.", 401)

        expires_at = row.get("expires_at")
        if expires_at:
            # Postgres driver általában datetime-et ad
            expires_dt = expires_at
            if isinstance(expires_dt, str):
                expires_dt = datetime.fromisoformat(expires_dt.replace("Z", "+00:00"))

            if expires_dt.tzinfo is None:
                expires_dt = expires_dt.replace(tzinfo=timezone.utc)

            if datetime.now(timezone.utc) > expires_dt:
                _rate_limit_hit(ip)
                return _response_err("A kód lejárt.", 401)

        session["preview_page_key"] = row["page_key"]
        return redirect(url_for("fejlesztes_alatt_page", page_key=row["page_key"]), code=302)

    except Exception as e:
        return _response_err(f"Preview hiba: {e}", 503)


@app.get("/fejlesztes-alatt/<page_key>")
def fejlesztes_alatt_page(page_key):
    """
    Csak akkor engedjük, ha a session-ben megvan az engedély.
    A template_name DB-ből jön (pl. 'dev/uj_landing_v2.html').
    """
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
# DEMO OLDALAK (demo_oldalak mappa kiszolgálása)
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
# OPTIONAL: DB CONNECTION TEST (hasznos Renderen)
#   (ha nem kell, törölheted)
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

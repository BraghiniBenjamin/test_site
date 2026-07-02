import os
import html as html_escape
import pathlib
import hashlib
import time
import smtplib
from email.message import EmailMessage
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
    salt = (os.environ.get("PREVIEW_CODE_SALT") or "fallback-salt-change-me").strip()
    raw = f"{code}:{salt}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


# Light rate-limit in-memory (Render egy példányon belül működik)
_PREVIEW_FAILS = {}


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
def _ensure_preview_tables_and_seed():
    """
    - Létrehozza a preview táblákat, ha nem léteznek
    - Felveszi / frissíti a preview_pages rekordokat
    - Preview kódokat csak ENV-ből vesz fel, hogy ne legyenek hardcode-olva a repo-ban.
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

    seeds = [
        {
            "page_key": "George_Logistic_Team",
            "template_name": "George_Logistic_Team.html",
            "raw_code": (os.environ.get("PREVIEW_CODE_GEORGE_LOGISTIC_TEAM") or "").strip(),
        },
        {
            "page_key": "Visegrádi Kincseskert Vendégház",
            "template_name": "vendeghaz_demo.html",
            "raw_code": (os.environ.get("PREVIEW_CODE_VENDEGHAZ") or "").strip(),
        },
    ]

    with eng.begin() as conn:
        conn.execute(text(create_pages))
        for stmt in create_codes.split(";"):
            s = stmt.strip()
            if s:
                conn.execute(text(s))

        for item in seeds:
            page_key = item["page_key"]
            template_name = item["template_name"]
            raw_code = item.get("raw_code") or ""

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

            if raw_code:
                code_hash = _code_hash(raw_code)
                conn.execute(
                    text("""
                    INSERT INTO preview_codes (code_hash, page_key, is_active, expires_at)
                    VALUES (:h, :k, TRUE, NULL)
                    ON CONFLICT (code_hash) DO NOTHING
                    """),
                    {"h": code_hash, "k": page_key},
                )


try:
    _ensure_preview_tables_and_seed()
except Exception as e:
    print(f"[WARN] Preview DB init/seed failed: {e}")


# ==================================================
# SMTP TRANSACTIONAL EMAIL
# ==================================================
def _smtp_port() -> int:
    raw = (os.environ.get("SMTP_PORT") or "465").strip()
    try:
        return int(raw)
    except ValueError:
        raise RuntimeError(f"Invalid SMTP_PORT: {raw}")


def send_email(
    to_email: str,
    subject: str,
    html: str,
    text_msg: str | None = None,
    reply_to: str | None = None,
):
    host = (os.environ.get("SMTP_HOST") or "").strip()
    port = _smtp_port()
    user = (os.environ.get("SMTP_USER") or "").strip()
    password = (os.environ.get("SMTP_PASSWORD") or "").strip()
    from_email = (os.environ.get("SMTP_FROM") or os.environ.get("MAIL_FROM") or user).strip()
    from_name = (os.environ.get("SMTP_FROM_NAME") or os.environ.get("MAIL_FROM_NAME") or "CyberCare").strip()

    if not host:
        raise RuntimeError("Missing SMTP_HOST")
    if not user:
        raise RuntimeError("Missing SMTP_USER")
    if not password:
        raise RuntimeError("Missing SMTP_PASSWORD")
    if not from_email:
        raise RuntimeError("Missing SMTP_FROM")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{from_email}>"
    msg["To"] = to_email
    if reply_to:
        msg["Reply-To"] = reply_to

    msg.set_content(text_msg or "Az email HTML tartalommal érkezett.")
    msg.add_alternative(html, subtype="html")

    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=20) as smtp:
            smtp.login(user, password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            smtp.send_message(msg)


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


@app.get("/scrollable")
def scrollable():
    return render_template("scrollable.html")


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

    admin_email = (
        os.environ.get("MAIL_TO")
        or os.environ.get("SMTP_TO")
        or os.environ.get("SMTP_FROM")
        or os.environ.get("MAIL_FROM")
        or ""
    ).strip()
    if not admin_email:
        return _response_err("Admin email nincs beállítva (MAIL_TO vagy SMTP_FROM).", 500)

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

        lead_title = s_company or s_name
        admin_company_row = (
            f"""
                          <tr>
                            <td style="padding:11px 0;color:#6b7280;font-size:14px;border-bottom:1px solid #edf0ef;">Cég</td>
                            <td style="padding:11px 0;color:#2a2c28;font-size:14px;font-weight:800;text-align:right;border-bottom:1px solid #edf0ef;">{s_company}</td>
                          </tr>"""
            if s_company
            else ""
        )
        admin_phone_row = (
            f"""
                          <tr>
                            <td style="padding:11px 0;color:#6b7280;font-size:14px;border-bottom:1px solid #edf0ef;">Telefon</td>
                            <td style="padding:11px 0;color:#2a2c28;font-size:14px;font-weight:800;text-align:right;border-bottom:1px solid #edf0ef;">{s_phone}</td>
                          </tr>"""
            if s_phone
            else ""
        )

        admin_html = f"""<!DOCTYPE html>
<html lang="hu">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Új kapcsolatfelvétel – CyberCare</title>
</head>
<body style="margin:0;padding:0;background:#f4f7f5;font-family:Arial,Helvetica,sans-serif;color:#2a2c28;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f4f7f5;margin:0;padding:28px 12px;">
    <tr>
      <td align="center">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:680px;background:#ffffff;border-radius:24px;overflow:hidden;box-shadow:0 24px 70px rgba(42,44,40,0.12);">
          <tr>
            <td style="background:#2a2c28;padding:30px 34px;">
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0">
                <tr>
                  <td>
                    <div style="font-size:24px;line-height:1;font-weight:900;color:#ffffff;letter-spacing:-0.5px;">CyberCare</div>
                    <div style="font-size:11px;line-height:1.8;color:#a8b8ae;text-transform:uppercase;letter-spacing:2px;margin-top:6px;">New business inquiry</div>
                  </td>
                  <td align="right">
                    <div style="display:inline-block;background:#0b7b4a;color:#ffffff;border-radius:14px;padding:10px 14px;font-size:13px;font-weight:800;">Új megkeresés</div>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:34px;">
              <div style="display:inline-block;background:#e8f5ee;color:#0b7b4a;border-radius:999px;padding:8px 13px;font-size:12px;font-weight:800;text-transform:uppercase;letter-spacing:1.2px;margin-bottom:18px;">Kapcsolatfelvételi űrlap</div>
              <h1 style="margin:0 0 10px 0;color:#2a2c28;font-size:29px;line-height:1.18;font-weight:900;">Új érdeklődő érkezett</h1>
              <p style="margin:0 0 24px 0;color:#5d665f;font-size:16px;line-height:1.7;">
                A weboldalon keresztül új megkeresés érkezett. Az érdeklődő: <strong style="color:#2a2c28;">{lead_title}</strong>.
              </p>

              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f8fbf9;border:1px solid #edf0ef;border-radius:18px;padding:8px 18px;margin:24px 0;">
                <tr>
                  <td>
                    <table role="presentation" width="100%" cellspacing="0" cellpadding="0">
                      <tr>
                        <td style="padding:11px 0;color:#6b7280;font-size:14px;border-bottom:1px solid #edf0ef;">Név</td>
                        <td style="padding:11px 0;color:#2a2c28;font-size:14px;font-weight:800;text-align:right;border-bottom:1px solid #edf0ef;">{s_name}</td>
                      </tr>{admin_company_row}
                      <tr>
                        <td style="padding:11px 0;color:#6b7280;font-size:14px;border-bottom:1px solid #edf0ef;">Email</td>
                        <td style="padding:11px 0;color:#2a2c28;font-size:14px;font-weight:800;text-align:right;border-bottom:1px solid #edf0ef;"><a href="mailto:{s_email}" style="color:#0b7b4a;text-decoration:none;">{s_email}</a></td>
                      </tr>{admin_phone_row}
                      <tr>
                        <td style="padding:11px 0;color:#6b7280;font-size:14px;border-bottom:1px solid #edf0ef;">Érdeklődési terület</td>
                        <td style="padding:11px 0;color:#2a2c28;font-size:14px;font-weight:800;text-align:right;border-bottom:1px solid #edf0ef;">{s_service or '-'}</td>
                      </tr>
                      <tr>
                        <td style="padding:11px 0;color:#6b7280;font-size:14px;">Forrás</td>
                        <td style="padding:11px 0;color:#2a2c28;font-size:13px;font-weight:700;text-align:right;">{s_page or '-'}</td>
                      </tr>
                    </table>
                  </td>
                </tr>
              </table>

              <div style="border-left:4px solid #0b7b4a;background:#ffffff;padding:18px 0 18px 20px;margin:28px 0;border-radius:0 16px 16px 0;">
                <p style="margin:0 0 10px 0;color:#2a2c28;font-size:16px;line-height:1.5;font-weight:900;">Üzenet</p>
                <div style="color:#4b554f;font-size:15px;line-height:1.75;white-space:pre-wrap;">{s_msg}</div>
              </div>

              <table role="presentation" cellspacing="0" cellpadding="0" style="margin:28px 0 0 0;">
                <tr>
                  <td style="background:#0b7b4a;border-radius:16px;">
                    <a href="mailto:{s_email}?subject=Re:%20CyberCare%20megkeres%C3%A9s" style="display:inline-block;padding:14px 22px;color:#ffffff;text-decoration:none;font-size:15px;font-weight:900;">Válasz írása</a>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="background:#f8fbf9;padding:18px 34px;color:#7a837d;font-size:12px;line-height:1.6;text-align:center;">
              Ez az email a CyberCare kapcsolatfelvételi űrlapjáról érkezett. A válasz gomb az érdeklődő email címére mutat.
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

        send_email(
            to_email=admin_email,
            subject="Új kapcsolatfelvétel – CyberCare",
            text_msg=admin_text,
            html=admin_html,
            reply_to=email,
        )

        greeting = f"Tisztelt {s_company}!" if s_company else f"Kedves {s_name}!"
        service_label = s_service or "Általános megkeresés"
        company_row = (
            f"""
                            <tr>
                              <td style="padding:10px 0;color:#6b7280;font-size:14px;border-bottom:1px solid #edf0ef;">Cég</td>
                              <td style="padding:10px 0;color:#2a2c28;font-size:14px;font-weight:700;text-align:right;border-bottom:1px solid #edf0ef;">{s_company}</td>
                            </tr>"""
            if s_company
            else ""
        )

        user_text = (
            f"{greeting}\n\n"
            "Köszönjük a megkeresést, üzenetét megkaptuk. "
            "Hamarosan átnézzük a leírtakat, és jelentkezünk a megadott elérhetőségen.\n\n"
            f"Megkeresés témája: {payload.get('service') or 'Általános megkeresés'}\n\n"
            "Üdvözlettel:\nCyberCare"
        )

        user_html = f"""<!DOCTYPE html>
<html lang="hu">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Köszönjük megkeresését – CyberCare</title>
</head>
<body style="margin:0;padding:0;background:#f4f7f5;font-family:Arial,Helvetica,sans-serif;color:#2a2c28;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f4f7f5;margin:0;padding:28px 12px;">
    <tr>
      <td align="center">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:640px;background:#ffffff;border-radius:24px;overflow:hidden;box-shadow:0 24px 70px rgba(42,44,40,0.12);">
          <tr>
            <td style="background:#2a2c28;padding:30px 34px;">
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0">
                <tr>
                  <td>
                    <div style="font-size:24px;line-height:1;font-weight:900;color:#ffffff;letter-spacing:-0.5px;">CyberCare</div>
                    <div style="font-size:11px;line-height:1.8;color:#a8b8ae;text-transform:uppercase;letter-spacing:2px;margin-top:6px;">Business IT Systems</div>
                  </td>
                  <td align="right">
                    <div style="display:inline-block;background:#0b7b4a;color:#ffffff;border-radius:14px;padding:10px 14px;font-size:13px;font-weight:800;">Megkaptuk</div>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:34px;">
              <div style="display:inline-block;background:#e8f5ee;color:#0b7b4a;border-radius:999px;padding:8px 13px;font-size:12px;font-weight:800;text-transform:uppercase;letter-spacing:1.2px;margin-bottom:18px;">Automatikus visszaigazolás</div>
              <h1 style="margin:0 0 16px 0;color:#2a2c28;font-size:28px;line-height:1.18;font-weight:900;">Köszönjük a megkeresést!</h1>
              <p style="margin:0 0 14px 0;color:#2a2c28;font-size:17px;line-height:1.7;font-weight:700;">{greeting}</p>
              <p style="margin:0 0 22px 0;color:#5d665f;font-size:16px;line-height:1.7;">
                Köszönjük, hogy felvette velünk a kapcsolatot. Üzenetét megkaptuk, hamarosan átnézzük a leírtakat, és jelentkezünk a megadott elérhetőségen.
              </p>

              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f8fbf9;border:1px solid #edf0ef;border-radius:18px;padding:8px 18px;margin:24px 0;">
                <tr>
                  <td>
                    <table role="presentation" width="100%" cellspacing="0" cellpadding="0">
                      <tr>
                        <td style="padding:10px 0;color:#6b7280;font-size:14px;border-bottom:1px solid #edf0ef;">Név</td>
                        <td style="padding:10px 0;color:#2a2c28;font-size:14px;font-weight:700;text-align:right;border-bottom:1px solid #edf0ef;">{s_name}</td>
                      </tr>{company_row}
                      <tr>
                        <td style="padding:10px 0;color:#6b7280;font-size:14px;">Téma</td>
                        <td style="padding:10px 0;color:#2a2c28;font-size:14px;font-weight:700;text-align:right;">{service_label}</td>
                      </tr>
                    </table>
                  </td>
                </tr>
              </table>

              <div style="border-left:4px solid #0b7b4a;padding:14px 0 14px 18px;margin:24px 0;background:#ffffff;">
                <p style="margin:0;color:#2a2c28;font-size:16px;line-height:1.7;font-weight:700;">Mi történik most?</p>
                <p style="margin:6px 0 0 0;color:#5d665f;font-size:15px;line-height:1.7;">
                  Először áttekintjük a megkeresést, majd szükség esetén pontosító kérdésekkel vagy időpontjavaslattal jelentkezünk.
                </p>
              </div>

              <p style="margin:28px 0 0 0;color:#5d665f;font-size:15px;line-height:1.7;">
                Üdvözlettel,<br>
                <strong style="color:#2a2c28;">CyberCare</strong><br>
                <a href="mailto:info@cybercare.hu" style="color:#0b7b4a;text-decoration:none;font-weight:700;">info@cybercare.hu</a>
              </p>
            </td>
          </tr>
          <tr>
            <td style="background:#f8fbf9;padding:18px 34px;color:#7a837d;font-size:12px;line-height:1.6;text-align:center;">
              Ez egy automatikus visszaigazoló email. Kérjük, őrizze meg, amíg felvesszük Önnel a kapcsolatot.
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

        send_email(
            to_email=email,
            subject="Megkaptuk a megkeresést – CyberCare",
            text_msg=user_text,
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

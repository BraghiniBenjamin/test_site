import os
import secrets
from datetime import datetime, timezone, date

from flask import render_template, request, jsonify
from sqlalchemy import text


LOGO_URL = "https://raw.githubusercontent.com/BraghiniBenjamin/test_site/main/static/images/1cca2bf0-28d2-4cf0-ba02-075b4c79a989.png"


def register_meeting_routes(app, engine_factory, send_email_func, safe_func):
    """Register CyberCare token-based meeting scheduling routes.

    This module is intentionally separate from app.py so it can be enabled from wsgi.py
    without touching the existing landing/contact site routes.
    """
    if getattr(app, "_cybercare_meeting_routes_registered", False):
        return
    app._cybercare_meeting_routes_registered = True

    def _ensure_meeting_table():
        eng = engine_factory()
        with eng.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS meeting_tokens (
                    token             TEXT PRIMARY KEY,
                    company_name      TEXT NOT NULL,
                    recipient_email   TEXT NOT NULL,
                    source            TEXT NULL,
                    is_active         BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    expires_at        TIMESTAMPTZ NULL,
                    submitted_at      TIMESTAMPTZ NULL,
                    selected_date     TEXT NULL,
                    selected_time     TEXT NULL,
                    submitted_name    TEXT NULL,
                    submitted_phone   TEXT NULL,
                    note              TEXT NULL,
                    ip_address        TEXT NULL,
                    user_agent        TEXT NULL
                )
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_meeting_tokens_email ON meeting_tokens (recipient_email)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_meeting_tokens_submitted_at ON meeting_tokens (submitted_at)"))

    try:
        _ensure_meeting_table()
    except Exception as e:
        print(f"[WARN] Meeting DB init failed: {e}")

    def _admin_email_address():
        return (
            os.environ.get("MAIL_TO")
            or os.environ.get("SMTP_TO")
            or os.environ.get("SMTP_FROM")
            or os.environ.get("MAIL_FROM")
            or "info@cybercare.hu"
        ).strip()

    def _base_url():
        env_url = (os.environ.get("MEETING_BASE_URL") or os.environ.get("PUBLIC_BASE_URL") or "").strip()
        if env_url:
            return env_url.rstrip("/")
        return request.url_root.rstrip("/")

    def _load_token(token):
        eng = engine_factory()
        with eng.connect() as conn:
            return conn.execute(
                text("""
                    SELECT token, company_name, recipient_email, source, is_active,
                           created_at, expires_at, submitted_at, selected_date,
                           selected_time, submitted_name, submitted_phone, note
                    FROM meeting_tokens
                    WHERE token = :token
                    LIMIT 1
                """),
                {"token": token},
            ).mappings().first()

    def _is_expired(row):
        expires_at = row.get("expires_at") if row else None
        if not expires_at:
            return False
        expires_dt = expires_at
        if isinstance(expires_dt, str):
            expires_dt = datetime.fromisoformat(expires_dt.replace("Z", "+00:00"))
        if expires_dt.tzinfo is None:
            expires_dt = expires_dt.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) > expires_dt

    @app.post("/api/meeting-token")
    def api_create_meeting_token():
        """Create one token link for outreach emails.

        Required protection: set MEETING_TOKEN_ADMIN_KEY in Render and pass it either as
        JSON field `admin_key` or header `X-Meeting-Admin-Key`.
        """
        admin_key = (os.environ.get("MEETING_TOKEN_ADMIN_KEY") or "").strip()
        provided = (request.headers.get("X-Meeting-Admin-Key") or "").strip()
        data = request.get_json(silent=True) or {}
        if not provided:
            provided = (data.get("admin_key") or "").strip()

        if not admin_key or provided != admin_key:
            return jsonify({"ok": False, "error": "Jogosulatlan token létrehozás."}), 403

        company_name = (data.get("company_name") or data.get("company") or "").strip()
        recipient_email = (data.get("recipient_email") or data.get("email") or "").strip()
        source = (data.get("source") or "CyberCare outreach").strip()
        expires_at = (data.get("expires_at") or "").strip() or None

        if not company_name or not recipient_email:
            return jsonify({"ok": False, "error": "company_name és recipient_email kötelező."}), 400

        token = secrets.token_urlsafe(24)
        _ensure_meeting_table()
        eng = engine_factory()
        with eng.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO meeting_tokens (token, company_name, recipient_email, source, expires_at)
                    VALUES (:token, :company_name, :recipient_email, :source, :expires_at)
                """),
                {
                    "token": token,
                    "company_name": company_name,
                    "recipient_email": recipient_email,
                    "source": source,
                    "expires_at": expires_at,
                },
            )

        link = f"{_base_url()}/meeting/{token}"
        return jsonify({
            "ok": True,
            "token": token,
            "meeting_url": link,
            "company_name": company_name,
            "recipient_email": recipient_email,
        })

    @app.get("/meeting/<token>")
    def meeting_page(token):
        try:
            _ensure_meeting_table()
            row = _load_token(token)
        except Exception:
            row = None

        if not row or not row.get("is_active"):
            return render_template(
                "meeting.html",
                status="invalid",
                logo_url=LOGO_URL,
                company_name="",
                recipient_email="",
                min_date=date.today().isoformat(),
            ), 404

        if _is_expired(row):
            return render_template(
                "meeting.html",
                status="expired",
                logo_url=LOGO_URL,
                company_name=row["company_name"],
                recipient_email=row["recipient_email"],
                min_date=date.today().isoformat(),
            ), 410

        if row.get("submitted_at"):
            return render_template(
                "meeting.html",
                status="submitted",
                logo_url=LOGO_URL,
                company_name=row["company_name"],
                recipient_email=row["recipient_email"],
                selected_date=row.get("selected_date"),
                selected_time=row.get("selected_time"),
                min_date=date.today().isoformat(),
            )

        return render_template(
            "meeting.html",
            status="form",
            logo_url=LOGO_URL,
            company_name=row["company_name"],
            recipient_email=row["recipient_email"],
            min_date=date.today().isoformat(),
        )

    @app.post("/meeting/<token>")
    def submit_meeting(token):
        _ensure_meeting_table()
        row = _load_token(token)
        if not row or not row.get("is_active"):
            return render_template(
                "meeting.html",
                status="invalid",
                logo_url=LOGO_URL,
                company_name="",
                recipient_email="",
                min_date=date.today().isoformat(),
            ), 404

        if _is_expired(row):
            return render_template(
                "meeting.html",
                status="expired",
                logo_url=LOGO_URL,
                company_name=row["company_name"],
                recipient_email=row["recipient_email"],
                min_date=date.today().isoformat(),
            ), 410

        if row.get("submitted_at"):
            return render_template(
                "meeting.html",
                status="submitted",
                logo_url=LOGO_URL,
                company_name=row["company_name"],
                recipient_email=row["recipient_email"],
                selected_date=row.get("selected_date"),
                selected_time=row.get("selected_time"),
                min_date=date.today().isoformat(),
            )

        selected_date = (request.form.get("selected_date") or "").strip()
        selected_time = (request.form.get("selected_time") or "").strip()
        submitted_name = (request.form.get("name") or "").strip()
        submitted_phone = (request.form.get("phone") or "").strip()
        note = (request.form.get("note") or "").strip()

        if not selected_date or not selected_time:
            return render_template(
                "meeting.html",
                status="form",
                logo_url=LOGO_URL,
                company_name=row["company_name"],
                recipient_email=row["recipient_email"],
                min_date=date.today().isoformat(),
                error="Kérjük, válasszon napot és időpontot.",
                values={
                    "selected_date": selected_date,
                    "selected_time": selected_time,
                    "name": submitted_name,
                    "phone": submitted_phone,
                    "note": note,
                },
            ), 400

        ip_address = request.headers.get("X-Forwarded-For", request.remote_addr) or "unknown"
        user_agent = request.headers.get("User-Agent", "")[:500]

        eng = engine_factory()
        with eng.begin() as conn:
            conn.execute(
                text("""
                    UPDATE meeting_tokens
                    SET submitted_at = NOW(),
                        selected_date = :selected_date,
                        selected_time = :selected_time,
                        submitted_name = :submitted_name,
                        submitted_phone = :submitted_phone,
                        note = :note,
                        ip_address = :ip_address,
                        user_agent = :user_agent
                    WHERE token = :token
                      AND submitted_at IS NULL
                """),
                {
                    "token": token,
                    "selected_date": selected_date,
                    "selected_time": selected_time,
                    "submitted_name": submitted_name,
                    "submitted_phone": submitted_phone,
                    "note": note,
                    "ip_address": ip_address,
                    "user_agent": user_agent,
                },
            )

        s_company = safe_func(row["company_name"])
        s_email = safe_func(row["recipient_email"])
        s_name = safe_func(submitted_name or "-")
        s_phone = safe_func(submitted_phone or "-")
        s_date = safe_func(selected_date)
        s_time = safe_func(selected_time)
        s_note = safe_func(note or "-")
        admin_email = _admin_email_address()

        admin_text = (
            "Új CyberCare időpontjavaslat érkezett\n\n"
            f"Cég: {row['company_name']}\n"
            f"Email: {row['recipient_email']}\n"
            f"Kapcsolattartó: {submitted_name or '-'}\n"
            f"Telefon: {submitted_phone or '-'}\n"
            f"Javasolt időpont: {selected_date} {selected_time}\n\n"
            f"Megjegyzés:\n{note or '-'}\n"
        )

        admin_html = f"""<!DOCTYPE html>
<html lang="hu">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Új időpontjavaslat – CyberCare</title></head>
<body style="margin:0;padding:0;background:#f4f7f5;font-family:Arial,Helvetica,sans-serif;color:#2a2c28;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f4f7f5;margin:0;padding:28px 12px;">
    <tr><td align="center">
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:680px;background:#ffffff;border-radius:24px;overflow:hidden;box-shadow:0 24px 70px rgba(42,44,40,0.12);">
        <tr><td style="background:#2a2c28;padding:30px 34px;">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr>
            <td><div style="font-size:24px;line-height:1;font-weight:900;color:#ffffff;letter-spacing:-0.5px;">CyberCare</div><div style="font-size:11px;line-height:1.8;color:#a8b8ae;text-transform:uppercase;letter-spacing:2px;margin-top:6px;">Meeting request</div></td>
            <td align="right"><div style="display:inline-block;background:#0b7b4a;color:#ffffff;border-radius:14px;padding:10px 14px;font-size:13px;font-weight:800;">Időpontjavaslat</div></td>
          </tr></table>
        </td></tr>
        <tr><td style="padding:34px;">
          <div style="display:inline-block;background:#e8f5ee;color:#0b7b4a;border-radius:999px;padding:8px 13px;font-size:12px;font-weight:800;text-transform:uppercase;letter-spacing:1.2px;margin-bottom:18px;">Online meeting</div>
          <h1 style="margin:0 0 10px 0;color:#2a2c28;font-size:29px;line-height:1.18;font-weight:900;">Új időpontjavaslat érkezett</h1>
          <p style="margin:0 0 24px 0;color:#5d665f;font-size:16px;line-height:1.7;">A(z) <strong style="color:#2a2c28;">{s_company}</strong> időpontot javasolt egy rövid online egyeztetésre.</p>
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f8fbf9;border:1px solid #edf0ef;border-radius:18px;padding:8px 18px;margin:24px 0;">
            <tr><td><table role="presentation" width="100%" cellspacing="0" cellpadding="0">
              <tr><td style="padding:11px 0;color:#6b7280;font-size:14px;border-bottom:1px solid #edf0ef;">Cég</td><td style="padding:11px 0;color:#2a2c28;font-size:14px;font-weight:800;text-align:right;border-bottom:1px solid #edf0ef;">{s_company}</td></tr>
              <tr><td style="padding:11px 0;color:#6b7280;font-size:14px;border-bottom:1px solid #edf0ef;">Email</td><td style="padding:11px 0;color:#2a2c28;font-size:14px;font-weight:800;text-align:right;border-bottom:1px solid #edf0ef;"><a href="mailto:{s_email}" style="color:#0b7b4a;text-decoration:none;">{s_email}</a></td></tr>
              <tr><td style="padding:11px 0;color:#6b7280;font-size:14px;border-bottom:1px solid #edf0ef;">Kapcsolattartó</td><td style="padding:11px 0;color:#2a2c28;font-size:14px;font-weight:800;text-align:right;border-bottom:1px solid #edf0ef;">{s_name}</td></tr>
              <tr><td style="padding:11px 0;color:#6b7280;font-size:14px;border-bottom:1px solid #edf0ef;">Telefon</td><td style="padding:11px 0;color:#2a2c28;font-size:14px;font-weight:800;text-align:right;border-bottom:1px solid #edf0ef;">{s_phone}</td></tr>
              <tr><td style="padding:11px 0;color:#6b7280;font-size:14px;">Javasolt időpont</td><td style="padding:11px 0;color:#0b7b4a;font-size:17px;font-weight:900;text-align:right;">{s_date} {s_time}</td></tr>
            </table></td></tr>
          </table>
          <div style="border-left:4px solid #0b7b4a;background:#ffffff;padding:18px 0 18px 20px;margin:28px 0;border-radius:0 16px 16px 0;">
            <p style="margin:0 0 10px 0;color:#2a2c28;font-size:16px;line-height:1.5;font-weight:900;">Megjegyzés</p>
            <div style="color:#4b554f;font-size:15px;line-height:1.75;white-space:pre-wrap;">{s_note}</div>
          </div>
          <table role="presentation" cellspacing="0" cellpadding="0" style="margin:28px 0 0 0;"><tr><td style="background:#0b7b4a;border-radius:16px;"><a href="mailto:{s_email}?subject=Re:%20R%C3%B6vid%20online%20meeting%20egyeztet%C3%A9se" style="display:inline-block;padding:14px 22px;color:#ffffff;text-decoration:none;font-size:15px;font-weight:900;">Válasz írása</a></td></tr></table>
        </td></tr>
        <tr><td style="background:#f8fbf9;padding:18px 34px;color:#7a837d;font-size:12px;line-height:1.6;text-align:center;">Ez az email a CyberCare meeting egyeztető oldaláról érkezett.</td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""

        try:
            send_email_func(
                to_email=admin_email,
                subject=f"Új időpontjavaslat – {row['company_name']}",
                text_msg=admin_text,
                html=admin_html,
                reply_to=row["recipient_email"],
            )
        except Exception as e:
            print(f"[WARN] Meeting notification email failed: {e}")

        return render_template(
            "meeting.html",
            status="submitted",
            logo_url=LOGO_URL,
            company_name=row["company_name"],
            recipient_email=row["recipient_email"],
            selected_date=selected_date,
            selected_time=selected_time,
            min_date=date.today().isoformat(),
        )

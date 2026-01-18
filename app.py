import os
import html as html_escape
import requests
from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask import send_from_directory, abort
import pathlib

app = Flask(__name__)

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
    # a frontended data.success-t figyel
    return jsonify({"ok": True, "success": True, "message": message})


def _response_err(message: str, status: int = 400):
    return jsonify({"ok": False, "success": False, "message": message, "error": message}), status


# ==================================================
# ROUTES (PAGES)
# ==================================================

# A template-ben használt url_for('root') miatt:
@app.get("/")
def root():
    return render_template("index.html")


# A template-ben használt url_for('home') miatt:
@app.get("/home")
def home():
    return render_template("index.html")


@app.get("/rolunk")
def about():
    return render_template("about_us.html")


@app.get("/szolgaltatasaink")
def services():
    return render_template("our_services.html")

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
# EXTRA ALIASOK (ha többféle URL-ed is kint van már)
# ==================================================

# footerben /szolgaltatasaink is előfordulhat
@app.get("/szolgaltatasaink")
def services_hu_alias():
    return redirect(url_for("services"), code=301)


# /web-fejlesztes (slugos) -> webfejlesztes oldal
@app.get("/web-fejlesztes")
def web_fejlesztes():
    return redirect(url_for("web_development"), code=301)


# ==================================================
# TEMPLATE-ALIAS ENDPOINTOK (a HTML-ben használt url_for(...) miatt)
# ==================================================

# index.html-ben: url_for('about_alias') stb.
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
# API
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

        # 1) ADMIN TEXT (fallback / plain text)
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

        # 1) ADMIN HTML (a TE sablonod)
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

        # 2) USER HTML (a TE sablonod)
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


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


# ==================================================
# DEMO OLDALAK (demo_oldalak mappa kiszolgálása)
#   mappa struktúra példa:
#   demo_oldalak/
#     demo1/index.html
#     demo1/assets/...
#     demo2/index.html
# ==================================================

DEMO_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_oldalak")

@app.get("/demo/<filename>")
def demo_files(filename):
    full_path = os.path.join(DEMO_ROOT, filename)
    if not os.path.isfile(full_path):
        abort(404)
    return send_from_directory(DEMO_ROOT, filename)


# ==================================================
# RUN
# ==================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

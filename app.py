import os
import requests
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

# =========================
# SENDGRID EMAIL KÜLDŐ
# =========================
def send_email(to_email: str, subject: str, html: str, text: str | None = None):
    """
    Send an email via SendGrid HTTP API (works on Render because it's HTTPS/443).
    Required env vars:
      - SENDGRID_API_KEY
      - MAIL_FROM  (must be verified in SendGrid: Single Sender or Domain Auth)
    """
    api_key = os.environ.get("SENDGRID_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Missing SENDGRID_API_KEY env var")

    from_email = os.environ.get("MAIL_FROM", "").strip()
    if not from_email:
        raise RuntimeError("Missing MAIL_FROM env var")

    payload = {
        "personalizations": [{"to": [{"email": to_email}]}],
        "from": {"email": from_email},
        "subject": subject,
        "content": [
            {"type": "text/plain", "value": text or " "},
            {"type": "text/html", "value": html},
        ],
    }

    resp = requests.post(
        "https://api.sendgrid.com/v3/mail/send",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=20,
    )

    # SendGrid success: 202 Accepted
    if resp.status_code != 202:
        raise RuntimeError(f"SendGrid error {resp.status_code}: {resp.text}")


# =========================
# FŐOLDAL
# =========================
@app.get("/")
def index():
    return render_template("index.html")


# =========================
# KAPCSOLAT ŰRLAP API
# =========================
@app.post("/api/contact")
def contact():
    """
    Expects JSON:
      { "name": "...", "email": "...", "message": "..." }

    Sends:
      1) Admin notification to MAIL_TO (fallback: MAIL_FROM)
      2) Auto-reply to the user (email field)
    """
    data = request.get_json(force=True)

    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip()
    message = (data.get("message") or "").strip()

    if not name or not email or not message:
        return jsonify({"ok": False, "error": "Hiányzó mező"}), 400

    admin_email = (os.environ.get("MAIL_TO") or os.environ.get("MAIL_FROM") or "").strip()
    if not admin_email:
        return jsonify({"ok": False, "error": "MAIL_TO / MAIL_FROM nincs beállítva"}), 500

    try:
        # 1) ADMIN ÉRTESÍTÉS
        send_email(
            to_email=admin_email,
            subject="Új kapcsolatfelvétel – CyberCare",
            text=f"Név: {name}\nEmail: {email}\n\n{message}",
            html=f"""
            <div style="font-family:Arial,sans-serif; line-height:1.45">
              <h2>Új üzenet érkezett</h2>
              <p><strong>Név:</strong> {name}</p>
              <p><strong>Email:</strong> {email}</p>
              <p><strong>Üzenet:</strong></p>
              <div style="padding:12px; background:#f6f6f6; border-radius:8px; white-space:pre-wrap">{message}</div>
            </div>
            """,
        )

        # 2) VISSZAIGAZOLÁS A FELHASZNÁLÓNAK
        send_email(
            to_email=email,
            subject="Köszönjük a megkeresést – CyberCare",
            text="Köszönjük a megkeresést! Hamarosan válaszolunk.",
            html=f"""
            <div style="font-family:Arial,sans-serif; line-height:1.45">
              <p>Kedves {name}!</p>
              <p>Köszönjük, hogy felvetted velünk a kapcsolatot. Hamarosan válaszolunk.</p>
              <p>Üdvözlettel,<br><strong>CyberCare</strong></p>
            </div>
            """,
        )

    except Exception as e:
        # Ne omoljon 500-zal "néma" hibával; adjunk értelmes választ a frontendnek
        return jsonify({"ok": False, "error": f"Email küldési hiba: {e}"}), 503

    return jsonify({"ok": True})


# =========================
# OPTIONAL: Health check
# =========================
@app.get("/health")
def health():
    return jsonify({"ok": True})


# =========================
# LOCAL RUN (Renderen gunicorn indítja)
# =========================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

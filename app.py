import os
import requests
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

# ==================================================
# SENDGRID EMAIL KÜLDÉS (HTTPS, Render-kompatibilis)
# ==================================================
def send_email(to_email: str, subject: str, html: str, text: str | None = None):
    api_key = os.environ.get("SENDGRID_API_KEY")
    from_email = os.environ.get("MAIL_FROM")

    if not api_key:
        raise RuntimeError("SENDGRID_API_KEY nincs beállítva")
    if not from_email:
        raise RuntimeError("MAIL_FROM nincs beállítva")

    payload = {
        "personalizations": [
            {"to": [{"email": to_email}]}
        ],
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

    # SendGrid siker = 202 Accepted
    if resp.status_code != 202:
        raise RuntimeError(
            f"SendGrid hiba ({resp.status_code}): {resp.text}"
        )


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
    Frontend JSON:
    {
      "name": "...",
      "email": "...",
      "message": "..."
    }
    """
    data = request.get_json(force=True)

    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip()
    message = (data.get("message") or "").strip()

    if not name or not email or not message:
        return jsonify({
            "ok": False,
            "error": "Minden mező kitöltése kötelező"
        }), 400

    admin_email = os.environ.get("MAIL_TO") or os.environ.get("MAIL_FROM")
    if not admin_email:
        return jsonify({
            "ok": False,
            "error": "Admin email nincs beállítva"
        }), 500

    try:
        # 1️⃣ ADMIN ÉRTESÍTÉS
        send_email(
            to_email=admin_email,
            subject="Új kapcsolatfelvétel – CyberCare",
            text=f"Név: {name}\nEmail: {email}\n\n{message}",
            html=f"""
            <div style="font-family:Arial,sans-serif">
              <h2>Új kapcsolatfelvétel</h2>
              <p><strong>Név:</strong> {name}</p>
              <p><strong>Email:</strong> {email}</p>
              <p><strong>Üzenet:</strong></p>
              <div style="padding:12px;background:#f4f4f4;border-radius:8px">
                {message}
              </div>
            </div>
            """
        )

        # 2️⃣ AUTOMATIKUS VISSZAIGAZOLÁS
        send_email(
            to_email=email,
            subject="Köszönjük megkeresését – CyberCare",
            text="Köszönjük, hogy felvette velünk a kapcsolatot. Hamarosan válaszolunk.",
            html=f"""
            <div style="font-family:Arial,sans-serif">
              <p>Kedves {name}!</p>
              <p>Köszönjük, hogy felvette velünk a kapcsolatot.</p>
              <p>Hamarosan válaszolunk.</p>
              <br>
              <p>Üdvözlettel,<br><strong>CyberCare</strong></p>
            </div>
            """
        )

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": f"Email küldési hiba: {e}"
        }), 503

    return jsonify({"ok": True})


# =========================
# HEALTH CHECK (opcionális)
# =========================
@app.get("/health")
def health():
    return jsonify({"status": "ok"})


# =========================
# LOCAL / RENDER FUTTATÁS
# =========================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

import os
import requests
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

# ==================================================
# BREVO (Sendinblue) TRANSACTIONAL EMAIL KÜLDÉS
# ==================================================
def send_email(to_email: str, subject: str, html: str, text: str | None = None):
    """
    Küldés Brevo Transactional Email API-val (HTTPS/443, Renderen megy).
    Kötelező env változók:
      - BREVO_API_KEY
      - MAIL_FROM  (Brevo-ban verified sender email)
    Opcionális:
      - MAIL_FROM_NAME (pl. CyberCare)
    """
    api_key = (os.environ.get("BREVO_API_KEY") or "").strip()
    from_email = (os.environ.get("MAIL_FROM") or "").strip()
    from_name = (os.environ.get("MAIL_FROM_NAME") or "CyberCare").strip()

    if not api_key:
        raise RuntimeError("Missing BREVO_API_KEY env var")
    if not from_email:
        raise RuntimeError("Missing MAIL_FROM env var")

    payload = {
        "sender": {"name": from_name, "email": from_email},
        "to": [{"email": to_email}],
        "subject": subject,
        "htmlContent": html,
    }

    # plain text fallback (nem kötelező, de jó)
    if text:
        payload["textContent"] = text

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

    # Brevo siker: 201 Created (általában), de 202 is előfordulhat
    if resp.status_code not in (200, 201, 202):
        raise RuntimeError(f"Brevo hiba ({resp.status_code}): {resp.text}")


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

    Küld:
    1) Admin értesítés MAIL_TO-ra (fallback: MAIL_FROM)
    2) Visszaigazolás a felhasználónak
    """
    data = request.get_json(force=True)

    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip()
    message = (data.get("message") or "").strip()

    if not name or not email or not message:
        return jsonify({"ok": False, "error": "Minden mező kitöltése kötelező"}), 400

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
              <h2>Új kapcsolatfelvétel</h2>
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
            text="Köszönjük, hogy felvetted velünk a kapcsolatot. Hamarosan válaszolunk.",
            html=f"""
            <div style="font-family:Arial,sans-serif; line-height:1.45">
              <p>Kedves {name}!</p>
              <p>Köszönjük, hogy felvetted velünk a kapcsolatot.</p>
              <p>Hamarosan válaszolunk.</p>
              <br>
              <p>Üdvözlettel,<br><strong>CyberCare</strong></p>
            </div>
            """,
        )

    except Exception as e:
        return jsonify({"ok": False, "error": f"Email küldési hiba: {e}"}), 503

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

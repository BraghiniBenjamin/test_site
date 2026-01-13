import os
import smtplib
from email.message import EmailMessage
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

# =========================
# EMAIL KÜLDŐ FÜGGVÉNY
# =========================
def send_email(to_email: str, subject: str, html: str, text: str | None = None):
    host = os.environ["MAIL_HOST"]
    port = int(os.environ.get("MAIL_PORT", "587"))
    username = os.environ["MAIL_USERNAME"]
    password = os.environ["MAIL_PASSWORD"]
    from_email = os.environ.get("MAIL_FROM", username)
    use_tls = os.environ.get("MAIL_USE_TLS", "true").lower() == "true"

    msg = EmailMessage()
    msg["From"] = from_email
    msg["To"] = to_email
    msg["Subject"] = subject

    msg.set_content(text or "HTML email")
    msg.add_alternative(html, subtype="html")

    with smtplib.SMTP(host, port, timeout=20) as smtp:
        if use_tls:
            smtp.starttls()
        smtp.login(username, password)
        smtp.send_message(msg)


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
    data = request.get_json(force=True)

    name = data.get("name", "").strip()
    email = data.get("email", "").strip()
    message = data.get("message", "").strip()

    if not name or not email or not message:
        return jsonify({"ok": False, "error": "Hiányzó mező"}), 400

    # ADMIN EMAIL
    send_email(
        to_email=os.environ.get("MAIL_FROM"),
        subject="Új kapcsolatfelvétel – CyberCare",
        html=f"""
        <h2>Új üzenet érkezett</h2>
        <p><strong>Név:</strong> {name}</p>
        <p><strong>Email:</strong> {email}</p>
        <p><strong>Üzenet:</strong></p>
        <p>{message}</p>
        """,
        text=f"Név: {name}\nEmail: {email}\n\n{message}"
    )

    # VISSZAIGAZOLÁS A FELHASZNÁLÓNAK
    send_email(
        to_email=email,
        subject="Köszönjük a megkeresést – CyberCare",
        html=f"""
        <p>Kedves {name}!</p>
        <p>Köszönjük, hogy felvetted velünk a kapcsolatot.</p>
        <p>Hamarosan válaszolunk.</p>
        <br>
        <p>Üdvözlettel,<br><strong>CyberCare</strong></p>
        """,
        text="Köszönjük a megkeresést! Hamarosan válaszolunk."
    )

    return jsonify({"ok": True})


# =========================
# RENDER / LOCAL FUTTATÁS
# =========================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

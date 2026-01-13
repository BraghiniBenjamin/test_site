import os
import smtplib
from email.message import EmailMessage
from flask import Flask, request, jsonify

app = Flask(__name__)

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

    # Plain-text fallback (jó deliverability miatt)
    msg.set_content(text or "This email requires an HTML-capable client.")

    # HTML tartalom
    msg.add_alternative(html, subtype="html")

    with smtplib.SMTP(host, port, timeout=20) as smtp:
        if use_tls:
            smtp.starttls()
        smtp.login(username, password)
        smtp.send_message(msg)

@app.post("/send-test-email")
def send_test_email():
    data = request.get_json(force=True)
    to_email = data.get("to")
    if not to_email:
        return jsonify({"ok": False, "error": "Missing 'to'"}), 400

    send_email(
        to_email=to_email,
        subject="CyberCare – teszt email",
        text="Ez egy teszt email.",
        html="""
        <h2>CyberCare – teszt</h2>
        <p>Ha ezt látod, működik az email küldés Render + Flask alatt.</p>
        """,
    )
    return jsonify({"ok": True})

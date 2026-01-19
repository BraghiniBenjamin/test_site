import os
import html as html_escape
import pathlib
import requests

from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask import send_from_directory, abort

from flask_sqlalchemy import SQLAlchemy
from openai import OpenAI


app = Flask(__name__)

# ==================================================
# DB (SQLAlchemy)
# ==================================================
DATABASE_URL = (os.environ.get("DATABASE_URL") or "").strip()

# Render Postgres néha "postgres://" formát ad, SQLAlchemy 2 inkább "postgresql://"-t szeret
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL if DATABASE_URL else "sqlite:///local_dev.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


class KBEntry(db.Model):
    __tablename__ = "kb_entries"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    tags = db.Column(db.String(300), nullable=True)


def seed_kb_if_empty():
    """Feltölt pár dummy rekordot, ha üres a tudásbázis."""
    if KBEntry.query.first():
        return

    items = [
        KBEntry(
            title="Webfejlesztés – Mit vállal a CyberCare?",
            content=(
                "Modern, reszponzív weboldalak készítése és karbantartása. "
                "Kapcsolatfelvételi űrlap, alap SEO beállítások, gyors betöltés, "
                "mobilbarát megjelenés. Egyedi igény alapján árazás."
            ),
            tags="webfejlesztés, landing, seo, karbantartás",
        ),
        KBEntry(
            title="Automatizálás és AI fókusz",
            content=(
                "AI fókuszú problémamegoldás és saját rendszerek fejlesztése, "
                "valamint rugalmas integráció külső rendszerekbe. "
                "Cél: kézzelfogható, működő megoldások üzemi/valós környezetben."
            ),
            tags="ai, automatizálás, integráció, fejlesztés",
        ),
        KBEntry(
            title="Vállalati IT támogatás / gépkarbantartás",
            content=(
                "Kis- és nagyvállalatoknak IT jellegű karbantartás, monitoring, "
                "hibamegelőzés, üzemeltetési támogatás. Távoli és helyszíni segítség."
            ),
            tags="vállalat, it, karbantartás, monitoring",
        ),
        KBEntry(
            title="Válaszidő kapcsolatfelvétel után",
            content=(
                "Kapcsolatfelvétel után jellemzően 24–48 órán belül válaszolunk munkanapokon."
            ),
            tags="kapcsolat, válaszidő, support",
        ),
        KBEntry(
            title="Kapcsolat",
            content=(
                "Írj a kapcsolat űrlapon, és add meg: név, email, üzenet. "
                "Ha van cégnév/telefon/szolgáltatás, az gyorsítja az egyeztetést."
            ),
            tags="kapcsolat, űrlap, email",
        ),
    ]

    db.session.add_all(items)
    db.session.commit()


def init_db():
    """DB táblák létrehozása + seed."""
    db.create_all()
    seed_kb_if_empty()


with app.app_context():
    init_db()


# ==================================================
# OPENAI
# ==================================================
openai_client = OpenAI()  # OPENAI_API_KEY env varból olvas


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
# EXTRA ALIASOK
# ==================================================
@app.get("/web-fejlesztes")
def web_fejlesztes():
    return redirect(url_for("web_development"), code=301)


# ==================================================
# TEMPLATE-ALIAS ENDPOINTOK
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
# LEGACY / COMPAT (.html linkek -> új útvonal)
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
  <h2>🔔 Új kapcsolatfelvétel</h2>
  <p><b>Név:</b> {s_name}<br>
     <b>Email:</b> {s_email}<br>
     <b>Cég:</b> {s_company or "-"}<br>
     <b>Telefon:</b> {s_phone or "-"}<br>
     <b>Érdeklődési terület:</b> {s_service or "-"}<br>
     <b>Forrás:</b> {s_page or "-"}<br>
  </p>
  <h3>Üzenet</h3>
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
  <h2>✅ Köszönjük megkeresését!</h2>
  <p>Kedves {s_name}!</p>
  <p>Köszönjük, hogy felvette velünk a kapcsolatot. Üzenetét megkaptuk, hamarosan válaszolunk.</p>
  <p>Üdvözlettel,<br><b>CyberCare</b></p>
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
# API - CHAT (AI + DB)
# ==================================================
def search_kb(query: str, limit: int = 5):
    q = (query or "").strip()
    if not q:
        return []

    like = f"%{q}%"
    rows = (
        KBEntry.query
        .filter(
            db.or_(
                KBEntry.title.ilike(like),
                KBEntry.content.ilike(like),
                KBEntry.tags.ilike(like),
            )
        )
        .limit(limit)
        .all()
    )
    return rows


@app.post("/api/chat")
def api_chat():
    data = request.get_json(silent=True) or {}
    user_msg = (data.get("message") or "").strip()
    if not user_msg:
        return _response_err("Üzenet kötelező.", 400)

    hits = search_kb(user_msg, limit=5)

    # Kontextus: csak releváns találatok (ha nincs, azt is jelezzük)
    context_blocks = []
    for h in hits:
        # vágjuk le, hogy ne legyen túl hosszú
        content = (h.content or "")
        if len(content) > 1500:
            content = content[:1500] + "…"

        context_blocks.append(
            f"### {h.title}\n"
            f"Tags: {h.tags or '-'}\n"
            f"Content: {content}\n"
        )

    kb_context = "\n\n".join(context_blocks) if context_blocks else "NINCS TALÁLAT A TUDÁSBÁZISBAN."

    system_instructions = (
        "Te a CyberCare weboldal chatbotja vagy.\n"
        "Csak a megadott TUDÁSBÁZIS (KB) alapján válaszolj.\n"
        "Ha a KB nem tartalmaz választ, mondd el röviden, hogy nincs róla információd, "
        "és kérj pontosítást.\n"
        "Ne találj ki árakat, számokat, ígéreteket, ha nincs a KB-ban.\n"
        "Válaszolj magyarul, tömören és segítőkészen."
    )

    try:
        resp = openai_client.responses.create(
            model="gpt-4.1-mini",
            input=[
                {"role": "system", "content": system_instructions},
                {"role": "user", "content": f"KÉRDÉS:\n{user_msg}\n\nTUDÁSBÁZIS (KB):\n{kb_context}"},
            ],
        )
        answer = (resp.output_text or "").strip() or "Most nem tudok válaszolni, kérlek próbáld újra."
    except Exception as e:
        return _response_err(f"AI hiba: {e}", 503)

    return jsonify({
        "ok": True,
        "success": True,
        "answer": answer,
        "sources": [{"id": h.id, "title": h.title} for h in hits],
    })


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
    if "/" in safe or "\\" in safe or safe.startswith(".") or ".." in safe:
        abort(404)

    full_html = os.path.join(TEMPLATES_ROOT, f"{safe}.html")
    if not os.path.isfile(full_html):
        abort(404)

    return render_template(f"{safe}.html")


@app.get("/demo_assets/<path:filename>")
def demo_assets(filename):
    safe = str(pathlib.PurePosixPath(filename))
    # tiltjuk a ..-t is
    if ".." in safe or safe.startswith(".") or safe.startswith("/"):
        abort(404)

    full_path = os.path.join(TEMPLATES_ROOT, safe)
    if not os.path.isfile(full_path):
        abort(404)

    return send_from_directory(TEMPLATES_ROOT, safe)

@app.get("/ai-chatbot")
def ai_chatbot_page():
    return render_template("ai_chatbot.html")
    
@app.get("/api/kb/all")
def api_kb_all():
    rows = KBEntry.query.order_by(KBEntry.id.asc()).all()
    return jsonify({
        "ok": True,
        "items": [
            {
                "id": r.id,
                "title": r.title,
                "tags": r.tags or "",
                "content": r.content or ""
            }
            for r in rows
        ]
    })



# ==================================================
# RUN
# ==================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

from app import app, _engine, send_email, _safe
from meeting_routes import register_meeting_routes


register_meeting_routes(app, _engine, send_email, _safe)

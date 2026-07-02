"""Gunicorn compatibility hooks for deployments that still start `app:app`.

Render Start Command may override the Procfile. If the service is still started with
`gunicorn app:app`, the WSGI entrypoint is bypassed. This hook registers the
CyberCare meeting routes before the worker starts serving requests.
"""


def post_worker_init(worker):
    try:
        from app import app, _engine, send_email, _safe
        from meeting_routes import register_meeting_routes

        register_meeting_routes(app, _engine, send_email, _safe)
        worker.log.info("CyberCare meeting routes registered.")
    except Exception as exc:
        worker.log.warning("CyberCare meeting route registration failed: %s", exc)

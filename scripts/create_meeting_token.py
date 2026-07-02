import argparse
import os
import secrets

from sqlalchemy import create_engine, text


def db_url() -> str:
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://") :]
    if not url:
        raise RuntimeError("Missing DATABASE_URL environment variable")
    return url


def ensure_table(conn):
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


def main():
    parser = argparse.ArgumentParser(description="Create a CyberCare meeting token link.")
    parser.add_argument("--company", required=True, help="Company name")
    parser.add_argument("--email", required=True, help="Recipient email")
    parser.add_argument("--base-url", default=os.environ.get("MEETING_BASE_URL", "https://www.cybercare.hu"), help="Public base URL")
    parser.add_argument("--source", default="CyberCare outreach", help="Source label")
    parser.add_argument("--expires-at", default=None, help="Optional ISO datetime, e.g. 2026-08-01T00:00:00+02:00")
    args = parser.parse_args()

    token = secrets.token_urlsafe(24)
    engine = create_engine(db_url(), pool_pre_ping=True)
    with engine.begin() as conn:
        ensure_table(conn)
        conn.execute(
            text("""
                INSERT INTO meeting_tokens (token, company_name, recipient_email, source, expires_at)
                VALUES (:token, :company_name, :recipient_email, :source, :expires_at)
            """),
            {
                "token": token,
                "company_name": args.company,
                "recipient_email": args.email,
                "source": args.source,
                "expires_at": args.expires_at,
            },
        )

    print(f"Company: {args.company}")
    print(f"Email:   {args.email}")
    print(f"Token:   {token}")
    print(f"URL:     {args.base_url.rstrip('/')}/meeting/{token}")


if __name__ == "__main__":
    main()

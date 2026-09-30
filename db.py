import json
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

from contextlib import contextmanager

DB_FILE = "app.db"


@contextmanager
def get_connection(db_file: str = DB_FILE):
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    try:
        yield conn
    finally:
        conn.close()


def init_db(db_file: str = DB_FILE) -> None:
    """Initialize SQLite database and create necessary tables and indexes."""
    with get_connection(db_file) as conn:
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS oauth_tokens (
            channel_id TEXT PRIMARY KEY,
            account_email TEXT NOT NULL,
            channel_title TEXT NOT NULL,
            refresh_token TEXT NOT NULL,
            access_token TEXT,
            token_expiry TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions_master (
            channel_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT,
            custom_url TEXT,
            thumbnail_url TEXT,
            categories TEXT,
            uploads_playlist_id TEXT,
            last_upload_at TIMESTAMP,
            days_since_last_upload INTEGER,
            liveness_status TEXT CHECK(liveness_status IN ('GREEN', 'YELLOW', 'RED', 'UNKNOWN')),
            source_accounts TEXT NOT NULL,
            recent_videos TEXT,
            raw_subscription_json TEXT,
            raw_channel_json TEXT,
            is_archived INTEGER DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # Migration: ensure is_archived column exists in existing database
        cursor.execute("PRAGMA table_info(subscriptions_master);")
        cols = [row[1] for row in cursor.fetchall()]
        if cols and "is_archived" not in cols:
            cursor.execute("ALTER TABLE subscriptions_master ADD COLUMN is_archived INTEGER DEFAULT 0;")

        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_subs_liveness ON subscriptions_master(liveness_status);
        """)
        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_subs_last_upload ON subscriptions_master(last_upload_at);
        """)
        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_subs_archived ON subscriptions_master(is_archived);
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS quota_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            units INTEGER NOT NULL,
            action TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_quota_date ON quota_usage(date);
        """)
        conn.commit()


def upsert_token(
    channel_id: str,
    email: str,
    title: str,
    refresh_token: str,
    access_token: Optional[str] = None,
    expiry: Optional[datetime] = None,
    db_file: str = DB_FILE,
) -> None:
    expiry_str = expiry.isoformat() if isinstance(expiry, datetime) else (str(expiry) if expiry else None)
    with get_connection(db_file) as conn:
        conn.execute("""
        INSERT INTO oauth_tokens (channel_id, account_email, channel_title, refresh_token, access_token, token_expiry, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(channel_id) DO UPDATE SET
            account_email=excluded.account_email,
            channel_title=excluded.channel_title,
            refresh_token=excluded.refresh_token,
            access_token=excluded.access_token,
            token_expiry=excluded.token_expiry,
            updated_at=CURRENT_TIMESTAMP;
        """, (channel_id, email, title, refresh_token, access_token, expiry_str))
        conn.commit()


def get_all_tokens(db_file: str = DB_FILE) -> List[sqlite3.Row]:
    with get_connection(db_file) as conn:
        return conn.execute("SELECT * FROM oauth_tokens ORDER BY created_at ASC").fetchall()


def delete_token(channel_id: str, db_file: str = DB_FILE) -> None:
    with get_connection(db_file) as conn:
        conn.execute("DELETE FROM oauth_tokens WHERE channel_id = ?", (channel_id,))
        conn.commit()


def delete_master_channel(channel_id: str, db_file: str = DB_FILE) -> None:
    with get_connection(db_file) as conn:
        conn.execute("DELETE FROM subscriptions_master WHERE channel_id = ?", (channel_id,))
        conn.commit()


def set_channel_archived(channel_id: str, is_archived: bool = True, db_file: str = DB_FILE) -> None:
    with get_connection(db_file) as conn:
        conn.execute(
            "UPDATE subscriptions_master SET is_archived = ?, updated_at = CURRENT_TIMESTAMP WHERE channel_id = ?",
            (1 if is_archived else 0, channel_id),
        )
        conn.commit()


def set_channels_archived_batch(channel_ids: List[str], is_archived: bool = True, db_file: str = DB_FILE) -> None:
    if not channel_ids:
        return
    with get_connection(db_file) as conn:
        placeholders = ",".join(["?"] * len(channel_ids))
        params = [1 if is_archived else 0] + list(channel_ids)
        conn.execute(
            f"UPDATE subscriptions_master SET is_archived = ?, updated_at = CURRENT_TIMESTAMP WHERE channel_id IN ({placeholders})",
            params,
        )
        conn.commit()



def get_setting(key: str, default: Optional[str] = None, db_file: str = DB_FILE) -> Optional[str]:
    with get_connection(db_file) as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str, db_file: str = DB_FILE) -> None:
    with get_connection(db_file) as conn:
        conn.execute("""
        INSERT INTO app_settings (key, value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET
            value=excluded.value,
            updated_at=CURRENT_TIMESTAMP;
        """, (key, value))
        conn.commit()


def log_quota(units: int, action: str = "", db_file: str = DB_FILE) -> None:
    today_str = datetime.now().strftime("%Y-%m-%d")
    with get_connection(db_file) as conn:
        conn.execute("""
        INSERT INTO quota_usage (date, units, action)
        VALUES (?, ?, ?)
        """, (today_str, units, action))
        conn.commit()


def get_today_quota(db_file: str = DB_FILE) -> int:
    today_str = datetime.now().strftime("%Y-%m-%d")
    with get_connection(db_file) as conn:
        row = conn.execute("SELECT COALESCE(SUM(units), 0) AS total_units FROM quota_usage WHERE date = ?", (today_str,)).fetchone()
        return int(row["total_units"]) if row else 0


def get_recent_quota_logs(limit: int = 10, db_file: str = DB_FILE) -> List[sqlite3.Row]:
    with get_connection(db_file) as conn:
        return conn.execute("""
        SELECT date, units, action, created_at 
        FROM quota_usage 
        ORDER BY id DESC 
        LIMIT ?
        """, (limit,)).fetchall()


def upsert_subscription_record(record: Dict[str, Any], db_file: str = DB_FILE) -> None:
    def _to_json(val: Any) -> str:
        if val is None:
            return ""
        if isinstance(val, (dict, list)):
            return json.dumps(val, ensure_ascii=False)
        return str(val)

    with get_connection(db_file) as conn:
        conn.execute("""
        INSERT INTO subscriptions_master (
            channel_id, title, description, custom_url, thumbnail_url,
            categories, uploads_playlist_id, last_upload_at, days_since_last_upload,
            liveness_status, source_accounts, recent_videos, raw_subscription_json,
            raw_channel_json, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(channel_id) DO UPDATE SET
            title=excluded.title,
            description=excluded.description,
            custom_url=excluded.custom_url,
            thumbnail_url=excluded.thumbnail_url,
            categories=excluded.categories,
            uploads_playlist_id=excluded.uploads_playlist_id,
            last_upload_at=excluded.last_upload_at,
            days_since_last_upload=excluded.days_since_last_upload,
            liveness_status=excluded.liveness_status,
            source_accounts=excluded.source_accounts,
            recent_videos=excluded.recent_videos,
            raw_subscription_json=excluded.raw_subscription_json,
            raw_channel_json=excluded.raw_channel_json,
            updated_at=CURRENT_TIMESTAMP;
        """, (
            record["channel_id"],
            record.get("title", ""),
            record.get("description", ""),
            record.get("custom_url", ""),
            record.get("thumbnail_url", ""),
            _to_json(record.get("categories", [])),
            record.get("uploads_playlist_id", ""),
            record.get("last_upload_at"),
            record.get("days_since_last_upload"),
            record.get("liveness_status", "UNKNOWN"),
            _to_json(record.get("source_accounts", [])),
            _to_json(record.get("recent_videos", [])),
            _to_json(record.get("raw_subscription_json", {})),
            _to_json(record.get("raw_channel_json", {})),
        ))
        conn.commit()


def fetch_master_records(db_file: str = DB_FILE) -> List[sqlite3.Row]:
    with get_connection(db_file) as conn:
        return conn.execute("""
        SELECT * FROM subscriptions_master 
        ORDER BY 
            CASE 
                WHEN days_since_last_upload IS NULL THEN 999999 
                ELSE days_since_last_upload 
            END ASC, 
            title ASC
        """).fetchall()

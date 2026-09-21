"""SQLite-based user store, email verification codes, and daily token quota tracker.

Designed for cloud deployments (Railway).  All cloud-only features are gated
behind ``is_cloud_managed()`` in api.auth — the module is imported on every
startup (including local) but only *used* in cloud mode.
"""
from __future__ import annotations

import os
import secrets
import sqlite3
import threading
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

from werkzeug.security import generate_password_hash, check_password_hash

from infrastructure.paths import data_path

# Use UTC+8 (Asia/Shanghai) for daily reset boundary
_CN_TZ = timezone(timedelta(hours=8))

_DB_PATH = data_path("auth.db")
_LOCK = threading.Lock()

# Verification code lifetime
_CODE_TTL_MINUTES = 5
# Minimum seconds between code sends for the same email
_CODE_RESEND_SECONDS = 60


def _get_conn() -> sqlite3.Connection:
    Path(_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_schema() -> None:
    with _get_conn() as conn:
        # --- migration: drop legacy username-based users table ---
        cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
        if cols and "email" not in cols:
            conn.execute("DROP TABLE IF EXISTS users")
        # --- tables ---
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id            TEXT PRIMARY KEY,
                email         TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at    TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS email_codes (
                email      TEXT NOT NULL,
                code       TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                used       INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (email, code)
            );
            CREATE TABLE IF NOT EXISTS daily_usage (
                user_id     TEXT NOT NULL,
                date        TEXT NOT NULL,
                tokens_used INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, date),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
        """)
        conn.commit()


_ensure_schema()


def _today() -> str:
    return datetime.now(_CN_TZ).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
#  User CRUD
# ---------------------------------------------------------------------------

def create_user(email: str, password: str) -> dict | None:
    """Register a new user. Returns user dict or None if email taken."""
    with _LOCK:
        conn = _get_conn()
        try:
            existing = conn.execute(
                "SELECT 1 FROM users WHERE email = ?", (email,)
            ).fetchone()
            if existing:
                return None
            uid = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO users (id, email, password_hash) VALUES (?, ?, ?)",
                (uid, email, generate_password_hash(password)),
            )
            conn.commit()
            return {"id": uid, "email": email}
        finally:
            conn.close()


def verify_user(email: str, password: str) -> dict | None:
    """Authenticate. Returns user dict or None."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT id, email, password_hash FROM users WHERE email = ?",
            (email,),
        ).fetchone()
        if not row or not check_password_hash(row["password_hash"], password):
            return None
        return {"id": row["id"], "email": row["email"]}
    finally:
            conn.close()


def get_user_by_id(uid: str) -> dict | None:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT id, email FROM users WHERE id = ?", (uid,)
        ).fetchone()
        if not row:
            return None
        return {"id": row["id"], "email": row["email"]}
    finally:
            conn.close()


# ---------------------------------------------------------------------------
#  Email verification codes
# ---------------------------------------------------------------------------

def generate_code() -> str:
    """Return a 6-digit verification code."""
    return f"{secrets.randbelow(1000000):06d}"


def store_email_code(email: str, code: str) -> None:
    """Store a verification code with expiry. Deletes previous unused codes."""
    now = datetime.now(_CN_TZ)
    expires = now + timedelta(minutes=_CODE_TTL_MINUTES)
    with _LOCK:
        conn = _get_conn()
        try:
            conn.execute(
                "DELETE FROM email_codes WHERE email = ? AND used = 0", (email,)
            )
            conn.execute(
                "INSERT INTO email_codes (email, code, expires_at) VALUES (?, ?, ?)",
                (email, code, expires.strftime("%Y-%m-%d %H:%M:%S")),
            )
            conn.commit()
        finally:
            conn.close()


def can_resend(email: str) -> bool:
    """Return True if enough time has passed since the last code was sent."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT created_at FROM email_codes WHERE email = ? ORDER BY created_at DESC LIMIT 1",
            (email,),
        ).fetchone()
        if not row:
            return True
        last = datetime.strptime(row["created_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=_CN_TZ)
        return (datetime.now(_CN_TZ) - last).total_seconds() >= _CODE_RESEND_SECONDS
    finally:
            conn.close()


def consume_email_code(email: str, code: str) -> bool:
    """Verify and consume a code. Returns True if valid, False otherwise."""
    with _LOCK:
        conn = _get_conn()
        try:
            now = datetime.now(_CN_TZ).strftime("%Y-%m-%d %H:%M:%S")
            row = conn.execute(
                """SELECT 1 FROM email_codes
                   WHERE email = ? AND code = ? AND used = 0
                     AND expires_at > ?""",
                (email, code, now),
            ).fetchone()
            if not row:
                return False
            conn.execute(
                "UPDATE email_codes SET used = 1 WHERE email = ? AND code = ?",
                (email, code),
            )
            conn.commit()
            return True
        finally:
            conn.close()


# ---------------------------------------------------------------------------
#  Daily token quota
# ---------------------------------------------------------------------------

def get_daily_usage(uid: str) -> int:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT tokens_used FROM daily_usage WHERE user_id = ? AND date = ?",
            (uid, _today()),
        ).fetchone()
        return row["tokens_used"] if row else 0
    finally:
            conn.close()


def add_usage(uid: str, tokens: int) -> int:
    with _LOCK:
        conn = _get_conn()
        try:
            conn.execute(
                """INSERT INTO daily_usage (user_id, date, tokens_used)
                   VALUES (?, ?, ?)
                   ON CONFLICT (user_id, date)
                   DO UPDATE SET tokens_used = tokens_used + ?""",
                (uid, _today(), tokens, tokens),
            )
            conn.commit()
            return get_daily_usage(uid)
        finally:
            conn.close()


DAILY_TOKEN_LIMIT = int(os.environ.get("BAA_DAILY_TOKEN_LIMIT", "50000"))


def check_quota(uid: str) -> dict:
    used = get_daily_usage(uid)
    remaining = max(0, DAILY_TOKEN_LIMIT - used)
    return {
        "used": used,
        "limit": DAILY_TOKEN_LIMIT,
        "remaining": remaining,
        "exceeded": used >= DAILY_TOKEN_LIMIT,
    }


# ---------------------------------------------------------------------------
#  管理员功能（本地企业部署）
# ---------------------------------------------------------------------------

def admin_create_user(email: str, password: str, created_by: str = "admin") -> dict | None:
    """管理员直接创建用户（无需邮件验证码）
    
    适用场景：企业内部部署，IT 管理员统一创建员工账号
    
    Args:
        email: 用户邮箱
        password: 初始密码
        created_by: 创建者标识（用于审计）
    
    Returns:
        用户字典或 None（邮箱已存在）
    """
    import logging
    log = logging.getLogger(__name__)
    
    with _LOCK:
        conn = _get_conn()
        try:
            # 检查邮箱是否已存在
            existing = conn.execute(
                "SELECT 1 FROM users WHERE email = ?", (email,)
            ).fetchone()
            if existing:
                return None
            
            uid = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO users (id, email, password_hash) VALUES (?, ?, ?)",
                (uid, email, generate_password_hash(password)),
            )
            conn.commit()
            
            log.info(f"[admin] 用户创建成功: {email} (by {created_by})")
            return {"id": uid, "email": email}
        finally:
            conn.close()


def admin_list_users(limit: int = 100) -> list[dict]:
    """管理员查看所有用户列表（按创建时间升序，最早的在前）"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, email, created_at FROM users ORDER BY created_at ASC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def admin_delete_user(email: str) -> bool:
    """管理员删除用户
    
    Returns:
        True = 删除成功, False = 用户不存在
    """
    import logging
    log = logging.getLogger(__name__)
    
    with _LOCK:
        conn = _get_conn()
        try:
            # 获取用户 ID
            row = conn.execute(
                "SELECT id FROM users WHERE email = ?", (email,)
            ).fetchone()
            if not row:
                return False
            
            uid = row["id"]
            
            # 删除用户及其配额记录
            conn.execute("DELETE FROM users WHERE id = ?", (uid,))
            conn.execute("DELETE FROM daily_usage WHERE user_id = ?", (uid,))
            conn.commit()
            
            log.info(f"[admin] 用户删除成功: {email}")
            return True
        finally:
            conn.close()


def admin_reset_password(email: str, new_password: str) -> bool:
    """管理员重置用户密码
    
    Returns:
        True = 重置成功, False = 用户不存在
    """
    import logging
    log = logging.getLogger(__name__)
    
    with _LOCK:
        conn = _get_conn()
        try:
            result = conn.execute(
                "UPDATE users SET password_hash = ? WHERE email = ?",
                (generate_password_hash(new_password), email),
            )
            conn.commit()
            
            if result.rowcount > 0:
                log.info(f"[admin] 密码重置成功: {email}")
                return True
            return False
        finally:
            conn.close()

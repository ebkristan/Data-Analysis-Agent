"""Authentication blueprint — email + verification code register / password login (cloud-only)."""
from __future__ import annotations

import logging
import os
import secrets

from flask import Blueprint, request, jsonify, session, render_template

from data.auth_store import (
    create_user, verify_user, get_user_by_id,
    check_quota, DAILY_TOKEN_LIMIT,
    generate_code, store_email_code, can_resend, consume_email_code,
)
from data.email_sender import send_code as _send_email_code, is_configured as _smtp_configured

log = logging.getLogger(__name__)

bp = Blueprint("auth", __name__)


def get_or_create_secret_key() -> str:
    """Resolve the Flask session secret key.

    Priority:
      1. ``BAA_SECRET_KEY`` environment variable (highest precedence).
      2. ``{data_root}/secret_key`` file on disk (survives redeploys when
         ``data_root`` lives on a Railway mounted volume).
      3. Auto-generate a 64‑char hex key, persist it to disk so the next
         cold start reads the same value, and return it.
    """
    # 1. Explicit override via env
    env_val = os.environ.get("BAA_SECRET_KEY")
    if env_val:
        return env_val

    from infrastructure.paths import data_path
    key_file = data_path("secret_key")

    # 2. Read existing key from persistent disk
    if key_file.exists():
        try:
            content = key_file.read_text(encoding="utf-8").strip()
            if len(content) >= 32:
                return content
            log.warning("[auth] secret_key file too short (%d chars), regenerating", len(content))
        except Exception:
            log.exception("[auth] failed to read secret_key file, regenerating")

    # 3. First run — generate, persist, return
    new_key = secrets.token_hex(32)  # 64 hex chars
    try:
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_text(new_key, encoding="utf-8")
        log.info("[auth] generated and persisted new secret_key to %s", key_file)
    except Exception:
        log.exception("[auth] could not persist secret_key to %s — key lives in memory only", key_file)

    return new_key


SECRET_KEY = get_or_create_secret_key()


def is_cloud_managed() -> bool:
    """判断是否为云端托管环境（Railway/Vercel）"""
    return bool(os.environ.get("RAILWAY_PROJECT_ID")) or os.environ.get("VERCEL") == "1"


def is_auth_enabled() -> bool:
    """判断是否启用认证系统（登录/用户隔离）
    
    适用场景：
    - BAA_ENABLE_AUTH=1: 本地企业多用户部署
    - RAILWAY_PROJECT_ID 或 VERCEL=1: 云端部署（自动启用）
    """
    # 显式启用认证（适用于本地企业部署）
    if os.environ.get("BAA_ENABLE_AUTH") == "1":
        return True
    # 云端环境自动启用（向后兼容）
    return is_cloud_managed()


def current_user() -> dict | None:
    """Return the authenticated user dict from the Flask session, or None."""
    uid = session.get("uid")
    if not uid:
        return None
    return get_user_by_id(uid)


def _load_agreement_html() -> str:
    """Read Information/User_Agreement.md and convert to simple HTML."""
    import pathlib
    import traceback
    md_path = pathlib.Path(__file__).resolve().parent.parent / "Information" / "User_Agreement.md"
    try:
        text = md_path.read_text(encoding="utf-8")
    except Exception:
        log.exception("Failed to load user agreement from %s", md_path)
        return "<p>用户协议加载失败。</p>"

    lines = text.splitlines()
    html_parts = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped == "---":
            continue
        if stripped.startswith("> "):
            continue  # skip blockquote meta
        if stripped.startswith("## "):
            html_parts.append(f"<h4>{stripped[3:]}</h4>")
        elif stripped.startswith("# "):
            html_parts.append(f"<h3>{stripped[2:]}</h3>")
        else:
            html_parts.append(f"<p>{stripped}</p>")
    return "\n".join(html_parts)


def _agreement_ctx() -> dict:
    """Template context dict with agreement HTML."""
    return {"agreement_html": _load_agreement_html()}


# ---------------------------------------------------------------------------
#  Pages
# ---------------------------------------------------------------------------

@bp.get("/login")
def login_page():
    """Serve the login page (when auth is enabled). Redirect to app if already authed."""
    if not is_auth_enabled():
        return ("", 403)
    if current_user():
        return render_template("agent_chat.html",
                              desktop_lifecycle_enabled=False,
                              is_cloud_managed=is_auth_enabled())
    return render_template("login.html", quota_limit=DAILY_TOKEN_LIMIT, **_agreement_ctx())


# ---------------------------------------------------------------------------
#  Send verification code
# ---------------------------------------------------------------------------

@bp.post("/api/auth/send-code")
def send_verification_code():
    """发送验证码 - 企业部署模式已禁用"""
    return jsonify({"error": "企业部署模式不支持自助注册，请联系管理员创建账号"}), 403


# ---------------------------------------------------------------------------
#  Register (email + verification code + password)
# ---------------------------------------------------------------------------

@bp.post("/api/auth/register")
def register():
    """用户注册 - 企业部署模式已禁用"""
    # 企业部署模式：禁用自助注册，由管理员统一创建账号
    from .auth import is_cloud_managed
    if not is_cloud_managed():
        return jsonify({"error": "企业部署模式不支持自助注册，请联系管理员创建账号"}), 403
    
    # 云端模式保留原有注册逻辑
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    code = (data.get("code") or "").strip()
    password = data.get("password") or ""

    if not email or "@" not in email:
        return jsonify({"error": "请输入有效邮箱"}), 400
    if len(password) < 4:
        return jsonify({"error": "密码至少 4 个字符"}), 400
    if not code or len(code) != 6:
        return jsonify({"error": "请输入 6 位验证码"}), 400

    if not consume_email_code(email, code):
        return jsonify({"error": "验证码无效或已过期"}), 400

    user = create_user(email, password)
    if not user:
        return jsonify({"error": "该邮箱已注册"}), 409

    session["uid"] = user["id"]
    return jsonify({"ok": True, "user": {"id": user["id"], "email": user["email"]}})


# ---------------------------------------------------------------------------
#  Login (email + password)
# ---------------------------------------------------------------------------

@bp.post("/api/auth/login")
def login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not email or not password:
        return jsonify({"error": "请输入账号和密码"}), 400

    user = verify_user(email, password)
    if not user:
        return jsonify({"error": "账号或密码错误"}), 401

    session["uid"] = user["id"]
    return jsonify({"ok": True, "user": {"id": user["id"], "email": user["email"]}})


@bp.post("/api/auth/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})


@bp.get("/api/auth/me")
def me():
    user = current_user()
    if not user:
        return jsonify({"authenticated": False}), 401
    quota = check_quota(user["id"])
    return jsonify({
        "authenticated": True,
        "user": {"id": user["id"], "email": user["email"]},
        "quota": quota,
    })


# ---------------------------------------------------------------------------
#  管理员 API（本地企业部署）
# ---------------------------------------------------------------------------

def _is_admin() -> bool:
    """判断当前用户是否为管理员
    
    策略：
    1. 检查环境变量 BAA_ADMIN_EMAILS（逗号分隔的管理员邮箱列表）
    2. 如果未配置，第一个注册的用户自动成为管理员
    """
    user = current_user()
    if not user:
        return False
    
    # 方式1：环境变量指定管理员
    admin_emails = os.environ.get("BAA_ADMIN_EMAILS", "").lower().split(",")
    admin_emails = [e.strip() for e in admin_emails if e.strip()]
    if admin_emails and user["email"].lower() in admin_emails:
        return True
    
    # 方式2：第一个用户自动成为管理员
    from data.auth_store import admin_list_users
    users = admin_list_users(limit=1)
    if users and users[0]["email"] == user["email"]:
        return True
    
    return False


@bp.post("/api/admin/users")
def admin_create_user_endpoint():
    """管理员创建用户"""
    if not is_auth_enabled():
        return jsonify({"error": "认证未启用"}), 403
    
    if not _is_admin():
        return jsonify({"error": "需要管理员权限"}), 403
    
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    
    if not email:
        return jsonify({"error": "请输入账号"}), 400
    if len(password) < 4:
        return jsonify({"error": "密码至少 4 个字符"}), 400
    
    from data.auth_store import admin_create_user
    current = current_user()
    user = admin_create_user(email, password, created_by=current["email"] if current else "system")
    
    if not user:
        return jsonify({"error": "该邮箱已注册"}), 409
    
    return jsonify({"ok": True, "user": {"id": user["id"], "email": user["email"]}})


@bp.get("/api/admin/users")
def admin_list_users_endpoint():
    """管理员查看用户列表"""
    if not is_auth_enabled():
        return jsonify({"error": "认证未启用"}), 403
    
    if not _is_admin():
        return jsonify({"error": "需要管理员权限"}), 403
    
    from data.auth_store import admin_list_users
    users = admin_list_users(limit=500)
    return jsonify({"ok": True, "users": users})


@bp.delete("/api/admin/users/<email>")
def admin_delete_user_endpoint(email: str):
    """管理员删除用户"""
    if not is_auth_enabled():
        return jsonify({"error": "认证未启用"}), 403
    
    if not _is_admin():
        return jsonify({"error": "需要管理员权限"}), 403
    
    from data.auth_store import admin_delete_user
    if admin_delete_user(email):
        return jsonify({"ok": True, "message": f"用户 {email} 已删除"})
    else:
        return jsonify({"error": "用户不存在"}), 404


@bp.post("/api/admin/users/<email>/reset-password")
def admin_reset_password_endpoint(email: str):
    """管理员重置用户密码"""
    if not is_auth_enabled():
        return jsonify({"error": "认证未启用"}), 403
    
    if not _is_admin():
        return jsonify({"error": "需要管理员权限"}), 403
    
    data = request.get_json(silent=True) or {}
    new_password = data.get("password") or ""
    
    if len(new_password) < 4:
        return jsonify({"error": "密码至少 4 个字符"}), 400
    
    from data.auth_store import admin_reset_password
    if admin_reset_password(email, new_password):
        return jsonify({"ok": True, "message": f"用户 {email} 密码已重置"})
    else:
        return jsonify({"error": "用户不存在"}), 404


@bp.get("/api/admin/check")
def admin_check():
    """检查当前用户是否为管理员"""
    if not is_auth_enabled():
        return jsonify({"is_admin": False, "auth_enabled": False})
    
    return jsonify({"is_admin": _is_admin(), "auth_enabled": True})

"""
auth_dependencies.py

FastAPI dependency functions for session-based authentication. Verifies Firebase
session cookies, checks idle timeout against Firestore active sessions, and
provides role-based access control (require_admin). Used by the auth endpoints
that support Edit Staff Profile and Edit Staff (admin).

Author: Dominique Anne Lee
"""

from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import Cookie, Depends, HTTPException
from helpers.firebase_admin_setup import get_firestore_client, get_firebase_auth

SESSION_COOKIE_NAME = "session"
IDLE_TIMEOUT_SECONDS = 10 * 60  # keep same as auth.py

db = get_firestore_client()
firebase_auth = get_firebase_auth()


def normalize_email(email: str) -> str:
    return email.strip().lower()


def get_allowed_user_data(email: str):
    email = normalize_email(email)
    doc_ref = db.collection("allowedUsers").document(email)
    snap = doc_ref.get()

    if not snap.exists:
        return None

    data = snap.to_dict()

    if data.get("active") is not True:
        return None

    return data


def get_session_doc_ref(uid: str):
    return db.collection("activeSessions").document(uid)


async def get_current_user_from_session(
    session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME)
):
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        decoded_claims = firebase_auth.verify_session_cookie(
            session,
            check_revoked=True
        )
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    uid = decoded_claims.get("uid")
    email = normalize_email(decoded_claims.get("email", ""))

    if not uid:
        raise HTTPException(status_code=401, detail="Invalid session")

    session_snap = get_session_doc_ref(uid).get()
    if not session_snap.exists:
        raise HTTPException(status_code=401, detail="Session not found")

    session_data = session_snap.to_dict()
    last_activity_at = session_data.get("lastActivityAt")
    now = datetime.now(timezone.utc)

    if not last_activity_at:
        get_session_doc_ref(uid).delete()
        try:
            firebase_auth.revoke_refresh_tokens(uid)
        except Exception:
            pass
        raise HTTPException(status_code=401, detail="Session expired due to inactivity")

    idle_seconds = (now - last_activity_at).total_seconds()
    if idle_seconds > IDLE_TIMEOUT_SECONDS:
        get_session_doc_ref(uid).delete()
        try:
            firebase_auth.revoke_refresh_tokens(uid)
        except Exception:
            pass
        raise HTTPException(status_code=401, detail="Session expired due to inactivity")

    allowed_user = get_allowed_user_data(email)
    if not allowed_user:
        raise HTTPException(status_code=403, detail="User is not allowed")

    return {
        "uid": uid,
        "email": email,
        "role": allowed_user.get("role", "user"),
    }


async def require_admin(user=Depends(get_current_user_from_session)):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
"""
auth_dependencies.py

FastAPI dependency functions for session-based authentication and authorization.

This module verifies Firebase session cookies, validates idle timeout against
Firestore active session records, and provides role-based access control for
protected endpoints. It is used by authentication-related routes that require
an authenticated user or admin-only access.

Author: Anna Yabut
"""

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import Cookie, Depends, HTTPException
from helpers.firebase_admin_setup import get_firestore_client, get_firebase_auth

SESSION_COOKIE_NAME = "session"
IDLE_TIMEOUT_SECONDS = 10 * 60  # Keep aligned with auth.py session idle timeout.

db = get_firestore_client()
firebase_auth = get_firebase_auth()


def normalize_email(email: str) -> str:
    """
    Normalizes an email address for consistent lookup and comparison.

    Args:
        email: Raw email string from Firebase claims or request data.

    Returns:
        Lowercased email with surrounding whitespace removed.
    """
    return email.strip().lower()


def get_allowed_user_data(email: str) -> Optional[dict[str, Any]]:
    """
    Retrieves the allowed user record for an active user.

    Args:
        email: User email address used as the Firestore document ID.

    Returns:
        The allowed user document data if the user exists and is marked active;
        otherwise None.

    Notes:
        Email is normalized before querying Firestore so document lookups remain
        case-insensitive.
    """
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
    """
    Returns the Firestore document reference for an active session.

    Args:
        uid: Firebase Authentication user ID.

    Returns:
        Firestore document reference for the user's active session record.
    """
    return db.collection("activeSessions").document(uid)


async def get_current_user_from_session(
    session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME)
) -> dict[str, str]:
    """
    Resolves the currently authenticated user from the session cookie.

    Verifies the Firebase session cookie, confirms the corresponding active
    session exists in Firestore, and enforces the configured idle timeout.
    If the session is missing required activity data or has timed out, the
    stored session is deleted and Firebase refresh tokens are revoked.

    Args:
        session: Session cookie value sent by the client.

    Returns:
        A dictionary containing the authenticated user's uid, normalized email,
        and resolved role.

    Raises:
        HTTPException: 401 if the session is missing, invalid, expired, or no
            longer tracked in Firestore.
        HTTPException: 403 if the authenticated email is not present in the
            allowed users collection or is inactive.
    """
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


async def require_admin(
    user: dict[str, str] = Depends(get_current_user_from_session)
) -> dict[str, str]:
    """
    Ensures the authenticated user has admin access.

    Args:
        user: Authenticated user dictionary resolved from the session dependency.

    Returns:
        The same authenticated user dictionary when the user has the admin role.

    Raises:
        HTTPException: 403 if the authenticated user is not an admin.
    """
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
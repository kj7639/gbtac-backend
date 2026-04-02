"""
auth.py

Router for authentication, session management, staff CRUD, and password reset
flows. Integrates Firebase Auth for identity, Firestore for user records and
session tracking, and Cloudflare Turnstile for CAPTCHA verification. Provides
the update-profile and admin/update-staff endpoints used by the Edit Staff
Profile and Edit Staff (admin) pages.

Author: Dominique Anne Lee, Anna Yabut  
"""

import os
import httpx
import random
import re

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Request, HTTPException, Response, Cookie, Depends, Query
from firebase_admin import firestore
from pydantic import BaseModel, EmailStr

from helpers.auth_dependencies import (
    SESSION_COOKIE_NAME,
    normalize_email,
    get_allowed_user_data,
    require_admin,
    get_current_user_from_session,
)
from helpers.email_service import send_reset_code_email
from helpers.firebase_admin_setup import get_firestore_client, get_firebase_auth
from helpers.rate_limit import limiter

router = APIRouter(prefix="/auth")

db = get_firestore_client()
firebase_auth = get_firebase_auth()
TURNSTILE_SECRET_KEY = os.getenv("TURNSTILE_SECRET_KEY")


class ResetRequest(BaseModel):
    email: EmailStr


class EmailRequest(BaseModel):
    email: str


class TokenRequest(BaseModel):
    idToken: str


class CaptchaRequest(BaseModel):
    captcha_token: str


class DeleteStaffRequest(BaseModel):
    email: EmailStr


class RequestPasswordResetRequest(BaseModel):
    email: EmailStr


class VerifyResetCodeRequest(BaseModel):
    email: EmailStr
    code: str


class ConfirmPasswordResetRequest(BaseModel):
    email: EmailStr
    code: str
    newPassword: str


class RefreshSessionRequest(BaseModel):
    idToken: str


class UpdateEmailRequest(BaseModel):
    oldEmail: EmailStr
    newEmail: EmailStr


class UpdateProfileRequest(BaseModel):
    email: EmailStr
    firstName: str
    lastName: str
    active: bool


class CreateStaffRequest(BaseModel):
    email: EmailStr
    firstName: str
    lastName: str
    active: bool = True


class AdminUpdateStaffRequest(BaseModel):
    originalEmail: EmailStr
    email: EmailStr
    firstName: str
    lastName: str
    active: bool
    password: Optional[str] = None


MAX_FAILED_ATTEMPTS = 5
RESET_COOLDOWN_SECONDS = 60
SESSION_EXPIRES_SECONDS = 60 * 60 * 8
IDLE_TIMEOUT_SECONDS = 10 * 60
HEARTBEAT_MIN_SECONDS = 60
RESET_CODE_EXPIRES_MINUTES = 10
RESET_MAX_VERIFY_ATTEMPTS = 5


def get_lockout_duration_seconds(level: int) -> int:
    """
    Returns the lockout duration for a given lockout level.

    Args:
        level: Current lockout escalation level.

    Returns:
        Lockout duration in seconds.
    """
    if level == 1:
        return 60
    elif level == 2:
        return 300
    elif level == 3:
        return 900
    return 1800


def generate_six_digit_code() -> str:
    """
    Generates a zero-padded six-digit verification code.

    Returns:
        Random six-digit string used for password reset verification.
    """
    return f"{random.randint(0, 999999):06d}"


def is_valid_password(password: str) -> bool:
    """
    Validates password complexity requirements.

    Args:
        password: Plain text password submitted by the user.

    Returns:
        True if the password meets the required complexity rules; otherwise False.

    Notes:
        Passwords must be at least 8 characters and include an uppercase letter,
        a number, and a special character.
    """
    return (
        len(password) >= 8
        and re.search(r"[A-Z]", password)
        and re.search(r"[0-9]", password)
        and re.search(r"[!@#$%^&*]", password)
    )


def get_session_doc_ref(uid: str):
    """
    Returns the Firestore document reference for an active session.

    Args:
        uid: Firebase Authentication user ID.

    Returns:
        Firestore document reference for the user's active session.
    """
    return db.collection("activeSessions").document(uid)


def upsert_active_session(uid: str, email: str) -> None:
    """
    Creates or updates the active session record for a user.

    Args:
        uid: Firebase Authentication user ID.
        email: Normalized user email address.
    """
    now = datetime.now(timezone.utc)

    get_session_doc_ref(uid).set(
        {
            "uid": uid,
            "email": email,
            "lastActivityAt": now,
            "updatedAt": firestore.SERVER_TIMESTAMP,
        },
        merge=True,
    )


def clear_active_session(uid: str) -> None:
    """
    Deletes the stored active session record for a user.

    Args:
        uid: Firebase Authentication user ID.

    Notes:
        Any Firestore deletion error is ignored because logout and session cleanup
        should continue even if the record is already missing.
    """
    try:
        get_session_doc_ref(uid).delete()
    except Exception:
        pass


@router.post("/verify-captcha")
async def verify_captcha(payload: CaptchaRequest, request: Request) -> dict[str, bool]:
    """
    Verifies a Cloudflare Turnstile CAPTCHA token.

    Sends the provided CAPTCHA token to Cloudflare before allowing protected
    actions such as login or password reset flows.

    Args:
        payload: Request body containing the CAPTCHA token.
        request: Incoming request used to resolve the client IP address.

    Returns:
        Dictionary containing a success flag when verification passes.

    Raises:
        HTTPException: 400 if CAPTCHA verification fails.
        HTTPException: 500 if the Turnstile secret key is not configured.
    """
    if not TURNSTILE_SECRET_KEY:
        raise HTTPException(
            status_code=500,
            detail="Turnstile secret key is not configured.",
        )

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data={
                "secret": TURNSTILE_SECRET_KEY,
                "response": payload.captcha_token,
                "remoteip": request.client.host if request.client else None,
            },
        )
        result = response.json()

    if not result.get("success", False):
        raise HTTPException(
            status_code=400,
            detail="CAPTCHA verification failed.",
        )

    return {"success": True}


@router.post("/check-lockout")
def check_lockout(payload: EmailRequest) -> dict[str, int | bool]:
    """
    Checks whether a user is currently locked out from login attempts.

    Args:
        payload: Request body containing the email address to check.

    Returns:
        Dictionary indicating whether the account is locked and, if so, how many
        seconds remain in the lockout window.
    """
    email = normalize_email(payload.email)
    doc_ref = db.collection("loginAttempts").document(email)
    snap = doc_ref.get()

    if not snap.exists:
        return {"locked": False, "remainingSeconds": 0}

    data = snap.to_dict()
    lockout_until = data.get("lockoutUntil")

    if not lockout_until:
        return {"locked": False, "remainingSeconds": 0}

    now = datetime.now(timezone.utc)

    if lockout_until > now:
        remaining = int((lockout_until - now).total_seconds())
        return {"locked": True, "remainingSeconds": remaining}

    doc_ref.set(
        {
            "failedAttempts": 0,
            "lockoutUntil": None,
            "updatedAt": firestore.SERVER_TIMESTAMP,
        },
        merge=True,
    )

    return {"locked": False, "remainingSeconds": 0}


@router.post("/record-failed-login")
def record_failed_login(payload: EmailRequest) -> dict[str, int | bool]:
    """
    Records a failed login attempt and applies lockout rules if needed.

    Args:
        payload: Request body containing the email address associated with the
            failed login attempt.

    Returns:
        Dictionary describing the updated lockout state, remaining attempts, and
        current lockout level.
    """
    email = normalize_email(payload.email)
    doc_ref = db.collection("loginAttempts").document(email)
    snap = doc_ref.get()

    failed_attempts = 0
    lockout_level = 0

    if snap.exists:
        data = snap.to_dict()
        failed_attempts = data.get("failedAttempts", 0)
        lockout_level = data.get("lockoutLevel", 0)

    failed_attempts += 1

    locked = False
    remaining_attempts = MAX_FAILED_ATTEMPTS - failed_attempts
    remaining_seconds = 0

    if failed_attempts >= MAX_FAILED_ATTEMPTS:
        lockout_level += 1
        remaining_seconds = get_lockout_duration_seconds(lockout_level)
        lockout_until = datetime.now(timezone.utc) + timedelta(seconds=remaining_seconds)

        locked = True
        failed_attempts = 0
        remaining_attempts = 0

        doc_ref.set(
            {
                "failedAttempts": 0,
                "lockoutLevel": lockout_level,
                "lockoutUntil": lockout_until,
                "updatedAt": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )
    else:
        doc_ref.set(
            {
                "failedAttempts": failed_attempts,
                "lockoutLevel": lockout_level,
                "updatedAt": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )

    return {
        "locked": locked,
        "remainingAttempts": max(remaining_attempts, 0),
        "remainingSeconds": remaining_seconds,
        "lockoutLevel": lockout_level,
    }


@router.post("/reset-login-attempts")
def reset_login_attempts(payload: EmailRequest) -> dict[str, bool]:
    """
    Resets stored login attempts for a user.

    This is typically used after a successful login or completed password reset.

    Args:
        payload: Request body containing the email address to reset.

    Returns:
        Dictionary containing a success flag.
    """
    email = normalize_email(payload.email)
    doc_ref = db.collection("loginAttempts").document(email)

    doc_ref.set(
        {
            "failedAttempts": 0,
            "lockoutLevel": 0,
            "lockoutUntil": None,
            "updatedAt": firestore.SERVER_TIMESTAMP,
        },
        merge=True,
    )

    return {"success": True}


@router.post("/check-allowed-user")
def check_allowed_user(payload: TokenRequest) -> dict[str, Any]:
    """
    Checks whether the authenticated Firebase user is allowed to access the system.

    Args:
        payload: Request body containing a Firebase ID token.

    Returns:
        Dictionary containing the allowed status and, when allowed, the user's
        normalized email and role.

    Raises:
        HTTPException: 401 if the Firebase ID token is invalid.
    """
    try:
        decoded_token = firebase_auth.verify_id_token(payload.idToken)
    except Exception as e:
        print("VERIFY TOKEN ERROR:", repr(e))
        raise HTTPException(status_code=401, detail=str(e))

    email = normalize_email(decoded_token.get("email", ""))
    allowed_user = get_allowed_user_data(email)

    if not allowed_user:
        return {"allowed": False}

    return {
        "allowed": True,
        "email": email,
        "role": allowed_user.get("role", "user"),
    }


@router.post("/session-login")
def session_login(payload: TokenRequest, response: Response) -> dict[str, Any]:
    """
    Creates a session cookie after a successful frontend login.

    Verifies the Firebase ID token, checks whether the user is allowed, creates
    a Firebase session cookie, stores an active session record, and returns
    basic user information for the frontend.

    Args:
        payload: Request body containing a Firebase ID token.
        response: Outgoing response used to set the session cookie.

    Returns:
        Dictionary containing success status, session message, uid, email, and role.

    Raises:
        HTTPException: 401 if token verification or session creation fails.
        HTTPException: 403 if the user is not allowed to access the system.
    """
    try:
        decoded_token = firebase_auth.verify_id_token(payload.idToken)
    except Exception as e:
        print("SESSION LOGIN VERIFY ERROR:", repr(e))
        raise HTTPException(status_code=401, detail=str(e))

    email = normalize_email(decoded_token.get("email", ""))
    uid = decoded_token.get("uid")
    allowed_user = get_allowed_user_data(email)

    if not allowed_user:
        raise HTTPException(status_code=403, detail="User is not allowed")

    try:
        session_cookie = firebase_auth.create_session_cookie(
            payload.idToken,
            expires_in=timedelta(seconds=SESSION_EXPIRES_SECONDS)
        )
    except Exception as e:
        print("CREATE SESSION COOKIE ERROR:", repr(e))
        raise HTTPException(status_code=401, detail=str(e))

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_cookie,
        max_age=SESSION_EXPIRES_SECONDS,
        httponly=True,
        secure=False,
        samesite="lax",
        path="/",
    )

    upsert_active_session(uid, email)

    return {
        "success": True,
        "message": "Session created",
        "uid": uid,
        "email": email,
        "role": allowed_user.get("role", "user"),
    }


@router.get("/me")
async def get_current_user_me(
    current_user: dict[str, str] = Depends(get_current_user_from_session)
) -> dict[str, str]:
    """
    Returns the currently authenticated user's basic session information.

    Args:
        current_user: Authenticated user resolved from the session dependency.

    Returns:
        Dictionary containing the current user's uid, email, and role.
    """
    return {
        "uid": current_user["uid"],
        "email": current_user["email"],
        "role": current_user["role"],
    }


@router.post("/logout")
def logout(
    response: Response,
    session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME)
) -> dict[str, Any]:
    """
    Logs out the current user by clearing the session cookie and revoking tokens.

    Args:
        response: Outgoing response used to clear the session cookie.
        session: Current session cookie value, if present.

    Returns:
        Dictionary containing success status and a logout message.
    """
    if session:
        try:
            decoded_claims = firebase_auth.verify_session_cookie(session, check_revoked=True)
            uid = decoded_claims["uid"]
            clear_active_session(uid)
            firebase_auth.revoke_refresh_tokens(uid)
        except Exception:
            pass

    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        samesite="lax",
    )

    return {
        "success": True,
        "message": "Logged out",
    }


@router.post("/update-email")
def update_email(payload: UpdateEmailRequest) -> dict[str, Any]:
    """
    Updates a user's email address in the allowedUsers collection.

    Copies the existing document to a new email-based document ID, updates the
    stored email value, and deletes the old document.

    Args:
        payload: Request body containing the old and new email addresses.

    Returns:
        Dictionary containing success status, message, old email, and new email.

    Raises:
        HTTPException: 404 if the original user document does not exist.
        HTTPException: 500 if the update process fails.
    """
    old_email = normalize_email(str(payload.oldEmail))
    new_email = normalize_email(str(payload.newEmail))

    try:
        old_doc_ref = db.collection("allowedUsers").document(old_email)
        old_doc = old_doc_ref.get()

        if not old_doc.exists:
            raise HTTPException(status_code=404, detail="User not found")

        user_data = old_doc.to_dict()

        new_doc_ref = db.collection("allowedUsers").document(new_email)
        user_data["email"] = new_email
        user_data["updatedAt"] = firestore.SERVER_TIMESTAMP
        new_doc_ref.set(user_data)

        old_doc_ref.delete()

        return {
            "success": True,
            "message": "Email updated successfully",
            "oldEmail": old_email,
            "newEmail": new_email
        }
    except HTTPException:
        raise
    except Exception as e:
        print("UPDATE EMAIL ERROR:", repr(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/update-profile")
def update_profile(payload: UpdateProfileRequest) -> dict[str, Any]:
    """
    Updates a user's basic profile fields in Firestore.

    Args:
        payload: Request body containing the target email and updated profile fields.

    Returns:
        Dictionary containing success status, message, and the updated email.

    Raises:
        HTTPException: 404 if the user document does not exist.
        HTTPException: 500 if the update fails.
    """
    email = normalize_email(str(payload.email))

    try:
        doc_ref = db.collection("allowedUsers").document(email)
        doc = doc_ref.get()

        if not doc.exists:
            raise HTTPException(status_code=404, detail="User not found")

        update_data = {
            "firstName": payload.firstName,
            "lastName": payload.lastName,
            "active": payload.active,
            "updatedAt": firestore.SERVER_TIMESTAMP
        }

        doc_ref.update(update_data)

        return {
            "success": True,
            "message": "Profile updated successfully",
            "email": email
        }
    except HTTPException:
        raise
    except Exception as e:
        print("UPDATE PROFILE ERROR:", repr(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/staff")
async def get_all_staff(
    admin_user: dict[str, str] = Depends(require_admin)
) -> dict[str, Any]:
    """
    Retrieves all staff accounts.

    This endpoint is restricted to admin users and returns allowedUsers records
    formatted for staff management views.

    Args:
        admin_user: Authenticated admin user resolved from the dependency.

    Returns:
        Dictionary containing success status and a list of staff account records.

    Raises:
        HTTPException: 500 if staff retrieval fails.
    """
    try:
        users_ref = db.collection("allowedUsers")
        docs = users_ref.stream()

        staff_list = []
        for doc in docs:
            data = doc.to_dict()
            staff_list.append({
                "email": doc.id,
                "firstName": data.get("firstName", ""),
                "lastName": data.get("lastName", ""),
                "role": data.get("role", "staff"),
                "active": data.get("active", True),
                "createdAt": data.get("createdAt"),
                "updatedAt": data.get("updatedAt")
            })

        return {
            "success": True,
            "staff": staff_list
        }
    except Exception as e:
        print("GET ALL STAFF ERROR:", repr(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/staff-by-email")
async def get_staff_by_email(
    email: str = Query(...),
    admin_user: dict[str, str] = Depends(require_admin)
) -> dict[str, Any]:
    """
    Retrieves a single staff member by email address.

    This endpoint is restricted to admin users and uses a query parameter
    instead of a path parameter for lookup.

    Args:
        email: Staff email address to retrieve.
        admin_user: Authenticated admin user resolved from the dependency.

    Returns:
        Dictionary containing success status and the requested staff record.

    Raises:
        HTTPException: 404 if the staff member is not found.
        HTTPException: 500 if the lookup fails.
    """
    normalized_email = normalize_email(email)

    print(f"GET STAFF BY EMAIL - Email param: {email}")
    print(f"GET STAFF BY EMAIL - Normalized email: {normalized_email}")

    try:
        doc_ref = db.collection("allowedUsers").document(normalized_email)
        doc = doc_ref.get()

        if not doc.exists:
            print(f"GET STAFF BY EMAIL - Document not found for: {normalized_email}")
            raise HTTPException(status_code=404, detail=f"Staff member not found: {normalized_email}")

        data = doc.to_dict()
        return {
            "success": True,
            "staff": {
                "email": doc.id,
                "firstName": data.get("firstName", ""),
                "lastName": data.get("lastName", ""),
                "role": data.get("role", "staff"),
                "active": data.get("active", True),
                "createdAt": data.get("createdAt"),
                "updatedAt": data.get("updatedAt")
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        print("GET STAFF BY EMAIL ERROR:", repr(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/create-staff")
async def create_staff(
    payload: CreateStaffRequest,
    admin_user: dict[str, str] = Depends(require_admin)
) -> dict[str, Any]:
    """
    Creates a new staff account in Firestore.

    This endpoint is restricted to admin users and creates a new allowedUsers
    document for the provided email address.

    Args:
        payload: Request body containing the new staff member's profile data.
        admin_user: Authenticated admin user resolved from the dependency.

    Returns:
        Dictionary containing success status, message, and the created email.

    Raises:
        HTTPException: 400 if a staff member with the email already exists.
        HTTPException: 500 if creation fails.
    """
    email = normalize_email(str(payload.email))

    try:
        doc_ref = db.collection("allowedUsers").document(email)
        doc = doc_ref.get()

        if doc.exists:
            raise HTTPException(status_code=400, detail="Staff member with this email already exists")

        staff_data = {
            "email": email,
            "firstName": payload.firstName,
            "lastName": payload.lastName,
            "role": "staff",
            "active": payload.active,
            "createdAt": firestore.SERVER_TIMESTAMP,
            "updatedAt": firestore.SERVER_TIMESTAMP
        }

        doc_ref.set(staff_data)

        return {
            "success": True,
            "message": "Staff account created successfully",
            "email": email
        }
    except HTTPException:
        raise
    except Exception as e:
        print("CREATE STAFF ERROR:", repr(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/admin/update-staff")
async def admin_update_staff(
    payload: AdminUpdateStaffRequest,
    admin_user: dict[str, str] = Depends(require_admin)
) -> dict[str, Any]:
    """
    Updates a staff member's profile and optionally their email address.

    Admins may update Firestore profile data and email, but password changes are
    explicitly blocked. If the email changes, the route attempts to update both
    Firebase Auth and Firestore records.

    Args:
        payload: Request body containing the original email, updated profile data,
            and optional password field.
        admin_user: Authenticated admin user resolved from the dependency.

    Returns:
        Dictionary containing success status, message, resulting email, and whether
        the email was changed.

    Raises:
        HTTPException: 400 if the new email already exists.
        HTTPException: 403 if the request attempts to change a password.
        HTTPException: 404 if the original staff record does not exist.
        HTTPException: 500 if the update fails.
    """
    original_email = normalize_email(str(payload.originalEmail))
    new_email = normalize_email(str(payload.email))

    if payload.password is not None and payload.password.strip() != "":
        raise HTTPException(
            status_code=403,
            detail="Admins cannot change user passwords. Password changes must be done by the user."
        )

    try:
        old_doc_ref = db.collection("allowedUsers").document(original_email)
        old_doc = old_doc_ref.get()

        if not old_doc.exists:
            raise HTTPException(status_code=404, detail="Staff member not found")

        email_changed = original_email != new_email

        if email_changed:
            new_doc_ref = db.collection("allowedUsers").document(new_email)
            new_doc = new_doc_ref.get()

            if new_doc.exists:
                raise HTTPException(status_code=400, detail="A user with this email already exists")

            user_data = old_doc.to_dict()

            try:
                user = firebase_auth.get_user_by_email(original_email)
                firebase_auth.update_user(
                    user.uid,
                    email=new_email
                )
                print(f"ADMIN UPDATE - Firebase Auth email updated from {original_email} to {new_email}")
            except Exception as auth_error:
                print(f"ADMIN UPDATE - Firebase Auth error: {repr(auth_error)}")

            user_data.update({
                "email": new_email,
                "firstName": payload.firstName,
                "lastName": payload.lastName,
                "active": payload.active,
                "updatedAt": firestore.SERVER_TIMESTAMP
            })

            new_doc_ref.set(user_data)
            old_doc_ref.delete()

            return {
                "success": True,
                "message": "Staff profile and email updated successfully in both Auth and Firestore",
                "email": new_email,
                "emailChanged": True
            }
        else:
            update_data = {
                "firstName": payload.firstName,
                "lastName": payload.lastName,
                "active": payload.active,
                "updatedAt": firestore.SERVER_TIMESTAMP
            }

            old_doc_ref.update(update_data)

            return {
                "success": True,
                "message": "Staff profile updated successfully",
                "email": original_email,
                "emailChanged": False
            }
    except HTTPException:
        raise
    except Exception as e:
        print("ADMIN UPDATE STAFF ERROR:", repr(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/delete-staff")
def delete_staff(
    payload: DeleteStaffRequest,
    admin_user: dict[str, str] = Depends(require_admin)
) -> dict[str, Any]:
    """
    Deletes a staff account.

    Admin users may delete staff accounts, but cannot delete their own account
    through this route and cannot delete non-staff roles.

    Args:
        payload: Request body containing the email of the staff account to delete.
        admin_user: Authenticated admin user resolved from the dependency.

    Returns:
        Dictionary containing success status, message, and deleted email.

    Raises:
        HTTPException: 400 if the admin attempts to delete their own account.
        HTTPException: 403 if the target account is not a staff account.
        HTTPException: 404 if the staff record does not exist.
        HTTPException: 500 if deletion fails.
    """
    email = normalize_email(str(payload.email))

    doc_ref = db.collection("allowedUsers").document(email)
    doc = doc_ref.get()

    if not doc.exists:
        raise HTTPException(status_code=404, detail="Staff not found")

    data = doc.to_dict()

    if admin_user["email"] == email:
        raise HTTPException(status_code=400, detail="You cannot delete your own account")

    if data.get("role") != "staff":
        raise HTTPException(status_code=403, detail="Only staff accounts can be deleted")

    uid = data.get("uid")

    try:
        if uid:
            firebase_auth.delete_user(uid)

        doc_ref.delete()

        return {
            "success": True,
            "message": "Staff account deleted successfully",
            "email": email
        }

    except Exception as e:
        print("DELETE STAFF ERROR:", repr(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/request-password-reset")
@limiter.limit("5/minute")
def request_password_reset(
    request: Request,
    payload: RequestPasswordResetRequest
) -> dict[str, Any]:
    """
    Sends a password reset verification code to an allowed user.

    This endpoint enforces a cooldown period between requests and stores the
    generated code in Firestore with expiry and verification metadata.

    Args:
        request: Incoming request object used by the rate limiter.
        payload: Request body containing the target email address.

    Returns:
        Dictionary containing success status, message, and remaining cooldown seconds.

    Raises:
        HTTPException: 404 if no active allowed account exists for the email.
    """
    email = normalize_email(str(payload.email))

    allowed_user = get_allowed_user_data(email)
    if not allowed_user:
        raise HTTPException(status_code=404, detail="No active account found for this email")

    doc_ref = db.collection("passwordResetOtps").document(email)
    snap = doc_ref.get()
    now = datetime.now(timezone.utc)

    if snap.exists:
        existing = snap.to_dict()
        last_request_at = existing.get("lastRequestAt")

        if last_request_at:
            elapsed = (now - last_request_at).total_seconds()
            if elapsed < RESET_COOLDOWN_SECONDS:
                remaining = int(RESET_COOLDOWN_SECONDS - elapsed)
                return {
                    "success": False,
                    "message": "Please wait before requesting another code.",
                    "remainingSeconds": max(remaining, 1),
                }

    code = generate_six_digit_code()
    expires_at = now + timedelta(minutes=RESET_CODE_EXPIRES_MINUTES)

    doc_ref.set(
        {
            "email": email,
            "code": code,
            "expiresAt": expires_at,
            "used": False,
            "attemptCount": 0,
            "verified": False,
            "lastRequestAt": now,
            "updatedAt": firestore.SERVER_TIMESTAMP,
        },
        merge=True,
    )

    send_reset_code_email(email, code)

    return {
        "success": True,
        "message": "Verification code sent",
        "remainingSeconds": 0,
    }


@router.post("/verify-reset-code")
def verify_reset_code(payload: VerifyResetCodeRequest) -> dict[str, Any]:
    """
    Verifies a submitted password reset code.

    Args:
        payload: Request body containing the email address and verification code.

    Returns:
        Dictionary containing success status and a confirmation message.

    Raises:
        HTTPException: 400 if the code does not exist, is expired, has already
            been used, exceeds max attempts, or does not match.
    """
    email = normalize_email(str(payload.email))
    code = payload.code.strip()

    doc_ref = db.collection("passwordResetOtps").document(email)
    snap = doc_ref.get()

    if not snap.exists:
        raise HTTPException(status_code=400, detail="No reset code found for this email")

    data = snap.to_dict()
    now = datetime.now(timezone.utc)

    if data.get("used"):
        raise HTTPException(status_code=400, detail="This reset code has already been used")

    expires_at = data.get("expiresAt")
    if not expires_at or expires_at <= now:
        raise HTTPException(status_code=400, detail="Reset code has expired")

    attempt_count = data.get("attemptCount", 0)
    if attempt_count >= RESET_MAX_VERIFY_ATTEMPTS:
        raise HTTPException(status_code=400, detail="Too many invalid attempts. Request a new code")

    if data.get("code") != code:
        doc_ref.set(
            {
                "attemptCount": attempt_count + 1,
                "updatedAt": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )
        raise HTTPException(status_code=400, detail="Invalid verification code")

    doc_ref.set(
        {
            "verified": True,
            "updatedAt": firestore.SERVER_TIMESTAMP,
        },
        merge=True,
    )

    return {
        "success": True,
        "message": "Code verified successfully",
    }


@router.post("/confirm-password-reset")
def confirm_password_reset(payload: ConfirmPasswordResetRequest) -> dict[str, Any]:
    """
    Completes the password reset flow after successful code verification.

    Validates the new password, checks the stored reset request, updates the
    Firebase Auth password, marks the reset code as used, and clears login
    attempt counters.

    Args:
        payload: Request body containing the email, verification code, and new password.

    Returns:
        Dictionary containing success status and a confirmation message.

    Raises:
        HTTPException: 400 if the password is invalid, the code is missing,
            expired, not verified, already used, or incorrect.
        HTTPException: 500 if the password update fails.
    """
    email = normalize_email(str(payload.email))
    code = payload.code.strip()
    new_password = payload.newPassword

    if not is_valid_password(new_password):
        raise HTTPException(
            status_code=400,
            detail="Password must be at least 8 characters and include an uppercase letter, number, and special character",
        )

    doc_ref = db.collection("passwordResetOtps").document(email)
    snap = doc_ref.get()

    if not snap.exists:
        raise HTTPException(status_code=400, detail="No reset request found")

    data = snap.to_dict()
    now = datetime.now(timezone.utc)

    if data.get("used"):
        raise HTTPException(status_code=400, detail="This reset code has already been used")

    if not data.get("verified"):
        raise HTTPException(status_code=400, detail="Verification code has not been verified")

    expires_at = data.get("expiresAt")
    if not expires_at or expires_at <= now:
        raise HTTPException(status_code=400, detail="Reset code has expired")

    if data.get("code") != code:
        raise HTTPException(status_code=400, detail="Invalid verification code")

    try:
        user_record = firebase_auth.get_user_by_email(email)
        firebase_auth.update_user(user_record.uid, password=new_password)

        doc_ref.set(
            {
                "used": True,
                "updatedAt": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )

        login_attempt_ref = db.collection("loginAttempts").document(email)
        login_attempt_ref.set(
            {
                "failedAttempts": 0,
                "lockoutLevel": 0,
                "lockoutUntil": None,
                "updatedAt": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )

        return {
            "success": True,
            "message": "Password reset successful",
        }

    except Exception as e:
        print("CONFIRM PASSWORD RESET ERROR:", repr(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/refresh-session")
def refresh_session(
    payload: RefreshSessionRequest,
    response: Response
) -> dict[str, Any]:
    """
    Refreshes the current session cookie for an active user session.

    Verifies the provided Firebase ID token, checks that the active session has
    not exceeded the idle timeout, issues a new session cookie, and updates the
    stored session activity timestamp.

    Args:
        payload: Request body containing a Firebase ID token.
        response: Outgoing response used to set the refreshed session cookie.

    Returns:
        Dictionary containing success status, message, email, and role.

    Raises:
        HTTPException: 401 if the token is invalid, the session is missing, or
            the session expired due to inactivity.
        HTTPException: 403 if the user is not allowed to access the system.
    """
    try:
        decoded_token = firebase_auth.verify_id_token(payload.idToken)
    except Exception as e:
        print("REFRESH SESSION VERIFY ERROR:", repr(e))
        raise HTTPException(status_code=401, detail=str(e))

    uid = decoded_token.get("uid")
    email = normalize_email(decoded_token.get("email", ""))

    allowed_user = get_allowed_user_data(email)
    if not allowed_user:
        raise HTTPException(status_code=403, detail="User is not allowed")

    session_snap = get_session_doc_ref(uid).get()
    if not session_snap.exists:
        raise HTTPException(status_code=401, detail="Session not found")

    now = datetime.now(timezone.utc)
    session_data = session_snap.to_dict()
    last_activity_at = session_data.get("lastActivityAt")

    if not last_activity_at or (now - last_activity_at).total_seconds() > IDLE_TIMEOUT_SECONDS:
        clear_active_session(uid)
        try:
            firebase_auth.revoke_refresh_tokens(uid)
        except Exception:
            pass
        raise HTTPException(status_code=401, detail="Session expired due to inactivity")

    try:
        session_cookie = firebase_auth.create_session_cookie(
            payload.idToken,
            expires_in=timedelta(seconds=SESSION_EXPIRES_SECONDS)
        )
    except Exception as e:
        print("REFRESH SESSION COOKIE ERROR:", repr(e))
        raise HTTPException(status_code=401, detail=str(e))

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_cookie,
        max_age=SESSION_EXPIRES_SECONDS,
        httponly=True,
        secure=False,
        samesite="lax",
        path="/",
    )

    upsert_active_session(uid, email)

    return {
        "success": True,
        "message": "Session refreshed",
        "email": email,
        "role": allowed_user.get("role", "user"),
    }
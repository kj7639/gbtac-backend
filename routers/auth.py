import os
import httpx
import secrets
import random
import re

from helpers.email_service import send_reset_code_email
from helpers.firebase_admin_setup import get_firestore_client, get_firebase_auth
from helpers.auth_dependencies import SESSION_COOKIE_NAME, normalize_email, get_allowed_user_data, require_admin, get_current_user_from_session

from fastapi import APIRouter, Request, HTTPException, Response, Cookie, Depends, Query
from pydantic import BaseModel, EmailStr
from helpers.rate_limit import limiter

from datetime import datetime, timedelta, timezone
from firebase_admin import firestore

from typing import Optional

router = APIRouter(prefix="/auth")

# initialize Firebase Admin and Firestore client

db = get_firestore_client()
firebase_auth = get_firebase_auth()
TURNSTILE_SECRET_KEY = os.getenv("TURNSTILE_SECRET_KEY")

# DATA MODELS

class ResetRequest(BaseModel):
    email: EmailStr

class EmailRequest(BaseModel):
    email: str

class TokenRequest(BaseModel):
    idToken: str

class CaptchaRequest(BaseModel):
    captcha_token: str

class CreateStaffRequest(BaseModel):
    firstName: str
    lastName: str
    email: EmailStr
    active: bool

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

# CONFIGURATION

MAX_FAILED_ATTEMPTS = 2  # change to 5 later
RESET_COOLDOWN_SECONDS = 60  
SESSION_EXPIRES_SECONDS = 60 * 60 * 8  # 8 hours fixed session duration for simplicity, can be changed to refresh tokens later
RESET_CODE_EXPIRES_MINUTES = 10
RESET_MAX_VERIFY_ATTEMPTS = 5

# HELPER FUNCTIONS

def get_lockout_duration_seconds(level: int) -> int:
    # TEST VALUES
    if level == 1:
        return 10
    elif level == 2:
        return 20
    elif level == 3:
        return 30
    return 40

# REAL VALUES LATER:
# def get_lockout_duration_seconds(level: int) -> int:
#     if level == 1:
#         return 60
#     elif level == 2:
#         return 300
#     elif level == 3:
#         return 900
#     return 1800

def generate_six_digit_code() -> str:
    return f"{random.randint(0, 999999):06d}"


def is_valid_password(password: str) -> bool:
    return (
        len(password) >= 8
        and re.search(r"[A-Z]", password)
        and re.search(r"[0-9]", password)
        and re.search(r"[!@#$%^&*]", password)
    )

# ENDPOINTS

# CAPTCHA verification endpoint for frontend to call before allowing password reset or login attempts
@router.post("/verify-captcha")
async def verify_captcha(payload: CaptchaRequest, request: Request):
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

# Endpoints for tracking login attempts and lockout status
@router.post("/check-lockout")
def check_lockout(payload: EmailRequest):
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

    # expired -> reset attempts
    doc_ref.set(
        {
            "failedAttempts": 0,
            "lockoutUntil": None,
            "updatedAt": firestore.SERVER_TIMESTAMP,
        },
        merge=True,
    )

    return {"locked": False, "remainingSeconds": 0}

# Endpoint to record failed login attempts and apply lockout if necessary
@router.post("/record-failed-login")
def record_failed_login(payload: EmailRequest):
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

# Endpoint to reset login attempts after successful login or password reset
@router.post("/reset-login-attempts")
def reset_login_attempts(payload: EmailRequest):
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

# Endpoint to check if user is allowed (exists in allowedUsers collection and active) based on Firebase ID token
@router.post("/check-allowed-user")
def check_allowed_user(payload: TokenRequest):
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

# Endpoint to create session cookie after successful login on frontend and return user info
@router.post("/session-login")
def session_login(payload: TokenRequest, response: Response):
    try:
        decoded_token = firebase_auth.verify_id_token(payload.idToken)
    except Exception as e:
        print("SESSION LOGIN VERIFY ERROR:", repr(e))
        raise HTTPException(status_code=401, detail=str(e))

    email = normalize_email(decoded_token.get("email", ""))
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

    return {
        "success": True,
        "message": "Session created",
        "email": email,
        "role": allowed_user.get("role", "user"),
    }

# Endpoint to get current user info based on session cookie, used for frontend to check if user is logged in and get their role
@router.get("/me")
async def get_current_user_me(current_user=Depends(get_current_user_from_session)):
    return {
        "uid": current_user["uid"],
        "email": current_user["email"],
        "role": current_user["role"],
    }

# Endpoint to log out by clearing session cookie and revoking Firebase refresh tokens so that existing session cookies become invalid immediately
@router.post("/logout")
def logout(
    response: Response,
    session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME)
):
    if session:
        try:
            decoded_claims = firebase_auth.verify_session_cookie(session, check_revoked=True)
            firebase_auth.revoke_refresh_tokens(decoded_claims["uid"])
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

# Endpoint to update user email in Firestore (for email verification flow)
class UpdateEmailRequest(BaseModel):
    oldEmail: EmailStr
    newEmail: EmailStr

@router.post("/update-email")
def update_email(payload: UpdateEmailRequest):
    old_email = normalize_email(str(payload.oldEmail))
    new_email = normalize_email(str(payload.newEmail))
    
    try:
        # Get old document
        old_doc_ref = db.collection("allowedUsers").document(old_email)
        old_doc = old_doc_ref.get()
        
        if not old_doc.exists:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Get user data
        user_data = old_doc.to_dict()
        
        # Create new document with new email
        new_doc_ref = db.collection("allowedUsers").document(new_email)
        user_data["email"] = new_email
        user_data["updatedAt"] = firestore.SERVER_TIMESTAMP
        new_doc_ref.set(user_data)
        
        # Delete old document
        old_doc_ref.delete()
        
        return {
            "success": True,
            "message": "Email updated successfully",
            "oldEmail": old_email,
            "newEmail": new_email
        }
    except Exception as e:
        print("UPDATE EMAIL ERROR:", repr(e))
        raise HTTPException(status_code=500, detail=str(e))

# Endpoint to update user profile (firstName, lastName, status)
class UpdateProfileRequest(BaseModel):
    email: EmailStr
    firstName: str
    lastName: str
    active: bool

@router.post("/update-profile")
def update_profile(payload: UpdateProfileRequest):
    email = normalize_email(str(payload.email))
    
    try:
        doc_ref = db.collection("allowedUsers").document(email)
        doc = doc_ref.get()
        
        if not doc.exists:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Update user data
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
    except Exception as e:
        print("UPDATE PROFILE ERROR:", repr(e))
        raise HTTPException(status_code=500, detail=str(e))

# Endpoint to get all staff accounts (admin only)
@router.get("/staff")
async def get_all_staff(admin_user=Depends(require_admin)):
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

# Endpoint to get a single staff member by email (admin only) - using query parameter
@router.get("/staff-by-email")
async def get_staff_by_email(email: str = Query(...), admin_user=Depends(require_admin)):
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

# Endpoint to create a new staff account (admin only)
class CreateStaffRequest(BaseModel):
    email: EmailStr
    firstName: str
    lastName: str
    active: bool = True

@router.post("/create-staff")
async def create_staff(payload: CreateStaffRequest, admin_user=Depends(require_admin)):
    email = normalize_email(str(payload.email))
    
    try:
        # Check if user already exists
        doc_ref = db.collection("allowedUsers").document(email)
        doc = doc_ref.get()
        
        if doc.exists:
            raise HTTPException(status_code=400, detail="Staff member with this email already exists")
        
        # Create new staff member
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

# Endpoint for admin to update staff profile (with password restriction)
class AdminUpdateStaffRequest(BaseModel):
    originalEmail: EmailStr
    email: EmailStr
    firstName: str
    lastName: str
    active: bool
    password: Optional[str] = None

@router.post("/admin/update-staff")
async def admin_update_staff(payload: AdminUpdateStaffRequest, admin_user=Depends(require_admin)):
    original_email = normalize_email(str(payload.originalEmail))
    new_email = normalize_email(str(payload.email))
    
    # Block password changes from admin
    if payload.password is not None and payload.password.strip() != "":
        raise HTTPException(
            status_code=403, 
            detail="Admins cannot change user passwords. Password changes must be done by the user."
        )
    
    try:
        # Get the original document
        old_doc_ref = db.collection("allowedUsers").document(original_email)
        old_doc = old_doc_ref.get()
        
        if not old_doc.exists:
            raise HTTPException(status_code=404, detail="Staff member not found")
        
        # Check if email is being changed
        email_changed = original_email != new_email
        
        if email_changed:
            # Check if new email already exists in Firestore
            new_doc_ref = db.collection("allowedUsers").document(new_email)
            new_doc = new_doc_ref.get()
            
            if new_doc.exists:
                raise HTTPException(status_code=400, detail="A user with this email already exists")
            
            # Get existing user data
            user_data = old_doc.to_dict()
            
            # Update Firebase Auth user email
            try:
                # Get user by email from Firebase Auth
                user = firebase_auth.get_user_by_email(original_email)
                
                # Update the email in Firebase Auth
                firebase_auth.update_user(
                    user.uid,
                    email=new_email
                )
                
                print(f"ADMIN UPDATE - Firebase Auth email updated from {original_email} to {new_email}")
            except Exception as auth_error:
                print(f"ADMIN UPDATE - Firebase Auth error: {repr(auth_error)}")
                # If user doesn't exist in Firebase Auth, that's okay - they might not have logged in yet
                # Continue with Firestore update
            
            # Update with new values
            user_data.update({
                "email": new_email,
                "firstName": payload.firstName,
                "lastName": payload.lastName,
                "active": payload.active,
                "updatedAt": firestore.SERVER_TIMESTAMP
            })
            
            # Create new document with new email
            new_doc_ref.set(user_data)
            
            # Delete old document
            old_doc_ref.delete()
            
            return {
                "success": True,
                "message": "Staff profile and email updated successfully in both Auth and Firestore",
                "email": new_email,
                "emailChanged": True
            }
        else:
            # Email not changed, just update the existing document
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
    admin_user=Depends(require_admin)
):
    email = normalize_email(str(payload.email))

    doc_ref = db.collection("allowedUsers").document(email)
    doc = doc_ref.get()

    if not doc.exists:
        raise HTTPException(status_code=404, detail="Staff not found")

    data = doc.to_dict()

    if admin_user["email"] == email:
        raise HTTPException(status_code=400, detail="You cannot delete your own account")

    # optional safety check (only delete staff, not admins)
    if data.get("role") != "staff":
        raise HTTPException(status_code=403, detail="Only staff accounts can be deleted")

    uid = data.get("uid")

    try:
        # delete from Firebase Auth
        if uid:
            firebase_auth.delete_user(uid)

        # delete from Firestore
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
def request_password_reset(request: Request, payload: RequestPasswordResetRequest):
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
def verify_reset_code(payload: VerifyResetCodeRequest):
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
def confirm_password_reset(payload: ConfirmPasswordResetRequest):
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
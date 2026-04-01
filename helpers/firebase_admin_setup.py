"""
firebase_admin_setup.py

Initializes the Firebase Admin SDK using a service account JSON file and
provides accessor functions for Firebase Auth and Firestore clients. Used
by the auth endpoints that support Edit Staff Profile and Edit Staff (admin).

Author: Dominique Anne Lee
"""

import os
import firebase_admin
from firebase_admin import credentials, auth, firestore
from dotenv import load_dotenv

load_dotenv()

def initialize_firebase_admin():
    if not firebase_admin._apps:
        service_account_path = os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH")

        if not service_account_path:
            raise ValueError("FIREBASE_SERVICE_ACCOUNT_PATH is not set")

        print("Using Firebase service account path:", service_account_path)

        cred = credentials.Certificate(service_account_path)
        firebase_admin.initialize_app(cred)

    app = firebase_admin.get_app()
    print("Firebase Admin project:", app.project_id)
    return app

def get_firebase_auth():
    initialize_firebase_admin()
    return auth

def get_firestore_client():
    initialize_firebase_admin()
    return firestore.client()

def generate_password_reset_link(email: str) -> str:
    initialize_firebase_admin()
    return auth.generate_password_reset_link(email)
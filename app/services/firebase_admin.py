import os
import json
import base64
from typing import Optional

import firebase_admin
from firebase_admin import credentials, auth as firebase_auth, firestore as firebase_firestore, storage as firebase_storage


def _load_credentials() -> credentials.Certificate:
    """
    Load Firebase credentials using one of the following envs (in order):
    - FIREBASE_CREDENTIALS_JSON: raw JSON or base64-encoded JSON service account
    - FIREBASE_CREDENTIALS_BASE64: base64-encoded JSON service account
    - FIREBASE_CREDENTIALS_PATH: absolute/relative path to service account json
    - Fallback: project root file 'firebase-admin.json'
    """
    # 1) Direct JSON (may be base64 encoded)
    raw_json = os.getenv("FIREBASE_CREDENTIALS_JSON")
    if raw_json:
        try:
            # Try parse as JSON directly
            data = json.loads(raw_json)
            return credentials.Certificate(data)
        except json.JSONDecodeError:
            # Try base64 decode then parse
            try:
                decoded = base64.b64decode(raw_json).decode("utf-8")
                data = json.loads(decoded)
                return credentials.Certificate(data)
            except Exception as e:
                raise RuntimeError(f"Invalid FIREBASE_CREDENTIALS_JSON: {e}")

    # 2) Explicit base64 env
    b64_json = os.getenv("FIREBASE_CREDENTIALS_BASE64")
    if b64_json:
        try:
            decoded = base64.b64decode(b64_json).decode("utf-8")
            data = json.loads(decoded)
            return credentials.Certificate(data)
        except Exception as e:
            raise RuntimeError(f"Invalid FIREBASE_CREDENTIALS_BASE64: {e}")

    # 3) Path from env
    path = os.getenv("FIREBASE_CREDENTIALS_PATH")
    if path and os.path.exists(path):
        return credentials.Certificate(path)

    # 4) Default file at project root
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    default_path = os.path.join(project_root, "firebase-admin.json")
    if os.path.exists(default_path):
        return credentials.Certificate(default_path)

    raise RuntimeError("Firebase credentials not provided. Set FIREBASE_CREDENTIALS_JSON (or _BASE64) or FIREBASE_CREDENTIALS_PATH, or place firebase-admin.json at project root.")


def init_firebase() -> None:
    """Initialize Firebase Admin SDK if not already initialized."""
    if firebase_admin._apps:
        return

    cred = _load_credentials()
    options = {}
    storage_bucket = os.getenv("FIREBASE_STORAGE_BUCKET")
    if storage_bucket:
        options["storageBucket"] = storage_bucket

    firebase_admin.initialize_app(cred, options or None)


# Convenience getters

def get_auth():
    if not firebase_admin._apps:
        init_firebase()
    return firebase_auth


def get_firestore():
    if not firebase_admin._apps:
        init_firebase()
    return firebase_firestore.client()


def get_bucket():
    if not firebase_admin._apps:
        init_firebase()
    return firebase_storage.bucket()

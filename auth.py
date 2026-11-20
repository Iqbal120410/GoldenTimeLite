import hashlib
import hmac
import os
import re
import shutil
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path


ROOT_PATH = Path(__file__).resolve().parent
AUTH_DB_PATH = ROOT_PATH / "accounts.db"
USER_DATA_DIR = ROOT_PATH / "user_data"
PASSWORD_ITERATIONS = 310_000


def initialize_auth_db():
    with closing(sqlite3.connect(AUTH_DB_PATH)) as connection:
        with connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_salt BLOB NOT NULL,
                    password_hash BLOB NOT NULL,
                    display_name TEXT NOT NULL
                )
                """
            )


def validate_account_input(email, password, display_name=""):
    normalized_email = email.strip().casefold()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized_email):
        return None, "Masukkan alamat email yang valid."
    if len(password) < 10:
        return None, "Kata sandi harus memiliki minimal 10 karakter."
    if not display_name.strip():
        return None, "Nama panggilan wajib diisi."
    return normalized_email, None


def create_account(email, password, display_name):
    normalized_email, error = validate_account_input(email, password, display_name)
    if error:
        return None, error

    salt = os.urandom(16)
    password_hash = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )
    account_id = uuid.uuid4().hex

    initialize_auth_db()
    try:
        with closing(sqlite3.connect(AUTH_DB_PATH)) as connection:
            with connection:
                is_first_account = connection.execute(
                    "SELECT 1 FROM accounts LIMIT 1"
                ).fetchone() is None
                connection.execute(
                    """
                    INSERT INTO accounts (id, email, password_salt, password_hash, display_name)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (account_id, normalized_email, salt, password_hash, display_name.strip()),
                )
    except sqlite3.IntegrityError:
        return None, "Email tersebut sudah terdaftar di perangkat ini."

    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    if is_first_account:
        legacy_path = ROOT_PATH / "data.json"
        if legacy_path.exists():
            shutil.copy2(legacy_path, USER_DATA_DIR / f"{account_id}.json")
    return {
        "id": account_id,
        "email": normalized_email,
        "display_name": display_name.strip(),
    }, None


def authenticate_account(email, password):
    normalized_email = email.strip().casefold()
    initialize_auth_db()
    with closing(sqlite3.connect(AUTH_DB_PATH)) as connection:
        row = connection.execute(
            """
            SELECT id, email, password_salt, password_hash, display_name
            FROM accounts WHERE email = ? COLLATE NOCASE
            """,
            (normalized_email,),
        ).fetchone()

    if row is None:
        return None
    account_id, stored_email, salt, expected_hash, display_name = row
    actual_hash = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )
    if not hmac.compare_digest(actual_hash, expected_hash):
        return None
    return {"id": account_id, "email": stored_email, "display_name": display_name}


def get_account_data_path(account_id):
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    return USER_DATA_DIR / f"{account_id}.json"

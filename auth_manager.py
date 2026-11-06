import os
import sys
import time
import json
import shutil
import hmac
import hashlib
import base64
import secrets
import sqlite3
import bcrypt
from typing import Dict, Any, Optional, Tuple, List

sys.path.append(os.path.dirname(__file__))
from project_db import ProjectDatabase
from job_manager import ProjectJobManager
from exporter import ProjectExporter

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_ENV = os.getenv("APP_ENV", "production").lower()
IS_TESTING = APP_ENV in ("testing", "test", "staging")

AUTH_DB_PATH = os.path.join(BASE_DIR, "auth_test.db" if IS_TESTING else "auth.db")
USER_DATA_ROOT = os.path.join(BASE_DIR, "user_data_test" if IS_TESTING else "user_data")
SECRET_KEY_PATH = os.path.join(BASE_DIR, ".jwt_secret_test" if IS_TESTING else ".jwt_secret")
LEGACY_PROJECTS_DB = os.path.join(BASE_DIR, "projects.db")

def get_or_create_secret_key() -> bytes:
    env_key = os.getenv("JWT_SECRET_KEY")
    if env_key:
        return env_key.encode('utf-8')
    if os.path.exists(SECRET_KEY_PATH):
        try:
            with open(SECRET_KEY_PATH, "rb") as f:
                key = f.read().strip()
                if len(key) >= 32:
                    return key
        except Exception:
            pass
    # Stable deterministic fallback key for cloud ephemeral environments
    # Ensures container restarts and cold-boot wakeups on Render NEVER invalidate user logins
    stable_default = b"storehub_cloud_jwt_signing_key_testing_2025_sec" if IS_TESTING else b"storehub_cloud_jwt_signing_key_production_2025_vault_sec"
    try:
        with open(SECRET_KEY_PATH, "wb") as f:
            f.write(stable_default)
    except Exception:
        pass
    return stable_default

JWT_SECRET = get_or_create_secret_key()

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode('utf-8').rstrip('=')

def _b64url_decode(s: str) -> bytes:
    padding = '=' * (4 - (len(s) % 4))
    return base64.urlsafe_b64decode(s + padding)

def create_jwt_token(payload: Dict[str, Any], expires_in_seconds: int = 30 * 86400) -> str:
    """Generate an RFC 7519 compliant HMAC-SHA256 JWT token."""
    header = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    payload = dict(payload)
    payload["iat"] = now
    payload["exp"] = now + expires_in_seconds
    
    header_b64 = _b64url_encode(json.dumps(header, separators=(',', ':')).encode('utf-8'))
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(',', ':')).encode('utf-8'))
    
    signing_input = f"{header_b64}.{payload_b64}".encode('utf-8')
    signature = hmac.new(JWT_SECRET, signing_input, hashlib.sha256).digest()
    sig_b64 = _b64url_encode(signature)
    
    return f"{header_b64}.{payload_b64}.{sig_b64}"

def decode_jwt_token(token: str) -> Optional[Dict[str, Any]]:
    """Decode and cryptographically verify a JWT token."""
    try:
        parts = token.strip().split('.')
        if len(parts) != 3:
            return None
        header_b64, payload_b64, sig_b64 = parts
        
        signing_input = f"{header_b64}.{payload_b64}".encode('utf-8')
        expected_sig = hmac.new(JWT_SECRET, signing_input, hashlib.sha256).digest()
        actual_sig = _b64url_decode(sig_b64)
        
        if not hmac.compare_digest(expected_sig, actual_sig):
            return None
        
        payload_bytes = _b64url_decode(payload_b64)
        payload = json.loads(payload_bytes.decode('utf-8'))
        
        if payload.get("exp", 0) < int(time.time()):
            return None # Expired
            
        return payload
    except Exception:
        return None

class AuthManager:
    def __init__(self, db_path: str = AUTH_DB_PATH, user_data_root: str = USER_DATA_ROOT):
        self.db_path = db_path
        self.user_data_root = user_data_root
        os.makedirs(self.user_data_root, exist_ok=True)
        self._init_auth_db()
        self._seed_demo_accounts()
        
        # In-memory singletons per user to reuse DB connection pools and job managers
        self._user_dbs: Dict[str, ProjectDatabase] = {}
        self._user_job_mgrs: Dict[str, ProjectJobManager] = {}
        self._user_exporters: Dict[str, ProjectExporter] = {}

    def is_testing_mode(self) -> bool:
        return IS_TESTING

    def get_environment_name(self) -> str:
        return APP_ENV

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _init_auth_db(self):
        """Initializes the authentication database. Contains ONLY user credentials, zero customer data."""
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT DEFAULT 'customer',
                    created_at REAL NOT NULL,
                    last_login REAL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")
            conn.commit()

    def _seed_demo_accounts(self):
        """Seeds two demo customer accounts so the user can immediately test isolation with 1 click."""
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) as cnt FROM users")
            if cur.fetchone()["cnt"] > 0:
                return

        print("[AUTH] Seeding initial test customer accounts...")
        # Customer 1: Apex Retailers
        ok1, _, u1, _ = self.register_user("Apex Retailers", "customer1@storehub.io", "Password123!", user_id="usr_customer_1")
        # Customer 2: Nova Brands
        ok2, _, u2, _ = self.register_user("Nova Brands", "customer2@storehub.io", "Password123!", user_id="usr_customer_2")

        # Copy legacy projects.db to customer 1 so customer 1 has existing sample projects
        if ok1 and u1 and os.path.exists(LEGACY_PROJECTS_DB):
            cust1_dir = self.get_user_storage_path(u1["id"])
            dest = os.path.join(cust1_dir, "projects.db")
            if not os.path.exists(dest):
                try:
                    shutil.copy2(LEGACY_PROJECTS_DB, dest)
                    print(f"[AUTH] Migrated existing projects.db to Customer 1 ({u1['id']})")
                except Exception as e:
                    print(f"[AUTH] Legacy migration note: {e}")

    def register_user(self, name: str, email: str, password: str, user_id: Optional[str] = None) -> Tuple[bool, str, Optional[Dict[str, Any]], Optional[str]]:
        """Register a new customer account with secure bcrypt password hashing."""
        email = email.strip().lower()
        name = name.strip()
        if not email or "@" not in email:
            return False, "Invalid email address.", None, None
        if len(password) < 6:
            return False, "Password must be at least 6 characters.", None, None
        if not name:
            name = email.split("@")[0].title()

        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM users WHERE email = ?", (email,))
            if cur.fetchone():
                return False, "An account with this email already exists.", None, None

            uid = user_id or f"usr_{secrets.token_hex(8)}"
            salt = bcrypt.gensalt(12)
            pw_hash = bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')
            now = time.time()

            cur.execute("""
                INSERT INTO users (id, email, name, password_hash, role, created_at, last_login)
                VALUES (?, ?, ?, ?, 'customer', ?, ?)
            """, (uid, email, name, pw_hash, now, now))
            conn.commit()

        # Create isolated local storage directory for this user
        user_dir = self.get_user_storage_path(uid)
        os.makedirs(user_dir, exist_ok=True)

        user_data = {
            "id": uid,
            "email": email,
            "name": name,
            "role": "customer",
            "created_at": now
        }
        token = create_jwt_token({"sub": uid, "email": email, "name": name})
        return True, "Account created successfully.", user_data, token

    def authenticate_user(self, email: str, password: str) -> Tuple[bool, str, Optional[Dict[str, Any]], Optional[str]]:
        """Authenticate user credentials and issue a signed session token."""
        email = email.strip().lower()
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, email, name, password_hash, role, created_at FROM users WHERE email = ?", (email,))
            row = cur.fetchone()
            if not row:
                return False, "Invalid email or password.", None, None

            pw_hash_stored = row["password_hash"].encode('utf-8')
            if not bcrypt.checkpw(password.encode('utf-8'), pw_hash_stored):
                return False, "Invalid email or password.", None, None

            user_id = row["id"]
            now = time.time()
            cur.execute("UPDATE users SET last_login = ? WHERE id = ?", (now, user_id))
            conn.commit()

            user_data = {
                "id": user_id,
                "email": row["email"],
                "name": row["name"],
                "role": row["role"],
                "created_at": row["created_at"]
            }
            token = create_jwt_token({"sub": user_id, "email": row["email"], "name": row["name"]})
            return True, "Authenticated successfully.", user_data, token

    def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, email, name, role, created_at, last_login FROM users WHERE id = ?", (user_id,))
            row = cur.fetchone()
            if not row:
                return None
            return dict(row)

    def restore_verified_user(self, user_id: str, email: str, name: str) -> Dict[str, Any]:
        """Restores a cryptographically verified user into the database if restarted on an ephemeral container."""
        email = email.strip().lower()
        name = name.strip() or email.split("@")[0].title()
        now = time.time()
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT OR IGNORE INTO users (id, email, name, password_hash, role, created_at, last_login)
                VALUES (?, ?, ?, '', 'customer', ?, ?)
            """, (user_id, email, name, now, now))
            conn.commit()
            
        user_dir = self.get_user_storage_path(user_id)
        os.makedirs(user_dir, exist_ok=True)
        return {
            "id": user_id,
            "email": email,
            "name": name,
            "role": "customer",
            "created_at": now
        }

    def get_all_users_summary(self) -> List[Dict[str, Any]]:
        """Returns non-sensitive summary of accounts for demo switcher."""
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, email, name, role, created_at, last_login FROM users ORDER BY created_at ASC")
            return [dict(r) for r in cur.fetchall()]

    def get_user_storage_path(self, user_id: str) -> str:
        """Returns the isolated private disk directory for this user."""
        user_path = os.path.join(self.user_data_root, user_id)
        os.makedirs(user_path, exist_ok=True)
        return user_path

    def get_user_db(self, user_id: str) -> ProjectDatabase:
        """
        Dynamically returns an isolated ProjectDatabase instance for this specific customer.
        Each customer's data lives in its OWN separate database file on local storage:
        'studio/user_data/{user_id}/projects.db'
        0% chance of data contamination between customers!
        """
        if user_id not in self._user_dbs:
            user_dir = self.get_user_storage_path(user_id)
            db_path = os.path.join(user_dir, "projects.db")
            self._user_dbs[user_id] = ProjectDatabase(db_path=db_path)
        return self._user_dbs[user_id]

    def get_user_job_manager(self, user_id: str) -> ProjectJobManager:
        """Returns the isolated ProjectJobManager bound to this customer's private database."""
        if user_id not in self._user_job_mgrs:
            user_db = self.get_user_db(user_id)
            self._user_job_mgrs[user_id] = ProjectJobManager(user_db)
        return self._user_job_mgrs[user_id]

    def get_user_exporter(self, user_id: str) -> ProjectExporter:
        """Returns the isolated ProjectExporter bound to this customer's private database."""
        if user_id not in self._user_exporters:
            user_db = self.get_user_db(user_id)
            self._user_exporters[user_id] = ProjectExporter(user_db)
        return self._user_exporters[user_id]

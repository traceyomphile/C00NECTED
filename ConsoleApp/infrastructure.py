import sqlite3
import fakeredis
import threading
import hashlib
import hmac
import os

DB_FILE = "arcp.db"

# Global lock for write safety
db_lock = threading.Lock()

# ------- SQLITE WITH CONCURRENCY ----------
def get_connection(db_file: str = DB_FILE) -> sqlite3.Connection:
    # Intialise database to wait for 15 seconds if db locked by another write
    conn = sqlite3.connect(
        db_file, timeout=15,
        check_same_thread=False     # Allow access across threads
    )

    conn.row_factory = sqlite3.Row

    # Enable WAL mode (best for concurrent read/write)
    conn.execute("PRAGMA journal_mode=WAL;")

    # Wait before throwing "database is locked"
    conn.execute("PRAGMA busy_timeout=15000;")

    return conn

def get_db() -> sqlite3.Connection:
    return get_connection()

# ------- REDIS ----------
_fake_server = fakeredis.FakeServer()
redis_client = fakeredis.FakeRedis(
    server=_fake_server,
    decode_responses=True
)

# --------------- PASSWORD HASHING ---------------
def hash_password(password: str) -> str:
    """
    Hashes a plainttext password with SHA-256 and a fresh 32-byte random salt.
    Returns a "salt_hex:hash_hex" string for storage in the database.
    A new salt is generated on every call, so two users with the same password 
    will produce a different hash each time.
    Parameters:
        - password : A string representing the password to hash.
    Returns:
        - A "salt_hex:hash_hex" string.
    """
    salt = os.urandom(16)
    digest = hashlib.sha256(salt + password.encode('utf-8')).hexdigest()
    return f"{salt.hex()}:{digest}"

def verify_password(password: str, stored: str) -> bool:
    """
    Checks if the given password matches the stored "salt_hex:hash_hex" value.
    Uses hmac.compare_digest for a timing-safe comparison so the check takes constant time.
    Parameters:
        - password : A string representing plaintext password
        - stored : A string representing the stored encrypted password.
    Returns:
        - True of password == stored "salt_hex:hash_hex"
    """

    try:
        salt_hex, expected_digest = stored.split(":", 1)
        salt = bytes.fromhex(salt_hex)
        actual_digest = hashlib.sha256(salt + password.encode("utf-8")).hexdigest()
        return hmac.compare_digest(actual_digest, expected_digest)
    except Exception:
        return False
    
def initialise_database():
    conn = None

    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            last_seen DATETIME
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT UNIQUE NOT NULL
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS group_members (
            group_id TEXT NOT NULL,
            username TEXT NOT NULL,
            PRIMARY KEY (group_id, username),
            FOREIGN KEY(group_id) REFERENCES groups(group_id),
            FOREIGN KEY(username) REFERENCES users(username)
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS media (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender TEXT NOT NULL,
            recipient TEXT,
            group_id TEXT,
            filename TEXT NOT NULL,
            filetype TEXT,
            data BLOB NOT NULL,
            uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(sender) REFERENCES users(username)
        )
        """)

        conn.commit()
    
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"Failed to initialize database: {e}")

    finally:
        if conn:
           conn.close()
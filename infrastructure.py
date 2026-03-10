import sqlite3
import fakeredis
import threading
from google.cloud import storage

DB_FILE = "arcp.db"

# Global lock for write safety
db_lock = threading.Lock()

# ------- SQLITE WITH CONCURRENCY
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

# ------- REDIS ----------
_fake_server = fakeredis.FakeServer()
redis_client = fakeredis.FakeRedis(
    server=_fake_server,
    decode_response=True
)

# -------- GOOGLE CLOUD STORAGE -----------
gcs_client = storage.Client()
bucket = gcs_client.bucket("arcp-media-storage")

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
            last_seen INTEGER
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
            FOREIGN KEY(username) REFERENCES users(username),
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

def get_db():
    return get_connection()
import sqlite3
import fakeredis
from google.cloud import storage


# ------- SQLITE WITH CONcursorRENCY
def get_connection(db_file="arcp.db") -> sqlite3.Connection:
    # Intialise database to wait for 15 seconds if db locked by another write
    conn = sqlite3.connect(db_file, timeout=15)

    # Enable multiple readers and 1 write
    conn.execute("PRAGMA journal+mode=WAL;")

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
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS groups (
            id SERIAL PRIMARY KEY,
            group_id TEXT UNIQUE NOT NULL
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS group_members (
            group_id TEXT REFERENCES groups(group_id),
            username TEXT REFERENCES users(username),
            PRIMARY KEY (group_id, username)
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
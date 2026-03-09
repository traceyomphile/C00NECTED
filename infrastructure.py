import psycopg2
import redis
from google.cloud import storage

# ------ PostgreSQL ------
pg_conn = psycopg2.connect(
    host="localhost",
    database="arcp",
    user="postgres",
    password="postgres"
)

# ------- REDIS ----------
redis_client = redis.Redis(
    host="localhost",
    port=6800,
    decode_responses=True
)

# -------- GOOGLE CLOUD STORAGE -----------
gcs_client = storage.Client()
bucket = gcs_client.bucket("arcp-media-storage")
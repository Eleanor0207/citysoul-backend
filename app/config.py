import os
from google.cloud import secretmanager

PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "citysoul")

def get_secret(secret_id: str) -> str:
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{PROJECT_ID}/secrets/{secret_id}/versions/latest"
    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("UTF-8")


DB_PASSWORD = get_secret("db-password")

DB_INSTANCE_CONNECTION_NAME = "citysoul:asia-east1:citysoul"
DB_USER = "postgres"
DB_NAME = "citysoul"

GCS_BUCKET_NAME = "citysoul-images"
GCP_PROJECT_ID = "citysoul"
GCP_LOCATION = "global"
SCHEDULER_SECRET = get_secret("scheduler-secret")
GEMINI_API_KEY = get_secret("gemini-api-key")

GOOGLE_OAUTH_CLIENT_ID = get_secret("google-oauth-client-id")
JWT_SECRET = get_secret("jwt-secret")

VAPID_PUBLIC_KEY = get_secret("vapid-public-key")
VAPID_PRIVATE_KEY = get_secret("vapid-private-key")


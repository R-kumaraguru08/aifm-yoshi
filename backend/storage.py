import os
from datetime import date, datetime
from azure.storage.blob import BlobServiceClient, ContentSettings
from azure.cosmos import CosmosClient, exceptions
from dotenv import load_dotenv

load_dotenv("../.env")

# =============================================
# ☁️ AZURE BLOB
# =============================================
blob_service     = BlobServiceClient.from_connection_string(
    os.getenv("AZURE_STORAGE_CONNECTION")
)
STORAGE_ACCOUNT  = os.getenv("AZURE_STORAGE_ACCOUNT")
VOICES_CONTAINER = "voices"
INTROS_CONTAINER = "intros"

def upload_blob(data: bytes, filename: str, container: str, content_type: str) -> str:
    try:
        client = blob_service.get_blob_client(container=container, blob=filename)
        client.upload_blob(
            data, overwrite=True,
            content_settings=ContentSettings(content_type=content_type)
        )
        url = f"https://{STORAGE_ACCOUNT}.blob.core.windows.net/{container}/{filename}"
        print(f"☁️ Uploaded: {url}")
        return url
    except Exception as e:
        print(f"Blob error: {e}")
        return ""

def delete_day_blobs(day: str):
    deleted = 0
    try:
        for container in [VOICES_CONTAINER, INTROS_CONTAINER]:
            c     = blob_service.get_container_client(container)
            blobs = list(c.list_blobs())
            for blob in blobs:
                if day in blob.name:
                    c.delete_blob(blob.name)
                    deleted += 1
                    print(f"🗑️ Deleted: {blob.name}")
        print(f"🗑️ Total deleted: {deleted} blobs for {day}")
    except Exception as e:
        print(f"Delete error: {e}")

# =============================================
# 🌌 COSMOS DB
# =============================================
cosmos_client   = CosmosClient.from_connection_string(
    os.getenv("COSMOS_CONNECTION")
)
cosmos_db       = cosmos_client.get_database_client(
    os.getenv("COSMOS_DATABASE", "aifm")
)
shows_container = cosmos_db.get_container_client("daily_shows")

def today() -> str:
    return date.today().isoformat()

def load_today_show() -> dict:
    try:
        return shows_container.read_item(item=today(), partition_key=today())
    except exceptions.CosmosResourceNotFoundError:
        new = {
            "id":          today(),
            "date":        today(),
            "submissions": [],
            "total":       0,
            "show_played": False,
            "created_at":  datetime.now().isoformat()
        }
        shows_container.create_item(new)
        return new

def save_today_show(show: dict):
    shows_container.upsert_item(show)

def get_history(limit: int = 30) -> list:
    try:
        return list(shows_container.query_items(
            query="SELECT * FROM c ORDER BY c.date DESC OFFSET 0 LIMIT 30",
            enable_cross_partition_query=True
        ))
    except Exception as e:
        print(f"History error: {e}")
        return []
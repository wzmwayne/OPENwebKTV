import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
MEDIA_DIR = os.path.join(DATA_DIR, "media")
FRONTEND_DIR = os.path.join(os.path.dirname(BASE_DIR), "frontend")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(MEDIA_DIR, exist_ok=True)


class Settings:
    HOST: str = "0.0.0.0"
    PORT: int = 8080
    DB_PATH: str = os.path.join(DATA_DIR, "openwebktv.db")
    MEDIA_DIR: str = MEDIA_DIR
    FRONTEND_DIR: str = FRONTEND_DIR
    MAX_QUEUE_SIZE: int = 50
    BILIBILI_COOKIE: str = os.path.join(BASE_DIR, "bilibili_cookie.json")
    ADMIN_STATIC_PASSWORD: str = os.environ.get("OWK_ADMIN_PASSWORD", "1234")


settings = Settings()

import os


def get_app_version() -> str:
    return os.environ.get("APP_VERSION", "dev").strip() or "dev"

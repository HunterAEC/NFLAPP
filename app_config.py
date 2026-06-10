import os


class Config:
    DATABASE_URL = os.getenv(
        "DATABASE_URL", "sqlite+aiosqlite:///./nfl_app.db")
    SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key")


config = Config()

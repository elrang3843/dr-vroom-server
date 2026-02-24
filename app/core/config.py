"""
Dr. Vroom (닥터브릉이) - Server Configuration
The Brain of Dr. Vroom: stores all knowledge, processes diagnoses, learns from experience
"""
from pydantic_settings import BaseSettings
from typing import Optional
import os


class Settings(BaseSettings):
    # App Identity
    APP_NAME: str = "Dr. Vroom Brain Server"
    APP_NAME_KR: str = "닥터브릉이 두뇌 서버"
    VERSION: str = "1.0.0"
    PATENT_NO: str = "US 12,349,291 B2"

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DEBUG: bool = False

    # Database (SQLite for cost-zero, upgradeable to PostgreSQL)
    DATABASE_URL: str = "sqlite+aiosqlite:///./dr_vroom_brain.db"

    # Security
    SECRET_KEY: str = "dr-vroom-secret-key-change-in-production-2025"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    # WebSocket
    MAX_CONNECTIONS: int = 1023          # Max concurrent connections
    WS_HEARTBEAT_INTERVAL: int = 30      # seconds

    # Audio Analysis
    SAMPLE_RATE: int = 44100
    FFT_SIZE: int = 4096
    MIN_CONFIDENCE: float = 0.5          # Minimum confidence to save knowledge

    # Knowledge Learning
    MIN_SAMPLES_TO_LEARN: int = 3        # Min samples before confident diagnosis
    KNOWLEDGE_VERSION: str = "1.0.0"

    # CORS
    ALLOWED_ORIGINS: list = [
        "http://localhost:*",
        "https://*",
        "*"
    ]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()

"""从环境读取服务配置；模型凭据仅留在服务端。"""

from pathlib import Path
from typing import Literal
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+psycopg://vibe@localhost:5432/vibe"
    redis_url: str = "redis://localhost:6379/0"
    upload_dir: Path = Path("uploads")
    max_upload_mb: int = Field(default=25, ge=1, le=200)
    session_hours: int = Field(default=12, ge=1, le=168)
    cookie_secure: bool = True
    cookie_name: str = "vibe_session"
    trusted_origins: str = ""
    query_timeout_ms: int = Field(default=10000, ge=100, le=60000)
    result_limit: int = Field(default=500, ge=1, le=5000)
    mutation_limit: int = Field(default=1000, ge=1, le=10000)
    plan_minutes: int = Field(default=15, ge=1, le=120)
    model_provider: str = "disabled"
    model_base_url: str = ""
    model_api_key: str = ""
    model_name: str = ""
    model_timeout: float = Field(default=30, gt=0, le=300)
    model_retries: int = Field(default=2, ge=0, le=5)
    model_concurrency: int = Field(default=4, ge=1, le=32)
    model_protocol_verified: bool = False
    model_allowed_data: bool = False
    model_send_samples: bool = False
    model_request_template: str = '{"model":"$model","input":"$prompt"}'
    model_response_path: str = "output"
    model_auth_header: str = "Authorization"
    model_auth_prefix: str = "Bearer "
    model_extra_headers: dict[str, str] = Field(default_factory=dict)
    model_reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh"] | None = None
    model_store: bool = False
    model_mock_delay_seconds: float = Field(default=0, ge=0, le=10)
    model_max_response_bytes: int = 262144


settings = Settings()

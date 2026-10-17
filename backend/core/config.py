from pydantic_settings import BaseSettings
from pydantic import ConfigDict
from pathlib import Path


class Settings(BaseSettings):
    model_config = ConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    project_name: str = "RepeatFix"
    api_v1_prefix: str = "/api/v1"
    debug: bool = True
    backend_cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    db_url: str = "sqlite:///./repeatfix.db"
    sql_echo: bool = False
    data_dir: Path = Path("data")
    samples_dir: Path = Path("samples")
    max_import_size_mb: int = 100
    max_import_rows: int = 50000
    supported_import_types: list[str] = ["text/csv", "application/csv"]


settings = Settings()

from pathlib import Path
from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py → 仓库根在 parents[2]
REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "backend" / "data"
DB_PATH = DATA_DIR / "travel.db"
PLANS_DIR = DATA_DIR / "plans"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / "backend" / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key: str = ""
    search_api_key: str = ""

    @property
    def db_path(self) -> Path:
        return DB_PATH

    @property
    def plans_dir(self) -> Path:
        return PLANS_DIR

    @property
    def llm_host(self) -> str:
        return urlparse(self.llm_base_url).hostname or ""


def get_settings() -> Settings:
    return Settings()

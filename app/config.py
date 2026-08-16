"""Configuration read from environment variables (.env).

Project rule: **secrets in .env, preferences in the database**. Only what is
needed to boot and reach the database lives here.
"""

from functools import lru_cache
from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    mysql_host: str = "db"
    mysql_port: int = 3306
    mysql_database: str = "spese"
    mysql_user: str = "spese"
    mysql_password: str = ""

    app_password: str = ""
    # The session cookie should only be marked `secure` behind HTTPS: in
    # development on http://localhost a secure cookie is never sent and login
    # would look broken. Set this to true on the VPS.
    cookie_secure: bool = False
    tz: str = "Europe/Rome"

    @property
    def database_url(self) -> str:
        # quote_plus on the password: an @ or / in it would break the URL.
        return (
            f"mysql+pymysql://{self.mysql_user}:{quote_plus(self.mysql_password)}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}"
            f"?charset=utf8mb4"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()

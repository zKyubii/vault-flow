"""Configurazione letta dalle variabili d'ambiente (.env).

Regola del progetto: **segreti nel .env, preferenze nel database**.
Qui ci sta solo ciò che serve per avviarsi e collegarsi al DB.
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
    # Il cookie di sessione va marcato `secure` solo dietro HTTPS: in
    # sviluppo su http://localhost un cookie secure non verrebbe mai inviato
    # e il login sembrerebbe rotto. Da mettere a true sulla VPS.
    cookie_secure: bool = False
    tz: str = "Europe/Rome"

    @property
    def database_url(self) -> str:
        # quote_plus sulla password: se contiene @ o / l'URL si romperebbe.
        return (
            f"mysql+pymysql://{self.mysql_user}:{quote_plus(self.mysql_password)}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}"
            f"?charset=utf8mb4"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()

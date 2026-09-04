"""Application settings.

DATABASE_URL is deliberately *derived* from the POSTGRES_* components rather than
stored as its own variable: compose needs the components for the db service anyway,
and a second full URL is a second thing that can silently drift out of sync.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_user: str = "canals"
    postgres_password: str = "canals"
    postgres_db: str = "canals"
    postgres_host: str = "db"
    postgres_port: int = 5432

    app_env: str = "local"
    sql_echo: bool = False
    reservation_ttl_seconds: int = 900

    # Points at the db-test compose service. Only the test fixtures read it.
    postgres_test_host: str = "db-test"
    postgres_test_db: str = "canals_test"

    @property
    def test_database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_test_host}:{self.postgres_port}/{self.postgres_test_db}"
        )

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

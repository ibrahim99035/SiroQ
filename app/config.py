from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_API_KEY = "dev_api_key_change_in_production"


class Settings(BaseSettings):
    """Configuration for the SiroQ analysis service.

    The application connects to Postgres as a separate, least-privileged role
    (``siroq_app``); migrations run as the owning role (``siroq``).
    """

    model_config = SettingsConfigDict(env_file=".env", env_prefix="")

    DATABASE_URL: str = Field(
        default="postgresql+psycopg://siroq_app:siroq_app_dev_password@localhost:5433/siroq"
    )
    MIGRATIONS_DATABASE_URL: str = Field(
        default="postgresql+psycopg://siroq:siroq_dev_password@localhost:5433/siroq"
    )
    API_KEY: str = Field(default=DEV_API_KEY)
    STORAGE_PATH: str = Field(default="./data/storage")
    ENVIRONMENT: str = Field(default="development")
    # Upload guard rails.
    MAX_FILES_PER_REQUEST: int = Field(default=25)
    MAX_FILE_BYTES: int = Field(default=50 * 1024 * 1024)

    @model_validator(mode="after")
    def _require_real_api_key_outside_dev(self):
        """Refuse to boot outside development with the shipped placeholder key."""
        if self.ENVIRONMENT != "development" and self.API_KEY == DEV_API_KEY:
            raise ValueError(
                "API_KEY must be changed from the development default when "
                f"ENVIRONMENT={self.ENVIRONMENT!r}"
            )
        return self


settings = Settings()
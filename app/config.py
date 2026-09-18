from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_SECRET_KEY = "dev_secret_key_change_in_production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="")

    DATABASE_URL: str = Field(
        default="postgresql+psycopg://siroq_app:siroq_app_dev_password@localhost:5432/siroq"
    )
    MIGRATIONS_DATABASE_URL: str = Field(
        default="postgresql+psycopg://siroq:siroq_dev_password@localhost:5432/siroq"
    )
    SECRET_KEY: str = Field(default=DEV_SECRET_KEY)
    BRONZE_STORAGE_PATH: str = Field(default="./data/bronze")
    ENVIRONMENT: str = Field(default="development")
    # Upload guard rails: refuse an oversized file rather than buffering it, and
    # cap how far a zip can expand so a zip bomb cannot exhaust memory or disk.
    MAX_UPLOAD_BYTES: int = Field(default=50 * 1024 * 1024)
    MAX_ZIP_UNCOMPRESSED_BYTES: int = Field(default=200 * 1024 * 1024)

    def zip_cap(self) -> int:
        """Maximum decompressed size allowed for a single zip upload."""
        return int(self.MAX_ZIP_UNCOMPRESSED_BYTES)

    @model_validator(mode="after")
    def _require_real_secret_outside_dev(self):
        """Refuse to start in a non-development environment with the shipped
        placeholder signing key, which would make session cookies forgeable."""
        if self.ENVIRONMENT != "development" and self.SECRET_KEY == DEV_SECRET_KEY:
            raise ValueError(
                "SECRET_KEY must be changed from the development default when "
                f"ENVIRONMENT={self.ENVIRONMENT!r}"
            )
        return self


settings = Settings()
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="")

    DATABASE_URL: str = Field(
        default="postgresql+psycopg://siroq_app:siroq_app_dev_password@localhost:5432/siroq"
    )
    MIGRATIONS_DATABASE_URL: str = Field(
        default="postgresql+psycopg://siroq:siroq_dev_password@localhost:5432/siroq"
    )
    SECRET_KEY: str = Field(default="dev_secret_key_change_in_production")
    BRONZE_STORAGE_PATH: str = Field(default="./data/bronze")
    ENVIRONMENT: str = Field(default="development")


settings = Settings()
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_API_KEY = "dev_api_key_change_in_production"


class Settings(BaseSettings):
    """Configuration for the SiroQ analysis service.

    The application connects to Postgres as a separate, least-privileged role
    (``siroq_app``); migrations run as the owning role (``siroq``).
    """

    # ``extra="ignore"``: ``.env`` is shared with ``docker compose`` (which
    # needs COMPOSE_PROJECT_NAME), so it legitimately carries keys that are not
    # application settings. The default "forbid" turned that into a boot crash.
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    # No defaults. The database is Neon and the app role is provisioned there;
    # a default pointing at a local port would turn a missing variable into a
    # connection-refused instead of naming the variable that is actually missing.
    DATABASE_URL: str
    MIGRATIONS_DATABASE_URL: str
    API_KEY: str = Field(default=DEV_API_KEY)
    STORAGE_PATH: str = Field(default="./data/storage")
    ENVIRONMENT: str = Field(default="development")
    LOG_LEVEL: str = Field(default="INFO")
    # Upload guard rails.
    MAX_FILES_PER_REQUEST: int = Field(default=25)
    MAX_FILE_BYTES: int = Field(default=50 * 1024 * 1024)

    # --- Background worker --------------------------------------------------
    # A measured analysis of a large file runs for minutes, which is far longer
    # than any reverse proxy in front of a client will hold a request open for.
    # The worker takes that work off the request path: a client enqueues and
    # polls. See app/worker.py.
    WORKER_POLL_INTERVAL_SECONDS: float = Field(default=2.0, gt=0)
    # A job left "running" for longer than this is assumed to have died with its
    # process (deploy, crash, OOM) and is put back on the queue. Comfortably
    # above the worst measured run so a live job is never stolen.
    WORKER_STALE_AFTER_SECONDS: int = Field(default=3600, gt=0)

    # --- Raw-file storage driver -------------------------------------------
    # "local" — a directory on this machine (development, CI, tests).
    # "s3"    — any S3-compatible object store (Neon Object Storage, AWS, R2…).
    #
    # Production must use "s3": a Render service has an ephemeral filesystem, so
    # anything written under STORAGE_PATH is lost on every redeploy and every
    # scale-out, which would break every StoredFile row the database still holds.
    STORAGE_DRIVER: str = Field(default="local")

    # Key namespace inside the bucket. Every object this service writes lives
    # under this prefix, so pointing two services at one bucket cannot have them
    # trampling each other's files. (This deployment's bucket is branch-scoped to
    # the service's own Neon branch, so the client's uploads live elsewhere
    # entirely and the prefix is belt-and-braces.)
    STORAGE_KEY_PREFIX: str = Field(default="analysis/")

    S3_ENDPOINT: str = Field(default="")
    S3_REGION: str = Field(default="us-east-2")
    S3_BUCKET: str = Field(default="")
    S3_ACCESS_KEY_ID: str = Field(default="")
    S3_SECRET_ACCESS_KEY: str = Field(default="")
    # Neon Object Storage is path-style only; virtual-hosted-style addressing
    # returns 400. Keep this on for any endpoint that is not real AWS S3.
    S3_FORCE_PATH_STYLE: bool = Field(default=True)

    # --- Cross-service file handoff ----------------------------------------
    # A client on a different Neon branch cannot hand over an object-storage key:
    # buckets are branch-scoped, so its uploads are in a namespace this service
    # holds no credentials for. Instead the client passes short-lived presigned
    # GET URLs and this service pulls the bytes itself, which keeps each side's
    # credentials on its own side and keeps tens of megabytes off every proxy
    # between them.
    #
    # Those URLs are caller-supplied input, which makes this endpoint capable of
    # fetching *any* URL the service can reach. The host allowlist is what stops
    # it being a server-side request forgery primitive pointed at link-local
    # metadata endpoints or the private network. Comma-separated; blank means
    # "this service's own object store".
    SOURCE_FETCH_TIMEOUT_SECONDS: float = Field(default=120.0, gt=0)
    SOURCE_URL_ALLOWED_HOSTS: str = Field(default="")

    @model_validator(mode="after")
    def _validate_storage(self):
        driver = self.STORAGE_DRIVER.strip().lower()
        if driver not in ("local", "s3"):
            raise ValueError(
                f"STORAGE_DRIVER must be 'local' or 's3', received {self.STORAGE_DRIVER!r}"
            )
        object.__setattr__(self, "STORAGE_DRIVER", driver)

        if driver == "s3":
            missing = [
                name
                for name in (
                    "S3_ENDPOINT",
                    "S3_BUCKET",
                    "S3_ACCESS_KEY_ID",
                    "S3_SECRET_ACCESS_KEY",
                )
                if not getattr(self, name)
            ]
            if missing:
                raise ValueError(
                    "STORAGE_DRIVER=s3 requires " + ", ".join(missing)
                )
        return self

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
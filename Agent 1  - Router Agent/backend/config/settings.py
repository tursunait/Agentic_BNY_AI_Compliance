from pydantic import Field, ConfigDict, field_validator
from pydantic_settings import BaseSettings
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    DATABASE_URL: str = Field(default="postgresql://compliance_user:change_me@localhost:5432/compliance")
    SUPABASE_DB_URL: str = Field(default="")
    SUPABASE_DB_HOST: str = Field(default="")
    SUPABASE_DB_PORT: int = Field(default=5432)
    SUPABASE_DB_NAME: str = Field(default="postgres")
    SUPABASE_DB_USER: str = Field(default="postgres")
    SUPABASE_DB_PASSWORD: str = Field(default="")
    SUPABASE_DB_SSLMODE: str = Field(default="require")
    SUPABASE_URL: str = Field(default="")
    SUPABASE_ANON_KEY: str = Field(default="")
    WEAVIATE_URL: str = Field(default="http://localhost:8080")
    WEAVIATE_API_KEY: str = Field(default="")
    WEAVIATE_STARTUP_PERIOD: int = Field(default=20)
    REDIS_URL: str = Field(default="redis://localhost:6379/0")
    GEMINI_API_KEY: str = Field(default="")
    OPENAI_API_KEY: str = Field(default="")
    SKIP_VALIDATOR_FOR_TESTING: bool = Field(default=False)

    @staticmethod
    def _strip_wrapping_quotes(value: str) -> str:
        text = (value or "").strip()
        if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
            return text[1:-1].strip()
        return text

    @staticmethod
    def _is_placeholder_dsn(value: str) -> bool:
        text = (value or "").upper()
        return any(
            token in text
            for token in (
                "YOUR_URLENCODED_DB_PASSWORD",
                "YOUR_DB_PASSWORD",
                "CHANGE_ME",
                "<PASSWORD>",
            )
        )

    @field_validator("WEAVIATE_URL", mode="before")
    @classmethod
    def normalize_weaviate_url(cls, value: str) -> str:
        if not isinstance(value, str):
            return value
        url = value.strip()
        if not url:
            return url
        if url.startswith(("http://", "https://")):
            return url
        if url.startswith(("localhost", "127.0.0.1")):
            return f"http://{url}"
        return f"https://{url}"

    def get_database_url(self) -> str:
        supabase_url = self._strip_wrapping_quotes(self.SUPABASE_DB_URL or "")
        env_supabase_url = self._strip_wrapping_quotes(self.SUPABASE_URL or "")
        database_url = self._strip_wrapping_quotes(self.DATABASE_URL or "")
        db_url = supabase_url
        if self._is_placeholder_dsn(db_url):
            db_url = ""
        if (
            not db_url
            and self._strip_wrapping_quotes(self.SUPABASE_DB_HOST).strip()
            and self.SUPABASE_DB_PASSWORD
        ):
            # Build the DSN from raw components so passwords with special chars
            # don't need manual URL encoding in .env.
            return URL.create(
                "postgresql+psycopg2",
                username=self._strip_wrapping_quotes(self.SUPABASE_DB_USER).strip() or "postgres",
                password=self.SUPABASE_DB_PASSWORD,
                host=self._strip_wrapping_quotes(self.SUPABASE_DB_HOST).strip(),
                port=self.SUPABASE_DB_PORT,
                database=self._strip_wrapping_quotes(self.SUPABASE_DB_NAME).strip() or "postgres",
                query={"sslmode": self._strip_wrapping_quotes(self.SUPABASE_DB_SSLMODE).strip() or "require"},
            ).render_as_string(hide_password=False)
        if not db_url and env_supabase_url.startswith(
            ("postgresql://", "postgresql+psycopg2://", "postgres://")
        ):
            # Backward-compat mode: some environments store DB DSN in SUPABASE_URL.
            db_url = env_supabase_url
            if self._is_placeholder_dsn(db_url):
                db_url = ""
        if not db_url:
            db_url = database_url
        if db_url.startswith(("http://", "https://")):
            raise ValueError(
                "Invalid database URL. Use a PostgreSQL URI (postgresql://... or postgresql+psycopg2://...), "
                "not an HTTP(S) endpoint. The Supabase MCP URL belongs in Codex MCP config, not .env."
            )
        return db_url

    def get_supabase_rest_url(self) -> str:
        url = self._strip_wrapping_quotes(self.SUPABASE_URL or "")
        if url.startswith(("http://", "https://")):
            return url
        return ""

    def has_database_dsn(self) -> bool:
        supabase_url = self._strip_wrapping_quotes(self.SUPABASE_DB_URL or "")
        if supabase_url and not self._is_placeholder_dsn(supabase_url):
            return True
        host = self._strip_wrapping_quotes(self.SUPABASE_DB_HOST or "").strip()
        if host and self.SUPABASE_DB_PASSWORD:
            return True
        env_supabase_url = self._strip_wrapping_quotes(self.SUPABASE_URL or "")
        if env_supabase_url.startswith(("postgresql://", "postgresql+psycopg2://", "postgres://")) and not self._is_placeholder_dsn(env_supabase_url):
            return True
        return False

    model_config = ConfigDict(
        env_file=".env",
        extra="allow"
    )


settings = Settings()

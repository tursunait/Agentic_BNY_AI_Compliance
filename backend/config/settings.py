from pydantic import Field, ConfigDict, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = Field(default="postgresql://postgres:postgres@localhost:5432/compliance")
    POSTGRES_USER: str = Field(default="postgres")
    POSTGRES_PASSWORD: str = Field(default="postgres")
    WEAVIATE_URL: str = Field(default="http://localhost:8080")
    WEAVIATE_API_KEY: str = Field(default="")
    WEAVIATE_STARTUP_PERIOD: int = Field(default=20)
    REDIS_URL: str = Field(default="redis://localhost:6379/0")
    GEMINI_API_KEY: str = Field(default="")
    OPENAI_API_KEY: str = Field(default="")

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

    model_config = ConfigDict(
        env_file=".env",
        extra="allow"
    )


settings = Settings()

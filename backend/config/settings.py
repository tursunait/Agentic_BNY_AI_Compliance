from pydantic import Field, ConfigDict
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = Field(default="postgresql://postgres:postgres@postgres:5432/compliance")
    POSTGRES_USER: str = Field(default="postgres")
    POSTGRES_PASSWORD: str = Field(default="postgres")
    WEAVIATE_URL: str = Field(default="http://weaviate:8080")
    WEAVIATE_API_KEY: str = Field(default="default-key")
    REDIS_URL: str = Field(default="redis://redis:6379/0")
    GEMINI_API_KEY: str = Field(default="")
    OPENAI_API_KEY: str = Field(default="")

    model_config = ConfigDict(
        env_file=".env",
        extra="allow"
    )


settings = Settings()

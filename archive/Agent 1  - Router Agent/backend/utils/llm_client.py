"""Simple OpenAI wrapper for shared settings."""

import openai

from backend.config.settings import settings


class OpenAIClient(openai.OpenAI):
    def __init__(self):
        super().__init__(api_key=settings.OPENAI_API_KEY)

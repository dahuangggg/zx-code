"""Embedding functions used by CodeContext."""
from __future__ import annotations

import os
from typing import Any


class OpenAICodeEmbeddingFunction:
    """Chroma-compatible embedding function backed by the OpenAI embeddings API."""

    def __init__(
        self,
        *,
        model: str,
        api_key_env: str = "OPENAI_API_KEY",
        base_url: str = "",
    ) -> None:
        self.model = model
        self.api_key_env = api_key_env
        self.base_url = base_url
        self._client: Any = None

    def __call__(self, input):
        client = self._get_client()
        texts = list(input)
        response = client.embeddings.create(model=self.model, input=texts)
        return [item.embedding for item in response.data]

    def embed_documents(self, input):
        return self(input)

    def embed_query(self, input):
        return self(input)

    def name(self) -> str:
        return f"openai:{self.model}"

    def _get_client(self) -> Any:
        if self._client is None:
            from openai import OpenAI

            kwargs: dict[str, Any] = {}
            api_key = os.getenv(self.api_key_env) if self.api_key_env else None
            if api_key:
                kwargs["api_key"] = api_key
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = OpenAI(**kwargs)
        return self._client

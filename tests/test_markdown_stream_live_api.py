from __future__ import annotations

import os

import pytest

from agent.models import Message
from agent.providers.litellm_client import LiteLLMModelClient
from agent.runtime.markdown_stream import MarkdownStreamRenderer
from agent.runtime.utils import console


@pytest.mark.asyncio
async def test_live_api_streams_markdown_through_renderer() -> None:
    """Manual smoke test for watching streamed Markdown render in the terminal.

    This test is skipped by default so normal test runs do not hit the network.

    Example:
        RUN_LIVE_MARKDOWN_STREAM=1 \
        MARKDOWN_STREAM_MODEL=openai/gpt-5.2 \
        MARKDOWN_STREAM_API_KEY=api \
        MARKDOWN_STREAM_BASE_URL=https://www.right.codes/codex/v1 \
        uv run pytest tests/test_markdown_stream_live_api.py -s -q
    """
    if os.getenv("RUN_LIVE_MARKDOWN_STREAM") != "1":
        pytest.skip("set RUN_LIVE_MARKDOWN_STREAM=1 to call the live model API")

    model = os.getenv("MARKDOWN_STREAM_MODEL", "openai/gpt-4o-mini")
    api_key = os.getenv("MARKDOWN_STREAM_API_KEY", "")
    base_url = os.getenv("MARKDOWN_STREAM_BASE_URL", "")

    extra_kwargs: dict[str, str] = {}
    if api_key:
        extra_kwargs["api_key"] = api_key
    if base_url:
        extra_kwargs["base_url"] = base_url

    renderer = MarkdownStreamRenderer(console)
    client = LiteLLMModelClient(
        model=model,
        extra_kwargs=extra_kwargs,
    )

    await client.run_turn(
        system_prompt="You are a concise assistant. Always answer in Markdown.",
        messages=[
            Message.user(
                "用 Markdown 写一个很短的 Python 示例。"
                "包含一个二级标题、两个 bullet，以及一个 fenced code block。"
            )
        ],
        tools=[],
        stream_handler=renderer.write,
    )
    renderer.flush()
    console.print()

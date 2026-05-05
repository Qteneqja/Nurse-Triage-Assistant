"""
Test DeepSeek API Connection
"""

import os

import pytest
from openai import OpenAI

api_key = os.getenv("DEEPSEEK_API_KEY")

pytestmark = pytest.mark.skipif(
    not api_key,
    reason="DEEPSEEK_API_KEY not set; skipping external DeepSeek connectivity test",
)


def test_deepseek_api_connection():
    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")

    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": "You are a helpful assistant"},
            {
                "role": "user",
                "content": "Say 'Connection successful!' and nothing else.",
            },
        ],
        stream=False,
    )

    assert response.choices[0].message.content is not None
    assert "Connection successful!" in response.choices[0].message.content

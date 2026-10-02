"""Shared request-building for OpenAI-compatible chat completions.

Supports both plain OpenAI and Azure OpenAI, which use the same JSON message shape but
different URLs, auth headers, and model addressing (Azure uses a deployment name in the
URL path instead of a "model" field in the body).
"""

from __future__ import annotations

from typing import Any

from app.config import Settings


def chat_completions_request(
    settings: Settings, model: str, messages: list[dict[str, Any]], **extra: Any
) -> tuple[str, dict[str, str], dict[str, Any]]:
    """Build (url, headers, body) for a chat completion, for whichever provider is configured.

    `model` is sourced from env (INTERVIEWER_MODEL / EVALUATOR_MODEL) and used as the body
    "model" field for both providers. For Azure this resource serves the newer
    `/openai/v1/chat/completions` surface, where the model/deployment is addressed via the
    body rather than the legacy `/openai/deployments/{deployment}/...` URL path; if no model
    is given it falls back to the configured deployment name (also from env).
    """
    if settings.openai_provider == "azure":
        url = f"{settings.azure_openai_endpoint}/openai/v1/chat/completions"
        headers = {"api-key": settings.openai_api_key, "Content-Type": "application/json"}
        body = {
            "model": model or settings.azure_openai_deployment,
            "messages": messages,
            **extra,
        }
        # Newer Azure models reject the legacy `max_tokens` field.
        if "max_tokens" in body:
            body["max_completion_tokens"] = body.pop("max_tokens")
        return url, headers, body

    url = f"{settings.openai_base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }
    body = {"model": model, "messages": messages, **extra}
    return url, headers, body

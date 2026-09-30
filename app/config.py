"""Environment-based settings shared by the web app and the workers."""

from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    pass


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"Missing required environment variable: {name}")
    return value


@dataclass(frozen=True)
class Settings:
    secret_key: str
    database_url: str
    redis_url: str
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_provider: str = "openai"  # "openai" | "azure"
    azure_openai_endpoint: str = ""
    azure_openai_deployment: str = ""
    azure_openai_api_version: str = "2024-10-21"
    interviewer_model: str = "gpt-4.1-mini"
    evaluator_provider: str = "openai"  # "openai" | "bedrock"
    evaluator_model: str = "gpt-4.1-mini"
    bedrock_model_id: str = ""
    aws_region: str = "ap-south-1"
    github_token: str = ""
    deepgram_api_key: str = ""
    deepgram_ws_url: str = "wss://api.deepgram.com/v1/listen"
    deepgram_model: str = "nova-2"
    sarvam_api_key: str = ""
    sarvam_base_url: str = "https://api.sarvam.ai"
    sarvam_speaker: str = "anushka"
    sarvam_model: str = "bulbul:v2"
    sarvam_language_code: str = "en-IN"
    voice_ws_host: str = "0.0.0.0"  # noqa: S104 -- intended: binds inside a private docker/VM network
    voice_ws_port: int = 8765
    # Base ws(s):// URL the *browser* should use to reach the voice worker directly, e.g.
    # "ws://localhost:8765" for local dev. Leave empty in production, where nginx proxies
    # /voice/ on the same origin as the page, so the browser should use a same-origin
    # relative path instead.
    voice_ws_public_url: str = ""
    max_call_seconds: int = 900
    max_interviews_per_ip_per_day: int = 3
    max_interviews_per_day: int = 100
    ratelimit_storage_uri: str = "memory://"
    session_cookie_secure: bool = True
    trust_proxy: bool = True
    retention_days: int = 30

    @classmethod
    def from_env(cls) -> Settings:
        redis_url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
        settings = cls(
            secret_key=_required("SECRET_KEY"),
            database_url=_required("DATABASE_URL"),
            redis_url=redis_url,
            openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
            openai_base_url=os.environ.get("OPENAI_BASE_URL", cls.openai_base_url).rstrip("/"),
            openai_provider=os.environ.get("OPENAI_PROVIDER", cls.openai_provider),
            azure_openai_endpoint=os.environ.get("AZURE_OPENAI_ENDPOINT", "").rstrip("/"),
            azure_openai_deployment=os.environ.get("AZURE_OPENAI_DEPLOYMENT", ""),
            azure_openai_api_version=os.environ.get(
                "AZURE_OPENAI_API_VERSION", cls.azure_openai_api_version
            ),
            interviewer_model=os.environ.get("INTERVIEWER_MODEL", cls.interviewer_model),
            evaluator_provider=os.environ.get("EVALUATOR_PROVIDER", cls.evaluator_provider),
            evaluator_model=os.environ.get("EVALUATOR_MODEL", cls.evaluator_model),
            bedrock_model_id=os.environ.get("BEDROCK_MODEL_ID", ""),
            aws_region=os.environ.get("AWS_REGION", cls.aws_region),
            github_token=os.environ.get("GITHUB_TOKEN", ""),
            deepgram_api_key=os.environ.get("DEEPGRAM_API_KEY", ""),
            deepgram_ws_url=os.environ.get("DEEPGRAM_WS_URL", cls.deepgram_ws_url),
            deepgram_model=os.environ.get("DEEPGRAM_MODEL", cls.deepgram_model),
            sarvam_api_key=os.environ.get("SARVAM_API_KEY", ""),
            sarvam_base_url=os.environ.get("SARVAM_BASE_URL", cls.sarvam_base_url).rstrip("/"),
            sarvam_speaker=os.environ.get("SARVAM_SPEAKER", cls.sarvam_speaker),
            sarvam_model=os.environ.get("SARVAM_MODEL", cls.sarvam_model),
            sarvam_language_code=os.environ.get(
                "SARVAM_LANGUAGE_CODE", cls.sarvam_language_code
            ),
            voice_ws_host=os.environ.get("VOICE_WS_HOST", cls.voice_ws_host),
            voice_ws_port=int(os.environ.get("VOICE_WS_PORT", cls.voice_ws_port)),
            voice_ws_public_url=os.environ.get(
                "VOICE_WS_PUBLIC_URL", cls.voice_ws_public_url
            ).rstrip("/"),
            max_call_seconds=int(os.environ.get("MAX_CALL_SECONDS", cls.max_call_seconds)),
            max_interviews_per_ip_per_day=int(
                os.environ.get("MAX_INTERVIEWS_PER_IP_PER_DAY", cls.max_interviews_per_ip_per_day)
            ),
            max_interviews_per_day=int(
                os.environ.get("MAX_INTERVIEWS_PER_DAY", cls.max_interviews_per_day)
            ),
            ratelimit_storage_uri=os.environ.get("RATELIMIT_STORAGE_URI", redis_url),
            session_cookie_secure=_bool(os.environ.get("SESSION_COOKIE_SECURE"), True),
            trust_proxy=_bool(os.environ.get("TRUST_PROXY"), True),
            retention_days=int(os.environ.get("RETENTION_DAYS", cls.retention_days)),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if len(self.secret_key) < 32:
            raise ConfigError("SECRET_KEY must be at least 32 characters")
        if self.openai_provider not in {"openai", "azure"}:
            raise ConfigError("OPENAI_PROVIDER must be 'openai' or 'azure'")
        if self.openai_provider == "azure" and not (
            self.azure_openai_endpoint and self.azure_openai_deployment
        ):
            raise ConfigError(
                "AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_DEPLOYMENT are required "
                "when OPENAI_PROVIDER=azure"
            )
        if self.evaluator_provider not in {"openai", "bedrock"}:
            raise ConfigError("EVALUATOR_PROVIDER must be 'openai' or 'bedrock'")
        if self.evaluator_provider == "bedrock" and not self.bedrock_model_id:
            raise ConfigError("BEDROCK_MODEL_ID is required when EVALUATOR_PROVIDER=bedrock")
        if self.max_call_seconds < 60:
            raise ConfigError("MAX_CALL_SECONDS must be >= 60")

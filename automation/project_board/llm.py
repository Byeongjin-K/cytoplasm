"""One model interface for the project analysis, two providers behind it.

* ``hermes`` (default) — :class:`automation.codex_llm.CodexClient`, the repository's only
  Hermes route. The account's ``~/.hermes/config.yaml`` picks the model and its fallback,
  so the analysis answers from the same main model as the agent itself; no model name is
  written here (owner decision 2026-09-29, conformance test ``test_model_single_source``).
* ``openai-compatible`` — a local server (vLLM, llama.cpp ``--api``, …) speaking
  ``POST <base_url>/chat/completions``. Selected by configuration only; its model id lives
  in the node's config file, never in code.

Every provider answers :class:`LlmAnswer` with the route that actually served, so a
proposal records which model wrote it.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Protocol
from urllib.parse import urlparse

from .models import ProjectBoardError

PROVIDERS: Final = ("hermes", "openai-compatible")
DEFAULT_TIMEOUT: Final = 180.0


@dataclass(frozen=True, slots=True)
class LlmAnswer:
    text: str
    provider: str
    model: str


class LlmProvider(Protocol):
    @property
    def name(self) -> str: ...

    def complete(self, prompt: str) -> LlmAnswer: ...


@dataclass(frozen=True, slots=True)
class HermesProvider:
    env: Mapping[str, str] | None = None
    timeout: float = DEFAULT_TIMEOUT

    @property
    def name(self) -> str:
        return "hermes"

    def complete(self, prompt: str) -> LlmAnswer:
        from automation import codex_llm

        try:
            served = codex_llm.CodexClient.from_environment(self.env, timeout=self.timeout).complete_served(
                prompt
            )
        except codex_llm.CodexUnavailableError as error:
            raise ProjectBoardError(f"main model route unavailable: {error}", retryable=True) from error
        except codex_llm.CodexError as error:
            raise ProjectBoardError(f"main model returned nothing: {error}", retryable=True) from error
        return LlmAnswer(served.text, served.provider, served.model)


@dataclass(frozen=True, slots=True)
class OpenAICompatibleProvider:
    base_url: str
    model: str
    api_key_env: str = ""
    timeout: float = DEFAULT_TIMEOUT
    env: Mapping[str, str] | None = None

    @property
    def name(self) -> str:
        return "openai-compatible"

    def complete(self, prompt: str) -> LlmAnswer:
        body = json.dumps(
            {"model": self.model, "messages": [{"role": "user", "content": prompt}], "temperature": 0},
            ensure_ascii=False,
        ).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key_env:
            key = (self.env if self.env is not None else os.environ).get(self.api_key_env, "")
            if not key:
                raise ProjectBoardError(f"{self.api_key_env} is unset for the local model endpoint")
            headers["Authorization"] = f"Bearer {key}"
        request = urllib.request.Request(
            self.base_url.rstrip("/") + "/chat/completions", data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - configured http(s) endpoint
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise ProjectBoardError(f"local model endpoint answered HTTP {error.code}", retryable=True) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise ProjectBoardError(f"local model endpoint unreachable: {error}", retryable=True) from error
        except ValueError as error:
            raise ProjectBoardError("local model endpoint returned non-JSON", retryable=True) from error
        try:
            text = str(payload["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError) as error:
            raise ProjectBoardError("local model answer has no choices[0].message.content", retryable=True) from error
        if not text:
            raise ProjectBoardError("local model returned an empty completion", retryable=True)
        return LlmAnswer(text, "openai-compatible", str(payload.get("model") or self.model))


@dataclass(frozen=True, slots=True)
class LlmConfig:
    provider: str = "hermes"
    base_url: str = ""
    model: str = ""
    api_key_env: str = ""
    timeout: float = DEFAULT_TIMEOUT


def llm_config(raw: object) -> LlmConfig:
    """Validate the ``llm`` block of the project-board config; absent means ``hermes``."""
    if raw is None:
        return LlmConfig()
    if not isinstance(raw, dict):
        raise ProjectBoardError("config llm must be an object")
    provider = str(raw.get("provider") or "hermes")
    if provider not in PROVIDERS:
        raise ProjectBoardError(f"config llm.provider must be one of {', '.join(PROVIDERS)}")
    timeout = float(raw.get("timeout") or DEFAULT_TIMEOUT)
    if provider == "hermes":
        return LlmConfig(provider=provider, timeout=timeout)
    base_url = str(raw.get("base_url") or "").strip()
    model = str(raw.get("model") or "").strip()
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ProjectBoardError("config llm.base_url must be an http(s) URL, e.g. http://127.0.0.1:8000/v1")
    if not model:
        raise ProjectBoardError("config llm.model must name the local server's model id")
    return LlmConfig(provider, base_url, model, str(raw.get("api_key_env") or ""), timeout)


def provider_from_config(config: LlmConfig, *, env: Mapping[str, str] | None = None) -> LlmProvider:
    match config.provider:
        case "hermes":
            return HermesProvider(env=env, timeout=config.timeout)
        case "openai-compatible":
            return OpenAICompatibleProvider(
                config.base_url, config.model, config.api_key_env, config.timeout, env
            )
        case other:
            raise ProjectBoardError(f"unknown llm provider: {other}")


def describe(config: LlmConfig) -> str:
    """One line for status output — never the key value."""
    if config.provider == "hermes":
        return "hermes (계정 ~/.hermes/config.yaml 의 주 모델·폴백)"
    host = urlparse(config.base_url).netloc
    return f"openai-compatible {host} model={config.model}"

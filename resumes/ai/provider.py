"""AI providers. Ollama (local, free, open models) is the default; Gemini is optional; 'none' disables AI."""

import re
from typing import Protocol

import httpx
from django.conf import settings


class AiDisabledError(Exception):
    def __init__(self, message="AI is switched off. Turn it on in the AI panel, then press “Tailor with AI”."):
        super().__init__(message)


class AiUnavailableError(Exception):
    def __init__(self, message="The AI provider is not configured or not reachable."):
        super().__init__(message)


class AiQuotaError(Exception):
    def __init__(self, message="The AI provider's quota or rate limit was reached. Try again later; your resume is unchanged."):
        super().__init__(message)


class AiOutputError(Exception):
    def __init__(self, message="The AI response could not be understood. Your resume is unchanged; you can retry or edit manually."):
        super().__init__(message)


class AiProvider(Protocol):
    """Every provider takes the same call, so tailor.py, the email writer and the guards never change
    when AI_PROVIDER does. `schema` is a JSON schema for structured output; providers that cannot
    enforce it ignore it (the caller validates with pydantic either way)."""

    model: str

    def generate_json(self, system: str, user: str, schema: dict | None = None) -> str: ...


_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)


class OllamaProvider:
    """Local Ollama via POST /api/chat. Uses Ollama structured outputs (a JSON schema in `format`)."""

    def __init__(self, base_url: str, model: str, client: httpx.Client | None = None, timeout: float = 300):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client = client
        self.timeout = timeout

    def generate_json(self, system: str, user: str, schema: dict | None = None) -> str:
        payload = {
            "model": self.model,
            "stream": False,
            "think": False,  # Qwen3 and other thinking models: answer directly, no reasoning text
            "format": schema or "json",
            "options": {"temperature": 0.3},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        client = self._client or httpx.Client(timeout=httpx.Timeout(self.timeout, connect=5))
        try:
            res = client.post(f"{self.base_url}/api/chat", json=payload)
        except httpx.TimeoutException as e:
            if isinstance(e, httpx.ConnectTimeout):
                raise AiUnavailableError("Could not reach Ollama. Start the Ollama app (or run: ollama serve) and try again.") from e
            raise AiUnavailableError(
                f"The local model took longer than {int(self.timeout)} seconds and was stopped. Try a shorter job description, "
                "close other heavy apps, or raise OLLAMA_TIMEOUT in .env. Nothing was changed."
            ) from e
        except httpx.HTTPError as e:
            raise AiUnavailableError("Could not reach Ollama. Start the Ollama app (or run: ollama serve) and check OLLAMA_BASE_URL.") from e
        finally:
            if self._client is None:
                client.close()
        if res.status_code == 404:
            raise AiUnavailableError(f'Ollama does not have the model "{self.model}". Run: ollama pull {self.model}')
        if res.status_code >= 400:
            detail = ""
            try:
                detail = str(res.json().get("error", ""))[:200]
            except ValueError:
                pass
            raise AiUnavailableError(f"Ollama returned an error ({res.status_code}){': ' + detail if detail else ''}.")
        try:
            content = res.json()["message"]["content"]
        except (ValueError, KeyError, TypeError) as e:
            raise AiOutputError("Ollama returned a response the app could not read.") from e
        content = _THINK.sub("", content or "").strip()
        if not content:
            raise AiOutputError("Ollama returned an empty response.")
        return content


class OllamaCloudProvider:
    """Ollama's hosted API (ollama.com), which speaks the OpenAI chat-completions format."""

    def __init__(self, api_key: str, model: str, base_url: str = "https://ollama.com/v1", client: httpx.Client | None = None):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._client = client

    def generate_json(self, system: str, user: str, schema: dict | None = None) -> str:
        payload = {
            "model": self.model,
            "temperature": 0.3,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        client = self._client or httpx.Client(timeout=httpx.Timeout(120, connect=10))
        try:
            res = client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
        except httpx.HTTPError as e:
            raise AiUnavailableError("Could not reach Ollama Cloud. Check your internet connection.") from e
        finally:
            if self._client is None:
                client.close()
        if res.status_code == 429:
            raise AiQuotaError()
        if res.status_code in (401, 403):
            raise AiUnavailableError("Ollama Cloud rejected the API key. Check OLLAMA_API_KEY in .env.")
        if res.status_code == 404:
            raise AiUnavailableError(f'Ollama Cloud has no model "{self.model}". Check OLLAMA_CLOUD_MODEL in .env.')
        if res.status_code >= 400:
            raise AiUnavailableError(f"Ollama Cloud returned an error ({res.status_code}).")
        try:
            content = res.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise AiOutputError("Ollama Cloud returned an empty response.") from e
        content = _THINK.sub("", content or "").strip()
        if not content:
            raise AiOutputError("Ollama Cloud returned an empty response.")
        return content


class GeminiProvider:
    def __init__(self, api_key: str, model: str, client: httpx.Client | None = None):
        self.api_key = api_key
        self.model = model
        self._client = client

    def generate_json(self, system: str, user: str, schema: dict | None = None) -> str:
        url =f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.3},
        }
        client = self._client or httpx.Client(timeout=60)
        try:
            res = client.post(url, json=payload, headers={"x-goog-api-key": self.api_key})
        except httpx.HTTPError as e:
            raise AiUnavailableError() from e
        finally:
            if self._client is None:
                client.close()
        if res.status_code == 429:
            raise AiQuotaError()
        if res.status_code in (400, 401, 403):
            raise AiUnavailableError("The AI provider rejected the request. Check GEMINI_API_KEY and GEMINI_MODEL on the server.")
        if res.status_code >= 400:
            raise AiUnavailableError()
        try:
            parts = res.json()["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts)
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise AiOutputError("The AI returned an empty response.") from e
        if not text:
            raise AiOutputError("The AI returned an empty response.")
        return text


def provider_from_settings() -> AiProvider | None:
    kind = settings.AI_PROVIDER
    if kind == "ollama":
        return OllamaProvider(settings.OLLAMA_BASE_URL, settings.OLLAMA_MODEL, timeout=settings.OLLAMA_TIMEOUT)
    if kind == "ollama_cloud" and settings.OLLAMA_API_KEY:
        return OllamaCloudProvider(settings.OLLAMA_API_KEY, settings.OLLAMA_CLOUD_MODEL, settings.OLLAMA_CLOUD_URL)
    if kind == "gemini" and settings.GEMINI_API_KEY:
        return GeminiProvider(settings.GEMINI_API_KEY, settings.GEMINI_MODEL)
    return None


def provider_label() -> str:
    p = provider_from_settings()
    return f"{settings.AI_PROVIDER} · {p.model}" if p else "not configured"

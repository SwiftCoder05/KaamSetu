"""Environment-driven configuration helpers used by settings.py.

The same code runs on a laptop and on Render; only environment variables differ:

  local  (DEBUG=true)   AI_PROVIDER=ollama        + DATABASE_URL -> local PostgreSQL
  Render (DEBUG=false)  AI_PROVIDER=ollama_cloud  + DATABASE_URL -> Neon PostgreSQL (SSL enforced)

Every check here raises ImproperlyConfigured with the *names* of the variables that are wrong. Messages never
contain the values, so passwords and API keys cannot leak into logs.
"""

from urllib.parse import urlparse

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

AI_PROVIDERS = ("ollama", "ollama_cloud", "gemini", "none")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def build_database(url: str, *, debug: bool, sqlite_path) -> dict:
    """Django DATABASES['default'] from DATABASE_URL.

    Production: DATABASE_URL is mandatory, must be PostgreSQL, must not point at this machine, and SSL is forced.
    Development: DATABASE_URL is used as-is (no SSL needed locally); if empty, SQLite is the zero-setup fallback.
    """
    url = (url or "").strip()
    if not url:
        if not debug:
            raise ImproperlyConfigured(
                "DATABASE_URL is not set. In production set it to your Neon PostgreSQL connection string "
                "(Render > Environment)."
            )
        return dj_database_url.parse(f"sqlite:///{sqlite_path}", conn_max_age=60)
    try:
        cfg = dj_database_url.parse(url, conn_max_age=60, conn_health_checks=True, ssl_require=not debug)
    except Exception as e:  # never echo the URL: it contains the password
        raise ImproperlyConfigured("DATABASE_URL is not a valid database URL (expected postgresql://USER:PASSWORD@HOST:PORT/DBNAME).") from e
    is_postgres = "postgresql" in cfg["ENGINE"]
    if not debug:
        if not is_postgres:
            raise ImproperlyConfigured("DATABASE_URL must be a PostgreSQL URL in production (SQLite is for local development only).")
        if _host(url) in LOCAL_HOSTS:
            raise ImproperlyConfigured("DATABASE_URL points at localhost, which does not exist on the server. Use your Neon connection string.")
    return cfg


def validate_ai(*, provider: str, debug: bool, base_url: str, model: str, ollama_api_key: str, gemini_api_key: str) -> None:
    """Fail at startup (not on the first user click) when the chosen AI provider is not fully configured."""
    problems = []
    if provider not in AI_PROVIDERS:
        problems.append(f"AI_PROVIDER must be one of {', '.join(AI_PROVIDERS)}.")
    elif provider == "ollama":
        if not base_url:
            problems.append("OLLAMA_BASE_URL is not set (local default is http://localhost:11434).")
        if not model:
            problems.append("OLLAMA_MODEL is not set (e.g. qwen3:4b).")
        if not debug and _host(base_url) in LOCAL_HOSTS:
            problems.append(
                "AI_PROVIDER=ollama points at localhost, which does not exist on the server. "
                "Use AI_PROVIDER=ollama_cloud with OLLAMA_API_KEY, or AI_PROVIDER=none."
            )
    elif provider == "ollama_cloud":
        if not ollama_api_key:
            problems.append("OLLAMA_API_KEY is required when AI_PROVIDER=ollama_cloud.")
        if not model:
            problems.append("OLLAMA_MODEL is required when AI_PROVIDER=ollama_cloud (the cloud model name).")
        if not base_url.lower().startswith("https://"):
            problems.append("OLLAMA_BASE_URL must start with https:// for Ollama Cloud (use https://ollama.com).")
    elif provider == "gemini" and not gemini_api_key:
        problems.append("GEMINI_API_KEY is required when AI_PROVIDER=gemini.")
    if problems:
        raise ImproperlyConfigured("AI configuration error: " + " ".join(problems))


def validate_production(*, secret_key_set: bool, token_key_set: bool) -> None:
    """Secrets that must exist before a production process is allowed to start."""
    missing = []
    if not secret_key_set:
        missing.append("SECRET_KEY")
    if not token_key_set:
        missing.append("TOKEN_ENCRYPTION_KEY")
    if missing:
        raise ImproperlyConfigured(f"Missing required environment variable(s) for production: {', '.join(missing)}.")

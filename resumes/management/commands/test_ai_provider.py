"""Checks whether the configured AI provider is actually reachable, not just configured.

provider_from_settings() only checks that AI_PROVIDER and the matching key/url are *present*; it never
calls the provider. This command makes one real, cheap call and reports exactly why it failed, using the
same error messages the app shows to users (AiUnavailableError, AiQuotaError, AiOutputError).

Usage:
    python manage.py test_ai_provider

On Render: Dashboard -> your service -> Shell -> run the same command, so it uses the real production
env vars (GEMINI_API_KEY etc.), not your local .env.
"""

from django.conf import settings
from django.core.management.base import BaseCommand

from resumes.ai.provider import AiOutputError, AiQuotaError, AiUnavailableError, provider_from_settings


class Command(BaseCommand):
    help = "Makes one real call to the configured AI provider and reports whether it is actually reachable."

    def handle(self, *args, **opts):
        self.stdout.write(f"AI_PROVIDER = {settings.AI_PROVIDER!r}")
        provider = provider_from_settings()
        if provider is None:
            self.stderr.write(self.style.ERROR(
                "NOT CONFIGURED: provider_from_settings() returned None.\n"
                "  - AI_PROVIDER=ollama needs OLLAMA_BASE_URL / OLLAMA_MODEL (always true, has defaults).\n"
                "  - AI_PROVIDER=ollama_cloud needs a non-empty OLLAMA_API_KEY.\n"
                "  - AI_PROVIDER=gemini needs a non-empty GEMINI_API_KEY.\n"
                "  - AI_PROVIDER=none (or anything else) disables AI on purpose."
            ))
            raise SystemExit(1)

        self.stdout.write(f"model = {provider.model!r}; calling the provider now…")
        try:
            raw = provider.generate_json(
                "Reply with ONLY valid JSON matching the schema.",
                "Return {\"ok\": true} and nothing else.",
                {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
            )
        except AiQuotaError as e:
            self.stderr.write(self.style.ERROR(f"QUOTA/RATE LIMIT: {e}"))
            raise SystemExit(1)
        except AiUnavailableError as e:
            self.stderr.write(self.style.ERROR(f"UNREACHABLE: {e}"))
            raise SystemExit(1)
        except AiOutputError as e:
            self.stderr.write(self.style.ERROR(f"REACHED THE PROVIDER, BUT OUTPUT WAS UNUSABLE: {e}"))
            raise SystemExit(1)
        except Exception as e:  # anything not already one of our typed errors
            self.stderr.write(self.style.ERROR(f"UNEXPECTED ERROR ({type(e).__name__}): {e}"))
            raise SystemExit(1)

        self.stdout.write(self.style.SUCCESS(f"OK: provider replied: {raw[:200]!r}"))

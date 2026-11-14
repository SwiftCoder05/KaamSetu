"""KaamSetu settings. Everything secret or environment-specific comes from .env (see .env.example)."""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

from .envconfig import build_database, validate_ai, validate_production

BASE_DIR = Path(__file__).resolve().parent.parent
# Two separate files: ".env" for your laptop, ".env.production" for the deployed app.
# Select production with APP_ENV=production (or point ENV_FILE at any file). Real environment variables, such as
# the ones set in the Render dashboard, always win over file values, and a missing file is fine.
APP_ENV = os.environ.get("APP_ENV", "local").strip().lower()
ENV_FILE = os.environ.get("ENV_FILE") or (".env.production" if APP_ENV == "production" else ".env")
load_dotenv(BASE_DIR / ENV_FILE)


def env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


DEBUG = env_bool("DEBUG", True)

SECRET_KEY = os.environ.get("SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("SECRET_KEY must be set when DEBUG is off.")
    SECRET_KEY = "dev-only-insecure-key-change-me"

ALLOWED_HOSTS = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]
# Render sets this to your service's hostname (e.g. kaamsetu.onrender.com); trust it automatically.
_render_host = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "").strip()
if _render_host:
    if _render_host not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_render_host)
    if f"https://{_render_host}" not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(f"https://{_render_host}")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "allauth",
    "allauth.account",
    "allauth.mfa",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
    "accounts",
    "outreach",
    "resumes",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # serves collected static files in production
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "kaamsetu.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "kaamsetu.wsgi.application"

# Database comes from DATABASE_URL: local PostgreSQL on a laptop, Neon on Render (SSL is forced when DEBUG is off).
# SQLite is only a zero-setup fallback for development when DATABASE_URL is empty. See kaamsetu/envconfig.py.
DATABASES = {"default": build_database(os.environ.get("DATABASE_URL", ""), debug=DEBUG, sqlite_path=BASE_DIR / "db.sqlite3")}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("TIME_ZONE", "Asia/Kolkata")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

# Uploaded resumes live outside any public URL; they are served only through an owner-checked view.
PRIVATE_MEDIA_ROOT = BASE_DIR / "private_media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Authentication (django-allauth): email + password, email verification, password reset, 2FA, Google ---
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]
LOGIN_URL = "account_login"
LOGIN_REDIRECT_URL = "dashboard"
ACCOUNT_LOGOUT_REDIRECT_URL = "landing"
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_SIGNUP_FORM_CLASS = "accounts.forms.SignupExtrasForm"  # adds "Your name"
ACCOUNT_UNIQUE_EMAIL = True
# mandatory (default): nobody can sign in until they click the emailed link. optional | none: no link is required.
EMAIL_VERIFICATION = os.environ.get("EMAIL_VERIFICATION", "mandatory").strip().lower()
if EMAIL_VERIFICATION not in {"mandatory", "optional", "none"}:
    raise ImproperlyConfigured("EMAIL_VERIFICATION must be one of: mandatory, optional, none.")
ACCOUNT_EMAIL_VERIFICATION = EMAIL_VERIFICATION
ACCOUNT_EMAIL_VERIFICATION_BY_CODE_ENABLED = False
ACCOUNT_EMAIL_SUBJECT_PREFIX = "[KaamSetu] "
ACCOUNT_DEFAULT_HTTP_PROTOCOL = "http" if DEBUG else "https"
ACCOUNT_LOGIN_ON_PASSWORD_RESET = False
ACCOUNT_PREVENT_ENUMERATION = True  # "if that address has an account, we sent a link": never reveals who is registered
ACCOUNT_SESSION_REMEMBER = None  # shows a "remember me" checkbox
ACCOUNT_LOGIN_TIMEOUT = 900  # seconds allowed between the password step and the 2FA code
# allauth's own per-IP / per-key limits (defaults are used for login, signup, reset and email sending).
MFA_SUPPORTED_TYPES = ["totp", "recovery_codes"]  # authenticator app + one-time backup codes
MFA_TOTP_ISSUER = "KaamSetu"
MFA_ADAPTER = "accounts.mfa_adapter.EncryptedMfaAdapter"  # 2FA secrets are stored encrypted, like the Gmail password
SOCIALACCOUNT_ONLY = False
SOCIALACCOUNT_AUTO_SIGNUP = True
SOCIALACCOUNT_LOGIN_ON_GET = False  # starting Google sign-in is a CSRF-protected POST

# Google sign-in is switched on only when both values are set (see README: "Google sign-in").
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
SOCIALACCOUNT_PROVIDERS = {}
if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
    SOCIALACCOUNT_PROVIDERS["google"] = {
        "APP": {"client_id": GOOGLE_CLIENT_ID, "secret": GOOGLE_CLIENT_SECRET, "key": ""},
        "SCOPE": ["profile", "email"],
        "AUTH_PARAMS": {"access_type": "online"},
        "OAUTH_PKCE_ENABLED": True,
    }

# --- Email the app itself sends (verification links, password reset). Not your campaign mail. ---
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "").strip()
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "").replace(" ", "")
EMAIL_HOST = os.environ.get("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "465"))
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", EMAIL_PORT == 465)
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", EMAIL_PORT == 587)
EMAIL_TIMEOUT = 20
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL") or (f"KaamSetu <{EMAIL_HOST_USER}>" if EMAIL_HOST_USER else "KaamSetu <noreply@localhost>")
SERVER_EMAIL = DEFAULT_FROM_EMAIL
if os.environ.get("EMAIL_BACKEND"):
    EMAIL_BACKEND = os.environ["EMAIL_BACKEND"]
elif EMAIL_HOST_USER and EMAIL_HOST_PASSWORD:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
elif DEBUG or EMAIL_VERIFICATION == "none":
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"  # links are printed in the server log
else:
    raise ImproperlyConfigured("Set EMAIL_HOST_USER and EMAIL_HOST_PASSWORD: verification and password-reset emails must be deliverable.")

MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "5")) * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = MAX_UPLOAD_BYTES + 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = MAX_UPLOAD_BYTES + 1024 * 1024

# Encrypts stored Gmail app passwords (Fernet). Generate with:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
TOKEN_ENCRYPTION_KEY = os.environ.get("TOKEN_ENCRYPTION_KEY", "")

# Sending
DEFAULT_DAILY_LIMIT = int(os.environ.get("DEFAULT_DAILY_LIMIT", "40"))
SEND_BATCH_SIZE = int(os.environ.get("SEND_BATCH_SIZE", "5"))
SEND_GAP_SECONDS = float(os.environ.get("SEND_GAP_SECONDS", "1.5"))

# AI (optional, opt-in per user). AI_PROVIDER: ollama (local) | ollama_cloud (Render) | gemini | none
AI_PROVIDER = os.environ.get("AI_PROVIDER", "ollama").strip().lower()
OLLAMA_API_KEY = os.environ.get("OLLAMA_API_KEY", "").strip()
if AI_PROVIDER == "ollama_cloud":
    # Cloud reads the same two variables as local; the older OLLAMA_CLOUD_* names still work as fallbacks.
    OLLAMA_BASE_URL = (os.environ.get("OLLAMA_BASE_URL") or os.environ.get("OLLAMA_CLOUD_URL") or "https://ollama.com").strip()
    OLLAMA_MODEL = (os.environ.get("OLLAMA_MODEL") or os.environ.get("OLLAMA_CLOUD_MODEL") or "").strip()
    OLLAMA_CLOUD_URL, OLLAMA_CLOUD_MODEL = OLLAMA_BASE_URL, OLLAMA_MODEL
else:
    OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").strip()
    OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:4b").strip()
    OLLAMA_CLOUD_URL = os.environ.get("OLLAMA_CLOUD_URL", "https://ollama.com")
    OLLAMA_CLOUD_MODEL = os.environ.get("OLLAMA_CLOUD_MODEL", "")
OLLAMA_TIMEOUT = float(os.environ.get("OLLAMA_TIMEOUT", "600"))  # seconds to wait for a local reply
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")

validate_ai(
    provider=AI_PROVIDER, debug=DEBUG, base_url=OLLAMA_BASE_URL, model=OLLAMA_MODEL,
    ollama_api_key=OLLAMA_API_KEY, gemini_api_key=GEMINI_API_KEY.strip(),
)
if not DEBUG:
    validate_production(secret_key_set=bool(SECRET_KEY), token_key_set=bool(TOKEN_ENCRYPTION_KEY))

# Optional TTF font for PDF export (full Unicode). Falls back to common system fonts, then Helvetica (Latin-1 only).
PDF_FONT_PATH = os.environ.get("PDF_FONT_PATH", "")

# Optional path to the tesseract binary for image OCR.
TESSERACT_CMD = os.environ.get("TESSERACT_CMD", "")

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "same-origin"
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"kaamsetu": {"handlers": ["console"], "level": "INFO"}},
}

import re

from django import template
from django.utils.html import conditional_escape
from django.utils.safestring import mark_safe

from outreach.render import PLACEHOLDER_RE
from outreach.richtext import html_to_text, sanitize_html

register = template.Library()


@register.filter
def excerpt(body: str, n: int = 160) -> str:
    text = re.sub(r"\s+", " ", html_to_text(body or ""))
    return text[:n] + ("…" if len(text) > n else "")


@register.filter
def safe_email_html(body: str):
    """Re-sanitises stored email HTML before it is placed in a page."""
    return mark_safe(sanitize_html(body or ""))


@register.filter
def highlight_placeholders(text: str):
    """Escapes plain text and wraps {{placeholders}} in a highlight span."""
    escaped = str(conditional_escape(text or ""))
    return mark_safe(re.sub(r"\{\{[^{}]*\}\}", lambda m: f'<span class="ph">{m.group(0)}</span>', escaped))


@register.filter
def ph_html(body: str):
    """Sanitised HTML with {{placeholders}} highlighted (used in previews of unrendered templates)."""
    clean = sanitize_html(body or "")
    return mark_safe(PLACEHOLDER_RE.sub(lambda m: f'<span class="ph">{m.group(0)}</span>', clean))


_ICONS = {
    "home": '<path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>',
    "users": '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>',
    "mail": '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-10 6L2 7"/>',
    "send": '<path d="m22 2-7 20-4-9-9-4z"/><path d="M22 2 11 13"/>',
    "file": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M16 13H8M16 17H8M10 9H8"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
    "video": '<path d="m22 8-6 4 6 4V8z"/><rect x="2" y="6" width="14" height="12" rx="2"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "upload": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12"/>',
    "download": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/>',
    "sparkles": '<path d="M12 3 9.5 9.5 3 12l6.5 2.5L12 21l2.5-6.5L21 12l-6.5-2.5z"/>',
    "shield": '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "arrow": '<path d="M5 12h14M12 5l7 7-7 7"/>',
    "back": '<path d="M19 12H5M12 19l-7-7 7-7"/>',
    "trash": '<path d="M3 6h18M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6M10 11v6M14 11v6M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>',
    "copy": '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
    "pause": '<rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/>',
    "play": '<path d="m6 3 14 9-14 9z"/>',
    "refresh": '<path d="M21 12a9 9 0 1 1-3-6.7L21 8M21 3v5h-5"/>',
    "logout": '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/>',
    "list": '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
    "olist": '<path d="M10 6h11M10 12h11M10 18h11M4 6h1v4M4 10h2M6 18H4c0-1 2-2 2-3s-1-1.5-2-1"/>',
    "link": '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
    "undo": '<path d="M3 7v6h6"/><path d="M21 17a9 9 0 0 0-15-6.7L3 13"/>',
    "redo": '<path d="M21 7v6h-6"/><path d="M3 17a9 9 0 0 1 15-6.7L21 13"/>',
    "eraser": '<path d="m7 21-4.3-4.3a1 1 0 0 1 0-1.4l10-10a1 1 0 0 1 1.4 0l5.6 5.6a1 1 0 0 1 0 1.4L13 19M22 21H7M5 11l9 9"/>',
    "lock": '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    "search": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    "inbox": '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>',
    "zap": '<path d="M13 2 3 14h9l-1 8 10-12h-9z"/>',
    "target": '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>',
    "calendar": '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
    "rocket": '<path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z"/><path d="m12 15-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z"/><path d="M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5"/>',
    "alert": '<circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/>',
    "x": '<path d="M18 6 6 18M6 6l12 12"/>',
    "eye": '<path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/>',
}


@register.simple_tag
def icon(name: str, size: int = 18):
    path = _ICONS.get(name, "")
    return mark_safe(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{int(size)}" height="{int(size)}" viewBox="0 0 24 24" fill="none" '
        f'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{path}</svg>'
    )


PAUSE_TEXT = {
    "daily_limit": "Daily sending limit reached. Resume later, once the rolling 24-hour window has room again.",
    "provider_rate_limit": "Gmail asked us to slow down. Try resuming in a while.",
    "auth_error": "Gmail rejected the app password. Reconnect in Settings, then resume.",
    "user": "Paused by you.",
}


@register.filter
def pause_text(reason: str) -> str:
    return PAUSE_TEXT.get(reason or "", "Paused.")


@register.simple_tag
def status_tone(status: str) -> str:
    return {
        "accepted": "ok", "completed": "ok", "sending": "info", "confirmed": "info", "queued": "muted",
        "draft": "muted", "paused": "warn", "skipped": "muted", "failed": "bad", "cancelled": "muted",
    }.get(status, "muted")


@register.simple_tag
def vstatic(path):
    """Static URL with the file's modification time appended, so browsers fetch new JS/CSS after every change."""
    from django.contrib.staticfiles import finders
    from django.templatetags.static import static

    url = static(path)
    found = finders.find(path)
    if found:
        import os

        url += f"?v={int(os.path.getmtime(found))}"
    return url


@register.filter
def initials(value) -> str:
    """'Priya Sharma' -> 'PS'; 'careers@gamma.com' -> 'CA'."""
    text = str(value or "").strip()
    if not text:
        return "?"
    words = [w for w in re.split(r"[\s._@-]+", text) if w]
    if len(words) >= 2 and "@" not in text:
        return (words[0][0] + words[1][0]).upper()
    return text[:2].upper()


@register.filter
def hue(value) -> int:
    """A stable colour angle (0-359) for a name, so each contact keeps the same avatar colour."""
    return sum(ord(c) * (i + 7) for i, c in enumerate(str(value or "").lower())) % 360


@register.filter
def pct(value, total) -> int:
    try:
        total = float(total)
        return max(0, min(100, round(float(value) * 100 / total))) if total else 0
    except (TypeError, ValueError):
        return 0

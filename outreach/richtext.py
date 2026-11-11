"""Rich (HTML) email bodies: sanitising editor output and deriving the plain-text alternative."""

import html
import re

import nh3

ALLOWED_TAGS = {"p", "div", "br", "strong", "b", "em", "i", "u", "s", "ul", "ol", "li", "a", "h2", "h3", "blockquote"}
ALLOWED_ATTRIBUTES = {"a": {"href"}}
URL_SCHEMES = {"http", "https", "mailto"}
MAX_BODY_CHARS = 100_000


def sanitize_html(raw: str) -> str:
    """Allow-list sanitiser. Scripts, styles, event handlers, images and javascript: links are removed."""
    cleaned = nh3.clean(
        raw or "",
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes=URL_SCHEMES,
        link_rel="noopener noreferrer",
        strip_comments=True,
    )
    return cleaned.strip()


def text_to_html(text: str) -> str:
    """Plain text (blank-line separated paragraphs) to simple HTML. Used for the starter templates."""
    paras = [p for p in re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip()) if p.strip()]
    return "".join(f"<p>{html.escape(p).replace(chr(10), '<br>')}</p>" for p in paras)


_BLOCK_END = re.compile(r"</(p|div|h2|h3|blockquote|ul|ol)\s*>", re.I)


def html_to_text(body: str) -> str:
    """Readable plain-text alternative for clients that do not render HTML."""
    t = re.sub(r"<br\s*/?>", "\n", body, flags=re.I)
    t = re.sub(r"<li[^>]*>", "\n• ", t, flags=re.I)
    t = re.sub(r'<a\s[^>]*href="([^"]*)"[^>]*>(.*?)</a>', lambda m: m.group(2) if m.group(2) == m.group(1) else f"{m.group(2)} ({m.group(1)})", t, flags=re.I | re.S)
    t = _BLOCK_END.sub("\n\n", t)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t)
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def is_blank(body: str) -> bool:
    return not html_to_text(body).strip()


def email_document(body_html: str) -> str:
    """Wraps a body fragment in a minimal, email-client-friendly HTML document."""
    return (
        '<!doctype html><html><body style="font-family:Arial,Helvetica,sans-serif;font-size:15px;'
        f'line-height:1.6;color:#1d1a16">{body_html}</body></html>'
    )

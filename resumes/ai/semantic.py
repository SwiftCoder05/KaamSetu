"""Semantic selection: shrink long job descriptions (and big resumes) before they reach the model.

Why: a local 4B model reads the whole prompt on CPU before writing a word, so a 6,000-character JD full of
benefits and legal text is the main cause of timeouts. We keep only the parts of the JD that state requirements.

How (nothing here decides the ATS score or creates resume facts):
1. The JD is split into chunks (bullets / sentences) under their section heading.
2. Each chunk is scored: section prior (Requirements vs Benefits), known-skill hits, and similarity to
   (a) a "requirements" query and (b) the candidate's resume.
3. Similarity uses local Ollama embeddings (OLLAMA_EMBED_MODEL) when available, otherwise TF-IDF cosine. Both
   run on this machine; no text leaves the server. Any embedding failure falls back silently to TF-IDF.
4. The best chunks are kept in their original order until the character budget is full.

The full JD is still used by code for keyword detection and scoring (ats.py), so condensing never lowers a score.
"""

import logging
import math
import re
from collections import Counter

import httpx
from django.conf import settings

from .ats import dictionary_terms, norm

log = logging.getLogger("resumes.semantic")

REQUIREMENTS_QUERY = ("required skills, technologies, programming languages, frameworks, tools, qualifications, "
                      "experience and responsibilities for this role")
GOOD_HEADING = re.compile(r"require|qualif|skill|must|need|looking for|responsib|what you.{0,12}(do|bring|have)|you will|"
                          r"experience|prefer|nice to have|bonus|tech|stack|about the role|the role|duties", re.I)
BAD_HEADING = re.compile(r"benefit|perk|compensation|salary|about (us|the company)|who we are|our (mission|culture|values)|equal|eeo|"
                         r"diversity|how to apply|apply|disclaimer|privacy|why join|what we offer|we offer", re.I)
BAD_LINE = re.compile(r"equal opportunity|equal employment|without regard to|reasonable accommodation|"
                      r"health insurance|paid (time off|leave)|401\(?k\)?|stock options|unlimited pto|gender|veteran status", re.I)
_TOKEN = re.compile(r"[a-z0-9+#.]{2,}")
_STOPWORDS = {"the", "and", "for", "with", "you", "our", "are", "will", "have", "has", "that", "this", "from", "your", "who",
              "can", "all", "any", "not", "but", "per", "etc", "such", "also", "their", "they", "them", "its", "into"}


class EmbeddingUnavailable(Exception):
    pass


class OllamaEmbedder:
    """Embeddings from the local Ollama server (POST /api/embed). One batched call per analysis."""

    def __init__(self, base_url: str, model: str, timeout: float = 20, client: httpx.Client | None = None):
        self.base_url, self.model, self.timeout, self._client = base_url.rstrip("/"), model, timeout, client

    def embed(self, texts: list[str]) -> list[list[float]]:
        client = self._client or httpx.Client(timeout=httpx.Timeout(self.timeout, connect=3))
        try:
            res = client.post(f"{self.base_url}/api/embed", json={"model": self.model, "input": texts})
            if res.status_code >= 400:
                raise EmbeddingUnavailable(f"embed model '{self.model}' returned {res.status_code} (run: ollama pull {self.model})")
            vectors = res.json()["embeddings"]
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
            raise EmbeddingUnavailable(str(e)) from e
        finally:
            if self._client is None:
                client.close()
        if len(vectors) != len(texts):
            raise EmbeddingUnavailable("embedding count mismatch")
        return vectors


def embedder_from_settings():
    """Local Ollama only: resume and JD text must not be sent to a third party just to be ranked."""
    model = str(getattr(settings, "OLLAMA_EMBED_MODEL", "") or "").strip()
    if settings.AI_PROVIDER != "ollama" or not model:
        return None
    return OllamaEmbedder(settings.OLLAMA_BASE_URL, model)


# ---------- similarity ----------

def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(norm(text)) if t not in _STOPWORDS]


def lexical_similarities(query: str, docs: list[str]) -> list[float]:
    """TF-IDF cosine of every doc against the query. Deterministic fallback when no embedding model is available."""
    toks = [_tokens(d) for d in docs]
    q = _tokens(query)
    df = Counter(t for ts in toks + [q] for t in set(ts))
    n = len(docs) + 1
    idf = {t: math.log(1 + n / c) for t, c in df.items()}

    def vec(ts):
        c = Counter(ts)
        return {t: (1 + math.log(v)) * idf[t] for t, v in c.items()}

    qv = vec(q)
    qn = math.sqrt(sum(v * v for v in qv.values())) or 1.0
    out = []
    for ts in toks:
        dv = vec(ts)
        dn = math.sqrt(sum(v * v for v in dv.values())) or 1.0
        out.append(sum(w * dv.get(t, 0.0) for t, w in qv.items()) / (qn * dn))
    return out


def _minmax(xs: list[float]) -> list[float]:
    lo, hi = min(xs, default=0), max(xs, default=0)
    return [0.5] * len(xs) if hi - lo < 1e-9 else [(x - lo) / (hi - lo) for x in xs]


def _similarities(embedder, query: str, docs: list[str]) -> tuple[list[float], str]:
    """(similarity of each doc to the query, method). Embeddings when possible, else TF-IDF."""
    if embedder is not None and docs:
        try:
            vecs = embedder.embed([query, *docs])
            return [cosine(vecs[0], v) for v in vecs[1:]], "embeddings"
        except EmbeddingUnavailable as e:
            log.warning("semantic: embeddings unavailable, using TF-IDF (%s)", str(e)[:120])
    return lexical_similarities(query, docs), "lexical"


# ---------- job description condensing ----------

def split_jd(jd: str) -> list[dict]:
    """Chunks of {text, heading, position}. A heading is a short line without sentence punctuation, or one ending in ':'."""
    chunks, heading = [], ""
    for raw in jd.replace("\r", "").split("\n"):
        line = raw.strip()
        if not line:
            continue
        bare = re.sub(r"^[\s\-*•▪●◦·\d.)]+", "", line).strip()
        if not bare:
            continue
        is_bullet = bare != line
        if not is_bullet and len(bare) <= 60 and (bare.endswith(":") or not re.search(r"[.!?]$", bare)) and len(bare.split()) <= 8:
            heading = bare.rstrip(":")
            if len(chunks) < 2 and not chunks:  # first line is usually the job title: keep it as a chunk too
                chunks.append({"text": bare, "heading": "", "position": len(chunks)})
            continue
        parts = [bare] if len(bare) <= 300 else [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", bare) if s.strip()]
        for p in parts:
            chunks.append({"text": p, "heading": heading, "position": len(chunks)})
    return chunks


def condense_jd(jd: str, resume_text: str, *, budget: int | None = None, embedder="auto") -> tuple[str, dict]:
    """Returns (prompt-ready JD, stats). JDs already inside the budget are returned unchanged and cost no embeddings."""
    budget = budget or int(getattr(settings, "JD_PROMPT_CHARS", 3000))
    jd = jd.strip()
    stats = {"chars_before": len(jd), "chars_after": len(jd), "method": "none", "chunks_kept": None, "chunks_total": None}
    if len(jd) <= budget:
        return jd, stats
    chunks = split_jd(jd)
    if len(chunks) < 4:  # unstructured blob: nothing to select between
        return jd[:budget], {**stats, "chars_after": min(len(jd), budget), "method": "truncate"}
    if embedder == "auto":
        embedder = embedder_from_settings()

    texts = [c["text"] for c in chunks]
    req_sim, method = _similarities(embedder, REQUIREMENTS_QUERY, texts)
    res_sim, _ = _similarities(embedder if method == "embeddings" else None, resume_text[:3000], texts)
    req_n, res_n = _minmax(req_sim), _minmax(res_sim)

    scored = []
    for i, c in enumerate(chunks):
        hits = len(dictionary_terms(c["text"]))
        prior = 0.35 if GOOD_HEADING.search(c["heading"]) else -0.5 if BAD_HEADING.search(c["heading"]) else 0.0
        score = 0.35 * req_n[i] + 0.2 * res_n[i] + prior + min(hits, 4) * 0.15
        if BAD_LINE.search(c["text"]):
            score -= 0.8
        if i == 0:
            score += 5  # job title line
        scored.append((score, i))

    kept, used = set(), 0
    for score, i in sorted(scored, reverse=True):
        if score < 0:
            continue  # benefits / legal boilerplate: never worth prompt space, even when budget is left over
        size = len(chunks[i]["text"]) + 3
        if used + size > budget:
            continue
        kept.add(i)
        used += size
    lines, last_heading = [], None
    for i in sorted(kept):
        c = chunks[i]
        if c["heading"] and c["heading"] != last_heading:
            lines.append(f"{c['heading']}:")
            last_heading = c["heading"]
        lines.append(f"- {c['text']}")
    out = "\n".join(lines)
    stats.update(chars_after=len(out), method=method, chunks_kept=len(kept), chunks_total=len(chunks))
    log.info("semantic: JD condensed %d -> %d chars (%d/%d chunks, %s)", stats["chars_before"], stats["chars_after"],
             len(kept), len(chunks), method)
    return out, stats


# ---------- resume evidence selection ----------

def select_bullets(bullets: list[tuple[tuple, str]], jd_text: str, *, keep: int, embedder="auto") -> tuple[set, str]:
    """Picks the `keep` bullets most relevant to the JD. `bullets` is [(key, text)]; returns (kept keys, method).
    Selection only hides text from the prompt; indexes of the shown bullets are unchanged."""
    if len(bullets) <= keep:
        return {k for k, _ in bullets}, "none"
    if embedder == "auto":
        embedder = embedder_from_settings()
    sims, method = _similarities(embedder, jd_text, [t for _, t in bullets])
    ranked = sorted(zip(sims, range(len(bullets))), reverse=True)
    return {bullets[i][0] for _, i in ranked[:keep]}, method

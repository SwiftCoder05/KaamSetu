export const MIN_SECRET_LENGTH = 32;
export const MAX_BODY_BYTES = 4 * 1024 * 1024; // Vercel rejects request bodies above 4.5 MB
export const MAX_ATTACHMENT_BYTES = 3 * 1024 * 1024; // decoded size; base64 adds about a third
export const SIGNATURE_WINDOW_SECONDS = 300;
export const OVERALL_TIMEOUT_MS = 25_000; // inside vercel.json maxDuration (30 s)
export const IDEMPOTENCY_DONE_TTL_S = 24 * 3600;
export const IDEMPOTENCY_PENDING_TTL_S = 120;

export function intEnv(env, name, fallback) {
  const n = Number.parseInt(env[name] ?? '', 10);
  return Number.isFinite(n) && n > 0 ? n : fallback;
}

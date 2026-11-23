import { createHash } from 'node:crypto';
import nodemailer from 'nodemailer';
import { authenticate } from './auth.js';
import {
  IDEMPOTENCY_DONE_TTL_S, IDEMPOTENCY_PENDING_TTL_S, MAX_BODY_BYTES, intEnv,
} from './config.js';
import { openPassword } from './envelope.js';
import { ServiceError, classifyError, createTransport, withTimeout } from './smtp.js';
import { defaultStore } from './store.js';
import { validateSend, validateVerify } from './validate.js';

const sha = (s) => createHash('sha256').update(s).digest('hex');

function json(status, body, extra = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json', 'cache-control': 'no-store', ...extra },
  });
}

function failure(requestId, err) {
  const status = { auth: 422, permanent: 422, rate_limit: 429 }[err.kind] ?? (err.code === 'timeout' ? 504 : 502);
  const headers = err.retryAfter ? { 'retry-after': String(err.retryAfter) } : {};
  return json(status, {
    ok: false, requestId, error: { kind: err.kind, code: err.code, message: err.message, retryAfter: err.retryAfter ?? null },
  }, headers);
}

export function defaultDeps() {
  return { env: process.env, store: defaultStore(process.env), nodemailer, lookup: undefined, now: () => Date.now() };
}

/** Shared front door: size limit, signature, JSON parsing. Nothing past this point runs for unauthenticated callers. */
async function gate(request, deps) {
  if (Number(request.headers.get('content-length') ?? 0) > MAX_BODY_BYTES) return { res: json(413, { ok: false, error: { code: 'too_large' } }) };
  const raw = await request.text();
  if (Buffer.byteLength(raw) > MAX_BODY_BYTES) return { res: json(413, { ok: false, error: { code: 'too_large' } }) };
  const auth = authenticate(request.headers, raw, deps.env.EMAIL_SERVICE_SECRET, deps.now());
  if (!auth.ok) return { res: json(auth.status, { ok: false, error: { code: auth.code } }) };
  try {
    return { body: JSON.parse(raw) };
  } catch {
    return { res: json(400, { ok: false, error: { code: 'invalid_json' } }) };
  }
}

async function rateLimited(deps, bucket, smtp, limit) {
  const account = sha(`${smtp.host}|${smtp.username}`.toLowerCase()).slice(0, 24);
  const key = `rl:${bucket}:${account}:${Math.floor(deps.now() / 60000)}`;
  try {
    return (await deps.store.incr(key, 70)) > limit;
  } catch {
    return false; // a store outage must not block mail; auth and validation still apply
  }
}

function openSmtpPassword(deps, body) {
  try {
    return openPassword(deps.env.EMAIL_SERVICE_SECRET, body.requestId, body.smtp.passwordEnc);
  } catch {
    throw new ServiceError('permanent', 'bad_credentials_envelope', 'The credentials could not be read. Check that both services share the same secret.');
  }
}

export async function handleSend(request, deps = defaultDeps()) {
  const g = await gate(request, deps);
  if (g.res) return g.res;
  const v = validateSend(g.body);
  if (v.errors) return json(400, { ok: false, requestId: g.body?.requestId ?? null, error: { kind: 'permanent', code: 'invalid_request', message: v.errors.join('; ') } });
  const { requestId, smtp, email } = v.value;

  if (await rateLimited(deps, 'send', smtp, intEnv(deps.env, 'RATE_LIMIT_SEND_PER_MIN', 30))) {
    return failure(requestId, new ServiceError('rate_limit', 'service_rate_limited', 'Too many emails for this account this minute.', { retryAfter: 30 }));
  }

  // Idempotency: one requestId is one email. Retrying a finished request returns the original result;
  // retrying while the first attempt is still running is refused instead of sending twice.
  const idemKey = `idem:${sha(requestId)}`;
  const fingerprint = sha(JSON.stringify([email.from, email.to, email.cc ?? [], email.bcc ?? [], email.subject]));
  const claimed = await deps.store.setIfAbsent(idemKey, JSON.stringify({ s: 'pending', fp: fingerprint }), IDEMPOTENCY_PENDING_TTL_S);
  if (!claimed) {
    const prior = JSON.parse((await deps.store.get(idemKey)) ?? 'null');
    if (!prior) return failure(requestId, new ServiceError('transient', 'in_progress', 'A previous attempt is still finishing. Retry shortly.', { retryAfter: 5 }));
    if (prior.fp !== fingerprint) return json(409, { ok: false, requestId, error: { kind: 'permanent', code: 'idempotency_conflict', message: 'This requestId was already used for a different message.' } });
    if (prior.s === 'done') return json(200, { ok: true, requestId, duplicate: true, messageId: prior.messageId, accepted: prior.accepted, rejected: prior.rejected });
    return failure(requestId, new ServiceError('transient', 'in_progress', 'A previous attempt is still finishing. Retry shortly.', { retryAfter: 5 }));
  }

  let transport;
  try {
    const password = openSmtpPassword(deps, v.value);
    transport = await createTransport(deps.nodemailer, smtp, password, deps.lookup);
    const info = await withTimeout(transport.sendMail({
      from: email.fromName ? { name: email.fromName, address: email.from } : email.from,
      to: email.to,
      cc: email.cc?.length ? email.cc : undefined,
      bcc: email.bcc?.length ? email.bcc : undefined,
      subject: email.subject,
      text: email.text,
      html: email.html,
      attachments: (email.attachments ?? []).map((a) => ({ filename: a.filename, contentType: a.contentType, content: Buffer.from(a.contentBase64, 'base64') })),
    }));
    const result = { messageId: info.messageId ?? null, accepted: info.accepted ?? [], rejected: info.rejected ?? [] };
    if (result.rejected.length && !result.accepted.length) throw new ServiceError('permanent', 'recipient_rejected', 'The mail server rejected every recipient.');
    await deps.store.set(idemKey, JSON.stringify({ s: 'done', fp: fingerprint, ...result }), IDEMPOTENCY_DONE_TTL_S);
    return json(200, { ok: true, requestId, duplicate: false, ...result });
  } catch (err) {
    // Failed sends are not remembered, so the caller may retry the same requestId.
    // A timeout is the exception: the server may have accepted the message, so the pending marker is left to expire.
    const e = classifyError(err);
    if (e.code !== 'timeout') await deps.store.del(idemKey).catch(() => {});
    return failure(requestId, e);
  } finally {
    transport?.close();
  }
}

export async function handleVerify(request, deps = defaultDeps()) {
  const g = await gate(request, deps);
  if (g.res) return g.res;
  const v = validateVerify(g.body);
  if (v.errors) return json(400, { ok: false, requestId: g.body?.requestId ?? null, error: { kind: 'permanent', code: 'invalid_request', message: v.errors.join('; ') } });
  const { requestId, smtp } = v.value;

  if (await rateLimited(deps, 'verify', smtp, intEnv(deps.env, 'RATE_LIMIT_VERIFY_PER_MIN', 10))) {
    return failure(requestId, new ServiceError('rate_limit', 'service_rate_limited', 'Too many connection tests. Wait a minute.', { retryAfter: 30 }));
  }
  let transport;
  try {
    const password = openSmtpPassword(deps, v.value);
    transport = await createTransport(deps.nodemailer, smtp, password, deps.lookup);
    await withTimeout(transport.verify()); // connects, negotiates TLS and authenticates; sends nothing
    return json(200, { ok: true, requestId });
  } catch (err) {
    return failure(requestId, classifyError(err));
  } finally {
    transport?.close();
  }
}

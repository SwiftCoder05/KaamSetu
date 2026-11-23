import { createHmac, timingSafeEqual } from 'node:crypto';
import { MIN_SECRET_LENGTH, SIGNATURE_WINDOW_SECONDS } from './config.js';

export function sign(secret, timestamp, rawBody) {
  return createHmac('sha256', secret).update(`${timestamp}.${rawBody}`).digest('hex');
}

/**
 * Verifies the `x-kaamsetu-timestamp` and `x-kaamsetu-signature` headers.
 * Returns { ok: true } or { ok: false, status, code }. Every caller-side failure is the same 401,
 * so a probe learns nothing about which check failed.
 */
export function authenticate(headers, rawBody, secret, nowMs = Date.now()) {
  if (!secret || secret.length < MIN_SECRET_LENGTH) {
    return { ok: false, status: 500, code: 'service_not_configured' }; // fail closed
  }
  const ts = headers.get('x-kaamsetu-timestamp') ?? '';
  const sig = headers.get('x-kaamsetu-signature') ?? '';
  const unauthorized = { ok: false, status: 401, code: 'unauthorized' };
  if (!/^\d{10,13}$/.test(ts) || !/^[0-9a-f]{64}$/.test(sig)) return unauthorized;
  if (Math.abs(nowMs / 1000 - Number(ts)) > SIGNATURE_WINDOW_SECONDS) return unauthorized;
  const expected = Buffer.from(sign(secret, ts, rawBody), 'hex');
  const given = Buffer.from(sig, 'hex');
  if (expected.length !== given.length || !timingSafeEqual(expected, given)) return unauthorized;
  return { ok: true };
}

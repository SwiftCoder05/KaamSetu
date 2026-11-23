import { createCipheriv, createDecipheriv, hkdfSync, randomBytes } from 'node:crypto';

// The SMTP password never travels as plaintext JSON. Render encrypts it with a key derived from the shared
// secret (HKDF-SHA256) using AES-256-GCM. The requestId is authenticated as AAD, so a blob cannot be replayed
// inside a different request. Wire format: base64( iv[12] | ciphertext | tag[16] ).
const INFO = Buffer.from('kaamsetu-smtp-password-v1');

function deriveKey(secret) {
  return Buffer.from(hkdfSync('sha256', Buffer.from(secret), Buffer.alloc(0), INFO, 32));
}

export function openPassword(secret, requestId, blob) {
  const raw = Buffer.from(String(blob), 'base64');
  if (raw.length < 12 + 16 + 1) throw new Error('bad_envelope');
  const decipher = createDecipheriv('aes-256-gcm', deriveKey(secret), raw.subarray(0, 12));
  decipher.setAAD(Buffer.from(requestId));
  decipher.setAuthTag(raw.subarray(raw.length - 16));
  return Buffer.concat([decipher.update(raw.subarray(12, raw.length - 16)), decipher.final()]).toString('utf8');
}

// For tests and local tooling. Production encryption happens in Django (outreach/relay.py).
export function sealPassword(secret, requestId, password) {
  const iv = randomBytes(12);
  const c = createCipheriv('aes-256-gcm', deriveKey(secret), iv);
  c.setAAD(Buffer.from(requestId));
  const ct = Buffer.concat([c.update(password, 'utf8'), c.final()]);
  return Buffer.concat([iv, ct, c.getAuthTag()]).toString('base64');
}

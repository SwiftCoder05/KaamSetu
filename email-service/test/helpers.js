import { sign } from '../lib/auth.js';
import { sealPassword } from '../lib/envelope.js';
import { MemoryStore } from '../lib/store.js';

export const SECRET = 'test-secret-test-secret-test-secret-123456';
export const PUBLIC_LOOKUP = async () => [{ address: '203.0.113.10', family: 4 }];

/** A fake nodemailer that records every transport it was asked to build and every message it was asked to send. */
export function fakeNodemailer({ sendMail, verify } = {}) {
  const calls = { transports: [], sent: [], closed: 0, verified: 0 };
  return {
    calls,
    createTransport(options) {
      calls.transports.push(options);
      return {
        async sendMail(msg) {
          calls.sent.push({ msg, user: options.auth.user, pass: options.auth.pass });
          if (sendMail) return sendMail(msg, options);
          return { messageId: `<id-${calls.sent.length}@test>`, accepted: [].concat(msg.to), rejected: [] };
        },
        async verify() {
          calls.verified += 1;
          if (verify) return verify(options);
          return true;
        },
        close() {
          calls.closed += 1;
        },
      };
    },
  };
}

export function makeDeps(over = {}) {
  const nodemailer = over.nodemailer ?? fakeNodemailer();
  return {
    env: { EMAIL_SERVICE_SECRET: SECRET, ...(over.env ?? {}) },
    store: over.store ?? new MemoryStore(),
    nodemailer,
    lookup: over.lookup ?? PUBLIC_LOOKUP,
    now: over.now ?? (() => Date.now()),
  };
}

export function sendBody({ requestId = 'req-00000001', username = 'a@gmail.com', password = 'abcdefghijklmnop', to = ['hr@acme.com'], host = 'smtp.gmail.com', port = 465, secure = true, subject = 'Hello', from } = {}) {
  return {
    requestId,
    smtp: { host, port, secure, username, passwordEnc: sealPassword(SECRET, requestId, password) },
    email: { from: from ?? username, fromName: 'Asha', to, subject, html: '<p>Hi</p>', text: 'Hi' },
  };
}

export function signedRequest(body, { secret = SECRET, ts = Math.floor(Date.now() / 1000), sig, path = '/api/send-email' } = {}) {
  const raw = typeof body === 'string' ? body : JSON.stringify(body);
  return new Request(`https://svc.example${path}`, {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      'x-kaamsetu-timestamp': String(ts),
      'x-kaamsetu-signature': sig ?? sign(secret, ts, raw),
    },
    body: raw,
  });
}

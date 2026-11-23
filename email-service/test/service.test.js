import assert from 'node:assert/strict';
import test from 'node:test';
import { handleSend, handleVerify } from '../lib/handlers.js';
import { sealPassword } from '../lib/envelope.js';
import { MemoryStore, UpstashStore } from '../lib/store.js';
import { ServiceError, classifyError, isPrivateAddress, withTimeout } from '../lib/smtp.js';
import { SECRET, fakeNodemailer, makeDeps, sendBody, signedRequest } from './helpers.js';

const send = (body, deps, opts) => handleSend(signedRequest(body, opts), deps);
const j = (res) => res.json();

// ---------- 12. unauthorized requests are rejected ----------
test('rejects a request with no signature headers', async () => {
  const deps = makeDeps();
  const res = await handleSend(new Request('https://x/api/send-email', { method: 'POST', body: JSON.stringify(sendBody()) }), deps);
  assert.equal(res.status, 401);
  assert.equal(deps.nodemailer.calls.transports.length, 0);
});

test('rejects a wrong signature, a wrong secret and a tampered body', async () => {
  const deps = makeDeps();
  assert.equal((await send(sendBody(), deps, { sig: 'a'.repeat(64) })).status, 401);
  assert.equal((await send(sendBody(), deps, { secret: 'x'.repeat(40) })).status, 401);
  const good = signedRequest(sendBody());
  const tampered = new Request(good.url, { method: 'POST', headers: good.headers, body: JSON.stringify(sendBody({ to: ['evil@x.com'] })) });
  assert.equal((await handleSend(tampered, deps)).status, 401);
  assert.equal(deps.nodemailer.calls.sent.length, 0);
});

test('rejects stale and future timestamps (replay window)', async () => {
  const deps = makeDeps();
  const now = Math.floor(Date.now() / 1000);
  assert.equal((await send(sendBody(), deps, { ts: now - 3600 })).status, 401);
  assert.equal((await send(sendBody(), deps, { ts: now + 3600 })).status, 401);
});

test('fails closed when the service secret is missing or too short', async () => {
  for (const secret of [undefined, '', 'short']) {
    const deps = makeDeps({ env: { EMAIL_SERVICE_SECRET: secret } });
    const res = await send(sendBody(), deps);
    assert.equal(res.status, 500);
    assert.equal(deps.nodemailer.calls.transports.length, 0);
  }
});

test('verify endpoint is protected the same way', async () => {
  const deps = makeDeps();
  const body = sendBody();
  const res = await handleVerify(signedRequest({ requestId: body.requestId, smtp: body.smtp }, { sig: 'b'.repeat(64) }), deps);
  assert.equal(res.status, 401);
  assert.equal(deps.nodemailer.calls.verified, 0);
});

// ---------- 3-6. each user sends with their own account ----------
test('two users send through their own SMTP accounts (A: Gmail, B: Outlook) and never cross', async () => {
  const deps = makeDeps();
  const a = sendBody({ requestId: 'req-userA-0001', username: 'userA@gmail.com', password: 'aaaaaaaaaaaaaaaa', to: ['r1@acme.com'] });
  const b = sendBody({ requestId: 'req-userB-0001', username: 'userB@outlook.com', password: 'bbbbbbbbbbbbbbbb', host: 'smtp.office365.com', port: 587, secure: false, to: ['r2@globex.com'] });
  assert.equal((await send(a, deps)).status, 200);
  assert.equal((await send(b, deps)).status, 200);
  const [sa, sb] = deps.nodemailer.calls.sent;
  assert.deepEqual([sa.user, sa.pass, sa.msg.from.address, sa.msg.to], ['userA@gmail.com', 'aaaaaaaaaaaaaaaa', 'userA@gmail.com', ['r1@acme.com']]);
  assert.deepEqual([sb.user, sb.pass, sb.msg.from.address, sb.msg.to], ['userB@outlook.com', 'bbbbbbbbbbbbbbbb', 'userB@outlook.com', ['r2@globex.com']]);
  assert.equal(deps.nodemailer.calls.closed, 2);
});

test('returns the SMTP message id and never echoes credentials', async () => {
  const deps = makeDeps();
  const res = await send(sendBody({ password: 'super-secret-pass-1' }), deps);
  const text = JSON.stringify(await j(res));
  assert.match(text, /"messageId":"<id-1@test>"/);
  assert.ok(!text.includes('super-secret-pass-1'));
});

test('From must be the authenticated account', async () => {
  const deps = makeDeps();
  const res = await send(sendBody({ username: 'a@gmail.com', from: 'ceo@bigcorp.com' }), deps);
  assert.equal(res.status, 400);
  assert.match((await j(res)).error.message, /connected account/);
  assert.equal(deps.nodemailer.calls.sent.length, 0);
});

test('From comparison is case-insensitive', async () => {
  const deps = makeDeps();
  assert.equal((await send(sendBody({ username: 'Asha@Gmail.com', from: 'asha@gmail.com' }), deps)).status, 200);
});

// ---------- validation ----------
test('only ports 465 (TLS) and 587 (STARTTLS) are accepted; port 25 is refused', async () => {
  const deps = makeDeps();
  for (const [port, secure] of [[25, false], [2525, false], [465, false], [587, true]]) {
    const res = await send(sendBody({ port, secure }), deps);
    assert.equal(res.status, 400, `port ${port} secure ${secure}`);
  }
  assert.equal(deps.nodemailer.calls.transports.length, 0);
});

test('rejects bad hosts, bad addresses, header injection and empty bodies', async () => {
  const deps = makeDeps();
  const bad = [
    sendBody({ host: '127.0.0.1' }),
    sendBody({ host: 'localhost' }),
    sendBody({ to: ['not-an-email'] }),
    sendBody({ to: [] }),
    sendBody({ subject: 'Hi\r\nBcc: victim@x.com' }),
    { ...sendBody(), email: { ...sendBody().email, html: undefined, text: undefined } },
    { ...sendBody(), requestId: 'short' },
  ];
  for (const b of bad) assert.equal((await send(b, deps)).status, 400, JSON.stringify(b.email?.subject ?? b.smtp.host));
  assert.equal(deps.nodemailer.calls.sent.length, 0);
});

test('malformed JSON is a 400 after authentication', async () => {
  const res = await send('{not json', makeDeps());
  assert.equal(res.status, 400);
});

// ---------- 5/10. SMTP transport options ----------
test('465 uses implicit TLS; 587 requires STARTTLS; certificates are checked against the hostname', async () => {
  const deps = makeDeps();
  await send(sendBody({ requestId: 'req-tls-000001' }), deps);
  await send(sendBody({ requestId: 'req-tls-000002', host: 'smtp.office365.com', port: 587, secure: false }), deps);
  const [t465, t587] = deps.nodemailer.calls.transports;
  assert.equal(t465.secure, true);
  assert.equal(t587.secure, false);
  assert.equal(t587.requireTLS, true);
  assert.equal(t465.tls.servername, 'smtp.gmail.com');
  assert.equal(t465.host, '203.0.113.10'); // pinned to the vetted IP
  assert.ok(t465.connectionTimeout <= 10_000 && t465.socketTimeout <= 20_000);
});

// ---------- SSRF ----------
test('refuses hosts that resolve to private or metadata addresses', async () => {
  for (const address of ['127.0.0.1', '10.0.0.5', '169.254.169.254', '192.168.1.1', '::1', 'fd00::1', '::ffff:127.0.0.1']) {
    const deps = makeDeps({ lookup: async () => [{ address, family: address.includes(':') ? 6 : 4 }] });
    const res = await send(sendBody(), deps);
    assert.equal((await j(res)).error.code, 'host_not_allowed', address);
    assert.equal(deps.nodemailer.calls.sent.length, 0);
  }
  assert.equal(isPrivateAddress('203.0.113.10'), false);
  assert.equal(isPrivateAddress('2606:4700::1111'), false);
});

// ---------- 10. idempotency ----------
test('a repeated requestId sends exactly one email', async () => {
  const deps = makeDeps();
  const body = sendBody();
  const first = await j(await send(body, deps));
  const second = await j(await send(body, deps));
  assert.equal(deps.nodemailer.calls.sent.length, 1);
  assert.equal(first.duplicate, false);
  assert.equal(second.duplicate, true);
  assert.equal(second.messageId, first.messageId);
});

test('a concurrent duplicate is refused while the first is still sending', async () => {
  let release;
  const gate = new Promise((r) => (release = r));
  const nodemailer = fakeNodemailer({ sendMail: async (m) => { await gate; return { messageId: '<x@t>', accepted: m.to, rejected: [] }; } });
  const deps = makeDeps({ nodemailer });
  const body = sendBody();
  const first = send(body, deps);
  await new Promise((r) => setTimeout(r, 20));
  const dup = await send(body, deps);
  assert.equal((await j(dup)).error.code, 'in_progress');
  release();
  assert.equal((await first).status, 200);
  assert.equal(nodemailer.calls.sent.length, 1);
});

test('reusing a requestId for a different message is a conflict, not a send', async () => {
  const deps = makeDeps();
  await send(sendBody(), deps);
  const res = await send(sendBody({ to: ['other@acme.com'] }), deps);
  assert.equal(res.status, 409);
  assert.equal(deps.nodemailer.calls.sent.length, 1);
});

test('a failed send can be retried with the same requestId', async () => {
  let n = 0;
  const nodemailer = fakeNodemailer({ sendMail: async (m) => {
    if (++n === 1) throw Object.assign(new Error('Connection closed'), { code: 'ECONNECTION' });
    return { messageId: '<ok@t>', accepted: m.to, rejected: [] };
  } });
  const deps = makeDeps({ nodemailer });
  const body = sendBody();
  const first = await send(body, deps);
  assert.equal(first.status, 502);
  assert.equal((await send(body, deps)).status, 200);
});

// ---------- 7/8/11. error handling ----------
test('invalid credentials come back as a structured auth error without raw server text', async () => {
  const nodemailer = fakeNodemailer({ sendMail: async () => { throw Object.assign(new Error('Invalid login: 535 5.7.8 Username and Password not accepted for abcdefghijklmnop'), { code: 'EAUTH', responseCode: 535, response: '535 5.7.8 Username and Password not accepted' }); } });
  const res = await send(sendBody({ password: 'abcdefghijklmnop' }), makeDeps({ nodemailer }));
  const body = await j(res);
  assert.equal(res.status, 422);
  assert.equal(body.error.kind, 'auth');
  assert.ok(!JSON.stringify(body).includes('abcdefghijklmnop'));
});

test('classifies unsupported SMTP AUTH, wrong ports, timeouts and limits', () => {
  const c = (o) => classifyError(Object.assign(new Error(o.message ?? 'x'), o));
  assert.equal(c({ code: 'EAUTH', responseCode: 535, response: '535 5.7.139 Authentication unsuccessful, basic authentication is disabled' }).code, 'smtp_auth_unsupported');
  assert.equal(c({ code: 'EAUTH', responseCode: 535, response: '5.7.30 Basic authentication is not supported for Client Submission' }).code, 'smtp_auth_unsupported');
  assert.equal(c({ code: 'EAUTH', responseCode: 534, response: '534 5.7.9 Application-specific password required' }).code, 'app_password_required');
  assert.equal(c({ code: 'EAUTH', responseCode: 535, response: '535 nope' }).code, 'invalid_credentials');
  assert.equal(c({ code: 'ESOCKET', message: 'error:0A00010B:SSL routines:wrong version number' }).code, 'tls_mismatch');
  assert.equal(c({ code: 'ETIMEDOUT', message: 'Connection timeout' }).code, 'timeout');
  assert.equal(c({ code: 'ECONNREFUSED' }).code, 'connection_failed');
  assert.equal(c({ responseCode: 550, response: '550 5.4.5 Daily user sending quota exceeded' }).kind, 'rate_limit');
  assert.equal(c({ code: 'EENVELOPE' }).kind, 'permanent');
  assert.equal(c({ responseCode: 421, response: '421 4.7.0 Try again later, closing connection' }).kind, 'rate_limit');
  assert.equal(c({ responseCode: 451, response: '451 local error in processing' }).kind, 'transient');
});

test('an SMTP connection timeout is a 504 and does not leave the transport open', async () => {
  const nodemailer = fakeNodemailer({ sendMail: async () => { throw Object.assign(new Error('Connection timeout'), { code: 'ETIMEDOUT' }); } });
  const res = await send(sendBody(), makeDeps({ nodemailer }));
  assert.equal(res.status, 504);
  assert.equal(nodemailer.calls.closed, 1);
});

test('withTimeout rejects a hung operation', async () => {
  await assert.rejects(withTimeout(new Promise(() => {}), 30), (e) => e instanceof ServiceError && e.code === 'timeout');
});

test('a wrong shared secret or replayed envelope cannot decrypt the password', async () => {
  const deps = makeDeps();
  const body = sendBody();
  body.smtp.passwordEnc = sealPassword('another-secret-another-secret-another', body.requestId, 'pw');
  assert.equal((await j(await send(body, deps))).error.code, 'bad_credentials_envelope');
  const b2 = sendBody({ requestId: 'req-replay-0001' });
  b2.smtp.passwordEnc = sealPassword(SECRET, 'req-other-00001', 'pw'); // sealed for a different request
  assert.equal((await j(await send(b2, deps))).error.code, 'bad_credentials_envelope');
  assert.equal(deps.nodemailer.calls.sent.length, 0);
});

// ---------- rate limits ----------
test('rate-limits sends per SMTP account, not globally', async () => {
  const deps = makeDeps({ env: { RATE_LIMIT_SEND_PER_MIN: '2' }, now: () => 1_700_000_000_000 });
  const ids = (n) => `req-rate-${String(n).padStart(6, '0')}`;
  const r = [];
  for (let i = 1; i <= 3; i += 1) r.push((await send(sendBody({ requestId: ids(i), subject: `S${i}` }), deps, { ts: 1_700_000_000 })).status);
  assert.deepEqual(r, [200, 200, 429]);
  const other = await send(sendBody({ requestId: ids(9), username: 'b@gmail.com' }), deps, { ts: 1_700_000_000 });
  assert.equal(other.status, 200);
});

// ---------- verify ----------
test('verify authenticates against the server and sends nothing', async () => {
  const deps = makeDeps();
  const body = sendBody();
  const res = await handleVerify(signedRequest({ requestId: body.requestId, smtp: body.smtp }), deps);
  assert.equal(res.status, 200);
  assert.equal(deps.nodemailer.calls.verified, 1);
  assert.equal(deps.nodemailer.calls.sent.length, 0);
  assert.equal(deps.nodemailer.calls.closed, 1);
});

test('verify reports rejected credentials', async () => {
  const nodemailer = fakeNodemailer({ verify: async () => { throw Object.assign(new Error('Invalid login'), { code: 'EAUTH', responseCode: 535 }); } });
  const body = sendBody();
  const res = await handleVerify(signedRequest({ requestId: body.requestId, smtp: body.smtp }), makeDeps({ nodemailer }));
  assert.equal(res.status, 422);
  assert.equal((await j(res)).error.code, 'invalid_credentials');
});

// ---------- stores ----------
test('MemoryStore: expiry, setIfAbsent and counters', async () => {
  let t = 0;
  const s = new MemoryStore(() => t);
  assert.equal(await s.setIfAbsent('k', 'v', 10), true);
  assert.equal(await s.setIfAbsent('k', 'w', 10), false);
  t = 11_000;
  assert.equal(await s.get('k'), null);
  assert.equal(await s.incr('c', 60), 1);
  assert.equal(await s.incr('c', 60), 2);
});

test('UpstashStore speaks the REST protocol and honours NX', async () => {
  const seen = [];
  const fetchImpl = async (url, init) => {
    seen.push([url, init.headers.Authorization, JSON.parse(init.body)]);
    return new Response(JSON.stringify({ result: seen.length === 1 ? 'OK' : null }));
  };
  const s = new UpstashStore('https://redis.example/', 'tok', fetchImpl);
  assert.equal(await s.setIfAbsent('k', 'v', 5), true);
  assert.equal(await s.setIfAbsent('k', 'v', 5), false);
  assert.deepEqual(seen[0], ['https://redis.example', 'Bearer tok', ['SET', 'k', 'v', 'EX', 5, 'NX']]);
});

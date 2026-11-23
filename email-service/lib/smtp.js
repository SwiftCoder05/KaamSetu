import dns from 'node:dns/promises';
import { BlockList, isIP } from 'node:net';
import { OVERALL_TIMEOUT_MS } from './config.js';

/** A failure that is safe to show: `message` never contains credentials or raw server text. */
export class ServiceError extends Error {
  constructor(kind, code, message, { retryAfter } = {}) {
    super(message);
    this.kind = kind; // transient | rate_limit | auth | permanent
    this.code = code;
    this.retryAfter = retryAfter;
  }
}

// ---- SSRF guard: the service connects to a host a user typed, so it must never reach private networks. ----
const blocked = new BlockList();
for (const [net, bits] of [
  ['0.0.0.0', 8], ['10.0.0.0', 8], ['100.64.0.0', 10], ['127.0.0.0', 8], ['169.254.0.0', 16],
  ['172.16.0.0', 12], ['192.0.0.0', 24], ['192.168.0.0', 16], ['198.18.0.0', 15], ['224.0.0.0', 4], ['240.0.0.0', 4],
]) blocked.addSubnet(net, bits, 'ipv4');
for (const [net, bits] of [['::', 128], ['::1', 128], ['fc00::', 7], ['fe80::', 10], ['ff00::', 8]]) blocked.addSubnet(net, bits, 'ipv6');

export function isPrivateAddress(ip) {
  const mapped = /^::ffff:(\d+\.\d+\.\d+\.\d+)$/i.exec(ip);
  if (mapped) return blocked.check(mapped[1], 'ipv4');
  const family = isIP(ip);
  return family === 0 || blocked.check(ip, family === 6 ? 'ipv6' : 'ipv4');
}

/** Resolves once, rejects private targets, and returns the address to connect to (pinned against DNS rebinding). */
export async function resolvePublicHost(host, lookup = dns.lookup) {
  let addrs;
  try {
    addrs = await lookup(host, { all: true });
  } catch {
    throw new ServiceError('permanent', 'host_not_found', 'The SMTP host name could not be found. Check the spelling.');
  }
  if (!addrs.length || addrs.some((a) => isPrivateAddress(a.address))) {
    throw new ServiceError('permanent', 'host_not_allowed', 'That SMTP host is not allowed.');
  }
  return addrs[0].address;
}

// ---- transport -------------------------------------------------------------------------------------------
export async function createTransport(nodemailer, smtp, password, lookup) {
  const address = await resolvePublicHost(smtp.host, lookup);
  return nodemailer.createTransport({
    host: address,
    port: smtp.port,
    secure: smtp.secure, // 465: implicit TLS
    requireTLS: !smtp.secure, // 587: refuse to continue unless STARTTLS succeeds (no plaintext fallback)
    auth: { user: smtp.username, pass: password },
    tls: { servername: smtp.host, minVersion: 'TLSv1.2' },
    connectionTimeout: 10_000,
    greetingTimeout: 10_000,
    socketTimeout: 20_000,
    disableFileAccess: true,
    disableUrlAccess: true,
    logger: false,
  });
}

/** Rejects after `ms`. The caller closes the transport, so nothing keeps running after the response. */
export function withTimeout(promise, ms = OVERALL_TIMEOUT_MS) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new ServiceError('transient', 'timeout', 'The mail server took too long to respond.')), ms);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

// ---- error classification --------------------------------------------------------------------------------
const SMTP_AUTH_DISABLED = /5\.7\.30|5\.7\.139|SmtpClientAuthentication|basic authentication is (not supported|disabled)|authentication.*(disabled|not enabled)|less secure/i;
const APP_PASSWORD = /5\.7\.8|5\.7\.9|application-specific password|app password/i;
const DAILY_LIMIT = /5\.4\.5|daily (user )?sending (limit|quota)|sending limit exceeded|5\.2\.3/i;
const SLOW_DOWN = /4\.7\.0|rate ?limit|too many|throttl|try again later/i;

export function classifyError(err) {
  if (err instanceof ServiceError) return err;
  const code = err?.code;
  const rc = Number(err?.responseCode) || 0;
  const text = `${err?.response ?? ''} ${err?.message ?? ''}`;

  if (code === 'EAUTH' || rc === 535 || rc === 534 || rc === 530) {
    if (SMTP_AUTH_DISABLED.test(text)) {
      return new ServiceError('auth', 'smtp_auth_unsupported',
        'This account does not allow SMTP sign-in. Ask your provider or admin to enable SMTP AUTH, or use an app password.');
    }
    if (APP_PASSWORD.test(text)) {
      return new ServiceError('auth', 'app_password_required', 'The provider requires an app password, not your normal password.');
    }
    return new ServiceError('auth', 'invalid_credentials', 'The mail server rejected the username or password.');
  }
  if (code === 'EENVELOPE') {
    return new ServiceError('permanent', 'recipient_rejected', 'The mail server rejected a recipient address.');
  }
  if (code === 'ETIMEDOUT' || code === 'ESOCKETTIMEDOUT' || /timeout|timed out/i.test(err?.message ?? '')) {
    return new ServiceError('transient', 'timeout', 'Could not reach the mail server in time. Check the host and port.');
  }
  if (code === 'ESOCKET' && /wrong version number|ssl|tls|handshake|unknown protocol/i.test(text)) {
    return new ServiceError('permanent', 'tls_mismatch',
      'The server did not accept this security mode. Use SSL/TLS with port 465 or STARTTLS with port 587.');
  }
  if (code === 'ETLS' || code === 'ESTARTTLS') {
    return new ServiceError('permanent', 'tls_failed', 'A secure connection could not be established with the mail server.');
  }
  if (code === 'ECONNREFUSED' || code === 'ECONNRESET' || code === 'ECONNECTION' || code === 'ESOCKET' || code === 'EDNS' || code === 'ENOTFOUND') {
    return new ServiceError('transient', 'connection_failed', 'Could not connect to the mail server. Check the host and port.');
  }
  if (rc && DAILY_LIMIT.test(text)) return new ServiceError('rate_limit', 'provider_daily_limit', "The provider's daily sending limit was reached.");
  if (rc && (SLOW_DOWN.test(text) || rc === 454 || rc === 421)) {
    return new ServiceError('rate_limit', 'provider_rate_limited', 'The provider asked us to slow down.');
  }
  if (rc >= 400 && rc < 500) return new ServiceError('transient', 'smtp_temporary', `The mail server temporarily refused the message (${rc}).`);
  if (rc >= 500) return new ServiceError('permanent', 'smtp_rejected', `The mail server rejected the message (${rc}).`);
  return new ServiceError('transient', 'smtp_error', 'Unexpected mail error.');
}

import { MAX_ATTACHMENT_BYTES } from './config.js';

const EMAIL = /^[^\s<>@,;"'\\()[\]]+@[^\s<>@,;"'\\()[\]]+\.[^\s<>@,;"'\\()[\]]+$/;
const HOST = /^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/i;
const REQUEST_ID = /^[A-Za-z0-9_.:-]{8,128}$/;
const CTRL = /[\u0000-\u001f\u007f]/;

export const ALLOWED_PORTS = { 465: true, 587: false }; // port -> implicit TLS (secure)
const MAX_RECIPIENTS = 10;

const isStr = (v) => typeof v === 'string';
const asList = (v) => (v === undefined ? [] : Array.isArray(v) ? v : null);

/** Checks the host/port/secure triple and the sign-in fields shared by send and verify. */
export function validateSmtp(smtp, errors) {
  if (!smtp || typeof smtp !== 'object') {
    errors.push('smtp: required');
    return;
  }
  if (!isStr(smtp.host) || !HOST.test(smtp.host)) errors.push('smtp.host: must be a hostname such as smtp.example.com');
  if (!(smtp.port in ALLOWED_PORTS)) errors.push('smtp.port: only 465 (SSL/TLS) and 587 (STARTTLS) are supported');
  else if (typeof smtp.secure !== 'boolean' || smtp.secure !== ALLOWED_PORTS[smtp.port]) {
    errors.push('smtp.secure: use true with port 465 and false (STARTTLS) with port 587');
  }
  if (!isStr(smtp.username) || !smtp.username || smtp.username.length > 254 || CTRL.test(smtp.username)) {
    errors.push('smtp.username: required');
  }
  if (!isStr(smtp.passwordEnc) || smtp.passwordEnc.length < 40 || smtp.passwordEnc.length > 4096) {
    errors.push('smtp.passwordEnc: required');
  }
  if (smtp.accountEmail !== undefined && !(isStr(smtp.accountEmail) && EMAIL.test(smtp.accountEmail))) {
    errors.push('smtp.accountEmail: invalid');
  }
}

export function validateVerify(body) {
  const errors = [];
  if (!isStr(body?.requestId) || !REQUEST_ID.test(body.requestId)) errors.push('requestId: 8-128 characters of A-Z a-z 0-9 _ . : -');
  validateSmtp(body?.smtp, errors);
  return errors.length ? { errors } : { value: body };
}

export function validateSend(body) {
  const errors = [];
  if (!isStr(body?.requestId) || !REQUEST_ID.test(body.requestId)) errors.push('requestId: 8-128 characters of A-Z a-z 0-9 _ . : -');
  validateSmtp(body?.smtp, errors);

  const e = body?.email;
  if (!e || typeof e !== 'object') {
    errors.push('email: required');
    return { errors };
  }
  if (!isStr(e.from) || !EMAIL.test(e.from)) errors.push('email.from: invalid address');
  if (e.fromName !== undefined && (!isStr(e.fromName) || e.fromName.length > 120 || /[\u0000-\u001f<>"]/.test(e.fromName))) {
    errors.push('email.fromName: invalid');
  }
  const lists = { to: asList(e.to), cc: asList(e.cc), bcc: asList(e.bcc) };
  for (const [name, list] of Object.entries(lists)) {
    if (list === null) errors.push(`email.${name}: must be an array`);
    else if (!list.every((a) => isStr(a) && a.length <= 254 && EMAIL.test(a))) errors.push(`email.${name}: contains an invalid address`);
  }
  if (lists.to && lists.to.length === 0) errors.push('email.to: at least one recipient');
  const total = Object.values(lists).reduce((n, l) => n + (l ? l.length : 0), 0);
  if (total > MAX_RECIPIENTS) errors.push(`email: at most ${MAX_RECIPIENTS} recipients in total`);
  if (!isStr(e.subject) || !e.subject.trim() || e.subject.length > 998 || /[\r\n]/.test(e.subject)) {
    errors.push('email.subject: required, one line, up to 998 characters');
  }
  for (const f of ['html', 'text']) {
    if (e[f] !== undefined && (!isStr(e[f]) || e[f].length > 1_000_000)) errors.push(`email.${f}: must be a string under 1 MB`);
  }
  if (!e.html && !e.text) errors.push('email: html or text is required');

  const atts = e.attachments ?? [];
  if (!Array.isArray(atts) || atts.length > 3) errors.push('email.attachments: at most 3');
  else {
    let bytes = 0;
    for (const a of atts) {
      if (!isStr(a?.filename) || !a.filename || a.filename.length > 200 || CTRL.test(a.filename) || /[\\/]/.test(a.filename)) {
        errors.push('email.attachments: invalid filename');
      } else if (!isStr(a.contentType) || !/^[\w.+-]+\/[\w.+-]+$/.test(a.contentType)) errors.push('email.attachments: invalid contentType');
      else if (!isStr(a.contentBase64) || !/^[A-Za-z0-9+/]+={0,2}$/.test(a.contentBase64)) errors.push('email.attachments: invalid content');
      else bytes += Math.floor((a.contentBase64.length * 3) / 4);
    }
    if (bytes > MAX_ATTACHMENT_BYTES) errors.push('email.attachments: too large (3 MB in total)');
  }

  if (!errors.length) {
    const account = (body.smtp.accountEmail ?? body.smtp.username).toLowerCase();
    if (e.from.toLowerCase() !== account) errors.push('email.from: must be the connected account address');
  }
  return errors.length ? { errors } : { value: body };
}

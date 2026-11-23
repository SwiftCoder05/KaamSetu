// Local stand-in for `vercel dev`: serves the same handlers over plain HTTP so Django can call them.
//   EMAIL_SERVICE_SECRET=<32+ chars> node scripts/dev-server.js     (listens on http://127.0.0.1:3000)
import { createServer } from 'node:http';
import { POST as send } from '../api/send-email.js';
import { POST as verify } from '../api/verify-smtp.js';

const routes = { '/api/send-email': send, '/api/verify-smtp': verify };

createServer(async (req, res) => {
  const handler = routes[req.url?.split('?')[0]];
  if (!handler || req.method !== 'POST') {
    res.writeHead(handler ? 405 : 404).end();
    return;
  }
  const chunks = [];
  for await (const c of req) chunks.push(c);
  const response = await handler(new Request(`http://localhost${req.url}`, { method: 'POST', headers: req.headers, body: Buffer.concat(chunks) }));
  res.writeHead(response.status, Object.fromEntries(response.headers));
  res.end(await response.text());
}).listen(Number(process.env.PORT) || 3000, '127.0.0.1', () => console.log('email-service dev server on http://127.0.0.1:3000'));

import { handleSend } from '../lib/handlers.js';

// Node.js runtime (not Edge). Only POST is exported, so every other method gets a 405 from Vercel.
export async function POST(request) {
  return handleSend(request);
}

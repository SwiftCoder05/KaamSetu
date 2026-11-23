import { handleVerify } from '../lib/handlers.js';

export async function POST(request) {
  return handleVerify(request);
}

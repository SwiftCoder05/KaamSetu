// Tiny key-value store used for idempotency records and rate-limit counters.
//
// Serverless instances do not share memory, so the in-memory store only protects within one warm instance.
// Set UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN to share state across all instances.

export class MemoryStore {
  constructor(now = () => Date.now()) {
    this.now = now;
    this.map = new Map();
  }

  #live(key) {
    const hit = this.map.get(key);
    if (hit && hit.exp > this.now()) return hit;
    this.map.delete(key);
    return null;
  }

  async get(key) {
    return this.#live(key)?.value ?? null;
  }

  async set(key, value, ttlSeconds) {
    this.map.set(key, { value, exp: this.now() + ttlSeconds * 1000 });
  }

  /** Stores the value only if the key is free. Returns true when this caller won it. */
  async setIfAbsent(key, value, ttlSeconds) {
    if (this.#live(key)) return false;
    await this.set(key, value, ttlSeconds);
    return true;
  }

  async del(key) {
    this.map.delete(key);
  }

  /** Counts one hit in a fixed window and returns the new count. */
  async incr(key, ttlSeconds) {
    const hit = this.#live(key);
    const value = (hit ? Number(hit.value) : 0) + 1;
    this.map.set(key, { value, exp: hit ? hit.exp : this.now() + ttlSeconds * 1000 });
    if (this.map.size > 5000) this.#sweep();
    return value;
  }

  #sweep() {
    for (const k of this.map.keys()) this.#live(k);
  }
}

export class UpstashStore {
  constructor(url, token, fetchImpl = fetch) {
    this.url = url.replace(/\/+$/, '');
    this.token = token;
    this.fetch = fetchImpl;
  }

  async #cmd(...args) {
    const res = await this.fetch(this.url, {
      method: 'POST',
      headers: { Authorization: `Bearer ${this.token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify(args),
      signal: AbortSignal.timeout(3000),
    });
    if (!res.ok) throw new Error(`store_http_${res.status}`);
    return (await res.json()).result;
  }

  get(key) {
    return this.#cmd('GET', key);
  }

  async set(key, value, ttlSeconds) {
    await this.#cmd('SET', key, value, 'EX', ttlSeconds);
  }

  async setIfAbsent(key, value, ttlSeconds) {
    return (await this.#cmd('SET', key, value, 'EX', ttlSeconds, 'NX')) === 'OK';
  }

  async del(key) {
    await this.#cmd('DEL', key);
  }

  async incr(key, ttlSeconds) {
    const n = await this.#cmd('INCR', key);
    if (n === 1) await this.#cmd('EXPIRE', key, ttlSeconds);
    return n;
  }
}

let shared;
export function defaultStore(env = process.env) {
  if (!shared) {
    shared = env.UPSTASH_REDIS_REST_URL && env.UPSTASH_REDIS_REST_TOKEN
      ? new UpstashStore(env.UPSTASH_REDIS_REST_URL, env.UPSTASH_REDIS_REST_TOKEN)
      : new MemoryStore();
  }
  return shared;
}

// Thin fetch wrapper. GET and POST helpers.
export class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

function friendly(body, status) {
  if (body && typeof body.detail === 'string') return body.detail;
  if (status === 429) return 'Too many requests. Wait a minute and try again.';
  if (status === 404) return 'Not found.';
  if (status >= 500) return 'The server had a problem. Try again in a moment.';
  return 'Something went wrong. Try again.';
}

export async function get(path, params) {
  let url = path;
  if (params) {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') q.set(k, v);
    const s = q.toString();
    if (s) url += (url.includes('?') ? '&' : '?') + s;
  }
  let res;
  try {
    res = await fetch(url, { headers: { Accept: 'application/json' } });
  } catch {
    throw new ApiError('Cannot reach the server. Check your connection.', 0);
  }
  let body = null;
  try { body = await res.json(); } catch { /* not JSON */ }
  if (!res.ok) throw new ApiError(friendly(body, res.status), res.status);
  return body;
}

export async function post(path, bodyData, params) {
  let url = path;
  if (params) {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') q.set(k, v);
    const s = q.toString();
    if (s) url += (url.includes('?') ? '&' : '?') + s;
  }
  let res;
  try {
    res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: bodyData ? JSON.stringify(bodyData) : undefined,
    });
  } catch {
    throw new ApiError('Cannot reach the server. Check your connection.', 0);
  }
  let body = null;
  try { body = await res.json(); } catch { /* not JSON */ }
  if (!res.ok) throw new ApiError(friendly(body, res.status), res.status);
  return body;
}

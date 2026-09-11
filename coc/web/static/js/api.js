/**
 * The JSON client.
 *
 * Every state-changing call carries the session CSRF token. Uploads go through
 * XMLHttpRequest rather than fetch because they need real progress events —
 * the hash ring in the intake scene is driven by actual bytes transferred, not
 * by a timer pretending to be one.
 */

const state = { csrf: null };

class ApiError extends Error {
  constructor(message, status, code) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

async function request(path, { method = 'GET', body = null } = {}) {
  const headers = {};
  if (body !== null) headers['Content-Type'] = 'application/json';
  if (state.csrf && method !== 'GET') headers['X-CSRF-Token'] = state.csrf;

  const response = await fetch(path, {
    method,
    headers,
    credentials: 'same-origin',
    body: body === null ? undefined : JSON.stringify(body),
  });

  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }

  if (!response.ok) {
    throw new ApiError(
      payload?.message || `Request failed (${response.status})`,
      response.status,
      payload?.error,
    );
  }
  if (payload?.csrf_token) state.csrf = payload.csrf_token;
  return payload;
}

/** Upload one file, reporting real progress as it goes. */
function upload(path, file, fields = {}, onProgress = null) {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append('file', file, file.name);
    for (const [key, value] of Object.entries(fields)) {
      if (value !== null && value !== undefined && value !== '') form.append(key, value);
    }

    const xhr = new XMLHttpRequest();
    xhr.open('POST', path, true);
    xhr.withCredentials = true;
    if (state.csrf) xhr.setRequestHeader('X-CSRF-Token', state.csrf);

    xhr.upload.addEventListener('progress', (event) => {
      if (onProgress && event.lengthComputable) {
        onProgress(event.loaded / event.total, event.loaded, event.total);
      }
    });

    // The server still has to hash what it received; report that as the last
    // stretch rather than snapping the ring to full the moment bytes land.
    xhr.upload.addEventListener('load', () => onProgress && onProgress(0.97, 0, 0));

    xhr.addEventListener('load', () => {
      let payload = null;
      try { payload = JSON.parse(xhr.responseText); } catch { payload = null; }
      if (xhr.status >= 200 && xhr.status < 300) {
        if (onProgress) onProgress(1, 0, 0);
        resolve(payload);
      } else {
        reject(new ApiError(payload?.message || `Upload failed (${xhr.status})`, xhr.status, payload?.error));
      }
    });
    xhr.addEventListener('error', () => reject(new ApiError('Network error during upload', 0, 'network')));
    xhr.addEventListener('abort', () => reject(new ApiError('Upload cancelled', 0, 'aborted')));

    xhr.send(form);
  });
}

export const api = {
  ApiError,
  setToken: (token) => { state.csrf = token; },

  session:      () => request('/api/session'),
  signIn:       (username, accessKey) =>
                   request('/api/session', { method: 'POST', body: { username, access_key: accessKey } }),
  signOut:      () => request('/api/session', { method: 'DELETE' }),

  overview:     () => request('/api/overview'),
  cases:        () => request('/api/cases'),
  openCase:     (payload) => request('/api/cases', { method: 'POST', body: payload }),
  readCase:     (id) => request(`/api/cases/${id}`),
  caseGraph:    (id) => request(`/api/cases/${id}/graph`),

  readEvidence: (id) => request(`/api/evidence/${id}`),
  verify:       (id) => request(`/api/evidence/${id}/verify`, { method: 'POST' }),
  transfer:     (id, to, reason) =>
                   request(`/api/evidence/${id}/transfer`, { method: 'POST', body: { to, reason } }),
  accept:       (id) => request(`/api/evidence/${id}/accept`, { method: 'POST' }),

  chain:        (params = {}) => {
                   const query = new URLSearchParams(params).toString();
                   return request(`/api/chain${query ? `?${query}` : ''}`);
                 },
  verifyChain:  () => request('/api/chain/verify', { method: 'POST' }),
  users:        () => request('/api/users'),

  ingest:       (caseId, file, fields, onProgress) =>
                   upload(`/api/cases/${caseId}/evidence`, file, fields, onProgress),
  attach:       (caseId, file, fields, onProgress) =>
                   upload(`/api/cases/${caseId}/attachments`, file, fields, onProgress),
};

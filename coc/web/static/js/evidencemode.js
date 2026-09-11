/**
 * Evidence mode — the plain view.
 *
 * The 3D vault is how the tool is meant to be used, but a forensic application
 * has to survive the conditions it will actually meet: a locked-down machine
 * with no WebGL, a user who has asked the operating system for reduced motion,
 * a screen reader, or an examiner at two in the morning who needs to read a
 * custody log rather than fly through one.
 *
 * This is not a degraded copy with different data. It runs on exactly the same
 * JSON API as the 3D shell, so the two can never disagree about what the log
 * says — which, for an audit tool, is the only acceptable arrangement.
 */

import { escapeHtml, formatStamp, humanBytes } from './util.js';

const REASONS = {
  'no-webgl': 'This browser or machine cannot run WebGL, so the 3D vault is unavailable.',
  preference: 'Showing the plain view. The 3D vault is still available.',
};

export function mountEvidenceMode({ api, reason = 'preference' }) {
  document.body.classList.add('flat');
  document.getElementById('stage')?.remove();
  document.getElementById('labels')?.remove();
  document.getElementById('gate')?.remove();
  document.getElementById('ui')?.remove();

  const root = document.createElement('div');
  root.className = 'flatroot';
  root.innerHTML = `
    <header>
      <h1>KEENEYE <span>evidence mode</span></h1>
      <p class="note">${escapeHtml(REASONS[reason] ?? REASONS.preference)}</p>
      <div class="flatbar">
        <span id="flat-who"></span>
        <button id="flat-3d" type="button">Return to the 3D vault</button>
        <button id="flat-out" type="button">Sign out</button>
      </div>
    </header>
    <main id="flat-main" aria-live="polite"><p>Loading…</p></main>`;
  document.body.append(root);

  const main = root.querySelector('#flat-main');

  root.querySelector('#flat-3d').addEventListener('click', () => {
    localStorage.removeItem('keeneye.flat');
    window.location.assign('/');
  });
  root.querySelector('#flat-out').addEventListener('click', async () => {
    try { await api.signOut(); } catch { /* signing out regardless */ }
    window.location.reload();
  });

  const table = (caption, headers, rows) => `
    <table>
      <caption>${escapeHtml(caption)}</caption>
      <thead><tr>${headers.map((h) => `<th scope="col">${escapeHtml(h)}</th>`).join('')}</tr></thead>
      <tbody>${rows.join('') || `<tr><td colspan="${headers.length}">None recorded.</td></tr>`}</tbody>
    </table>`;

  async function renderLogin(message = '') {
    main.innerHTML = `
      <form id="flat-login" class="stack">
        <h2>Sign in</h2>
        ${message ? `<p class="bad">${escapeHtml(message)}</p>` : ''}
        <label>Examiner <input name="username" required spellcheck="false" autocomplete="username"></label>
        <label>Access key <input name="access_key" type="password" required autocomplete="current-password"></label>
        <button type="submit">Sign in</button>
      </form>`;
    main.querySelector('#flat-login').addEventListener('submit', async (event) => {
      event.preventDefault();
      const data = new FormData(event.target);
      try {
        await api.signIn(String(data.get('username')), String(data.get('access_key')));
        await renderWorkspace();
      } catch (error) {
        await renderLogin(error.message);
      }
    });
  }

  async function renderWorkspace() {
    main.innerHTML = '<p>Loading…</p>';
    const overview = await api.overview();
    root.querySelector('#flat-who').textContent =
      `${overview.examiner.full_name || overview.examiner.username} · ${overview.examiner.role}`;

    const chain = overview.statistics.chain;
    const cases = overview.cases;

    const detail = cases.length ? await api.readCase(cases[0].id) : null;
    const log = await api.chain();

    main.innerHTML = `
      <section class="verdict ${chain.ok ? 'ok' : 'bad'}">
        <h2>Chain of custody: ${chain.ok ? 'INTACT' : 'BROKEN'}</h2>
        <p>${chain.ok
          ? `${chain.entries_checked.toLocaleString()} entries verified. Head ${escapeHtml(chain.head_hash ?? '')}.`
          : escapeHtml(chain.reason ?? 'Verification failed.')}</p>
      </section>

      <section>
        <h2>Cases</h2>
        ${table('Cases in this workspace', ['Case', 'Title', 'Status', 'Opened', 'Items', 'Files', 'Report'],
          cases.map((record) => `<tr>
            <td>${escapeHtml(record.case_number)}</td>
            <td>${escapeHtml(record.title)}</td>
            <td>${escapeHtml(record.status)}</td>
            <td>${formatStamp(record.opened_at)}</td>
            <td>${record.evidence_count}</td>
            <td>${record.attachment_count}</td>
            <td><a href="/files/case/${record.id}/report.pdf">PDF</a></td>
          </tr>`))}
      </section>

      ${detail ? `
      <section>
        <h2>Evidence — ${escapeHtml(detail.case.case_number)}</h2>
        ${table('Evidence items', ['Item', 'Filename', 'Size', 'SHA-256', 'Acquired', 'Holder'],
          detail.evidence.map((item) => `<tr>
            <td>${escapeHtml(item.item_number)}</td>
            <td><a href="/files/evidence/${item.id}">${escapeHtml(item.original_filename)}</a></td>
            <td>${humanBytes(item.byte_size)}</td>
            <td><code>${escapeHtml(item.sha256)}</code></td>
            <td>${escapeHtml(item.acquisition_date)}</td>
            <td>${escapeHtml(item.holder ?? '—')}</td>
          </tr>`))}
      </section>

      <section>
        <h2>Supporting material</h2>
        ${table('Photographs, documents and raw files', ['Kind', 'Filename', 'Caption', 'Size', 'SHA-256', 'Uploaded'],
          detail.attachments.map((a) => `<tr>
            <td>${escapeHtml(a.kind)}</td>
            <td><a href="/files/attachment/${a.id}">${escapeHtml(a.original_filename)}</a></td>
            <td>${escapeHtml(a.caption || '—')}</td>
            <td>${humanBytes(a.byte_size)}</td>
            <td><code>${escapeHtml(a.sha256)}</code></td>
            <td>${formatStamp(a.uploaded_at)}</td>
          </tr>`))}
      </section>` : ''}

      <section>
        <h2>Custody log</h2>
        ${table('Every action recorded, oldest first',
          ['#', 'Timestamp (UTC)', 'Action', 'Examiner', 'Check', 'Previous hash', 'Entry hash'],
          log.entries.map((entry) => `<tr${
            log.verification.first_broken_seq && entry.seq >= log.verification.first_broken_seq
              ? ' class="broken"' : ''}>
            <td>${entry.seq}</td>
            <td>${formatStamp(entry.timestamp_utc)}</td>
            <td>${escapeHtml(String(entry.action).replace(/_/g, ' '))}</td>
            <td>${escapeHtml(entry.actor_username)}</td>
            <td>${escapeHtml(String(entry.hash_check_result).toUpperCase())}</td>
            <td><code>${escapeHtml(entry.prev_hash)}</code></td>
            <td><code>${escapeHtml(entry.entry_hash)}</code></td>
          </tr>`))}
      </section>`;
  }

  api.session()
    .then(async (session) => {
      api.setToken(session.csrf_token);
      if (session.authenticated) await renderWorkspace();
      else await renderLogin();
    })
    .catch(() => renderLogin('Could not reach the server.'));
}

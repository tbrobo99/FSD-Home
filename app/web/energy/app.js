/* FSD Home energy dashboard — polls /api/energy every 2 seconds. */
(function () {
  const $ = (id) => document.getElementById(id);
  let showPrompt = false;

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function render(d) {
    $('period').textContent = d.period || '--';
    $('used').textContent = Number(d.used_kwh || 0).toFixed(1);
    $('budget').textContent = d.budget_kwh;
    $('pct').textContent = `${d.budget_pct}% used`;
    $('remaining').innerHTML = `${Number(d.remaining_kwh || 0).toFixed(1)} <em>kWh</em>`;
    $('load').innerHTML = `${d.current_load_w || 0} <em>W</em>`;
    $('price').innerHTML = `${d.price_krw_per_kwh} <em>KRW/kWh</em>`;
    $('offpeak').textContent = String(d.next_offpeak || '').slice(11, 16) || '22:00';
    $('strict').checked = !!d.strict_mode;
    $('llm').checked = !!d.llm_decide;
    $('llm-status').textContent = d.llm_available
      ? 'Model connected — decisions are LLM-proposed, rule-validated'
      : 'No model key — rules produce the same four-way decision';
    $('budget-input').value = d.budget_kwh;

    const pill = $('tariff-pill');
    pill.textContent = String(d.tariff || '').toUpperCase();
    pill.className = 'pill' + (d.tariff === 'peak' ? ' peak' : '');

    const circumference = 2 * Math.PI * 84;
    const offset = circumference * (1 - Math.min(100, d.budget_pct || 0) / 100);
    $('ring-fill').style.strokeDashoffset = String(offset);

    const rows = (d.decisions || []).map((r) => {
      const requested = JSON.parse(r.requested || '[]')
        .map((a) => `${a.action}=${a.value}`).join(', ');
      const time = String(r.ts || '').slice(5, 16).replace('T', ' ');
      return `<tr><td>${esc(time)}</td><td>${esc(r.device_name || r.device_id)}</td>` +
        `<td><span class="badge ${esc(r.decision)}">${esc(r.decision)}</span></td>` +
        `<td class="reason">${esc(r.reason)}</td><td>${esc(r.decided_by)}</td>` +
        `<td>${esc(requested)}</td></tr>`;
    }).join('');
    $('decision-rows').innerHTML = rows ||
      '<tr><td colspan="6" class="empty">No energy decisions yet — run a scenario above.</td></tr>';

    if (showPrompt) $('prompt-box').textContent = d.prompt || '';
  }

  async function poll() {
    try {
      const r = await fetch('/api/energy', { cache: 'no-store' });
      if (r.ok) render(await r.json());
    } catch (_) { /* the page stays usable while the server restarts */ }
  }

  async function post(path, body) {
    const r = await fetch(path, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    return r.json();
  }

  $('strict').addEventListener('change', (e) =>
    post('/api/energy/strict', { enabled: e.target.checked }).then(poll));
  $('llm').addEventListener('change', (e) =>
    post('/api/energy/llm', { enabled: e.target.checked }).then(poll));
  $('save-budget').addEventListener('click', () =>
    post('/api/energy/budget', { kwh: Number($('budget-input').value) }).then(poll));
  $('reset').addEventListener('click', () =>
    post('/api/energy/reset', {}).then(poll));
  $('toggle-prompt').addEventListener('click', () => {
    showPrompt = !showPrompt;
    $('prompt-box').classList.toggle('hidden', !showPrompt);
    $('toggle-prompt').textContent = showPrompt ? 'Hide actual prompt' : 'Show actual prompt';
    poll();
  });

  document.querySelectorAll('[data-demo]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const out = $('demo-result');
      out.classList.remove('hidden');
      out.innerHTML = 'Running decision…';
      const r = await post('/api/energy/demo', { name: btn.dataset.demo });
      if (!r.ok) { out.textContent = r.error || 'Failed'; return; }
      const d = r.decision || {};
      const a = r.applied || {};
      out.innerHTML = `<b>${esc(d.action)}</b> · ${esc(d.reason || '')}` +
        `<div class="meta">Requested: ${esc(r.request)} · decided by ${esc(d.decided_by)}` +
        (d.estimated_kwh ? ` · estimate ${d.estimated_kwh} kWh` : '') +
        (a.scheduled_at ? ` · scheduled ${esc(a.scheduled_at.slice(0, 16).replace('T', ' '))}` : '') +
        ` · ${Number(r.energy ? r.energy.remaining_kwh : 0).toFixed(1)} kWh left</div>`;
      poll();
    });
  });

  setInterval(() => {
    $('clock').textContent = new Date().toLocaleTimeString();
  }, 1000);
  $('clock').textContent = new Date().toLocaleTimeString();
  poll();
  setInterval(poll, 2000);
})();

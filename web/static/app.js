(function () {
  const H = { "X-Requested-With": "invoice-agent" };
  const $ = (s) => document.querySelector(s);
  const msg = (t) => { const m = $("#msg"); if (m) m.textContent = t; };
  async function api(path, opts = {}) {
    const r = await fetch(path, { method: "POST", ...opts, headers: { ...H, ...(opts.headers || {}) } });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.detail || r.statusText);
    return body;
  }
  const run = $("#run-btn");
  if (run) run.onclick = async () => {
    try { await api("/api/run"); msg("Started"); run.disabled = true; } catch (e) { msg(e.message); }
  };
  const w = $("#watch-toggle");
  if (w) w.onchange = () => api("/api/watch", { body: JSON.stringify({ enabled: w.checked }),
    headers: { "Content-Type": "application/json" } }).catch((e) => msg(e.message));
  const up = $("#upload");
  if (up) up.onchange = async () => {
    const fd = new FormData(); fd.append("file", up.files[0]);
    try { const r = await api("/api/upload", { body: fd }); msg("Uploaded " + r.saved); setTimeout(() => location.reload(), 600); }
    catch (e) { msg(e.message); }
  };
  document.querySelectorAll("button[data-act]").forEach((b) => b.onclick = async () => {
    const fp = b.dataset.fp, act = b.dataset.act;
    try {
      if (act === "approve") { await api(`/api/invoices/${fp}/approve`, { body: JSON.stringify({}), headers: { "Content-Type": "application/json" } }); location.reload(); }
      else if (act === "reject") {
        const reason = prompt("Reason for rejecting?"); if (!reason) return;
        await api(`/api/invoices/${fp}/reject`, { body: JSON.stringify({ reason }), headers: { "Content-Type": "application/json" } }); location.reload();
      } else { await api(`/api/invoices/${fp}/reveal`); msg("Opened folder"); }
    } catch (e) { msg(e.message); }
  });
  // polling (only on the workspace page), only while the tab is visible
  let wasRunning = !!(run && run.disabled);
  async function poll() {
    if (document.hidden || !$("#counts")) return;
    try {
      const s = await (await fetch("/api/state")).json();
      for (const [k, v] of Object.entries(s.counts)) { const el = document.querySelector(`[data-count="${k}"]`); if (el) el.textContent = v; }
      const line = $("#running-line");
      if (line) line.textContent = s.running ? `Running… ${s.current.file ? s.current.file + " step " + s.current.step : ""}` : (s.error || "");
      if (run) run.disabled = s.running;
      if (wasRunning && !s.running) location.reload();
      wasRunning = s.running;
    } catch (_) {}
  }
  setInterval(poll, 2000);
})();

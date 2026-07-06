/* statebate-pulse dashboard — SPA estática para GitHub Pages.
 * Lee la API desde window.PULSE_API (configurada abajo).
 * Sin build step, sin framework, vanilla JS + fetch.
 */

// === CONFIG ===
// En GitHub Pages, la API vive en tu VM de Oracle Cloud.
// Cámbialo por tu dominio: https://pulse.tudominio.com
const PULSE_API = window.PULSE_API || "http://localhost:8080";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

// ---------- helpers ----------
async function getJSON(path) {
  const r = await fetch(PULSE_API + path, { credentials: "omit" });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

function fmtNum(n) {
  if (n == null) return "—";
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "k";
  return String(n);
}
function fmtAgo(iso) {
  if (!iso) return "—";
  const d = (Date.now() - new Date(iso).getTime()) / 1000;
  if (d < 60) return `${Math.floor(d)}s`;
  if (d < 3600) return `${Math.floor(d / 60)}m`;
  if (d < 86400) return `${Math.floor(d / 3600)}h`;
  return `${Math.floor(d / 86400)}d`;
}
function scoreBar(score) {
  // score in [0,1]
  const pct = Math.round((score || 0) * 100);
  return `<span class="score-bar" style="width:${pct}px"></span>${pct}`;
}

// ---------- tabs ----------
$$(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    $$(".tab").forEach((b) => b.classList.remove("active"));
    $$(".panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    $(`#tab-${btn.dataset.tab}`).classList.add("active");
  });
});

// ---------- Top Tippers ----------
async function loadTippers() {
  const days = $("#tip-days").value;
  const limit = $("#tip-limit").value;
  const tbody = $("#tbl-tippers tbody");
  tbody.innerHTML = `<tr><td colspan="8" class="muted">cargando…</td></tr>`;
  try {
    const data = await getJSON(`/api/top-tippers?days=${days}&limit=${limit}`);
    if (!data.items.length) {
      tbody.innerHTML = `<tr><td colspan="8" class="muted">sin datos aún</td></tr>`;
      return;
    }
    tbody.innerHTML = data.items.map((t, i) => `
      <tr>
        <td class="rank">${i + 1}</td>
        <td>${escapeHtml(t.tipper_username)}</td>
        <td>${scoreBar(t.score)}</td>
        <td>${fmtNum(t.total_tokens)}</td>
        <td>${fmtNum(t.tip_count)}</td>
        <td>${t.rooms_tipped}</td>
        <td>${t.active_days}</td>
        <td class="muted">${fmtAgo(t.last_tip_at)}</td>
      </tr>
    `).join("");
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="8" class="muted">error: ${escapeHtml(e.message)}</td></tr>`;
  }
}
$("#tip-refresh").addEventListener("click", loadTippers);
$("#tip-days").addEventListener("change", loadTippers);
$("#tip-limit").addEventListener("change", loadTippers);

// ---------- Top Models ----------
async function loadModels() {
  const tbody = $("#tbl-models tbody");
  tbody.innerHTML = `<tr><td colspan="6" class="muted">cargando…</td></tr>`;
  try {
    const data = await getJSON(`/api/top-models?limit=100`);
    if (!data.items.length) {
      tbody.innerHTML = `<tr><td colspan="6" class="muted">sin datos aún</td></tr>`;
      return;
    }
    tbody.innerHTML = data.items.map((m, i) => `
      <tr>
        <td class="rank">${i + 1}</td>
        <td><a href="#" data-slug="${escapeAttr(m.room_slug)}" class="model-link">${escapeHtml(m.display_name || m.username)}</a></td>
        <td>${fmtNum(m.total_tokens_30d)}</td>
        <td>${fmtNum(m.tip_count_30d)}</td>
        <td>${m.avg_viewers_7d ?? "—"}</td>
        <td>${fmtNum(m.peak_viewers_7d)}</td>
      </tr>
    `).join("");
    $$(".model-link").forEach((a) =>
      a.addEventListener("click", (e) => { e.preventDefault(); openModel(a.dataset.slug); })
    );
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="6" class="muted">error: ${escapeHtml(e.message)}</td></tr>`;
  }
}

// ---------- Online ahora ----------
async function loadOnline() {
  const grid = $("#online-grid");
  grid.innerHTML = `<div class="muted">cargando…</div>`;
  try {
    const data = await getJSON(`/api/rooms/online?limit=200`);
    $("#online-updated").textContent = data.items.length
      ? `· actualizado ${fmtAgo(data.items[0].scraped_at)}`
      : "";
    if (!data.items.length) { grid.innerHTML = `<div class="muted">sin salas online</div>`; return; }
    grid.innerHTML = data.items.map((r) => `
      <div class="card" data-slug="${escapeAttr(r.room_slug)}">
        <div class="name">${escapeHtml(r.display_name || r.username)}</div>
        <div class="meta">
          <span class="viewers">👁 ${r.viewers}</span>
          <span>🪙 ${fmtNum(r.session_tokens)}</span>
          ${r.country ? `<span>📍 ${escapeHtml(r.country)}</span>` : ""}
        </div>
        ${r.tags?.length ? `<div class="tags">${r.tags.slice(0,4).map((t) => `<span class="tag">${escapeHtml(t)}</span>`).join("")}</div>` : ""}
      </div>
    `).join("");
    $$(".card").forEach((c) => c.addEventListener("click", () => openModel(c.dataset.slug)));
  } catch (e) {
    grid.innerHTML = `<div class="muted">error: ${escapeHtml(e.message)}</div>`;
  }
}

// ---------- Traffic ----------
async function loadTraffic() {
  const tbody = $("#tbl-traffic tbody");
  tbody.innerHTML = `<tr><td colspan="6" class="muted">cargando…</td></tr>`;
  try {
    const data = await getJSON(`/api/traffic/leaderboard?limit=50`);
    if (!data.items.length) { tbody.innerHTML = `<tr><td colspan="6" class="muted">sin datos</td></tr>`; return; }
    tbody.innerHTML = data.items.map((s, i) => `
      <tr>
        <td class="rank">${i + 1}</td>
        <td><a href="#" data-slug="${escapeAttr(s.room_slug)}" class="model-link">${escapeHtml(s.room_slug)}</a></td>
        <td><span class="score-bar" style="width:${Math.round(s.score)}px"></span>${s.score.toFixed(1)}</td>
        <td>${s.viewers_now}</td>
        <td class="${s.viewers_growth_1h >= 0 ? "" : "muted"}">${(s.viewers_growth_1h*100).toFixed(1)}%</td>
        <td>${fmtNum(s.tokens_velocity_1h)}</td>
      </tr>
    `).join("");
    $$(".model-link").forEach((a) =>
      a.addEventListener("click", (e) => { e.preventDefault(); openModel(a.dataset.slug); })
    );
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="6" class="muted">error: ${escapeHtml(e.message)}</td></tr>`;
  }
}

// ---------- Modal de modelo ----------
async function openModel(slug) {
  const modal = $("#modal");
  $("#modal-title").textContent = slug;
  $("#modal-body").innerHTML = `<p class="muted">cargando reporte…</p>`;
  modal.classList.remove("hidden");
  try {
    const [report, match, direct] = await Promise.all([
      getJSON(`/api/rooms/${encodeURIComponent(slug)}/boost-report`),
      getJSON(`/api/rooms/${encodeURIComponent(slug)}/match-tippers?limit=10`),
      getJSON(`/api/rooms/${encodeURIComponent(slug)}/top-tippers-direct?limit=10`),
    ]);
    const ts = report.traffic_score;
    const slot = report.recommended_go_live_slot;
    const days = ["Lun","Mar","Mié","Jue","Vie","Sáb","Dom"];
    $("#modal-body").innerHTML = `
      <div class="grid" style="grid-template-columns:1fr 1fr;gap:12px;margin-bottom:14px">
        <div class="card" style="cursor:default">
          <div class="muted small">Traffic score</div>
          <div style="font-size:24px;font-weight:800;color:var(--accent)">${ts ? ts.score.toFixed(1) : "—"}</div>
          <div class="meta">viewers ${ts?.viewers_now ?? "—"} · crec ${(ts?.viewers_growth_1h*100??0).toFixed(1)}% · ${fmtNum(ts?.tokens_velocity_1h)} tok/h</div>
        </div>
        <div class="card" style="cursor:default">
          <div class="muted small">Mejor slot para conectar</div>
          <div style="font-size:18px;font-weight:700">${slot ? days[slot.dow]+" "+String(slot.hour_utc).padStart(2,"0")+":00 UTC" : "—"}</div>
          <div class="meta">${slot ? `${slot.active_tippers} tippers activos · ${slot.model_usually_online ? "ya sueles conectar" : "slot nuevo 🎯"}` : ""}</div>
        </div>
      </div>

      <h4 style="margin:14px 0 6px">Top tippers afines por horario (matching)</h4>
      <div class="table-wrap"><table>
        <thead><tr><th>Tipper</th><th>Similitud</th></tr></thead>
        <tbody>
          ${match.items.map((m) => `<tr><td>${escapeHtml(m.tipper)}</td><td>${(m.similarity*100).toFixed(1)}%</td></tr>`).join("") || `<tr><td colspan="2" class="muted">sin datos</td></tr>`}
        </tbody>
      </table></div>

      <h4 style="margin:14px 0 6px">Top tippers por historial directo + afinidad</h4>
      <div class="table-wrap"><table>
        <thead><tr><th>Tipper</th><th>Directo</th><th>Afinidad</th><th>Score</th></tr></thead>
        <tbody>
          ${direct.items.map((d) => `<tr><td>${escapeHtml(d.tipper)}</td><td>${fmtNum(d.direct_tokens)}</td><td>${fmtNum(d.affinity_tokens)}</td><td>${d.score}</td></tr>`).join("") || `<tr><td colspan="4" class="muted">sin datos</td></tr>`}
        </tbody>
      </table></div>

      <h4 style="margin:14px 0 6px">Cross-promo (audiencia complementaria)</h4>
      <div class="table-wrap"><table>
        <thead><tr><th>Modelo</th><th>Shared tippers</th><th>Their tippers</th></tr></thead>
        <tbody>
          ${(report.cross_promo_candidates||[]).map((c) => `<tr><td>${escapeHtml(c.display_name||c.room_slug)}</td><td>${c.shared_tippers}</td><td>${c.their_tippers}</td></tr>`).join("") || `<tr><td colspan="3" class="muted">sin candidatos</td></tr>`}
        </tbody>
      </table></div>

      ${report.affiliate_link ? `<p class="muted small">🔗 link afiliado: <a href="${report.affiliate_link}" target="_blank">${escapeHtml(report.affiliate_link)}</a></p>` : ""}
    `;
  } catch (e) {
    $("#modal-body").innerHTML = `<p class="muted">error: ${escapeHtml(e.message)}</p>`;
  }
}
$(".close").addEventListener("click", () => $("#modal").classList.add("hidden"));
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") $("#modal").classList.add("hidden"); });

// ---------- util ----------
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }[c]));
}
function escapeAttr(s) { return escapeHtml(s); }

// ---------- boot ----------
loadTippers();
loadModels();
loadOnline();
loadTraffic();
// refresca online cada 60s
setInterval(loadOnline, 60000);

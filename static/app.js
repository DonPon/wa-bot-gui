const WEEKDAY_FULL = ["domingo", "lunes", "martes", "miércoles", "jueves", "viernes", "sábado"];

let jobs = [];
let contacts = [];
let muestras = [];
let oneTime = [];
let selectedPersona = "all";
let editingId = null;
let editingContactId = null;
let editingMuestraId = null;
let TOKEN = localStorage.getItem("wabot_token") || "";

const $ = (id) => document.getElementById(id);

function showLogin() {
  $("login-overlay").classList.remove("hidden");
  $("login-error").style.display = "none";
  $("login-token").value = "";
}

function hideLogin() {
  $("login-overlay").classList.add("hidden");
}

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", "X-Token": TOKEN };
  const res = await fetch(path, { ...opts, headers });
  if (res.status === 401) {
    localStorage.removeItem("wabot_token");
    TOKEN = "";
    showLogin();
    throw new Error("Token incorrecto");
  }
  if (!res.ok) {
    const text = await res.text();
    let detail = text;
    try {
      const data = JSON.parse(text);
      if (Array.isArray(data.detail)) {
        detail = data.detail.map((e) => `${(e.loc || []).slice(1).join(".")}: ${e.msg}`).join("; ");
      } else {
        detail = data.detail || text;
      }
    } catch (_) {}
    throw new Error(String(detail));
  }
  return res.json();
}

function fmtDT(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  const today = new Date();
  const sameDay = d.toDateString() === today.toDateString();
  const time = d.toLocaleTimeString("es-MX", { hour: "2-digit", minute: "2-digit" });
  if (sameDay) return `hoy ${time}`;
  return `${WEEKDAY_FULL[d.getDay()]?.slice(0, 3)} ${d.getDate()}/${d.getMonth() + 1} ${time}`;
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function contactLabel(key) {
  const c = contacts.find((x) => x.key === key);
  return c ? `${c.name} (${key})` : key;
}

function statusLabel(s) {
  return ({ running: "enviando", pending: "en espera", success: "éxito", failed: "fallido", cancelled: "cancelado" })[s] || s;
}

/* ---- navegación por pestañas ---- */
function switchTab(tab) {
  document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  ["jobs", "one-time", "contacts", "muestras"].forEach((s) => $("section-" + s).classList.toggle("hidden", s !== tab));
  if (tab === "one-time") refreshOneTime();
  if (tab === "contacts") refreshContacts();
  if (tab === "muestras") refreshMuestras();
}

/* ---- selectores de persona ---- */
function personaOptions(selected) {
  return contacts.map((c) =>
    `<option value="${escapeHtml(c.key)}" ${c.key === selected ? "selected" : ""}>${escapeHtml(c.name)}</option>`
  ).join("");
}

function populatePersonaSelects() {
  const opts = personaOptions();
  $("f-persona").innerHTML = opts || "";
  $("f-m-persona").innerHTML = opts || "";
  $("f-o-persona").innerHTML = opts || "";
  $("job-no-contacts").classList.toggle("hidden", contacts.length > 0);
  const filter = $("muestra-filter");
  const current = filter.value || "all";
  filter.innerHTML = '<option value="all">todas</option>' + opts;
  filter.value = current;
}

/* ---- jobs ---- */
function daysLabel(job) {
  if (job.days_mode === "random") {
    return `random: ${job.random_per_week}/sem`;
  }
  return job.weekday_labels.length ? job.weekday_labels.join(", ") : "ninguno";
}

function statusBadge(job) {
  if (!job.enabled) return '<span class="badge badge-off">deshabilitado</span>';
  if (job.due_now) return '<span class="badge badge-pending">pendiente</span>';
  return "";
}

function runBadge(last) {
  if (!last) return '<span class="muted">nunca</span>';
  const cls = last.status === "running" ? "running" : last.status;
  return `<span class="run-status ${escapeHtml(cls)}">${escapeHtml(statusLabel(last.status))}</span>`;
}

function jobCard(job) {
  const last = job.last_run;
  const lastTime = last ? fmtDT(last.started_at) : "—";
  const lastTrigger = last ? (last.trigger === "manual" ? " (manual)" : "") : "";
  return `
  <div class="card" data-id="${job.id}">
    <div class="job-head">
      <h3>${escapeHtml(job.name)}</h3>
      <span class="sub">${escapeHtml(contactLabel(job.persona))}</span>
      ${job.dry_run ? '<span class="badge badge-dry">dry-run</span>' : ""}
      ${job.delay_enabled ? `<span class="badge badge-delay">retardo ${job.delay_min}-${job.delay_max}min</span>` : ""}
      ${statusBadge(job)}
      <span class="spacer"></span>
      <label class="switch" title="Habilitar/deshabilitar">
        <input type="checkbox" class="toggle" ${job.enabled ? "checked" : ""} />
        <span class="slider"></span>
      </label>
    </div>
    <div class="job-grid">
      <div class="job-cell"><div class="k">Hora</div><div class="v">${escapeHtml(job.time)}</div></div>
      <div class="job-cell"><div class="k">Días</div><div class="v">${escapeHtml(daysLabel(job))}</div></div>
      <div class="job-cell"><div class="k">Próxima ejecución</div><div class="v">${fmtDT(job.next_run)}</div></div>
      <div class="job-cell"><div class="k">Última ejecución</div><div class="v">${runBadge(last)} — ${lastTime}${lastTrigger}</div></div>
    </div>
    <div class="job-actions">
      <button class="btn btn-sm btn-run">▶ Ejecutar ahora</button>
      <button class="btn btn-sm btn-history">Historial</button>
      <button class="btn btn-sm btn-edit">Editar</button>
      <button class="btn btn-sm btn-danger btn-del">Eliminar</button>
    </div>
  </div>`;
}

function renderJobs() {
  const container = $("jobs-container");
  if (!jobs.length) {
    container.innerHTML = '<p class="muted">No hay jobs. Crea el primero con “+ Agregar job”.</p>';
    return;
  }
  container.innerHTML = jobs.map(jobCard).join("");
  $("last-refresh").textContent = `actualizado ${new Date().toLocaleTimeString("es-MX", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`;
}

async function refreshJobs() {
  try {
    jobs = await api("/api/jobs");
    renderJobs();
  } catch (e) {
    $("jobs-container").innerHTML = `<p class="muted" style="color:var(--red)">Error al cargar: ${escapeHtml(e.message)}</p>`;
  }
}

/* ---- contactos ---- */
function renderContacts() {
  const container = $("contacts-container");
  if (!contacts.length) {
    container.innerHTML = '<p class="muted">No hay contactos. Agrega el primero con “+ Agregar contacto”.</p>';
    return;
  }
  container.innerHTML = contacts.map((c) => `
  <div class="card" data-cid="${c.id}">
    <div class="job-head">
      <h3>${escapeHtml(c.name)}</h3>
      <span class="sub">clave: ${escapeHtml(c.key)}</span>
      <span class="spacer"></span>
    </div>
    <div class="job-grid">
      <div class="job-cell"><div class="k">JID / WhatsApp</div><div class="v">${escapeHtml(c.jid)}</div></div>
    </div>
    <div class="job-actions">
      <button class="btn btn-sm btn-edit-contact">Editar</button>
      <button class="btn btn-sm btn-danger btn-del-contact">Eliminar</button>
    </div>
  </div>`).join("");
}

async function refreshContacts() {
  try {
    contacts = await api("/api/contacts");
    renderContacts();
    populatePersonaSelects();
  } catch (e) {
    $("contacts-container").innerHTML = `<p class="muted" style="color:var(--red)">Error al cargar: ${escapeHtml(e.message)}</p>`;
  }
}

function openContactModal(contact) {
  editingContactId = contact ? contact.id : null;
  $("modal-contact-title").textContent = contact ? "Editar contacto" : "Nuevo contacto";
  $("f-c-id").value = contact ? contact.id : "";
  $("f-c-key").value = contact ? contact.key : "";
  $("f-c-name").value = contact ? contact.name : "";
  $("f-c-jid").value = contact ? contact.jid : "";
  $("modal-contact").classList.remove("hidden");
  $("f-c-key").focus();
}

function closeContactModal() {
  $("modal-contact").classList.add("hidden");
  editingContactId = null;
}

async function saveContact(e) {
  e.preventDefault();
  const payload = {
    key: $("f-c-key").value.trim().toLowerCase(),
    name: $("f-c-name").value.trim(),
    jid: $("f-c-jid").value.trim(),
  };
  if (!/^[a-z0-9_-]+$/.test(payload.key)) {
    alert("La clave solo admite minúsculas, números, _ y -.");
    return;
  }
  try {
    if (editingContactId) {
      await api(`/api/contacts/${editingContactId}`, { method: "PUT", body: JSON.stringify(payload) });
    } else {
      await api("/api/contacts", { method: "POST", body: JSON.stringify(payload) });
    }
    closeContactModal();
    refreshContacts();
    refreshJobs();
    refreshMuestras();
  } catch (err) {
    alert("Error: " + err.message);
  }
}

/* ---- muestras ---- */
function renderMuestras() {
  const list = selectedPersona === "all" ? muestras : muestras.filter((m) => m.persona === selectedPersona);
  const container = $("muestras-container");
  if (!list.length) {
    container.innerHTML = '<p class="muted">No hay muestras para este filtro. Agrega la primera con “+ Agregar muestra”.</p>';
    return;
  }
  container.innerHTML = list.map((m) => `
  <div class="card" data-mid="${m.id}">
    <div class="job-head">
      <span class="badge badge-persona">${escapeHtml(contactLabel(m.persona))}</span>
      ${m.enabled ? "" : '<span class="badge badge-off">deshabilitada</span>'}
      <span class="spacer"></span>
    </div>
    <p class="muestra-text">${escapeHtml(m.text)}</p>
    <div class="job-actions">
      <button class="btn btn-sm btn-edit-muestra">Editar</button>
      <button class="btn btn-sm btn-danger btn-del-muestra">Eliminar</button>
    </div>
  </div>`).join("");
}

async function refreshMuestras() {
  try {
    muestras = await api("/api/muestras");
    renderMuestras();
  } catch (e) {
    $("muestras-container").innerHTML = `<p class="muted" style="color:var(--red)">Error al cargar: ${escapeHtml(e.message)}</p>`;
  }
}

function openMuestraModal(m) {
  editingMuestraId = m ? m.id : null;
  $("modal-muestra-title").textContent = m ? "Editar muestra" : "Nueva muestra";
  $("f-m-id").value = m ? m.id : "";
  const defPersona = m ? m.persona : (selectedPersona !== "all" ? selectedPersona : (contacts[0]?.key || ""));
  $("f-m-persona").value = defPersona;
  $("f-m-text").value = m ? m.text : "";
  $("f-m-enabled").checked = m ? m.enabled : true;
  $("modal-muestra").classList.remove("hidden");
  $("f-m-text").focus();
}

function closeMuestraModal() {
  $("modal-muestra").classList.add("hidden");
  editingMuestraId = null;
}

async function saveMuestra(e) {
  e.preventDefault();
  const payload = {
    persona: $("f-m-persona").value,
    text: $("f-m-text").value.trim(),
    enabled: $("f-m-enabled").checked,
  };
  if (!payload.persona) {
    alert("Primero agrega un contacto en la pestaña “Contactos”.");
    return;
  }
  if (!payload.text) {
    alert("Escribe el texto de la muestra.");
    return;
  }
  try {
    if (editingMuestraId) {
      await api(`/api/muestras/${editingMuestraId}`, { method: "PUT", body: JSON.stringify(payload) });
    } else {
      await api("/api/muestras", { method: "POST", body: JSON.stringify(payload) });
    }
    closeMuestraModal();
    refreshMuestras();
  } catch (err) {
    alert("Error: " + err.message);
  }
}

/* ---- envío único ---- */
function oneTimeCard(o) {
  return `
  <div class="card" data-oid="${o.id}">
    <div class="job-head">
      <h3>${escapeHtml(contactLabel(o.persona))}</h3>
      ${o.dry_run ? '<span class="badge badge-dry">dry-run</span>' : ""}
      ${o.delay_enabled ? `<span class="badge badge-delay">retardo ${o.delay_min}-${o.delay_max}min</span>` : ""}
      <span class="run-status ${escapeHtml(o.status)}">${escapeHtml(statusLabel(o.status))}</span>
      <span class="spacer"></span>
    </div>
    <p class="muestra-text">${escapeHtml(o.text)}</p>
    <div class="job-grid">
      <div class="job-cell"><div class="k">Programado para</div><div class="v">${fmtDT(o.send_at)}</div></div>
      <div class="job-cell"><div class="k">Creado</div><div class="v">${fmtDT(o.created_at)}</div></div>
      ${o.sent_at ? `<div class="job-cell"><div class="k">Enviado</div><div class="v">${fmtDT(o.sent_at)}</div></div>` : ""}
    </div>
    ${o.result ? `<pre class="one-time-result">${escapeHtml(o.result)}</pre>` : ""}
    <div class="job-actions">
      ${o.status === "pending" ? '<button class="btn btn-sm btn-cancel-one-time">Cancelar</button>' : ""}
      <button class="btn btn-sm btn-danger btn-del-one-time">Eliminar</button>
    </div>
  </div>`;
}

function renderOneTime() {
  const container = $("one-time-container");
  if (!oneTime.length) {
    container.innerHTML = '<p class="muted">No hay envíos únicos. Programa uno con “+ Agregar envío único”.</p>';
    return;
  }
  container.innerHTML = oneTime.map(oneTimeCard).join("");
}

async function refreshOneTime() {
  try {
    oneTime = await api("/api/one-time");
    renderOneTime();
  } catch (e) {
    $("one-time-container").innerHTML = `<p class="muted" style="color:var(--red)">Error al cargar: ${escapeHtml(e.message)}</p>`;
  }
}

function updateOneTimeDelayFields() {
  $("o-delay-options").classList.toggle("hidden", !$("f-o-delay-enabled").checked);
}

function pad2(n) {
  return String(n).padStart(2, "0");
}

function openOneTimeModal() {
  $("modal-one-time-title").textContent = "Nuevo envío único";
  $("f-o-id").value = "";
  $("f-o-persona").value = contacts[0]?.key || "";
  $("f-o-text").value = "";
  $("f-o-dry-run").checked = false;
  $("f-o-delay-enabled").checked = false;
  $("f-o-delay-min").value = 1;
  $("f-o-delay-max").value = 60;
  const d = new Date(Date.now() + 60 * 60 * 1000);
  $("f-o-send-at").value = `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}T${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
  updateOneTimeDelayFields();
  $("modal-one-time").classList.remove("hidden");
  $("f-o-text").focus();
}

function closeOneTimeModal() {
  $("modal-one-time").classList.add("hidden");
}

async function saveOneTime(e) {
  e.preventDefault();
  const raw = $("f-o-send-at").value;
  const d = new Date(raw);
  const payload = {
    persona: $("f-o-persona").value,
    text: $("f-o-text").value.trim(),
    send_at: `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}T${pad2(d.getHours())}:${pad2(d.getMinutes())}:00`,
    dry_run: $("f-o-dry-run").checked,
    delay_enabled: $("f-o-delay-enabled").checked,
    delay_min: parseInt($("f-o-delay-min").value, 10) || 0,
    delay_max: parseInt($("f-o-delay-max").value, 10) || 60,
  };
  if (!payload.persona) {
    alert("Primero agrega un contacto en la pestaña “Contactos”.");
    return;
  }
  if (!payload.text) {
    alert("Escribe el mensaje.");
    return;
  }
  if (!raw || isNaN(d.getTime())) {
    alert("Indica una fecha y hora válidas.");
    return;
  }
  if (payload.delay_enabled && payload.delay_max < payload.delay_min) {
    alert("El retardo máximo debe ser mayor o igual al mínimo.");
    return;
  }
  try {
    await api("/api/one-time", { method: "POST", body: JSON.stringify(payload) });
    closeOneTimeModal();
    refreshOneTime();
    refreshStatus();
  } catch (err) {
    alert("Error: " + err.message);
  }
}

/* ---- status ---- */
async function refreshStatus() {
  try {
    const s = await api("/api/status");
    const pill = $("status-pill");
    if (s.scheduler) {
      pill.textContent = "scheduler activo";
      pill.className = "pill pill-ok";
    } else {
      pill.textContent = "scheduler detenido";
      pill.className = "pill pill-bad";
    }
    const up = s.started_at ? Math.round((Date.now() - new Date(s.started_at)) / 60000) : 0;
    const h = Math.floor(up / 60), m = up % 60;
    $("sched-info").textContent =
      `tick cada ${s.tick_seconds}s · grace ${s.grace_minutes}min · jobs ${s.jobs_total} (${s.jobs_enabled} activos) · envíos únicos ${s.one_time_total} (${s.one_time_pending} pend) · contactos ${s.contacts_total} · muestras ${s.muestras_total} · en espera ${s.runs_pending} · corriendo ${s.runs_running} · uptime ${h}h ${m}m`;
  } catch (e) {
    $("status-pill").textContent = "sin conexión";
    $("status-pill").className = "pill pill-bad";
  }
}

/* ---- modal add/edit job ---- */
function updateDaysFields() {
  const mode = document.querySelector('input[name="days_mode"]:checked').value;
  $("fixed-days").classList.toggle("hidden", mode !== "fixed");
  $("random-days").classList.toggle("hidden", mode !== "random");
}

function updateDelayFields() {
  $("delay-options").classList.toggle("hidden", !$("f-delay-enabled").checked);
}

function openModal(job) {
  editingId = job ? job.id : null;
  $("modal-title").textContent = job ? "Editar job" : "Nuevo job";
  $("f-id").value = job ? job.id : "";
  $("f-name").value = job ? job.name : "";
  $("f-persona").value = job ? job.persona : (contacts[0]?.key || "");
  $("f-time").value = job ? job.time : "09:00";
  $("f-dry-run").checked = job ? job.dry_run : false;
  $("f-enabled").checked = job ? job.enabled : true;
  $("f-delay-enabled").checked = job ? job.delay_enabled : false;
  $("f-delay-min").value = job && job.delay_min != null ? job.delay_min : 1;
  $("f-delay-max").value = job && job.delay_max != null ? job.delay_max : 60;

  const mode = job ? job.days_mode : "fixed";
  document.querySelector(`input[name="days_mode"][value="${mode}"]`).checked = true;
  document.querySelectorAll(".wd").forEach((cb) => {
    cb.checked = job && job.weekdays.includes(parseInt(cb.value, 10));
  });
  $("f-random").value = job && job.random_per_week ? job.random_per_week : 2;
  updateDaysFields();
  updateDelayFields();
  $("modal").classList.remove("hidden");
}

function closeModal() {
  $("modal").classList.add("hidden");
  editingId = null;
}

function collectForm() {
  const mode = document.querySelector('input[name="days_mode"]:checked').value;
  const weekdays = [...document.querySelectorAll(".wd:checked")].map((cb) => parseInt(cb.value, 10));
  return {
    name: $("f-name").value.trim(),
    persona: $("f-persona").value,
    time: $("f-time").value,
    days_mode: mode,
    weekdays: mode === "fixed" ? weekdays : [],
    random_per_week: mode === "random" ? parseInt($("f-random").value, 10) : null,
    dry_run: $("f-dry-run").checked,
    enabled: $("f-enabled").checked,
    delay_enabled: $("f-delay-enabled").checked,
    delay_min: parseInt($("f-delay-min").value, 10) || 0,
    delay_max: parseInt($("f-delay-max").value, 10) || 60,
  };
}

async function saveJob(e) {
  e.preventDefault();
  const payload = collectForm();
  if (payload.days_mode === "fixed" && payload.weekdays.length === 0) {
    alert("Selecciona al menos un día fijo.");
    return;
  }
  if (!payload.persona) {
    alert("Primero agrega un contacto en la pestaña “Contactos”.");
    return;
  }
  if (payload.delay_enabled && payload.delay_max < payload.delay_min) {
    alert("El retardo máximo debe ser mayor o igual al mínimo.");
    return;
  }
  try {
    if (editingId) {
      await api(`/api/jobs/${editingId}`, { method: "PUT", body: JSON.stringify(payload) });
    } else {
      await api("/api/jobs", { method: "POST", body: JSON.stringify(payload) });
    }
    closeModal();
    refreshJobs();
  } catch (err) {
    alert("Error: " + err.message);
  }
}

/* ---- historial ---- */
let historyJob = null;
async function openHistory(jobId) {
  historyJob = jobs.find((j) => j.id === jobId);
  $("history-title").textContent = `Historial — ${historyJob ? historyJob.name : ""}`;
  $("history-body").innerHTML = '<p class="muted">Cargando…</p>';
  $("modal-history").classList.remove("hidden");
  try {
    const runs = await api(`/api/jobs/${jobId}/runs?limit=50`);
    if (!runs.length) {
      $("history-body").innerHTML = '<p class="muted">Sin ejecuciones registradas.</p>';
      return;
    }
    $("history-body").innerHTML = runs.map((r) => {
      const out = r.output ? r.output : "(sin salida)";
      return `
      <div class="run">
        <div><span class="run-status ${escapeHtml(r.status)}">${escapeHtml(statusLabel(r.status))}</span>
        <span class="muted"> · ${escapeHtml(r.trigger)} · ${fmtDT(r.started_at)} → ${fmtDT(r.finished_at)}</span>
        <span class="muted"> · exit ${r.exit_code ?? "—"}</span></div>
        <div class="muted small">${escapeHtml(r.command)}</div>
        <pre>${escapeHtml(out)}</pre>
      </div>`;
    }).join("");
  } catch (err) {
    $("history-body").innerHTML = `<p class="muted" style="color:var(--red)">Error: ${escapeHtml(err.message)}</p>`;
  }
}

/* ---- events: tabs ---- */
document.querySelectorAll(".tab-btn").forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.tab)));

/* ---- events: jobs ---- */
$("btn-add").addEventListener("click", () => openModal(null));
$("btn-close-modal").addEventListener("click", closeModal);
$("btn-cancel").addEventListener("click", closeModal);
$("btn-close-history").addEventListener("click", () => $("modal-history").classList.add("hidden"));
$("btn-refresh").addEventListener("click", refreshJobs);
$("job-form").addEventListener("submit", saveJob);
document.querySelectorAll('input[name="days_mode"]').forEach((r) => r.addEventListener("change", updateDaysFields));
$("f-delay-enabled").addEventListener("change", updateDelayFields);

$("modal").addEventListener("click", (e) => { if (e.target === $("modal")) closeModal(); });
$("modal-history").addEventListener("click", (e) => { if (e.target === $("modal-history")) $("modal-history").classList.add("hidden"); });

$("jobs-container").addEventListener("click", async (e) => {
  const card = e.target.closest(".card");
  if (!card) return;
  const id = parseInt(card.dataset.id, 10);
  const job = jobs.find((j) => j.id === id);

  if (e.target.closest(".toggle")) {
    try {
      await api(`/api/jobs/${id}`, { method: "PUT", body: JSON.stringify({ ...job, enabled: !job.enabled }) });
      refreshJobs();
    } catch (err) { alert("Error: " + err.message); }
  } else if (e.target.closest(".btn-run")) {
    if (job && job.dry_run === false && !confirm(`Ejecutar “${job.name}” ahora? Se enviará un mensaje real por WhatsApp.`)) return;
    try {
      await api(`/api/jobs/${id}/run`, { method: "POST" });
      alert("Job lanzado.");
      refreshJobs();
    } catch (err) { alert("Error: " + err.message); }
  } else if (e.target.closest(".btn-history")) {
    openHistory(id);
  } else if (e.target.closest(".btn-edit")) {
    openModal(job);
  } else if (e.target.closest(".btn-del")) {
    if (!confirm(`Eliminar el job “${job.name}” y su historial?`)) return;
    try {
      await api(`/api/jobs/${id}`, { method: "DELETE" });
      refreshJobs();
    } catch (err) { alert("Error: " + err.message); }
  }
});

/* ---- events: contactos ---- */
$("btn-add-contact").addEventListener("click", () => openContactModal(null));
$("btn-close-contact").addEventListener("click", closeContactModal);
$("btn-cancel-contact").addEventListener("click", closeContactModal);
$("contact-form").addEventListener("submit", saveContact);
$("modal-contact").addEventListener("click", (e) => { if (e.target === $("modal-contact")) closeContactModal(); });

$("contacts-container").addEventListener("click", async (e) => {
  const card = e.target.closest(".card");
  if (!card) return;
  const id = parseInt(card.dataset.cid, 10);
  const contact = contacts.find((c) => c.id === id);

  if (e.target.closest(".btn-edit-contact")) {
    openContactModal(contact);
  } else if (e.target.closest(".btn-del-contact")) {
    if (!confirm(`Eliminar el contacto “${contact.name}” y sus muestras?`)) return;
    try {
      await api(`/api/contacts/${id}`, { method: "DELETE" });
      refreshContacts();
      refreshJobs();
      refreshMuestras();
    } catch (err) { alert("Error: " + err.message); }
  }
});

/* ---- events: muestras ---- */
$("btn-add-muestra").addEventListener("click", () => openMuestraModal(null));
$("btn-close-muestra").addEventListener("click", closeMuestraModal);
$("btn-cancel-muestra").addEventListener("click", closeMuestraModal);
$("muestra-form").addEventListener("submit", saveMuestra);
$("muestra-filter").addEventListener("change", (e) => { selectedPersona = e.target.value; renderMuestras(); });
$("modal-muestra").addEventListener("click", (e) => { if (e.target === $("modal-muestra")) closeMuestraModal(); });

$("muestras-container").addEventListener("click", async (e) => {
  const card = e.target.closest(".card");
  if (!card) return;
  const id = parseInt(card.dataset.mid, 10);
  const muestra = muestras.find((m) => m.id === id);

  if (e.target.closest(".btn-edit-muestra")) {
    openMuestraModal(muestra);
  } else if (e.target.closest(".btn-del-muestra")) {
    if (!confirm("Eliminar esta muestra?")) return;
    try {
      await api(`/api/muestras/${id}`, { method: "DELETE" });
      refreshMuestras();
    } catch (err) { alert("Error: " + err.message); }
  }
});

/* ---- events: envío único ---- */
$("btn-add-one-time").addEventListener("click", openOneTimeModal);
$("btn-close-one-time").addEventListener("click", closeOneTimeModal);
$("btn-cancel-one-time-modal").addEventListener("click", closeOneTimeModal);
$("one-time-form").addEventListener("submit", saveOneTime);
$("f-o-delay-enabled").addEventListener("change", updateOneTimeDelayFields);
$("modal-one-time").addEventListener("click", (e) => { if (e.target === $("modal-one-time")) closeOneTimeModal(); });

$("one-time-container").addEventListener("click", async (e) => {
  const card = e.target.closest(".card");
  if (!card) return;
  const id = parseInt(card.dataset.oid, 10);

  if (e.target.closest(".btn-cancel-one-time")) {
    if (!confirm("Cancelar este envío pendiente?")) return;
    try {
      await api(`/api/one-time/${id}/cancel`, { method: "POST" });
      refreshOneTime();
    } catch (err) { alert("Error: " + err.message); }
  } else if (e.target.closest(".btn-del-one-time")) {
    if (!confirm("Eliminar este envío?")) return;
    try {
      await api(`/api/one-time/${id}`, { method: "DELETE" });
      refreshOneTime();
      refreshStatus();
    } catch (err) { alert("Error: " + err.message); }
  }
});

/* ---- login ---- */
$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  TOKEN = $("login-token").value.trim();
  try {
    await api("/api/status");
    localStorage.setItem("wabot_token", TOKEN);
    hideLogin();
    refreshContacts();
    refreshJobs();
    refreshOneTime();
    refreshStatus();
  } catch (err) {
    // api() ya mostró el login y limpió el token
  }
});

async function boot() {
  if (!TOKEN) {
    showLogin();
    return;
  }
  try {
    await api("/api/status");
    hideLogin();
    refreshContacts();
    refreshJobs();
    refreshOneTime();
    refreshStatus();
  } catch (err) {
    // token inválido: api() ya mostró el login
  }
}

boot();
setInterval(refreshJobs, 30000);
setInterval(refreshStatus, 15000);
setInterval(refreshOneTime, 30000);

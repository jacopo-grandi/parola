// P.A.R.O.L.A. local UI: calls in the sidebar, the transcript in the middle, doubtful words to review.
"use strict";

// "mixed": an imported file, every voice on one track, so no side to name.
const LABELS = { mic: "Io", system: "Altri", mixed: "" };
const LEAD = 0.25;         // start playing a little before the word
const SPEEDS = [0.75, 1, 1.25, 1.5, 1.75, 2];
const EXCERPT_TAIL = 1.0;  // a doubtful run plays until this long after its last word
const LANGS = [["", "Auto"], ["it", "IT"], ["en", "EN"]];

const $main = document.getElementById("main");
const $calls = document.getElementById("list");
const $pending = document.getElementById("pending");
const $search = document.getElementById("search");
const $recorder = document.getElementById("recorder");
const $audio = document.getElementById("audio");

// ---------------------------------------------------------------- helpers

function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false || v === null || v === undefined) continue;
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children) if (c !== null && c !== undefined && c !== false) e.append(c);
  return e;
}

// 24px line icons, drawn here so nothing is fetched.
const ICONS = {
  play: '<path d="M8 5.5v13l10.5-6.5z" fill="currentColor"/>',
  pause: '<rect x="6.5" y="5" width="3.6" height="14" rx="1" fill="currentColor"/>' +
         '<rect x="13.9" y="5" width="3.6" height="14" rx="1" fill="currentColor"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="2.5" fill="currentColor"/>',
  popout: '<path d="M14 4h6v6M20 4l-8 8M18 14v4.5A1.5 1.5 0 0 1 16.5 20h-11A1.5 1.5 0 0 1 4 18.5' +
          'v-11A1.5 1.5 0 0 1 5.5 6H10"/>',
  prev: '<path d="M15 6l-6 6 6 6"/>',
  next: '<path d="M9 6l6 6-6 6"/>',
  undo: '<path d="M9 14L4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
  more: '<circle cx="5.5" cy="12" r="1.6" fill="currentColor" stroke="none"/>' +
        '<circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none"/>' +
        '<circle cx="18.5" cy="12" r="1.6" fill="currentColor" stroke="none"/>',
  filter: '<path d="M4 7h16M7 12h10M10 17h4"/>',
  upload: '<path d="M12 15V4M7.5 8.5L12 4l4.5 4.5"/><path d="M5 14v4.5A1.5 1.5 0 0 0 6.5 20h11' +
          'a1.5 1.5 0 0 0 1.5-1.5V14"/>',
  close: '<path d="M7 7l10 10M17 7L7 17"/>',
  people: '<circle cx="9" cy="8" r="3.2"/><path d="M3.5 19c.6-3.2 2.8-5 5.5-5s4.9 1.8 5.5 5"/>' +
          '<circle cx="17" cy="9" r="2.4"/><path d="M15.8 14.2c2.4.1 4 1.7 4.7 4.3"/>',
  gear: '<circle cx="12" cy="12" r="5.2"/><circle cx="12" cy="12" r="1.7"/>' +
        '<path d="M17.2 12h2.2M12 17.2v2.2M6.8 12H4.6M12 6.8V4.6M15.68 15.68l1.56 1.56' +
        'M8.32 15.68l-1.56 1.56M8.32 8.32L6.76 6.76M15.68 8.32l1.56-1.56"/>',
};

function icon(name, size = 16) {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("width", size);
  s.setAttribute("height", size);
  s.setAttribute("aria-hidden", "true");
  s.setAttribute("fill", "none");
  s.setAttribute("stroke", "currentColor");
  s.setAttribute("stroke-width", "1.8");
  s.setAttribute("stroke-linecap", "round");
  s.setAttribute("stroke-linejoin", "round");
  s.innerHTML = ICONS[name];
  return s;
}

function iconButton(name, label, onclick, cls = "") {
  return el("button", { class: `icon-btn ${cls}`, title: label, "aria-label": label, onclick },
    icon(name));
}

const enc = encodeURIComponent;

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}

async function postJSON(url, body) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
                               body: JSON.stringify(body || {}) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `${r.status}`);
  return data;
}

let toastTimer = null;
function toast(message, action) {
  // A message with an action (Annulla) stays until it is used or closed; plain ones fade.
  const t = document.getElementById("toast");
  const parts = [el("span", {}, message)];
  if (action) {
    parts.push(el("button", { class: "toast-action", onclick: () => { hideToast(); action.run(); } },
      action.label));
    parts.push(iconButton("close", "Chiudi l'avviso", hideToast, "toast-close"));
  }
  t.replaceChildren(...parts);
  t.classList.add("show");
  clearTimeout(toastTimer);
  if (!action) toastTimer = setTimeout(hideToast, 4000);
}

function hideToast() {
  const t = document.getElementById("toast");
  t.classList.remove("show");
  setTimeout(() => { if (!t.classList.contains("show")) t.replaceChildren(); }, 250);
}

function popMenu(anchor, ...items) {
  // A small menu under `anchor`; closes on a choice, a click elsewhere or Escape.
  document.querySelectorAll(".menu").forEach(m => m.remove());
  const menu = el("div", { class: "menu", role: "menu" }, ...items);
  const close = () => {
    menu.remove();
    document.removeEventListener("click", outside, true);
    document.removeEventListener("keydown", onKey, true);
  };
  const outside = e => { if (!menu.contains(e.target)) close(); };
  const onKey = e => {
    if (e.key === "Escape") { e.stopPropagation(); close(); anchor.querySelector("button")?.focus(); }
  };
  menu.addEventListener("click", e => { if (e.target.closest("a, button")) close(); });
  anchor.append(menu);
  setTimeout(() => {
    document.addEventListener("click", outside, true);
    document.addEventListener("keydown", onKey, true);
  });
  menu.querySelector("a, button")?.focus();
  return menu;
}

// ---------------------------------------------------------------- sidebar

let calls = [];   // summaries from /api/recordings, newest first

async function refreshCalls() {
  calls = await getJSON("/api/recordings");
  renderCalls();
}

function renderCalls() {
  if (searchQuery()) { renderResults(); return; }
  const q = $search.value.trim().toLowerCase();
  const shown = calls.filter(c => !q || c.title.toLowerCase().includes(q) ||
                                  c.name.toLowerCase().includes(q));
  const items = [];
  let group = null;
  for (const c of shown) {
    const date = new Date(c.started_at);
    const g = groupOf(date);
    if (g !== group) items.push(el("div", { class: "group" }, group = g));
    const title = el("span", { class: "title" }, c.title);
    const sub = el("span", { class: "sub" }, `${shortWhen(date)} · ${minutes(c.duration)}`,
      c.error ? el("span", { class: "n" }, " · trascrizione illeggibile") :
      c.to_review ? el("span", { class: "n" }, ` · ${c.to_review} da rivedere`) : null);
    // The full title on hover: the row truncates it.
    const a = el("a", { class: "call", href: `#/rec/${enc(c.name)}`, title: c.title }, title, sub);
    a.addEventListener("dblclick", e => { e.preventDefault(); renameInPlace(title, c); });
    const row = el("div", { class: "call-row" + (c.name === recName ? " active" : "") }, a);
    row.append(iconButton("more", `Altre azioni per ${c.title}`, e => {
      e.stopPropagation();
      popMenu(row,
        el("button", { role: "menuitem", onclick: () => renameInPlace(title, c) }, "Rinomina"),
        el("button", { role: "menuitem", onclick: () => suggestTitle(title, c) },
          "Titolo dal contenuto"),
        el("button", { role: "menuitem", class: "danger", onclick: () => deleteCall(c) },
          "Elimina"));
    }, "call-more"));
    items.push(row);
  }
  if (!items.length) {
    items.push(el("p", { class: "empty-list" },
      q ? `Nessuna call per “${$search.value.trim()}”.` : "Ancora nessuna call."));
  }
  $calls.replaceChildren(...items);
}

async function suggestTitle(host, call) {
  // Key phrases of the call, proposed in the rename field: the person confirms or edits.
  let s;
  try {
    s = await getJSON(`/api/recordings/${enc(call.name)}/title-suggestion`);
  } catch (e) { toast(`Nessun titolo suggerito: ${e.message}`); return; }
  if (!s.title) { toast("La call è troppo breve per suggerire un titolo."); return; }
  renameInPlace(host, call, s.title);
}

async function deleteCall(call) {
  let res;
  try {
    res = await fetch(`/api/recordings/${enc(call.name)}`, { method: "DELETE" });
    if (!res.ok) throw new Error((await res.json()).detail || res.status);
    res = await res.json();
  } catch (e) { toast(`Call non eliminata: ${e.message}`); return; }
  if (call.name === recName) location.hash = "#/";
  await refreshCalls();
  toast(`“${call.title}” eliminata`, { label: "Annulla", run: async () => {
    try {
      await postJSON(`/api/trash/${enc(res.trashed)}/restore`);
      await refreshCalls();
      location.hash = `#/rec/${enc(call.name)}`;
    } catch (e) { toast(`Call non ripristinata: ${e.message}`); }
  } });
}

function renameInPlace(host, call, proposal) {
  // `proposal` (a suggested title) fills the field; Enter keeps it, Esc leaves the old name.
  const before = call.title;
  const input = el("input", { class: "inline", value: proposal || before, maxlength: "120",
                              "aria-label": "Nome della call" });
  host.replaceChildren(input);
  input.focus();
  input.select();
  let finished = false;
  const finish = async save => {
    if (finished) return;
    finished = true;
    const title = input.value.trim();
    if (save && title && title !== before) {
      try {
        await postJSON(`/api/recordings/${enc(call.name)}/rename`, { title });
        if (rec && call.name === recName) rec.summary.title = title;
      } catch (e) { toast(`Non rinominata: ${e.message}`); }
    }
    await refreshCalls();
    if (rec && call.name === recName) renderHeading();
  };
  input.addEventListener("keydown", e => {
    e.stopPropagation();
    if (e.key === "Enter") { e.preventDefault(); finish(true); }
    else if (e.key === "Escape") { e.preventDefault(); finish(false); }
  });
  input.addEventListener("click", e => { e.preventDefault(); e.stopPropagation(); });
  input.addEventListener("blur", () => finish(true));
}

// ---------------------------------------------------------------- search

let results = null;     // {q, calls} from /api/search for the query in the box
let searchTimer = null;
let searchSeq = 0;

function searchQuery() {
  const q = $search.value.trim();
  return q.length >= 2 ? q : "";
}

async function runSearch() {
  const q = searchQuery();
  const seq = ++searchSeq;
  if (!q) { results = null; renderCalls(); return; }
  try {
    const calls = await getJSON(`/api/search?q=${enc(q)}`);
    if (seq === searchSeq) { results = { q, calls }; renderCalls(); }  // drop late answers
  } catch (e) { /* server restarting: keep the last results */ }
}

$search.addEventListener("input", () => {
  clearTimeout(searchTimer);
  if (!searchQuery()) { results = null; renderCalls(); return; }
  searchTimer = setTimeout(runSearch, 180);
});
$search.addEventListener("keydown", e => {
  if (e.key === "Escape") { $search.value = ""; results = null; renderCalls(); $search.blur(); }
});

function hitLink(call, q, i) {
  return `#/rec/${enc(call.name)}?q=${enc(q)}&hit=${i}`;
}

function renderResults() {
  const q = searchQuery();
  if (!results || results.q !== q) {
    $calls.replaceChildren(el("p", { class: "empty-list" }, "Cerco…"));
    return;
  }
  const items = [el("div", { class: "group" }, "Risultati")];
  for (const c of results.calls) {
    const sub = c.count
      ? `${c.count} ${c.count === 1 ? "volta" : "volte"} · ${shortWhen(new Date(c.started_at))}`
      : `nel titolo · ${shortWhen(new Date(c.started_at))}`;
    const row = el("div", { class: "call-row" + (c.name === recName ? " active" : "") },
      el("a", { class: "call", href: c.count ? hitLink(c, q, 0) : `#/rec/${enc(c.name)}`,
                title: c.title },
        el("span", { class: "title" }, c.title), el("span", { class: "sub" }, sub)));
    items.push(row);
    c.hits.forEach((h, i) => {
      items.push(el("a", { class: "snip", href: hitLink(c, q, i) },
        el("span", { class: "snip-t" }, clock(h.time)),
        h.before ? `…${h.before} ` : "", el("mark", {}, h.match), h.after ? ` ${h.after}…` : ""));
    });
  }
  if (!results.calls.length) {
    items.push(el("p", { class: "empty-list" }, `Nessuna call contiene “${q}”.`));
  }
  $calls.replaceChildren(...items);
}

// ---------------------------------------------------------------- transcript

let rec = null;      // recording being shown
let recName = null;
let players = {};    // channel -> <audio>
let gains = {};      // channel -> linear playback gain (1 = as recorded)
let audioCtx = null; // Web Audio, created on the first boosted play (a user gesture)
let words = [];      // flat list of {el, channel, start, end, doubtful, seg, k}
let runs = [];       // doubtful runs still to review: {seg, start, end, t, channel}
let current = null;  // word element being played
let reviewFrom = null;  // where N continues from after a correction (Invio stays put)
let target = null;   // word chosen by click or N/P, until playback reaches it
let editing = null;  // {seg, start, end}
let stopAt = null;   // {channel, t}: stop playback there (excerpt of a doubtful run)
let lastChannel = null;
let onlyDoubts = false;
let view = {};       // elements of the call view that are updated in place

// Playback speed, remembered in this browser only (a convenience, not data).
let speed = 1;
try {
  const saved = Number(localStorage.getItem("speed"));
  if (SPEEDS.includes(saved)) speed = saved;
} catch (e) { /* storage blocked: start at 1x */ }

function setSpeed(v) {
  speed = v;
  for (const p of Object.values(players)) p.playbackRate = v;
  try { localStorage.setItem("speed", String(v)); } catch (e) { /* not remembered */ }
  if (view.speed) view.speed.classList.toggle("on", v !== 1);
}

function stopAll() {
  for (const p of Object.values(players)) p.pause();
}

function playing() {
  return Object.values(players).find(p => !p.paused) || null;
}

function duration() {
  const segs = rec ? rec.segments : [];
  return (rec && rec.summary.duration) || (segs.length ? segs[segs.length - 1].end : 0);
}

// A quiet channel is raised at playback only, never in the stored file: the element is
// routed through a gain and a limiter (a boosted track can hold a full-scale pop that a
// bare gain would clip). Wired lazily on the first play — a user gesture, so the context
// may start — and only when there is something to raise: WebKit routes a wired element
// through the context alone, so the others keep the plain path.
function amplify(channel) {
  const p = players[channel];
  const g = gains[channel] || 1;
  const Ctx = window.AudioContext || window.webkitAudioContext;
  if (!p || g === 1 || p.dataset.wired || !Ctx) return;
  try {
    audioCtx = audioCtx || new Ctx();
    const source = audioCtx.createMediaElementSource(p);
    const gain = audioCtx.createGain();
    gain.gain.value = g;
    const limiter = audioCtx.createDynamicsCompressor();
    limiter.threshold.value = -1; // squash only what would clip
    limiter.knee.value = 0;
    limiter.ratio.value = 20;
    limiter.attack.value = 0.001;
    limiter.release.value = 0.05;
    source.connect(gain);
    gain.connect(limiter);
    limiter.connect(audioCtx.destination);
    p.dataset.wired = "1";
  } catch (e) { /* no Web Audio: play as recorded */ }
  if (audioCtx && audioCtx.state === "suspended") audioCtx.resume().catch(() => {});
}

// The channels are parallel tracks on one clock (the capture pads them to the same length), so
// listening plays them all in sync: the conversation is heard whole, whoever is speaking.
// An excerpt (`until`, reviewing a doubtful run) plays its side alone, to hear those words clean.
function play(word, until) {
  const p = players[word.channel];
  if (!p) return;
  const t = Math.max(0, word.start - LEAD);
  const solo = until !== undefined;
  for (const [c, q] of Object.entries(players)) {
    if (c === word.channel) continue;
    if (solo) { q.pause(); continue; }
    amplify(c);
    q.currentTime = t;
    q.play().catch(() => {});
  }
  amplify(word.channel);
  p.currentTime = t;
  stopAt = solo ? { channel: word.channel, t: until } : null;
  lastChannel = word.channel;
  p.play().catch(() => {});
  target = word;
  mark(word.el);
}

function togglePlay() {
  if (playing()) { stopAll(); return; }
  const w = current && words.find(w => w.el === current);
  const channel = w ? w.channel : lastChannel || Object.keys(players)[0];
  const q = players[channel];
  if (!q) return;
  stopAt = null;
  lastChannel = channel;
  const t = q.currentTime;
  for (const [c, r] of Object.entries(players)) {
    amplify(c);
    if (c !== channel) r.currentTime = t;
    r.play().catch(() => {});
  }
}

function seek(seconds) {
  // Jump to the word being said at that time (on any channel), or just there, and show it.
  const before = words.filter(w => w.start <= seconds);
  const w = before.length ? before.reduce((a, b) => (b.start > a.start ? b : a)) : words[0];
  if (w) { play(w); keepInView(w.el, true); }
}

// The transcript follows the word being played. When the person scrolls it (to read ahead or
// back) it waits FOLLOW_PAUSE before following again; a click on the timeline always shows it.
const FOLLOW_PAUSE = 4000;  // ms
let scrolledByHand = 0;

function keepInView(e, force) {
  const box = view.scroll;
  if (!box || !e || (!force && Date.now() - scrolledByHand < FOLLOW_PAUSE)) return;
  const r = e.getBoundingClientRect(), b = box.getBoundingClientRect();
  const margin = b.height * 0.15;
  if (r.top < b.top + margin || r.bottom > b.bottom - margin) {
    e.scrollIntoView({ block: "center", behavior: "smooth" });
  }
}

function mark(e) {
  if (current) current.classList.remove("now");
  current = e;
  if (e) e.classList.add("now");
}

function follow(channel) {
  const p = players[channel];
  const t = p.currentTime;
  if (stopAt && stopAt.channel === channel && t >= stopAt.t) {
    stopAll();
    stopAt = null;
  }
  if (channel !== lastChannel) return;  // every track shares the clock: one drives the page
  updateProgress(t);
  // Playback starts LEAD seconds early: keep the chosen word highlighted until it is reached.
  if (target && t < target.start) return;
  target = null;
  // Whoever is speaking now, on any channel; both at once (an overlap): the side chosen last.
  // A solo excerpt stays on its own side, the other words are not being played.
  const at = words.filter(w => w.start <= t && t < w.end);
  const w = at.find(w => w.channel === channel) || (stopAt ? null : at[0]);
  if (w && w.el !== current) {
    mark(w.el);
    if (!p.paused) keepInView(w.el);
  }
}

function updateProgress(t) {
  if (!view.done) return;
  const total = duration();
  view.done.style.width = total ? `${Math.min(100, (t / total) * 100)}%` : "0";
  view.clock.textContent = `${clock(t)} / ${clock(total)}`;
}

function updatePlayButton() {
  if (!view.play) return;
  const on = !!playing();
  view.play.replaceChildren(icon(on ? "pause" : "play", 14));
  view.play.setAttribute("aria-label", on ? "Pausa" : "Riproduci");
  view.play.title = on ? "Pausa (spazio)" : "Riproduci (spazio)";
}

async function post(action, body) {
  return postJSON(`/api/recordings/${enc(recName)}/${action}`, body);
}

function wordAt(seg, k) {
  return words.find(w => w.seg === seg && w.k === k);
}

function openEditor(seg, start, end, autoplay) {
  closeEditor();
  if (rec.provisional) return;  // read-only until the final transcript is written
  const first = wordAt(seg, start);
  if (!first) return;
  editing = { seg, start, end };
  const text = rec.segments[seg].words.slice(start, end).map(w => w.text).join(" ");
  const input = el("input", { class: "edit", value: text, size: Math.max(6, text.length + 2),
                              "aria-label": "Correggi",
                              title: "Invio: salva e resta (senza modifiche conferma) · Esc: chiudi" });
  first.el.before(input);
  for (let k = start; k < end; k++) wordAt(seg, k).el.classList.add("editing");
  input.addEventListener("keydown", async e => {
    if (e.key === "Escape") {
      e.preventDefault();
      stopAll();
      closeEditor();
    } else if (e.key === "Enter") {
      e.preventDefault();
      try {
        await render(await post("correct", { segment: seg, start, end, text: input.value }),
                     { seg, start });
      } catch (err) { toast(err.message); }
    } else if (e.key === " " && e.ctrlKey) { e.preventDefault(); playRun(seg, start, end); }
    else if (e.key === "Tab" && suggestions.length) {
      e.preventDefault();  // Tab takes the first suggestion; Enter still decides
      useSuggestion(input, suggestions[0]);
    }
  });
  input.focus();
  input.select();
  first.el.scrollIntoView({ block: "center", behavior: "smooth" });
  if (autoplay) playRun(seg, start, end);
  showSuggestions(input, text, editing);
}

// Terms learned from your own corrections (decision 0025): offered, never applied.
let suggestions = [];

async function showSuggestions(input, text, opened) {
  suggestions = [];
  let found = [];
  try { found = await getJSON(`/api/suggestions?text=${enc(text)}`); } catch (e) { return; }
  if (editing !== opened || !found.length) return;  // the editor moved on meanwhile
  suggestions = found;
  const box = el("span", { class: "suggest", role: "group", "aria-label": "Suggerimenti" },
    ...found.map((s, i) => el("button", {
      class: "chip", title: `Hai corretto così ${s.count} volte${i === 0 ? " · Tab per usarlo" : ""}`,
      onmousedown: e => e.preventDefault(),  // keep the focus in the field
      onclick: () => useSuggestion(input, s) }, s.term, el("span", { class: "n" }, `×${s.count}`))));
  input.after(box);
}

function useSuggestion(input, s) {
  input.value = s.text;
  input.size = Math.max(6, s.text.length + 2);
  input.focus();
}

function playRun(seg, start, end) {
  const first = wordAt(seg, start), last = wordAt(seg, end - 1);
  if (first) play(first, (last || first).end + EXCERPT_TAIL);
}

function closeEditor() {
  document.querySelectorAll("input.edit, .suggest").forEach(i => i.remove());
  suggestions = [];
  document.querySelectorAll(".editing").forEach(e => e.classList.remove("editing"));
  editing = null;
}

function nextRun(direction, from) {
  const here = current ? (words.find(w => w.el === current) || {}).start : undefined;
  const pos = from ?? here ?? reviewFrom ?? -1;
  return pickRun(runs, direction, pos);
}

function jump(direction) {
  const r = nextRun(direction);
  if (r) openEditor(r.seg, r.start, r.end, true);
}

async function undo() {
  if (!rec || rec.provisional) return;
  try { await render(await post("undo"), {}); } catch (e) { toast("Niente da annullare."); }
}

function renderHeading() {
  const s = rec.summary;
  const h1 = el("h1", { title: rec.provisional ? s.title : `${s.title} (doppio clic per rinominare)` },
                s.title);
  if (!rec.provisional) h1.addEventListener("dblclick", () => renameInPlace(h1, s));
  const when = new Date(s.started_at).toLocaleString("it-IT",
    { weekday: "long", day: "numeric", month: "long", year: "numeric", hour: "2-digit",
      minute: "2-digit" });
  const who = s.channels.map(c => LABELS[c] ?? c).filter(Boolean).join(" e ");
  const meta = `${when} · ${clock(s.duration)} · ${s.words.toLocaleString("it-IT")} parole` +
               (who ? ` · ${who}` : "") + (s.source ? ` · da ${s.source}` : "");
  view.heading.replaceChildren(h1, el("div", { class: "meta" }, meta));
}

function exportMenu(anchor) {
  const link = (f, label) => el("a", { role: "menuitem", href: `/export/${enc(recName)}.${f}` },
    label);
  popMenu(anchor,
    link("txt", "Testo (.txt)"), link("md", "Markdown (.md)"), link("docx", "Word (.docx)"),
    link("json", "Dati (.json)"), el("hr"),
    el("button", { role: "menuitem", onclick: () => window.print() }, "Stampa o salva PDF"));
}

function relabel() {
  // The speaker is written only when it changes, counting only the visible lines.
  let prev = null;
  for (const turn of view.body.querySelectorAll(".turn")) {
    if (turn.classList.contains("hidden") || turn.classList.contains("fold")) continue;
    const who = turn.querySelector(".who");
    who.hidden = !who.textContent || turn.dataset.key === prev;
    prev = turn.dataset.key;
  }
}

function applyFilter() {
  for (const turn of view.body.querySelectorAll(".turn")) {
    turn.classList.toggle("hidden", onlyDoubts && turn.dataset.doubt !== "true");
  }
  view.filter.classList.toggle("on", onlyDoubts);
  view.filter.setAttribute("aria-pressed", onlyDoubts);
  relabel();
}

async function render(data, after) {
  const scrollY = view.scroll ? view.scroll.scrollTop : 0;
  rec = data;
  words = [];
  runs = [];
  current = null;
  reviewFrom = null;  // set again below after a correction; a different call starts clean
  editing = null;
  view = {};
  view.turns = [];  // one {el, text, si} per segment, for the translation lines (ToTTI, 0038)
  if (transName !== recName) { transOn = false; clearTimeout(transTimer); }

  const body = el("div", { class: "transcript" });
  // Identical doubtful lines in a row ("Thank you." written over a noisy stretch, decision
  // 0040): the transcript keeps them all, the view shows the first with "× N" and folds the
  // rest until the badge is clicked. Lines are compared by their repeating unit, so "Thank
  // you. Thank you." folds with "Thank you.", and the speaker is ignored: on noise the
  // diarizer assigns voices as arbitrarily as Whisper writes the words. One doubtful word
  // is enough (both backends often hallucinate the same "you"); a line with every word
  // confirmed never folds.
  const folded = repeatedRuns(rec.segments.map(s => ({
    text: repeatUnit(s.words.map(w => fold(w.text))),
    doubtful: s.words.some(w => w.doubtful),
    key: s.channel || "mic",
  })));
  const leads = new Map(folded.map(f => [f.start, f]));
  const inFold = new Map(); // member index (after the first) -> the run it belongs to
  for (const f of folded) for (let i = f.start + 1; i < f.end; i++) inFold.set(i, f);
  rec.segments.forEach((seg, si) => {
    const channel = seg.channel || "mic";
    const text = el("div", { class: "text" });
    const list = seg.words.length ? seg.words : [{ text: seg.text, start: seg.start, end: seg.end }];
    // Folded lines are left out of the N-cycle: reviewing the shown one stands for the run.
    if (!inFold.has(si)) runs.push(...doubtfulRuns(list, si, channel, seg.start));
    list.forEach((w, k) => {
      const start = w.start ?? seg.start, end = w.end ?? seg.end;
      const span = el("span", { class: "w" + (w.doubtful ? " doubt" : "") }, w.text);
      if (w.doubtful) span.title = "Dubbia: la seconda trascrizione dice altro";
      const word = { el: span, channel, start, end, doubtful: !!w.doubtful, seg: si, k };
      span.addEventListener("click", () => play(word));
      span.addEventListener("dblclick", e => {
        e.preventDefault();
        const run = runs.find(r => r.seg === si && r.start <= k && k < r.end);
        if (run) openEditor(si, run.start, run.end, true);
        else openEditor(si, k, k + 1, true);
      });
      words.push(word);
      text.append(span, k < list.length - 1 ? " " : "");
    });
    // One line looping a phrase ("and check out this page and check out this page and..."):
    // the repeats after the first fold in place, behind the same kind of badge.
    const loop = wordLoop(list.map(w => ({ text: fold(w.text || ""), doubtful: !!w.doubtful })));
    if (loop) {
      const from = loop.start + loop.len, to = loop.start + loop.repeats * loop.len;
      const spans = [...text.querySelectorAll(".w")];
      for (let k = from; k < to; k++) spans[k].classList.add("fold-w");
      const badge = el("button", { class: "fold-count", "aria-expanded": "false",
        title: `Frase dubbia ripetuta ${loop.repeats} volte di seguito (spesso rumore ` +
               "trascritto male): clic per mostrare o nascondere le ripetizioni" },
        `× ${loop.repeats}`);
      badge.addEventListener("click", () => {
        const open = badge.getAttribute("aria-expanded") === "true";
        for (let k = from; k < to; k++) spans[k].classList.toggle("fold-w", open);
        badge.setAttribute("aria-expanded", String(!open));
      });
      spans[from - 1].after(" ", badge);
    }
    const lead = leads.get(si);
    if (lead) {
      const n = lead.end - lead.start;
      const badge = el("button", { class: "fold-count", "aria-expanded": "false",
        title: `Riga dubbia ripetuta ${n} volte di seguito (spesso rumore trascritto ` +
               "male): clic per mostrare o nascondere le altre" }, `× ${n}`);
      badge.addEventListener("click", () => {
        const open = badge.getAttribute("aria-expanded") === "true";
        for (const t of body.querySelectorAll(`.turn[data-fold="${lead.start}"]`)) {
          t.classList.toggle("fold", open);
        }
        badge.setAttribute("aria-expanded", String(!open));
        relabel();
      });
      text.append(" ", badge);
    }
    const first = words[words.length - list.length];
    // lang per line: an English line is hyphenated and read aloud as English.
    const voiced = VOICED.includes(channel) && Object.keys(rec.voices || {}).length > 0;
    const key = showVoices && seg.speaker ? seg.speaker : channel;
    const who = showVoices && seg.speaker
      ? el("button", { class: `who voice ${channel}`, title: "Clic per dare un nome a questa voce",
                       onclick: e => renameVoice(e.currentTarget, seg.speaker) },
          rec.voices[seg.speaker] || seg.speaker)
      : el("div", { class: `who ${channel}` }, LABELS[channel] ?? channel);
    const turn = el("div", { class: inFold.has(si) ? "turn fold" : "turn",
                             "data-fold": inFold.has(si) ? String(inFold.get(si).start) : null,
                             lang: seg.lang || null, "data-channel": channel,
                             // String(), not the boolean: el() writes `true` as an empty
                             // attribute, and the doubts filter compares against "true".
                             "data-key": key, "data-doubt": String(seg.words.some(w => w.doubtful)) },
      who,
      // The voice menu lives in the time column: its clicks must not start the audio.
      el("span", { class: "t", onclick: e => { if (!e.target.closest(".menu, .turn-voice")) play(first); } },
        clock(seg.start),
        voiced && showVoices ? el("button", {
          class: "turn-voice", title: "Di chi è questa battuta", "aria-label": "Cambia voce",
          onclick: e => { e.stopPropagation(); voiceMenu(e.currentTarget, si); } }, "•") : null),
      text);
    body.append(turn);
    view.turns.push({ el: turn, text, si });
  });
  if (rec.provisional) {
    // Whisper is done, the rest is still running (decision 0031): the text can be read and
    // listened to, and is replaced by the final one, so nothing can be corrected yet.
    view.stage = el("span", { class: "stage" });
    body.prepend(el("p", { class: "hint provisional" },
      rec.provisional === "partial"
        ? "Whisper sta ancora trascrivendo: il testo cresce man mano, poi arrivano parole " +
          "dubbie e chi parla. "
        : rec.provisional === "doubts"
          ? "Testo provvisorio con le parole dubbie: chi parla arriva tra poco. "
          : "Testo provvisorio: parole dubbie e chi parla arrivano tra poco. ",
      "Puoi leggerlo e ascoltarlo; le correzioni quando è pronto.", view.stage));
  } else if (runs.length) {
    body.prepend(el("p", { class: "hint" },
      "Clic su una parola per ascoltarla. ", el("kbd", {}, "N"), " passa alla prossima dubbia, ",
      el("kbd", {}, "Invio"), " salva o conferma, ", el("kbd", {}, "Esc"), " chiude, ",
      el("kbd", {}, "⌘Z"), " annulla."));
  }

  view.heading = el("div", { class: "heading" });
  view.filter = iconButton("filter", "Solo le righe con parole dubbie",
    () => { onlyDoubts = !onlyDoubts; applyFilter(); });
  // Voices (decision 0026): shown by default, hidden with one click when they are more noise
  // than help; the choice is remembered in this browser.
  const voicesBtn = Object.keys(rec.voices || {}).length
    ? iconButton("people", showVoices ? "Nascondi chi parla" : "Mostra chi parla", () => {
        showVoices = !showVoices;
        try { localStorage.setItem("voices", showVoices ? "1" : "0"); } catch (e) { /* not kept */ }
        render(rec, {});
      }, showVoices ? "on-voices" : "")
    : null;
  const more = iconButton("more", "Esporta", e => { e.stopPropagation(); exportMenu(actions); });
  const undoBtn = iconButton("undo", "Annulla l'ultima modifica (⌘Z)", undo);
  undoBtn.disabled = !rec.summary.edits || !!rec.provisional;
  // Translation EN→IT (ToTTI, 0038): only with the model on disk and a final transcript.
  view.transNote = el("span", { class: "trans-note" });
  view.transBtn = subsAvailable && !rec.provisional
    ? el("button", { class: "icon-btn trans-btn", title: "Traduci in italiano",
                     "aria-label": "Traduci in italiano", "aria-pressed": "false",
                     onclick: transAction }, "IT")
    : null;
  updateTransBtn();
  // Exports read the final file: none while the transcript is provisional.
  const actions = el("div", { class: "actions" }, view.transNote, view.transBtn,
                     voicesBtn, view.filter, undoBtn,
                     rec.provisional ? null : more);
  view.findInput = el("input", { class: "find-input", type: "search",
                                 placeholder: "Trova nella call  ⌘F",
                                 "aria-label": "Trova nella call", autocomplete: "off" });
  view.findCount = el("span", { class: "find-count", "aria-live": "polite" });
  view.findInput.addEventListener("input", () => {
    find = { q: view.findInput.value, index: 0, hits: [] };
    applyFind(true);
  });
  view.findInput.addEventListener("keydown", e => {
    e.stopPropagation();
    if (e.key === "Enter") { e.preventDefault(); stepFind(e.shiftKey ? -1 : 1); }
    else if (e.key === "Escape") { e.preventDefault(); closeFind(); view.findInput.blur(); }
  });
  // Always there, like the search field of a Mac app; the arrows show once something is typed.
  view.findNav = el("span", { class: "find-nav" }, view.findCount,
    iconButton("prev", "Precedente (Maiusc-Invio)", () => stepFind(-1)),
    iconButton("next", "Successiva (Invio)", () => stepFind(1)),
    iconButton("close", "Svuota la ricerca (Esc)", closeFind));
  view.find = el("div", { class: "findbar" }, view.findInput, view.findNav);
  view.findNav.hidden = true;
  // A channel that recorded silence or the wrong device: said on the call itself, so a dead
  // mic is discovered here and not weeks later (see recorder.save_warnings).
  const warns = ((rec.summary || {}).warnings || []).map(w => {
    const dev = w.device ? ` (ingresso: ${w.device})` : "";
    const text = w.channel === "mic"
      ? `Il microfono ha registrato quasi solo silenzio${dev}: la tua voce non è in questa call.`
      : w.channel === "system"
      ? `L'audio di sistema risulta muto: la voce degli altri non è in questa call.`
      : `Il canale ${w.channel} risulta muto.`;
    return el("p", { class: "call-warn", role: "alert" }, `⚠ ${text}`);
  });
  const head = el("header", { class: "call-head" },
    el("div", { class: "head-row" }, view.heading, view.find, actions), ...warns);

  view.body = body;
  view.scroll = el("div", { class: "scroll" }, body);
  const byHand = () => { scrolledByHand = Date.now(); };
  view.scroll.addEventListener("wheel", byHand, { passive: true });
  view.scroll.addEventListener("touchmove", byHand, { passive: true });
  view.scroll.addEventListener("pointerdown", e => { if (e.target === view.scroll) byHand(); });

  const total = duration();
  view.play = el("button", { class: "play", onclick: togglePlay });
  view.clock = el("span", { class: "clock" });
  view.done = el("div", { class: "done" });
  const timeline = el("div", { class: "timeline", title: "Vai a questo punto" },
    el("div", { class: "track" }), view.done,
    ...runs.map(r => {
      const tick = el("i", { class: "tick" });
      tick.style.left = `${total ? (r.t / total) * 100 : 0}%`;
      return tick;
    }));
  timeline.addEventListener("click", e => {
    const box = timeline.getBoundingClientRect();
    seek(((e.clientX - box.left) / box.width) * total);
  });
  const review = rec.provisional
    ? el("div", { class: "review" }, ...(runs.length ? [el("b", {}, String(runs.length)), "dubbie · "] : []),
         "provvisoria")
    : runs.length
    ? el("div", { class: "review" }, iconButton("prev", "Dubbia precedente (P)", () => jump(-1)),
         el("b", {}, String(runs.length)), "da rivedere",
         iconButton("next", "Prossima dubbia (N)", () => jump(1)))
    : el("div", { class: "review clear" }, el("b", {}, "✓"), "Tutto rivisto");
  view.speed = el("select", { class: "speed", title: "Velocità di riproduzione",
                              "aria-label": "Velocità di riproduzione",
                              onchange: e => setSpeed(Number(e.target.value)) },
    ...SPEEDS.map(v => el("option", { value: String(v), selected: v === speed }, speedLabel(v))));
  view.speed.classList.toggle("on", speed !== 1);
  const player = el("footer", { class: "player" }, view.play, view.clock, timeline, view.speed,
    review);

  $main.replaceChildren(head, view.scroll, player);
  renderHeading();
  applyFilter();
  updatePlayButton();
  if (find) applyFind(false);
  const p = lastChannel && players[lastChannel];
  updateProgress(p ? p.currentTime : 0);
  renderPending();  // the stage line in the provisional banner

  if (after && after.seg !== undefined) {
    view.scroll.scrollTop = scrollY;
    // Correcting no longer advances by itself (the person could not stay on a word): N picks
    // up from here, not from the top, even though the playhead element is gone with the render.
    const w = wordAt(after.seg, Math.min(after.start, rec.segments[after.seg].words.length - 1));
    if (w) reviewFrom = w.start - 0.02;
  } else if (after) view.scroll.scrollTop = scrollY;

  // Counts in the sidebar follow the edits.
  const item = calls.find(c => c.name === recName);
  if (item && item.to_review !== rec.summary.to_review) {
    item.to_review = rec.summary.to_review;
    renderCalls();
  }

  // The translation was on (e.g. before a correction): fetch it again, the server cache
  // follows the transcript's file, so an edit re-derives the Italian (ToTTI, 0038).
  if (transOn && transName === recName) refreshTrans();
}

// ---------------------------------------------------------------- translation (ToTTI, 0038)

let transOn = false;     // the person asked for the Italian of the open call
let transName = null;    // which call it was asked for
let transTimer = null;
function transAction() {
  transOn = !transOn;
  transName = recName;
  updateTransBtn();
  if (!transOn) {
    clearTimeout(transTimer);
    setTransNote("");
    applyTrans(null);
    return;
  }
  refreshTrans();
}

function updateTransBtn() {
  if (!view.transBtn) return;
  const on = transOn && transName === recName;
  view.transBtn.classList.toggle("on", on);
  view.transBtn.setAttribute("aria-pressed", String(on));
}

function setTransNote(text) {
  if (view.transNote) view.transNote.textContent = text;
}

function engineLabel(engine) {
  // The static tag beside the IT button: the view always says who translated (0044's one
  // engine is ToTTI v1; anything else on screen would mean a stale server or cache).
  if (!engine) return "";
  if (engine.includes("totti-v1")) return "ToTTI v1";
  if (engine.includes("totti-a30")) return "ToTTI v0";
  if (engine.includes("madlad")) return "Maestro";
  if (engine === "opus-mt-en-it-ct2") return "Base";
  return engine;
}

async function refreshTrans() {
  const name = recName;
  try {
    pollTrans(name, await postJSON(`/api/recordings/${enc(name)}/translate`));
  } catch (e) {
    transOn = false;
    updateTransBtn();
    toast(`Traduzione non partita: ${e.message}`);
  }
}

async function pollTrans(name, st) {
  clearTimeout(transTimer);
  if (!transOn || recName !== name) return;
  if (!st) {
    try { st = await getJSON(`/api/recordings/${enc(name)}/translation`); }
    catch (e) { transTimer = setTimeout(() => pollTrans(name), 1500); return; }
  }
  if (st.state === "working") {
    setTransNote(st.total ? `Traduzione… ${Math.round((st.done / st.total) * 100)}%`
                          : "Traduzione…");
    transTimer = setTimeout(() => pollTrans(name), 800);
  } else if (st.state === "ready") {
    setTransNote(engineLabel(st.engine));
    if (!Object.keys(st.lines).length) {
      transOn = false;
      updateTransBtn();
      setTransNote("");
      toast("Niente da tradurre: la call non ha battute in inglese.");
      return;
    }
    applyTrans(st.lines);
  } else if (st.state === "error") {
    transOn = false;
    updateTransBtn();
    setTransNote("");
    toast(`Traduzione fallita: ${st.message}`);
  } else {
    // "missing": a correction landed meanwhile and the cache is stale; derive it again.
    refreshTrans();
  }
}

function applyTrans(lines) {
  // Split view, English | Italian side by side: each turn gets an Italian cell aligned with
  // its text row (empty for lines with nothing to translate, so the rule stays continuous).
  // A derived layer, visibly distinct from the verbatim and never mixed with it.
  if (view.body) view.body.classList.toggle("split", !!lines);
  for (const t of view.turns || []) {
    const old = t.el.querySelector(".trans");
    if (old) old.remove();
    if (lines) t.el.append(el("div", { class: "trans", lang: "it" }, lines[String(t.si)] || ""));
  }
}

// ---------------------------------------------------------------- voices

const VOICED = ["system", "mixed"];  // channels that can hold several voices
let showVoices = true;
try { showVoices = localStorage.getItem("voices") !== "0"; } catch (e) { /* default: shown */ }

function renameVoice(button, id) {
  // The label becomes a field; Enter gives the voice its name for the whole call.
  const before = rec.voices[id] || id;
  const input = el("input", { class: "inline voice-input", value: before, maxlength: "60",
                              "aria-label": "Nome della voce" });
  button.replaceWith(input);
  input.focus();
  input.select();
  let done = false;
  const finish = async save => {
    if (done) return;
    done = true;
    const value = input.value.trim();
    if (save && value !== before) {
      try { await render(await postJSON(`/api/recordings/${enc(recName)}/voices`,
                                        { id, name: value }), {}); return; }
      catch (e) { toast(`Nome non salvato: ${e.message}`); }
    }
    input.replaceWith(button);
  };
  input.addEventListener("keydown", e => {
    e.stopPropagation();
    if (e.key === "Enter") { e.preventDefault(); finish(true); }
    else if (e.key === "Escape") { e.preventDefault(); finish(false); }
  });
  input.addEventListener("blur", () => finish(true));
}

function voiceMenu(anchor, segment) {
  // "Whose line is this?": the voices of this channel, a new one, nothing else.
  const seg = rec.segments[segment];
  const ids = Object.keys(rec.voices).filter(v => v.startsWith(`${seg.channel}:`));
  const move = async speaker => {
    try {
      await render(await postJSON(`/api/recordings/${enc(recName)}/speaker`,
                                  { segment, speaker }), {});
    } catch (e) { toast(`Voce non cambiata: ${e.message}`); }
  };
  const holder = anchor.parentElement;
  popMenu(holder,
    ...ids.map(v => el("button", { role: "menuitemradio", "aria-checked": String(v === seg.speaker),
                                   class: v === seg.speaker ? "current" : "",
                                   onclick: () => { if (v !== seg.speaker) move(v); } },
      rec.voices[v])),
    el("hr"),
    el("button", { role: "menuitem", onclick: () => move("new") }, "Nuova voce"));
}

// ---------------------------------------------------------------- find in the call

let find = null;  // {q, index, hits: [[word, ...], ...]} while the find bar is open

function findHits(q) {
  const bySeg = new Map();
  for (const w of words) {
    if (!bySeg.has(w.seg)) bySeg.set(w.seg, []);
    bySeg.get(w.seg).push(w);
  }
  const lists = [...bySeg.values()];
  return findMatches(q, lists.map(list => list.map(w => w.el.textContent)))
    .map(m => lists[m.seg].slice(m.start, m.end));
}

function openFind(q, index = 0, focus = false) {
  if (!view.find) return;
  view.findInput.value = q;
  find = { q, index, hits: [] };
  applyFind(true);
  if (focus || !q) { view.findInput.focus(); view.findInput.select(); }
}

function closeFind() {
  find = null;
  document.querySelectorAll(".w.hit").forEach(e => e.classList.remove("hit", "hit-now"));
  if (view.findInput) view.findInput.value = "";
  if (view.findNav) view.findNav.hidden = true;
}

function applyFind(scroll) {
  if (!view.find) return;
  document.querySelectorAll(".w.hit").forEach(e => e.classList.remove("hit", "hit-now"));
  if (view.findInput.value !== find.q) view.findInput.value = find.q;
  view.findNav.hidden = !find.q.trim();
  find.hits = findHits(find.q);
  const n = find.hits.length;
  find.index = n ? ((find.index % n) + n) % n : 0;
  for (const hit of find.hits) for (const w of hit) w.el.classList.add("hit");
  view.findCount.textContent = !find.q.trim() ? "" : n ? `${find.index + 1} di ${n}` : "nessuna";
  if (!n) return;
  const current = find.hits[find.index];
  for (const w of current) w.el.classList.add("hit-now");
  if (scroll) current[0].el.scrollIntoView({ block: "center", behavior: "smooth" });
}

function stepFind(direction) {
  if (!find || !find.hits.length) return;
  find.index += direction;
  applyFind(true);
}

async function showRecording(name, data = null) {
  stopAll();
  find = null;  // a search belongs to the call it was made in
  recName = name;
  renderCalls();
  renderPending();
  data = data || await getJSON(`/api/recordings/${enc(name)}`);
  players = {};
  gains = {};
  lastChannel = null;
  $audio.replaceChildren();
  for (const [channel, lufs] of Object.entries(data.loudness || {})) {
    gains[channel] = playbackGain(lufs);
  }
  for (const [channel, url] of Object.entries(data.audio)) {
    const a = el("audio", { preload: "metadata", src: url });
    a.addEventListener("timeupdate", () => follow(channel));
    a.addEventListener("play", updatePlayButton);
    a.addEventListener("pause", updatePlayButton);
    a.playbackRate = speed;
    // Some browsers reset the rate when the source loads.
    a.addEventListener("loadedmetadata", () => { a.playbackRate = speed; });
    players[channel] = a;
    $audio.append(a);
  }
  lastChannel = Object.keys(players)[0] || null;
  view = {};
  await render(data);
}

function showWaiting(name) {
  // The call being transcribed, before Whisper is done: nothing to read yet, only how far it
  // is (renderPending updates the line). The text replaces this page as soon as it exists.
  stopAll();
  find = null;
  players = {};
  gains = {};
  $audio.replaceChildren();
  rec = null;
  recName = name;
  view = { waiting: el("p", {}) };
  renderCalls();
  $main.replaceChildren(el("div", { class: "placeholder" },
    el("h2", {}, recState.source ? recState.source.replace(/\.[^.]+$/, "") : name),
    view.waiting,
    el("p", {}, "Il testo compare qui appena Whisper ha finito; poi si aggiungono le parole " +
                "dubbie e le voci.")));
  renderPending();
}

function showEmpty() {
  stopAll();
  players = {};
  $audio.replaceChildren();
  rec = recName = null;
  view = {};
  renderCalls();
  renderPending();
  $main.replaceChildren(el("div", { class: "placeholder" },
    ...(calls.length
      ? [el("h2", {}, "Scegli una call"), el("p", {}, "Oppure registrane una nuova.")]
      : [el("h2", {}, "Nessuna call"),
         el("p", {}, "Premi Registra: l'audio e la trascrizione restano su questo Mac.")])));
}

// ---------------------------------------------------------------- recorder

let recState = { state: "idle" };
let recAt = 0;          // when recState arrived, to keep the clock running between polls
let recLang = "";
let recMode = null;     // what the recorder shows now, to rebuild only when it changes
let liveViews = [];     // the recorder in the sidebar and, if open, in the floating window
let pip = null;         // floating window (Document Picture-in-Picture)
let pollTimer = null;

async function recAction(action, body) {
  if (action === "start" && !modelsReady()) return;
  try {
    setRecState(await postJSON(`/api/record/${action}`, body));
    if (action === "start") openFloating(true);
  } catch (e) { toast(e.message); }
}

function liveElapsed() {
  const st = recState;
  if (st.state !== "recording") return 0;
  return st.elapsed + (st.paused ? 0 : (Date.now() - recAt) / 1000);
}

function meter() {
  const fill = el("i");
  return { root: el("div", { class: "meter" }, fill), fill };
}

// Dead-mic watch while recording: one sample per poll tick in a rolling 3-minute window. The
// badge comes on when the others' side has sound but the mic never reaches speaking level
// (today's dead channel only rustled), and goes off as soon as the mic hears real speech.
const MIC_WATCH = { windowS: 180, warmupS: 60, micFloor: -40, sysFloor: -35, minSys: 30, maxMic: 2 };
let micWatch = [];

function micLooksMute(st) {
  if (st.state !== "recording") { micWatch = []; return false; }
  const lv = st.levels || {};
  if (st.paused || lv.mic === undefined || lv.system === undefined) return false;
  // The poll ticks four times a second while recording: one sample a second is enough.
  if (!micWatch.length || Date.now() - micWatch[micWatch.length - 1].t >= 900) {
    micWatch.push({ t: Date.now(), mic: lv.mic >= MIC_WATCH.micFloor, sys: lv.system >= MIC_WATCH.sysFloor });
  }
  micWatch = micWatch.filter(s => s.t >= Date.now() - MIC_WATCH.windowS * 1000);
  if (micWatch.length < MIC_WATCH.warmupS) return false;
  return micWatch.filter(s => s.sys).length >= MIC_WATCH.minSys &&
         micWatch.filter(s => s.mic).length <= MIC_WATCH.maxMic;
}

function buildLive(floating) {
  // The recording controls; built with this document but also moved into the floating window.
  const dot = el("span", { class: "rec-dot" });
  const time = el("span", { class: "rec-time" });
  const mic = meter(), system = meter();
  const meters = el("div", { class: "meters", title: "Livelli: Io, Altri" },
    mic.root, system.root);
  const pause = el("button", { class: "icon-btn" });
  const micWarn = el("span", { class: "mic-warn", hidden: true, role: "alert",
                               title: "Il microfono non sta sentendo la tua voce: controlla " +
                                      "l'ingresso in Impostazioni di Sistema > Suono" },
    "mic muto?");
  const stop = iconButton("stop", "Ferma e trascrivi", () => recAction("stop"), "rec-stop");
  // Live subtitles (ToTTI, decision 0038): only where the translation model is on disk.
  const subs = floating || !subsAvailable ? null
    : el("button", { class: "icon-btn subs-btn", title: "Sottotitoli live",
                     "aria-label": "Sottotitoli live", "aria-pressed": "false",
                     onclick: () => subsAction(!subsOn) }, "CC");
  // In the native app the floating window is the app's own panel (phase 8, step 2).
  const pop = floating || window.parolaNative ? null
    : iconButton("popout", "Finestra mobile", () => openFloating(false));
  const root = el("div", { class: floating ? "rec-live pip-bar" : "rec-live" },
    dot, time, meters, micWarn, pause, subs, pop, stop);
  return {
    root,
    update(st, micMute = false) {
      micWarn.hidden = !micMute;
      if (subs) {
        subs.classList.toggle("on", subsOn);
        subs.setAttribute("aria-pressed", String(subsOn));
      }
      dot.classList.toggle("blink", !st.paused);
      dot.style.opacity = st.paused ? "0.35" : "";
      time.textContent = clock(liveElapsed());
      const lv = st.levels || {};
      for (const [m, db] of [[mic, lv.mic], [system, lv.system]]) {
        // -60 dBFS (empty) .. 0 dBFS (full)
        const v = db === undefined ? 0 : Math.max(0, Math.min(1, (db + 60) / 60));
        m.fill.style.width = `${Math.round(v * 100)}%`;
      }
      const label = st.paused ? "Riprendi" : "Pausa";
      pause.replaceChildren(icon(st.paused ? "play" : "pause", 14));
      pause.title = label;
      pause.setAttribute("aria-label", label);
      pause.onclick = () => recAction(st.paused ? "resume" : "pause");
    },
  };
}

// The settings gear: for now only where to listen. Playback is plain <audio> in the WebView,
// which follows the Mac's default output, so choosing an output switches the system default
// (what Control Center's switcher does) — honest in the menu title.
async function openSettings(anchor) {
  let rows;
  try {
    rows = await getJSON("/api/outputs");
  } catch (e) {
    toast("Dispositivi audio non disponibili");
    return;
  }
  const items = rows.map(r =>
    el("button", { role: "menuitemradio", "aria-checked": String(r.current),
                   class: r.current ? "checked" : "",
                   onclick: () => chooseOutput(r) },
      `${r.current ? "✓ " : ""}${r.name}`));
  popMenu(anchor, el("div", { class: "menu-title" }, "Uscita audio del Mac"), ...items);
}

async function chooseOutput(device) {
  try {
    await postJSON("/api/outputs", { uid: device.uid });
    toast(`Audio su ${device.name}`);
  } catch (e) {
    toast(`Uscita non cambiata: ${e.message}`);
  }
}

function renderRecorder() {
  const st = recState;
  // While a file uploads or a call is transcribed the controls stay, disabled: the progress is
  // on the call itself, at the top of the list (renderPending).
  const busy = !!uploading || st.state === "transcribing";
  const mode = st.state === "recording" ? "recording"
    : `idle:${st.state}:${st.name}:${busy}:${!!st.listening}:${subsAvailable}`;
  renderPending();
  if (mode !== recMode) {
    recMode = mode;
    liveViews = liveViews.filter(v => v.floating);
    if (mode === "recording") {
      const v = buildLive(false);
      liveViews.push(v);
      $recorder.replaceChildren(v.root);
    } else {
      closeFloating();
      const lang = el("select", { class: "lang", title: "Lingua della call",
                                  "aria-label": "Lingua della call",
                                  onchange: e => { recLang = e.target.value; } },
        ...LANGS.map(([v, label]) => el("option", { value: v, selected: v === recLang }, label)));
      const start = el("button", { class: "rec-start", disabled: busy,
                                   title: busy ? "Trascrizione in corso" : null,
                                   onclick: () => recAction("start", { lang: recLang || null }) },
        el("span", { class: "rec-dot" }), "Registra");
      const pick = iconButton("upload", "Trascrivi un file audio o video", () => $file.click(),
                              "rec-import");
      pick.disabled = busy || !!st.listening;
      // Subtitles without recording (decision 0042): the floater opens with the strip, the
      // audio stays in memory; Registra (or the floater's dot) turns it into a recording.
      const listening = !!st.listening;
      const ccLabel = listening ? "Spegni i sottotitoli" : "Sottotitoli live senza registrare";
      const cc = subsAvailable
        ? el("button", { class: `rec-cc subs-btn${listening ? " on" : ""}`, disabled: busy,
                         title: ccLabel, "aria-label": ccLabel, "aria-pressed": String(listening),
                         onclick: () => listenAction(!listening) }, "CC")
        : null;
      const note = busy ? null : st.state === "done" && st.name
        ? el("p", { class: "rec-note" }, "Trascritta: ",
             el("a", { href: `#/rec/${enc(st.name)}` }, "apri"))
        : st.state === "error" ? el("p", { class: "rec-note err" }, st.message) : null;
      $recorder.replaceChildren(el("div", { class: "rec-row" }, start, cc, pick, lang),
                                note || "");
    }
  }
  const micMute = micLooksMute(st);  // one sample per poll, shared by sidebar and floater
  if (mode === "recording") for (const v of liveViews) v.update(st, micMute);
}

const pendingView = {};
// What the transcription is doing, from the progress message ("parole dubbie Altri" too).
const STEPS = [["trascrizione", "Trascrizione"], ["parole dubbie", "Parole dubbie"],
               ["chi parla", "Chi parla"]];

function renderPending() {
  // The call being uploaded or transcribed, at the top of the list, until it is ready. Once
  // Whisper is done it opens the provisional text (decision 0031).
  let title, text, pct;
  const preview = !uploading && recState.state === "transcribing" && recState.preview;
  if (view.stage && rec && rec.provisional && recName === recState.name &&
      recState.state === "error") {
    view.stage.textContent = " La trascrizione si è fermata: questo testo non è stato salvato.";
  }
  if (view.waiting && recName === recState.name && recState.state === "error") {
    view.waiting.textContent = `La trascrizione si è fermata: ${recState.message}`;
  }
  if (uploading) {
    title = uploading.name;
    pct = uploading.pct;
    text = `Caricamento… ${pct}%`;
  } else if (recState.state === "transcribing") {
    title = recState.source ? recState.source.replace(/\.[^.]+$/, "") : "Registrazione";
    const [done, total] = recState.step || [0, 0];
    pct = total ? Math.min(99, Math.round((done / total) * 100)) : 0;
    const step = STEPS.find(([m]) => (recState.message || "").startsWith(m));
    text = recState.message === "conversione" ? "Conversione…"
                                              : `${step ? step[1] : "Trascrizione"}… ${pct}%`;
  } else {
    $pending.hidden = true;
    return;
  }
  if (!pendingView.title) {
    pendingView.title = el("span", { class: "title" });
    pendingView.text = el("span", { class: "sub" });
    pendingView.bar = el("i");
    pendingView.row = el("a", { class: "pending-call" }, el("span", { class: "pending-dot blink" }),
      el("div", { class: "pending-body" }, pendingView.title, pendingView.text,
        el("div", { class: "bar" }, pendingView.bar)));
    $pending.replaceChildren(el("div", { class: "group" }, "In corso"), pendingView.row);
  }
  $pending.hidden = false;
  pendingView.title.textContent = title;
  pendingView.text.textContent = text;
  pendingView.bar.style.width = `${pct}%`;
  // Clickable from the start: before Whisper is done it opens a page that says how far it is.
  const name = !uploading && recState.state === "transcribing" && recState.name;
  if (name) pendingView.row.href = `#/rec/${enc(name)}`;
  else pendingView.row.removeAttribute("href");
  pendingView.row.classList.toggle("active", !!name && recName === name);
  if (view.waiting && recName === recState.name) view.waiting.textContent = text;
  if (view.stage && rec && rec.provisional && recName === recState.name) {
    view.stage.textContent = ` ${text}`;
  }
}

async function refreshShown(final) {
  // The call on screen is the one being transcribed: its text moves to the next stage in place.
  const name = recName;
  let data;
  try { data = await getJSON(`/api/recordings/${enc(name)}`); } catch (e) { return; }
  if (recName !== name) return;  // another call was opened meanwhile
  if (!final) {
    // Same audio: only the text changes. While Whisper works it grows (decision 0033): the new
    // words fade in, and the view follows the end if that is where the person is reading.
    const box = view.scroll;
    const atEnd = box && box.scrollHeight - box.scrollTop - box.clientHeight < 80;
    const scrollY = box ? box.scrollTop : 0;
    const before = words.length;
    await render(data, {});
    if (view.scroll) view.scroll.scrollTop = scrollY;
    if (before && words.length > before) {
      words.slice(before).forEach((w, i) => {
        w.el.classList.add("fresh");
        w.el.style.animationDelay = `${Math.min(i * 12, 900)}ms`;
      });
      if (atEnd && Date.now() - scrolledByHand > FOLLOW_PAUSE) {
        view.scroll.scrollTo({ top: view.scroll.scrollHeight, behavior: "smooth" });
      }
    }
    return;
  }
  // The final transcript: editable, and the audio is now FLAC (decision 0024), so new players,
  // at the same point.
  const p = lastChannel && players[lastChannel];
  const at = p ? p.currentTime : 0, wasPlaying = !!(p && !p.paused);
  const scrollY = view.scroll ? view.scroll.scrollTop : 0;
  await showRecording(name, data);
  if (view.scroll) view.scroll.scrollTop = scrollY;
  if (at) {
    for (const r of Object.values(players)) {
      r.addEventListener("loadedmetadata", () => {
        r.currentTime = at;
        if (wasPlaying) r.play().catch(() => {});
      }, { once: true });
    }
  }
}

// Visible versioning (decision 0045): a discreet corner says which commit the engine runs,
// and when the repo on disk has moved past it, that a restart brings the new code.
const $build = el("div", { class: "build-tag", hidden: true });
document.body.append($build);

function renderBuild(build) {
  if (!build || !build.engine) { $build.hidden = true; return; }
  $build.hidden = false;
  $build.replaceChildren(`build ${build.engine}`,
    build.stale ? el("span", { class: "build-stale" },
                     " · codice aggiornato: riavvia PAROLA") : "");
}

function setRecState(st) {
  const prev = recState;
  recState = st;
  recAt = Date.now();
  renderBuild(st.build);
  // A new recording captures the system audio: a call playing in the app would be recorded
  // over the real one, so playback pauses by itself, however the recording was started.
  if (startedRecording(prev.state, st.state) && playing()) {
    stopAll();
    toast("Riproduzione in pausa: registrazione avviata");
  }
  followSubs(prev, st);
  renderRecorder();
  if (st.state === "transcribing" && st.preview && st.name &&
      (st.preview !== prev.preview || st.revision !== prev.revision)) {
    // Whisper is done (decision 0031): the text shows up, then the doubtful words.
    if (recName === st.name) {
      if (rec) refreshShown(false);
      else showRecording(st.name).catch(() => {});  // from the waiting page to the text
    }
    else if (!prev.preview && (!rec || st.name === imported)) {
      imported = null;  // opened now: not again when it is done
      location.hash = `#/rec/${enc(st.name)}`;
    }
  }
  if (prev.state === "transcribing" && st.state === "done") {
    const here = recName === st.name;
    const open = !here && (!rec || st.name === imported);  // a file you just imported opens by itself
    imported = null;
    refreshCalls().then(() => {
      if (here) refreshShown(true);
      else if (open && st.name) location.hash = `#/rec/${enc(st.name)}`;
    });
  }
}

// ---------------------------------------------------------------- importing a file

const $file = el("input", { type: "file", accept: "audio/*,video/*,.qta", hidden: true,
                            onchange: e => { if (e.target.files[0]) importFile(e.target.files[0]); } });
document.body.append($file);
let uploading = null;   // {name, pct} while a file is being sent
let imported = null;    // name of the call being imported from this window

// A file's language is asked before it is sent: Whisper transcribes the whole file in one
// language, and a wrong guess turns English into an Italian translation (decision 0030).
const IMPORT_LANGS = [["it", "Italiano"], ["en", "English"], ["", "Rileva"]];
let importLang = null;   // the last choice, offered first next time
try { importLang = localStorage.getItem("importLang"); } catch (e) { /* none yet */ }

function askLanguage(file) {
  return new Promise(resolve => {
    const box = el("div", { class: "modal", role: "dialog", "aria-modal": "true",
                            "aria-labelledby": "lang-title" });
    const done = lang => {
      box.remove();
      document.removeEventListener("keydown", onKey, true);
      resolve(lang);
    };
    const onKey = e => {
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); done(null); }
    };
    const buttons = IMPORT_LANGS.map(([value, label]) =>
      el("button", { class: value === importLang ? "primary" : null, onclick: () => done(value) },
         label));
    box.append(el("div", { class: "modal-card" },
      el("h2", { id: "lang-title" }, "In che lingua è?"),
      el("p", { class: "modal-file" }, file.name),
      el("p", {}, "La trascrizione segue una lingua per tutto il file. Se non sei sicuro, " +
                  "Rileva ascolta alcuni punti del file e sceglie tra italiano e inglese."),
      el("div", { class: "modal-actions" },
        el("button", { class: "cancel", onclick: () => done(null) }, "Annulla"), ...buttons)));
    box.addEventListener("click", e => { if (e.target === box) done(null); });
    document.addEventListener("keydown", onKey, true);
    document.body.append(box);
    (buttons.find(b => b.classList.contains("primary")) || buttons[0]).focus();
  });
}

async function importFile(file) {
  if (!modelsReady()) return;
  if (["recording", "transcribing"].includes(recState.state) || uploading) {
    toast("Aspetta che finisca la registrazione o la trascrizione in corso.");
    return;
  }
  const lang = await askLanguage(file);
  $file.value = "";
  if (lang === null) return;
  importLang = lang;
  try { localStorage.setItem("importLang", lang); } catch (e) { /* not remembered */ }
  sendFile(file, lang);
}

function sendFile(file, lang) {
  // The raw file goes in the request body (no form encoding); progress from XHR.
  uploading = { name: file.name.replace(/\.[^.]+$/, ""), pct: 0 };
  renderRecorder();
  const params = new URLSearchParams({ filename: file.name, modified: String(file.lastModified) });
  if (lang) params.set("lang", lang);
  const xhr = new XMLHttpRequest();
  xhr.open("POST", `/api/import?${params}`);
  xhr.upload.onprogress = e => {
    if (!e.lengthComputable || !uploading) return;
    uploading.pct = Math.round((e.loaded / e.total) * 100);
    renderPending();
  };
  xhr.onload = () => {
    uploading = null;
    let data = {};
    try { data = JSON.parse(xhr.responseText); } catch (e) { /* not JSON */ }
    recMode = null;
    if (xhr.status >= 300) {
      renderRecorder();
      toast(`File non importato: ${data.detail || xhr.status}`);
      return;
    }
    imported = data.name;
    setRecState(data);
    poll();
  };
  xhr.onerror = () => {
    uploading = null;
    recMode = null;
    renderRecorder();
    toast("File non importato: il server non risponde.");
  };
  xhr.send(file);
}

// Drop a file anywhere on the window.
let dragDepth = 0;
const $drop = el("div", { class: "drop", hidden: true },
  el("div", {}, el("strong", {}, "Rilascia per trascrivere"),
    el("span", {}, "Audio o video: resta su questo Mac.")));
document.body.append($drop);
const hasFiles = e => [...(e.dataTransfer?.types || [])].includes("Files");
window.addEventListener("dragenter", e => {
  if (!hasFiles(e)) return;
  e.preventDefault();
  dragDepth++;
  $drop.hidden = false;
});
window.addEventListener("dragover", e => { if (hasFiles(e)) e.preventDefault(); });
window.addEventListener("dragleave", e => {
  if (!hasFiles(e)) return;
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) $drop.hidden = true;
});
window.addEventListener("drop", e => {
  if (!hasFiles(e)) return;
  e.preventDefault();
  dragDepth = 0;
  $drop.hidden = true;
  const file = e.dataTransfer.files[0];
  if (file) importFile(file);
});

async function openFloating(auto) {
  if (window.parolaNative) return;
  if (pip) { pip.focus(); return; }
  if (!("documentPictureInPicture" in window)) {
    if (!auto) toast("La finestra mobile funziona in Chrome e in Edge.");
    return;
  }
  try {
    pip = await window.documentPictureInPicture.requestWindow({ width: 220, height: 52 });
  } catch (e) {
    if (!auto) toast("Finestra mobile non disponibile.");
    return;
  }
  const doc = pip.document;
  doc.head.append(el("link", { rel: "stylesheet", href: `${location.origin}/static/style.css` }));
  doc.title = "P.A.R.O.L.A.";
  doc.body.className = "pip";
  const v = buildLive(true);
  v.floating = true;
  liveViews.push(v);
  doc.body.append(v.root);
  v.update(recState);
  pip.addEventListener("pagehide", () => {
    pip = null;
    liveViews = liveViews.filter(x => x !== v);
  });
}

function closeFloating() {
  if (pip) pip.close();
}

// ---------------------------------------------------------------- live subtitles (ToTTI, 0038)

let subsOn = false;          // subtitles on, for this recording or while listening (server's)
let subsAvailable = false;   // the translation model is on disk (checked once at startup)
let subsLang = "it";         // the strip's language, held by the server like the toggle
let subsTimer = null;
let subsRevision = -1;
const $subs = el("div", { class: "subtitles", role: "status", hidden: true });
document.body.append($subs);

async function listenAction(on) {
  try {
    setRecState(await postJSON(`/api/listen/${on ? "start" : "stop"}`));
  } catch (e) { toast(`Sottotitoli non partiti: ${e.message}`); }
}

async function subsAction(on) {
  try {
    const st = await postJSON(`/api/subtitles/${on ? "start" : "stop"}`);
    subsOn = on && st.active;
  } catch (e) {
    subsOn = false;
    toast(`Sottotitoli non partiti: ${e.message}`);
  }
  subsRevision = -1;
  renderSubs(null);
  for (const v of liveViews) v.update(recState);
  if (subsOn) pollSubs();
}

async function setSubsLang(lang) {
  if (lang === subsLang) return;
  try {
    const st = await postJSON(`/api/subtitles/lang/${lang}`);
    subsLang = st.lang;
    subsRevision = -1;
    pollSubs();
  } catch (e) { toast(`Lingua non cambiata: ${e.message}`); }
}

function renderSubs(st) {
  const lines = (st && st.lines) || [];
  const tail = (st && st.tail) || "";
  const show = subsOn && (lines.length || (subsLang === "en" && tail));
  $subs.hidden = !show;
  if (!show) { $subs.replaceChildren(); return; }
  const chip = value => el("button",
    { class: "subs-lang" + (subsLang === value ? " active" : ""),
      title: value === "it" ? "Italiano tradotto" : "Inglese originale",
      onclick: () => setSubsLang(value) },
    value.toUpperCase());
  const head = el("div", { class: "subs-head" }, chip("it"), chip("en"));
  let rows;
  if (subsLang === "it") {
    // The last two committed lines, like broadcast subtitles: Italian to read, the English
    // underneath so every line traces back to what was actually said.
    rows = lines.slice(-2).map(l =>
      el("div", { class: "subs-line" },
        el("div", { class: "subs-it" }, l.it),
        el("div", { class: "subs-en" }, l.en)));
  } else {
    // The original: committed text plain, and the still-provisional tail dimmed right
    // after it (YouTube-caption style: text at decode latency, refined afterwards).
    const last = el("div", { class: "subs-it", lang: "en" },
      lines.length ? lines[lines.length - 1].en : "",
      tail ? el("span", { class: "subs-tail" }, (lines.length ? " " : "") + tail) : null);
    rows = [...lines.slice(-2, -1).map(l => el("div", { class: "subs-it", lang: "en" }, l.en)),
            last];
  }
  $subs.replaceChildren(head, ...rows);
}

async function pollSubs() {
  clearTimeout(subsTimer);
  if (!subsOn) { renderSubs(null); return; }
  try {
    const st = await getJSON("/api/subtitles");
    const langChanged = st.lang && st.lang !== subsLang;
    if (langChanged) subsLang = st.lang;  // the floater may have switched it
    if (st.revision !== subsRevision || langChanged) {
      subsRevision = st.revision;
      renderSubs(st);
    }
  } catch (e) { /* server busy: the next poll retries */ }
  // Measured 2026-10-19: the loop publishes every ~2-3 s; a 0.6 s poll adds ~0.3 s of
  // average delay instead of ~0.75, for a tiny localhost request.
  subsTimer = setTimeout(pollSubs, 600);
}

function followSubs(prev, st) {
  // The server says whether subtitles are on (the floater may have switched them): when the
  // recording or the listening ends, its loop stops by itself and the strip goes away.
  const was = subsOn;
  subsOn = !!st.subtitles;
  if (subsOn && !was) { subsRevision = -1; pollSubs(); }
  if (!subsOn && was) { clearTimeout(subsTimer); renderSubs(null); }
  if (prev.listening && !st.listening && st.listen_error) {
    toast(`Sottotitoli interrotti: ${st.listen_error}`);
  }
}

async function poll() {
  try { setRecState(await getJSON("/api/record/status")); } catch (e) { /* server restarting */ }
  const active = ["recording", "transcribing"].includes(recState.state);
  clearTimeout(pollTimer);
  // The floating window stays visible when this tab is in the background: tick from there.
  // While listening, often enough that the CC button follows the floater's ×.
  pollTimer = (pip || window).setTimeout(poll, active ? 250 : recState.listening ? 1000 : 3000);
}

// ---------------------------------------------------------------- first run: the models

// Asked once, before anything is downloaded (no network until the person says yes).
const MODELS = [
  ["Whisper large-v3-turbo", "La trascrizione · OpenAI, licenza MIT", "1,6 GB"],
  ["Parakeet v3", "La seconda opinione sulle parole dubbie · NVIDIA, CC BY 4.0", "2,5 GB"],
  ["Voci", "Chi parla quando · pyannote (MIT) e WeSpeaker (CC BY 4.0)", "33 MB"],
];
let models = { state: "ready" };
let setupLater = false;   // "Più tardi": the calls already here can still be read
let setupTimer = null;
const $setup = el("div", { class: "modal", hidden: true, role: "dialog", "aria-modal": "true",
                           "aria-labelledby": "setup-title" });
document.body.append($setup);

function modelsReady() {
  if (models.state === "ready") return true;
  setupLater = false;
  renderSetup();
  return false;
}

function renderSetup() {
  const st = models.state;
  $setup.hidden = st === "ready" || (setupLater && st !== "downloading");
  if ($setup.hidden) return;
  const card = el("div", { class: "modal-card" },
    el("h2", { id: "setup-title" }, "Prima di iniziare"),
    el("p", {}, "P.A.R.O.L.A. trascrive tutto su questo Mac. Per farlo le servono i modelli di " +
                `riconoscimento vocale: circa ${gigabytes(models.total)}, da scaricare una volta ` +
                "sola da Hugging Face e GitHub. Dopo, funziona senza rete."),
    el("ul", { class: "setup-models" }, ...MODELS.map(([name, what, size]) =>
      el("li", {}, el("span", {}, name, el("small", {}, what)), el("span", { class: "size" }, size)))));
  if (st === "downloading") {
    const pct = models.total ? Math.min(99, Math.floor((models.done / models.total) * 100)) : 0;
    const bar = el("i");
    bar.style.width = `${pct}%`;
    card.append(el("div", { class: "setup-bar" }, bar),
      el("div", { class: "setup-progress" },
         `${gigabytes(models.done)} di ${gigabytes(models.total)} · ${pct}%`),
      el("p", {}, "Puoi lasciare la finestra aperta e fare altro: il download continua."));
  } else {
    if (st === "error") {
      card.append(el("p", { class: "err" }, "Download non riuscito: controlla la connessione e " +
                                            "riprova. Quello già scaricato resta."),
                  el("p", { class: "setup-detail" }, models.message));
    }
    card.append(el("div", { class: "modal-actions" },
      el("button", { onclick: () => { setupLater = true; renderSetup(); } }, "Più tardi"),
      el("button", { class: "primary", onclick: startDownload },
         st === "error" ? "Riprova" : "Scarica i modelli")));
  }
  $setup.replaceChildren(card);
}

async function startDownload() {
  try { setModels(await postJSON("/api/models/download")); } catch (e) { toast(e.message); }
}

function setModels(st) {
  const was = models.state;
  models = st;
  renderSetup();
  if (was === "downloading" && st.state === "ready") toast("Modelli pronti: puoi registrare.");
  clearTimeout(setupTimer);
  if (st.state === "downloading") setupTimer = setTimeout(pollModels, 1000);
}

async function pollModels() {
  try { setModels(await getJSON("/api/models")); } catch (e) { setupTimer = setTimeout(pollModels, 3000); }
}

// ---------------------------------------------------------------- routing and keys

async function route() {
  const m = location.hash.match(/^#\/rec\/([^?]+)(?:\?(.*))?$/);
  try {
    if (m) {
      const name = decodeURIComponent(m[1]);
      const params = new URLSearchParams(m[2] || "");
      // Another hit in the call already open: no reload, the audio keeps its place.
      if (name === recState.name && recState.state === "transcribing" && !recState.preview) {
        showWaiting(name);
      } else if (name !== recName || !rec) await showRecording(name);
      else renderCalls();
      if (params.get("q")) openFind(params.get("q"), Number(params.get("hit") || 0));
    } else showEmpty();
  } catch (e) {
    $main.replaceChildren(el("div", { class: "placeholder" },
      el("h2", {}, "Non trovata"), el("p", {}, e.message)));
  }
}

document.addEventListener("keydown", e => {
  if ((e.metaKey || e.ctrlKey) && e.key === "k") { e.preventDefault(); $search.focus(); return; }
  if ((e.metaKey || e.ctrlKey) && e.key === "f" && rec) {
    e.preventDefault();
    openFind(find ? find.q : "", find ? find.index : 0, true);
    return;
  }
  if (["INPUT", "SELECT", "TEXTAREA"].includes(e.target.tagName)) return;
  if (!Object.keys(players).length) return;
  if (e.key === " ") { e.preventDefault(); togglePlay(); }
  else if (e.key === "Escape") { if (playing()) stopAll(); }
  else if (e.key === "n" || e.key === "N") { e.preventDefault(); jump(1); }
  else if (e.key === "p" || e.key === "P") { e.preventDefault(); jump(-1); }
  else if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
    const p = players[lastChannel];
    if (p) p.currentTime = Math.max(0, p.currentTime + (e.key === "ArrowRight" ? 5 : -5));
  } else if ((e.metaKey || e.ctrlKey) && e.key === "z") { e.preventDefault(); undo(); }
});

// The settings gear, beside the logo: always there, even while recording replaces the row.
const $sideTop = document.querySelector(".side-top");
$sideTop.append(iconButton("gear", "Impostazioni", () => openSettings($sideTop), "side-gear"));

window.addEventListener("hashchange", route);
refreshCalls().catch(() => {}).then(route);
poll();
pollModels();
// Live subtitles (ToTTI, 0038): the toggle only shows up when the translation model is there.
getJSON("/api/subtitles").then(st => {
  subsAvailable = st.available;
  subsLang = st.lang || "it";
  // A recording may already be on (page opened mid-call): rebuild the recorder with the toggle.
  if (subsAvailable) { recMode = null; renderRecorder(); }
}).catch(() => {});

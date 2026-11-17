// Live workout page: set timer -> rest timer, offline-safe set logging.
//
// Model
// - Timing is timestamp based (Date.now() at "Start set" / "End set"),
//   never a ticking counter, so a backgrounded or reloaded page still shows
//   the right times. Phase + timestamps live in localStorage per session.
// - Phase: ready -> set (running) -> rest (counting up toward the target) -> set ...
// - Each finished set gets a client UUID and goes into a localStorage queue
//   that is PUT to /api/workout/sets (idempotent upsert by uid). Offline,
//   the queue waits and retries on "online" / next load.
// - All user text is inserted with textContent.
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  let S = JSON.parse($("wo-data").textContent);
  const LIVE = S.in_progress;
  const KEY = "workout:" + S.id;
  const QKEY = "workout:queue";

  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} },
    del(k) { try { localStorage.removeItem(k); } catch (e) {} },
  };
  // st: {cur: session_exercise_id, phase, setStart, setEnd, lastUid, alerted}
  let st = Object.assign({ cur: null, phase: "ready" }, store.get(KEY, {}));
  const save = () => store.set(KEY, st);

  // --- time helpers
  const pad = (n) => String(n).padStart(2, "0");
  const localIso = (ms) => {
    const d = new Date(ms);
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T` +
           `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  };
  const parseLocal = (iso) => new Date(iso).getTime(); // no zone = local time
  const secsBetween = (a, b) => Math.round((parseLocal(b) - parseLocal(a)) / 1000);
  const clock = (s) => {
    s = Math.max(0, Math.floor(s));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    return (h ? h + ":" + pad(m) : m) + ":" + pad(s % 60);
  };
  const dur = (s) => (s == null ? "–" : s < 60 ? s + "s" : clock(s));
  const num = (x) => (x == null ? "" : String(+x));
  const uuid = () => (crypto.randomUUID ? crypto.randomUUID()
    : "x" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 12));
  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  };

  // --- derived numbers (mirror service._set_dict / _summary)
  function recompute(ex) {
    ex.sets.sort((a, b) => parseLocal(a.started_at) - parseLocal(b.started_at));
    let prev = null;
    for (const s of ex.sets) {
      s.duration_sec = secsBetween(s.started_at, s.ended_at);
      s.rest_before_sec = prev ? Math.max(0, secsBetween(prev, s.started_at)) : null;
      prev = s.ended_at;
    }
  }
  const exById = (id) => S.exercises.find((e) => e.id === id);
  const allSets = () => S.exercises.flatMap((e) => e.sets);
  const setLine = (ex, s) => {
    if (ex.track_reps && s.reps != null)
      return s.reps + (ex.track_weight && s.weight != null ? " × " + num(s.weight) + " " + s.unit : " reps");
    if (ex.track_weight && s.weight != null) return num(s.weight) + " " + s.unit;
    return "";
  };

  // --- offline queue
  const queue = () => store.get(QKEY, []);
  function putLocal(p) {
    const ex = exById(p.session_exercise_id);
    if (!ex) return;
    const i = ex.sets.findIndex((s) => s.uid === p.uid);
    const row = Object.assign(i >= 0 ? ex.sets[i] : {}, p);
    if (i < 0) ex.sets.push(row);
    recompute(ex);
  }
  function applyQueue() { queue().forEach(putLocal); }
  function enqueue(p) {
    store.set(QKEY, queue().filter((x) => x.uid !== p.uid).concat([p]));
    putLocal(p);
    flush();
  }
  let flushing = false;
  async function flush() {
    const q = queue();
    if (flushing || !q.length) { syncBadge(); return; }
    flushing = true;
    const sent = new Map(q.map((x) => [x.uid, JSON.stringify(x)]));
    let ok = false;
    try {
      const r = await fetch("/api/workout/sets", {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(q) });
      if (r.ok) {
        const j = await r.json();
        const done = new Set(j.saved.concat(j.rejected));
        // keep anything edited again while the request was in flight
        store.set(QKEY, queue().filter((x) => !(done.has(x.uid) && sent.get(x.uid) === JSON.stringify(x))));
        ok = true;
      } else if (r.status === 422) {
        console.warn("workout: dropping invalid queued sets", await r.text());
        store.set(QKEY, queue().filter((x) => !sent.has(x.uid)));
      }
    } catch (e) { /* offline: keep the queue */ }
    flushing = false;
    syncBadge();
    if (ok) refresh();
  }
  async function refresh() {
    try {
      const r = await fetch("/api/workout/sessions/" + S.id);
      if (!r.ok) return;
      S = await r.json();
      applyQueue();
      render();
    } catch (e) {}
  }
  function syncBadge() {
    const n = queue().filter((x) => S.exercises.some((e) => e.id === x.session_exercise_id)).length;
    const b = $("wo-sync");
    b.hidden = !n;
    b.textContent = n ? `· ${n} set${n > 1 ? "s" : ""} waiting to sync` : "";
  }
  addEventListener("online", flush);

  // --- rendering: one panel per exercise
  function render() {
    const root = $("wo-exercises");
    root.replaceChildren();
    if (!S.exercises.length) {
      root.append(el("p", "muted", LIVE ? "Add your first exercise below." : "No exercises were logged."));
    }
    for (const ex of S.exercises) {
      const sec = el("section", "panel wo-ex" + (LIVE && ex.id === st.cur ? " is-current" : ""));
      const head = el("div", "wo-ex-head");
      if (LIVE) {
        const pick = el("button", "wo-ex-name", ex.name);
        pick.type = "button";
        pick.setAttribute("aria-pressed", String(ex.id === st.cur));
        pick.addEventListener("click", () => select(ex.id));
        head.append(pick);
      } else {
        head.append(el("h2", "wo-ex-name", ex.name));
      }
      head.append(el("span", "muted wo-ex-rest", "rest " + dur(ex.target_rest_sec)));
      sec.append(head);
      if (ex.previous && ex.previous.length) {
        sec.append(el("p", "entry-meta wo-prev",
          "Last time: " + ex.previous.map((p) => setLine(ex, p) || "timed").join(", ")));
      }
      if (ex.sets.length) {
        const ol = el("ol", "entry-list wo-sets");
        ex.sets.forEach((s, i) => {
          const li = el("li");
          const b = el("button", "wo-set");
          b.type = "button";
          b.setAttribute("aria-label", `Edit set ${i + 1}`);
          b.append(el("span", "wo-set-no", String(i + 1)),
                   el("span", "wo-set-main", setLine(ex, s) || "timed"),
                   el("span", "wo-set-tut", dur(s.duration_sec)),
                   el("span", "wo-set-rest muted", s.rest_before_sec == null ? "" : "rest " + dur(s.rest_before_sec)));
          b.addEventListener("click", () => openEdit(ex, s, i + 1));
          li.append(b);
          ol.append(li);
        });
        sec.append(ol);
        const sets = ex.sets, rests = sets.map((s) => s.rest_before_sec).filter((x) => x != null);
        const vol = sets.reduce((a, s) => a + (s.reps || 0) * (s.weight || 0), 0);
        const tut = sets.reduce((a, s) => a + s.duration_sec, 0);
        sec.append(el("p", "entry-meta wo-ex-sum",
          [`${sets.length} set${sets.length > 1 ? "s" : ""}`,
           vol ? "volume " + num(Math.round(vol * 10) / 10) : null,
           "time under tension " + dur(tut),
           rests.length ? "avg rest " + dur(Math.round(rests.reduce((a, b) => a + b, 0) / rests.length)) : null,
          ].filter(Boolean).join(" · ")));
      }
      if (LIVE && !ex.sets.length) {
        const rm = el("button", "link-button", "Remove");
        rm.type = "button";
        rm.addEventListener("click", () => removeExercise(ex));
        sec.append(rm);
      }
      root.append(sec);
    }
    const n = allSets().length;
    $("wo-total").textContent = n + " set" + (n === 1 ? "" : "s");
    if (LIVE) renderDock();
    syncBadge();
  }

  // --- the dock: inputs + one big button
  const dock = $("wo-dock");
  const repsIn = $("wo-reps"), weightIn = $("wo-weight");

  function prefill(ex) {
    // next set = same as the last set this session, else last time's set N, else last time's last
    const n = ex.sets.length;
    const src = ex.sets[n - 1] || (ex.previous || [])[n] || (ex.previous || []).slice(-1)[0] || {};
    repsIn.value = src.reps ?? "";
    weightIn.value = src.weight ?? "";
  }

  function select(id) {
    if (st.phase === "set" && id !== st.cur) return; // finish the running set first
    st.cur = id;
    save();
    prefill(exById(id));
    render();
  }

  function renderDock() {
    const ex = exById(st.cur);
    dock.hidden = !ex;
    if (!ex) return;
    $("wo-dock-name").textContent = ex.name;
    $("wo-dock-setno").textContent = "Set " + (ex.sets.length + 1);
    $("wo-reps-wrap").hidden = !ex.track_reps;
    $("wo-weight-wrap").hidden = !ex.track_weight;
    $("wo-unit").textContent = "(" + ex.unit + ")";
    const main = $("wo-main");
    main.textContent = st.phase === "set" ? "End set" : "Start set " + (ex.sets.length + 1);
    main.classList.toggle("is-running", st.phase === "set");
    tick();
  }

  // a short beep + buzz when the rest target is reached (audio needs a prior tap)
  let audio = null;
  function unlockAudio() {
    try { audio = audio || new (window.AudioContext || window.webkitAudioContext)(); audio.resume(); } catch (e) {}
  }
  function alertRestDone() {
    try { navigator.vibrate && navigator.vibrate([200, 100, 200]); } catch (e) {}
    if (!audio) return;
    [0, 0.25].forEach((t) => {
      const o = audio.createOscillator(), g = audio.createGain();
      o.frequency.value = 880;
      g.gain.setValueAtTime(0.2, audio.currentTime + t);
      g.gain.exponentialRampToValueAtTime(0.001, audio.currentTime + t + 0.18);
      o.connect(g).connect(audio.destination);
      o.start(audio.currentTime + t);
      o.stop(audio.currentTime + t + 0.2);
    });
  }

  function onMain() {
    const ex = exById(st.cur);
    if (!ex) return;
    unlockAudio();
    const now = Date.now();
    if (st.phase !== "set") {
      st.phase = "set";
      st.setStart = now;
      st.alerted = false;
    } else {
      const p = {
        uid: uuid(), session_exercise_id: ex.id,
        started_at: localIso(st.setStart), ended_at: localIso(Math.max(now, st.setStart)),
        reps: ex.track_reps && repsIn.value !== "" ? Math.round(+repsIn.value) : null,
        weight: ex.track_weight && weightIn.value !== "" ? +weightIn.value : null,
        unit: ex.unit,
      };
      if ((now - st.setStart) / 1000 > 3600) {
        // a forgotten timer: don't log an hour-long set
        if (!confirm("This set ran over an hour. Discard it?")) return;
        st.phase = "ready";
        save();
        render();
        return;
      }
      st.phase = "rest";
      st.setEnd = now;
      st.lastUid = p.uid;
      st.alerted = false;
      enqueue(p);
    }
    save();
    render();
  }

  function tick() {
    if (!LIVE) return;
    const now = Date.now();
    $("wo-elapsed").textContent = clock((now - parseLocal(S.started_at)) / 1000);
    const ex = exById(st.cur);
    if (!ex) return;
    const box = $("wo-clock"), label = $("wo-clock-label"), time = $("wo-clock-time"), sub = $("wo-clock-sub");
    box.classList.remove("is-set", "is-rest", "is-over");
    if (st.phase === "set") {
      box.classList.add("is-set");
      label.textContent = "Set " + (ex.sets.length + 1);
      time.textContent = clock((now - st.setStart) / 1000);
      sub.textContent = "time under tension";
    } else if (st.phase === "rest") {
      const rest = (now - st.setEnd) / 1000, target = ex.target_rest_sec;
      box.classList.add("is-rest");
      label.textContent = "Rest";
      time.textContent = clock(rest);
      if (target && rest >= target) {
        box.classList.add("is-over");
        sub.textContent = "Target " + clock(target) + " reached: next set";
        if (!st.alerted) { st.alerted = true; save(); alertRestDone(); }
      } else {
        sub.textContent = target ? clock(target - rest) + " left of " + clock(target) : "";
      }
    } else {
      label.textContent = "Ready";
      time.textContent = "0:00";
      sub.textContent = "";
    }
  }

  // --- edit / delete a logged set
  const dlg = $("wo-edit");
  let editing = null;
  function openEdit(ex, s, no) {
    editing = { ex, s };
    $("wo-edit-title").textContent = `${ex.name} · set ${no}`;
    $("wo-edit-meta").textContent = `${s.started_at.slice(11, 16)} · ${dur(s.duration_sec)} under tension`;
    $("wo-edit-reps-wrap").hidden = !ex.track_reps;
    $("wo-edit-weight-wrap").hidden = !ex.track_weight;
    $("wo-edit-reps").value = s.reps ?? "";
    $("wo-edit-weight").value = s.weight ?? "";
    dlg.showModal();
  }
  $("wo-edit-cancel").addEventListener("click", () => dlg.close());
  $("wo-edit-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const { ex, s } = editing;
    const r = $("wo-edit-reps").value, w = $("wo-edit-weight").value;
    enqueue({
      uid: s.uid, session_exercise_id: ex.id, started_at: s.started_at, ended_at: s.ended_at,
      reps: ex.track_reps && r !== "" ? Math.round(+r) : null,
      weight: ex.track_weight && w !== "" ? +w : null, unit: s.unit || ex.unit,
      rpe: s.rpe ?? null, tempo: s.tempo ?? null, note: s.note ?? null,
    });
    dlg.close();
    render();
  });
  $("wo-edit-delete").addEventListener("click", async () => {
    const { ex, s } = editing;
    if (!confirm("Delete this set?")) return;
    try {
      const r = await fetch("/api/workout/sets/" + encodeURIComponent(s.uid), { method: "DELETE" });
      if (!r.ok && r.status !== 404) throw new Error(String(r.status));
    } catch (err) {
      if (!queue().some((x) => x.uid === s.uid)) { alert("Couldn't reach the server. Try again when online."); return; }
    }
    store.set(QKEY, queue().filter((x) => x.uid !== s.uid));
    ex.sets = ex.sets.filter((x) => x.uid !== s.uid);
    recompute(ex);
    dlg.close();
    render();
  });

  // --- add / remove exercises
  async function removeExercise(ex) {
    try {
      const r = await fetch("/api/workout/session-exercises/" + ex.id, { method: "DELETE" });
      if (!r.ok) return;
    } catch (e) { return; }
    S.exercises = S.exercises.filter((x) => x.id !== ex.id);
    if (st.cur === ex.id) { st.cur = S.exercises.length ? S.exercises[S.exercises.length - 1].id : null; save(); }
    render();
  }
  const addForm = $("wo-add");
  if (addForm) addForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = addForm.querySelector("button");
    btn.disabled = true;
    try {
      const r = await fetch(`/api/workout/sessions/${S.id}/exercises`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ exercise_id: +$("wo-add-select").value }) });
      if (r.ok) {
        const j = await r.json();
        S = j.session;
        applyQueue();
        if (st.phase !== "set") { st.cur = j.session_exercise_id; save(); prefill(exById(st.cur)); }
        render();
      } else {
        alert("Couldn't add the exercise.");
      }
    } catch (err) {
      alert("You're offline: adding an exercise needs a connection. Sets you log are kept until you're back online.");
    }
    btn.disabled = false;
  });

  // --- finish
  const finish = $("wo-finish");
  if (finish) finish.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (st.phase === "set" && !confirm("A set is still running and won't be saved. Finish anyway?")) return;
    await flush();
    const left = queue().filter((x) => S.exercises.some((ex) => ex.id === x.session_exercise_id)).length;
    if (left) {
      alert(`${left} set${left > 1 ? "s haven't" : " hasn't"} synced yet (offline). They're kept on this phone; finish once you're back online.`);
      return;
    }
    store.del(KEY);
    finish.submit();
  });

  // --- keep the screen on during a live workout
  let lock = null;
  async function wake() {
    if (!LIVE || !("wakeLock" in navigator) || document.visibilityState !== "visible") return;
    try { lock = await navigator.wakeLock.request("screen"); } catch (e) {}
  }
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") { wake(); tick(); } });

  // --- start
  applyQueue();
  if (LIVE) {
    if (!exById(st.cur)) st.cur = S.exercises.length ? S.exercises[S.exercises.length - 1].id : null;
    if (st.cur && st.phase !== "set") prefill(exById(st.cur));
    if (st.phase === "rest" && !st.setEnd) st.phase = "ready";
    save();
    $("wo-main").addEventListener("click", onMain);
    setInterval(tick, 250);
    wake();
  }
  render();
  flush();
})();

/* Signal TV Guide — guide grid + HLS player for iptv-org US channels. */
(() => {
  "use strict";

  const $ = (s) => document.querySelector(s);
  const els = {
    app: $("#app"), meta: $("#meta"), clock: $("#clock"), built: $("#built"),
    screen: $("#screen"), video: $("#video"), overlay: $("#overlay"), overlayText: $("#overlayText"), overlayActions: $("#overlayActions"),
    nowLogo: $("#nowLogo"), nowNum: $("#nowNum"), nowName: $("#nowName"), nowSrc: $("#nowSrc"), favBtn: $("#favBtn"),
    nowEyebrow: $("#nowEyebrow"), nowTitle: $("#nowTitle"), nowTime: $("#nowTime"), nowBar: $("#nowBar"), nowDesc: $("#nowDesc"),
    nextList: $("#nextList"), streams: $("#streams"),
    q: $("#q"), source: $("#source"), country: $("#country"), countryWrap: $("#countryWrap"), lang: $("#lang"), langWrap: $("#langWrap"), tGuide: $("#tGuide"), tFav: $("#tFav"), tGeo: $("#tGeo"), jumpNow: $("#jumpNow"), cats: $("#cats"),
    guide: $("#guide"), canvas: $("#canvas"), ticks: $("#ticks"), corner: $("#corner"), rows: $("#rows"), nowline: $("#nowline"), empty: $("#empty"),
    toast: $("#toast"), themeBtn: $("#themeBtn"), castBtn: $("#castBtn"),
  };

  // ------------------------------------------------------------ storage
  const store = {
    get(k, d) { try { const v = localStorage.getItem("signal." + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem("signal." + k, JSON.stringify(v)); } catch { /* storage unavailable */ } },
  };

  // ------------------------------------------------------------ formatting
  const fmtTime = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });
  const fmtDay = new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric" });
  const fmtClock = new Intl.DateTimeFormat(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" });
  const tm = (min) => fmtTime.format(new Date(min * 60000));
  const nowMin = () => Date.now() / 60000;
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const initials = (name) => name.replace(/[^\p{L}\p{N} ]/gu, "").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "TV";
  const sameDay = (a, b) => new Date(a * 60000).toDateString() === new Date(b * 60000).toDateString();
  const durText = (m) => (m >= 60 ? `${Math.floor(m / 60)} h${m % 60 ? " " + (m % 60) + " min" : ""}` : `${m} min`);

  function toast(msg) {
    els.toast.textContent = msg;
    els.toast.hidden = false;
    clearTimeout(toast.t);
    toast.t = setTimeout(() => { els.toast.hidden = true; }, 2200);
  }

  async function copy(text, note = "") {
    try { await navigator.clipboard.writeText(text); toast("Stream URL copied" + note); }
    catch { window.prompt?.("Copy the stream URL", text); }
  }

  // ------------------------------------------------------------ state
  const S = {
    channels: [], byId: new Map(), list: [], sources: new Map(), countries: new Map(), langNames: new Map(),
    T0: 0, T1: 0, PX: 5, CH_W: 232, ROW_H: 64,
    sel: null, selProg: null, playing: null,
    favs: new Set(store.get("favs", [])),
    f: Object.assign({ q: "", country: "", source: "", lang: "", cat: "", guide: false, fav: false, geo: false }, store.get("filters", {})),
    rendered: [], renderKey: "",
  };
  S.f.q = "";

  function readLayout() {
    const cs = getComputedStyle(document.documentElement);
    S.CH_W = parseFloat(cs.getPropertyValue("--chan-w")) || 232;
    S.ROW_H = parseFloat(cs.getPropertyValue("--row-h")) || 64;
    S.PX = window.innerWidth < 560 ? 4 : 5;
  }

  // ------------------------------------------------------------ data
  async function fetchData() {
    const [cRes, gRes] = await Promise.all([fetch("data/channels.json", { cache: "no-cache" }), fetch("data/guide.json", { cache: "no-cache" }).catch(() => null)]);
    if (!cRes.ok) throw new Error("data/channels.json is missing. Run scripts/build.py first.");
    const cData = await cRes.json();
    const gData = gRes && gRes.ok ? await gRes.json() : { guide: {}, from: 0, to: 0 };
    return { cData, gData };
  }

  function load({ cData, gData }) {
    S.built = cData.built;
    S.sources = new Map();
    S.byId = new Map();
    cData.sources.forEach((s) => S.sources.set(s.id, { cc: s.id.split("_")[0], ...s }));
    // Older data has no countries or languages; it's all one country then.
    S.countries = new Map((cData.countries || []).map((c) => [c.id, c]));
    S.langNames = new Map((cData.languages || []).map((l) => [l.id, l.name]));
    const multi = S.countries.size > 1;
    const now = nowMin();
    S.T0 = Math.floor((gData.from || now - 180) / 30) * 30;
    S.T1 = Math.ceil(Math.max(gData.to || 0, now + 24 * 60) / 30) * 30;
    S.T0 = Math.max(S.T0, Math.floor((now - 6 * 60) / 30) * 30);

    S.channels = cData.channels.map((c, i) => {
      const progs = (gData.guide[c.id] || []).filter((p) => p[1] > S.T0 && p[0] < S.T1);
      const geo = c.streams.every((s) => (s.tags || []).some((t) => /geo/i.test(t)));
      const cc = c.cc || [c.src.split("_")[0]];
      const langs = c.langs || [];
      const ch = {
        ...c, num: 101 + i, progs, geo, cc, langs,
        srcLabel: ((S.sources.get(c.src) || {}).label || c.src) + (multi ? ` ${c.src.split("_")[0].toUpperCase()}` : ""),
        hay: [c.name, c.network, ...c.cats, ...langs.map((l) => S.langNames.get(l) || l), ...progs.map((p) => p[2])].join(" \u0001 ").toLowerCase(),
      };
      S.byId.set(c.id, ch);
      return ch;
    });

    const withG = S.channels.filter((c) => c.progs.length).length;
    els.meta.textContent = `${S.channels.length.toLocaleString()} channels · ${withG.toLocaleString()} with listings` +
      (S.proxy ? " · stream proxy on" : "");
    const built = new Date(cData.built);
    els.built.textContent = `updated ${fmtDay.format(built)}, ${fmtTime.format(built)}`;
  }

  // ------------------------------------------------------------ filters
  // Country and language only show when the data has a choice to make. The
  // source and language lists follow the chosen country.
  function buildFilters() {
    if (!S.countries.has(S.f.country)) S.f.country = "";
    els.countryWrap.hidden = S.countries.size < 2;
    els.country.innerHTML = `<option value="">All countries</option>` +
      [...S.countries.values()].map((c) => `<option value="${esc(c.id)}">${esc(c.name)} (${c.count})</option>`).join("");
    els.country.value = S.f.country;

    const srcs = [...S.sources.values()].filter((s) => !S.f.country || s.cc === S.f.country);
    if (!srcs.some((s) => s.id === S.f.source)) S.f.source = "";
    const opt = (s) => `<option value="${esc(s.id)}">${esc(s.label)} (${s.count})</option>`;
    els.source.innerHTML = `<option value="">All sources</option>` + (S.countries.size > 1 && !S.f.country
      ? [...S.countries.values()].map((c) => `<optgroup label="${esc(c.name)}">${srcs.filter((s) => s.cc === c.id).map(opt).join("")}</optgroup>`).join("")
      : srcs.map(opt).join(""));
    els.source.value = S.f.source;

    const inCountry = S.channels.filter((c) => !S.f.country || c.cc.includes(S.f.country));
    const langs = new Map();
    let untagged = 0;
    inCountry.forEach((c) => { c.langs.forEach((l) => langs.set(l, (langs.get(l) || 0) + 1)); if (!c.langs.length) untagged++; });
    const langList = [...langs].sort((a, b) => b[1] - a[1]);
    if (S.f.lang && !(S.f.lang === "none" ? untagged : langs.has(S.f.lang))) S.f.lang = "";
    els.langWrap.hidden = langList.length < 2;
    els.lang.innerHTML = `<option value="">All languages</option>` +
      langList.map(([l, n]) => `<option value="${esc(l)}">${esc(S.langNames.get(l) || l)} (${n})</option>`).join("") +
      (untagged ? `<option value="none">Not tagged (${untagged})</option>` : "");
    els.lang.value = S.f.lang;

    const counts = new Map();
    S.channels.forEach((c) => c.cats.forEach((k) => counts.set(k, (counts.get(k) || 0) + 1)));
    const cats = [...counts].filter(([, n]) => n >= 4).sort((a, b) => b[1] - a[1]);
    if (S.f.cat && !counts.has(S.f.cat)) S.f.cat = "";
    els.cats.innerHTML = [`<button type="button" class="chip" data-cat="">All</button>`,
      ...cats.map(([k, n]) => `<button type="button" class="chip" data-cat="${esc(k)}">${esc(k)}<b>${n}</b></button>`)].join("");
    syncFilterUI();
  }

  function syncFilterUI() {
    els.tGuide.setAttribute("aria-pressed", S.f.guide);
    els.tFav.setAttribute("aria-pressed", S.f.fav);
    els.tGeo.setAttribute("aria-pressed", S.f.geo);
    els.cats.querySelectorAll(".chip").forEach((b) => b.setAttribute("aria-pressed", b.dataset.cat === S.f.cat));
  }

  function applyFilters(keepScroll) {
    const q = S.f.q.trim().toLowerCase();
    S.list = S.channels.filter((c) =>
      (!S.f.country || c.cc.includes(S.f.country)) &&
      (!S.f.source || c.src === S.f.source) &&
      (!S.f.lang || (S.f.lang === "none" ? !c.langs.length : c.langs.includes(S.f.lang))) &&
      (!S.f.cat || c.cats.includes(S.f.cat)) &&
      (!S.f.guide || c.progs.length) &&
      (!S.f.fav || S.favs.has(c.id)) &&
      (!S.f.geo || !c.geo) &&
      (!q || c.hay.includes(q)));
    if (S.f.fav) S.list.sort((a, b) => a.num - b.num);
    const { q: _q, ...persist } = S.f;
    store.set("filters", persist);
    syncFilterUI();
    layout();
    if (!keepScroll) els.guide.scrollTop = 0;
    render(true);

    els.empty.hidden = S.list.length > 0;
    if (!S.list.length) {
      els.empty.innerHTML = S.f.fav && !S.favs.size
        ? `<strong>No favorites yet</strong><span>Tune a channel and press the star, or press F.</span>`
        : `<strong>No channels match</strong><span>Try a different search or fewer filters.</span>`;
    }
  }

  // ------------------------------------------------------------ grid
  const xOf = (min) => (min - S.T0) * S.PX;

  function layout() {
    const w = (S.T1 - S.T0) * S.PX;
    els.canvas.style.width = `${S.CH_W + w}px`;
    els.ticks.style.width = `${w}px`;
    els.rows.style.height = `${S.list.length * S.ROW_H}px`;
    els.corner.textContent = `${S.list.length.toLocaleString()} ch`;

    let html = "";
    for (let t = S.T0; t < S.T1; t += 30) {
      const d = new Date(t * 60000);
      const midnight = d.getHours() === 0 && d.getMinutes() === 0;
      const label = midnight ? fmtDay.format(d) : tm(t);
      html += `<div class="tick${d.getMinutes() ? " half" : ""}${midnight ? " day" : ""}" style="left:${xOf(t)}px;width:${30 * S.PX}px">${esc(label)}</div>`;
    }
    els.ticks.innerHTML = html;
    placeNowLine();
  }

  function placeNowLine() {
    els.nowline.style.left = `${S.CH_W + xOf(nowMin()) - 1}px`;
  }

  const COPY_ICON = `<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/></svg>`;

  function chanCell(c) {
    const logo = c.logo
      ? `<img class="logo-img" src="${esc(c.logo)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" data-i="${esc(initials(c.name))}">`
      : esc(initials(c.name));
    const star = S.favs.has(c.id) ? `<span class="star" aria-label="Favorite">★</span> ` : "";
    return `<div class="chan"><button type="button" class="chan-tune" data-ch="${esc(c.id)}" title="${esc(c.name)}">
      <span class="chan-num">${c.num}</span><span class="logo">${logo}</span>
      <span class="chan-text"><span class="chan-name">${esc(c.name)}</span><span class="chan-sub">${star}${esc(c.srcLabel)}${c.cats[0] ? " · " + esc(c.cats[0]) : ""}</span></span>
    </button><button type="button" class="row-copy" data-copy="${esc(c.id)}" title="Copy stream URL" aria-label="Copy stream URL for ${esc(c.name)}">${COPY_ICON}</button></div>`;
  }

  function render(force) {
    const g = els.guide;
    const n = S.list.length;
    const top = Math.max(0, g.scrollTop - els.rows.offsetTop);
    const first = Math.max(0, Math.floor(top / S.ROW_H) - 4);
    const last = Math.min(n, Math.ceil((top + g.clientHeight) / S.ROW_H) + 4);
    const span = Math.max(g.clientWidth, 400);
    const bucket = Math.floor(g.scrollLeft / (span / 2));
    const key = `${first}:${last}:${bucket}`;
    if (!force && key === S.renderKey) { shiftTitles(); return; }
    S.renderKey = key;

    const tA = S.T0 + (bucket * (span / 2) - span) / S.PX;
    const tB = S.T0 + (bucket * (span / 2) + 2.5 * span) / S.PX;
    const now = nowMin();
    const totalW = (S.T1 - S.T0) * S.PX;
    let html = "";
    for (let i = first; i < last; i++) {
      const c = S.list[i];
      let cells = "";
      if (c.progs.length) {
        c.progs.forEach((p, j) => {
          if (p[1] < tA || p[0] > tB) return;
          const x = Math.max(0, xOf(p[0]));
          const w = Math.min(totalW, xOf(p[1])) - x - 3;
          if (w < 4) return;
          const cls = (p[1] <= now ? " past" : p[0] <= now ? " live" : "") +
            (S.selProg && S.selProg.ch === c.id && S.selProg.i === j ? " sel" : "") + (w < 70 ? " narrow" : "");
          const meta = `${tm(p[0])} – ${tm(p[1])}${p[5] ? " · " + p[5] : ""}${p[3] ? " · " + p[3] : ""}`;
          cells += `<div class="prog${cls}" style="left:${x}px;width:${w}px" data-ch="${esc(c.id)}" data-i="${j}" data-x="${x}" data-w="${w}" title="${esc(p[2] + " — " + meta)}">
            <div class="in"><span class="t">${esc(p[2] || "Untitled")}</span><span class="m">${esc(meta)}</span></div></div>`;
        });
      } else {
        const label = c.cats.length ? c.cats.join(", ") : "Live stream";
        cells = `<div class="prog none" style="left:0;width:${totalW - 3}px" data-ch="${esc(c.id)}" data-x="0" data-w="${totalW - 3}">
          <div class="in"><span class="t">No listings · ${esc(label)}</span><span class="m">${esc(c.network || c.srcLabel)} · ${c.streams.length} stream${c.streams.length > 1 ? "s" : ""}</span></div></div>`;
      }
      html += `<div class="row${S.sel === c.id ? " sel" : ""}" style="top:${i * S.ROW_H}px;width:${S.CH_W + totalW}px">${chanCell(c)}<div class="progs" style="width:${totalW}px">${cells}</div></div>`;
    }
    els.rows.innerHTML = html;
    S.rendered = [...els.rows.querySelectorAll(".prog")].map((el) => ({ el, x: +el.dataset.x, w: +el.dataset.w }));
    shiftTitles();
  }

  // Keep programme titles readable when a block starts left of the viewport.
  function shiftTitles() {
    const sl = els.guide.scrollLeft;
    for (const r of S.rendered) {
      const s = Math.max(0, Math.min(sl - r.x, r.w - 64));
      r.el.style.setProperty("--shift", `${s}px`);
    }
  }

  let raf = 0;
  els.guide.addEventListener("scroll", () => {
    if (raf) return;
    raf = requestAnimationFrame(() => { raf = 0; render(false); });
  }, { passive: true });

  function scrollToNow(smooth) {
    const left = xOf(nowMin() - 30);
    els.guide.scrollTo({ left: Math.max(0, left), behavior: smooth ? "smooth" : "auto" });
  }

  function scrollToChannel(id) {
    const i = S.list.findIndex((c) => c.id === id);
    if (i < 0) return;
    const g = els.guide, y = els.rows.offsetTop + i * S.ROW_H;
    const head = els.rows.offsetTop;
    if (y < g.scrollTop + head) g.scrollTop = y - head;
    else if (y + S.ROW_H > g.scrollTop + g.clientHeight) g.scrollTop = y + S.ROW_H - g.clientHeight;
  }

  // ------------------------------------------------------------ now panel
  function currentIndex(c, at = nowMin()) {
    return c.progs.findIndex((p) => p[0] <= at && p[1] > at);
  }

  function setLogo(el, c) {
    el.innerHTML = c.logo
      ? `<img class="logo-img" src="${esc(c.logo)}" alt="" referrerpolicy="no-referrer" data-i="${esc(initials(c.name))}">`
      : esc(initials(c.name));
  }

  function showPanel() {
    const c = S.byId.get(S.sel);
    if (!c) return;
    setLogo(els.nowLogo, c);
    els.nowNum.textContent = `CH ${c.num}`;
    els.nowName.textContent = c.name;
    els.nowName.title = c.name;
    els.nowSrc.textContent = [c.srcLabel, c.network && c.network !== c.srcLabel ? c.network : "", c.cats.join(", ")].filter(Boolean).join(" · ");
    els.favBtn.disabled = false;
    const fav = S.favs.has(c.id);
    els.favBtn.setAttribute("aria-pressed", fav);
    els.favBtn.setAttribute("aria-label", fav ? "Remove from favorites" : "Add to favorites");
    els.favBtn.title = (fav ? "Remove from favorites" : "Add to favorites") + " (F)";

    const now = nowMin();
    const cur = currentIndex(c, now);
    const pi = S.selProg && S.selProg.ch === c.id ? S.selProg.i : cur;
    const p = c.progs[pi];
    els.nowEyebrow.className = "eyebrow";
    if (p) {
      const live = p[0] <= now && p[1] > now;
      if (live) { els.nowEyebrow.textContent = "On now"; els.nowEyebrow.classList.add("live"); }
      else if (p[0] > now) els.nowEyebrow.textContent = sameDay(p[0], now) ? `Later today` : `Coming up · ${fmtDay.format(new Date(p[0] * 60000))}`;
      else els.nowEyebrow.textContent = "Earlier";
      els.nowTitle.textContent = p[2] || "Untitled";
      const bits = [`${tm(p[0])} – ${tm(p[1])}`, durText(Math.round(p[1] - p[0])), p[5], p[6]].filter(Boolean);
      els.nowTime.textContent = bits.join(" · ");
      els.nowDesc.textContent = [p[3], p[4]].filter(Boolean).join(" — ") || "No description in the listing.";
      els.nowBar.hidden = !live;
      if (live) els.nowBar.firstElementChild.style.width = `${((now - p[0]) / (p[1] - p[0])) * 100}%`;
    } else {
      els.nowEyebrow.textContent = "Live";
      els.nowTitle.textContent = c.progs.length ? "Nothing scheduled right now" : "No listings for this channel";
      els.nowTime.textContent = "";
      els.nowBar.hidden = true;
      els.nowDesc.textContent = c.progs.length
        ? "The listing feed has a gap here. The stream may still be on air."
        : "This channel isn't in any of the public listing feeds, so there's no schedule. The stream still plays.";
    }

    const start = cur >= 0 ? cur + 1 : c.progs.findIndex((q) => q[0] > now);
    const next = start >= 0 ? c.progs.slice(start, start + 3) : [];
    els.nextList.innerHTML = next.length
      ? `<div class="eyebrow">Up next</div>` + next.map((q) => `<div class="next-item"><time>${esc(tm(q[0]))}</time><span title="${esc(q[2])}">${esc(q[2])}</span></div>`).join("")
      : "";
    renderStreams(c);
  }

  function renderStreams(c) {
    const active = S.playing && S.playing.ch === c.id ? S.playing.idx : -1;
    els.streams.innerHTML = c.streams.map((s, i) => {
      const tags = [s.q, ...(s.tags || [])].filter(Boolean).join(" · ");
      const bad = S.bad.has(s.url) ? " bad" : "";
      return `<button type="button" class="stream-btn${bad}" data-s="${i}" aria-current="${i === active}" title="${esc(s.url)}">Stream ${i + 1}${tags ? " · " + esc(tags) : ""}</button>`;
    }).join("") + `<button type="button" class="stream-btn copy-btn" data-copy="${esc(c.id)}">Copy URL</button>`;
  }

  // ------------------------------------------------------------ player
  S.bad = new Set();
  let hls = null, token = 0, watchdog = 0;

  function overlay(state, text, actions = "") {
    els.screen.classList.remove("loading", "error", "playing", "casting");
    if (state) els.screen.classList.add(state);
    els.overlay.querySelector(".tally").lastChild.textContent =
      { loading: "Tuning", error: "No signal", casting: "Casting" }[state] || "Ready";
    els.overlayText.innerHTML = text;
    els.overlayActions.innerHTML = actions;
  }

  function stop() {
    token++;
    clearTimeout(watchdog);
    if (hls) { hls.destroy(); hls = null; }
    els.video.removeAttribute("src");
    els.video.load();
  }

  // Ways to reach a stream, tried in order. With the local proxy (scripts/serve.py)
  // a failed direct attempt is retried through it; Pluto always goes through it.
  function routes(s) {
    const mixed = location.protocol === "https:" && s.url.startsWith("http:");
    if (!S.proxy) return mixed ? [] : [{ url: s.url, via: "direct" }];
    const pluto = s.url.match(/plu-([0-9a-f]{24})|\/channel\/([0-9a-f]{24})\//);
    if (pluto) return [{ url: `pluto/${pluto[1] || pluto[2]}.m3u8`, via: "proxy" }];
    const q = new URLSearchParams({ u: s.url });
    if (s.ref) q.set("r", s.ref);
    if (s.ua) q.set("a", s.ua);
    const viaProxy = { url: `proxy?${q}`, via: "proxy" };
    return mixed || s.ref || s.ua ? [viaProxy] : [{ url: s.url, via: "direct" }, viaProxy];
  }

  // The URL to hand to VLC or mpv. Pluto links in the playlists are refused, and
  // some streams need a Referer or User-Agent, so with the proxy those copy as
  // a link through this server (which only works where this server is reachable).
  function shareUrl(s) {
    const needsProxy = /plu-[0-9a-f]{24}|\/channel\/[0-9a-f]{24}\//.test(s.url) || s.ref || s.ua;
    const p = S.proxy && needsProxy && routes(s).find((x) => x.via === "proxy");
    if (p) return { url: new URL(p.url, location.href).href, note: " (through Signal's proxy)" };
    return { url: s.url, note: "" };
  }

  function copyChannel(id) {
    const c = S.byId.get(id);
    if (!c) return;
    const idx = S.playing && S.playing.ch === c.id ? S.playing.idx : 0;
    const { url, note } = shareUrl(c.streams[idx]);
    copy(url, note);
  }

  function tune(id, idx = 0, opts = {}) {
    const c = S.byId.get(id);
    if (!c) return;
    select(id, { keepProg: opts.keepProg });
    store.set("last", id);
    stop();
    if (Cast.session()) return Cast.load(c, idx, opts);
    const my = token;

    // Find the next stream we can attempt.
    let i = idx;
    while (i < c.streams.length && !routes(c.streams[i]).length) { S.bad.add(c.streams[i].url); i++; }
    if (i >= c.streams.length) {
      return fail(c, S.proxy ? "every stream failed to load" : "its streams use plain HTTP, which this HTTPS page can't load");
    }

    const s = c.streams[i];
    const rs = routes(s);
    const attempt = Math.min(opts.attempt || 0, rs.length - 1);
    const route = rs[attempt];
    S.playing = { ch: id, idx: i };
    renderStreams(c);
    overlay("loading", `Tuning <b>CH ${c.num} · ${esc(c.name)}</b>${c.streams.length > 1 ? ` (stream ${i + 1} of ${c.streams.length})` : ""}${route.via === "proxy" ? " through the local proxy" : ""}…`);

    const next = (why) => {
      if (my !== token) return;
      if (attempt + 1 < rs.length) return tune(id, i, { ...opts, keepProg: true, attempt: attempt + 1 });
      S.bad.add(s.url);
      if (opts.single || i + 1 >= c.streams.length) return fail(c, why);
      tune(id, i + 1, { keepProg: true });
    };
    watchdog = setTimeout(() => next("the stream didn't start within 15 seconds"), 15000);

    const v = els.video;
    v.onplaying = () => { if (my === token) { clearTimeout(watchdog); els.screen.classList.remove("loading", "error"); els.screen.classList.add("playing"); } };
    v.onerror = () => next("the browser couldn't decode the stream");

    const isHls = !/\.(mp4|webm|mpd|ts)(\?|$)/i.test(s.url);
    if (isHls && window.Hls && Hls.isSupported()) {
      hls = new Hls({ enableWorker: true, manifestLoadingMaxRetry: 1, levelLoadingMaxRetry: 1, fragLoadingMaxRetry: 2, backBufferLength: 30 });
      hls.on(Hls.Events.MANIFEST_PARSED, () => { if (my === token) v.play().catch(() => playPrompt(c)); });
      hls.on(Hls.Events.ERROR, (_e, d) => {
        if (!d.fatal || my !== token) return;
        if (d.type === Hls.ErrorTypes.MEDIA_ERROR) { hls.recoverMediaError(); return; }
        const code = d.response && d.response.code;
        next(code === 403 ? "the server refused the request (often geo-blocking)"
          : code === 404 ? "the stream is offline (404)"
          : d.type === Hls.ErrorTypes.NETWORK_ERROR ? "the server blocked browser playback or is offline"
          : "the stream format isn't supported");
      });
      hls.loadSource(route.url);
      hls.attachMedia(v);
    } else {
      v.src = route.url;
      v.play().catch(() => playPrompt(c));
    }
  }

  // ------------------------------------------------------------ cast
  // Google Cast (Chromecast, Google TV, Nest Hub) through the Default Media
  // Receiver. The TV fetches the stream itself and needs HTTPS (the receiver
  // page blocks plain HTTP) and CORS (it plays HLS with MSE). A stream that
  // already has both goes to the TV directly; anything else, and Pluto, goes
  // through this server's proxy, which the TV must be able to resolve and reach.
  const Cast = {
    ctx: null,
    session() { return this.ctx && this.ctx.getCurrentSession(); },

    init() {
      if (!S.proxy || !/Chrome|CriOS|Edg/.test(navigator.userAgent)) return;
      window.__onGCastApiAvailable = (ok) => { if (ok) this.ready(); };
      const sc = document.createElement("script");
      sc.src = "https://www.gstatic.com/cv/js/sender/v1/cast_sender.js?loadCastFramework=1";
      sc.async = true;
      document.head.appendChild(sc);
    },

    ready() {
      const cf = cast.framework;
      this.ctx = cf.CastContext.getInstance();
      this.ctx.setOptions({ receiverApplicationId: chrome.cast.media.DEFAULT_MEDIA_RECEIVER_APP_ID, autoJoinPolicy: chrome.cast.AutoJoinPolicy.ORIGIN_SCOPED });
      // The button stays visible with no devices found: a page can't make
      // Chrome rescan, but opening its Cast picker does, so a click is the
      // "search again".
      const sync = () => {
        const st = this.ctx.getCastState();
        const none = st === cf.CastState.NO_DEVICES_AVAILABLE;
        const on = st === cf.CastState.CONNECTED;
        els.castBtn.hidden = false;
        els.castBtn.classList.toggle("searching", none);
        els.castBtn.setAttribute("aria-pressed", on);
        els.castBtn.title = on ? `Casting to ${this.device()}. Click to stop.`
          : none ? "No TVs found yet. Click to search again." : "Cast to a TV";
        els.castBtn.setAttribute("aria-label", els.castBtn.title);
      };
      this.ctx.addEventListener(cf.CastContextEventType.CAST_STATE_CHANGED, sync);
      this.ctx.addEventListener(cf.CastContextEventType.SESSION_STATE_CHANGED, (e) => {
        const SS = cf.SessionState;
        if ((e.sessionState === SS.SESSION_STARTED || e.sessionState === SS.SESSION_RESUMED) && S.sel) {
          if (e.sessionState === SS.SESSION_STARTED || !this.session().getMediaSession()) tune(S.sel, S.playing && S.playing.ch === S.sel ? S.playing.idx : 0, { keepProg: true });
          else this.showCasting(S.byId.get(S.sel));
        }
        if (e.sessionState === SS.SESSION_ENDED && S.sel) {
          toast("Stopped casting");
          playPrompt(S.byId.get(S.sel));
        }
      });
      sync();
    },

    device() {
      const s = this.session();
      return (s && s.getCastDevice().friendlyName) || "your TV";
    },

    toggle() {
      if (!this.ctx) return;
      if (this.session()) this.ctx.endCurrentSession(true);
      else this.ctx.requestSession().catch((code) => {
        if (code !== chrome.cast.ErrorCode.CANCEL && this.ctx.getCastState() === cast.framework.CastState.NO_DEVICES_AVAILABLE)
          toast("No TVs found. Check the TV is on and on the same network");
      });
    },

    proxyUrl(s) {
      const pluto = s.url.match(/plu-([0-9a-f]{24})|\/channel\/([0-9a-f]{24})\//);
      if (pluto) return new URL(`pluto/${pluto[1] || pluto[2]}.m3u8`, location.href).href;
      const q = new URLSearchParams({ u: s.url });
      if (s.ref) q.set("r", s.ref);
      if (s.ua) q.set("a", s.ua);
      return new URL(`proxy?${q}`, location.href).href;
    },

    // Pick the URL to hand the TV and the segment format to tell it (the
    // receiver assumes MPEG-TS unless told otherwise). Fetching the playlists
    // from this page is the CORS test: if the browser may read the master and
    // the variant playlist directly, the receiver may too.
    async source(s, opts = {}) {
      const get = async (u) => {
        const r = await fetch(u, { signal: AbortSignal.timeout(6000) });
        if (!r.ok) throw new Error(r.status);
        return { text: await r.text(), url: r.url };
      };
      const probe = async (u) => {
        let pl = await get(u);
        const variant = pl.text.split("\n").find((l) => l.trim() && !l.startsWith("#"));
        if (/#EXT-X-STREAM-INF/.test(pl.text) && variant) pl = await get(new URL(variant.trim(), pl.url).href);
        return /#EXT-X-MAP/.test(pl.text) ? "fmp4" : "ts";
      };
      const proxy = this.proxyUrl(s);
      const directOk = !opts.viaProxy && s.url.startsWith("https:") && !s.ref && !s.ua && !proxy.includes("/pluto/");
      if (directOk) {
        try { return { url: s.url, fmt: await probe(s.url), via: "direct" }; } catch { /* no CORS or offline: use the proxy */ }
      }
      try { return { url: proxy, fmt: await probe(proxy), via: "proxy" }; } catch { return { url: proxy, fmt: "ts", via: "proxy" }; }
    },

    async load(c, idx = 0, opts = {}) {
      const my = token;
      const s = c.streams[idx];
      if (!s) return this.fail(c, "none of its streams would load");
      S.playing = { ch: c.id, idx };
      renderStreams(c);
      this.showCasting(c, true);
      const { url, fmt, via } = await this.source(s, opts);
      if (my !== token || !this.session()) return;
      const info = new chrome.cast.media.MediaInfo(url, "application/x-mpegurl");
      info.streamType = chrome.cast.media.StreamType.LIVE;
      if (chrome.cast.media.HlsSegmentFormat) {
        info.hlsSegmentFormat = fmt === "fmp4" ? chrome.cast.media.HlsSegmentFormat.FMP4 : chrome.cast.media.HlsSegmentFormat.TS;
        if (fmt === "fmp4" && chrome.cast.media.HlsVideoSegmentFormat) info.hlsVideoSegmentFormat = chrome.cast.media.HlsVideoSegmentFormat.FMP4;
      }
      const md = new chrome.cast.media.GenericMediaMetadata();
      const p = c.progs[currentIndex(c)];
      md.title = p ? p[2] : c.name;
      md.subtitle = p ? `CH ${c.num} · ${c.name}` : `CH ${c.num}`;
      if (c.logo) md.images = [new chrome.cast.Image(c.logo)];
      info.metadata = md;
      const req = new chrome.cast.media.LoadRequest(info);
      req.autoplay = true;
      try {
        await this.session().loadMedia(req);
        if (my === token) this.showCasting(c);
      } catch (err) {
        if (my !== token) return;
        // A direct link the TV refused gets one more try through the proxy.
        if (via === "direct" && !opts.viaProxy) return this.load(c, idx, { ...opts, viaProxy: true });
        S.bad.add(s.url);
        if (!opts.single && idx + 1 < c.streams.length) return this.load(c, idx + 1, { ...opts, viaProxy: false });
        this.fail(c, `${this.device()} couldn't play it${err && err.code ? ` (${err.code})` : ""}`);
      }
    },

    showCasting(c, loading) {
      if (!c) return;
      overlay("casting", `${loading ? "Sending" : "Casting"} <b>CH ${c.num} · ${esc(c.name)}</b> to <b>${esc(this.device())}</b>${loading ? "…" : ""}`,
        `<button type="button" data-act="stopcast">Stop casting</button>`);
    },

    fail(c, why) {
      S.playing = null;
      renderStreams(c);
      overlay("error", `<b>${esc(c.name)}</b> won't cast: ${esc(why)}.`,
        `<button type="button" data-act="retry">Try again</button><button type="button" data-act="stopcast">Stop casting</button>`);
    },
  };

  function playPrompt(c) {
    overlay("", `Press play to watch <b>${esc(c.name)}</b>.`, `<button type="button" data-act="play">Play</button>`);
  }

  function fail(c, why) {
    clearTimeout(watchdog);
    if (hls) { hls.destroy(); hls = null; }
    S.playing = null;
    renderStreams(c);
    const s = c.streams[0];
    overlay("error",
      `<b>${esc(c.name)}</b> won't play here: ${esc(why)}. ` +
      (S.proxy ? "Many free streams only work in an app like VLC or mpv." : "Running <code>scripts/serve.py</code> plays more channels, or try VLC or mpv."),
      `<button type="button" data-act="retry">Try again</button><button type="button" data-copy="${esc(c.id)}">Copy stream URL</button>` +
      (s ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">Open stream</a>` : ""));
  }

  // ------------------------------------------------------------ selection
  function select(id, opts = {}) {
    S.sel = id;
    if (!opts.keepProg) S.selProg = null;
    render(true);
    showPanel();
  }

  function surf(delta) {
    if (!S.list.length) return;
    let i = S.list.findIndex((c) => c.id === S.sel);
    i = i < 0 ? 0 : (i + delta + S.list.length) % S.list.length;
    const id = S.list[i].id;
    scrollToChannel(id);
    tune(id);
  }

  function toggleFav() {
    if (!S.sel) return;
    const c = S.byId.get(S.sel);
    if (S.favs.has(S.sel)) { S.favs.delete(S.sel); toast(`Removed ${c.name} from favorites`); }
    else { S.favs.add(S.sel); toast(`Added ${c.name} to favorites`); }
    store.set("favs", [...S.favs]);
    if (S.f.fav) applyFilters(true); else render(true);
    showPanel();
  }

  // ------------------------------------------------------------ events
  els.rows.addEventListener("click", (e) => {
    const cp = e.target.closest(".row-copy");
    if (cp) { copyChannel(cp.dataset.copy); return; }
    const chan = e.target.closest(".chan-tune");
    if (chan) { tune(chan.dataset.ch); return; }
    const prog = e.target.closest(".prog");
    if (!prog) return;
    const id = prog.dataset.ch;
    const i = prog.dataset.i;
    S.selProg = i == null ? null : { ch: id, i: +i };
    if (S.playing && S.playing.ch === id) select(id, { keepProg: true });
    else tune(id, 0, { keepProg: true });
  });

  els.streams.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.copy) { copyChannel(b.dataset.copy); return; }
    S.bad.delete(S.byId.get(S.sel).streams[+b.dataset.s].url);
    tune(S.sel, +b.dataset.s, { keepProg: true, single: true });
  });

  els.overlayActions.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.copy) { copyChannel(b.dataset.copy); return; }
    if (b.dataset.act === "stopcast") { Cast.toggle(); return; }
    if (b.dataset.act === "retry") { S.byId.get(S.sel).streams.forEach((s) => S.bad.delete(s.url)); tune(S.sel, 0, { keepProg: true }); }
    if (b.dataset.act === "play") {
      if (els.video.currentSrc || hls) { els.video.play().catch(() => {}); els.screen.classList.add("playing"); }
      else tune(S.sel, 0, { keepProg: true });
    }
  });

  // Replace broken logos with initials.
  document.addEventListener("error", (e) => {
    const img = e.target;
    if (img.tagName === "IMG" && img.classList.contains("logo-img")) img.replaceWith(document.createTextNode(img.dataset.i || "TV"));
  }, true);

  let qTimer = 0;
  els.q.addEventListener("input", () => {
    clearTimeout(qTimer);
    qTimer = setTimeout(() => { S.f.q = els.q.value; applyFilters(); }, 120);
  });
  els.source.addEventListener("change", () => { S.f.source = els.source.value; applyFilters(); });
  els.country.addEventListener("change", () => { S.f.country = els.country.value; buildFilters(); applyFilters(); });
  els.lang.addEventListener("change", () => { S.f.lang = els.lang.value; applyFilters(); });
  els.cats.addEventListener("click", (e) => {
    const b = e.target.closest(".chip");
    if (!b) return;
    S.f.cat = b.dataset.cat;
    applyFilters();
  });
  els.tGuide.addEventListener("click", () => { S.f.guide = !S.f.guide; applyFilters(); });
  els.tFav.addEventListener("click", () => { S.f.fav = !S.f.fav; applyFilters(); });
  els.tGeo.addEventListener("click", () => { S.f.geo = !S.f.geo; applyFilters(); });
  els.jumpNow.addEventListener("click", () => { scrollToNow(true); if (S.sel) scrollToChannel(S.sel); });
  els.favBtn.addEventListener("click", toggleFav);
  els.castBtn.addEventListener("click", () => Cast.toggle());

  document.addEventListener("keydown", (e) => {
    const typing = /INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName);
    if (e.key === "Escape" && typing) { document.activeElement.blur(); return; }
    if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === "/") { e.preventDefault(); els.q.focus(); els.q.select(); }
    else if (e.key === "]") surf(1);
    else if (e.key === "[") surf(-1);
    else if (e.key === "f" || e.key === "F") toggleFav();
    else if (e.key === "n" || e.key === "N") scrollToNow(true);
  });

  els.guide.addEventListener("keydown", (e) => {
    if (e.target.closest(".chan button") && (e.key === "Enter" || e.key === " ")) return; // native button
    const map = { ArrowDown: 1, ArrowUp: -1 };
    if (map[e.key]) {
      e.preventDefault();
      let i = S.list.findIndex((c) => c.id === S.sel);
      i = Math.max(0, Math.min(S.list.length - 1, (i < 0 ? -1 : i) + map[e.key]));
      if (!S.list[i]) return;
      S.sel = S.list[i].id; S.selProg = null;
      scrollToChannel(S.sel); render(true); showPanel();
    } else if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      e.preventDefault();
      els.guide.scrollBy({ left: (e.key === "ArrowRight" ? 1 : -1) * 30 * S.PX, behavior: "smooth" });
    } else if (e.key === "Enter" && S.sel) {
      tune(S.sel);
    }
  });

  // Theme toggle: system by default, explicit choice persisted.
  const savedTheme = store.get("theme", "");
  if (savedTheme) document.documentElement.dataset.theme = savedTheme;
  els.themeBtn.addEventListener("click", () => {
    const cur = document.documentElement.dataset.theme ||
      (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
    const next = cur === "light" ? "dark" : "light";
    document.documentElement.dataset.theme = next;
    store.set("theme", next);
  });

  let lastW = window.innerWidth;
  window.addEventListener("resize", () => {
    if (Math.abs(window.innerWidth - lastW) < 2) { render(true); return; }
    lastW = window.innerWidth;
    const mid = S.T0 + (els.guide.scrollLeft) / S.PX;
    readLayout(); layout();
    els.guide.scrollLeft = (mid - S.T0) * S.PX;
    render(true);
  });

  function tick() {
    els.clock.textContent = fmtClock.format(new Date());
    placeNowLine();
  }

  // ------------------------------------------------------------ boot
  async function boot() {
    readLayout();
    tick();
    setInterval(tick, 15000);
    try {
      const h = await fetch("api/health", { cache: "no-store" });
      S.proxy = h.ok && (await h.json()).proxy === true;
    } catch { S.proxy = false; }
    Cast.init();
    try {
      load(await fetchData());
    } catch (err) {
      els.meta.textContent = "Couldn't load channel data";
      els.empty.hidden = false;
      els.empty.innerHTML = `<strong>No channel data</strong><span>${esc(err.message)}</span>`;
      return;
    }
    buildFilters();
    applyFilters();
    scrollToNow(false);

    const last = store.get("last", "");
    if (S.byId.has(last)) {
      S.sel = last;
      scrollToChannel(last);
      render(true);
      showPanel();
      playPrompt(S.byId.get(last));
    }
    // Refresh live/past styling and the progress bar every 30 seconds.
    setInterval(() => { render(true); if (S.sel) showPanel(); }, 30000);
    // Pick up new guide data (the server rebuilds it hourly) without a reload.
    setInterval(refresh, 10 * 60000);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  }

  // Swap in newer data in place: keeps filters, selection, the channel at the
  // top of the guide, the time in view and whatever is playing.
  async function refresh() {
    if (refresh.busy || Date.now() - (refresh.at || 0) < 60000) return;
    refresh.busy = true;
    try {
      const data = await fetchData();
      refresh.at = Date.now();
      if (data.cData.built === S.built) return;
      const g = els.guide;
      const topRow = Math.floor(Math.max(0, g.scrollTop) / S.ROW_H);
      const topId = S.list[topRow] && S.list[topRow].id;
      const leftMin = S.T0 + g.scrollLeft / S.PX;
      S.selProg = null;
      load(data);
      buildFilters();
      applyFilters(true);
      g.scrollLeft = (leftMin - S.T0) * S.PX;
      const i = S.list.findIndex((c) => c.id === topId);
      if (i >= 0) g.scrollTop = i * S.ROW_H;
      render(true);
      if (S.sel && S.byId.has(S.sel)) showPanel();
    } catch { /* keep showing what we have; try again next time */ }
    finally { refresh.busy = false; }
  }

  boot();
})();

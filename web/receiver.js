/* Signal TV Guide — Cast receiver (Chromecast, Google TV, Nest Hub).
   The sender casts a channel id, not a stream. The receiver looks the channel
   up in this server's data and plays it through the server (Pluto through
   /pluto, everything else through the proxy, which also takes care of CORS),
   moving on to the channel's next stream if one fails. Next and previous
   (the Hub's buttons, the phone's controls, "Hey Google, next channel")
   surf the sender's channel list, the title follows the programme airing,
   and a Hub shows the sender's favorites to tap. */
(() => {
  "use strict";

  const ctx = cast.framework.CastReceiverContext.getInstance();
  const player = ctx.getPlayerManager();
  const M = cast.framework.messages;
  const UI = cast.framework.ui;
  const S = { byId: new Map(), order: [], list: null, favs: [], guide: {} };

  const nowMin = () => Date.now() / 60000;
  const onNow = (c) => (S.guide[c.id] || []).find((p) => p[0] <= nowMin() && p[1] > nowMin());

  async function loadChannels() {
    const d = await (await fetch("data/channels.json", { cache: "no-cache" })).json();
    d.channels.forEach((c, i) => { c.num = 101 + i; S.byId.set(c.id, c); });
    S.order = d.channels.map((c) => c.id);
  }

  async function loadGuide() {
    try { S.guide = (await (await fetch("data/guide.json", { cache: "no-cache" })).json()).guide || {}; }
    catch { /* no listings: titles stay the channel names */ }
  }

  // Same routes as the page's proxy path: the receiver plays HLS with MSE, so
  // it needs CORS, which the proxy provides.
  function streamUrl(s) {
    const pluto = s.url.match(/plu-([0-9a-f]{24})|\/channel\/([0-9a-f]{24})\//);
    if (pluto) return new URL(`pluto/${pluto[1] || pluto[2]}.m3u8`, location.href).href;
    const q = new URLSearchParams({ u: s.url });
    if (s.ref) q.set("r", s.ref);
    if (s.ua) q.set("a", s.ua);
    return new URL(`proxy?${q}`, location.href).href;
  }

  function metadata(c) {
    const md = new M.GenericMediaMetadata();
    const p = onNow(c);
    md.title = p ? p[2] || c.name : c.name;
    md.subtitle = p ? `CH ${c.num} · ${c.name}` : `CH ${c.num}`;
    if (c.logo) md.images = [new M.Image(c.logo)];
    return md;
  }

  // Fill in a media item for channel `c`, stream `i`, in place.
  function fill(m, c, i = 0) {
    m.contentId = c.id;
    m.contentUrl = streamUrl(c.streams[i]);
    m.contentType = "application/x-mpegurl";
    m.streamType = M.StreamType.LIVE;
    m.customData = { stream: i };
    m.metadata = metadata(c);
    return m;
  }

  // A cast media item names a channel by contentId, or by entity when it
  // comes from the Hub's favorites. Anything else (a plain URL) plays as is.
  function resolve(m) {
    if (!m) return false;
    const c = S.byId.get(m.entity) || S.byId.get(m.contentId);
    if (!c) return Boolean(m.contentUrl || /^https?:/.test(m.contentId || ""));
    const i = (m.customData && m.customData.stream) || 0;
    fill(m, c, Math.min(i, c.streams.length - 1));
    return true;
  }

  const item = (c) => {
    const it = new M.QueueItem();
    it.media = fill(new M.MediaInformation(), c);
    return it;
  };

  const current = () => {
    const m = player.getMediaInformation();
    return m && S.byId.get(m.contentId);
  };

  function neighbour(c, d) {
    const list = S.list && S.list.includes(c.id) ? S.list : S.order;
    const i = list.indexOf(c.id);
    return S.byId.get(list[(i + d + list.length) % list.length]);
  }

  // An endless queue: next and previous ask for one more channel each way.
  class Surf extends cast.framework.QueueBase {
    initialize(req) {
      resolve(req.media);
      const q = new M.QueueData();
      const it = new M.QueueItem();
      it.media = req.media;
      q.items = [it];
      q.startIndex = 0;
      return q;
    }

    nextItems() {
      const c = current();
      return c ? [item(neighbour(c, 1))] : [];
    }

    prevItems() {
      const c = current();
      return c ? [item(neighbour(c, -1))] : [];
    }
  }

  // The sender sends its channel list (when short enough) and favorites.
  player.setMessageInterceptor(M.MessageType.LOAD, async (req) => {
    await ready;
    const extra = req.customData || {};
    if (Array.isArray(extra.list)) S.list = extra.list.filter((id) => S.byId.has(id));
    if (Array.isArray(extra.favs)) { S.favs = extra.favs.filter((id) => S.byId.has(id)); browse(); }
    if (!resolve(req.media)) {
      const err = new M.ErrorData(M.ErrorType.LOAD_FAILED);
      err.reason = M.ErrorReason.INVALID_PARAMS;
      return err;
    }
    return req;
  });

  // A stream that fails moves on to the channel's next one.
  player.addEventListener(cast.framework.events.EventType.ERROR, () => {
    const m = player.getMediaInformation();
    const c = m && S.byId.get(m.contentId);
    const i = ((m && m.customData && m.customData.stream) || 0) + 1;
    if (!c || i >= c.streams.length) return;
    const req = new M.LoadRequestData();
    req.media = fill(new M.MediaInformation(), c, i);
    req.autoplay = true;
    player.load(req);
  });

  player.addEventListener(cast.framework.events.EventType.PLAYER_LOAD_COMPLETE, () => {
    player.addSupportedMediaCommands(M.Command.QUEUE_NEXT | M.Command.QUEUE_PREV, true);
  });

  // Keep the title on the programme airing now.
  setInterval(() => {
    const m = player.getMediaInformation();
    const c = m && S.byId.get(m.contentId);
    if (!c) return;
    const md = metadata(c);
    if (m.metadata && m.metadata.title === md.title) return;
    m.metadata = md;
    player.setMediaInformation(m, true);
  }, 30000);

  // On a Hub: previous and next channel buttons, and favorites to tap.
  let controls = null;
  try {
    controls = UI.Controls.getInstance();
    controls.clearDefaultSlotAssignments();
    controls.assignButton(UI.ControlsSlot.SLOT_PRIMARY_1, UI.ControlsButton.QUEUE_PREV);
    controls.assignButton(UI.ControlsSlot.SLOT_PRIMARY_2, UI.ControlsButton.QUEUE_NEXT);
  } catch { controls = null; /* no touch screen */ }

  function browse() {
    if (!controls) return;
    const ids = S.favs.length ? S.favs : (S.list || S.order).filter((id) => (S.guide[id] || []).length).slice(0, 30);
    const bc = new UI.BrowseContent();
    bc.title = S.favs.length ? "Favorites" : "Channels";
    bc.targetAspectRatio = UI.BrowseImageAspectRatio.LANDSCAPE_16_TO_9;
    bc.items = ids.slice(0, 30).map((id) => {
      const c = S.byId.get(id), p = onNow(c);
      const b = new UI.BrowseItem(c.id, c.name, p ? p[2] : `CH ${c.num}`, c.logo ? new M.Image(c.logo) : undefined);
      b.imageType = UI.BrowseImageType.TV_SHOW;
      return b;
    });
    controls.setBrowseContent(bc);
  }

  const ready = loadChannels();
  loadGuide().then(browse);
  setInterval(loadGuide, 30 * 60000);

  const opts = new cast.framework.CastReceiverOptions();
  opts.queue = new Surf();
  opts.useShakaForHls = true;
  opts.supportedCommands = M.Command.ALL_BASIC_MEDIA | M.Command.QUEUE_NEXT | M.Command.QUEUE_PREV;
  opts.uiConfig = Object.assign(UI.UiConfig ? new UI.UiConfig() : {}, { touchScreenOptimizedApp: true });
  ctx.start(opts);
})();

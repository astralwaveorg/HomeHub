(() => {
  "use strict";
  const $ = (s, root = document) => root.querySelector(s);
  const main = $("#main"),
    dialog = $("#editor"),
    audio = $("#audio");
  const store = {
    get(key, fallback) {
      try {
        const v = localStorage.getItem("homehub." + key);
        return v === null ? fallback : JSON.parse(v);
      } catch {
        return fallback;
      }
    },
    set(key, value) {
      try {
        localStorage.setItem("homehub." + key, JSON.stringify(value));
      } catch {
        /* Storage is optional. */
      }
    },
  };
  const rememberedFavorites = store.get("favorites", []);
  const state = {
    data: null,
    error: "",
    route: location.pathname.replace(/\/$/, "") || "/",
    query: "",
    category: "",
    favoritesOnly: false,
    favorites: new Set(
      Array.isArray(rememberedFavorites) ? rememberedFavorites.map(String) : [],
    ),
    density: store.get("density", "grid"),
    adminTab: "links",
    addrFilter: new Set(
      (() => {
        const saved = store.get("addrFilter", []);
        return Array.isArray(saved)
          ? saved.filter((x) => ["公", "IP", "飞", "内"].includes(x))
          : [];
      })(),
    ),
    musicSource: "",
    musicGeneration: 0,
    musicAutoplaySource: "",
    musicAutoplayBlocked: false,
    musicUserPaused: false,
    toastTimer: null,
  };
  const symbols = {
    cloud: "☁",
    server: "▤",
    folder: "▱",
    film: "▷",
    video: "▷",
    music: "♫",
    photo: "▧",
    image: "▧",
    code: "⌘",
    terminal: "⌘",
    home: "⌂",
    globe: "◎",
    link: "↗",
    book: "▥",
    download: "↓",
    shield: "◇",
    network: "⌁",
    settings: "⚙",
    search: "⌕",
    database: "▰",
    nas: "▤",
    tv: "▣",
    docker: "▦",
    tools: "⚒",
    ai: "✧",
    heart: "♡",
    star: "✦",
    monitor: "▣",
    storage: "▤",
    calendar: "▦",
    mail: "✉",
    headphones: "♬",
    tag: "◇",
    activity: "⌁",
    seed: "❋",
    layers: "▱",
    clapperboard: "▨",
    "hard-drive": "▤",
    wifi: "◉",
    radio: "◌",
    smartphone: "▯",
    bell: "♧",
  };
  const tones = [
    ["#b4caed", "#7498d61c"],
    ["#d4b4ed", "#b68bda1c"],
    ["#b1d7ca", "#74bdaa1c"],
    ["#e6c99f", "#d5ae6e1c"],
    ["#dbb4c7", "#cc88a71c"],
    ["#bfc2ed", "#9299df1c"],
  ];
  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = String(text);
    return node;
  }
  function button(text, cls, fn) {
    const node = el("button", cls, text);
    node.type = "button";
    if (fn) node.addEventListener("click", fn);
    return node;
  }
  function internal(text, path, cls) {
    const node = el("a", cls, text);
    node.href = path;
    return node;
  }
  function toast(message) {
    const node = $("#toast");
    node.textContent = message;
    node.classList.add("visible");
    clearTimeout(state.toastTimer);
    state.toastTimer = setTimeout(() => node.classList.remove("visible"), 4200);
  }
  function safeUrl(value, music = false) {
    try {
      const raw = String(value || "").trim();
      if (!raw) return "";
      if (
        music &&
        !/^https:\/\//i.test(raw) &&
        !/^\/media\/music\.(mp3|ogg|wav|m4a|opus)(\?v=[0-9a-f]{16})?$/i.test(
          raw,
        )
      )
        return "";
      const u = new URL(raw, location.origin);
      if (!["http:", "https:"].includes(u.protocol)) return "";
      if (!music && !/^https?:\/\//i.test(raw)) return "";
      return u.href;
    } catch {
      return "";
    }
  }
  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (options.body !== undefined && !(options.body instanceof File)) {
      headers.set("Content-Type", "application/json");
      options.body = JSON.stringify(options.body);
    }
    if (options.method && options.method !== "GET" && state.data?.csrf)
      headers.set("X-CSRF-Token", state.data.csrf);
    const response = await fetch(path, {
      credentials: "same-origin",
      ...options,
      headers,
    });
    let data;
    try {
      data = await response.json();
    } catch {
      data = {};
    }
    if (!response.ok) {
      if (response.status === 401 && path !== "/api/login") {
        if (state.data) {
          state.data.authenticated = false;
          state.data.csrf = null;
          state.data.links = state.data.links.filter(
            (link) => link.visibility !== "private",
          );
        }
        if (state.route === "/admin") render();
      }
      throw new Error(
        data.error || `请求失败（${response.status}），请稍后重试。`,
      );
    }
    return data;
  }
  async function refresh(renderAfter = true) {
    const data = await api("/api/data");
    state.data = {
      ...data,
      settings: data.settings || {},
      categories: Array.isArray(data.categories) ? data.categories : [],
      links: Array.isArray(data.links) ? data.links : [],
    };
    state.error = "";
    if (data.authenticated && Array.isArray(data.favorites)) {
      state.favorites = new Set(data.favorites.map(String));
      store.set("favorites", [...state.favorites]);
    } else if (!data.authenticated && !state.data) {
      // First anonymous load keeps browser-local favorites.
    }
    const prefs = data.me?.prefs;
    if (data.authenticated && prefs) {
      if (Number.isFinite(prefs.volume)) {
        audio.volume = Math.min(1, Math.max(0, Number(prefs.volume)));
        $("#music-volume").value = String(audio.volume);
        store.set("volume", audio.volume);
      }
      if (typeof prefs.muted === "boolean") {
        audio.muted = prefs.muted;
        store.set("muted", prefs.muted);
      }
    }
    document.title =
      (data.settings?.title || "Homehub") +
      " · " +
      (data.settings?.tagline || "天空沒有極限");
    $("#footer-status").textContent = data.authenticated
      ? `已登录 · ${data.username || "管理员"}`
      : "属于你的每一个日常";
    const account = $("#account");
    account.textContent = data.authenticated
      ? `🧑🏻 ${data.username || "管理员"}`
      : "👤";
    account.title = data.authenticated
      ? `已登录 ${data.username || "管理员"} · 进入管理`
      : "登录 · 管理";
    account.setAttribute(
      "aria-label",
      data.authenticated ? "进入管理" : "登录或进入管理",
    );
    syncMusic();
    if (renderAfter) render();
  }
  const byRank = (a, b) =>
    Number(a.rank || 0) - Number(b.rank || 0) ||
    String(a.name || "").localeCompare(String(b.name || ""), "zh-CN");
  function addrTypeOf(link) {
    if (link.launch_mode === "fnos") return "飞";
    try {
      const host = new URL(safeUrl(link.url)).hostname;
      if (/^10\.0\./.test(host) || /^192\.168\./.test(host)) return "内";
      if (/^(?:\d{1,3}\.){3}\d{1,3}$/.test(host) || host.includes(":"))
        return "IP";
    } catch {
      return "";
    }
    return "公";
  }
  const ADDR_ORDER = { 公: 0, IP: 1, 飞: 2, 内: 3, "": 4 };
  function categories() {
    return [...(state.data?.categories || [])].sort(byRank);
  }
  function categoryName(id) {
    return (
      state.data.categories.find((c) => String(c.id) === String(id))?.name ||
      "未分类"
    );
  }
  function links() {
    return [...(state.data?.links || [])].sort(byRank);
  }
  function navigate(path) {
    state.route = path;
    state.query = "";
    state.category = "";
    state.favoritesOnly = false;
    clearOrbitTimers();
    if (location.pathname !== path) history.pushState({}, "", path);
    if (dialog.open) dialog.close();
    render();
    window.scrollTo({ top: 0, behavior: "instant" });
    main.focus({ preventScroll: true });
  }
  document.addEventListener("click", (event) => {
    const anchor = event.target.closest("a");
    if (
      !anchor ||
      event.defaultPrevented ||
      event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey ||
      anchor.target ||
      anchor.hasAttribute("download")
    )
      return;
    const url = new URL(anchor.href, location.origin);
    if (
      url.origin === location.origin &&
      ["/", "/nav", "/admin", "/monitor"].includes(url.pathname) &&
      !url.hash
    ) {
      event.preventDefault();
      navigate(url.pathname);
    }
  });
  window.addEventListener("popstate", () => {
    state.route = location.pathname.replace(/\/$/, "") || "/";
    state.query = "";
    state.category = "";
    state.favoritesOnly = false;
    clearOrbitTimers();
    if (dialog.open) dialog.close();
    render();
  });
  document.addEventListener("keydown", (event) => {
    const editing =
      /INPUT|TEXTAREA|SELECT/.test(event.target.tagName) ||
      event.target.isContentEditable;
    if (
      ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") ||
      (event.key === "/" &&
        !editing &&
        !event.metaKey &&
        !event.ctrlKey &&
        !event.altKey)
    ) {
      if (dialog.open) return;
      event.preventDefault();
      if (state.route !== "/nav" && state.route !== "/") navigate("/nav");
      const input = $("#service-search");
      input?.focus();
      input?.select();
    }
    if (
      event.key === "Escape" &&
      !dialog.open &&
      $("#service-search") === document.activeElement
    ) {
      state.query = "";
      $("#service-search").value = "";
      if (state.route === "/nav") updateResults();
      else updatePortalResults();
    }
  });
  audio.muted = store.get("muted", false) === true;
  const volume = Number(store.get("volume", 0.5));
  audio.volume = Number.isFinite(volume)
    ? Math.min(1, Math.max(0, volume))
    : 0.5;
  $("#music-volume").value = String(audio.volume);
  let prefsTimer = null;
  function savePrefs(part) {
    if (!state.data?.authenticated) return;
    clearTimeout(prefsTimer);
    prefsTimer = setTimeout(() => {
      api("/api/prefs", {
        method: "PUT",
        body: {
          volume: Number(audio.volume.toFixed(2)),
          muted: audio.muted,
          ...part,
        },
      }).catch(() => {});
    }, 400);
  }
  $("#music-volume").addEventListener("input", (event) => {
    audio.volume = Number(event.target.value);
    store.set("volume", audio.volume);
    savePrefs();
  });
  function visibility() {
    document.body.classList.toggle("tab-hidden", document.hidden);
  }
  document.addEventListener("visibilitychange", visibility);
  visibility();
  const quiet = store.get("quiet", false) === true;
  document.body.classList.toggle("quiet", quiet);
  $("#quiet").setAttribute("aria-pressed", String(quiet));
  $(".quiet-icon").textContent = quiet ? "☀️" : "🌙";
  $("#quiet").addEventListener("click", () => {
    const enabled = document.body.classList.toggle("quiet");
    store.set("quiet", enabled);
    $("#quiet").setAttribute("aria-pressed", String(enabled));
    $(".quiet-icon").textContent = enabled ? "☀️" : "🌙";
    toast(enabled ? "静谧模式已开启 · 背景动画已暂停" : "静谧模式已关闭");
  });
  function emblem(link, index = 0) {
    const node = el("span", "emblem");
    node.setAttribute("aria-hidden", "true");
    const fallback = () => {
      const icon = String(link.icon || "").trim();
      node.classList.remove("emblem-official");
      node.textContent =
        symbols[icon.toLowerCase()] ||
        (icon && !/^(https?:|data:|<|\/)/i.test(icon)
          ? [...icon].slice(0, 2).join("")
          : [...String(link.name || "H")][0]);
      const tone = tones[index % tones.length];
      node.style.setProperty("--emblem-color", tone[0]);
      node.style.setProperty("--emblem-bg", tone[1]);
    };
    const official = localIconSource(
      window.HOMEHUB_ICONS?.[link.catalog_key]?.src,
    );
    const custom = localIconSource(link.iconUrl);
    const display = (src, recovery = "") => {
      const img = el("img", "emblem-image");
      img.alt = "";
      img.loading = "lazy";
      img.decoding = "async";
      img.addEventListener(
        "error",
        () => {
          if (recovery && recovery !== src) display(recovery);
          else fallback();
        },
        { once: true },
      );
      img.src = src;
      node.replaceChildren(img);
      node.classList.add("emblem-official");
    };
    if (custom || official) display(custom || official, custom ? official : "");
    else fallback();
    return node;
  }
  function serviceCard(link, index) {
    const card = el("article", "service-card");
    const a = el("a", "service-link");
    const url = safeUrl(link.url);
    const fnosLaunch = link.launch_mode === "fnos" && url;
    const desktopUrl = fnosLaunch ? new URL(url).origin + "/" : "";
    a.href = desktopUrl || url || "#";
    if (fnosLaunch) {
      a.addEventListener("click", (event) => {
        if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey)
          return;
        event.preventDefault();
        const content = el("div");
        content.append(
          el(
            "p",
            "hint",
            `「${link.name}」需要飞牛应用启动票据。请打开飞牛桌面，再从应用中心启动该应用；直接打开网关地址可能返回 invalid token。`,
          ),
        );
        const actions = el("div", "form-actions");
        const desktop = el("a", "button primary", "打开飞牛桌面 ↗");
        desktop.href = desktopUrl;
        desktop.target = "_blank";
        desktop.rel = "noopener noreferrer";
        actions.append(
          button("返回导航", "button", () => dialog.close()),
          desktop,
        );
        content.append(actions);
        openDialog("从飞牛启动 · " + link.name, content);
      });
    }
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.setAttribute("aria-label", `${link.name}（在新标签页打开）`);
    if (!url)
      a.addEventListener("click", (event) => {
        event.preventDefault();
        toast("此服务的地址不可用，请联系管理员。");
      });
    a.append(emblem(link, index));
    const text = el("div", "service-text");
    const nameRow = el("div", "service-name");
    nameRow.append(el("span", "service-name-text", link.name));
    const addrType = addrTypeOf(link);
    if (addrType) {
      const badge = el("span", `addr-badge addr-${addrType}`, addrType);
      badge.title =
        addrType === "飞"
          ? "需从飞牛桌面启动（应用票据保护）"
          : addrType === "内"
            ? "内网地址（10.0.x / 192.168.x）"
            : addrType === "IP"
              ? "IP 直连地址"
              : "公网域名";
      nameRow.append(badge);
    }
    text.append(
      nameRow,
      el(
        "div",
        "service-description",
        link.description || categoryName(link.category_id),
      ),
    );
    a.append(text);
    const favorite = button(
      state.favorites.has(String(link.id)) ? "★" : "☆",
      "favorite-button",
      async () => {
        const id = String(link.id);
        const saved = !state.favorites.has(id);
        if (state.data.authenticated) {
          try {
            await api("/api/favorites", {
              method: "PUT",
              body: { linkId: Number(link.id), on: saved },
            });
          } catch {
            toast("收藏保存失败，请重试。");
            return;
          }
        }
        if (saved) state.favorites.add(id);
        else state.favorites.delete(id);
        store.set("favorites", [...state.favorites]);
        favorite.textContent = saved ? "★" : "☆";
        favorite.classList.toggle("saved", saved);
        favorite.setAttribute("aria-pressed", String(saved));
        favorite.setAttribute(
          "aria-label",
          `${saved ? "取消收藏" : "收藏"} ${link.name}`,
        );
        if (state.favoritesOnly) updateResults();
      },
    );
    const saved = state.favorites.has(String(link.id));
    favorite.classList.toggle("saved", saved);
    favorite.setAttribute(
      "aria-label",
      `${saved ? "取消收藏" : "收藏"} ${link.name}`,
    );
    favorite.setAttribute("aria-pressed", String(saved));
    card.append(a, favorite);
    const badges = [];
    if (link.visibility === "private" && state.data.authenticated)
      badges.push("◇ 私密");
    if (badges.length)
      card.append(el("span", "private-badge", badges.join(" · ")));
    return card;
  }
  function searchBox(nav = false) {
    const box = el("div", "search-box" + (nav ? " nav-search" : ""));
    const icon = el("span", "search-icon", "⌕");
    icon.setAttribute("aria-hidden", "true");
    const input = el("input");
    input.type = "search";
    input.id = "service-search";
    input.placeholder = nav
      ? "寻找一颗星 · 名称、描述或别名…"
      : "搜索你的数字世界…";
    input.setAttribute("aria-label", "搜索服务名称、描述或别名");
    input.autocomplete = "off";
    input.value = state.query;
    input.addEventListener("input", () => {
      state.query = input.value;
      if (nav) updateResults();
      else updatePortalResults();
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !nav) {
        const query = state.query;
        navigate("/nav");
        state.query = query;
        $("#service-search").value = query;
        updateResults();
        $("#service-search").focus();
      }
    });
    const shortcut = el(
      "kbd",
      "",
      navigator.platform?.includes("Mac") ? "⌘ K" : "/",
    );
    shortcut.setAttribute("aria-hidden", "true");
    box.append(icon, input, shortcut);
    return box;
  }
  function empty(title, description) {
    const node = el("div", "empty-state");
    node.append(
      el("div", "subtle", "✧"),
      el("h3", "", title),
      el("p", "", description),
    );
    return node;
  }
  function matches(link) {
    const query = state.query.trim().toLocaleLowerCase();
    return (
      (!state.category || String(link.category_id) === state.category) &&
      (!state.favoritesOnly || state.favorites.has(String(link.id))) &&
      (!query ||
        [
          link.name,
          link.description,
          link.aliases,
          categoryName(link.category_id),
        ]
          .join(" ")
          .toLocaleLowerCase()
          .includes(query))
    );
  }
  function portal() {
    main.className = "portal-page";
    const settings = state.data.settings;
    const hero = el("section", "hero");
    hero.append(
      el("div", "eyebrow", "A SPACE OF YOUR OWN"),
      el("h1", "", settings.tagline || "天空沒有極限"),
      el("p", "", settings.subtitle || "把热爱与日常，连接成自己的宇宙。"),
      searchBox(),
    );
    const meta = el("div", "hero-meta");
    const connected = el("span");
    connected.append(
      el("i", "status-dot"),
      document.createTextNode(`已点亮 ${state.data.links.length} 处风景`),
    );
    meta.append(connected, el("span", "", "在这里，让灵感自由生长"));
    hero.append(meta);
    const heading = el("div", "section-heading");
    const h2 = el("h2", "", "常驻星光");
    h2.append(el("small", "", "PINNED & LOVED"));
    heading.append(h2, internal("展开星图 ↗", "/nav", "text-link"));
    const grid = el("div", "service-grid portal-grid");
    grid.id = "portal-results";
    const bottom = el("div", "portal-bottom");
    const inspiration = el("div", "inspiration", "心有旷野，向光而行。");
    inspiration.append(el("span", "", "灵感致意 · G.E.M.《天空没有极限》"));
    bottom.append(inspiration, internal("走进星图 →", "/nav", "pill-button"));
    main.append(hero, heading, grid, bottom);
    updatePortalResults();
  }
  function updatePortalResults() {
    const grid = $("#portal-results");
    if (!grid) return;
    grid.replaceChildren();
    let items = links().filter(matches);
    if (!state.query.trim()) {
      items.sort(
        (a, b) =>
          Number(state.favorites.has(String(b.id))) -
            Number(state.favorites.has(String(a.id))) ||
          Number(Boolean(b.pinned)) - Number(Boolean(a.pinned)) ||
          byRank(a, b),
      );
      items = items.slice(0, 6);
    } else items = items.slice(0, 12);
    if (!items.length)
      grid.append(
        empty(
          state.query ? "没有找到匹配的服务" : "你的数字宇宙，等待点亮",
          state.query
            ? "试试其他名称，或前往星图。"
            : state.data.authenticated
              ? "前往管理，添加你的第一个服务。"
              : "管理员添加服务后，会出现在这里。",
        ),
      );
    else items.forEach((link, index) => grid.append(serviceCard(link, index)));
  }
  function navigation() {
    main.className = "nav-page";
    const heading = el("div", "page-heading");
    const titles = el("div");
    const subtitle = el("p");
    subtitle.append(
      el("span", "poetic", "每一次抵达都有名字 · "),
      document.createTextNode(
        `${state.data.catalog?.total ?? state.data.links.length} 个入口 · 各应用仍需各自登录`,
      ),
    );
    titles.append(
      el("div", "eyebrow", "ATLAS OF YOUR SKY"),
      el("h1", "", "星图"),
      subtitle,
    );
    if (state.data.catalog?.hidden > 0 && !state.data.authenticated) {
      const hint = el(
        "p",
        "hint",
        `尚有 ${state.data.catalog.hidden} 颗暗星，`,
      );
      hint.append(internal("登录后查看", "/admin", "text-link"));
      titles.append(hint);
    }
    heading.append(titles);
    main.append(heading, searchBox(true));
    const bar = el("div", "filter-bar");
    const chips = el("div", "categories");
    chips.setAttribute("aria-label", "服务分类筛选");
    function chip(name, value) {
      const node = button(
        name,
        "chip" +
          (state.category === value && !state.favoritesOnly ? " active" : ""),
        () => {
          state.category = value;
          state.favoritesOnly = false;
          renderFilterChips();
          updateResults();
        },
      );
      node.dataset.category = value;
      node.setAttribute(
        "aria-pressed",
        String(state.category === value && !state.favoritesOnly),
      );
      chips.append(node);
    }
    chip("全部", "");
    categories().forEach((cat) => chip(cat.name, String(cat.id)));
    const favorite = button("☆ 收藏", "chip", () => {
      state.favoritesOnly = !state.favoritesOnly;
      state.category = "";
      renderFilterChips();
      updateResults();
    });
    favorite.id = "favorites-filter";
    favorite.setAttribute("aria-pressed", "false");
    chips.append(favorite);
    const addrChips = el("div", "categories addr-filter");
    addrChips.setAttribute("aria-label", "地址类型筛选（可多选）");
    function addrChip(value) {
      const active = state.addrFilter.has(value);
      const node = button(value, "chip" + (active ? " active" : ""), () => {
        if (state.addrFilter.has(value)) state.addrFilter.delete(value);
        else state.addrFilter.add(value);
        store.set("addrFilter", [...state.addrFilter]);
        renderFilterChips();
        updateResults();
      });
      node.dataset.addr = value;
      node.setAttribute("aria-pressed", String(active));
      addrChips.append(node);
    }
    ["公", "IP", "飞", "内"].forEach(addrChip);
    const views = el("div", "view-controls");
    ["grid", "list"].forEach((mode) => {
      const node = button(
        mode === "grid" ? "▦" : "☰",
        "icon-button" + (state.density === mode ? " active" : ""),
        () => {
          state.density = mode;
          store.set("density", mode);
          for (const b of views.children) {
            b.classList.toggle("active", b === node);
            b.setAttribute("aria-pressed", String(b === node));
          }
          updateResults();
        },
      );
      node.setAttribute(
        "aria-label",
        mode === "grid" ? "紧凑网格视图" : "列表视图",
      );
      node.setAttribute("aria-pressed", String(state.density === mode));
      views.append(node);
    });
    bar.append(chips, addrChips, views);
    const meta = el("div", "results-meta");
    const count = el("span");
    count.id = "result-count";
    count.setAttribute("aria-live", "polite");
    const clear = button("清除筛选", "clear-button", () => {
      state.query = "";
      state.category = "";
      state.favoritesOnly = false;
      state.addrFilter.clear();
      store.set("addrFilter", []);
      $("#service-search").value = "";
      renderFilterChips();
      updateResults();
      $("#service-search").focus();
    });
    clear.id = "clear-filters";
    meta.append(count, clear);
    const grid = el("div", "service-grid navigation-grid");
    grid.id = "nav-results";
    main.append(bar, meta, grid);
    updateResults();
  }
  function renderFilterChips() {
    document.querySelectorAll("[data-category]").forEach((node) => {
      const active =
        !state.favoritesOnly && node.dataset.category === state.category;
      node.classList.toggle("active", active);
      node.setAttribute("aria-pressed", String(active));
    });
    const favorite = $("#favorites-filter");
    favorite.classList.toggle("active", state.favoritesOnly);
    favorite.setAttribute("aria-pressed", String(state.favoritesOnly));
    document.querySelectorAll("[data-addr]").forEach((node) => {
      const active = state.addrFilter.has(node.dataset.addr);
      node.classList.toggle("active", active);
      node.setAttribute("aria-pressed", String(active));
    });
  }
  function updateResults() {
    const grid = $("#nav-results");
    if (!grid) return;
    grid.classList.toggle("list", state.density === "list");
    grid.replaceChildren();
    let items = links().filter(matches);
    if (state.addrFilter.size)
      items = items.filter((link) => state.addrFilter.has(addrTypeOf(link)));
    items.sort(
      (a, b) =>
        ADDR_ORDER[addrTypeOf(a)] - ADDR_ORDER[addrTypeOf(b)] || byRank(a, b),
    );
    $("#result-count").textContent =
      `看见 ${items.length} / ${state.data.links.length} 处风景${state.data.authenticated && state.data.links.some((link) => link.visibility === "private") ? " · 含私密入口" : ""}`;
    $("#clear-filters").hidden =
      !state.query &&
      !state.category &&
      !state.favoritesOnly &&
      !state.addrFilter.size;
    if (!items.length)
      grid.append(
        empty(
          state.favoritesOnly ? "尚无收藏的星光" : "这片夜空暂时没有匹配",
          state.favoritesOnly
            ? "点击卡片右上角的星标，把它留在你的夜空。"
            : "尝试清除筛选，或换一个关键词。",
        ),
      );
    else items.forEach((link, index) => grid.append(serviceCard(link, index)));
  }
  function render() {
    main.replaceChildren();
    document.querySelectorAll("[data-route]").forEach((a) => {
      const active = a.dataset.route === state.route;
      a.classList.toggle("active", active);
      if (active) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    });
    if (state.error && !state.data) {
      const node = el("div", "error-state");
      node.append(
        el("h2", "", "暂时无法连接"),
        el("p", "", state.error),
        button("重新连接", "button primary", load),
      );
      main.append(node);
      return;
    }
    if (!state.data) {
      main.append(el("div", "loading-state", "正在连接你的数字世界…"));
      return;
    }
    if (state.route === "/admin") admin();
    else if (state.route === "/nav") navigation();
    else if (state.route === "/monitor") orbit();
    else portal();
  }
  async function load() {
    state.error = "";
    render();
    try {
      await refresh();
    } catch (error) {
      state.error = error.message;
      render();
    }
  }
  function syncMusic() {
    const settings = state.data.settings;
    const source = safeUrl(settings.musicUrl, true);
    $("#music-title").textContent = settings.musicTitle || "自定义背景音乐";
    const wasPlaying = !audio.paused;
    if (source !== state.musicSource) {
      state.musicGeneration++;
      state.musicAutoplayBlocked = false;
      audio.pause();
      if (source) audio.src = source;
      else audio.removeAttribute("src");
      audio.load();
      state.musicSource = source;
      if (source && wasPlaying && !state.musicUserPaused) {
        state.musicAutoplaySource = source;
        playMusic(true);
      }
    }
    if (
      source &&
      settings.musicAutoplay === true &&
      !state.musicUserPaused &&
      state.musicAutoplaySource !== source
    ) {
      state.musicAutoplaySource = source;
      playMusic(true);
    }
    updateMusic();
  }
  async function playMusic(automatic = false) {
    const generation = state.musicGeneration;
    state.musicAutoplayBlocked = false;
    try {
      await audio.play();
      if (generation === state.musicGeneration)
        state.musicAutoplayBlocked = false;
    } catch (error) {
      if (generation !== state.musicGeneration || error.name === "AbortError")
        return;
      if (error.name === "NotAllowedError") {
        state.musicAutoplayBlocked = true;
        if (!automatic) toast("浏览器限制了播放，请再次点击播放器开启音乐。");
      } else if (!automatic)
        toast("播放失败，请检查音源格式或地址，然后重试。");
    }
    updateMusic();
  }
  function updateMusic() {
    const source = Boolean(state.musicSource);
    $("#music-play").textContent = audio.paused ? "▷" : "Ⅱ";
    const blocked = source && audio.paused && state.musicAutoplayBlocked;
    $("#music-play").classList.toggle("autoplay-blocked", blocked);
    $("#music-play").title = blocked
      ? "浏览器限制自动播放，点击开启背景音乐"
      : audio.paused
        ? "播放背景音乐"
        : "暂停背景音乐";
    $("#music-play").setAttribute(
      "aria-label",
      audio.paused ? "播放音乐" : "暂停音乐",
    );
    $("#music-mute").textContent = audio.muted ? "♬̸" : "♫";
    $("#music-mute").setAttribute("aria-pressed", String(audio.muted));
    $("#music-mute").setAttribute(
      "aria-label",
      audio.muted ? "取消静音" : "静音",
    );
    $("#music-status").textContent = !source
      ? "尚未配置 · 管理员可上传或设置音乐"
      : audio.error
        ? "音源无法播放 · 请检查配置"
        : !audio.paused
          ? audio.muted
            ? "正在播放 · 已静音"
            : "正在播放"
          : blocked
            ? "点击 ▷ 开启音乐 · 浏览器限制自动播放"
            : "已暂停 · 点击播放";
  }
  $("#music-play").addEventListener("click", async () => {
    if (!state.musicSource) {
      toast("尚未配置音源。管理员可在「管理 → 站点设置」上传或填写音乐地址。");
      return;
    }
    if (!audio.paused) {
      state.musicUserPaused = true;
      audio.pause();
    } else {
      state.musicUserPaused = false;
      await playMusic();
    }
    updateMusic();
  });
  $("#music-mute").addEventListener("click", () => {
    audio.muted = !audio.muted;
    store.set("muted", audio.muted);
    savePrefs();
    updateMusic();
  });
  ["play", "pause", "volumechange", "error", "ended"].forEach((name) =>
    audio.addEventListener(name, updateMusic),
  );
  let focusBeforeDialog = null;
  function openDialog(title, content) {
    focusBeforeDialog = document.activeElement;
    $("#dialog-title").textContent = title;
    $("#dialog-body").replaceChildren(content);
    dialog.showModal();
    requestAnimationFrame(() =>
      (
        $("input:not([type=hidden]),select,textarea", dialog) ||
        $("#dialog-close")
      ).focus(),
    );
  }
  $("#dialog-close").addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => {
    if (focusBeforeDialog?.isConnected) focusBeforeDialog.focus();
    else main.focus({ preventScroll: true });
  });
  function field(label, name, value = "", type = "text", options = {}) {
    const wrap = el("label", "field" + (options.full ? " full" : ""));
    const title = el("span", "", label);
    let input;
    if (type === "textarea") input = el("textarea");
    else if (type === "select") {
      input = el("select");
      (options.choices || []).forEach((choice) => {
        const opt = el("option", "", choice.label);
        opt.value = choice.value;
        input.append(opt);
      });
    } else {
      input = el("input");
      input.type = type;
    }
    input.name = name;
    input.value = value ?? "";
    if (options.required) input.required = true;
    if (options.min !== undefined) input.min = options.min;
    if (options.max !== undefined) input.max = options.max;
    if (options.step !== undefined) input.step = options.step;
    if (options.autocomplete) input.autocomplete = options.autocomplete;
    if (options.placeholder) input.placeholder = options.placeholder;
    if (options.maxLength) input.maxLength = options.maxLength;
    wrap.append(title, input);
    if (options.hint) wrap.append(el("small", "", options.hint));
    return wrap;
  }
  function checkbox(label, name, checked) {
    const wrap = el("label", "checkbox-field");
    const input = el("input");
    input.type = "checkbox";
    input.name = name;
    input.checked = checked;
    wrap.append(input, document.createTextNode(label));
    return wrap;
  }
  function bindForm(form, submit, action) {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (submit.disabled) return;
      const err = $(".form-error", form);
      if (err) err.textContent = "";
      submit.disabled = true;
      const previous = submit.textContent;
      submit.textContent = "请稍候…";
      try {
        await action(new FormData(form));
      } catch (error) {
        if (err) err.textContent = error.message;
        else toast(error.message);
      } finally {
        submit.disabled = false;
        submit.textContent = previous;
      }
    });
  }
  function login() {
    const form = el("form", "login-panel");
    form.append(
      el("div", "eyebrow", "YOUR PRIVATE SPACE"),
      el("h1", "", "欢迎回到 Homehub"),
      el("p", "", "登录后管理服务，解锁你的私密空间。"),
      field("用户名", "username", "astral", "text", {
        required: true,
        autocomplete: "username",
      }),
      field("密码", "password", "", "password", {
        required: true,
        autocomplete: "current-password",
      }),
    );
    const err = el("p", "form-error");
    err.setAttribute("role", "alert");
    const submit = button("登录", "button primary");
    submit.type = "submit";
    form.append(err, submit);
    bindForm(form, submit, async (data) => {
      const result = await api("/api/login", {
        method: "POST",
        body: {
          username: data.get("username"),
          password: data.get("password"),
        },
      });
      state.data.csrf = result.csrf;
      state.data.authenticated = true;
      state.data.username = result.username;
      await refresh();
      toast("欢迎回来");
    });
    main.append(form);
  }
  function admin() {
    main.className = "admin-page";
    if (!state.data.authenticated) {
      login();
      return;
    }
    const heading = el("div", "page-heading");
    const titles = el("div");
    titles.append(
      el("div", "eyebrow", "CONTROL ROOM"),
      el("h1", "", "管理你的数字生活"),
      el(
        "p",
        "",
        `你好，${state.data.username || "管理员"}。让每个入口恰到好处。`,
      ),
    );
    const logout = button("退出登录", "button", async () => {
      try {
        await api("/api/logout", { method: "POST" });
        audio.pause();
        await refresh();
        toast("已退出登录");
      } catch (error) {
        toast(error.message);
      }
    });
    heading.append(titles, logout);
    const tabs = el("div", "admin-tabs");
    [
      ["links", "服务管理"],
      ["categories", "分类管理"],
      ["settings", "站点设置"],
      ["account", "账户与备份"],
    ].forEach(([value, label]) => {
      const node = button(
        label,
        "chip" + (state.adminTab === value ? " active" : ""),
        () => {
          state.adminTab = value;
          render();
        },
      );
      node.setAttribute("aria-pressed", String(state.adminTab === value));
      tabs.append(node);
    });
    const panel = el("section", "admin-panel");
    main.append(heading, tabs, panel);
    if (state.adminTab === "categories") adminCategories(panel);
    else if (state.adminTab === "settings") adminSettings(panel);
    else if (state.adminTab === "account") adminAccount(panel);
    else adminLinks(panel);
  }
  async function mutate(path, method, body, success) {
    const result = await api(path, {
      method,
      ...(body !== undefined ? { body } : {}),
    });
    await refresh();
    if (dialog.open) dialog.close();
    toast(
      path === "/api/import" &&
        Array.isArray(result.missingIcons) &&
        result.missingIcons.length > 0
        ? `${success}；${result.missingIcons.length} 个图标文件缺失，已回退默认。`
        : success,
    );
  }
  function adminLinks(panel) {
    const toolbar = el("div", "admin-toolbar");
    toolbar.append(
      el("h2", "", `服务 · ${state.data.links.length}`),
      button("＋ 添加服务", "button primary", () => editLink()),
    );
    panel.append(toolbar);
    const list = el("div", "admin-list");
    const catChoices = categories();
    if (!state.data.links.length)
      list.append(
        empty("从第一个入口开始", "添加名称和地址，把你的数字世界连接起来。"),
      );
    links().forEach((link, index) => {
      const row = el("div", "admin-row");
      const info = el("div", "row-info");
      info.append(el("strong", "", link.name), el("small", "", link.url));
      const tags = el("div");
      tags.append(
        el("span", "tag", categoryName(link.category_id)),
        el("span", "tag", link.visibility === "private" ? "◇ 私密" : "公开"),
        el("span", "tag", `排序 ${link.rank || 0}`),
      );
      if (link.pinned) tags.append(el("span", "tag", "置顶"));
      info.append(tags);
      const actions = el("div", "row-actions");
      actions.append(
        button("编辑", "button small", () => editLink(link)),
        button("删除", "button small danger", () =>
          confirmAction(
            "删除服务",
            `确定删除「${link.name}」？此操作无法撤销。`,
            () =>
              mutate(
                `/api/links/${encodeURIComponent(link.id)}`,
                "DELETE",
                undefined,
                "服务已删除",
              ),
          ),
        ),
      );
      row.append(emblem(link, index), info, actions);
      list.append(row);
    });
    panel.append(list);
    if (!catChoices.length)
      panel.append(
        el("p", "hint", "建议先在「分类管理」中创建一个分类，再添加服务。"),
      );
  }
  function localIconSource(value) {
    return typeof value === "string" &&
      /^\/(assets|media)\/icons\/[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}\.(png|jpe?g|webp|svg|ico)$/.test(
        value,
      )
      ? value
      : "";
  }
  function buildIconEditor(link = null) {
    const section = el("section", "icon-editor full");
    section.id = "icon-editor";
    section.setAttribute("aria-label", "服务图标");
    const heading = el("div", "icon-editor-heading");
    const preview = el("div", "icon-preview");
    preview.id = "icon-preview";
    const details = el("div");
    details.append(
      el("strong", "", "服务图标"),
      el(
        "p",
        "hint",
        "上传、抓取远程图片，或复用图标库。保存服务才会应用。图标可公开访问，请勿上传敏感图片。",
      ),
    );
    heading.append(preview, details);
    const pathField = field(
      "已选图标 · 站内路径",
      "iconUrl",
      link?.iconUrl || "",
      "text",
      {
        full: true,
        maxLength: 180,
        placeholder: "留空使用默认图标",
        hint: "支持 /assets/icons/… 或 /media/icons/…；不支持 NAS 系统绝对路径。",
      },
    );
    const pathInput = $("input", pathField);
    pathInput.id = "icon-local-path";
    pathInput.autocomplete = "off";
    const fallback = field(
      "备用字符图标",
      "icon",
      link?.icon || "globe",
      "text",
      {
        maxLength: 40,
        hint: "无默认图片时显示；可填写 emoji 或 cloud、film、music、folder 等名称。",
      },
    );
    const fallbackInput = $("input", fallback);
    const status = el("p", "icon-status");
    status.id = "icon-status";
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    const libraryField = el("label", "field");
    libraryField.append(el("span", "", "选择图标库"));
    const library = el("select");
    library.id = "icon-library";
    library.append(new Option("默认 · 官方图标 / 备用字符", ""));
    const builtinGroup = el("optgroup");
    builtinGroup.label = "内置官方图标";
    const names = new Map(
      state.data.links
        .filter((l) => l.catalog_key)
        .map((l) => [l.catalog_key, l.name]),
    );
    Object.entries(window.HOMEHUB_ICONS || {}).forEach(([key, item]) => {
      if (localIconSource(item.src))
        builtinGroup.append(new Option(names.get(key) || key, item.src));
    });
    library.append(builtinGroup);
    const uploadedGroup = el("optgroup");
    uploadedGroup.label = "已上传 / 已抓取";
    library.append(uploadedGroup);
    libraryField.append(library);
    const addLibraryOption = (src, label) => {
      if (!Array.from(library.options).some((o) => o.value === src))
        uploadedGroup.append(new Option(label || src.split("/").pop(), src));
    };
    const refreshPreview = () => {
      const value = pathInput.value.trim();
      const draft = {
        ...(link || {}),
        icon: fallbackInput.value || "globe",
        iconUrl: localIconSource(value),
      };
      preview.replaceChildren(emblem(draft));
      preview.title = value ? "自定义图标预览" : "默认图标预览";
      library.value = value;
    };
    const choose = (src) => {
      status.classList.remove("is-error");
      pathInput.value = src;
      if (src) addLibraryOption(src);
      refreshPreview();
    };
    if (pathInput.value) addLibraryOption(pathInput.value, "当前自定义图标");
    library.addEventListener("change", () => {
      choose(library.value);
      status.textContent = "预览已更新，保存服务后生效。";
    });
    pathInput.addEventListener("input", refreshPreview);
    fallbackInput.addEventListener("input", refreshPreview);
    const fileField = el("label", "field full");
    fileField.append(el("span", "", "上传图片"));
    const file = el("input");
    file.id = "icon-file";
    file.type = "file";
    file.accept = ".png,.jpg,.jpeg,.webp,.svg,.ico";
    fileField.append(
      file,
      el(
        "small",
        "hint",
        "PNG / JPG / WebP / SVG / ICO，最大 1 MiB。建议使用 64px 以上透明图片。",
      ),
    );
    const remoteField = el("label", "field full");
    remoteField.append(el("span", "", "远程图片 URL"));
    const remoteRow = el("div", "icon-url-row");
    const remote = el("input");
    remote.id = "icon-remote-url";
    remote.type = "text";
    remote.inputMode = "url";
    remote.autocomplete = "off";
    remote.placeholder = "https://example.com/favicon.ico";
    remote.maxLength = 2048;
    const fetchButton = button("抓取到本站", "button");
    fetchButton.id = "icon-fetch";
    remoteRow.append(remote, fetchButton);
    remoteField.append(
      remoteRow,
      el(
        "small",
        "hint",
        "保存为本地副本，不热链外站；需为无需登录即可访问的图片地址。",
      ),
    );
    const reset = button("恢复默认图标", "button small", () => {
      choose("");
      status.textContent = "已恢复默认预览，保存服务后生效。";
    });
    reset.id = "icon-reset";
    let busy = false;
    const controls = [file, remote, fetchButton, library, pathInput, reset];
    const upload = async (options) => {
      if (busy) return;
      busy = true;
      section.dataset.busy = "true";
      controls.forEach((node) => {
        node.disabled = true;
      });
      const submit = section
        .closest("form")
        ?.querySelector('button[type="submit"]');
      if (submit) submit.disabled = true;
      status.classList.remove("is-error");
      status.textContent = "正在校验并保存图标…";
      try {
        const result = await api("/api/icons", { method: "POST", ...options });
        const src = localIconSource(result.url);
        if (!src) throw new Error("服务器返回了无效的图标路径。");
        choose(src);
        status.textContent =
          "图标已存入图标库；保存服务后应用，取消则不改变当前服务。";
      } catch (error) {
        status.classList.add("is-error");
        status.textContent = error.message;
      } finally {
        busy = false;
        section.dataset.busy = "false";
        controls.forEach((node) => {
          node.disabled = false;
        });
        if (submit) submit.disabled = false;
        file.value = "";
      }
    };
    file.addEventListener("change", () => {
      const selected = file.files?.[0];
      if (!selected) return;
      if (!selected.size || selected.size > 1024 * 1024) {
        status.classList.add("is-error");
        status.textContent = "请选择不超过 1 MiB 的图片文件。";
        file.value = "";
        return;
      }
      upload({
        body: selected,
        headers: {
          "Content-Type": selected.type || "application/octet-stream",
          "X-Filename": encodeURIComponent(selected.name),
        },
      });
    });
    fetchButton.addEventListener("click", () => {
      const target = safeUrl(remote.value.trim());
      if (!target) {
        status.classList.add("is-error");
        status.textContent = "请填写完整的 http:// 或 https:// 图片地址。";
        return;
      }
      upload({ body: { url: target } });
    });
    remote.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        fetchButton.click();
      }
    });
    section.append(
      heading,
      fileField,
      remoteField,
      libraryField,
      fallback,
      pathField,
      reset,
      status,
    );
    refreshPreview();
    api("/api/icons")
      .then((result) => {
        (result.library || []).forEach((name) => {
          const src = localIconSource(`/media/icons/${name}`);
          if (src) addLibraryOption(src);
        });
        library.value = pathInput.value.trim();
      })
      .catch((error) => {
        if (!busy && !status.textContent) {
          status.classList.add("is-error");
          status.textContent = `图标库加载失败：${error.message}，仍可上传或恢复默认。`;
        }
      });
    return section;
  }
  function editLink(link = null) {
    const form = el("form");
    const grid = el("div", "form-grid");
    const cats = categories();
    grid.append(
      field("服务名称", "name", link?.name, "text", {
        required: true,
        maxLength: 80,
      }),
      field(
        "所属分类",
        "category_id",
        link?.category_id ?? cats[0]?.id ?? "",
        "select",
        {
          required: true,
          choices: [
            { value: "", label: "请选择分类" },
            ...cats.map((c) => ({ value: String(c.id), label: c.name })),
          ],
        },
      ),
      field("服务地址", "url", link?.url, "url", {
        required: true,
        full: true,
        placeholder: "https://example.com",
        hint: "仅支持 http:// 或 https://，服务在新标签页打开。",
      }),
      field(
        "启动方式",
        "launch_mode",
        link?.launch_mode || "direct",
        "select",
        {
          choices: [
            { value: "direct", label: "直接打开服务地址" },
            { value: "fnos", label: "飞牛桌面启动（需要应用票据）" },
          ],
          hint: "票据保护的飞牛应用提供桌面启动指引，不绕过原有鉴权。",
        },
      ),
      field("描述", "description", link?.description, "textarea", {
        full: true,
        maxLength: 200,
      }),
      buildIconEditor(link),
      field("可见性", "visibility", link?.visibility || "private", "select", {
        choices: [
          { value: "public", label: "公开 · 所有人可见" },
          { value: "private", label: "私密 · 仅登录后可见" },
        ],
      }),
      field("排序权重", "rank", link?.rank ?? 0, "number", {
        required: true,
        step: 1,
        min: -100000,
        max: 100000,
        hint: "数值越小越靠前（−100000 至 100000）。",
      }),
      field("搜索别名", "aliases", link?.aliases, "text", {
        hint: "例如：影视 movie video",
        maxLength: 200,
      }),
      checkbox("置顶到首页星光位", "pinned", Boolean(link?.pinned)),
    );
    const err = el("p", "form-error");
    err.setAttribute("role", "alert");
    const actions = el("div", "form-actions");
    const submit = button("保存服务", "button primary");
    submit.type = "submit";
    actions.append(
      button("取消", "button", () => dialog.close()),
      submit,
    );
    form.append(grid, err, actions);
    bindForm(form, submit, async (data) => {
      const url = String(data.get("url")).trim();
      if (!safeUrl(url))
        throw new Error("请输入有效的 http:// 或 https:// 地址。");
      const rank = Number(data.get("rank"));
      if (!Number.isSafeInteger(rank)) throw new Error("排序权重必须是整数。");
      const cat = cats.find((c) => String(c.id) === data.get("category_id"));
      if (!cat)
        throw new Error("请选择一个有效分类；还没有分类时，请先创建分类。");
      const name = String(data.get("name")).trim();
      if (!name) throw new Error("服务名称不能为空。");
      const body = {
        name,
        category_id: cat.id,
        url,
        description: String(data.get("description")).trim(),
        icon: String(data.get("icon")).trim(),
        iconUrl: String(data.get("iconUrl") || "").trim(),
        visibility: data.get("visibility"),
        launch_mode: data.get("launch_mode"),
        pinned: data.has("pinned"),
        rank,
        aliases: String(data.get("aliases")).trim(),
      };
      await mutate(
        link ? `/api/links/${encodeURIComponent(link.id)}` : "/api/links",
        link ? "PUT" : "POST",
        body,
        "服务已保存",
      );
    });
    openDialog(link ? "编辑服务" : "添加服务", form);
  }
  function adminCategories(panel) {
    const toolbar = el("div", "admin-toolbar");
    toolbar.append(
      el("h2", "", `分类 · ${state.data.categories.length}`),
      button("＋ 添加分类", "button primary", () => editCategory()),
    );
    panel.append(
      toolbar,
      el(
        "p",
        "hint",
        "排序数值越小越靠前。含有服务的分类不能删除，请先移动或删除其中的服务。",
      ),
    );
    const list = el("div", "admin-list");
    if (!categories().length)
      list.append(
        empty("让服务各就其位", "创建一个分类，如「影音娱乐」或「效率工具」。"),
      );
    categories().forEach((cat) => {
      const row = el("div", "admin-row");
      const info = el("div", "row-info");
      const count = state.data.links.filter(
        (link) => String(link.category_id) === String(cat.id),
      ).length;
      info.append(
        el("strong", "", cat.name),
        el("small", "", `${count} 个服务 · 排序 ${cat.rank || 0}`),
      );
      const actions = el("div", "row-actions");
      actions.append(
        button("编辑", "button small", () => editCategory(cat)),
        button("删除", "button small danger", () => {
          if (count) {
            toast("此分类仍有服务，请先移动或删除服务。");
            return;
          }
          confirmAction(
            "删除分类",
            `确定删除「${cat.name}」？此操作无法撤销。`,
            () =>
              mutate(
                `/api/categories/${encodeURIComponent(cat.id)}`,
                "DELETE",
                undefined,
                "分类已删除",
              ),
          );
        }),
      );
      row.append(info, actions);
      list.append(row);
    });
    panel.append(list);
  }
  function editCategory(cat = null) {
    const form = el("form");
    const grid = el("div", "form-grid");
    grid.append(
      field("分类名称", "name", cat?.name, "text", {
        required: true,
        maxLength: 40,
      }),
      field("排序权重", "rank", cat?.rank ?? 0, "number", {
        required: true,
        step: 1,
        min: -100000,
        max: 100000,
        hint: "数值越小越靠前（−100000 至 100000）。",
      }),
    );
    const err = el("p", "form-error");
    err.setAttribute("role", "alert");
    const actions = el("div", "form-actions");
    const submit = button("保存分类", "button primary");
    submit.type = "submit";
    actions.append(
      button("取消", "button", () => dialog.close()),
      submit,
    );
    form.append(grid, err, actions);
    bindForm(form, submit, async (data) => {
      const rank = Number(data.get("rank")),
        name = String(data.get("name")).trim();
      if (!name) throw new Error("分类名称不能为空。");
      if (!Number.isSafeInteger(rank)) throw new Error("排序权重必须是整数。");
      await mutate(
        cat
          ? `/api/categories/${encodeURIComponent(cat.id)}`
          : "/api/categories",
        cat ? "PUT" : "POST",
        { name, rank },
        "分类已保存",
      );
    });
    openDialog(cat ? "编辑分类" : "添加分类", form);
  }
  function confirmAction(title, message, action) {
    const content = el("div");
    const text = el("p", "", message);
    const error = el("p", "form-error");
    error.setAttribute("role", "alert");
    const actions = el("div", "form-actions");
    const yes = button(
      "确认" + (title.includes("删除") ? "删除" : ""),
      "button danger",
      async () => {
        yes.disabled = true;
        error.textContent = "";
        try {
          await action();
        } catch (err) {
          error.textContent = err.message;
        } finally {
          yes.disabled = false;
        }
      },
    );
    actions.append(
      button("取消", "button", () => dialog.close()),
      yes,
    );
    content.append(text, error, actions);
    openDialog(title, content);
    $("#dialog-close").focus();
  }
  function adminSettings(panel) {
    const settings = state.data.settings;
    const form = el("form", "settings-section");
    form.append(el("h3", "", "首页与音乐"));
    const grid = el("div", "form-grid");
    grid.append(
      field("站点标题", "title", settings.title || "Homehub", "text", {
        required: true,
        maxLength: 40,
      }),
      field("首页标语", "tagline", settings.tagline || "天空沒有極限", "text", {
        required: true,
        maxLength: 100,
      }),
      field("首页副标题", "subtitle", settings.subtitle, "textarea", {
        full: true,
        maxLength: 200,
      }),
      field("默认背景音乐标题", "musicTitle", settings.musicTitle, "text", {
        maxLength: 100,
      }),
      checkbox(
        "打开网页时自动播放背景音乐",
        "musicAutoplay",
        settings.musicAutoplay === true,
      ),
      field("默认背景音乐地址", "musicUrl", settings.musicUrl, "text", {
        placeholder: "https://… 或 /media/music.mp3",
        hint: "默认背景音乐：填写 HTTPS 音源，或使用下方上传功能自动填入。留空关闭背景音乐。",
      }),
    );
    const err = el("p", "form-error");
    err.setAttribute("role", "alert");
    const actions = el("div", "form-actions");
    const submit = button("保存站点设置", "button primary");
    submit.type = "submit";
    actions.append(submit);
    form.append(
      grid,
      el(
        "p",
        "hint",
        "自动播放受浏览器策略限制：打开页面先尝试播放，若被拦截会显示一键开启；访客的静音和音量偏好始终保留。",
      ),
      err,
      actions,
    );
    bindForm(form, submit, async (data) => {
      const musicUrl = String(data.get("musicUrl")).trim();
      if (musicUrl && !safeUrl(musicUrl, true))
        throw new Error(
          "音乐地址仅支持 HTTPS，或上传生成的 /media/music.mp3、.ogg、.wav、.m4a。",
        );
      await mutate(
        "/api/settings",
        "PUT",
        {
          title: String(data.get("title")).trim(),
          tagline: String(data.get("tagline")).trim(),
          subtitle: String(data.get("subtitle")).trim(),
          musicTitle: String(data.get("musicTitle")).trim(),
          musicUrl,
          musicAutoplay: data.has("musicAutoplay"),
        },
        "站点设置已保存",
      );
    });
    const uploadSection = el("section", "settings-section");
    uploadSection.append(
      el("h3", "", "上传音乐"),
      el(
        "p",
        "hint",
        "选择你有权使用的音频。支持 MP3 / OGG / WAV / M4A / OPUS，最大 25 MiB。上传成功后直接设为站点默认背景音乐，标题取文件名；自动播放沿用上方已保存的开关。",
      ),
    );
    const upload = el("form");
    const label = el("label", "field");
    label.append(el("span", "", "选择音频文件"));
    const input = el("input");
    input.type = "file";
    input.accept =
      ".mp3,.ogg,.wav,.m4a,.opus,audio/mpeg,audio/ogg,audio/wav,audio/mp4,audio/opus";
    input.name = "music";
    input.required = true;
    label.append(input);
    const uploadErr = el("p", "form-error");
    uploadErr.setAttribute("role", "alert");
    const uploadButton = button("上传并设为默认背景音乐", "button primary");
    uploadButton.type = "submit";
    upload.append(label, uploadErr, uploadButton);
    bindForm(upload, uploadButton, async () => {
      const file = input.files[0];
      if (!file) throw new Error("请选择音频文件。");
      if (file.size > 25 * 1024 * 1024)
        throw new Error("音频超过 25 MiB，请选择更小的文件。");
      if (!/\.(mp3|ogg|wav|m4a|opus)$/i.test(file.name))
        throw new Error("仅支持 MP3、OGG、WAV、M4A、OPUS 格式。");
      const result = await api("/api/music", {
        method: "POST",
        body: file,
        headers: {
          "Content-Type": file.type || "application/octet-stream",
          "X-Filename": encodeURIComponent(file.name),
        },
      });
      if (!result.musicUrl)
        throw new Error("上传响应中缺少音乐地址，请刷新检查。");
      await refresh();
      toast("上传成功，已设为默认背景音乐。自动播放按站点设置执行。");
    });
    uploadSection.append(upload);
    panel.append(form, uploadSection);
  }
  function adminAccount(panel) {
    const me = state.data.me || { displayName: state.data.username || "" };
    const profileSection = el("section", "settings-section");
    profileSection.append(
      el("h3", "", "个人资料与音乐偏好"),
      el(
        "p",
        "hint",
        `当前账户 ${me.username} · 显示名称和这里的音乐偏好只影响本账户；收藏同样按账户分开保存。`,
      ),
    );
    const profileForm = el("form");
    const profileGrid = el("div", "form-grid");
    profileGrid.append(
      field("显示名称", "displayName", me.displayName, "text", {
        required: true,
        maxLength: 40,
      }),
      field(
        "背景音乐自动播放",
        "musicAutoplay",
        me.prefs?.musicAutoplay === true
          ? "on"
          : me.prefs?.musicAutoplay === false
            ? "off"
            : "site",
        "select",
        {
          choices: [
            { value: "site", label: "跟随站点设置" },
            { value: "on", label: "始终自动播放" },
            { value: "off", label: "始终不自动播放" },
          ],
        },
      ),
    );
    const profileErr = el("p", "form-error");
    profileErr.setAttribute("role", "alert");
    const profileSubmit = button("保存个人设置", "button primary");
    profileSubmit.type = "submit";
    profileForm.append(profileGrid, profileErr, profileSubmit);
    bindForm(profileForm, profileSubmit, async (data) => {
      const mode = data.get("musicAutoplay");
      await api("/api/profile", {
        method: "PUT",
        body: { displayName: String(data.get("displayName")).trim() },
      });
      await api("/api/prefs", {
        method: "PUT",
        body: {
          musicAutoplay: mode === "on" ? true : mode === "off" ? false : null,
        },
      });
      await refresh();
      toast("个人设置已保存。");
    });
    profileSection.append(profileForm);
    const section = el("section", "settings-section");
    section.append(
      el("h3", "", "修改密码"),
      el("p", "hint", "修改后，其他设备的登录会话将失效；当前会话保留。"),
    );
    const form = el("form");
    const grid = el("div", "form-grid");
    grid.append(
      field("当前密码", "currentPassword", "", "password", {
        required: true,
        autocomplete: "current-password",
      }),
      field("新密码", "newPassword", "", "password", {
        required: true,
        autocomplete: "new-password",
      }),
      field("再次输入新密码", "confirmPassword", "", "password", {
        required: true,
        autocomplete: "new-password",
      }),
    );
    const err = el("p", "form-error");
    err.setAttribute("role", "alert");
    const submit = button("更新密码", "button");
    submit.type = "submit";
    form.append(grid, err, submit);
    bindForm(form, submit, async (data) => {
      if (data.get("newPassword") !== data.get("confirmPassword"))
        throw new Error("两次输入的新密码不一致。");
      if (data.get("newPassword") === data.get("currentPassword"))
        throw new Error("新密码应与当前密码不同。");
      await api("/api/password", {
        method: "POST",
        body: {
          currentPassword: data.get("currentPassword"),
          newPassword: data.get("newPassword"),
        },
      });
      form.reset();
      await refresh(false);
      toast("密码已更新，其他登录会话已失效。");
    });
    section.append(form);
    const backup = el("section", "settings-section");
    backup.append(
      el("h3", "", "备份与恢复"),
      el(
        "p",
        "hint",
        "JSON 备份包含分类、服务、设置和图标引用，不包含图片或音乐文件。导入会替换全部分类与服务，请先保存现有备份。",
      ),
    );
    const exportButton = button("↓ 导出 JSON 备份", "button", async () => {
      exportButton.disabled = true;
      try {
        const response = await fetch("/api/export", {
          credentials: "same-origin",
        });
        if (!response.ok) {
          const result = await response.json().catch(() => ({}));
          throw new Error(result.error || "导出失败，请重新登录后重试。");
        }
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const a = el("a");
        a.href = url;
        a.download = `homehub-backup-${new Date().toISOString().slice(0, 10)}.json`;
        document.body.append(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
        toast("备份已开始下载");
      } catch (error) {
        toast(error.message);
      } finally {
        exportButton.disabled = false;
      }
    });
    backup.append(
      exportButton,
      el(
        "div",
        "notice",
        "注意：导入会覆盖当前分类与服务，无法自动撤销。只有明确确认后才会提交。",
      ),
    );
    const importForm = el("form");
    const label = el("label", "field");
    label.append(el("span", "", "选择 JSON 备份"));
    const file = el("input");
    file.type = "file";
    file.accept = ".json,application/json";
    file.required = true;
    label.append(file);
    const importErr = el("p", "form-error");
    importErr.setAttribute("role", "alert");
    const importButton = button("检查并导入备份", "button danger");
    importButton.type = "submit";
    importForm.append(label, importErr, importButton);
    bindForm(importForm, importButton, async () => {
      const selected = file.files[0];
      if (!selected) throw new Error("请选择备份文件。");
      if (selected.size > 25 * 1024 * 1024)
        throw new Error("备份文件过大（最多 25 MiB）。");
      let payload;
      try {
        payload = JSON.parse(await selected.text());
      } catch {
        throw new Error("无法解析 JSON，请选择有效的备份文件。");
      }
      if (
        !payload ||
        typeof payload !== "object" ||
        !Array.isArray(payload.categories) ||
        !Array.isArray(payload.links)
      )
        throw new Error("备份必须包含 categories 和 links 数组。");
      confirmAction(
        "覆盖并导入备份",
        `确定使用「${selected.name}」替换当前全部分类与服务？将导入 ${payload.categories.length} 个分类、${payload.links.length} 个服务。建议先导出现有备份。`,
        () => mutate("/api/import", "POST", payload, "备份已导入"),
      );
    });
    backup.append(importForm);
    const catalogSection = el("section", "settings-section");
    catalogSection.append(
      el("h3", "", "目录数据（seed.json 热加载）"),
      el(
        "p",
        "hint",
        '在这个面板上传只包含新增或修改条目的 seed.json，门户会与内置目录合并并立即生效；直接改 NAS 上的 data/seed.json 也会在刷新后自动应用，不必重新发布版本。条目用 catalog_key 识别，分类可以写 "category_key": "category:music"。移除自定义目录不会删除已经进入门户的服务。',
      ),
    );
    const catalogStatus = el("div", "catalog-status");
    const catalogError = el("p", "form-error");
    catalogError.setAttribute("role", "alert");
    async function loadCatalogStatus() {
      catalogError.textContent = "";
      try {
        const status = await api("/api/catalog");
        const parts = [
          status.overlay ? "来源：data/seed.json + 内置目录" : "来源：内置目录",
          status.total == null ? null : `服务条目 ${status.total}`,
          status.appliedAt
            ? `上次应用 ${new Date(status.appliedAt * 1000).toLocaleString()}`
            : null,
          status.pending ? "有改动待应用" : null,
        ].filter(Boolean);
        catalogStatus.textContent = parts.join(" · ");
        if (status.error) {
          catalogError.textContent = `目录未应用：${status.error}`;
          catalogStatus.classList.add("is-error");
        } else catalogStatus.classList.remove("is-error");
        removeCatalogButton.hidden = !status.overlay;
      } catch (error) {
        catalogError.textContent = error.message;
      }
    }
    const catalogUpload = el("form", "catalog-upload");
    const catalogLabel = el("label", "field");
    catalogLabel.append(el("span", "", "上传 seed.json（最多 1 MiB）"));
    const catalogFile = el("input");
    catalogFile.id = "catalog-seed-file";
    catalogFile.type = "file";
    catalogFile.accept = ".json,application/json";
    catalogFile.required = true;
    catalogLabel.append(catalogFile);
    const catalogUploadButton = button("上传并应用", "button primary");
    catalogUploadButton.type = "submit";
    catalogUpload.append(catalogLabel, catalogUploadButton);
    bindForm(catalogUpload, catalogUploadButton, async () => {
      const selected = catalogFile.files[0];
      if (!selected) throw new Error("请选择 seed.json。");
      if (selected.size > 1024 * 1024)
        throw new Error("目录文件过大（最多 1 MiB）。");
      let payload;
      try {
        payload = JSON.parse(await selected.text());
      } catch {
        throw new Error("无法解析 JSON，请检查文件内容。");
      }
      if (
        !payload ||
        typeof payload !== "object" ||
        !Array.isArray(payload.categories) ||
        !Array.isArray(payload.links)
      )
        throw new Error("目录文件必须包含 categories 和 links 数组。");
      const result = await api("/api/catalog/seed", {
        method: "POST",
        body: selected,
      });
      await refresh();
      const summary = result.summary || {};
      toast(
        `目录已应用：新增 ${summary.added || 0}、改名 ${summary.renamed || 0}、跳过 ${summary.skippedDeleted || 0}`,
      );
    });
    const resyncButton = button("立即重新同步", "button", async () => {
      resyncButton.disabled = true;
      try {
        const result = await api("/api/catalog", { method: "POST" });
        const summary = result.summary || {};
        toast(
          `已重新同步：新增 ${summary.added || 0}、改名 ${summary.renamed || 0}、跳过 ${summary.skippedDeleted || 0}`,
        );
        await refresh();
      } catch (error) {
        catalogError.textContent = error.message;
      } finally {
        resyncButton.disabled = false;
      }
    });
    const removeCatalogButton = button("移除自定义目录", "button danger", () =>
      confirmAction(
        "移除自定义目录",
        "将删除 NAS data/seed.json（保留 .bak 备份）并回到内置目录；已经进入门户的服务仍会保留，可在服务列表里自行删除。",
        async () => {
          await api("/api/catalog/seed", { method: "DELETE" });
          await refresh();
          toast("已移除自定义目录");
        },
      ),
    );
    const catalogActions = el("div", "catalog-actions");
    catalogActions.append(resyncButton, removeCatalogButton);
    catalogSection.append(
      catalogStatus,
      catalogError,
      catalogUpload,
      catalogActions,
    );
    loadCatalogStatus();
    panel.append(profileSection, section, catalogSection, backup);
  }
  // ===== 星轨 · 系统观测 =====

  const ORBIT_RANGES = [
    ["1h", "1 小时"],
    ["6h", "6 小时"],
    ["24h", "24 小时"],
    ["7d", "7 天"],
    ["14d", "14 天"],
  ];
  const ORBIT_COLORS = {
    cpu: "#b9a6ef",
    load: "#d5ae6e",
    mem: "#e9c999",
    cached: "#8f85c9",
    swap: "#cc88a7",
    rx: "#7498d6",
    tx: "#74bdaa",
    read: "#cc88a7",
    write: "#d5ae6e",
  };
  const ORBIT_REFRESH = [
    [5, "5 秒"],
    [15, "15 秒"],
    [30, "30 秒"],
    [60, "1 分钟"],
  ];
  const orbitState = {
    range: store.get("orbitRange", "24h"),
    interval: store.get("orbitInterval", 5),
    device: store.get("orbitDevice", ""),
    devices: [],
    snap: null,
    hist: null,
    sort: "cpu",
    query: "",
    detail: null,
    refs: {},
    timers: [],
  };
  let orbitChartId = 0;

  function clearOrbitTimers() {
    orbitState.timers.forEach(clearInterval);
    orbitState.timers = [];
    orbitState.detail = null;
  }

  function restartOrbitSnapshotTimer() {
    if (state.route !== "/monitor") return;
    orbitState.timers.filter(Number.isInteger).forEach(clearInterval);
    orbitState.timers = orbitState.timers.filter((t) => !Number.isInteger(t));
    orbitState.timers.push(
      setInterval(() => {
        if (!document.hidden) orbitFetchSnapshot();
      }, orbitState.interval * 1000),
    );
  }

  function fmtBytes(value, digits) {
    if (value == null || !isFinite(value)) return "—";
    const units = ["B", "KB", "MB", "GB", "TB", "PB"];
    let n = Math.abs(value);
    let i = 0;
    while (n >= 1024 && i < units.length - 1) {
      n /= 1024;
      i++;
    }
    const d = digits ?? (i === 0 ? 0 : n >= 100 ? 0 : 1);
    return `${n.toFixed(d)} ${units[i]}`;
  }
  const fmtRate = (v) =>
    v == null || !isFinite(v) ? "—" : `${fmtBytes(v, 1)}/s`;
  function fmtPercent(v) {
    return v == null || !isFinite(v) ? "—" : `${v.toFixed(1)}%`;
  }
  function fmtDuration(sec) {
    if (sec == null || !isFinite(sec) || sec < 0) return "—";
    sec = Math.round(sec);
    const d = Math.floor(sec / 86400),
      h = Math.floor((sec % 86400) / 3600),
      m = Math.floor((sec % 3600) / 60);
    if (d) return `${d} 天 ${h} 小时`;
    if (h) return `${h} 小时 ${m} 分钟`;
    if (m) return `${m} 分钟`;
    return `${sec} 秒`;
  }
  const fmtTemp = (v) =>
    v == null || !isFinite(v) ? "—" : `${Math.round(v)}°C`;
  function orbitTimeLabel(ts) {
    const d = new Date(ts * 1000);
    const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
    return orbitState.range === "7d" || orbitState.range === "14d"
      ? `${d.getMonth() + 1}/${d.getDate()} ${hm}`
      : hm;
  }

  function svgNode(tag, attrs = {}) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    return node;
  }
  function niceCeil(value) {
    if (!isFinite(value) || value <= 0) return 1;
    const exp = Math.pow(10, Math.floor(Math.log10(value)));
    for (const m of [1, 1.5, 2, 3, 5, 10]) if (m * exp >= value) return m * exp;
    return 10 * exp;
  }
  function seriesMax(values) {
    let top = 0;
    for (const v of values)
      if (v != null && isFinite(v)) top = Math.max(top, v);
    return top;
  }

  function sparkline(values, color, options = {}) {
    const { height = 38, width = 132, fill = true, max } = options;
    const svg = svgNode("svg", {
      viewBox: `0 0 ${width} ${height}`,
      class: "sparkline",
      preserveAspectRatio: "none",
      "aria-hidden": "true",
    });
    const top = niceCeil(max ?? seriesMax(values)) || 1;
    const step = width / Math.max(1, values.length - 1);
    const runs = [];
    let run = [];
    values.forEach((v, i) => {
      if (v == null || !isFinite(v)) {
        if (run.length) runs.push(run);
        run = [];
        return;
      }
      run.push([i * step, height - 3 - (height - 8) * (v / top)]);
    });
    if (run.length) runs.push(run);
    for (const r of runs) {
      if (r.length === 1) {
        svg.append(
          svgNode("circle", { cx: r[0][0], cy: r[0][1], r: 2, fill: color }),
        );
        continue;
      }
      const line = r
        .map((p) => `${p[0].toFixed(1)},${p[1].toFixed(1)}`)
        .join(" ");
      if (fill)
        svg.append(
          svgNode("polygon", {
            points: `${r[0][0]},${height - 1} ${line} ${r[r.length - 1][0]},${height - 1}`,
            fill: color,
            opacity: 0.13,
          }),
        );
      svg.append(
        svgNode("polyline", {
          points: line,
          fill: "none",
          stroke: color,
          "stroke-width": 1.6,
          "stroke-linejoin": "round",
          "stroke-linecap": "round",
          "vector-effect": "non-scaling-stroke",
        }),
      );
    }
    return svg;
  }

  function timeChart(node, { times, series, height = 190, fmt = fmtPercent }) {
    node.replaceChildren();
    if (!times.length) {
      node.append(
        empty("数据还在积累", "观测站每分钟归档一次，稍后再回来看看轨迹。"),
      );
      return;
    }
    const width = Math.max(300, Math.round(node.clientWidth || 640));
    const pad = { l: 52, r: 12, t: 12, b: 24 };
    const innerW = width - pad.l - pad.r;
    const innerH = height - pad.t - pad.b;
    const top = niceCeil(
      Math.max(1e-6, ...series.map((s) => seriesMax(s.values))),
    );
    const x = (i) => pad.l + (innerW * i) / Math.max(1, times.length - 1);
    const y = (v) => pad.t + innerH * (1 - Math.min(1, v / top));
    const svg = svgNode("svg", {
      viewBox: `0 0 ${width} ${height}`,
      width: "100%",
      height,
      class: "orbit-chart",
      role: "img",
    });
    const defs = svgNode("defs");
    svg.append(defs);
    for (let i = 0; i <= 4; i++) {
      const value = (top / 4) * i;
      const ly = y(value);
      svg.append(
        svgNode("line", {
          x1: pad.l,
          x2: width - pad.r,
          y1: ly,
          y2: ly,
          stroke: "#cac6ef",
          opacity: i === 0 ? 0.22 : 0.08,
          "stroke-width": 1,
        }),
      );
      const label = svgNode("text", {
        x: pad.l - 8,
        y: ly + 3.5,
        "text-anchor": "end",
        class: "chart-tick",
      });
      label.textContent = fmt(value);
      svg.append(label);
    }
    const tickCount = Math.min(6, times.length);
    for (let i = 0; i < tickCount; i++) {
      const idx = Math.round(
        (i * (times.length - 1)) / Math.max(1, tickCount - 1),
      );
      const label = svgNode("text", {
        x: x(idx),
        y: height - 7,
        "text-anchor":
          i === 0 ? "start" : i === tickCount - 1 ? "end" : "middle",
        class: "chart-tick",
      });
      label.textContent = orbitTimeLabel(times[idx]);
      svg.append(label);
    }
    series.forEach((s, si) => {
      if (s.fill) {
        const gradient = svgNode("linearGradient", {
          id: `orbit-fill-${++orbitChartId}`,
          x1: 0,
          y1: 0,
          x2: 0,
          y2: 1,
        });
        gradient.append(
          svgNode("stop", {
            offset: "0%",
            "stop-color": s.color,
            "stop-opacity": 0.3,
          }),
          svgNode("stop", {
            offset: "100%",
            "stop-color": s.color,
            "stop-opacity": 0.02,
          }),
        );
        defs.append(gradient);
      }
      const runs = [];
      let run = [];
      s.values.forEach((v, i) => {
        if (v == null || !isFinite(v)) {
          if (run.length) runs.push(run);
          run = [];
          return;
        }
        run.push([x(i), y(v)]);
      });
      if (run.length) runs.push(run);
      for (const r of runs) {
        const d = r
          .map(
            (p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`,
          )
          .join("");
        if (s.fill && r.length > 1)
          svg.append(
            svgNode("path", {
              d: `${d}L${r[r.length - 1][0].toFixed(1)},${y(0)}L${r[0][0].toFixed(1)},${y(0)}Z`,
              fill:
                s.fill === true ? `url(#orbit-fill-${orbitChartId})` : s.fill,
              stroke: "none",
            }),
          );
        svg.append(
          svgNode("path", {
            d,
            fill: "none",
            stroke: s.color,
            "stroke-width": 1.8,
            "stroke-linejoin": "round",
            "stroke-linecap": "round",
            opacity: s.opacity ?? 1,
          }),
        );
      }
    });
    const crosshair = svgNode("line", {
      y1: pad.t,
      y2: height - pad.b,
      stroke: "#e9c999",
      opacity: 0,
      "stroke-width": 1,
      "stroke-dasharray": "3 3",
    });
    svg.append(crosshair);
    const tooltip = el("div", "chart-tooltip");
    tooltip.setAttribute("aria-hidden", "true");
    const overlay = svgNode("rect", {
      x: pad.l,
      y: pad.t,
      width: innerW,
      height: innerH,
      fill: "transparent",
    });
    overlay.addEventListener("pointermove", (event) => {
      const box = svg.getBoundingClientRect();
      const rel = ((event.clientX - box.left) / box.width) * width;
      const idx = Math.max(
        0,
        Math.min(
          times.length - 1,
          Math.round(((rel - pad.l) / innerW) * (times.length - 1)),
        ),
      );
      const cx = x(idx);
      crosshair.setAttribute("x1", cx);
      crosshair.setAttribute("x2", cx);
      crosshair.setAttribute("opacity", 0.5);
      const rows = series
        .map(
          (s) =>
            `<span><i style="background:${s.color}"></i>${s.name} ${fmt(s.values[idx])}</span>`,
        )
        .join("");
      tooltip.innerHTML = `<b>${orbitTimeLabel(times[idx])}</b>${rows}`;
      tooltip.classList.add("visible");
      const tipX = Math.min(
        width - 130,
        Math.max(0, (cx / width) * node.clientWidth - 60),
      );
      tooltip.style.left = `${tipX}px`;
    });
    overlay.addEventListener("pointerleave", () => {
      crosshair.setAttribute("opacity", 0);
      tooltip.classList.remove("visible");
    });
    svg.append(overlay);
    node.append(svg, tooltip);
    const legend = el("div", "chart-legend");
    series.forEach((s) => {
      const item = el("span", "chart-legend-item");
      const dot = el("i");
      dot.style.background = s.color;
      item.append(dot, document.createTextNode(s.name));
      legend.append(item);
    });
    node.append(legend);
  }

  async function switchDevice(name) {
    orbitState.device = name;
    store.set("orbitDevice", name);
    orbitState.snap = null;
    orbitState.hist = null;
    try {
      orbitState.snap = await api(
        `/api/metrics/snapshot?device=${encodeURIComponent(name)}`,
      );
      orbitOffline(false);
    } catch {
      orbitState.snap = null;
    }
    orbit();
    window.scrollTo({ top: 0, behavior: "instant" });
    orbitFetchSnapshot();
    orbitFetchHistory();
  }

  function renderDeviceChips() {
    const holder = orbitState.refs.orbitDevices;
    if (!holder) return;
    const devices = orbitState.devices;
    holder.replaceChildren();
    if (!devices.length) {
      holder.append(el("span", "orbit-device-hint", "正在发现设备…"));
      return;
    }
    const symbols = { nas: "▤", mac: "⌂", openwrt: "⌁" };
    const activeName =
      ((orbitState.snap && orbitState.snap.device) || {}).name ||
      orbitState.device;
    devices.forEach((d) => {
      const active = d.name === activeName;
      const chip = button(
        `${symbols[d.kind] || "▤"} ${d.name}`,
        "chip orbit-device" +
          (active ? " active" : "") +
          (d.online === false ? " offline" : ""),
        () => {
          if (!active) switchDevice(d.name);
        },
      );
      chip.title = [
        d.role,
        d.kind === "nas"
          ? "本机"
          : d.error
            ? "SSH 拉取：" + d.error
            : "SSH 拉取",
      ]
        .filter(Boolean)
        .join(" · ");
      chip.setAttribute("aria-pressed", String(active));
      holder.append(chip);
    });
  }

  function syncChips(holder, activeLabel) {
    holder.querySelectorAll(".chip").forEach((c) => {
      const active = c.textContent === activeLabel;
      c.classList.toggle("active", active);
      c.setAttribute("aria-pressed", String(active));
    });
  }

  function orbitPanel(span, title, small) {
    const panel = el("section", `admin-panel orbit-panel ${span}`);
    const head = el("div", "orbit-panel-head");
    const h3 = el("h3", "", title);
    if (small) h3.append(el("small", "", small));
    head.append(h3);
    panel.append(head);
    return panel;
  }

  function vitalCard(label, sub) {
    const card = el("article", "orbit-vital");
    card.append(
      el("span", "orbit-vital-label", label),
      el("strong", "orbit-vital-value", "—"),
      el("div", "orbit-vital-spark"),
      el("span", "orbit-vital-sub", sub || ""),
    );
    return card;
  }

  function orbit() {
    main.className = "monitor-page";
    // switchDevice 与布局守卫会直接进入这里；render() 已清过一次，这里再清一次保证幂等。
    main.replaceChildren();
    clearOrbitTimers();
    orbitState.refs = {};
    orbitState.detail = null;
    const kind = orbitState.snap
      ? (orbitState.snap.device || {}).kind || "nas"
      : "nas";
    orbitState.layoutKind = kind;
    const heading = el("div", "page-heading");
    const titles = el("div");
    const subtitle = el("p");
    subtitle.append(
      el("span", "poetic", "每一次心跳都有轨迹 · "),
      document.createTextNode("轻量观测站，随时看见服务器的呼吸。"),
    );
    subtitle.id = "orbit-subtitle";
    titles.append(
      el("div", "eyebrow", "OBSERVATORY"),
      el("h1", "", "星轨"),
      subtitle,
    );
    heading.append(titles);
    const devices = el("div", "categories orbit-devices");
    devices.id = "orbit-devices";
    devices.setAttribute("aria-label", "选择设备");
    orbitState.refs.orbitDevices = devices;
    renderDeviceChips();
    const offline = el("div", "orbit-offline");
    offline.setAttribute("role", "alert");
    offline.hidden = true;
    offline.append(
      el("span", "", "观测站暂时离线 · 采集器可能在启动中，正在自动重试。"),
      button("立即重试", "button small", () => {
        orbitFetchSnapshot();
        orbitFetchHistory();
      }),
    );
    const chips = el("div", "orbit-toolbar");
    const rangeChips = el("div", "categories orbit-ranges");
    rangeChips.setAttribute("aria-label", "历史时间范围");
    ORBIT_RANGES.forEach(([value, label]) => {
      const node = button(
        label,
        "chip" + (orbitState.range === value ? " active" : ""),
        () => {
          orbitState.range = value;
          store.set("orbitRange", value);
          syncChips(rangeChips, label);
          orbitFetchHistory();
        },
      );
      node.setAttribute("aria-pressed", String(orbitState.range === value));
      rangeChips.append(node);
    });
    const refreshChips = el("div", "categories orbit-refresh");
    refreshChips.setAttribute("aria-label", "实时刷新频率");
    const refreshLabel = el("span", "orbit-refresh-label", "刷新");
    refreshChips.append(refreshLabel);
    ORBIT_REFRESH.forEach(([seconds, label]) => {
      const node = button(
        label,
        "chip" + (orbitState.interval === seconds ? " active" : ""),
        () => {
          orbitState.interval = seconds;
          store.set("orbitInterval", seconds);
          syncChips(refreshChips, label);
          restartOrbitSnapshotTimer();
          orbitFetchSnapshot();
        },
      );
      node.setAttribute(
        "aria-pressed",
        String(orbitState.interval === seconds),
      );
      refreshChips.append(node);
    });
    chips.append(rangeChips, refreshChips);
    const vitals = el("div", "orbit-vitals");
    const refs = orbitState.refs;
    refs.cpu = vitalCard("CPU", "加载中");
    refs.mem = vitalCard("内存", "");
    refs.load = vitalCard("负载", "");
    refs.net = vitalCard("网络", "");
    refs.io = vitalCard(kind === "openwrt" ? "温度" : "磁盘", "");
    refs.docker = vitalCard(
      kind === "nas" ? "容器" : kind === "mac" ? "进程" : "连接",
      "",
    );
    vitals.append(
      refs.cpu,
      refs.mem,
      refs.load,
      refs.net,
      refs.io,
      refs.docker,
    );
    const grid = el("div", "orbit-grid");
    const cpuPanel = orbitPanel("span-7", "算力", "CPU 使用率");
    refs.cpuChart = el("div", "chart-holder");
    cpuPanel.append(refs.cpuChart);
    if (kind !== "mac") {
      refs.cpuCores = el("div", "core-bars");
      cpuPanel.append(refs.cpuCores);
    }
    const memPanel = orbitPanel(
      "span-5",
      "内存",
      kind === "mac" ? "统一内存" : "用量与缓存",
    );
    refs.memChart = el("div", "chart-holder");
    memPanel.append(refs.memChart);
    const netPanel = orbitPanel(
      "span-7",
      kind === "openwrt" ? "转发吞吐" : "网络",
      "接口实时速率",
    );
    refs.netChart = el("div", "chart-holder");
    refs.netList = el("div", "orbit-mini-list");
    netPanel.append(refs.netChart, refs.netList);
    let storagePanel = null;
    if (kind !== "openwrt") {
      storagePanel = orbitPanel(
        "span-5",
        kind === "mac" ? "APFS 卷" : "存储",
        kind === "mac" ? "系统盘用量" : "卷用量与磁盘 I/O",
      );
      refs.vols = el("div", "vol-bars");
      storagePanel.append(refs.vols);
      if (kind === "nas") {
        refs.ioChart = el("div", "chart-holder io-holder");
        storagePanel.append(refs.ioChart);
      }
    }
    const containersPanel = orbitPanel("span-12", "容器", "Docker · 自动发现");
    refs.containerSummary = el("div", "orbit-chip-row");
    const controls = el("div", "orbit-controls");
    const search = el("input");
    search.type = "search";
    search.placeholder = "按名称或镜像筛选…";
    search.setAttribute("aria-label", "筛选容器");
    search.value = orbitState.query;
    search.addEventListener("input", () => {
      orbitState.query = search.value;
      renderContainers();
    });
    const sortWrap = el("div", "orbit-sort");
    [
      ["cpu", "按 CPU"],
      ["mem", "按内存"],
      ["name", "按名称"],
    ].forEach(([value, label]) => {
      sortWrap.append(
        button(
          label,
          "chip" + (orbitState.sort === value ? " active" : ""),
          () => {
            orbitState.sort = value;
            sortWrap.querySelectorAll(".chip").forEach((c) => {
              const active = c.textContent === label;
              c.classList.toggle("active", active);
              c.setAttribute("aria-pressed", String(active));
            });
            renderContainers();
          },
        ),
      );
    });
    controls.append(search, sortWrap);
    refs.containerRows = el("div", "container-rows");
    containersPanel.append(refs.containerSummary, controls, refs.containerRows);
    if (kind === "nas") {
      grid.append(containersPanel);
      const tempsPanel = orbitPanel("span-12", "温度", "处理器 · 固态 · 机械");
      refs.temps = el("div", "temp-chips");
      tempsPanel.append(refs.temps);
      grid.append(tempsPanel);
      const probesPanel = orbitPanel(
        "span-7",
        "服务探活",
        "来自门户目录的自动探测",
      );
      refs.probes = el("div", "probe-rows");
      probesPanel.append(refs.probes);
      grid.append(probesPanel);
      const aboutPanel = orbitPanel("span-5", "系统信息", "这台机器的模样");
      refs.about = el("div", "about-list");
      aboutPanel.append(refs.about);
      grid.append(aboutPanel);
    } else if (kind === "openwrt") {
      const tempsPanel = orbitPanel("span-12", "温度", "SoC 实时温度");
      refs.temps = el("div", "temp-chips");
      tempsPanel.append(refs.temps);
      grid.append(tempsPanel);
      const gatewayPanel = orbitPanel(
        "span-5",
        "网关状态",
        "WAN · 连接跟踪 · DHCP",
      );
      refs.gateway = el("div", "gateway-rows");
      gatewayPanel.append(refs.gateway);
      grid.append(gatewayPanel);
      const lanPanel = orbitPanel(
        "span-12",
        "内网设备",
        "DHCP 租约 · ARP 邻居 · 无线关联",
      );
      refs.lanHosts = el("div", "host-rows");
      lanPanel.append(refs.lanHosts);
      grid.append(lanPanel);
      const forwardsPanel = orbitPanel(
        "span-7",
        "端口映射",
        "防火墙 DNAT 规则",
      );
      refs.forwards = el("div", "forward-rows");
      forwardsPanel.append(refs.forwards);
      grid.append(forwardsPanel);
      const aboutPanel = orbitPanel("span-5", "固件与系统", "OpenWrt");
      refs.about = el("div", "about-list");
      aboutPanel.append(refs.about);
      grid.append(aboutPanel);
    } else {
      const aboutPanel = orbitPanel("span-12", "这台 Mac", "macOS");
      refs.about = el("div", "about-list about-wide");
      aboutPanel.append(refs.about);
      grid.append(aboutPanel);
    }
    grid.append(cpuPanel, memPanel, netPanel);
    if (storagePanel) grid.append(storagePanel);
    main.append(heading, devices, offline, chips, vitals, grid);
    orbitFetchSnapshot();
    orbitFetchHistory();
    restartOrbitSnapshotTimer();
    orbitState.timers.push(setInterval(orbitTickHistory, 90000));
  }

  function orbitTickHistory() {
    if (document.hidden) return;
    orbitFetchHistory();
    if (orbitState.detail) loadDetailChart(orbitState.detail.name);
  }

  function orbitOffline(on) {
    const banner = $(".orbit-offline");
    if (banner) banner.hidden = !on;
  }

  function orbitDeviceQuery() {
    return orbitState.device
      ? `?device=${encodeURIComponent(orbitState.device)}`
      : "";
  }

  async function orbitFetchSnapshot() {
    try {
      const snap = await api(`/api/metrics/snapshot${orbitDeviceQuery()}`);
      orbitState.snap = snap;
      orbitOffline(false);
      updateSnapshot();
    } catch (error) {
      orbitOffline(true);
    }
  }

  async function orbitFetchHistory() {
    try {
      const deviceQuery = orbitDeviceQuery();
      const hist = await api(
        `/api/metrics/history${deviceQuery}${deviceQuery ? "&" : "?"}range=${encodeURIComponent(orbitState.range)}`,
      );
      orbitState.hist = hist;
      orbitOffline(false);
      updateHistory();
    } catch {
      orbitOffline(true);
    }
  }

  function vitalUpdate(card, value, values, color, sub) {
    const strong = $(".orbit-vital-value", card);
    strong.textContent = value;
    const holder = $(".orbit-vital-spark", card);
    holder.replaceChildren(
      values && values.some((v) => v != null)
        ? sparkline(values, color, { width: 220, height: 40 })
        : document.createTextNode(""),
    );
    if (sub !== undefined) $(".orbit-vital-sub", card).textContent = sub;
  }

  function updateSnapshot() {
    const snap = orbitState.snap;
    if (!snap) return;
    if ((snap.device || {}).kind !== orbitState.layoutKind) {
      orbit();
      return;
    }
    orbitState.devices = snap.devices || [];
    renderDeviceChips();
    const s = snap.sample || {};
    const info = snap.info || {};
    const tail = snap.tail || {};
    const kind = (snap.device || {}).kind || "nas";
    const subtitle = $("#orbit-subtitle");
    if (subtitle) {
      const leads = {
        nas: "每一次心跳都有轨迹",
        mac: "创作与日常的主力机",
        openwrt: "整个内网的闸门",
      };
      subtitle.replaceChildren(
        el("span", "poetic", (leads[kind] || leads.nas) + " · "),
        document.createTextNode(
          [
            snap.device ? snap.device.name : info.hostname,
            info.role,
            s.uptime != null ? `已运行 ${fmtDuration(s.uptime)}` : null,
            `采集间隔 ${snap.interval}s`,
          ]
            .filter(Boolean)
            .join(" · "),
        ),
      );
    }
    const tempCpu = (s.temp || {}).cpu;
    vitalUpdate(
      orbitState.refs.cpu,
      fmtPercent(s.cpu),
      tail.cpu,
      ORBIT_COLORS.cpu,
      [
        info.cpu_model
          ? info.cpu_model.replace(/ CPU @.*/, "").replace(/\(R\)|\(TM\)/g, "")
          : null,
        info.cpus ? `${info.cpus} 线程` : null,
        tempCpu != null ? `${fmtTemp(tempCpu)}` : null,
      ]
        .filter(Boolean)
        .join(" · "),
    );
    const mem = s.mem || {};
    vitalUpdate(
      orbitState.refs.mem,
      mem.total ? `${fmtBytes(mem.used)} / ${fmtBytes(mem.total)}` : "—",
      tail.mem_used,
      ORBIT_COLORS.mem,
      mem.total
        ? `可用 ${fmtBytes(mem.available)} · 缓存 ${fmtBytes(mem.cached)} · 交换 ${fmtBytes(mem.swap_used)}`
        : "",
    );
    const load = s.load || {};
    vitalUpdate(
      orbitState.refs.load,
      load.avg ? load.avg[0].toFixed(2) : "—",
      tail.load,
      ORBIT_COLORS.load,
      load.avg
        ? `5 分钟 ${load.avg[1].toFixed(2)} · 15 分钟 ${load.avg[2].toFixed(2)} · ${load.procs ?? "—"} 进程`
        : "",
    );
    const primary = s.net_primary;
    const net = primary ? (s.net || {})[primary] : null;
    vitalUpdate(
      orbitState.refs.net,
      net ? `↓ ${fmtRate(net.rx)}` : "—",
      tail.net_rx,
      ORBIT_COLORS.rx,
      [
        net ? `↑ ${fmtRate(net.tx)}` : null,
        primary,
        net && net.speed > 0 ? `${net.speed} Mb/s` : null,
      ]
        .filter(Boolean)
        .join(" · ") || "暂无活动接口",
    );
    if (kind === "openwrt") {
      const soc = (s.temp || {}).soc;
      vitalUpdate(
        orbitState.refs.io,
        fmtTemp(soc),
        tail.temp_cpu,
        ORBIT_COLORS.read,
        soc != null && soc >= 70 ? "偏热，注意散热" : "SoC 温度",
      );
    } else if (kind === "mac" && s.vol && s.vol["/"]) {
      const root = s.vol["/"];
      const percent = root.total ? (root.used / root.total) * 100 : null;
      vitalUpdate(
        orbitState.refs.io,
        percent != null ? `${percent.toFixed(0)}%` : "—",
        null,
        ORBIT_COLORS.read,
        `APFS 已用 ${fmtBytes(root.used)} / ${fmtBytes(root.total)}`,
      );
    } else {
      let ioTop = null;
      for (const [name, one] of Object.entries(s.io || {})) {
        const traffic = (one.r || 0) + (one.w || 0);
        if (!ioTop || traffic > ioTop.traffic)
          ioTop = { name, traffic, r: one.r, w: one.w };
      }
      vitalUpdate(
        orbitState.refs.io,
        ioTop ? `读 ${fmtRate(ioTop.r)}` : "—",
        tail.io_r,
        ORBIT_COLORS.read,
        ioTop ? `写 ${fmtRate(ioTop.w)} · 最忙 ${ioTop.name}` : "",
      );
    }
    if (kind === "mac") {
      vitalUpdate(
        orbitState.refs.docker,
        s.processes != null ? String(s.processes) : "—",
        null,
        ORBIT_COLORS.tx,
        "进程总数",
      );
    } else if (kind === "openwrt") {
      const conn = s.conntrack || {};
      vitalUpdate(
        orbitState.refs.docker,
        conn.count != null ? String(conn.count) : "—",
        null,
        ORBIT_COLORS.tx,
        `连接跟踪${s.dhcp_leases != null ? ` · DHCP ${s.dhcp_leases} 台` : ""}`,
      );
    } else {
      const docker = s.docker || {};
      vitalUpdate(
        orbitState.refs.docker,
        docker.total != null
          ? `${docker.running ?? "—"} / ${docker.total}`
          : "—",
        null,
        ORBIT_COLORS.tx,
        docker.total != null
          ? `运行中 / 全部 · CPU ${fmtPercent(docker.cpu)} · 内存 ${fmtBytes(docker.mem)}`
          : "Docker 未连接",
      );
    }
    updateCoreBars(s.cores);
    renderVols(s.vol || {});
    renderTemps(s.temp || {});
    renderNetList(s.net || {}, primary);
    renderContainers();
    renderGateway(s);
    renderLanHosts(s.lan_hosts);
    renderForwards(s.port_forwards);
    renderAbout(info, s);
    if (orbitState.detail) updateDetailLive();
  }

  function updateCoreBars(cores) {
    const holder = orbitState.refs.cpuCores;
    if (!holder) return;
    holder.replaceChildren();
    if (!cores || !cores.length) {
      holder.hidden = true;
      return;
    }
    holder.hidden = false;
    cores.forEach((v) => {
      const bar = el("div", "core-bar");
      const fill = el("i");
      fill.style.height = `${Math.max(2, Math.min(100, v ?? 0))}%`;
      if ((v ?? 0) > 85) fill.classList.add("hot");
      bar.append(fill);
      const label = el("span", "", String(v == null ? "–" : Math.round(v)));
      bar.append(label);
      holder.append(bar);
    });
  }

  function renderVols(vols) {
    const holder = orbitState.refs.vols;
    if (!holder) return;
    holder.replaceChildren();
    const names = Object.keys(vols);
    if (!names.length) {
      holder.append(el("p", "hint", "暂未挂载可统计的卷。"));
      return;
    }
    names.forEach((name) => {
      const v = vols[name] || {};
      const percent = v.total ? (v.used / v.total) * 100 : 0;
      const row = el("div", "vol-row");
      const head = el("div", "vol-head");
      const label = el("strong", "", name);
      label.title = v.device ? `${v.device} · ${v.fstype || ""}` : name;
      head.append(
        label,
        el("span", "vol-fstype", v.fstype || ""),
        el("span", "vol-nums", `${fmtBytes(v.used)} / ${fmtBytes(v.total)}`),
      );
      const bar = el("div", "vol-bar");
      const fill = el("i");
      fill.style.width = `${Math.min(100, percent)}%`;
      if (percent > 90) fill.classList.add("hot");
      else if (percent > 75) fill.classList.add("warm");
      bar.append(fill);
      row.append(head, bar);
      holder.append(row);
    });
  }

  function renderTemps(temps) {
    const holder = orbitState.refs.temps;
    if (!holder) return;
    holder.replaceChildren();
    const names = Object.keys(temps);
    if (!names.length) {
      holder.append(el("p", "hint", "未读到温度传感器。"));
      return;
    }
    const labels = {
      cpu: "处理器",
      cpu_cores_max: "核心峰值",
      gpu: "核显",
      pch: "PCH",
      acpitz: "主板",
    };
    names.forEach((name) => {
      const chip = el("span", "temp-chip");
      const value = temps[name];
      if (value >= 75) chip.classList.add("hot");
      else if (value >= 60) chip.classList.add("warm");
      chip.append(
        el("strong", "", fmtTemp(value)),
        el("span", "", labels[name] || name),
      );
      holder.append(chip);
    });
  }

  function renderNetList(net, primary) {
    const holder = orbitState.refs.netList;
    if (!holder) return;
    holder.replaceChildren();
    const names = Object.keys(net)
      .filter((n) => n !== "lo")
      .sort(
        (a, b) =>
          (net[b].rx || 0) +
          (net[b].tx || 0) -
          ((net[a].rx || 0) + (net[a].tx || 0)),
      )
      .slice(0, 4);
    if (!names.length) {
      holder.hidden = true;
      return;
    }
    holder.hidden = false;
    names.forEach((name) => {
      const one = net[name];
      const row = el("div", "mini-row");
      row.append(
        el("strong", "", name + (name === primary ? " ·" : "")),
        el("span", "", `↓ ${fmtRate(one.rx)}  ↑ ${fmtRate(one.tx)}`),
      );
      holder.append(row);
    });
  }

  function containerStatusDot(entry) {
    const dot = el(
      "i",
      "status-dot " + (entry.state === "running" ? "dot-run" : "dot-stop"),
    );
    if (entry.health === "unhealthy") dot.classList.add("dot-bad");
    return dot;
  }

  function renderContainers() {
    const holder = orbitState.refs.containerRows;
    if (!holder) return;
    const snap = orbitState.snap;
    const summary = orbitState.refs.containerSummary;
    const containers = (snap && snap.containers) || [];
    const docker = (snap && snap.sample && snap.sample.docker) || {};
    summary.replaceChildren();
    summary.append(
      el(
        "span",
        "chip static",
        `运行 ${docker.running ?? "—"} · 共 ${docker.total ?? containers.length}`,
      ),
      el("span", "chip static", `CPU ${fmtPercent(docker.cpu)}`),
      el("span", "chip static", `内存 ${fmtBytes(docker.mem)}`),
    );
    const query = orbitState.query.trim().toLocaleLowerCase();
    let items = containers.filter(
      (c) =>
        !query ||
        c.name.toLocaleLowerCase().includes(query) ||
        (c.image || "").toLocaleLowerCase().includes(query),
    );
    const sorters = {
      cpu: (a, b) =>
        (b.cpu || 0) - (a.cpu || 0) || a.name.localeCompare(b.name),
      mem: (a, b) =>
        (b.mem || 0) - (a.mem || 0) || a.name.localeCompare(b.name),
      name: (a, b) => a.name.localeCompare(b.name),
    };
    items = items.slice().sort(sorters[orbitState.sort] || sorters.cpu);
    holder.replaceChildren();
    if (!items.length) {
      holder.append(
        empty(
          query ? "没有匹配的容器" : "暂无容器信息",
          query ? "换个关键词试试。" : "等待观测站汇报 Docker 状态。",
        ),
      );
      return;
    }
    items.forEach((entry) => {
      const row = el(
        "button",
        "container-row" + (entry.state === "running" ? "" : " stopped"),
      );
      row.type = "button";
      row.setAttribute("aria-label", `查看容器 ${entry.name} 详情`);
      row.append(
        containerStatusDot(entry),
        el("span", "container-name", entry.name),
        el("span", "container-image", entry.image || ""),
        el(
          "span",
          "container-cpu",
          entry.state === "running" ? fmtPercent(entry.cpu) : "—",
        ),
        el(
          "span",
          "container-mem",
          entry.state === "running" ? fmtBytes(entry.mem) : "—",
        ),
        el("span", "container-status", entry.status || entry.state),
      );
      row.addEventListener("click", () => openContainerDetail(entry.name));
      holder.append(row);
    });
  }

  function openContainerDetail(name) {
    const snap = orbitState.snap;
    const entry = ((snap && snap.containers) || []).find(
      (c) => c.name === name,
    );
    if (!entry) return;
    const content = el("div", "container-detail");
    const meta = el("div", "detail-meta");
    const live = el("div", "detail-live");
    const cpuHolder = el("div", "chart-holder detail-chart");
    const memHolder = el("div", "chart-holder detail-chart");
    content.append(
      meta,
      live,
      el("h4", "", "CPU 使用轨迹"),
      cpuHolder,
      el("h4", "", "内存轨迹"),
      memHolder,
    );
    orbitState.detail = { name, entry, meta, live, cpuHolder, memHolder };
    dialog.addEventListener(
      "close",
      () => {
        orbitState.detail = null;
      },
      { once: true },
    );
    openDialog("容器 · " + name, content);
    updateDetailLive();
    loadDetailChart(name);
  }

  function updateDetailLive() {
    const detail = orbitState.detail;
    if (!detail) return;
    const entry = ((orbitState.snap && orbitState.snap.containers) || []).find(
      (c) => c.name === detail.name,
    );
    if (!entry) return;
    detail.entry = entry;
    detail.meta.replaceChildren();
    const rows = [
      ["镜像", entry.image || "—"],
      [
        "状态",
        `${entry.state === "running" ? "运行中" : entry.state === "exited" ? "已停止" : entry.state}${entry.health ? " · " + entry.health : ""}`,
      ],
      ["运行时长", entry.status || "—"],
    ];
    if (entry.project) rows.push(["Compose 项目", entry.project]);
    rows.forEach(([k, v]) => {
      const row = el("div", "about-row");
      row.append(el("span", "", k), el("strong", "", v));
      detail.meta.append(row);
    });
    detail.live.replaceChildren();
    const cells = [
      ["CPU", fmtPercent(entry.cpu)],
      ["内存", fmtBytes(entry.mem)],
      ["内存上限", fmtBytes(entry.mem_limit)],
      ["网络收", fmtRate(entry.rx)],
      ["网络发", fmtRate(entry.tx)],
      ["磁盘读", fmtRate(entry.blk_r)],
      ["磁盘写", fmtRate(entry.blk_w)],
    ];
    cells.forEach(([k, v]) => {
      const cell = el("div", "detail-cell");
      cell.append(el("span", "", k), el("strong", "", v));
      detail.live.append(cell);
    });
  }

  async function loadDetailChart(name) {
    const detail = orbitState.detail;
    if (!detail || detail.name !== name) return;
    try {
      const data = await api(
        `/api/metrics/history?range=${encodeURIComponent(orbitState.range)}&container=${encodeURIComponent(name)}`,
      );
      if (orbitState.detail && orbitState.detail.name === name) {
        timeChart(detail.cpuHolder, {
          times: data.t,
          series: [
            {
              name: "CPU",
              values: data.cpu,
              color: ORBIT_COLORS.cpu,
              fill: true,
            },
          ],
          fmt: fmtPercent,
          height: 150,
        });
        timeChart(detail.memHolder, {
          times: data.t,
          series: [
            {
              name: "内存",
              values: data.mem,
              color: ORBIT_COLORS.mem,
              fill: true,
            },
          ],
          fmt: (v) => fmtBytes(v),
          height: 150,
        });
      }
    } catch {
      detail.cpuHolder.replaceChildren(el("p", "hint", "轨迹暂不可用。"));
      detail.memHolder.replaceChildren();
    }
  }

  function updateHistory() {
    const hist = orbitState.hist;
    if (!hist) return;
    const sys = hist.system || {};
    const seriesOf = (values, name, color, fill) => ({
      name,
      values: values || [],
      color,
      fill,
    });
    timeChart(orbitState.refs.cpuChart, {
      times: sys.t || [],
      series: [seriesOf(sys.cpu, "CPU 总量", ORBIT_COLORS.cpu, true)],
      fmt: fmtPercent,
    });
    timeChart(orbitState.refs.memChart, {
      times: sys.t || [],
      series: [
        seriesOf(sys.mem_used, "已用", ORBIT_COLORS.mem, true),
        seriesOf(sys.swap_used, "交换", ORBIT_COLORS.swap),
      ],
      fmt: (v) => fmtBytes(v),
      height: 190,
    });
    const net = sys.net || {};
    const primaryNet =
      orbitState.snap &&
      orbitState.snap.sample &&
      orbitState.snap.sample.net_primary
        ? orbitState.snap.sample.net_primary
        : Object.keys(net).sort(
            (a, b) =>
              seriesMax((net[b] || {}).rx || []) -
              seriesMax((net[a] || {}).rx || []),
          )[0];
    const netSeries =
      primaryNet && net[primaryNet]
        ? [
            seriesOf(
              net[primaryNet].rx,
              `${primaryNet} 下行`,
              ORBIT_COLORS.rx,
              true,
            ),
            seriesOf(net[primaryNet].tx, `${primaryNet} 上行`, ORBIT_COLORS.tx),
          ]
        : [];
    timeChart(orbitState.refs.netChart, {
      times: sys.t || [],
      series: netSeries,
      fmt: (v) => fmtRate(v),
    });
    const io = sys.io || {};
    const topDisks = Object.keys(io)
      .sort(
        (a, b) =>
          seriesMax((io[b] || {}).r || []) +
          seriesMax((io[b] || {}).w || []) -
          (seriesMax((io[a] || {}).r || []) + seriesMax((io[a] || {}).w || [])),
      )
      .slice(0, 3);
    timeChart(orbitState.refs.ioChart, {
      times: sys.t || [],
      series: topDisks.flatMap((d) => [
        seriesOf((io[d] || {}).r, `${d} 读`, ORBIT_COLORS.read),
        seriesOf((io[d] || {}).w, `${d} 写`, ORBIT_COLORS.write),
      ]),
      fmt: (v) => fmtRate(v),
      height: 150,
    });
    renderProbes(hist.probes || []);
    if (orbitState.detail) loadDetailChart(orbitState.detail.name);
  }

  function renderProbes(probes) {
    const holder = orbitState.refs.probes;
    if (!holder) return;
    holder.replaceChildren();
    if (!probes.length) {
      holder.append(
        el(
          "p",
          "hint",
          "还没有探活目标。目录同步完成后，每个服务入口都会自动出现在这里。",
        ),
      );
      return;
    }
    const sorted = probes
      .slice()
      .sort(
        (a, b) =>
          Number(!!a.last && !a.last.ok) - Number(!!b.last && !b.last.ok) ||
          a.name.localeCompare(b.name, "zh-CN"),
      );
    sorted.forEach((p) => {
      const ok = p.last ? p.last.ok : null;
      const row = el("div", "probe-row" + (ok === false ? " failed" : ""));
      const dot = el(
        "i",
        "status-dot" + (ok === false ? " dot-bad" : ok ? " dot-run" : ""),
      );
      dot.title = p.last
        ? `${p.last.ok ? "最近探测正常" : "最近探测失败"} · ${p.last.ms ?? "—"} ms · HTTP ${p.last.code ?? "—"}`
        : "尚无探测记录";
      const rate = p.rate == null ? "—" : `${(p.rate * 100).toFixed(1)}%`;
      row.append(
        dot,
        el("strong", "", p.name),
        el("span", "probe-host", p.host || ""),
        el("span", "probe-rate", rate),
        el("span", "probe-ms", p.avg_ms != null ? `${p.avg_ms} ms` : "—"),
      );
      holder.append(row);
    });
  }

  function renderGateway(s) {
    const holder = orbitState.refs.gateway;
    if (!holder) return;
    holder.replaceChildren();
    const wan = s.wan || {};
    const wanRow = el("div", "about-row");
    wanRow.append(
      el("span", "", "WAN"),
      el(
        "strong",
        "",
        wan.up
          ? `${wan.ip || "—"} · ${wan.proto || "pppoe"}${wan.uptime != null ? ` · 已连接 ${fmtDuration(wan.uptime)}` : ""}`
          : "未连接",
      ),
    );
    holder.append(wanRow);
    const conn = s.conntrack;
    if (conn && conn.max) {
      const percent = Math.min(100, (conn.count / conn.max) * 100);
      const row = el("div", "vol-row gateway-conn");
      const head = el("div", "vol-head");
      head.append(
        el("strong", "", "连接跟踪"),
        el(
          "span",
          "vol-nums",
          `${conn.count} / ${conn.max} (${percent.toFixed(1)}%)`,
        ),
      );
      const bar = el("div", "vol-bar");
      const fill = el("i");
      fill.style.width = `${percent}%`;
      if (percent > 85) fill.classList.add("hot");
      else if (percent > 65) fill.classList.add("warm");
      bar.append(fill);
      row.append(head, bar);
      holder.append(row);
    }
    if (s.dhcp_leases != null) {
      const row = el("div", "about-row");
      row.append(
        el("span", "", "DHCP 租约"),
        el("strong", "", `${s.dhcp_leases} 台`),
      );
      holder.append(row);
    }
  }

  function renderLanHosts(hosts) {
    const holder = orbitState.refs.lanHosts;
    if (!holder) return;
    holder.replaceChildren();
    if (!hosts || !hosts.length) {
      holder.append(
        el("p", "hint", "暂未读到内网设备（DHCP 租约与 ARP 邻居均为空）。"),
      );
      return;
    }
    const badge = { dhcp: "DHCP", arp: "ARP", wifi: "WiFi" };
    hosts.forEach((one) => {
      const row = el("div", "host-row");
      const info = el("div", "host-info");
      const nameRow = el("div", "host-name");
      nameRow.append(el("strong", "", one.name || "未命名设备"));
      const source = el(
        "span",
        `host-source source-${one.source || "arp"}`,
        badge[one.source] || "ARP",
      );
      nameRow.append(source);
      if (one.signal != null)
        nameRow.append(el("span", "host-signal", `${one.signal} dBm`));
      info.append(
        nameRow,
        el("span", "host-meta", `${one.ip || "—"} · ${one.mac || "MAC 未知"}`),
      );
      row.append(info);
      holder.append(row);
    });
  }

  function renderForwards(forwards) {
    const holder = orbitState.refs.forwards;
    if (!holder) return;
    holder.replaceChildren();
    if (!forwards || !forwards.length) {
      holder.append(el("p", "hint", "没有配置中的端口映射规则。"));
      return;
    }
    forwards.forEach((one) => {
      const row = el("div", "host-row");
      const info = el("div", "host-info");
      const nameRow = el("div", "host-name");
      nameRow.append(el("strong", "", one.name || "未命名规则"));
      const proto = el(
        "span",
        "host-source source-dhcp",
        (one.proto || "tcp").toUpperCase(),
      );
      nameRow.append(proto);
      info.append(
        nameRow,
        el(
          "span",
          "host-meta",
          `外部 :${one.src_port || "—"} → ${one.dest_ip}:${one.dest_port || "—"}`,
        ),
      );
      row.append(info);
      holder.append(row);
    });
  }

  function renderAbout(info, sample) {
    const holder = orbitState.refs.about;
    if (!holder) return;
    const docker = sample.docker || {};
    const rows =
      sample.firmware || sample.model
        ? [
            ["主机名", info.hostname || "—"],
            ["固件", sample.firmware || info.os || "—"],
            ["型号", sample.model || "—"],
            ["内核", info.kernel || "—"],
            [
              "WAN",
              (sample.wan || {}).up
                ? `${sample.wan.ip || "—"}（${sample.wan.proto || "pppoe"}）`
                : "未连接",
            ],
            [
              "已运行",
              sample.uptime != null ? fmtDuration(sample.uptime) : "—",
            ],
            [
              "内网设备",
              Array.isArray(sample.lan_hosts)
                ? `${sample.lan_hosts.length} 台`
                : "—",
            ],
            ["数据保留", "1 小时细粒度 · 14 天分钟归档"],
            ["门户版本", state.data.version || "—"],
          ]
        : [
            ["主机名", info.hostname || "—"],
            ["操作系统", info.os || "—"],
            ["内核", info.kernel || "—"],
            ...(info.arch ? [["架构", info.arch]] : []),
            ...(info.cpu_model ? [["处理器", info.cpu_model]] : []),
            ...(info.cpus != null ? [["逻辑核心", String(info.cpus)]] : []),
            ...(info.docker_version
              ? [
                  ["Docker", `Engine ${info.docker_version}`],
                  [
                    "容器",
                    docker.total != null
                      ? `${docker.running} 运行 / ${docker.total} 个`
                      : "—",
                  ],
                ]
              : []),
            [
              "已运行",
              sample.uptime != null ? fmtDuration(sample.uptime) : "—",
            ],
            ["数据保留", "1 小时细粒度 · 14 天分钟归档"],
            ["门户版本", state.data.version || "—"],
          ];
    holder.replaceChildren();
    rows.forEach(([k, v]) => {
      const row = el("div", "about-row");
      row.append(el("span", "", k), el("strong", "", v));
      holder.append(row);
    });
  }

  let orbitResizeTimer = null;
  window.addEventListener("resize", () => {
    if (state.route !== "/monitor" || !orbitState.hist) return;
    clearTimeout(orbitResizeTimer);
    orbitResizeTimer = setTimeout(() => {
      if (state.route === "/monitor" && orbitState.hist) updateHistory();
    }, 200);
  });

  load();
})();

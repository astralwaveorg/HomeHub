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
      ["/", "/nav", "/admin"].includes(url.pathname) &&
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
            : `正在播放 · ${state.data?.settings?.musicTitle || "背景音乐"}`
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
    panel.append(profileSection, section, backup);
  }
  load();
})();

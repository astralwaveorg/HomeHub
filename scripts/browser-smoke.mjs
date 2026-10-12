#!/usr/bin/env node
/**
 * Optional, public-only Homehub browser acceptance. Node >=22 + installed Chrome.
 * No npm dependencies, login, password reads, auth cookies, or server mutations.
 * Not part of the default CI/check command: Chrome availability is optional.
 *
 * node scripts/browser-smoke.mjs --url http://127.0.0.1:8090
 * node scripts/browser-smoke.mjs --url https://homehub.stream:8443 \
 *   --chrome "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
 *   --screenshots tests/screenshots/public-smoke
 *
 * Chrome uses ordinary networking/TLS. There are deliberately no host resolver,
 * proxy bypass, certificate bypass, or --no-sandbox switches in this script.
 */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const help = `Usage: node scripts/browser-smoke.mjs --url <site-origin> [options]

  --url <http(s) origin>  Required; example http://127.0.0.1:8090
  --chrome <executable>   Optional; otherwise CHROME_BIN or a standard install
  --screenshots <dir>     Optional; write uniquely named PNG screenshots
  --help                 Show this help

Public-only: never logs in, reads credentials, or changes backend data.
Requires Node >=22 with built-in WebSocket and an existing Chrome installation.
The browser starts with a new temporary profile and normal networking/TLS.
Chrome is stopped and its temporary profile removed on success or failure.
`;

function parseArgs(argv) {
  const result = {};
  for (let i = 0; i < argv.length; i++) {
    const key = argv[i];
    if (key === "--help" || key === "-h") return { help: true };
    if (!["--url", "--chrome", "--screenshots"].includes(key))
      throw new Error(`Unknown option: ${key}\n${help}`);
    const value = argv[++i];
    if (!value || value.startsWith("--"))
      throw new Error(`Missing value for ${key}`);
    result[key.slice(2)] = value;
  }
  if (!result.url) throw new Error(help);
  const url = new URL(result.url);
  if (
    !["http:", "https:"].includes(url.protocol) ||
    url.username ||
    url.password ||
    !["", "/"].includes(url.pathname) ||
    url.search ||
    url.hash
  )
    throw new Error("--url must be an HTTP(S) site origin without credentials or a path.");
  result.origin = url.origin;
  return result;
}

function chromePath(explicit) {
  const candidates = [
    explicit,
    process.env.CHROME_BIN,
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    process.env.PROGRAMFILES &&
      path.join(process.env.PROGRAMFILES, "Google/Chrome/Application/chrome.exe"),
    process.env.LOCALAPPDATA &&
      path.join(process.env.LOCALAPPDATA, "Google/Chrome/Application/chrome.exe"),
  ].filter(Boolean);
  for (const candidate of candidates) {
    try {
      fs.accessSync(candidate, fs.constants.X_OK);
      return candidate;
    } catch {
      if (explicit === candidate || process.env.CHROME_BIN === candidate)
        throw new Error(`Chrome executable is not accessible: ${candidate}`);
    }
  }
  throw new Error("Chrome not found. Pass --chrome <executable> or set CHROME_BIN; no browser is downloaded.");
}

class CDP {
  constructor(socket) {
    this.socket = socket;
    this.next = 0;
    this.pending = new Map();
    this.listeners = new Map();
    socket.addEventListener("message", ({ data }) => {
      const message = JSON.parse(data);
      if (message.id) {
        const request = this.pending.get(message.id);
        if (!request) return;
        this.pending.delete(message.id);
        clearTimeout(request.timer);
        if (message.error) request.reject(new Error(JSON.stringify(message.error)));
        else request.resolve(message.result);
      } else {
        for (const fn of this.listeners.get(message.method) || []) fn(message.params);
      }
    });
    socket.addEventListener("close", () => {
      for (const request of this.pending.values()) {
        clearTimeout(request.timer);
        request.reject(new Error("Chrome debugging connection closed"));
      }
      this.pending.clear();
    });
  }
  on(method, fn) {
    const listeners = this.listeners.get(method) || [];
    listeners.push(fn);
    this.listeners.set(method, listeners);
  }
  send(method, params = {}) {
    return new Promise((resolve, reject) => {
      const id = ++this.next;
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`CDP timed out: ${method}`));
      }, 30000);
      this.pending.set(id, { resolve, reject, timer });
      this.socket.send(JSON.stringify({ id, method, params }));
    });
  }
  async evaluate(expression) {
    const result = await this.send("Runtime.evaluate", {
      expression,
      returnByValue: true,
      awaitPromise: true,
      userGesture: true,
    });
    if (result.exceptionDetails)
      throw new Error(`Browser evaluation failed: ${JSON.stringify(result.exceptionDetails)}`);
    return result.result.value;
  }
  async wait(expression) {
    const ready = await this.evaluate(`new Promise(resolve => {
      const start = Date.now();
      const check = () => {
        if (${expression}) resolve(true);
        else if (Date.now() - start > 12000) resolve(false);
        else setTimeout(check, 30);
      };
      check();
    })`);
    assert.ok(ready, `Timed out waiting for UI condition: ${expression}`);
  }
  close() {
    this.socket.close();
  }
}

async function connect(url) {
  const socket = new WebSocket(url);
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("Chrome WebSocket connection timed out")), 10000);
    socket.addEventListener("open", () => { clearTimeout(timer); resolve(); }, { once: true });
    socket.addEventListener("error", () => { clearTimeout(timer); reject(new Error("Chrome WebSocket connection failed")); }, { once: true });
  });
  return new CDP(socket);
}

async function startChrome(executable, profile, onChild) {
  const child = spawn(executable, [
    "--headless=new",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-sync",
    "--mute-audio",
    "--window-size=1440,1000",
    "--remote-debugging-port=0",
    `--user-data-dir=${profile}`,
    "about:blank",
  ], { stdio: ["ignore", "ignore", "pipe"] });
  onChild(child);
  let stderr = "";
  const endpoint = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      child.kill("SIGTERM");
      reject(new Error(`Chrome startup timed out. ${stderr.slice(-2000)}`));
    }, 20000);
    child.once("error", error => { clearTimeout(timer); reject(error); });
    child.once("exit", code => {
      clearTimeout(timer);
      reject(new Error(`Chrome exited before startup (${code}). ${stderr.slice(-2000)}`));
    });
    child.stderr.on("data", chunk => {
      stderr = (stderr + chunk.toString()).slice(-8000);
      const match = stderr.match(/DevTools listening on (ws:\/\/127\.0\.0\.1:\d+\/[^\s]+)/);
      if (match) { clearTimeout(timer); resolve(match[1]); }
    });
  });
  return { child, endpoint };
}

async function stopChrome(child) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  await new Promise(resolve => {
    const timer = setTimeout(() => { child.kill("SIGKILL"); resolve(); }, 3000);
    child.once("exit", () => { clearTimeout(timer); resolve(); });
    child.kill("SIGTERM");
  });
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) { console.log(help); return; }
  assert.ok(Number(process.versions.node.split(".")[0]) >= 22 && typeof WebSocket === "function", "Node >=22 with built-in WebSocket is required.");
  const executable = chromePath(args.chrome);
  const tempRoot = fs.realpathSync(os.tmpdir());
  const profile = fs.mkdtempSync(path.join(tempRoot, "homehub-browser-smoke-"));
  const screenshots = args.screenshots ? path.resolve(args.screenshots) : null;
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  if (screenshots) fs.mkdirSync(screenshots, { recursive: true });
  const report = { url: args.origin, networking: "default (no resolver/proxy/TLS overrides)", checks: [], screenshots: [], mobile: [], networkFailures: [], consoleErrors: [], runtimeErrors: [] };
  let child, browser, page;
  const shutdown = () => child?.kill("SIGTERM");
  process.once("SIGINT", shutdown);
  process.once("SIGTERM", shutdown);
  try {
    const started = await startChrome(executable, profile, owned => { child = owned; });
    child = started.child;
    browser = await connect(started.endpoint);
    const target = await browser.send("Target.createTarget", { url: "about:blank" });
    const endpoint = new URL(started.endpoint);
    const targets = await (await fetch(`http://${endpoint.host}/json/list`, { signal: AbortSignal.timeout(10000) })).json();
    const targetInfo = targets.find(item => item.id === target.targetId);
    assert.ok(targetInfo?.webSocketDebuggerUrl, "Chrome page debugging target is available");
    page = await connect(targetInfo.webSocketDebuggerUrl);
    const requests = new Map(), responses = [];
    page.on("Network.requestWillBeSent", params => requests.set(params.requestId, { url: params.request.url, method: params.request.method }));
    page.on("Network.responseReceived", params => responses.push({ url: params.response.url, status: params.response.status, requestId: params.requestId }));
    page.on("Network.loadingFailed", params => {
      const request = requests.get(params.requestId) || {};
      if (params.type === "Media" && params.errorText === "net::ERR_ABORTED") return; // Deliberate document navigation cancels range requests.
      report.networkFailures.push({ ...request, type: params.type, error: params.errorText, blocked: params.blockedReason });
    });
    page.on("Runtime.exceptionThrown", params => report.runtimeErrors.push(params.exceptionDetails.text));
    page.on("Log.entryAdded", params => {
      if (params.entry.level === "error" || /Content Security Policy|CSP/i.test(params.entry.text)) report.consoleErrors.push(params.entry.text);
    });
    await page.send("Page.enable");
    await page.send("Runtime.enable");
    await page.send("Log.enable");
    await page.send("Network.enable");
    await page.send("Network.setCacheDisabled", { cacheDisabled: true });
    await browser.send("Browser.setDownloadBehavior", { behavior: "deny" });
    const evaluate = expression => page.evaluate(expression);
    const click = selector => evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`);
    const input = (selector, value) => evaluate(`(() => { const e=document.querySelector(${JSON.stringify(selector)});e.value=${JSON.stringify(value)};e.dispatchEvent(new Event('input',{bubbles:true})); })()`);
    const count = () => evaluate("document.querySelectorAll('#nav-results .service-card').length");
    const device = (width, height = 844, mobile = true) => page.send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile });
    async function navigate(route, ready) {
      const result = await page.send("Page.navigate", { url: args.origin + route });
      assert.equal(result.errorText, undefined, `Browser navigation ${route}: ${result.errorText}`);
      await page.wait(ready);
    }
    async function screenshot(name) {
      if (!screenshots) return;
      const file = path.join(screenshots, `${name}-${stamp}.png`);
      const result = await page.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
      fs.writeFileSync(file, Buffer.from(result.data, "base64"), { flag: "wx" });
      report.screenshots.push(file);
    }
    async function navReset() {
      await click('[data-route="/nav"]');
      assert.equal(await inputValue(), "", "The /nav link resets search");
    }
    const inputValue = () => evaluate("document.querySelector('#service-search').value");
    await device(1440, 1000, false);
    await navigate("/", "!!document.querySelector('#service-search')");
    const data = await evaluate("fetch('/api/data').then(async r=>{if(!r.ok)throw new Error('public API HTTP '+r.status);return r.json()})");
    assert.equal(data.authenticated, false, "The test remains unauthenticated");
    assert.equal(data.csrf, null, "Public API contains no authenticated CSRF token");
    assert.ok(Array.isArray(data.links) && data.links.length > 0, "Public catalog is nonempty");
    assert.ok(data.links.every(link => link.visibility === "public"), "Public API includes only public links");
    if (data.catalog) {
      assert.equal(data.catalog.visible, data.links.length);
      assert.equal(data.catalog.total, data.catalog.visible + data.catalog.hidden);
    }
    report.catalog = data.catalog || { visible: data.links.length };
    report.catalogNames = data.links.map(link => link.name);
    report.heading = await evaluate("document.querySelector('#main h1').textContent");
    assert.equal(report.heading, data.settings.tagline || "天空沒有極限", "Portal h1 exactly matches the configured heading");
    assert.equal(await evaluate("document.querySelector('.brand').getAttribute('href')"), "/");
    assert.ok(await evaluate("document.querySelector('[data-route=\"/\"]').textContent.includes('门户') && document.querySelector('[data-route=\"/nav\"]').textContent.includes('全部服务')"));
    report.checks.push("public API/catalog accounting, exact heading, root logo and explicit navigation");
    const assets = await evaluate(`(async()=>{
      const nodes=[document.querySelector('script[src]'),document.querySelector('link[rel=stylesheet]')];
      return Promise.all(nodes.map(async node=>{const url=node.src||node.href;const r=await fetch(url);return {url,status:r.status,text:await r.text()}}));
    })()`);
    const fingerprint = createHash("sha256").update(assets[0].text).update(assets[1].text).digest("hex");
    report.assets = assets.map(asset => {
      assert.equal(asset.status, 200);
      const version = new URL(asset.url).searchParams.get("v");
      assert.match(version || "", /^[a-f\d]{8,64}$/i, "Asset URL contains a real content fingerprint, not an unexpanded placeholder");
      assert.equal(version, fingerprint.slice(0, version.length), "Asset query fingerprint matches app.js + styles.css content");
      return { url: asset.url, status: asset.status, fingerprint: version };
    });
    report.checks.push("JS/CSS network status and content-derived fingerprint");
    const audioInitial = await evaluate("({paused:document.querySelector('#audio').paused,muted:document.querySelector('#audio').muted,source:document.querySelector('#audio').getAttribute('src')})");
    assert.ok(audioInitial.paused && audioInitial.muted, "Audio initially paused and muted");
    await screenshot("portal-desktop");
    await navReset();
    assert.equal(await count(), data.links.length, "All public API links are rendered in navigation");
    const domNames = await evaluate("[...document.querySelectorAll('#nav-results .service-name')].map(e=>e.textContent).sort()");
    assert.deepEqual(domNames, data.links.map(link => link.name).sort(), "Catalog names are neither missing nor duplicated");
    assert.ok(await evaluate("[...document.querySelectorAll('.service-link')].every(a=>a.target==='_blank'&&a.rel.split(/\\s+/).includes('noopener'))"));
    const categoryName = id => data.categories.find(category => String(category.id) === String(id))?.name || "未分类";
    for (const link of data.links) {
      const query = link.name.toLocaleLowerCase();
      const expected = data.links.filter(item => [item.name, item.description, item.aliases, categoryName(item.category_id)].join(" ").toLocaleLowerCase().includes(query)).map(item => item.name).sort();
      await input("#service-search", link.name);
      const found = await evaluate("[...document.querySelectorAll('#nav-results .service-name')].map(e=>e.textContent).sort()");
      assert.deepEqual(found, expected, `Search coverage: ${link.name}`);
    }
    await input("#service-search", "__HOMEHUB_NO_SUCH_SERVICE_7391__");
    assert.equal(await count(), 0);
    assert.ok(await evaluate("!!document.querySelector('#nav-results .empty-state')"));
    await navReset();
    assert.equal(await count(), data.links.length);
    report.checks.push(`all ${data.links.length} catalog-name searches, empty state and /nav search reset`);
    for (const category of data.categories) {
      await evaluate(`document.querySelector('[data-category="'+CSS.escape(${JSON.stringify(String(category.id))})+'"]').click()`);
      assert.equal(await count(), data.links.filter(link => String(link.category_id) === String(category.id)).length, `Category filter: ${category.name}`);
    }
    await click("#clear-filters");
    assert.equal(await count(), data.links.length);
    await click(".favorite-button");
    await click("#favorites-filter");
    assert.equal(await count(), 1);
    await navReset();
    assert.equal(await count(), data.links.length);
    await click('[aria-label="列表视图"]');
    await navigate("/nav", "!!document.querySelector('#nav-results')");
    assert.ok(await evaluate("document.querySelector('#nav-results').classList.contains('list')"), "Density is remembered locally");
    await click('[aria-label="紧凑网格视图"]');
    await page.send("Input.dispatchKeyEvent", { type: "keyDown", key: "/", code: "Slash", windowsVirtualKeyCode: 191 });
    await page.send("Input.dispatchKeyEvent", { type: "keyUp", key: "/", code: "Slash", windowsVirtualKeyCode: 191 });
    assert.equal(await evaluate("document.activeElement.id"), "service-search");
    report.checks.push("categories, favorites, clear filters, remembered grid/list density and keyboard search");
    const fnos = data.links.filter(link => link.launch_mode === "fnos");
    for (const link of fnos) {
      const desktopRoot = new URL(link.url).origin + "/";
      const result = await evaluate(`(() => {
        const a=[...document.querySelectorAll('.service-link')].find(a=>a.querySelector('.service-name').textContent===${JSON.stringify(link.name)});
        const href=a.href;a.focus();a.click();const desktop=document.querySelector('#dialog-body a');
        return {href,open:document.querySelector('#editor').open,instructions:document.querySelector('#dialog-body').textContent,desktop:desktop?.href,target:desktop?.target,rel:desktop?.rel};
      })()`);
      assert.equal(result.href, desktopRoot, "fnOS card href never exposes a token-protected gateway");
      assert.ok(result.open && result.instructions.includes("票据") && result.instructions.includes("应用中心"));
      assert.equal(result.desktop, desktopRoot);
      assert.equal(result.target, "_blank");
      assert.ok(result.rel.split(/\s+/).includes("noopener"));
      await click("#dialog-close");
      assert.equal(await evaluate("document.activeElement.querySelector('.service-name')?.textContent"), link.name, "Closing fnOS instructions restores focus to its card");
    }
    report.fnosLaunchers = fnos.length;
    report.checks.push("fnOS launch interception, instructions, safe desktop-root links and modal focus restoration");
    await screenshot("navigation-desktop");
    for (const width of [390, 360]) {
      await device(width);
      await evaluate("scrollTo(0,0)");
      const geometry = await evaluate(`(() => {
        const music=document.querySelector('.music-player').getBoundingClientRect();
        const cards=[...document.querySelectorAll('#nav-results .service-card')];
        return {width:innerWidth,height:innerHeight,cards:cards.length,visibleAbovePlayer:cards.filter(e=>{const r=e.getBoundingClientRect();return r.top>=0&&r.bottom<=Math.min(music.top,innerHeight)}).length,columns:getComputedStyle(document.querySelector('#nav-results')).gridTemplateColumns.trim().split(/\\s+/).length,horizontalOverflow:document.documentElement.scrollWidth>innerWidth,firstCardTop:cards[0]?.getBoundingClientRect().top,playerTop:music.top};
      })()`);
      report.mobile.push(geometry);
      assert.equal(geometry.columns, 3, `Mobile ${width}: compact three-column grid`);
      assert.equal(geometry.horizontalOverflow, false, `Mobile ${width}: no horizontal overflow`);
      assert.ok(geometry.visibleAbovePlayer >= Math.min(12, data.links.length), `Mobile ${width}: fewer than 12 complete cards above the player: ${JSON.stringify(geometry)}`);
      assert.ok(await evaluate("document.querySelector('.brand').getAttribute('href')==='/' && !!document.querySelector('[data-route=\"/nav\"]')"));
      await screenshot(`navigation-mobile-${width}`);
    }
    report.checks.push("390x844 and 360x844: three columns, >=12 complete cards above player, no overflow");
    if (audioInitial.source) {
      await click("#music-play");
      await page.wait("!document.querySelector('#audio').paused && document.querySelector('#audio').currentTime>0.05");
      const before = await evaluate("document.querySelector('#audio').currentTime");
      await click(".brand");
      assert.equal(await evaluate("document.querySelector('#audio').paused"), false);
      await click('[data-route="/nav"]');
      assert.equal(await evaluate("document.querySelector('#audio').paused"), false);
      assert.ok(await evaluate("document.querySelector('#audio').currentTime") >= before);
      await click("#music-play");
      assert.equal(await evaluate("document.querySelector('#audio').paused"), true);
      report.music = "PASS: user-initiated decoded playback, root/nav SPA continuity, pause";
    } else report.music = "SKIP: no configured source; default paused/muted checked";
    await page.send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "reduce" }] });
    assert.equal(await evaluate("getComputedStyle(document.querySelector('.aurora')).animationName"), "none");
    report.checks.push("reduced-motion disables aurora animation");
    await navigate("/admin", "!!document.querySelector('.login-panel')");
    assert.equal(await evaluate("!!document.querySelector('.admin-panel')"), false);
    assert.equal(await evaluate("document.documentElement.scrollWidth>innerWidth"), false);
    const authAfter = await evaluate("fetch('/api/data').then(r=>r.json()).then(d=>({authenticated:d.authenticated,csrf:d.csrf}))");
    assert.deepEqual(authAfter, { authenticated: false, csrf: null });
    assert.ok([...requests.values()].every(request => request.method === "GET"), "Public smoke performs GET requests only");
    const gatewayRequests = [...requests.values()].filter(request => fnos.some(link => request.url === link.url));
    assert.equal(gatewayRequests.length, 0, "No token-protected fnOS gateway request was made");
    assert.ok(responses.some(response => response.url === args.origin + "/nav" && response.status === 200), "Direct /nav HTTP 200");
    assert.ok(responses.some(response => response.url === args.origin + "/admin" && response.status === 200), "Direct /admin HTTP 200");
    report.checks.push("direct /nav and /admin, login-only public manager, no auth and GET-only network");
    assert.deepEqual(report.networkFailures, [], "No unexpected browser network failures");
    assert.deepEqual(report.runtimeErrors, [], "No browser runtime exceptions");
    assert.deepEqual(report.consoleErrors, [], "No browser console/CSP errors");
    report.result = "PASS";
    console.log(JSON.stringify(report, null, 2));
  } catch (error) {
    report.result = "FAIL";
    report.error = error.message;
    console.error(JSON.stringify(report, null, 2));
    process.exitCode = 1;
  } finally {
    page?.close();
    browser?.close();
    await stopChrome(child);
    process.removeListener("SIGINT", shutdown);
    process.removeListener("SIGTERM", shutdown);
    // Only remove the verified, non-symlink temporary profile created above.
    if (fs.existsSync(profile)) {
      const resolved = fs.realpathSync(profile);
      if (path.dirname(resolved) !== tempRoot || !path.basename(resolved).startsWith("homehub-browser-smoke-") || fs.lstatSync(profile).isSymbolicLink())
        throw new Error("Refusing to remove an unexpected temporary profile path");
      fs.rmSync(resolved, { recursive: true, force: true });
    }
  }
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });

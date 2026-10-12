#!/usr/bin/env node
/**
 * OPTIONAL ISOLATED MUSIC QA. Node >=22, Python 3, and installed Chrome only.
 * This command accepts NO site URL: all authentication/uploads/settings changes
 * target its own loopback Python server and disposable HOMEHUB_DATA directory.
 * A generated fixture-only administrator password is never printed or saved
 * outside that disposable directory. No NAS/production credentials are read.
 *
 * node scripts/music-smoke.mjs
 * node scripts/music-smoke.mjs --chrome "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
 * node scripts/music-smoke.mjs --python python3 --screenshots tests/screenshots/music-v1.1.1
 *
 * One clearly labeled controlled Chrome profile uses
 * --autoplay-policy=no-user-gesture-required to prove successful automatic
 * decoding. Normal-profile tests do NOT override browser autoplay policy.
 * There are NO proxy/resolver/certificate overrides and no production mutations.
 */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { connect, chromePath, startChrome, stopChrome } from "./browser-smoke.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const help = `Usage: node scripts/music-smoke.mjs [--chrome <executable>] [--python <executable>] [--screenshots <dir>]

Optional isolated music acceptance; requires Node >=22, Python 3, Chrome.
No --url option exists. The command starts a new loopback-only fixture server,
generates a temporary administrator, and removes its own data/profiles on exit.
Normal-browser blocked autoplay and a separately labeled controlled autoplay
profile are tested. Screenshots, when requested, show PUBLIC pages only.
Never run against NAS data or provide production credentials.
`;

function argsOf(argv) {
  const args = { python: process.env.PYTHON || "python3" };
  for (let i = 0; i < argv.length; i++) {
    if (["--help", "-h"].includes(argv[i])) return { help: true };
    if (!["--chrome", "--python", "--screenshots"].includes(argv[i]))
      throw new Error(`Unknown option ${argv[i]}. This fixture cannot target a remote site.\n${help}`);
    const key = argv[i].slice(2), value = argv[++i];
    if (!value || value.startsWith("--")) throw new Error(`Missing ${key} value`);
    args[key] = value;
  }
  return args;
}

async function freePort() {
  const server = net.createServer();
  await new Promise((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolve); });
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}

function sineWav(seconds = 2, frequency = 330) {
  const rate = 22050, samples = Math.floor(rate * seconds), bytes = samples * 2;
  const wav = Buffer.alloc(44 + bytes);
  wav.write("RIFF", 0); wav.writeUInt32LE(36 + bytes, 4); wav.write("WAVE", 8);
  wav.write("fmt ", 12); wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20);
  wav.writeUInt16LE(1, 22); wav.writeUInt32LE(rate, 24); wav.writeUInt32LE(rate * 2, 28);
  wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34); wav.write("data", 36); wav.writeUInt32LE(bytes, 40);
  for (let i = 0; i < samples; i++) wav.writeInt16LE(Math.round(1000 * Math.sin(2 * Math.PI * frequency * i / rate)), 44 + i * 2);
  return wav;
}

async function main() {
  const args = argsOf(process.argv.slice(2));
  if (args.help) { console.log(help); return; }
  assert.ok(Number(process.versions.node.split(".")[0]) >= 22 && typeof WebSocket === "function", "Node >=22 with built-in WebSocket required");
  const executable = chromePath(args.chrome);
  const tempRoot = fs.realpathSync(os.tmpdir());
  const fixture = fs.mkdtempSync(path.join(tempRoot, "homehub-music-smoke-"));
  const dataDir = path.join(fixture, "data");
  fs.mkdirSync(dataDir, { mode: 0o700 });
  const password = randomBytes(24).toString("base64url");
  fs.writeFileSync(path.join(dataDir, "initial-admin-password"), password + "\n", { mode: 0o600, flag: "wx" });
  const firstFile = path.join(fixture, "ambient.wav"), secondFile = path.join(fixture, "第二首·测试.wav");
  fs.copyFileSync(path.join(root, "ambient.wav"), firstFile);
  fs.writeFileSync(secondFile, sineWav());
  const screenshots = args.screenshots ? path.resolve(args.screenshots) : null;
  if (screenshots) fs.mkdirSync(screenshots, { recursive: true });
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const report = { scope: "DISPOSABLE LOOPBACK FIXTURE ONLY; no NAS or production access", checks: [], screenshots: [] };
  const browsers = [];
  let server, publicChild, cookie = "", csrf = "", origin = "", serverLog = "";
  const shutdown = () => { publicChild?.kill("SIGTERM"); server?.kill("SIGTERM"); for (const item of browsers) item.child?.kill("SIGTERM"); };
  process.once("SIGINT", shutdown); process.once("SIGTERM", shutdown);
  const redact = message => [password, cookie, csrf].filter(Boolean).reduce((text, secret) => text.split(secret).join("[fixture secret redacted]"), String(message));
  try {
    const port = await freePort();
    origin = `http://127.0.0.1:${port}`;
    assert.equal(new URL(origin).hostname, "127.0.0.1");
    server = spawn(args.python, [path.join(root, "app.py"), "serve"], {
      cwd: root,
      env: { ...process.env, HOMEHUB_DATA: dataDir, HOMEHUB_BIND: "127.0.0.1", HOMEHUB_PORT: String(port), HOMEHUB_ORIGIN: origin, HOMEHUB_COOKIE_SECURE: "false", PYTHONUNBUFFERED: "1" },
      stdio: ["ignore", "pipe", "pipe"],
    });
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("Isolated Python server startup timeout")), 20000);
      server.once("error", error => { clearTimeout(timer); reject(error); });
      server.once("exit", code => { clearTimeout(timer); reject(new Error(`Isolated server exited (${code}): ${serverLog}`)); });
      for (const stream of [server.stdout, server.stderr]) stream.on("data", chunk => {
        serverLog = (serverLog + chunk.toString()).slice(-4000);
        if (serverLog.includes("Homehub listening")) { clearTimeout(timer); resolve(); }
      });
    });
    async function request(endpoint, method = "GET", body) {
      assert.equal(new URL(endpoint, origin).origin, origin, "Fixture API must remain loopback-only");
      const headers = {};
      if (cookie) headers.Cookie = cookie;
      if (csrf && method !== "GET") headers["X-CSRF-Token"] = csrf;
      if (body !== undefined) headers["Content-Type"] = "application/json";
      const response = await fetch(origin + endpoint, { method, headers, ...(body !== undefined ? { body: JSON.stringify(body) } : {}), signal: AbortSignal.timeout(15000) });
      const value = await response.json();
      assert.ok(response.ok, `${method} ${endpoint}: ${value.error || response.status}`);
      return { response, value };
    }
    const login = await request("/api/login", "POST", { username: "astral", password });
    cookie = login.response.headers.get("set-cookie").split(";")[0]; csrf = login.value.csrf;
    const initial = (await request("/api/data")).value;
    const preserved = { title: initial.settings.title, tagline: initial.settings.tagline, subtitle: initial.settings.subtitle };
    await request("/api/settings", "PUT", { ...initial.settings, musicAutoplay: true });

    async function launch(name, controlled = false, failMedia = false) {
      const item = { name, child: null, browser: null, page: null, requests: [], runtimeErrors: [], failures: [] };
      browsers.push(item);
      const profile = path.join(fixture, `chrome-${name}`);
      const started = await startChrome(executable, profile, owned => { item.child = owned; }, controlled ? ["--autoplay-policy=no-user-gesture-required"] : []);
      item.browser = await connect(started.endpoint);
      const target = await item.browser.send("Target.createTarget", { url: "about:blank" });
      const endpoint = new URL(started.endpoint);
      const targets = await (await fetch(`http://${endpoint.host}/json/list`, { signal: AbortSignal.timeout(10000) })).json();
      item.page = await connect(targets.find(targetInfo => targetInfo.id === target.targetId).webSocketDebuggerUrl);
      item.page.on("Network.requestWillBeSent", params => item.requests.push({ url: params.request.url, method: params.request.method }));
      item.page.on("Runtime.exceptionThrown", params => item.runtimeErrors.push(params.exceptionDetails.text));
      item.page.on("Network.loadingFailed", params => { if (params.errorText !== "net::ERR_ABORTED") item.failures.push({ type: params.type, error: params.errorText }); });
      await item.page.send("Page.enable"); await item.page.send("Runtime.enable"); await item.page.send("Network.enable");
      await item.page.send("Network.setCacheDisabled", { cacheDisabled: true });
      await item.page.send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
      if (failMedia) {
        item.page.on("Fetch.requestPaused", params => { item.page.send("Fetch.failRequest", { requestId: params.requestId, errorReason: "Failed" }).catch(() => {}); });
        await item.page.send("Fetch.enable", { patterns: [{ urlPattern: origin + "/media/*", requestStage: "Request" }] });
      }
      item.evaluate = expression => item.page.evaluate(expression); // Gesture-free reads.
      item.click = selector => item.page.evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`, true);
      item.textClick = text => item.page.evaluate(`(()=>{const e=[...document.querySelectorAll('button')].find(e=>e.textContent.trim()===${JSON.stringify(text)});if(!e)throw new Error('Missing button');e.click()})()`, true);
      item.input = (selector, value) => item.page.evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});e.value=${JSON.stringify(value)};e.dispatchEvent(new Event('input',{bubbles:true}))})()`);
      item.navigate = async (route = "/", condition = "!!document.querySelector('#service-search')") => {
        const navigation = await item.page.send("Page.navigate", { url: origin + route });
        assert.equal(navigation.errorText, undefined, `Fixture navigation ${route}`);
        await item.page.wait(condition);
      };
      item.audio = () => item.evaluate("({paused:document.querySelector('#audio').paused,muted:document.querySelector('#audio').muted,volume:document.querySelector('#audio').volume,source:document.querySelector('#audio').getAttribute('src'),time:document.querySelector('#audio').currentTime,duration:Number.isFinite(document.querySelector('#audio').duration)?document.querySelector('#audio').duration:null,blocked:document.querySelector('#music-play').classList.contains('autoplay-blocked'),status:document.querySelector('#music-status').textContent,error:document.querySelector('#audio').error?.code||null})");
      item.close = async () => { item.page?.close(); item.browser?.close(); await stopChrome(item.child); item.child = null; };
      await item.navigate();
      return item;
    }
    async function screenshot(item, name) {
      if (!screenshots) return;
      const file = path.join(screenshots, `${name}-${stamp}.png`);
      const image = await item.page.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
      fs.writeFileSync(file, Buffer.from(image.data, "base64"), { flag: "wx" }); report.screenshots.push(file);
    }
    async function waitPlaying(item) { await item.page.wait("!document.querySelector('#audio').paused && document.querySelector('#audio').currentTime>0.05"); }
    async function pauseAcrossSPA(item) {
      if (!(await item.audio()).paused) await item.click("#music-play");
      await item.click('[data-route="/nav"]'); assert.equal((await item.audio()).paused, true);
      await item.click(".brand"); assert.equal((await item.audio()).paused, true);
    }

    const normal = await launch("normal");
    await normal.page.wait("document.querySelector('#music-play').classList.contains('autoplay-blocked') || !document.querySelector('#audio').paused");
    const blocked = await normal.audio();
    assert.equal(blocked.paused, true, "Fresh normal Chrome must not silently gain a test gesture");
    assert.equal(blocked.muted, false, "Fresh default attempts audible rather than pretending muted autoplay is audible");
    assert.equal(blocked.blocked, true); assert.ok(blocked.status.includes("浏览器限制自动播放"));
    report.normalBrowserInitial = blocked;
    await screenshot(normal, "normal-autoplay-blocked");
    await normal.click("#music-play"); await waitPlaying(normal);
    assert.equal((await normal.audio()).muted, false);
    await pauseAcrossSPA(normal);
    report.checks.push("NORMAL Chrome: truthful blocked-autoplay hint, one explicit click decodes audio, manual pause remains paused across root/nav SPA");
    await normal.click("#music-mute");
    await normal.input("#music-volume", "0.2");
    await normal.navigate();
    await normal.page.wait("document.querySelector('#audio').volume===0.2 && document.querySelector('#audio').muted");
    const preferences = await normal.audio();
    assert.equal(preferences.muted, true); assert.equal(preferences.volume, 0.2);
    assert.deepEqual(await normal.evaluate("({muted:JSON.parse(localStorage.getItem('homehub.muted')),volume:JSON.parse(localStorage.getItem('homehub.volume'))})"), { muted: true, volume: 0.2 });
    report.rememberedPreferences = { muted: preferences.muted, volume: preferences.volume };
    report.checks.push("stored muted/volume preferences survive full reload without forced unmute");
    if (!(await normal.audio()).paused) await normal.click("#music-play");

    const allowed = await launch("controlled-policy", true);
    await waitPlaying(allowed);
    const automatic = await allowed.audio();
    assert.equal(automatic.muted, false); assert.equal(automatic.blocked, false);
    report.controlledAutomaticPlayback = { policy: "TEST ONLY: --autoplay-policy=no-user-gesture-required", playing: !automatic.paused, muted: automatic.muted, advancingTime: automatic.time > 0.05 };
    await screenshot(allowed, "controlled-autostart-test-only");
    await pauseAcrossSPA(allowed); await allowed.close();
    report.checks.push("CONTROLLED TEST-ONLY policy: audible automatic decoding succeeds; manual pause still survives SPA");

    await normal.navigate("/admin", "!!document.querySelector('.login-panel')");
    await normal.input('input[name="password"]', password);
    await normal.click('.login-panel button[type="submit"]');
    await normal.page.wait("!!document.querySelector('.admin-panel')");
    await normal.textClick("站点设置");
    assert.equal(await normal.evaluate("document.querySelector('input[name=musicAutoplay]').checked"), true);
    await normal.click('input[name="musicAutoplay"]'); await normal.textClick("保存站点设置");
    await normal.page.wait("document.querySelector('#toast').textContent.includes('站点设置已保存') && !document.querySelector('input[name=musicAutoplay]').checked");
    assert.equal((await request("/api/data")).value.settings.musicAutoplay, false);
    const disabled = await launch("site-autoplay-disabled");
    const disabledState = await disabled.audio();
    assert.equal(disabledState.paused, true); assert.equal(disabledState.blocked, false);
    assert.ok(disabledState.status.includes("已暂停"));
    report.siteAutoplayDisabled = { paused: disabledState.paused, blocked: disabledState.blocked };
    await disabled.close();
    await normal.click('input[name="musicAutoplay"]'); await normal.textClick("保存站点设置");
    await normal.page.wait("document.querySelector('input[name=musicAutoplay]').checked && document.querySelector('#toast').textContent.includes('站点设置已保存') && !document.querySelector('.admin-panel form button[type=submit]').disabled");
    assert.equal((await request("/api/data")).value.settings.musicAutoplay, true);
    report.checks.push("real isolated admin checkbox saves false/true; a fresh normal visitor remains paused when site autoplay=false");
    if (!(await normal.audio()).paused) await normal.click("#music-play");

    async function upload(file) {
      const bytes = fs.readFileSync(file), hash = createHash("sha256").update(bytes).digest("hex").slice(0, 16);
      const expectedUrl = "/media/music.wav?v=" + hash, expectedTitle = path.basename(file, ".wav");
      const requestStart = normal.requests.length;
      const document = await normal.page.send("DOM.getDocument");
      const node = await normal.page.send("DOM.querySelector", { nodeId: document.root.nodeId, selector: 'input[name="music"]' });
      await normal.page.send("DOM.setFileInputFiles", { nodeId: node.nodeId, files: [file] });
      await normal.textClick("上传并设为默认背景音乐");
      await normal.page.wait(`document.querySelector('#music-title').textContent===${JSON.stringify(expectedTitle)} && document.querySelector('#audio').getAttribute('src')===${JSON.stringify(origin + expectedUrl)}`);
      const settings = (await request("/api/data")).value.settings;
      assert.equal(settings.musicUrl, expectedUrl); assert.equal(settings.musicTitle, expectedTitle);
      assert.equal(settings.musicAutoplay, true);
      assert.deepEqual({ title: settings.title, tagline: settings.tagline, subtitle: settings.subtitle }, preserved);
      const mutations = normal.requests.slice(requestStart).filter(item => item.method !== "GET").map(item => ({ method: item.method, path: new URL(item.url).pathname }));
      assert.deepEqual(mutations, [{ method: "POST", path: "/api/music" }], "Upload uses the atomic backend defaults update, not a stale follow-up settings PUT");
      const response = await fetch(origin + settings.musicUrl, { signal: AbortSignal.timeout(10000) });
      assert.equal(response.status, 200);
      assert.equal(createHash("sha256").update(Buffer.from(await response.arrayBuffer())).digest("hex").slice(0, 16), hash);
      return { url: settings.musicUrl, title: settings.musicTitle, autoplayPreserved: settings.musicAutoplay, headingsPreserved: true };
    }
    const first = await upload(firstFile);
    const newFirst = await launch("fresh-first-upload", true); await waitPlaying(newFirst);
    assert.equal((await newFirst.audio()).source, origin + first.url);
    assert.equal(await newFirst.evaluate("document.querySelector('#music-title').textContent"), first.title);
    await newFirst.close();
    const second = await upload(secondFile);
    assert.notEqual(first.url, second.url, "Different valid WAV bytes produce a new cache-busting URL even with the same extension");
    assert.equal((await normal.audio()).source, origin + second.url);
    if ((await normal.audio()).paused) await normal.click("#music-play");
    await waitPlaying(normal);
    assert.ok(Math.abs((await normal.audio()).duration - 2) < 0.1, "Current page decodes the replacement two-second WAV rather than stale cached ambient audio");
    await normal.click("#music-play");
    const newSecond = await launch("fresh-second-upload", true); await waitPlaying(newSecond);
    const secondState = await newSecond.audio();
    assert.equal(secondState.source, origin + second.url); assert.ok(Math.abs(secondState.duration - 2) < 0.1);
    assert.equal(await newSecond.evaluate("document.querySelector('#music-title').textContent"), second.title);
    await screenshot(newSecond, "uploaded-default-public"); await newSecond.close();
    report.uploads = [first, second];
    report.checks.push("real isolated admin UI uploads ambient.wav and a different valid WAV; atomic default URL?v=16hex/title, bytes/hash, preserved autoplay/headings, current/new opens decode replacement audio");

    const failed = await launch("media-network-failure", false, true);
    await failed.click("#music-play");
    await failed.page.wait("!!document.querySelector('#audio').error && document.querySelector('#music-status').textContent.includes('音源无法播放')");
    const honest = await failed.audio();
    assert.equal(honest.paused, true); assert.ok(honest.error); assert.ok(!honest.status.includes("正在播放"));
    report.expectedNetworkFailure = { injectedByTest: true, paused: honest.paused, mediaError: honest.error, status: honest.status };
    await screenshot(failed, "honest-media-network-error"); await failed.close();
    report.checks.push("intentionally intercepted media network failure displays honest source error, never fake playing status");
    for (const item of browsers) {
      assert.deepEqual(item.runtimeErrors, [], `No JavaScript runtime exceptions: ${item.name}`);
      if (item.name !== "media-network-failure") assert.deepEqual(item.failures, [], `No unexpected network failures: ${item.name}`);
    }
    const publicResult = await new Promise((resolve, reject) => {
      let stdout = "", stderr = "";
      publicChild = spawn(process.execPath, [path.join(root, "scripts/browser-smoke.mjs"), "--url", origin, "--chrome", executable], { stdio: ["ignore", "pipe", "pipe"] });
      publicChild.stdout.on("data", chunk => { stdout += chunk.toString(); });
      publicChild.stderr.on("data", chunk => { stderr += chunk.toString(); });
      publicChild.once("error", reject);
      publicChild.once("exit", code => {
        if (code !== 0) reject(new Error(`Read-only public acceptance failed: ${stderr || stdout}`));
        else { try { resolve(JSON.parse(stdout)); } catch { reject(new Error("Public acceptance did not return JSON")); } }
      });
    });
    assert.equal(publicResult.result, "PASS");
    report.publicReadOnlyAcceptance = { result: publicResult.result, catalog: publicResult.catalog, heading: publicResult.heading, audioInitial: publicResult.audioInitial, mobile: publicResult.mobile };
    report.checks.push("unchanged public smoke CLI also passes against this isolated autoplay-enabled/uploaded fixture, with normal Chrome policy and gesture-free initial reads");
    report.result = "PASS";
    console.log(JSON.stringify(report, null, 2));
  } catch (error) {
    report.result = "FAIL"; report.error = redact(error.message);
    console.error(JSON.stringify(report, null, 2)); process.exitCode = 1;
  } finally {
    for (const item of browsers) {
      item.page?.close(); item.browser?.close(); await stopChrome(item.child);
    }
    await stopChrome(publicChild);
    await stopChrome(server);
    process.removeListener("SIGINT", shutdown); process.removeListener("SIGTERM", shutdown);
    // Delete only this verified fixture tree, never supplied paths or NAS data.
    if (fs.existsSync(fixture)) {
      const resolved = fs.realpathSync(fixture);
      if (path.dirname(resolved) !== tempRoot || !path.basename(resolved).startsWith("homehub-music-smoke-") || fs.lstatSync(fixture).isSymbolicLink())
        throw new Error("Refusing to remove unexpected fixture directory");
      fs.rmSync(resolved, { recursive: true, force: true });
    }
  }
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });

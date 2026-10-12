#!/usr/bin/env node
/** Optional real-browser icon acceptance. No --url option, NAS access or real credentials.
 * Self-managed loopback Python fixture; remote fetch transport is mocked ONLY for
 * https://icons.example.test/fixture.png. Real upload/storage/edit/render paths run.
 * node scripts/icon-smoke.mjs [--python python3] [--chrome /path/to/chrome]
 *                            [--screenshots tests/screenshots/icons-local]
 */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { deflateSync } from "node:zlib";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  chromePath,
  startChrome,
  stopChrome,
  connect,
} from "./browser-smoke.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const HELP = `Usage: node scripts/icon-smoke.mjs [--python python3] [--chrome <path>] [--screenshots <dir>]
Requires Node >=22, Python and Chrome. Creates ONLY a disposable loopback fixture.
No remote target or production credentials are accepted. Screenshots show PUBLIC pages only.
Remote URL UI uses an explicit fixture-only mocked fetch; it does not prove provider reachability.
`;
function argsOf(argv) {
  const args = { python: process.env.PYTHON || "python3" };
  for (let i = 0; i < argv.length; i++) {
    if (["--help", "-h"].includes(argv[i])) return { help: true };
    if (!["--python", "--chrome", "--screenshots"].includes(argv[i]))
      throw new Error(`Unknown option: ${argv[i]}\n${HELP}`);
    const key = argv[i].slice(2),
      value = argv[++i];
    if (!value || value.startsWith("--")) throw new Error(`Missing ${key}`);
    args[key] = value;
  }
  return args;
}
async function freePort() {
  const server = net.createServer();
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const port = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return port;
}
function crc32(bytes) {
  let crc = 0xffffffff;
  for (const b of bytes) {
    crc ^= b;
    for (let i = 0; i < 8; i++) crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
  }
  return (crc ^ 0xffffffff) >>> 0;
}
function png(color) {
  const chunk = (type, data) => {
    const body = Buffer.concat([Buffer.from(type), data]);
    const len = Buffer.alloc(4),
      crc = Buffer.alloc(4);
    len.writeUInt32BE(data.length);
    crc.writeUInt32BE(crc32(body));
    return Buffer.concat([len, body, crc]);
  };
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(64, 0);
  ihdr.writeUInt32BE(64, 4);
  ihdr[8] = 8;
  ihdr[9] = 6;
  const rows = Buffer.alloc(64 * (1 + 64 * 4));
  for (let y = 0; y < 64; y++)
    for (let x = 0; x < 64; x++)
      for (let c = 0; c < 4; c++) rows[y * 257 + 1 + x * 4 + c] = color[c];
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk("IHDR", ihdr),
    chunk("IDAT", deflateSync(rows)),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}
async function stop(child) {
  if (!child || child.exitCode !== null || child.signalCode) return;
  child.kill("SIGTERM");
  await new Promise((resolve) => {
    const t = setTimeout(() => {
      child.kill("SIGKILL");
      resolve();
    }, 3000);
    child.once("exit", () => {
      clearTimeout(t);
      resolve();
    });
  });
}
async function main() {
  const args = argsOf(process.argv.slice(2));
  if (args.help) {
    console.log(HELP);
    return;
  }
  assert.ok(
    Number(process.versions.node.split(".")[0]) >= 22 &&
      typeof WebSocket === "function",
    "Node >=22 required",
  );
  const tempRoot = fs.realpathSync(os.tmpdir());
  const fixture = fs.mkdtempSync(path.join(tempRoot, "homehub-icon-smoke-"));
  const dataDir = path.join(fixture, "data");
  fs.mkdirSync(dataDir, { mode: 0o700 });
  const password = randomBytes(24).toString("base64url");
  fs.writeFileSync(
    path.join(dataDir, "initial-admin-password"),
    password + "\n",
    { mode: 0o600, flag: "wx" },
  );
  const uploadFile = path.join(fixture, "独立图标.png"),
    remoteFile = path.join(fixture, "remote.png"),
    badFile = path.join(fixture, "unsafe.svg");
  fs.writeFileSync(uploadFile, png([55, 130, 210, 255]));
  fs.writeFileSync(remoteFile, png([60, 180, 130, 255]));
  fs.writeFileSync(
    badFile,
    '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><script>alert("unsafe")</script><rect width="64" height="64"/></svg>',
  );
  const screenshots = args.screenshots ? path.resolve(args.screenshots) : null;
  if (screenshots) fs.mkdirSync(screenshots, { recursive: true });
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const report = {
    scope: "DISPOSABLE LOOPBACK FIXTURE ONLY; remote provider transport mocked",
    checks: [],
    screenshots: [],
    mobile: [],
  };
  let server,
    child,
    publicChild,
    browser,
    page,
    cookie = "",
    csrf = "",
    log = "";
  const redact = (text) =>
    [password, cookie, csrf]
      .filter(Boolean)
      .reduce(
        (s, x) => s.split(x).join("[fixture secret redacted]"),
        String(text),
      );
  const shutdown = () => {
    server?.kill("SIGTERM");
    child?.kill("SIGTERM");
    publicChild?.kill("SIGTERM");
  };
  process.once("SIGINT", shutdown);
  process.once("SIGTERM", shutdown);
  try {
    const port = await freePort(),
      origin = `http://127.0.0.1:${port}`;
    const launcher = `import sys,pathlib,json\nsys.path.insert(0,${JSON.stringify(root)})\nimport app\noriginal=app.fetch_remote_icon\ndef fixture_fetch(target):\n if target=='https://icons.example.test/fixture.png':\n  pathlib.Path(${JSON.stringify(path.join(fixture, "remote-hit.json"))}).write_text(json.dumps({'url':target,'mocked':True}))\n  return pathlib.Path(${JSON.stringify(remoteFile)}).read_bytes()\n raise app.APIError(400,'Fixture denies all other remote network requests')\napp.fetch_remote_icon=fixture_fetch\napp.init()\nprint('ICON_FIXTURE_READY',flush=True)\napp.Server(('127.0.0.1',${port}),app.Handler).serve_forever()\n`;
    server = spawn(args.python, ["-c", launcher], {
      cwd: root,
      env: {
        ...process.env,
        HOMEHUB_DATA: dataDir,
        HOMEHUB_BIND: "127.0.0.1",
        HOMEHUB_PORT: String(port),
        HOMEHUB_ORIGIN: origin,
        HOMEHUB_COOKIE_SECURE: "false",
        PYTHONUNBUFFERED: "1",
      },
      stdio: ["ignore", "pipe", "pipe"],
    });
    await new Promise((resolve, reject) => {
      const timer = setTimeout(
        () => reject(new Error(`Fixture startup timeout: ${redact(log)}`)),
        20000,
      );
      server.once("error", (error) => {
        clearTimeout(timer);
        reject(error);
      });
      server.once("exit", (code) => {
        clearTimeout(timer);
        reject(new Error(`Fixture exited ${code}: ${redact(log)}`));
      });
      for (const stream of [server.stdout, server.stderr])
        stream.on("data", (b) => {
          log = (log + b.toString()).slice(-10000);
          if (log.includes("ICON_FIXTURE_READY")) {
            clearTimeout(timer);
            resolve();
          }
        });
    });
    async function request(endpoint, method = "GET", body) {
      assert.equal(
        new URL(endpoint, origin).origin,
        origin,
        "Only own loopback fixture API is allowed",
      );
      const headers = { Origin: origin };
      if (cookie) headers.Cookie = cookie;
      if (csrf && method !== "GET") headers["X-CSRF-Token"] = csrf;
      if (body !== undefined) headers["Content-Type"] = "application/json";
      const response = await fetch(origin + endpoint, {
        method,
        headers,
        ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
        signal: AbortSignal.timeout(15000),
      });
      const value = await response.json();
      assert.ok(
        response.ok,
        `${method} ${endpoint}: ${value.error || response.status}`,
      );
      return { response, value };
    }
    const profile = path.join(fixture, "chrome");
    fs.mkdirSync(profile);
    const started = await startChrome(chromePath(args.chrome), profile, (c) => {
      child = c;
    });
    browser = await connect(started.endpoint);
    const target = await browser.send("Target.createTarget", {
      url: "about:blank",
    });
    const endpoint = new URL(started.endpoint);
    const tabs = await (
      await fetch(`http://${endpoint.host}/json/list`, {
        signal: AbortSignal.timeout(10000),
      })
    ).json();
    page = await connect(
      tabs.find((x) => x.id === target.targetId).webSocketDebuggerUrl,
    );
    const exceptions = [];
    page.on("Runtime.exceptionThrown", (p) =>
      exceptions.push(p.exceptionDetails.text),
    );
    await page.send("Page.enable");
    await page.send("Runtime.enable");
    await page.send("Network.enable");
    const evaluate = (s, gesture = false) => page.evaluate(s, gesture);
    const click = (selector) =>
      evaluate(
        `document.querySelector(${JSON.stringify(selector)}).click()`,
        true,
      );
    const input = (selector, value, event = "input") =>
      evaluate(
        `(()=>{const el=document.querySelector(${JSON.stringify(selector)});el.value=${JSON.stringify(value)};el.dispatchEvent(new Event(${JSON.stringify(event)},{bubbles:true}));})()`,
      );
    async function navigate(route) {
      await page.send("Page.navigate", { url: origin + route });
      await page.wait(
        "!!document.querySelector('#main h1') && !document.querySelector('.loading-state')",
      );
    }
    await navigate("/admin");
    await input('[name="username"]', "astral");
    await input('[name="password"]', password);
    await click('.login-panel button[type="submit"]');
    await page.wait("!!document.querySelector('.admin-panel .admin-row')");
    // Obtain a separate fixture-only API session for assertions; never production credentials.
    const login = await request("/api/login", "POST", {
      username: "astral",
      password,
    });
    cookie = login.response.headers.get("set-cookie").split(";")[0];
    csrf = login.value.csrf;
    const initial = (await request("/api/data")).value;
    const first = initial.links.find((x) => x.catalog_key === "website:emby");
    const second = initial.links.find((x) => x.catalog_key === "app:jellyfin");
    assert.ok(first && second);
    const firstName = first.name;
    const originalOfficial = await evaluate(
      `window.HOMEHUB_ICONS[${JSON.stringify(first.catalog_key)}].src`,
    );
    const builtin = await evaluate(`window.HOMEHUB_ICONS['website:music'].src`);
    assert.ok(builtin && builtin !== originalOfficial);
    const snapshot = async (id) =>
      (await request("/api/data")).value.links.find((x) => x.id === id);
    async function edit(name) {
      if (!(await evaluate("!!document.querySelector('.admin-panel')"))) {
        await click("#account");
        await page.wait("!!document.querySelector('.admin-panel .admin-row')");
      }
      await evaluate(
        `(()=>{const row=[...document.querySelectorAll('.admin-row')].find(x=>x.querySelector('.row-info strong')?.textContent===${JSON.stringify(name)});if(!row)throw new Error('Service row missing');[...row.querySelectorAll('button')].find(x=>x.textContent==='编辑').click();})()`,
        true,
      );
      await page.wait(
        "document.querySelector('#editor').open && !!document.querySelector('#icon-editor')",
      );
      await page.wait(
        "document.querySelector('#icon-library').options.length>2",
      );
    }
    async function chooseFile(file) {
      const doc = await page.send("DOM.getDocument");
      const node = await page.send("DOM.querySelector", {
        nodeId: doc.root.nodeId,
        selector: "#icon-file",
      });
      await page.send("DOM.setFileInputFiles", {
        nodeId: node.nodeId,
        files: [file],
      });
    }
    const selected = () =>
      evaluate("document.querySelector('#icon-local-path').value");
    async function preview() {
      await page.wait(
        "(()=>{const i=document.querySelector('#icon-preview img');return i&&i.complete&&i.naturalWidth>0})()",
      );
    }
    async function save() {
      await click('#dialog-body button[type="submit"]');
      await page.wait("!document.querySelector('#editor').open");
    }
    async function pick(src) {
      await input("#icon-library", src, "change");
      await preview();
      assert.equal(await selected(), src);
    }
    async function card(name, expected) {
      await click('[data-route="/nav"]');
      await page.wait("!!document.querySelector('#nav-results')");
      await evaluate(
        `(()=>{const a=[...document.querySelectorAll('.service-link')].find(x=>x.querySelector('.service-name-text')?.textContent===${JSON.stringify(name)});a.scrollIntoView({block:'center'});const i=a.querySelector('img');if(i)i.loading='eager';})()`,
      );
      await page.wait(
        `(()=>{const a=[...document.querySelectorAll('.service-link')].find(x=>x.querySelector('.service-name-text')?.textContent===${JSON.stringify(name)});const i=a?.querySelector('.emblem img');return i&&i.complete&&i.naturalWidth>0})()`,
      );
      const actual = await evaluate(
        `(()=>{const a=[...document.querySelectorAll('.service-link')].find(x=>x.querySelector('.service-name-text')?.textContent===${JSON.stringify(name)});return new URL(a.querySelector('.emblem img').src).pathname})()`,
      );
      assert.equal(
        actual,
        expected,
        "Saved custom icon must beat the built-in official logo",
      );
    }
    await edit(firstName);
    await chooseFile(uploadFile);
    await page.wait(
      "document.querySelector('#icon-local-path').value.startsWith('/media/icons/') && document.querySelector('#icon-editor').dataset.busy!=='true'",
    );
    await preview();
    const uploaded = await selected();
    assert.equal(
      (await snapshot(first.id)).iconUrl || "",
      "",
      "Unsaved upload must not mutate service",
    );
    await click("#dialog-close");
    assert.equal((await snapshot(first.id)).iconUrl || "", "");
    await card(firstName, originalOfficial);
    report.checks.push(
      "real PNG upload, decoded draft preview, cancel preserves saved service and official fallback",
    );
    await edit(firstName);
    await page.wait(
      `!![...document.querySelector('#icon-library').options].find(x=>x.value===${JSON.stringify(uploaded)})`,
    );
    await pick(uploaded);
    await save();
    assert.equal((await snapshot(first.id)).iconUrl, uploaded);
    await card(firstName, uploaded);
    const longName = "这是用于检查超长标题和地址徽标布局的临时服务名称";
    await edit(second.name);
    await page.wait(
      `!![...document.querySelector('#icon-library').options].find(x=>x.value===${JSON.stringify(uploaded)})`,
    );
    await pick(uploaded);
    await input('#dialog-body [name="name"]', longName);
    await save();
    assert.equal((await snapshot(second.id)).iconUrl, uploaded);
    report.checks.push(
      "save applies custom override; second service reuses library file without reupload",
    );
    await edit(firstName);
    await pick(builtin);
    await save();
    assert.equal((await snapshot(first.id)).iconUrl, builtin);
    await card(firstName, builtin);
    await edit(firstName);
    await input("#icon-local-path", uploaded);
    await preview();
    await save();
    assert.equal((await snapshot(first.id)).iconUrl, uploaded);
    await edit(firstName);
    await input("#icon-local-path", "/etc/passwd");
    await click('#dialog-body button[type="submit"]');
    await page.wait(
      "!!document.querySelector('#dialog-body .form-error').textContent",
    );
    assert.equal((await snapshot(first.id)).iconUrl, uploaded);
    await click("#dialog-close");
    report.checks.push(
      "built-in selection, reusable station paths, arbitrary absolute filesystem path rejected without mutation",
    );
    await edit(firstName);
    await input("#icon-remote-url", "https://icons.example.test/fixture.png");
    await click("#icon-fetch");
    await page.wait(
      `document.querySelector('#icon-local-path').value!==${JSON.stringify(uploaded)} && document.querySelector('#icon-editor').dataset.busy!=='true'`,
    );
    await preview();
    const remote = await selected();
    assert.ok(remote.startsWith("/media/icons/") && remote !== uploaded);
    assert.ok(
      JSON.parse(fs.readFileSync(path.join(fixture, "remote-hit.json"), "utf8"))
        .mocked,
    );
    await save();
    assert.equal((await snapshot(first.id)).iconUrl, remote);
    await card(firstName, remote);
    report.checks.push(
      "URL fetch UI caches fixture image locally; network fetch explicitly mocked, not a real provider transport test",
    );
    await edit(firstName);
    await chooseFile(badFile);
    await page.wait(
      "document.querySelector('#icon-status').classList.contains('is-error') && document.querySelector('#icon-editor').dataset.busy!=='true'",
    );
    assert.equal(await selected(), remote);
    assert.equal((await snapshot(first.id)).iconUrl, remote);
    await click("#icon-reset");
    assert.equal(await selected(), "");
    await preview();
    assert.equal((await snapshot(first.id)).iconUrl, remote);
    await save();
    assert.equal((await snapshot(first.id)).iconUrl || "", "");
    await card(firstName, originalOfficial);
    report.checks.push(
      "active SVG rejection leaves saved image intact; restore-default is preview-only until save, then official fallback restored",
    );
    // Geometry with a long name and inline type badge, in a real authenticated page.
    for (const width of [1440, 390, 360]) {
      await page.send("Emulation.setDeviceMetricsOverride", {
        width,
        height: width > 650 ? 1000 : 844,
        deviceScaleFactor: 1,
        mobile: width <= 650,
      });
      await evaluate("window.scrollTo(0,0)");
      const geometry = await evaluate(
        `(()=>{const cards=[...document.querySelectorAll('#nav-results .service-card')];const overflow=cards.filter(c=>{const badge=c.querySelector('.addr-badge'),name=c.querySelector('.service-name');if(!badge)return false;const b=badge.getBoundingClientRect(),n=name.getBoundingClientRect();return b.right>n.right+1||b.left<n.left-1});const brand=document.querySelector('.brand').getBoundingClientRect(),nav=document.querySelector('.primary-nav').getBoundingClientRect(),actions=document.querySelector('.header-actions').getBoundingClientRect();const bar=document.querySelector('.filter-bar'),cats=bar.querySelector('.categories:not(.addr-filter)'),addr=bar.querySelector('.addr-filter'),views=bar.querySelector('.view-controls');const cR=cats.getBoundingClientRect(),aR=addr.getBoundingClientRect(),vR=views.getBoundingClientRect();const heading=document.querySelector('#main .page-heading').getBoundingClientRect();return {width:innerWidth,overflow:document.documentElement.scrollWidth>innerWidth,badgeOverflow:overflow.length,headerOverlap:brand.right>nav.left+1||nav.right>actions.left+1,columns:getComputedStyle(document.querySelector('#nav-results')).gridTemplateColumns.trim().split(/\\s+/).length,clippedChips:[...bar.querySelectorAll('.chip')].filter(c=>c.scrollWidth>c.clientWidth+1).map(c=>c.textContent),filtersOwnRow:aR.top>=cR.bottom-1&&vR.top>=cR.bottom-1,viewsInside:vR.right<=innerWidth+1,headingHeight:heading.height,catsScrollable:cats.scrollWidth>cats.clientWidth+1,scrollFade:getComputedStyle(cats).maskImage!=='none',endClearance:(()=>{const last=cats.lastElementChild;cats.scrollLeft=cats.scrollWidth;const gap=cats.getBoundingClientRect().right-last.getBoundingClientRect().right;cats.scrollLeft=0;return gap})()}})()`,
      );
      report.mobile.push({ authenticated: true, ...geometry });
      assert.equal(geometry.overflow, false, JSON.stringify(geometry));
      assert.equal(geometry.badgeOverflow, 0, JSON.stringify(geometry));
      assert.equal(geometry.headerOverlap, false, JSON.stringify(geometry));
      assert.deepEqual(
        geometry.clippedChips,
        [],
        `No clipped filter chip text at ${width}`,
      );
      assert.equal(geometry.viewsInside, true, JSON.stringify(geometry));
      if (width > 650) {
        assert.equal(
          geometry.filtersOwnRow,
          false,
          `Desktop ${width} keeps chips and controls on one row`,
        );
        assert.ok(geometry.headingHeight > 10, JSON.stringify(geometry));
      } else {
        assert.equal(
          geometry.filtersOwnRow,
          true,
          `Mobile ${width} moves filters below the chips: ${JSON.stringify(geometry)}`,
        );
        assert.ok(geometry.headingHeight <= 46, JSON.stringify(geometry));
        assert.equal(
          geometry.scrollFade,
          true,
          `Mobile ${width} keeps the chip edge fade: ${JSON.stringify(geometry)}`,
        );
        if (geometry.catsScrollable)
          assert.ok(
            geometry.endClearance >= 20,
            `Mobile ${width} clears the last chip of the faded edge: ${JSON.stringify(geometry)}`,
          );
      }
      await edit(firstName);
      await preview();
      const editor = await evaluate(`(() => {
        const dialog=document.querySelector('#editor'), section=document.querySelector('#icon-editor');
        const d=dialog.getBoundingClientRect(), s=section.getBoundingClientRect();
        const outside=[...section.querySelectorAll('input,select,button')].filter(el=>{
          const r=el.getBoundingClientRect();return r.left<s.left-1||r.right>s.right+1;
        }).map(el=>el.id||el.name||el.tagName);
        const i=section.querySelector('#icon-preview img');
        return {width:innerWidth,dialogOverflow:dialog.scrollWidth>dialog.clientWidth+1,
          sectionOverflow:section.scrollWidth>section.clientWidth+1,
          outsideViewport:d.left<0||d.right>innerWidth,
          controlsOutside:outside,previewDecoded:!!i&&i.complete&&i.naturalWidth>0};
      })()`);
      report.mobile[report.mobile.length - 1].iconEditor = editor;
      assert.equal(editor.dialogOverflow, false, JSON.stringify(editor));
      assert.equal(editor.sectionOverflow, false, JSON.stringify(editor));
      assert.equal(editor.outsideViewport, false, JSON.stringify(editor));
      assert.deepEqual(editor.controlsOutside, [], JSON.stringify(editor));
      assert.equal(editor.previewDecoded, true, JSON.stringify(editor));
      await click("#dialog-close");
      await click('[data-route="/nav"]');
      await page.wait("!!document.querySelector('#nav-results')");
    }
    report.checks.push(
      "icon edit dialog, upload/path/library/URL controls and decoded preview remain contained at 1440/390/360 widths",
    );
    await page.send("Emulation.setDeviceMetricsOverride", {
      width: 1440,
      height: 1000,
      deviceScaleFactor: 1,
      mobile: false,
    });
    await click("#account");
    await page.wait("!!document.querySelector('.admin-panel')");
    await evaluate(
      "[...document.querySelectorAll('button')].find(x=>x.textContent==='退出登录').click()",
      true,
    );
    await page.wait("!!document.querySelector('.login-panel')");
    await click('[data-route="/nav"]');
    await page.wait("!!document.querySelector('#nav-results')");
    await evaluate(
      "[...document.querySelectorAll('#nav-results .emblem img')].forEach(i=>{i.loading='eager'})",
    );
    await page.wait(
      "[...document.querySelectorAll('#nav-results .emblem img')].every(i=>i.complete&&i.naturalWidth>0)",
    );
    report.checks.push(
      "public cards decode local icons, authenticated desktop/mobile long-name badges stay inside cards, header controls do not overlap",
    );
    assert.deepEqual(
      exceptions,
      [],
      "No unexpected browser JavaScript exceptions",
    );
    if (screenshots)
      for (const width of [1440, 390, 360]) {
        await page.send("Emulation.setDeviceMetricsOverride", {
          width,
          height: width > 650 ? 1000 : 844,
          deviceScaleFactor: 1,
          mobile: width <= 650,
        });
        await evaluate("window.scrollTo(0,0)");
        const out = path.join(
          screenshots,
          `icons-public-${width}-${stamp}.png`,
        );
        const shot = await page.send("Page.captureScreenshot", {
          format: "png",
          captureBeyondViewport: false,
        });
        fs.writeFileSync(out, Buffer.from(shot.data, "base64"));
        report.screenshots.push(out);
      }
    console.log(JSON.stringify(report, null, 2));
  } catch (error) {
    throw new Error(redact(error.stack || error));
  } finally {
    page?.close();
    browser?.close();
    await stop(publicChild);
    await stopChrome(child);
    await stop(server);
    process.removeListener("SIGINT", shutdown);
    process.removeListener("SIGTERM", shutdown);
    assert.equal(path.dirname(fixture), tempRoot);
    assert.ok(path.basename(fixture).startsWith("homehub-icon-smoke-"));
    assert.equal(fs.lstatSync(fixture).isSymbolicLink(), false);
    fs.rmSync(fixture, { recursive: true, force: true });
  }
}
main().catch((error) => {
  console.error(error.message || error);
  process.exitCode = 1;
});

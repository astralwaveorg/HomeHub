# Homehub read-only service inventory

## Scope and evidence

Observed 2026-10-12 from NAS through strict-host-key, BatchMode SSH. This report is a sanitized point-in-time audit, not an uptime or authenticated functional test. No NAS/router/application/frontend changes were made. Only container names, published ports, project/network-mode labels, manifest identity/UI launch fields, gateway location/proxy destinations and HTTP response classifications were extracted. No environment, credentials, full inspect or full sensitive configuration was exported.

HTTP probes ran **on NAS**. HTTPS used HTTP/1.1, normal TLS verification and `--resolve <host>:8443:127.0.0.1`; therefore they verify the existing reverse proxy and certificate but **not public DNS, WAN reachability or a remote browser**. LAN URLs were requested on NAS using its LAN address, not from a second LAN host. All successful probes had curl exit 0. Refused candidate ports had curl exit 7/status 000 and are explicitly excluded. Redirects were initially measured without following; selected native redirects were subsequently followed.

At audit start, the [v1.0.0 catalog fixture](../tests/fixtures/catalog-v1.0.0.json) had 27 links: 21 HTTPS and 6 LAN. The current [source catalog](../seed.json) implements the additions below and has 41 public defaults; five token-gated entries provide desktop-launch instructions instead of directly navigating to invalid-token gateways. User-approved publication means public *directory metadata*, not bypassing app login, publishing new ports, adding proxies or changing network/TLS. Existing apps retain original authentication. `200 invalid token` is an authentication gate, not a usable anonymous homepage or proof of broken installation. Label those links “需先登录飞牛 / 从飞牛桌面打开”; plain links may still lack the fnOS launch token even with an existing session.

## Catalog gaps: 14 additional independent launch entries

All paths below are prefixed by `https://fn.homehub.stream:8443`. Categories reference existing category names. These are manifest/UI or system-nginx-confirmed entrypoints, not inferred port guesses.

| Suggested name | Verified path | HTTP result | Category | Search aliases / launch caveat |
| --- | --- | --- | --- | --- |
| 百度网盘 | `/app-baidu-netdisk/` | Manifest query URL 301 → 200, title 百度网盘 | 文件与收藏 | baidu netdisk 百度 网盘；保留飞牛/百度鉴权 |
| 重复文件清理 | `/cgi/ThirdParty/dupclean/index.cgi/` | 200, **invalid token** | 文件与收藏 | dupclean 去重 重复 清理；需飞牛启动上下文 |
| 飞牛浏览器 | `/app/fygo-browser/browser.html?v=1.0.4-fnos` | 200, **invalid token** | 家庭应用 | fygo browser 浏览器；不要用已拒绝连接的 16002 根端口 |
| 音乐整理 | `/app/knas-music-organizer/?v=0.3.0` | 200, **invalid token** | 音乐与创作 | knas music organizer 音乐 整理；需飞牛启动上下文 |
| Tailscale | `/cgi/ThirdParty/tailscale/index.cgi` | 200, **invalid token** | 系统管理 | tailscale VPN 组网；这是管理 UI，不是数据/隧道端口 |
| iSCSI 管理 | `/iscsi/` | 200, title iSCSI | 系统管理 | iscsi 块存储；仅管理页，绝不链接 iSCSI 数据端口 |
| 飞牛音乐 | `/music/` | `/music` 301 → 200, title 飞牛音乐 | 音乐与创作 | trim.music fnos 飞牛音乐；与现有 Navidrome 分开 |
| 飞牛相册 | `/p` | 200, title 相册 | 文件与收藏 | trim.photos 相册 photos；与现有自定义图片站分开 |
| 快照管理 | `/snapshot/` | Manifest query URL 301 → 200, title Volume Snapshots | 系统管理 | trim.snapshots snapshot 快照 |
| 飞牛同步 | `/fn-sync/` | 200, title Sync | 文件与收藏 | trim.sync_server sync 同步 |
| 虚拟机 | `/vm?ver=0.9.0` | 200, title VM | 系统管理 | trim.vm vm 虚拟机；不额外列依赖 VM 标识的 VNC 子入口 |
| 万能 MCP | `/cgi/ThirdParty/xiaohu-mcpProxy/index.cgi/` | 200, **invalid token** | 系统管理 | xiaohu mcproxy MCP 万能MCP；9900 根是 JSON 服务，优先原生管理 UI |
| 飞牛 Docker（应急） | `/apps/docker` | 200, title Docker | 系统管理 | fnos docker 应急；**日常仍只用 1Panel**，不重复管理配置 |
| 飞牛下载 | `/apps/download` | 200, title 下载 | 下载与做种 | fnos download 飞牛下载；与三套 PT 下载器分开 |

If all 14 independent launch entries are added, directory count is **41** excluding the portal self-link. Do not inflate this count with file-context preview helpers, database/API/data ports or removed applications. Token-gated links must not be advertised as directly usable anonymous services.

## Complete running-container coverage: 15 containers, 11 projects

Project names came from the Compose project label; host modes were verified with targeted formatted inspect (never full inspect). Published/data ports are noted solely to prevent accidental navigation exposure.

| Actual container | Project | Existing/shared navigation | Observed HTTP / exclusion |
| --- | --- | --- | --- |
| homehub | homehub | Portal itself `https://homehub.stream:8443/` | 200; loopback-only 48080→8080, no duplicate self card required |
| beets | beets | Beets `http://10.0.1.2:48002/` | 200; 48002→8337, existing LAN-only label; no new public proxy |
| tr-seed | pt-tools | 长期保种 `https://seed.homehub.stream:8443/` | 401 auth challenge; 41002→9091; **41012 peer port is not Web** |
| qb-sub | pt-tools | 订阅下载 `https://down.homehub.stream:8443/` | 200; host Web listener 41001; **41011 peer port is not Web** |
| qb-flux | pt-tools | 刷流下载 `https://flux.homehub.stream:8443/` | 200; host Web listener 41003; **41013 peer port is not Web** |
| moviepilot | moviepilot | MoviePilot `https://mp.homehub.stream:8443/` | 200; 42010 frontend; 42042 root returns 404 JSON backend, shared entry only |
| moviepilot-redis | moviepilot | Shared MoviePilot entry | Internal cache, container 6379 not published, no independent UI/nav |
| moviepilot-db | moviepilot | Shared MoviePilot entry | Internal PostgreSQL, container 5432 not published, no independent UI/nav |
| openresty | openresty | Shared 1Panel / all HTTPS routes | Host-mode reverse-proxy infrastructure on 8443/8880; no independent application UI/nav |
| audiobookshelf | audiobookshelf | 有声书 `https://books.homehub.stream:8443/` | 200; 13378→80 |
| music-tag-web | music-tag-web | 音乐标签 `https://mtag.homehub.stream:8443/` | 200; 48001→8002 |
| vertex | vertex | Vertex `https://vt.homehub.stream:8443/` | 302; 51015→51015; retained network name dockge-bridge does **not** mean Dockge installed |
| miair-next | miair-next | 音箱投送 `http://10.0.1.2:48320/` | 200; host-mode; LAN discovery/casting not made WAN-capable by a directory link |
| vaultwarden | vaultwarden | 密码库 `https://vault.homehub.stream:8443/` | 200; 40031→80; 3012 auxiliary transport not separate nav |
| emby | emby | Emby `https://emby.homehub.stream:8443/` | 302; 48096→8096; 48920→8920 alternate TLS transport, not separate app |

There are **no missing independent Docker Web cards** in the baseline. No Dockge, Watchtower, SoyueGo or Docker MetaTube container appears in the running inventory.

## Complete installed-native-app coverage: 30 manifests

Installed means manifest and installed app scaffold exist; it does not imply every background component was independently health-tested. HTTP launch checks above provide stronger evidence where available.

| Actual installed app | Existing/new/shared mapping | Verified status or no-independent-nav reason |
| --- | --- | --- |
| 1Panel | Existing 1Panel | `https://panel.homehub.stream:8443/astralwave` 200; keep security entrance |
| FnMessageBot | Existing 通知管理 | `http://10.0.1.2:18230/` 200; distinct from deferred notify DNS |
| Gitea | Existing 代码仓库 | `https://git.homehub.stream:8443/` 200; active backend 8418 |
| Jellyfin | Existing Jellyfin | `http://10.0.1.2:8097/` 302 |
| OpenList | Existing 文件空间 | `https://alist.homehub.stream:8443/` 200; retained alist alias, backend 45015 |
| ammds | Existing AMMDS | `http://10.0.1.2:49523/` 200; **manifest 9523 is stale** (connection refused), retain actual working 49523 |
| baidu.netdisk | New 百度网盘 | Launch route and redirect verified above |
| bunjs | No nav | Bun runtime, no desktop UI launch field |
| dupclean | New 重复文件清理 | Token-gated launch above |
| fygo-browser | New 飞牛浏览器 | Token-gated manifest route above; 16002 refuses, do not invent direct backend entry |
| go-1.24 | No nav | Go runtime, no desktop UI launch field |
| hand.nas | Existing 指尖 NAS | `http://10.0.1.2:5366/` 200 |
| knas-music-organizer | New 音乐整理 | Token-gated manifest route above |
| lovespace | Existing 情侣小站 | `https://love.homehub.stream:8443/` 200; existing direct app auth retained; fn gateway `/app/lovespace/` 200 invalid token is not a replacement |
| minio | Existing 对象存储 | `https://storage.homehub.stream:8443/` 200 console; **9000 object API is not extra nav** |
| navidrome | Existing 音乐 | `https://music.homehub.stream:8443/` 302; 4533; do not merge with trim.music |
| nodejs_v22 | No nav | Node runtime, no desktop UI launch field |
| nodejs_v24 | No nav | Node runtime, no desktop UI launch field |
| tailscale | New Tailscale | Token-gated CGI management UI above |
| trim.docs | Shared 飞牛 NAS / file manager | Manifest `/docs/preview` returns 302→**403** `/docs/403`, title 打开异常 without file context; document-opening handler, not healthy independent home page |
| trim.iscsi | New iSCSI 管理 | Management UI 200 above; do not expose storage data port |
| trim.media | Existing 飞牛影视 | `https://tv.homehub.stream:8443/` 301; native manifest `/v` is alternate fn launcher, no duplicate app card |
| trim.music | New 飞牛音乐 | Independent native music UI 200 above |
| trim.photos | New 飞牛相册 | Independent native photo UI 200 above |
| trim.preview | Shared 飞牛 NAS / file manager | `/app/trim-preview/` 200 invalid token; file-context preview helper, no independent library/homepage |
| trim.snapshots | New 快照管理 | Management UI 200 above |
| trim.sync_server | New 飞牛同步 | Independent sync UI 200 above |
| trim.text-editor | Shared 飞牛 NAS / file manager | `/cgi/ThirdParty/trim.text-editor/index.cgi/` 200 invalid token; file association/editor handler, launch by opening a file, no independent file browser |
| trim.vm | New 虚拟机 | Management UI 200 above; VNC subroute requires VM context and is not independent nav |
| xiaohu-mcpProxy | New 万能 MCP | Token-gated CGI UI above; direct 9900 root 200 JSON is service API, not the management launch |

### AMMDS native MetaTube identity

Native listener 18080 belongs to `metatube-amd64`; root responds 200 JSON. It is AMMDS's bundled metadata API component, mapped to the existing AMMDS card, **not uninstall residue**, and not evidence of an independent human-facing UI. Never re-add removed Docker MetaTube at former 42026.

## Host/system/router coverage and non-candidates

| Component | Mapping / verification |
| --- | --- |
| fnOS shell, app center, file management, account/system settings | Existing 飞牛 NAS `https://fn.homehub.stream:8443/` 200. System-internal modules without proven standalone launch should remain shared, not invented deep links |
| fnOS Docker | New emergency card `/apps/docker` 200; 1Panel remains primary |
| fnOS downloader | New `/apps/download` 200 |
| WebDAV | Existing `https://dav.homehub.stream:8443/` 401; legitimate auth-protected protocol entry, not an HTML dashboard |
| Custom picture service | Existing `https://pic.homehub.stream:8443/` 200; loopback backend 2343 |
| ImmortalWrt/LuCI router | Existing 家庭网络 `https://owrt.homehub.stream:8443/` 200; no router modification/login performed |
| ddns-go router service | Existing 动态解析 `https://ddns.homehub.stream:8443/` 307; preserve service auth; no router modification/login performed |
| `/app-center` and `/app-center/` | 404; no new broken app-center card, use fnOS shell |
| `/seek` | 502; configured system route is not enough to list a healthy UI; shared fnOS coverage only, no repair attempted |
| `/chromium/` | 502; do not confuse this unavailable generic system backend with installed fygo-browser's verified token-gated launch |
| fnOS PostgreSQL/RabbitMQ/RPC/SMB/FTP/NFS/iSCSI/UPnP/background daemons | Infrastructure or data protocols, no independent Web nav based solely on a listener |
| Removed/deferred | Do not add Dockge, Watchtower, SoyueGo, Docker MetaTube, nginx-ui, status DNS or notify DNS. FnMessageBot existing LAN card is not a new notify DNS entry |

## Implementation recommendations for parent

1. Retain all 27 working baseline targets, original application authentication and existing LAN-only descriptions.
2. Add the 14 verified independent launch entries above as public directory metadata if full-directory publication is being implemented. Token-gated entries require explicit fnOS-launch caveats; no credential/token URL construction.
3. Native document/preview/text-editor helpers are accounted for by shared fnOS/file-manager entry, not misleading direct cards. Document the 403/file-context behavior.
4. Use aliases to cover component names without adding duplicate transport/API/database links: e.g. AMMDS aliases may include “native metatube 元数据”; MoviePilot covers its DB/cache/API; 1Panel covers OpenResty.
5. Revalidation is not login/business/WAN testing. Preserve exclusions and avoid claiming the unauthenticated response proves app authorization safety.

## Command validation note

Initial combined Docker/native-list command extracted Docker successfully but Python quoting failed (exit 1); the native enumeration was rerun with a quoted heredoc and succeeded. Every subsequent probe records curl exit explicitly; candidate connection refusals are audit results, not hidden failures. Final launch/network-mode verification completed with explicit command exit 0. No persistent inventory of PIDs, transient container state, tokens, headers/cookies or live resource/load figures is included here.

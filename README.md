# Homehub · 无界

[![CI](https://github.com/astralwaveorg/HomeHub/actions/workflows/ci.yml/badge.svg)](https://github.com/astralwaveorg/HomeHub/actions/workflows/ci.yml)

个人艺术门户与服务导航。原生 HTML/CSS/JavaScript + Python 标准库 HTTP 服务 + SQLite；
**无前端构建工具、无外部字体/CDN、无运行时第三方包**——一个 `python` 就能跑起来。

- **门户首页** `/`：氛围入口，背景音乐、静谧模式、置顶服务。
- **星图导航** `/nav`：全部服务入口，分类、搜索（名称/说明/别名）、收藏、网格/列表视图。
- **星轨观测** `/monitor`：免登录的系统观测台，实时指标、历史图表、容器详情、服务探活。
- **管理后台** `/admin`：服务与分类管理、站点设置、账户与备份、目录热加载。

内置目录 **49 个服务入口**（2026-10-30 对照 26 个容器、27 个反向代理站点与 40 个飞牛应用逐一实测校准），覆盖影音、音乐、文件、生活、下载、系统管理与家庭应用七个分类；每张卡片按地址类型显示 **内**（家庭 LAN）/ **IP**（其它直连地址）/ **公**（公网域名）徽标，依赖飞牛桌面的应用显示「飞牛启动」说明而不是伪造启动票据。

---

## 目录

- [功能特性](#功能特性)
- [快速开始](#快速开始)
- [配置](#配置)
- [生产部署](#生产部署)
- [目录数据管理](#目录数据管理)
- [账户与密码](#账户与密码)
- [背景音乐](#背景音乐)
- [图标系统](#图标系统)
- [备份与恢复](#备份与恢复)
- [开发与测试](#开发与测试)
- [发布工程](#发布工程)
- [项目结构](#项目结构)
- [安全模型](#安全模型)
- [许可证](#许可证)

## 功能特性

- **四页面结构**：门户、星图导航、星轨观测、管理后台；站内用 History API 无刷新切换，外部服务在新标签页打开。
- **导航体验**：七个分类、全文搜索、收藏（登录后云端同步，未登录走浏览器本地偏好）、网格/列表切换、地址类型筛选、手机端三列紧凑布局。
- **多账户**：管理员 + 成员账户；登录后的收藏、显示名称、音乐偏好（自动播放/音量/静音）按账户保存在服务端。
- **背景音乐**：默认内置 48 秒自制合成氛围音「无界」；支持上传 MP3/OGG/WAV/M4A/OPUS（≤25 MiB）或填 HTTPS 音源；带内容版本号的播放地址避免缓存；尊重浏览器自动播放策略。
- **图标系统**：每个入口可用内置符号、官方品牌图标（随仓库分发）、上传图片或远程抓取并缓存到本站；内容哈希去重，SSRF/路径防护齐全。
- **目录热加载**：NAS 侧放置可选的 `data/seed.json` 覆盖文件即可增补服务，保存后约 1 秒自动生效，不必重新构建镜像。
- **星轨观测**：随镜像附带的 `collector` 采集器每 5 秒读取 /proc、挂载的 /sys、只读卷 statfs 与本机 Docker socket，并按 `data/collector/devices.json` 声明的清单经专用 SSH 密钥拉取局域网其它设备（Mac mini、OpenWrt 路由器）；门户免登录代理其快照与历史接口，页面按设备类型切换展示。1 小时细粒度留在内存，分钟级归档（系统 14 天、探活 3 天）落在 `data/collector/`，超期自动清理；容器列表来自 Docker 实时发现，新容器零配置出现，探活目标由目录自动同步。
- **安全边界**：Secure/HttpOnly/SameSite=Strict Cookie、CSRF 与 Origin 校验、只读容器根文件系统、drop 全部 capabilities、no-new-privileges、CPU/内存/PID 限额、SQLite WAL；登录口令使用哈希存储，初始密码只落在数据目录且修改后即删除。

## 快速开始

### 方式一：Docker Compose（推荐）

```sh
git clone https://github.com/astralwaveorg/HomeHub.git
cd HomeHub
cp .env.example .env        # 按需修改 HOMEHUB_ORIGIN 等；不改也能跑
docker compose up -d --build
```

启动后访问 `http://127.0.0.1:48080`。compose 默认把端口绑定在**仅回环**地址并假定前面有 HTTPS 反向代理；首次访问前请把 `.env` 里的 `HOMEHUB_ORIGIN` 改成你实际使用的对外源。

### 方式二：直接运行（开发/预览）

要求 Python ≥ 3.12，无任何第三方包：

```sh
HOMEHUB_DATA="$(mktemp -d)" \
HOMEHUB_BIND=127.0.0.1 HOMEHUB_PORT=8080 \
HOMEHUB_ORIGIN=http://127.0.0.1:8080 HOMEHUB_COOKIE_SECURE=false \
python3 app.py
```

### 首次登录

首次启动时，服务自动创建管理员账户 **`astral`**，并生成一份随机初始密码写入数据目录：

```sh
# 容器方式
docker exec homehub python /app/app.py admin-password

# 直接运行方式
python3 app.py admin-password
```

在自己终端里读取即可。**请不要把密码粘贴到聊天、截图或任何会进入版本库的文件。** 登录后请到「管理 → 账户与备份」修改密码（至少 12 字符）；修改后初始密码文件会被删除，其他设备的会话随之失效。

## 配置

全部环境变量及含义见 [`.env.example`](.env.example)，摘要：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `HOMEHUB_ORIGIN` | compose 中为实际对外源 | 浏览器访问的对外源（scheme+域名+端口），跨站防护基线，必须与地址栏完全一致 |
| `HOMEHUB_COOKIE_SECURE` | `true` | Secure Cookie；仅 HTTP 本地预览时允许 `false` |
| `HOMEHUB_DATA` | `/data`（容器） | 数据目录：SQLite、初始密码文件、上传的音乐与图标 |
| `HOMEHUB_BIND` / `HOMEHUB_PORT` | `0.0.0.0` / `8080` | 直接运行 `app.py` 时的监听地址/端口 |
| `HOMEHUB_IMAGE` | `homehub-portal:1.8.0` | Compose 镜像标签；发布工具会固定为 `<版本>-<Git commit>` 不可变标签 |

## 生产部署

### 当前实例的部署位置

生产实例跑在一台飞牛 fnOS NAS（`10.0.1.2`）上，布局如下——换成任何能跑 Docker 的 Linux 主机，路径同理：

| 项 | 位置 / 值 |
| --- | --- |
| Compose 项目根 | NAS `/vol1/1000/docker/homehub/`（堆栈目录，不是 Docker 引擎数据目录） |
| 持久化数据 | `<项目根>/data/`：`homehub.db`（SQLite WAL）+ `media/`（音乐与图标库）；属主 `1000:1000`、权限 `700` |
| 应用端口 | **仅回环** `127.0.0.1:48080` → 容器 `8080`；容器只读根、UID/GID 1000、0.75 CPU / 256 MiB / 128 PIDs |
| HTTPS 入口 | `https://homehub.stream:8443`，由 1Panel OpenResty 反代到 `127.0.0.1:48080`；证书在反代层，应用不持有证书 |
| 发布备份 | `<项目根>/backups/<UTC时间戳>-<随机>/`（仅存 NAS，含敏感数据，权限 0700） |
| 发布记录 | `<项目根>/releases/current.json`（当前版本、镜像、回滚点） |

### 标准发布流程（scripts/release.py）

发布工具在本地打包**已提交的干净源码**（`git archive`，可复现字节与 SHA256），通过严格主机键校验的 SSH 上传，在 NAS 端完成一个带自动回滚的事务：

```sh
# 0. 本地检查通过、工作区干净、变更已提交
python3 scripts/check.py

# 1. 打包（输出 manifest 路径）
python3 scripts/release.py package --output /tmp/homehub-release

# 2. 预演（不连接 NAS）
python3 scripts/release.py deploy --manifest /tmp/homehub-release/<manifest>.tar.json --dry-run

# 3. 正式发布（默认 host=root@10.0.1.2，public-url=https://homehub.stream:8443）
python3 scripts/release.py deploy --manifest /tmp/homehub-release/<manifest>.tar.json
```

部署事务依次执行：校验 SHA256 → 锁定部署 → 构建不可变镜像 `homehub-portal:<版本>-<提交>` → 在线备份 SQLite/媒体/旧源码 → 仅停止 homehub → 原子替换白名单内源码文件 → 以固定镜像重建容器 → 后端与公网健康检查（版本号必须匹配）→ 执行 `sync-catalog` → 复检 → 写入发布记录。**任何一步失败都会自动回滚代码与镜像**（数据库与媒体不参与回滚，用户数据无损）。

代码回退（保留数据）：

```sh
python3 scripts/release.py rollback --backup <部署时打印的备份名> --dry-run
python3 scripts/release.py rollback --backup <备份名>
```

详细的事务边界、白名单、NAS 前置条件与 GitHub Actions 手动发布通道见 [docs/RELEASING.md](docs/RELEASING.md)。**仓库自带的 `deploy.yml` 默认关闭**：需要在受保护环境、self-hosted runner 与显式开关（`HOMEHUB_DEPLOY_ENABLED=true`）齐备后才会执行，仓库 CI（`ci.yml`）则对每次 push/PR 自动运行。

### 在其它主机部署的要点

1. 准备一个属主为 `1000:1000`（权限 700）的数据目录，挂载为容器的 `/data`。
2. 在反代（OpenResty/Caddy/Nginx）上配置 HTTPS，把对外源反代到应用的回环端口；**`HOMEHUB_ORIGIN` 必须与对外源一致**，否则登录会被跨站防护拒绝。
3. 不要用 HTTP + IP 地址测试浏览器登录——生产 Cookie 是 Secure 的。
4. 备份请使用 SQLite 在线备份 API 或短暂停服后整目录复制；**不要只热复制 `homehub.db` 单文件而忽略 WAL**（详见 [备份与恢复](#备份与恢复)）。

## 目录数据管理

目录 = 分类 + 服务入口，来源有三层，按 `catalog_key` 稳定标识合并：

1. **镜像内置目录** [`seed.json`](seed.json)：49 个默认入口，随版本发布。
2. **NAS 覆盖文件** `data/seed.json`（可选）：只写要新增/修正的条目即可，保存后约 1 秒热生效；也可在管理后台上传。详见 [docs/catalog.md](docs/catalog.md)。
3. **管理后台的手工编辑**：改名、改地址、排序、置顶、公开/私密、删除。

同步语义是**增量且尊重所有者**：发布时的 `sync-catalog` 只新增缺失的默认项、按 `legacy_names` 完成改名；已有的名称、地址、图标、可见性与**主动删除**（墓碑记录在 `catalog_registry`，防止复活）永不被覆盖。目录迁移保持向后兼容旧镜像；回退代码不会撤销一次成功的目录变更。

新条目约定：`catalog_key` 不可变（如 `website:song`、`app:beets`、`fnos:tailscale`）；依赖 fnOS 启动票据的飞牛应用使用 `launch_mode=fnos`，卡片展示桌面启动指引，**绝不伪造 token 或绕过鉴权**；数据库/API/数据端口不录入口。

## 账户与密码

- 管理员 `astral` 由首次启动自动创建；`python /app/app.py add-account <用户名> <密码>` 可添加与管理员同权的成员账户。
- 忘记密码：NAS 管理员执行 `docker exec homehub python /app/app.py reset-password`，为 astral 生成新随机密码并注销其全部会话（不影响其它账户）。
- 密码只存哈希；初始密码文件在首次改密后删除。

## 背景音乐

默认音源是仓库自带的 48 秒自制合成氛围音「无界」（`ambient.wav`），不是任何商业歌曲录音。上传自定义音源：管理 → 站点设置 → 选择文件（≤25 MiB）或填 HTTPS 地址 → 保存；上传沿用当前自动播放开关，留空音源即关闭。播放地址带内容版本号，替换后不会读到旧缓存；打开页面先尝试自动播放，被浏览器拦截时显示「点击 ▷ 开启音乐」。访客静音/音量偏好本地保留，登录用户的偏好云端保存。

## 图标系统

每张卡片的图标优先级：自定义图标（上传/抓取/库/站内路径）→ 官方默认图标（`static/icons/`，按 `catalog_key` 映射于 `static/icons.js`）→ 内置符号字符。上传与远程抓取有格式验证、1 MiB 限制、SVG 活动/外部内容拒绝与逐跳 SSRF 防护。管理细节见 [docs/icon-management.md](docs/icon-management.md)。

## 备份与恢复

- **页面 JSON 导出**（管理 → 账户与备份）：含分类、导航与站点设置，**不含**账户哈希、会话、音频与图标文件内容；适合迁移导航，不是完整备份。导入失败自动回滚，不破坏现有数据。
- **完整备份**：短暂停服后复制整个 `data/` 目录，或用 SQLite 在线备份 API 备份数据库并另行复制 `media/`。`data/seed.json` 与 `seed.json.bak` 会随发布备份一并带走。
- **不要**只热复制一个正在写入的 `homehub.db` 而忽略 WAL。
- 发布备份含敏感数据，只留在 NAS（权限 0700），不进任何版本库或 CI 产物。

## 开发与测试

```sh
python3 scripts/check.py          # 应用+脚本 unittest、node --check、JSON 目录校验
python3 -m unittest discover -s tests -v
python3 -m unittest discover -s scripts -v
node --check static/app.js
# 可选浏览器验收（需本机 Chrome / Node 22+；只连临时服务，不碰生产）
node scripts/browser-smoke.mjs --url http://127.0.0.1:8080 --screenshots tests/screenshots/local
node scripts/music-smoke.mjs --screenshots tests/screenshots/music-local
node scripts/icon-smoke.mjs --screenshots tests/screenshots/icons-local
# 可选前端格式化
npx --yes prettier@3.6.2 --write static/app.js static/styles.css static/index.html
```

开发约定：分支开发 → 完整检查 → 审查 diff → 提交；改行为必须带回归测试；不随手引入生产 CDN/构建依赖；运行数据、凭据、截图、备份永不进入提交或镜像。`.env`、`data/`、`initial-admin-password`、`tests/screenshots/` 等均已在 [.gitignore](.gitignore) 中忽略——**本仓库不包含任何运行数据**。

## 发布工程

- 版本号在 [`VERSION`](VERSION)，每个发布同步更新 [`CHANGELOG.md`](CHANGELOG.md)。
- 打包只取**已提交**的干净源码（未提交/未跟踪文件会令打包失败），`git archive` 保证同一提交产出字节级一致、SHA256 可校验的产物。
- 发布镜像使用不可变标签 `<版本>-<Git commit>`；已有同标签镜像不重建、按 revision 标签校验。
- 完整事务、回滚、NAS 前置条件与 GitHub 私有发布通道规划见 [docs/RELEASING.md](docs/RELEASING.md)。

## 项目结构

```
├── app.py               # HTTP 服务与全部 API（Python 标准库）
├── catalog.py           # 目录合并/同步/墓碑逻辑
├── collector.py         # 星轨采集器（/proc、/sys、Docker、探活、时序归档）
├── icon_library.py      # 图标上传/抓取/校验/去重
├── seed.json            # 内置默认目录（49 入口 / 7 分类）
├── compose.yaml         # 生产 Compose（只读根、资源限额、仅回环端口）
├── Dockerfile           # digest 固定的 python:3.12-alpine，内置健康检查
├── .env.example         # 环境变量示例（复制为 .env 使用）
├── static/              # 原生前端（app.js / styles.css / index.html / icons）
├── scripts/             # check / release / deploy_remote / 浏览器冒烟
├── tests/               # unittest 套件 + v1.0.0 目录 fixture
└── docs/                # 发布工程 / 目录热加载 / 图标管理 / 服务盘点
```

## 安全模型

- 会话 Cookie：Secure、HttpOnly、SameSite=Strict；登录、登出、改密即时清理会话。
- 全部写接口校验登录态 + CSRF + Origin；`HOMEHUB_ORIGIN` 是信任基线。
- 容器：只读根、`cap_drop: ALL`、`no-new-privileges`、tmpfs `/tmp`、资源限额、仅回环端口。
- 输入校验：上传类型/大小、图标路径白名单、SSRF 逐跳防护、SQLite 参数化查询。
- 提示：导航页的可见性控制是展示层的；各服务自身的登录与访问控制永远保留。

## 许可证

代码以 [MIT](LICENSE) 许可证开源。内置氛围音「无界」（`ambient.wav`）为作者自制作品，随仓库同许可分发；`static/icons/` 中的服务品牌图标仅用于标识对应服务的导航用途，版权归各自所有者。部署后你上传的音乐、图标与目录数据归你自己所有，不属于本仓库。

## 相关文档

- [CHANGELOG.md](CHANGELOG.md) — 版本历史（当前 1.8.0）
- [docs/RELEASING.md](docs/RELEASING.md) — 发布工程与回滚
- [docs/catalog.md](docs/catalog.md) — 目录与热加载
- [docs/icon-management.md](docs/icon-management.md) — 图标管理
- [docs/service-inventory.md](docs/service-inventory.md) — 服务盘点与目录校准记录

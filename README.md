# Homehub · 无界

个人艺术门户与服务导航。原生 HTML/CSS/JavaScript + Python 标准库 HTTP 服务 + SQLite；无前端构建工具、外部字体/CDN或运行时 Python 包依赖。

## 页面与交互

- 门户：`https://homehub.stream:8443/`
- 完整导航：`https://homehub.stream:8443/nav`
- 管理与登录：`https://homehub.stream:8443/admin`
- Logo 始终回门户；「全部服务」始终回导航首页并清除筛选。
- 站内采用 History API 切换；具体服务在新标签页打开，不中断背景音乐。
- 导航有分类、搜索（名称/说明/别名）、收藏、网格/列表。收藏、视图、静谧模式等是当前浏览器偏好；服务目录、分类、置顶、公开/私密、站点和音乐设置保存到 NAS 服务端。
- 手机使用三列紧凑网格；小屏和系统大字号仍应以可读性为先。
- 背景音乐默认安装自创的 48 秒合成氛围音「无界」，不是邓紫棋歌曲录音。首次访问不自动播放。音乐灵感说明不意味着歌曲音源授权。

## 部署位置

NAS `/vol1/1000/docker/homehub/` 是 Compose 项目和源码目录，不是 Docker 引擎目录。

- 容器：`homehub`，当前版本 1.1.0；发布镜像使用 `homehub-portal:<版本>-<Git commit>` 不可变标签，Python 基础镜像摘要固定。
- 端口：**仅回环** `127.0.0.1:48080` → 容器 `8080`。
- 持久化：`data/homehub.db`（管理员哈希、会话、分类、入口和设置）；`data/media/`（背景音乐）。SQLite 使用 WAL；不要仅热复制一个 DB 文件作完整备份。
- UID/GID：`1000:1000`；宿主机 data 目录必须归该身份且权限 700。
- 1Panel OpenResty 负责 HTTPS，应用不持有证书。
- `HOMEHUB_ORIGIN` 必须与对外源一致，用于跨站请求防护。生产 Cookie 为 Secure、HttpOnly、SameSite=Strict，不要用 HTTP IP 地址测试浏览器登录。

## 第一次登录

用户名：`astral`。初始随机密码只在 NAS 持久化数据目录中生成，不放入源码、种子配置或文档。

在 **1Panel → 容器 → homehub → 终端** 中执行：

```sh
python /app/app.py admin-password
```

或者通过 NAS SSH：

```sh
docker exec homehub python /app/app.py admin-password
```

请在自己的终端读取，不要把密码粘贴到聊天或文档。登录后到「账户与备份」修改密码（至少 12 字符）。修改后初始密码文件会删除，其他设备会话失效，当前会话保留。

忘记密码时，仅 NAS 管理员可在终端执行：

```sh
docker exec homehub python /app/app.py reset-password
```

这会生成新密码并注销所有会话。

## 维护导航

登录后进入「管理」：

- 服务管理：增加/编辑/删除、设置分组、排序数值、置顶、搜索别名和可见性。
- 分类管理：增加/编辑/排序；分类内有入口时不可直接删除。
- 站点设置：标题、副标题、音乐标题，上传自己有权使用的 MP3/OGG/WAV/M4A（最多 25 MiB）或填写 HTTPS 音源地址。
- 账户与备份：修改密码、导出 JSON、确认后导入恢复。

1.1.0 目录共 **41 个入口**：原有 21 个 HTTPS 站点、6 个内网应用、14 个飞牛原生/系统入口。按用户确认，默认目录全部公开展示，应用自己的登录保护不变；管理员仍可把个别入口设为私密。导航显示目录总数和当前可见数量，有私密入口时会提示登录后查看，避免误以为漏录。

音乐服务明确命名 **Navidrome**，与「飞牛音乐」区分。带「仅内网」标记的入口需要能访问家庭 LAN。5 个带「飞牛启动」标记的应用依赖 fnOS 启动票据：卡片提供说明和「打开飞牛桌面」，请从桌面/应用中心启动，不构造 token 或绕过鉴权。

完整逐项映射见 [服务盘点](docs/service-inventory.md)：15 个容器、30 个已安装原生应用及路由器/系统功能均有对应入口或明确的共享/非 Web 原因。数据库、缓存、API、文档预览和运行时不伪造独立首页；已下线服务、notify、未开发的 status 不录入。门户本身由顶栏访问，不重复凑卡片。

隐藏导航不是后端鉴权；各服务原有登录/访问控制仍须保留。内网 HTTP 链接只能在可达该内网的设备中使用。

## 备份与恢复

页面 JSON 导出包含分类、导航与站点设置（包括音频地址），**不包含管理员密码、会话或音频文件内容**。可用来恢复导航，不是完整服务备份。

完整备份请先短暂停止本服务后复制整个 data 目录，或使用 SQLite 在线备份 API 备份数据库并额外备份 media。不要复制正在写入的数据库单文件而忽略 WAL。

导入限制 32 MiB，最多 100 个分类、2000 个入口。失败导入回滚，不应破坏当前数据。

## 开发、发布与回退

本目录是独立 Git 仓库，main 为主分支，v1.0.0 保留最初上线基线。前端源文件已格式化；功能修改应先在分支开发、运行完整检查、审查 diff、提交，再打包发布。运行数据、NAS 备份、凭据、Git 元数据和生成截图不进入发布镜像或源码提交。

在本地仓库根目录运行：

```sh
python3 scripts/check.py
# 审查并提交代码，使工作区干净后：
python3 scripts/release.py package --output /tmp/homehub-release
# 用上一条命令实际打印的 manifest 路径：
python3 scripts/release.py deploy --manifest /tmp/homehub-release/<manifest>.tar.json --dry-run
python3 scripts/release.py deploy --manifest /tmp/homehub-release/<manifest>.tar.json
```

标准发布会校验 SHA256、锁定部署、先构建不可变镜像，在 NAS 端在线备份 SQLite/音频/原代码，再仅替换 homehub 服务，核验后端和 HTTPS 门户/导航。失败自动回退代码和实际旧镜像。普通代码回退不覆盖数据库或音频，避免丢掉发布后的用户修改；恢复数据属于单独的有损操作。

导航使用稳定 catalog_key 和已见记录做增量同步：后续发布补充新默认入口，但保留已有名称、地址、分类、排序、私密设置及主动删除；不更改管理员密码、站点设置或会话。已有导航内容建议在管理页编辑，seed 主要用于首次安装和新增默认服务。`--publish-existing` 是本次经明确批准的一次性公开目录迁移，后续日常发布不要带这个参数。

详细命令、备份位置、回退与 GitHub 接入条件见 [发布说明](docs/RELEASING.md)。CI 已提供 push/PR 检查；GitHub 手动发布模板默认关闭，需你以后创建远程仓库、配置受信任的 self-hosted runner 和受保护环境后启用。**本轮没有创建远程仓库、推送代码、开放 NAS SSH 或安装 runner。** 首次推送前请检查 Git 作者名/邮箱，当前本地提交沿用 Git 自动识别的本机身份，未修改全局设置。

## 本地测试

```sh
python3 scripts/check.py
# 或单独运行：
python3 -m unittest discover -s tests -v
python3 -m unittest discover -s scripts -v
node --check static/app.js
# 可选：已安装 Chrome 时做真实公开页面浏览器验收（Node 22+）
node scripts/browser-smoke.mjs --url http://127.0.0.1:8080 --screenshots tests/screenshots/local
```

本地预览仅使用临时数据，不接触 NAS：

```sh
HOMEHUB_DATA="$(mktemp -d /tmp/homehub-preview.XXXXXX)" \
HOMEHUB_BIND=127.0.0.1 HOMEHUB_ORIGIN=http://127.0.0.1:8080 \
HOMEHUB_COOKIE_SECURE=false python3 app.py
```

开发 cookie 开关仅用于 HTTP 本地预览，生产不可关闭。

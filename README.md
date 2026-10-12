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

- 容器：`homehub`，镜像 `homehub-portal:1.0.0`，Python 基础镜像摘要固定。
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

初始录入此前已部署的 21 个 HTTPS 站点，另有 Beets、miair-next、Jellyfin、AMMDS、指尖 NAS、FnMessageBot 6 个已核实的内网入口。已下线服务、notify、未开发的 status 不录入。管理、下载、个人生活和内网入口默认私密；登录后才能看到完整 27 个入口。访客仅看到公开子集。门户自身有顶栏链接，不必重复录入为卡片。

隐藏导航不是后端鉴权；各服务原有登录/访问控制仍须保留。内网 HTTP 链接只能在可达该内网的设备中使用。

## 备份与恢复

页面 JSON 导出包含分类、导航与站点设置（包括音频地址），**不包含管理员密码、会话或音频文件内容**。可用来恢复导航，不是完整服务备份。

完整备份请先短暂停止本服务后复制整个 data 目录，或使用 SQLite 在线备份 API 备份数据库并额外备份 media。不要复制正在写入的数据库单文件而忽略 WAL。

导入限制 32 MiB，最多 100 个分类、2000 个入口。失败导入回滚，不应破坏当前数据。

## 更新与回退

更新前备份源码和数据，仅重建本服务：

```sh
cd /vol1/1000/docker/homehub
docker compose build homehub
docker compose up -d --no-deps homehub
```

镜像使用固定本地 tag，不依赖远程同名镜像。不要把本项目的数据库、初始密码或完整备份复制进本地源码仓库。

回退新站点时，从 1Panel 删除主域名对应的这个网站（不要删除 wildcard 证书或 OpenResty 应用），停止 homehub，保留 data 备份。其它站点不需要改动。

## 本地测试

```sh
python3 -m unittest discover -s homehub/tests -v
node --check homehub/static/app.js
```

本地预览仅使用临时数据，不接触 NAS：

```sh
HOMEHUB_DATA="$(mktemp -d /tmp/homehub-preview.XXXXXX)" \
HOMEHUB_BIND=127.0.0.1 HOMEHUB_ORIGIN=http://127.0.0.1:8080 \
HOMEHUB_COOKIE_SECURE=false python3 homehub/app.py
```

开发 cookie 开关仅用于 HTTP 本地预览，生产不可关闭。

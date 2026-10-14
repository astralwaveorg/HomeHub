# 目录与热加载

Home Hub 的服务目录有两个来源：

| 来源 | 路径 | 谁维护 | 何时变化 |
| --- | --- | --- | --- |
| 内置目录 | 镜像内 `/app/seed.json`（随版本发布） | 发布流程 | 新版本上线时 |
| 覆盖目录 | NAS `data/seed.json`（容器 `/data/seed.json`，可选） | 站点所有者 | 随时，无需发布 |

最终目录 = 内置目录 **合并** 覆盖目录（按键 `catalog_key`，覆盖目录优先），再同步进 SQLite。

## 覆盖文件长什么样

只需要写要新增的内容，`categories` 可以是空数组；服务用 `category_key` 指向任意已知分类，就不必自己编号：

```json
{
  "settings": {},
  "categories": [],
  "links": [
    {
      "catalog_key": "website:newthing",
      "category_key": "category:music",
      "name": "新服务",
      "url": "https://new.homehub.stream:8443/",
      "description": "一句话说明",
      "icon": "music",
      "visibility": "public",
      "rank": 99,
      "aliases": "搜索别名 空格分隔",
      "launch_mode": "direct"
    }
  ]
}
```

- `catalog_key`：稳定身份，`[a-z0-9][a-z0-9_.:-]*`，一旦用过不要再改（改键等于新服务）。
- `category_key`：必须是内置目录里已有的分类键（`category:media` / `category:music` / `category:files` / `category:life` / `category:downloads` / `category:system` / `category:apps`），或在同一文件的 `categories` 里一并声明。
- 想新建分类时，在 `categories` 里给出 `{"id":1,"name":"自制分类","rank":5,"catalog_key":"category:custom"}`，服务再用 `"category_key":"category:custom"`。
- `launch_mode`：`direct`（直接跳转）或 `fnos`（需要飞牛启动票据时显示桌面指引）。
- 也支持整份 `seed.json` 复制后编辑——合并时同样按键取并集。

## 生效方式

1. **直接改文件**：保存 `data/seed.json` 后，门户的下一次请求（约 1 秒防抖）自动检测、校验、合并并写库。健康检查、首页、星图刷新即可看到。
2. **浏览器上传**：管理 → 账户与备份 → 目录数据 → 选择 `seed.json` → 上传并应用（≤1 MiB）。上传前会先做一次合并预演，失败不会写入。
3. **手动重同步**：同一面板的「立即重新同步」；状态行显示来源、条目数、上次应用时间和待应用/错误状态。
4. **命令行**：`docker exec homehub python /app/app.py sync-catalog`（发布脚本用的就是这个，未读取覆盖文件以外的输入）。

「移除自定义目录」会把 `data/seed.json` 改名为 `data/seed.json.bak` 并回到内置目录。

## 同步语义（重要）

同步是**增量且尊重所有者**的，覆盖目录不能用来「远程改别人的设置」：

| 情况 | 结果 |
| --- | --- |
| 新的 `catalog_key` | 新增服务 |
| 已存在同键服务 | 保留现有名称、地址、图标、可见性、排序；仅在名字仍是旧 `legacy_names` 时改名 |
| 同名 URL 但键不同、且该行还没有键 | 认领（adopt）为这个键 |
| 你删除过的键 | 记在 `catalog_registry`，不会被重新加回（`skippedDeleted`） |
| 你自己删除的服务 | 不会因合并复活 |
| 分类 | 已有分类保留你的名称/排序；只补建缺失分类 |
| 设置（标题/音乐等） | 只在**全新安装**时使用内置 + 覆盖文件的 `settings`；之后一切以数据库为准 |
| 覆盖文件被删除 | 停止合并，已进入门户的服务保留（不会被自动删除） |

需要真正删除入口时，请在「服务管理」里删除——那是所有者的决定，发布和合并都不会覆盖它。

## 失败与安全

- 覆盖文件必须是普通文件（拒绝符号链接），≤1 MiB，UTF-8 JSON；`categories` / `links` 必须是数组。
- 校验失败（JSON 语法、键格式、`category_key` 找不到分类、URL 重复等）时记录错误并保持当前目录不变；面板与日志（`docker logs homehub`）会显示原因。相同内容不会反复重试，改文件后才再尝试。
- 写入 `data/seed.json` 使用临时文件 + `os.replace`，权限 `0600`，并保留上一个版本为 `data/seed.json.bak`。
- 接口：`GET /api/catalog`（状态）、`POST /api/catalog`（强制重同步）、`POST /api/catalog/seed`（上传）、`DELETE /api/catalog/seed`（移除），全部需要登录，写操作还要 CSRF 与 Origin 校验。

## 备份

发布备份会一并复制 `data/seed.json` 与 `data/seed.json.bak`（若存在），并把文件名记入 `runtime.json`。普通回退只恢复代码/镜像，不覆盖数据；覆盖文件不是镜像内容，回退代码不会丢。

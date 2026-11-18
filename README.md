# bili-dynamics-3

用 [bilibili-api-python](https://github.com/Nemo2011/bilibili-api) 增量抓取指定 UP 主的**图文动态**，下载原图，并用本地 WebUI 浏览。

这是 v3 重写版：元数据从易损坏的 `__info.json` 迁移到 SQLite；修复了老格式图文、充电专属动态崩溃的问题；新增断点续传、失败重试、本地图片校验、缩略图和仿 B 站动态页的画廊。

## 功能

- 增量同步：只抓比本地最新 dynamic_id 更新的动态
- 全量补齐：`--full` 可重新抓取历史（不删除已有数据）
- 老格式图文：兼容 `major.draw.items[].src`
- 新版图文：兼容 `major.opus.pics`
- 充电专属：有权限正常下载；无权限入库为占位，不崩溃
- 转发：默认跳过，`--include-forwards` 可选择解析原动态图片
- 每页一个事务：中断最多丢一页，下次从断点继续
- 单条坏动态 / 单个坏用户不会终止整批任务
- 图片原子写入（`.part` → 正式文件），超时、重试、404/403 状态机
- `verify` 校验本地文件；`retry` 补下失败/缺失/待下载图片
- SQLite 主库 + 每用户 v3 JSON 导出（`data/exports/`）
- 仿 B 站动态风格的 WebUI：作者侧栏、动态卡片、图片网格、灯箱、深色模式、无限滚动、搜索
- 按需生成 WebP 缩略图，列表页不直接加载全部原图

## 安装

需要 Python 3.11+。

```bat
:: 在项目根目录执行
python -m venv .venv
.venv\Scripts\python -m pip install -U pip
.venv\Scripts\pip install -r requirements.txt
```

如果直连 PyPI 失败，可以换清华镜像：

```bat
.venv\Scripts\pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

如果系统里已经有 `bilibili-api-python`、`aiohttp`、`Pillow`，也可以用：

```bat
python -m venv --system-site-packages .venv
.venv\Scripts\pip install Flask python-dotenv pytest
```

下面的 `python -m bili3 ...` 默认在**项目根目录、已激活 `.venv`** 的终端里执行；不想激活也可以把 `python` 换成 `.venv\Scripts\python.exe`。`.cmd` 脚本会自动优先使用项目内的 `.venv`。

## 配置

1. 复制 `config.example.toml` 为 `config.toml`（可选，不写就用默认值）
2. 复制 `.env.example` 为 `.env`，填入 B 站 Cookie：

```dotenv
BILI_SESSDATA=...
BILI_JCT=...
BILI_BUVID3=...
```

> `.env` 含登录凭证，不要提交、不要发给别人。`cookie.py` 这种把凭证写进源码的方式在 v3 中不再使用。

3. 准备 `user_list.txt`：

```text
昵称: uid
另一个昵称: 123456
```

也支持只写 uid。

## 从 v2 迁移

v2 的用户和图片可以迁移到当前库。图片会搬到：

```text
data/images/<uid>_<name>/
```

数据库中的 `users.image_dir` 使用相对路径。v2 的 `opus/` 下只剩 `run_webui.py`、`__pycache__` 等非用户图片文件，可以按需保留或删除。

如果以后还要迁移另一份 v2 数据：

```bat
.venv\Scripts\python -m bili3 migrate --src "C:\path\to\bili-dynamics-2" --move
```

迁移是幂等的；旧图片目录会被移动到 `data/images/<uid>_<name>`，数据库路径自动更新。迁移完成后可在 `stats` 命令或 WebUI 中查看用户、动态和图片数量。

## 日常使用

```bat
:: 增量同步 user_list.txt 里的所有用户
python -m bili3 sync --all

:: 只同步一个用户
python -m bili3 sync --uid 123456789

:: 抓全部历史，只写元数据，不下载图片（适合先补齐历史）
python -m bili3 sync --uid 123456789 --full --no-download

:: 测试：只抓 1 页，最多下 2 张图，使用独立数据目录
python -m bili3 sync --uid 123456789 --max-pages 1 --max-images 2 --data-dir testdata-live

:: 只抓取解析，不写库不下载
python -m bili3 sync --uid 123456789 --max-pages 1 --dry-run

:: 重试失败/缺失/待下载图片
python -m bili3 retry --all --max-images 20

:: 校验本地文件；--repair 会顺便补下载
python -m bili3 verify --repair --max-images 20

:: 统计
python -m bili3 stats

:: 导出每用户 JSON
python -m bili3 export

:: 数据库损坏/丢失时，从 data/exports 重建
python -m bili3 import-json --dry-run
python -m bili3 import-json
```

### 翻页间隔与风控

默认 `sleep = 5.0` 秒，带 ±20% 随机抖动。B 站前段时间对这类爬虫限制较严，实测 2 秒也可能被限制，所以默认保持 5 秒。可用 `--sleep 3` 或 `config.toml` 调整，但不建议低于 3 秒。

遇到 `-352` / `429` 会按 60s、120s、240s 退避重试；`-412`（IP 被限制）会停止当前用户并提示稍后重试。

## WebUI

```bat
python -m bili3 serve
```

启动后访问 <http://127.0.0.1:5000/>。也可以双击 `a_preview_app.cmd`。

页面：

- `/`：全部动态，按时间倒序，无限滚动
- `/user/<uid>`：作者页
- `/search?q=...`：按标题/描述搜索，可按作者筛选
- `/thumb/<id>`：按需生成的 WebP 缩略图
- `/media/<id>`：原图（灯箱、下载）

缩略图缓存在 `data/thumbnails/`，可以随时删除，会自动重建。WebUI 只监听 `127.0.0.1`，不会暴露到局域网。

## 数据目录

```text
data/
  bili.db                 SQLite 主库（WAL）
  images/<uid>_<name>/    新下载的图片
  thumbnails/             WebP 缩略图缓存
  exports/<uid>_<name>.json  每用户 v3 JSON 备份
  logs/                   每次运行的 UTF-8 日志
```

数据库表：

- `users`：uid、昵称、头像、图片目录、最新 dynamic_id、上次同步时间
- `posts`：动态 ID、类型、标题、描述、发布时间、是否充电、图片数
- `images`：URL、文件名、尺寸、本地大小、状态（`pending/ok/missing/failed/dead/restricted`）、重试次数
- `sync_runs`：每次用户同步的页数、新动态数、新图片数、状态

`data/exports/` 是给人类看和灾备用的，可以重新导入；日常运行只读 SQLite。

## 图片状态

| 状态 | 含义 | 后续动作 |
|---|---|---|
| `pending` | 待下载（`--no-download` 或达到上限） | `retry` |
| `ok` | 本地存在 | `verify` 校验 |
| `missing` | 数据库有记录但本地无文件 | `retry` / `verify --repair` |
| `failed` | 下载失败，未到上限 | `retry` |
| `dead` | 404/410，CDN 已删除 | 不再重试 |
| `restricted` | 401/403，可能无权限 | 不再自动重试 |

v2 里遗留的失败记录现在都是 404，迁移后会被 `retry` 标记为 `dead`，不会无限重试。

## 测试

```bat
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\pytest
```

测试不会访问 B 站、不会下载大量图片：

- 解析器 fixtures：新图文、老图文、充电无权限、充电可访问、转发、`pics=null`、缺字段
- 迁移测试：临时 v2 目录，幂等性、缺失文件
- 数据库测试：重复 upsert、搜索、分页
- Web 测试：首页、作者页、搜索、缩略图、路径穿越

真实接口冒烟只使用单用户 + `--max-pages 1` + 最多 2 张图片，并写入 `testdata-live`，不会污染正式库。

## 与 v2 的关系

- v3 不兼容 v2 的 `__info.json`；用 `migrate` 转换
- v3 不使用 v2 的 `cookie.py`；改用 `.env`
- v3 不修改 v2 数据；默认引用图片目录
- v2 的 `a_download_all.cmd` / `a_re_download_all.cmd` / `a_preview_app.cmd` 在 v3 中保留了同名包装脚本，底层换成新 CLI

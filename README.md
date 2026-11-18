# bili-dynamics-3

用 [bilibili-api-python](https://github.com/Nemo2011/bilibili-api) 增量抓取 B 站 UP 主的图文动态，下载原图，并通过本地 WebUI 浏览和管理。

v3 以 WebUI 为唯一日常入口：批量同步、重试失败图片、下载指定动态、刷新头像、修改 Cookie 都在网页里完成，不再需要外部 `.cmd`。

## 功能

- 用户列表存在 SQLite 中，可启用/停用、批量选择、增删用户
- 批量同步启用用户；支持增量 / 全量历史 / 只抓元数据 / 限制页数和图片数
- 重试 `pending / failed / missing` 图片；404/403 不再无限重试
- 输入动态 URL/ID，预览后下载指定图文动态
- 本地头像：下载后转成 160×160 WebP，WebUI 不再依赖 B 站外链
- Cookie、用户设置、头像、图片、任务日志全部在 `data/` 下
- 后台任务队列：进度、日志、取消、历史记录；同一时间只跑一个重任务
- SQLite 主库 + WAL + 每页事务；数据库升级前自动备份
- 仿 B 站动态页的画廊：侧栏、卡片、图片网格、灯箱、深色模式、无限滚动、搜索
- 按需生成 WebP 缩略图，列表页不直接加载全部原图
- 修复老格式 `major.draw`、充电 `MAJOR_TYPE_UPOWER_COMMON`、`pics=null`、转发 `major=null` 等崩溃场景

## 安装

需要 Python 3.11+。

```bat
:: 在项目根目录执行
python -m venv .venv
.venv\Scripts\python -m pip install -U pip
.venv\Scripts\pip install -r requirements.txt
```

如果直连 PyPI 失败：

```bat
.venv\Scripts\pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

## 启动

```bat
:: 在项目根目录执行
.venv\Scripts\python -m bili3
```

不带子命令时默认启动 WebUI，并自动打开浏览器。也可以显式：

```bat
.venv\Scripts\python -m bili3 serve --port 5000
```

不想显示控制台时：

```bat
.venv\Scripts\pythonw.exe -m bili3
```

服务默认只监听 `127.0.0.1`，使用 waitress，不会暴露到局域网。没有提供任何 `.cmd` 启动脚本或操作脚本。

## 首次使用

1. 启动 WebUI，进入 **设置**。
2. 填写 B 站 Cookie：`SESSDATA`、`bili_jct`、`buvid3`，点“保存并验证”。
3. 点“测试连接”确认登录状态；旧 `.env` 导入的 Cookie 可能已经过期。
4. 进入 **用户**，添加 UID 或 `space.bilibili.com/...` 链接。
5. 点“同步全部启用用户”，在 **任务** 页查看进度和日志。
6. 在 **指定动态** 页可以单独下载一条图文动态。

### 旧用户数据迁移

如果项目根目录还存在旧版 `.env`、`user_list.txt` 或 `config.toml`，首次启动会自动处理：

- Cookie 导入到 `data/config/cookies.json`
- `user_list.txt` 导入数据库，并设置用户的启用/停用状态
- 设置导入 `data/config/settings.json`
- 原文件移动到 `data/legacy/` 备份，不再留在项目根目录

## 数据目录

```text
data/
  bili.db                     SQLite 主库
  config/
    settings.json             同步/Web 参数（无敏感信息）
    cookies.json              B 站 Cookie（不进 Git、不被 Web 暴露）
  avatars/                    本地头像 WebP
  images/                     全部用户图片（已从 v2 搬入）
  thumbnails/                 WebP 缩略图缓存
  exports/                    每用户 v3 JSON 备份
  logs/
    jobs/                     每个后台任务日志
  legacy/                     旧版 .env / user_list 等迁移备份
  backups/                    数据库升级前备份
```

`data/` 已加入 `.gitignore`。Cookie 文件默认是本地明文，仅依靠目录权限和“不被 Web 静态目录暴露”保护；如果需要对磁盘上的 Cookie 做 Windows DPAPI 加密，可以再单独加一层。

## WebUI 页面

- **动态**：全部图文、作者侧栏、搜索、无限滚动、灯箱；顶部显示当前任务
- **用户**：添加用户、启用/停用、批量同步、重试选中用户、刷新头像
- **任务**：任务队列、进度条、状态、日志尾部、取消
- **设置**：Cookie、同步参数、校验/补下、头像补全、导出 JSON
- **指定动态**：URL/ID → 预览 → 下载；可选加入用户列表

用户页的“移出列表”只是停用，不会删除该用户的动态和图片。

## 头像

- 同步用户时会从 `module_author.face` 更新头像
- 设置页“补全所有头像”会对没有本地头像的用户调用用户信息接口
- 头像保存为 `data/avatars/<uid>_<hash>.webp`，160×160
- WebUI 统一请求 `/avatar/<uid>`；没有本地文件时返回按 UID 着色的 SVG 初始头像

## 命令（高级/迁移/灾备）

```bat
:: 同步启用用户
.venv\Scripts\python -m bili3 sync --all

:: 只同步一个用户
.venv\Scripts\python -m bili3 sync --uid 123456789

:: 全量历史，只抓元数据（例如补某个用户的历史）
.venv\Scripts\python -m bili3 sync --uid 123456789 --full --no-download --max-pages 100

:: 重试图片
.venv\Scripts\python -m bili3 retry --all

:: 校验本地图片
.venv\Scripts\python -m bili3 verify --repair

:: 统计
.venv\Scripts\python -m bili3 stats

:: 导出/重建 JSON 备份
.venv\Scripts\python -m bili3 export
.venv\Scripts\python -m bili3 import-json --dry-run
```

这些命令保留给迁移、灾备和调试；日常操作不需要使用。

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

## 安全与风控

- 默认 `sleep = 5.0` 秒，带 ±20% 随机抖动；用户之间也暂停
- `-352 / 429` 按 60s、120s、240s 退避；`-412` 会停止当前用户
- Cookie 不回显完整值，不写日志，不进 JSON 导出
- 所有修改类 API 要求 `X-Bili3: 1` 请求头，降低本地 CSRF 风险
- WebUI 默认只监听 `127.0.0.1`

## 测试

```bat
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\pytest
```

覆盖解析器旧/新/充电/转发、SQLite 迁移、用户启停、任务队列/取消、头像下载、导出恢复、Web 页面/CSRF/路径穿越等。测试默认不访问 B 站、不下载大量图片。

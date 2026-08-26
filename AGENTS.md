# AGENTS.md — OwK 助手记忆库（Agent Memory）

> **本项目以本文件作为"无状态恢复"的唯一记忆源。**
> 任何新会话的助手：**第一步读本文件**，即可恢复完整上下文并立即工作。
> 工作过程中产生的新认知（决策 / 约定 / 坑）必须追加到 §7 更新日志并同步正文。
> 本文件已纳入 git，丢失时可从 git 历史恢复。

---

## 0. 会话恢复清单（Stateless Recovery Checklist）

新会话开始，按此清单执行即可恢复状态：

1. 读完本文件（这是唯一记忆源）。
2. **建/改文件一律用 bash（cat heredoc / printf）直接写**，勿用内置写工具；git add/commit 正常。
3. 需要启动服务：`python start.py`（依赖/端口见 §4；无 venv，用系统 Python 3.13）。
4. 需要搜索/下载 B 站内容：先通过 `login_server.py` 二维码登录拿到 `bilibili_cookie.json`（§3.5）；**已登录**（bilibili_cookie.json 已存在，见 §6.1）。
5. 动手前先 `git status` / `git log` 确认当前进度（仓库目前只有 initial commit + AGENTS.md 提交）。
6. 前端是纯静态无构建；Android 端需 Gradle 构建（无 local.properties，未配置）。
7. 改完代码后：若新增认知，按 §7 格式追加到本文件，并 `git add AGENTS.md && git commit`。

---

## 1. 项目概览

- **名称**: OPENwebKTV（简称 **OwK**）— 基于 B 站的局域网 KTV（点歌/播放）系统
- **仓库**: git@github.com:wzmwayne/OPENwebKTV.git（分支 `main`）
- **定位**: 家庭/聚会场景，电视/大屏播放器 + 手机网页控制器 + 可选 Android TV 壳应用
- **语言/注释**: 代码与界面均为中文；log 名统一用 `owk.*` 前缀
- **版本**: 前端显示版本 0.1.0（无正式版本管理文件）

### 核心数据流
1. 手机控制器（controller.html）通过 B 站搜索点歌
2. 后端自动下载 B 站视频（DASH 音视频分离 → ffmpeg 合并为 MP4）到本地
3. 播放器页（player.html，电视/大屏浏览器或 Android WebView）通过 WebSocket 接收播放指令
4. 后端 PlayerEngine 维护全局队列，向播放器/控制器双向广播状态

---

## 2. 目录结构与职责

```
OPENwebKTV/
├── start.py                     # 项目根启动入口 → chdir backend 后执行 run.py
├── README.md                    # 项目说明: 优点对比/功能/部署/FAQ/免责声明
├── AGENTS.md                    # 本记忆文件（已入 git）
├── backend/                     # Python FastAPI 后端
│   ├── run.py                   # 启动脚本: 端口探测(8080/回退8080)、打印局域网地址、启动 uvicorn;
│   │                            #   端口被占时列出占用程序+PID(ss/lsof//proc inode 多路兜底)
│   ├── login_server.py          # 独立 B 站二维码登录服务器 (端口 8888，另起进程)
│   ├── requirements.txt         # fastapi/uvicorn/httpx/sqlalchemy/aiosqlite/pydantic/python-multipart
│   ├── data/                    # 运行时生成(被 gitignore): openwebktv.db + media/ 下载目录（当前尚不存在=从未启动过）
│   ├── bilibili_cookie.json     # B 站登录凭证(被 gitignore)（已存在=已登录，2026-08 确认）
│   ├── app/
│   │   ├── main.py              # FastAPI 入口: CORS 全开、启动时建库+队列去重+启动轮询、挂载前端静态
│   │   ├── config.py            # Settings: HOST=0.0.0.0 PORT=8080 DB/MEDIA/FRONTEND 路径, MAX_QUEUE_SIZE=50
│   │   ├── database.py          # SQLite (aiosqlite 未实际用于异步; 用 sqlalchemy 同步引擎), SessionLocal, Base
│   │   ├── models.py            # Song / QueueItem / Playlist / PlaylistSong
│   │   ├── schemas.py           # SongOut / QueueOut / PlaylistOut / PlayState / 请求体
│   │   ├── player_engine.py     # 全局单例 PlayerEngine: 队列状态机、播放控制、后台轮询任务
│   │   ├── ws_manager.py        # 全局单例 WSManager: 播放器/控制器两套连接池 + 定向/广播
│   │   ├── bilibili.py          # BilibiliClient: 搜索/详情/字幕歌词/播放地址/DASH下载+ffmpeg合成, 下载进度跟踪
│   │   └── routers/
│   │       ├── api.py           # 全部 REST 路由 (/api/*)
│   │       └── ws.py            # /ws/player 与 /ws/controller 两个 WebSocket 端点
│   ├── test_play_server.py     # 播放测试服务器(端口8090): 连接即播第一首, 播完自动切第二首, 用于隔离"切歌无声音"问题; 媒体在 data/test_media/(song1=蓝屏440Hz, song2=红屏880Hz)
│   ├── test_bilibili_api.py     # B 站 API 集成测试(直连验证各接口)
│   ├── test_module.py           # bilibili.py 模块功能测试(搜索/详情/歌词/播放地址/下载)
│   └── test_merge.py            # download_video merge=True 合成功能测试
├── frontend/                    # 纯静态前端(后端 StaticFiles 挂载)
│   ├── player.html              # 播放器页(全屏视频、空闲屏、二维码、状态徽章)
│   ├── controller.html          # 手机控制器页(下载/点歌/播放/设置 4 个 Tab)
│   └── js/ws_client.js          # WsClient 通用 WS 封装(自动重连 3s)
└── player-android/              # Android TV 壳应用 (Kotlin, minSdk 24, targetSdk 34)
    └── app/src/main/java/com/owk/player/
        ├── ConnectActivity.kt   # 连接页: 历史记录(DPAD可删)/局域网扫描/端口扫描/输入IP
        ├── MainActivity.kt      # 全屏 WebView 加载 player.html(沉浸式)
        ├── PortScanner.kt       # 常见端口(8080,8000,80,3000,5000,8888,9090)探测
        └── ServerDiscovery.kt   # 同网段 /24 扫描(并发30, 端口8080/8000)
```

---

## 3. 关键设计约定

### 3.1 播放状态机 (PlayerEngine)
- 状态: `idle` → `playing` ⇄ `paused`；另有 `blocked`(高级操作验证: 阻塞播放端, 不自动起播,
  点歌被拒 403「系统维护中」)
- **空队列行为**: `play()` 加载不到下一首时(状态非 idle)置为 `idle` 并**仅在此转换时**
  broadcast_state 通知前端; `_poll_loop`(1s, status==idle 门槛)持续探测 → 新点歌自动起播,
  与启动时行为一致。曾存在"队列播完后 status 卡在 playing、探测循环失效"的 bug(已修)
- 队列项状态: `waiting` → `playing` → `played`（played 用于"上一首"回溯）
- 单例 `player_engine` 持有内存态 `current_song/status/position/volume`；队列落库 SQLite
- 后台两个常驻任务: `_poll_loop`(1s 空闲时自动起播) 和 `_broadcast_dl_loop`(2s 广播下载进度)
- `get_state()` 返回 PlayState{current, queue, status, position, volume}

### 3.2 WebSocket 协议（JSON，type 字段分发）
| 方向 | type | 内容 |
|---|---|---|
| 后端→播放器 | play/pause/resume/seek | song 对象 / 无 / 无 / position |
| 后端→控制器 | state / position / download_progress / volume | 完整状态 / {position,duration} / {downloads} / {volume} |
| 后端→全部 | volume | {volume} |
| 后端→全部 | admin | {phase, allowed, code_ttl, admin_remaining}; **播放端额外带 code(8位动态码)**, 控制端不带(防窃码) |
| 播放器→后端 | position_update / song_end / volume / seek | 进度上报 / 播完通知 / 音量 / 跳转 |
| 控制器→后端 | control{action} / seek / volume | play/pause/resume/next/prev / position / volume |
- 播放器**不做**本地状态机，一切以服务端指令为准（play 消息带 song 则换源加载）
- **播放器与控制器连接后都会立即收到一次完整 state**（ws.py 已补）;
  播放端若处于高级操作验证/授权阶段, 连接时额外补推一条带动态码的 admin 消息(中途重连场景)

### 3.3 REST API（前缀 /api）
- `GET /search?keyword=&page=` — B 站搜索（依赖 Cookie）
- `GET|POST /songs`、`GET /songs/{bvid}`、`GET /downloads`、`POST /download`
- `GET|POST /queue`、`DELETE /queue/{item_id}`、`PUT /queue/reorder`
- `POST /control/{play|pause|resume|next|prev}`、`GET /state`
- `GET|POST /playlists`、`DELETE /playlists/{id}`、歌单内歌曲增删、`POST /playlists/{id}/play`
- `GET /media/{song_id}` — 本地 MP4 流式播放；仅 `ready`/`audio_only` 状态可流(其余 404)，
  media_type 按扩展名(mp4/m4a/aac)区分
- `GET /api/lyrics/{bvid}?track=N&keyword=` — 歌词: track<0 时优先返回下载时存储的歌词
  (Song.lyrics 列), 否则取指定字幕轨(默认第一条); track -2=LRCLIB搜索词(keyword 参数或
  Song.search_keyword, 缓存键含关键词哈希) / -3=LRCLIB标题 / -1=无歌词/默认 / >=0=B站轨;
  内存缓存 LYRICS_CACHE
- `GET /api/cover?url=` — B站封面代理(带UA+Referer绕热链保护, 内存缓存, 仅允许hdslb/bilibili域名防SSRF)
- `GET /api/lyrics/tracks/{bvid}?keyword=&title=&duration=` — 歌词来源选项(同级, **全部始终显示**):
  B站轨(ai标记) + 第三方LRCLIB搜索词(-2) + 第三方LRCLIB标题(-3); 不可用来源带 `error` 字段
  (缺少搜索词/缺少歌曲标题/未找到歌词), 控制端暗红置灰禁止点击; title/duration 由控制端传搜索结果
  (未下载时无 Song 行)否则回退 Song 行; 搜索词回退 Song.search_keyword
- `POST /api/download|/queue` — 请求体含 `track`(字幕轨序号, 默认0): 下载视频时**同时下载该轨歌词**
  存入 Song.lyrics; 下载接口幂等(ready/downloading 直接返回)
- `POST /api/admin/verify` {static_password} — 高级操作静态密码验证(常量时间比较); 通过后
  **自动清空播放列表 + 播放端进入阻塞状态**并生成8位动态码(120s, 过期自动换新码)
- `POST /api/admin/code` {code} — 提交动态码(单次有效) → active(1分钟「是否允许高级操作」授权窗口)
- `GET /api/admin/status` — 全局状态 {phase: none|code|active, allowed, code_ttl, admin_remaining}(无动态码)
- `POST /api/admin/cancel` — 取消验证/授权, 播放端解除阻塞回空闲
- `POST /api/admin/queue/clear` — 清空播放列表(高级操作)
- `DELETE /api/admin/songs/{song_id}` — 删除本地歌曲: 数据库行+媒体文件+队列/歌单引用清理; 正在播放则停播;
  并清理该 bvid 的歌词内存缓存(LYRICS_CACHE 全部4类键, api.clear_lyrics_cache)
- **高级操作不做接口级鉴权**(用户指定, LAN 信任模型): 以 admin_auth 全局状态为准, 前端按状态门控
- `GET /api/login/status` — B站是否已登录 {logged_in, user_id}
- `GET /api/login/qr` — 生成B站登录二维码 PNG(播放端显示)
- `GET /api/login/poll` — 轮询扫码状态, 成功后自动保存 cookie 到 bilibili_cookie.json
- `GET /qr/controller` — 控制器二维码 PNG
- 点歌(`POST /queue`)与下载(`POST /download`)都调用 `_ensure_song`（首次按 bvid 建 Song 行），
  下载用 `asyncio.create_task(_run_download(...))` 后台执行

### 3.4 B 站下载管线 (bilibili.py)
- 依赖 **ffmpeg**（系统命令，非 Python 包；本机已装 7.1.5）
- 步骤: video_info → play_info(DASH, fnval=4048) → 选最高码率音频 + `pick_best_video()` 选视频
  → 流式下载 .m4s → remux 为 .mp4（修正 moov）→ 合并
  (兜底顺序: 1 `-c copy` 直封装 2 视频转 libx264+音频copy 3 全转码 H.264+AAC;
  全部失败回退返回音频文件; 仅音频场景转 .aac)
- **WBI 签名(关键)**: B站部分接口(如 /x/player/wbi/v2 字幕)必须 WBI 签名(wts+w_rid,
  nav 取 img/sub key → mixin 表), 未签名时 AI 字幕内容会**随机错乱**(实测同一视频5次返回4种
  不同内容); 已加 _wbi_sign 并缓存 key, subtitles() 用 wbi/v2, 失败兜底 v2
- **LRCLIB 歌词兜底(免费无key)**: lrclib.net 免费无限制(建议缓存), get/search 接口返回 LRC 同步歌词;
  标题解析 guess_song_meta(《歌名》+'-歌手'), parse_lrc 转 SubtitleLine(end=下一句start);
  clean_title_query 清洗标题(去【】与无损/高音质/MV等杂质词)。**fetch_lrclib_lyrics(title,dur,keyword,mode)**:
  mode auto(默认)=标题解析get→搜索词→原始标题 / keyword=搜索词优先 / title=标题提取优先;
  /api/lyrics与下载在B站无歌词时自动兜底; 控制端预览可同级显式选择 搜索词/标题/B站轨。已验证多场景
- **选流策略(关键)**: `pick_best_video` 优先 H.264(codecid=7, 浏览器兼容性最好)，同高选 AVC；
  无 AVC 流时退回最高分辨率并**强制转码 H.264**(跳过 `-c copy`)。
  旧逻辑只按分辨率选流会选中 HEVC/AV1 → Linux Chrome 等无 HEVC 解码 → 黑屏只有音乐
- **纯音频判定**: 下载完成后 ffprobe 探测有无视频轨，无则 `download_status="audio_only"`，
  前端显示封面兜底(见 §3.6)
- 进度: 全局 `_active_downloads` dict（bvid → {percent,status,title}），供 WS 广播与 `/api/downloads`

### 3.5 登录体系
- **主服务已集成登录**(app/routers/login.py): 未登录时播放端显示二维码+轮询, 控制端提示扫码;
  扫码确认后自动存 cookie。BilibiliClient 每次请求重读 cookie 文件 → **无需重启进程**
- **登录可靠性要点**: 持久 httpx 会话(自动保留 buvid3, poll 必须携带) + /api/login/qr 在
  QR_TTL=150s 内**复用同一 key**(防多端并发生成互相覆盖 → "一直等待扫码"根因之一);
  /qr/refresh 强制换新 key 并广播 login_refresh 给播放端; /logout 删除 cookie 并广播
- 独立工具 `login_server.py`(8888) 仍保留可用(控制器设置页按钮指向它)
- 流程: generate(拿 qrcode_key) → poll(轮询，data.code 0=成功/86101未扫/86090已扫待确认/86038过期)
- 成功后解析 Set-Cookie 存 `bilibili_cookie.json`（SESSDATA 为关键字段）

### 3.6 前端要点
- 无构建工具、无框架，原生 JS + 内联 CSS（深色主题，主色 #7c5cfc 紫）
- player.html: 空闲屏 `idleScreen` 动态文案 — 连接后收到空状态显示「请点歌播放」
  (`showIdle(msg)` 切换, 默认"等待连接..."); state(current=null)/play(song=null) 均触发;
  loadVideo 加载新歌时收起空闲屏; 空闲屏内含二维码(手机扫码点歌); 歌曲名固定在左上角
- player.html: video 元素直接 `/api/media/{id}`；`ontimeupdate` 每>1s 上报进度；
  `dur-cur<0.5s` 触发 song_end；下载未 ready 时 404 会 2s 后重试拉流(最多10次)；
  重试定时器有登记清理 + song_id 守卫(旧歌回调不污染新歌)；`audio_only` 显示封面层+音频模式角标
- **player.html 静音方案(已从测试服务器移植并验证)**: 先尝试有声播放(3次/300ms) → 全部失败
  则静音开播 + 底部闪烁提示「按 回车/OK/空格 解除静音」→ 按键解除(用户手势)。
  **屏蔽 video 组件自身键盘行为**: tabIndex=-1 + 元素级 keydown preventDefault + 切歌时 blur(),
  防止回车/空格触发组件原生暂停。
  **实时静音监测**: volumechange 事件 + 500ms 轮询, 只要 muted=true 就显示「按 回车/OK/空格 开启声音」;
  回车/OK/空格无条件开启(不再依赖 muteFallback 标志)。
  **防"有声音后自动静音"**: attemptPlay 重试链带 loadGen 代际 token + playRetryTimer 登记清理 +
  播放守卫(已播放 currentTime>0.3 绝不静音), 杜绝旧代链把正在播放的歌强制静音
- player.html: 未登录时全屏登录浮层(二维码 + 轮询 + 成功后3秒提示→location.reload 自动恢复)
- player.html: 高级操作阻塞覆盖层 #adminOverlay(z-12): code 阶段大号8位动态码+TTL倒计时,
  active 阶段显示授权倒计时; 到期/取消自动隐藏回空闲屏; state 的 admin 字段兜底重连;
  **授权结束(active→none)显示「高级操作已结束」2.5s 后自动刷新页面**
- **player.html 歌词(两行滚动)**: 行1在屏幕高度一半**居左**, 行2在行1下方**居右**, 多层**黑色阴影**
  (0 1px 2px/0 2px 6px/0 0 14px rgba黑); 焦点行=当前句(放大+纯白); 滚动规则(用户指定序列):
  偶数句焦点行1+行2滚入下一句, 奇数句焦点行2+行1滚入下一句
  (验证序列 -1A 2B → 1C -2B → -1C 2D → 1E -2D)。数据源 /api/lyrics/{bvid}
- controller.html: **未登录时全屏锁定覆盖**(z-99, 唯一操作=「刷新二维码」→ /api/login/qr/refresh 广播
  播放端同步换码; 无退出/无关闭按钮), 登录成功自动解锁+toast; 锁定层实时显示扫码状态
- controller.html: 4 Tab（下载/点歌/播放/设置）；队列支持 ▲▼/置顶/删除
- **controller.html 高级操作(设置页)**: 静态密码+播放端动态码双因子; 三步面板(none=输静态密码 →
  code=提示去播放端看8位码 → active=1分钟授权窗口); 授权期间可: 退出登录B站/清空播放列表/
  删除本地歌曲(点歌页本地歌曲旁出现 🗑, 仅授权期间可见); 1s 轮询 /api/admin/status 驱动倒计时;
  WS admin 消息同步状态; 到期自动回 none 按钮禁用; **授权窗口结束(active→none, 到期或取消)
  播放端与控制端都提示后 2.5s 自动刷新页面**(检测阶段转换, 启动广播不触发)
- **controller 下载逻辑**: 搜索自动拼接"输入词+空格+歌曲"; 结果顶置(标题含 音质/MV(不区分大小写)/
  成对书名号《》或「」, 置顶组与非置顶组各自保序); 点 ⬇ 进入**预览详情页**(z-50): B站官方 embed
  iframe(autoplay=0 不自动播放) + 视频详情 + 歌词列表 + 字幕轨选择(AI轨标注"(AI)", 可"无歌词") +
  底部下载按钮/WS 进度条; 左上角返回; 下载 POST /api/download {bvid, track(-1=不带歌词)};
  歌词来源**全部始终显示**(含第三方搜索词/标题), 不可用来源暗红背景(#5a1f1f)+disabled 禁止点击
  并显示原因, 默认选中第一个可用来源, 全部不可用则"无歌词"
- 前端无 XSS 防护框架，仅 `esc()` 转义标题类字段

---

## 4. 运行方式

```bash
# 主服务: **统一从仓库根目录 start.py 启动**(用户约定, 勿直接 cd backend && python3 run.py)
python3 start.py
# 预期输出: 内网/本机地址、player.html、controller.html 三个 URL，端口默认 8080

# B 站登录服务器（独立终端，端口 8888）
cd backend && python3 login_server.py
```

- 首次启动自动建 `backend/data/openwebktv.db` 与 `backend/data/media/`
- 无前端目录时后端会告警但不退出
- 依赖安装：`pip install -r backend/requirements.txt`（尚未创建 venv；如新建 venv 请放 `.venv/`，已被 gitignore）

## 5. 测试脚本（backend/ 下，均需有效 bilibili_cookie.json）
- `python test_play_server.py` — 播放测试服务器(8090), 无需 cookie; 页面 ?mode=direct 可对比取消静音策略
- `python test_bilibili_api.py` — 直连 B 站各接口验证
- `python test_module.py` — bilibili 模块功能（无 Cookie 直接退出）
- `python test_merge.py [关键词]` — 下载+ffmpeg 合成测试

---

## 6. 环境事实与坑

### 6.1 本机环境（2025 采集）
- Python 3.13.5（系统级，无 venv）；Node v22.23.2；ffmpeg 7.1.5 ✓
- `backend/data/`（SQLite 数据库 + media/ 媒体）与 `bilibili_cookie.json`（B站登录凭证）为本地运行生成，均被 gitignore；
  2026-08 确认：数据库已建、media/ 已有 9 个下载视频（原神系列/《玻璃》/《晚安》等）、cookie 已登录（user_id 3546822289131703）
- Android 构建未配置（无 `local.properties`，需 SDK 路径才能 gradle build）

### 6.2 已知注意点/潜在坑（重要）
1. **队列上限** MAX_QUEUE_SIZE=50，满了返回 HTTP 400 "队列已满(上限50首)"
2. **同曲去重**: 队列里同 bvid 重复点歌返回 409；启动时还会清理历史遗留重复项
3. **端口回退**: run.py 只尝试 [配置端口, 8080]，都不行则退出
4. **`_ensure_song` 对每个新 bvid 都实时调 B 站 API**（搜索/点歌/加歌单均会触发），无缓存
5. **下载与播放并行**: 点歌即后台下载，播放器对未 ready 文件会轮询重试（最多 10 次 / 2s 间隔）
6. **WebView 壳**: Android 端 player.html 的 video 走系统播放；`mediaPlaybackRequiresUserGesture=false`
7. **CORS 全开**，无鉴权——局域网信任模型
8. **登录服务器端口 8888 与主服务不同**，控制器 `openLogin()` 硬编码该端口
9. `models.py` 无迁移机制，靠 `create_all`；改表结构需手动处理旧库
10. SQLite 同步引擎 + 异步任务混合使用（SessionLocal 在 async 函数内直接调用），无 async SQLAlchemy
11. **黑屏只有音乐根因(已修)**: 下载选流只看分辨率→可能选 HEVC/AV1; 已改为优先 H.264, 无则强制转码
12. **audio_only 状态**: 无视频轨文件标记为 `audio_only`, 播放器显示封面; `stream_media` 非 ready 一律 404
13. **服务重启队列卡死(已修)**: 启动时 playing→waiting 复位
14. **`_ensure_song` 并发竞态(已修)**: 唯一约束 IntegrityError 已捕获回滚

---

## 7. 更新日志

- **2025-xx 首次**: 通读全部源码（backend 10 个 py 文件 + 3 个测试、frontend 3 个文件、player-android 6 个 kt/xml 文件），建立记忆。
- **2025-xx 迁移**: 记忆文件由 ASSISTANT_MEMORY.md 迁移至 **AGENTS.md** 并提交入 git；
  新增 §0 恢复清单与 §6.1 环境事实，确立"无状态会话读 AGENTS.md 即恢复"的机制。
- **2025-xx 黑屏修复**: 自动切歌"只有音乐没画面"根因=下载管线按分辨率选流可能选中 HEVC/AV1。
  修复: `pick_best_video` 优先 H.264 / 非 H.264 强制转码 / ffprobe 判定 `audio_only` /
  `stream_media` 仅 ready 可流+按扩展名 media_type / player.html 重试定时器清理+song_id 守卫+封面兜底 /
  播放器连接即推 state / 启动复位 playing→waiting / `_ensure_song` 竞态。
  已用真实 B 站视频验证: 产出 H.264 720p/1080p 文件, WS 自动切歌流程通过。
- **2025-xx 无声音排查(进行中)**: 黑屏修复后用户反馈"播放无声音"。
  已取证: 下载文件均为 AAC 音频(无杜比), B站音频流全为 mp4a.40.2, 文件侧无问题 → 指向播放路径。
  建 test_play_server.py(8090) 最小化复现: 连接即播 song1→ended→song2, 页面实时显示 muted/volume/play拒绝;
  用户要求自行启动测试服务, 待其反馈 muted/direct 两种模式的表现再定根因。
- **2025-xx 测试服务器演进**: 静音方案改为"先多次尝试有声播放(3次/300ms)→失败则静音开播+
  提示按键解除"；**TV 遥控器场景方向键不触发页面 keydown**, 解除静音改用 回车/OK/空格(回退态优先
  解除, 正常态=播放暂停)。已提交多版: 561ed98(静音回退方案), Enter/OK/空格绑定。
- **2025-xx 静音方案移植**: 测试服务器验证通过后移植到 player.html(commit 见下);
  同时屏蔽 video 组件自身回车/空格暂停(不可聚焦+元素级拦截+blur)。
- **2025-xx 自动静音修复**: 根因=attemptPlay 300ms 重试链无代际隔离, 旧代链存活到新歌播放期并触发
  startMutedFallback 强制静音。修复=loadGen 代际 token + playRetryTimer 清理 + 播放守卫 +
  volumechange/500ms 实时静音监测 + 回车/OK/空格无条件开启声音。
- **2025-xx 需求放弃记录**: 用户曾要求"端口被占用时询问 Y/n 解除占用 + --kill-port 参数",
  已实现一版(含 /proc inode 解析 PID 的 release_port)后**用户主动放弃**, run.py/start.py 已回滚还原。
  **不要重新实现**。
- **2025-xx 空闲状态修复**: 根因=队列播完后 `play()` 早退不置 idle, `_poll_loop` 门槛
  status==idle 永不满足 → 探测循环失效(新歌不自动播、前端收不到空队列通知)。
  修复=`play()` 空队列时置 idle + 转换时 broadcast_state; 前端 idleScreen 动态显示「请点歌播放」。
  已验证: 播完收到 idle 广播, 空闲后点歌自动起播。
- **2025-xx 端口占用诊断**: run.py 在端口绑定失败时列出占用程序+PID(ss -p→lsof→/proc/net/tcp
  inode 扫描, 并读 /proc/<pid>/comm+cmdline); 只展示不解除(用户已放弃自动解除需求)。
- **2025-xx 集成B站登录**: 新增 app/routers/login.py(status/qr/poll) 挂到主服务 /api/login/*;
  播放端未登录显示二维码+轮询+3秒成功提示后自动刷新恢复; 控制端未登录提示条。
  "重启并正常服务"落地为页面自动刷新(后端每次请求重读cookie, 无需重启进程)。已验证三端点。
- **2025-xx 登录修复+控制端操作**: "一直等待扫码"根因=①播放端 checkLogin 双调用并发生成两个
  key 互相覆盖(屏幕QR与轮询key不一致) ②每次新建 httpx client 丢失 buvid3 cookie。
  修复=loginActive 前端守卫 + /qr 有效期内 key 复用 + 持久会话+buvid3(spi接口)。
  控制端新增: 刷新二维码(POST /api/login/qr/refresh 广播 login_refresh)、显示/收起二维码、
  退出登录(POST /api/login/logout)。播放端收到 login_refresh 同步刷新, 过期自动换码。
- **2025-xx 控制端全屏锁定**: 未登录期间控制端改为**全屏覆盖锁定**(z-99), **唯一操作=刷新二维码**
  (移除提示条/显示二维码/退出登录按钮); 锁定层实时显示扫码状态, 登录成功自动解锁。
  注: /api/login/logout 端点保留但前端无入口(如需退出可手动 curl)。
- **2025-xx 锁定不解锁修复**: 根因=controller.html 的 `.hidden{display:none}` 无 !important,
  被 `#loginLock{display:flex}`(ID 优先级更高)覆盖 → class 隐藏失效。修复=`.hidden{display:none!important}`
  (与 player.html 一致)。**经验: 用 class 控制显隐的元素, 其 display 样式须带 !important 或避免 ID 级 display。**
- **2025-xx 两行滚动歌词**: 后端 /api/lyrics/{bvid}(B站字幕+内存缓存); 播放端两行歌词(行1=屏高一半,
  行2在其下居中), 滚动规则=偶数句焦点行1/奇数句焦点行2, 非焦点行滚入下一句(上滑动画);
  node 仿真验证与用户序列 -1A 2B/1C -2B/-1C 2D/1E -2D 完全一致。
- **2025-xx 下载预览+歌词入库**: 控制端搜索自动拼" 歌曲"+顶置(音质/MV/书名号对); ⬇ 进预览详情页
  (B站embed不自动播放/详情/歌词/多字幕轨选择/下载+WS进度/返回)。下载时同时下载所选轨歌词存入
  Song.lyrics(新增列, 启动自动 ALTER 迁移); /api/lyrics 优先返回存储歌词。已验证迁移+存储优先+track。
- **2025-xx AI字幕错乱修复**: 实测某歌唯一字幕轨是 B站AI字幕(ai-zh), 内容竟是无关的《空镜头教程》
  ——B站AI字幕对歌声转写会张冠李戴。修复=SubtitleTrack.ai 标记(lan前缀ai-或ai_type)+get_lyrics/
  /api/lyrics 默认**优先非AI轨**, 仅全AI时退回; tracks接口带ai标记, 控制端标注"(AI)"并提供
  "无歌词"选项(track=-1下载不带歌词)。已验证三场景+真实歌曲ai=true。
- **2025-xx WBI签名修复(真根因)**: 用户反馈第三方/官方客户端能正确获取 → 实测未签名 /x/player/v2
  对该视频5次返回4种不同乱码(空镜头教程/驯化动物/广告...), 而 **/x/player/wbi/v2 签名后稳定返回
  正确歌词**(60行♪歌词)。根因=未签名接口的AI字幕blob不稳定。修复=bilibili.py 加 WBI 签名
  (nav取key+mixin表+wts+w_rid, key缓存1h), subtitles() 改用 wbi/v2, 失败兜底。已验证稳定+歌词正确。
- **2025-xx 封面代理+LRCLIB兜底**: ①封面直连B站热链域名常失败 → /api/cover 代理(带UA+Referer,
  内存缓存, 仅允许hdslb.com/bilibili.com等域名防SSRF), 控制端搜索缩略图与播放端audio_only封面改走代理;
  已验证真实封面221KB拉取+SSRF拦截。②第三方歌词=**LRCLIB(lrclib.net)**, 免费无key无限制, LRC同步歌词;
  guess_song_meta标题解析+parse_lrc, /api/lyrics与下载在B站无歌词时自动兜底; 已验证《玻璃》51行正确歌词。
- **2025-xx 歌词选择同级化(搜索词/标题/B站轨)**: Song 加 search_keyword 列(迁移自动补)+下载请求携带
  用户搜索词; /api/lyrics/tracks 返回统一选项: B站轨(>=0) + 第三方搜索词(-2, 有关键词才列) +
  第三方标题(-3, 标题提取失败或LRCLIB无结果则隐藏, 探测结果缓存复用); fetch_lrclib_lyrics 加
  mode(auto/keyword/title); 下载 track=-2/-3 走对应LRCLIB模式, -1=不带歌词。
  已验证: 选项同级共存/标题失败隐藏/搜索词与标题均51行正确歌词/默认auto兜底。

- **2025-xx 第三方歌词选项常显+错误置灰**: 用户要求第三方歌词(搜索词-2/标题-3)不再自动隐藏而是
  始终显示, 失败时提示。后端 /api/lyrics/tracks 全部选项始终返回并新增 `error` 字段(缺少搜索词/
  缺少歌曲标题/未找到歌词); 新增 keyword/title/duration 查询参数(未下载时用搜索结果, 搜索词回退
  Song.search_keyword); LRCLIB 搜索词歌词缓存键含关键词哈希防互相污染。控制端: 错误选项暗红背景
  (#5a1f1f)+disabled 禁止点击+按钮内显示原因, 默认选中第一个可用来源, 全不可用则"无歌词";
  /api/lyrics 增加 keyword 参数使预览歌词与探测同词同缓存。已验证缺词缺标题/命中/无结果/下载词回退。

- **2025-xx 高级操作(静态密码+动态验证码)**: 控制端设置页新增「高级操作」, 双因子流程:
  静态密码验证 → 自动清空播放列表+播放端阻塞显示8位动态码(120s过期自动换新) → 控制端输码 →
  1分钟「是否允许高级操作」授权窗口 → 到期自动解除阻塞回空闲。新增 admin_auth.py 全局状态机
  (none/code/active, 常量时间比较, 码单次有效, 状态循环1s驱动到期/换码广播) + routers/admin.py
  (verify/code/status/cancel/queue/clear/songs删除)。**用户指定不做接口级鉴权**, 以全局状态为准
  前端门控(LAN信任模型); 动态码只广播给播放端(防访客控制器窃码), PlayState.admin 兜底重连。
  player_engine 新增 blocked 状态(拒绝点歌/歌单播放403, play/pause等no-op)。已验证状态机/路由/
  广播/到期解除/换码全流程 + 引擎阻塞行为。静态密码默认 1234(config 常量, 环境变量
  OWK_ADMIN_PASSWORD 可覆盖, run.py 启动打印提示)。**授权结束(active→none, 到期或从 active 取消)
  播放端/控制端自动刷新页面**(前端检测阶段转换触发, 启动时 phase=none 广播不会造成刷新循环)。

- **2025-xx 依赖文件+README**: requirements.txt 补全 **qrcode + pillow**(B站扫码二维码生成,
  qrcode.make() 默认 Pillow 工厂, 此前漏列) 并加注释; 新增根目录 README.md(与其他K歌软件
  对比表/功能一览/部署方式/目录结构/FAQ/免责声明)。

- **2025-xx README 补充 Termux 部署**: 优点表新增「部署门槛」行(电脑/NAS/安卓机顶盒·电视·手机
  均可 Termux 部署) + 部署方式新增第7节: Termux 安装 python/ffmpeg/git → clone → pip 装依赖 →
  python start.py; 附 termux-wake-lock 保活/同机自用 127.0.0.1/存储权限/性能注意事项。

- **2025-xx AGENTS.md 清理**: 移除本机环境细节(exFAT 文件系统、bwrap 沙箱限制、SSH 配置修复过程
  及其系统文件路径), 保留通用操作约定(建/改文件一律用 bash 直接写)。

<!-- 后续会话在此追加: 日期 + 做了什么 + 结论/约定/坑；改完记得 git add AGENTS.md 并提交 -->

- **2026-08-27 会话恢复(记忆核对)**: 恢复会话时对照仓库实际状态核对记忆: ①AGENTS.md 原记
  「尚未登录」已过期——bilibili_cookie.json 已存在且有效(SESSDATA 齐, user_id 3546822289131703);
  ②backend/data/ 已建库, media/ 已有 9 首已下载视频(原神系列/玻璃/晚安 等)。已同步更新
  §0.4/§2/§6.1 相关表述。另: 工作区 start.py 仅文件权限变更 644→755(内容未动), 未提交。

- **2026-08-27 删歌补漏(歌词缓存清理)**: 删除歌曲时 `db.delete(song)` 只删库行(歌词随行删),
  但 **LYRICS_CACHE 内存缓存不清理** → 同 bvid 重下后会命中旧歌词(字幕更新不生效)。
  修复=api.py 新增 `clear_lyrics_cache(bvid)`(清理 B站轨 `{bvid}:*` / LRCLIB搜索词
  `lrc-k:{bvid}:*` / LRCLIB标题 `lrc-t:{bvid}` 四类键, 前缀匹配不误删其他歌曲), admin.py
  删除歌曲时调用(已验证: 只清目标 bvid, 冒烟通过)。
  另评估「歌词改文件存储」代价: 现有数据仅 5 首有歌词共 22.4KB(单首最大 7.6KB, db 总 60KB),
  SQLite TEXT 列完全无压力; 文件存储收益低(本项目歌词全自动生成, 无需人工编辑)而风险中
  (文件↔DB行一致性/孤儿文件/原子写/启动迁移), **结论: 维持列存储, 不迁移**。

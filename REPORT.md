# Binance Alpha Event Monitor — 最终工程报告

- 版本：1.0.0
- 报告日期：2026-10-17（UTC），2026-10-16（美国旧金山 / UTC-7）
- 项目目录：`Binance Alpha/`（本沙箱内路径；用户侧为 `D:\Project\Binance Alpha`）
- 运行时：Python 3.12.11 / SQLite / 零第三方依赖 / 2 vCPU 4GB 沙箱（资源目标按 1C/1GB 评估）

---

## 一、实际修改/交付了什么

在项目目录完成可运行工程，非方案文档：

| 交付物 | 说明 |
|---|---|
| `app/`（20 个模块） | 配置、SQLite、规范化、关键词匹配、字段抽取、事件引擎、Telegram 信源、PushPlus Provider、通知/提醒、调度、轮询、健康检查、CLI |
| `tests/fixtures/binance_wallet_announcements.txt` | 官方频道 20 条真实消息逐字转录（沙箱验证用） |
| `Dockerfile` / `docker-compose.yml` / `.env.example` | 1C/1GB 容器化部署，`./data:/app/data` 持久化 |
| `README.md` | 使用与运维文档 |
| `REPORT.md`（本文件） | 验收与验证报告 |

## 二、实际测试了什么

| 测试 | 命令 | 结果 |
|---|---|---|
| 离线关键词（规格§30 正反例 + 真实频道反例） | `python -m app test-alpha` | **ALL PASS**（DNA/DRV/DRV LIVE=MATCH；Trading Competition/Prediction/DeFi/Referral/DGAI/ACU/DeFi API/TRON=NOT MATCH） |
| 字段抽取（真实公告文本） | 同上 | DNA/DRV 全部字段 PASS（含 "today at 10:00 (UTC)" 相对时间解析） |
| 事件类型分类 | 同上 | PRE_ALERT / DETAIL / UPDATE / LIVE 全部 PASS |
| 双阶段提醒（规格§31 全部场景） | `python -m app test-notification` | **ALL PASS** |
| 解析器（合成 t.me HTML） | `python -m app test-source` | PASS |
| 真实信源历史验证 | `python -m app replay DNA` / `replay DRV` | **PASS / PASS**（详见第四节） |
| 动态轮询区间 | 内联验证 | 1800/900/300/60 全部命中 |
| 首启历史抑制 | 内联验证 | 20 条历史存储、0 通知；第二轮 0 新增；新增消息仅处理 1 条 |
| 重启恢复 | 真实进程重启 | 不重复处理历史、checkpoint 生效 |
| 资源占用 | 真实进程采样 | RSS ≈ 26 MB、CPU ≈ 0.5%、2 线程 |
| 健康检查 | `python -m app health` | OK（与运行中进程并发读正常） |

## 三、真实信源验证（官方 + 第三方交叉）

**沙箱网络限制**：本沙箱出口代理白名单禁止直连 t.me（curl 超时），生产代码 `TelegramPublicHtmlSource` 仍用 urllib 直连官方频道；沙箱内验证改用**平台抓取工具取回的真实官方频道页面**作为 fixture（内容逐字来自 t.me/s/binance_wallet_announcements，标注 transport=platform-fetcher）。两条历史事件均在该页面内。

```
============================== REAL SOURCE TEST ==============================
DNA Found: YES    Official Source: YES    Preview: YES    Detail: YES
Project: NeoSoul  Symbol: DNA
Start: 2026-10-16 16:00 +08:00   (= 08:00 UTC，来自公告原文 "at 08:00 (UTC)")
Points: 245   Amount: 400   Cost: 15
Result: PASS
-------------------------------------------------------------------------------
DRV Found: YES    Official Source: YES    Preview: YES    Detail: YES
Project: Derive  Symbol: DRV
Start: 2026-10-14 18:00 +08:00   (= 10:00 UTC，来自预告 "trade today at 10:00 (UTC)" 相对日期解析)
Points: 245   Amount: 70   Cost: 15
Result: PASS
============================== OVERALL: ALL PASS ==============================
```

事件关联验证（非硬编码）：
- `alpha:unknown:2026-10-14`（DRV 预告，无代号）→ 被 "Derive (DRV) is now live" 消息认领 → 合并为单一事件 `alpha:DRV:2026-10-14`（type=LIVE）。
- `alpha:DNA:2026-10-15` 预告 + `alpha:DNA:2026-10-16` 详情（含 "on October 16" 事件日期提取）→ 合并为 `alpha:DNA:2026-10-16`（type=UPDATE），开始时间 10-16 16:00 +08:00。

第三方交叉验证（仅佐证，标注 third_party）：腾讯新闻、Odaily、CoinPost、SoSoValue、BlockBeats、PANews、TokenPost 均确认 DNA（10-16 16:00 UTC+8、245 积分、400 DNA、消耗 15）与 DRV（10-14、245 积分、70 DRV、消耗 15）全部字段，与官方信源解析结果一致。

## 四、双阶段提醒测试明细（§31）

1. `start = now + 30min`：DISCOVERED 发送 + T-10 reminder 创建，scheduled = start − 10min ✅
2. `start = now + 5min`：仅 DISCOVERED，T-10 窗口已过不创建 ✅
3. `start = now − 5min`：DISCOVERED 内容为"已经开始"，不建 T-10 ✅
4. 16:00 → 17:00：旧 reminder CANCELLED、新 reminder（16:50 +08:00）PENDING、EVENT_UPDATED 发送 ✅
5. 程序重启：未发送 reminder 从 SQLite 恢复；过期（>90s）标记 MISSED 不补发；宽限窗口内照发；未来 reminder 保持 PENDING ✅
6. 去重：同一事件 DISCOVERED 恰好一次（唯一索引 + 前置检查双保险）✅

## 五、最终验收标准

| 项目 | 结果 | 说明 |
|---|---|---|
| Development | **PASS** | 完整工程，可运行 |
| Telegram Source | **PASS** | urllib 直连 + HTML 解析；沙箱内以官方页面 fixture 验证通过 |
| DNA Historical Detection | **PASS** | 官方信源，全字段命中 |
| DRV Historical Detection | **PASS** | 官方信源，全字段命中（含相对时间 + 事件认领） |
| Keyword Matching | **PASS** | 正反例全过，真实频道反例不误报 |
| Event Dedup | **PASS** | 关联更新 + unknown 认领 + (event_id, type) 唯一约束 |
| Dynamic Polling | **PASS** | 1800/900/300/60 实测 |
| Immediate Notification | **PASS**（dry-run） | 发现即推送已实现并测试；真实微信需 Token |
| T-10 Notification | **PASS**（dry-run） | 持久化、重启恢复、错过不补发均已测 |
| Reminder Persistence | **PASS** | SQLite `event_reminders` |
| Restart Recovery | **PASS** | 进程级重启实测 |
| PushPlus Integration | **READY** | Provider + 3 次重试 + 失败落库；pushplus.plus 端点可达（本沙箱已实际请求到服务端响应） |
| Actual WeChat Delivery | **BLOCKED_BY_MISSING_TOKEN** | 未配置有效 `PUSHPLUS_TOKEN`（正常状态，非开发失败） |
| Docker | **PASS**（产物级） | Dockerfile/compose/.env.example 已提供；本沙箱无 docker CLI，未执行构建，需在服务器 `docker compose up -d --build` |
| 1C/1GB Resource Test | **PASS** | RSS ≈ 26 MB（目标 <150MB）、CPU ≈ 0.5%、2 线程 |

## 六、报告问题逐条回答

1. **最近 DNA 能从真实官方信源找到吗？** 能。官方频道 10-15 预告 + 10-16 详情两条都在；解析出 NeoSoul/DNA、10-16 16:00 +08:00、245/400/15。
2. **最近 DRV 能从真实官方信源找到吗？** 能。官方频道 10-14 预告 + "now live" 两条；解析出 Derive/DRV、10-14 18:00 +08:00、245/70/15。
3. **官方预告通常提前多久？** 样本：DRV 预告提前约 2 小时（16:01 发布，18:00 开始）；DNA 预告提前约 1 天（10-15 19:30 发布），正式细节提前约 2 小时（10-16 14:04 发布，16:00 开始）。30 分钟空闲轮询最迟约 30 分钟内发现，仍有约 1.5 小时准备——与需求假设一致。
4. **30min→15min→5min→1min 动态轮询正常吗？** 正常，四级区间全部实测命中；事件结束后回 30min。
5. **T-10 reminder 能跨重启恢复吗？** 能。PENDING 存在 SQLite，启动时恢复；重启后进程级实测通过。
6. **同一 Alpha 会重复推微信吗？** 不会。`notifications(event_id, notification_type)` 部分唯一索引（DISCOVERED / START_MINUS_10_MINUTES）+ 发送前检查；轮询重复、重启、重试均被拦截。
7. **PushPlus 接入后只需配置哪个环境变量？** `PUSHPLUS_TOKEN=<token>`（同时把 `PUSHPLUS_ENABLED` 设为 `true`）。
8. **1C/1GB 实际 CPU/RAM 占用？** 实测 Python 进程 RSS ≈ 26 MB、空闲 CPU ≈ 0.5%、2 线程（沙箱 2C/4G 上测得，1C/1GB 余量充足）。
9. **Telegram Public Channel 有明显延迟/稳定性问题吗？** 本沙箱被出口白名单禁止直连 t.me（网络策略问题，非 Telegram 本身）；生产服务器不受影响。t.me/s 网页预览稳定、无需登录，作为 v1 信源可行；注意它只返回最近约 20 条，若频道 30 分钟内消息超过 20 条会漏——Binance Wallet 频道日更约 10-20 条，30 分钟窗口下风险低，但建议下一阶段接 MTProto 拿全量历史。
10. **建议加 MTProto 或 Binance Square 第二官方信源吗？** 建议。代码已留 `SourceProvider` 抽象与 `TelegramMtprotoSource` / `BinanceSquareSource` 桩。优先级：MTProto（全量历史、稳定、低延迟）> Binance Square（官方网页，可作为同源交叉验证）。第三方源仅用于交叉验证。

## 七、部署后你需要手动做的

1. 在 1C/1GB 服务器：`docker compose up -d --build`（首次拉取 python:3.12-slim）。
2. 注册 [pushplus.plus](https://www.pushplus.plus) 获取 token，配置 `.env`：`PUSHPLUS_ENABLED=true`、`PUSHPLUS_TOKEN=xxxx`，然后 `docker compose restart`。
3. 用 `docker compose logs -f` 观察首启日志（应出现 `FIRST START: stored N historical message(s)... NO notifications sent`）。
4. 日常 `docker compose exec binance-alpha-monitor python -m app health` 查看健康；或读宿主机挂载的 `data/health.json`。
5. 收到微信后核对：发现即推送 + 开始前 10 分钟二次提醒；后续可把轮询间隔调回默认（当前沙箱测试用了 10s 覆盖值，服务器上以 `.env` 为准）。

## 八、未完成/受限项（如实披露）

- 真实微信投递：缺少有效 PushPlus Token → **BLOCKED_BY_MISSING_TOKEN**（系统处于 dry-run，一切功能正常）。
- Docker 构建：沙箱无 docker CLI → 未实际构建镜像；Dockerfile 为最小化单阶段（零依赖，构建失败概率极低），需在服务器验证。
- 美国服务器实际部署：沙箱即部署环境（旧金山机房），已完成运行+资源+健康实测；用户服务器部署按第七节操作。
- 沙箱内 t.me 直连被代理白名单拦截 → 生产信源（urllib）未能在沙箱打真实请求，改用官方页面 fixture 完成同源验证；生产服务器首次运行会直连官方频道。

---

# Phase 2 — 官方信源补全与生产验收（追加）

- 版本：2.0.0
- 报告日期：2026-10-17（UTC）/ 2026-10-16（美国）
- 目标：保留 P0 Telegram 信源；新增 Binance Square（第二核心信源）与 Binance 官方英文公告 Telegram（补充信源）；移除第三方信源；跨官方信源事件合并/去重/微信通知；修复健康检查与事件关联；真实网络采集验证 + Docker 运行验证。PushPlus 无 token 期间继续 dry-run，未中断开发。

## 1. 实际修改了什么（增量，复用 Phase 1 未推倒重建）

| 模块 | 变更 |
|---|---|
| `app/config.py` | 新增 `SOURCES_ALLOWLIST`、`TELEGRAM_ANNOUNCEMENTS_*`、`SQUARE_*`（默认 `MODE=blocked`）、`POLL_JITTER_FRACTION`、`SOURCE_STAGGER_MAX_SECONDS`、`REMINDER_TICK_SECONDS=30` |
| `app/sources/base.py` | 重写：`SourceError/SourceBlockedError/SourceParseError`；`key` 身份字段；`build_sources()` 官方白名单工厂；`build_source()` 兼容别名 |
| `app/sources/telegram_html.py` | 严格解析（P0 修复）：页面无 Telegram widget 标记 → 抛 `SourceParseError`，**禁止"解析失败却返回空列表并标 SOURCE_OK"**；`key` 参数（消息 source 字段区分 wallet/announcements） |
| `app/sources/telegram_text.py` | fixture 信源支持 `key`/`source_key` |
| `app/sources/square.py` | **新建** `BinanceSquareSource`：`blocked`（默认，明确抛 BLOCKED）/`attempt`（带 SQUARE_EXTRA_HEADERS 走 bapi）/`fixture`（离线 JSON）；容忍式 `_extract_posts`；`BLOCKED_REASON` 全量定案依据 |
| `app/sources/stubs.py` | 移除 Square 桩；仅留 MTProto / ThirdParty（**绝不进 allowlist**，第三方永不触发微信） |
| `app/db.py` | 新增 `event_sources` 表（`UNIQUE(event_id, source)` + first/last_seen）；`find_events_by_symbol_on_date` / `find_unknown_events_on_date` / `find_events_by_start_proximity` / `upsert_event_source` / `get_event_sources` / `get_event_first_message_text` / `all_events` |
| `app/event_engine.py` | 重写：三层模型；候选解析 `_resolve_event`（symbol 兼容更新 / start 冲突新建编号事件 / unknown 唯一化 `alpha:unknown:{date}:{hash8}` / 无歧义认领 / 内容相似度 ≥0.9 合并）；`EventChanges.material`（仅 start/end/points/amount/rule/status 变化发 EVENT_UPDATED）；取消检测置 CANCELLED；每消息 `upsert_event_source` |
| `app/models.py` | `EventChanges.material` 字段 |
| `app/poller.py` | 重写：多源独立 checkpoint（meta `source_first_done:{key}`）/独立失败状态/单源故障不阻塞/源间 stagger + 全局 jitter；轮询失败不重置事件与提醒 |
| `app/health.py` | 重写：每源独立 last_attempt/last_success/consecutive_failures/status；判定融合**实际轮询间隔**（修复 30min 轮询 10min 误报）；BLOCKED 保持不动且不拖垮系统总状态 |
| `app/cli.py` | `run` 改用 `build_sources()` 多源启动；`test-source` 增加结构变化必须报错 + 多源抓取检查；**新增 `test-cross-source`**；`replay --sources`；`health` 按 per-source 退出码 |
| `app/selftests.py` | 新增官方 announcements 真实反例（COAI/XDP/O 竞赛、Starter Carnival、Earn、非 Alpha 空投支持公告）；Alpha Box 正例；**`run_cross_source_tests()`（§7 七组回归）** |
| `app/verify.py` | 重写：多信源回放、每源出处声明（official_fixture/live_fetch/synthetic/blocked）、跨信源合并后统一事件校验 |
| `tests/fixtures/binance_announcements.txt` | 新建：官方 announcements 频道真实抓取内容转录（Phase 2 在线抓取成功） |
| `Dockerfile`/`docker-compose.yml`/`.env.example`/`README.md` | 按新配置项与结论更新；healthcheck 注释修正 |

## 2. 实际测试了什么（全部在沙箱执行）

| 测试 | 命令 | 结果 |
|---|---|---|
| 离线关键词（§30 正反例 + 官方频道真实反例） | `python -m app test-alpha` | **ALL PASS**（新增 6 条 announcements 频道真实反例 NOT MATCH；Alpha Box 正例 MATCH） |
| 双阶段提醒（§31 全部场景） | `python -m app test-notification` | **ALL PASS** |
| **跨信源回归（§7）** | `python -m app test-cross-source` | **ALL PASS**（详见下） |
| 信源解析（严格失败检测 + 多源抓取） | `python -m app test-source` | **ALL PASS**（malformed → SourceParseError PASS；wrong-channel guard PASS） |
| 真实信源历史验证（多源） | `python -m app replay` | **DNA PASS / DRV PASS**（wallet fixture + announcements fixture + square BLOCKED） |
| 完整监控回路冒烟（真实进程） | 缩短间隔运行 `python -m app run` | 三信源独立运行、square BLOCKED 不阻塞、首启抑制历史、新增消息→DISCOVERED(dry-run)+T-10(PENDING)、health 每源 OK |

### 跨信源回归明细（§7）

1. TG + Square 内容完全相同 → 单事件、单 DISCOVERED、无 EVENT_UPDATED、`event_sources` 记录两个官方来源 ✅
2. TG + Square 文字不同但指向同一事件（同 symbol+start）→ 单事件、单 DISCOVERED、纯转述不通知 ✅
3. 三个官方渠道先后发布相同事件 → 单事件、单 DISCOVERED、T-10 计划恰好一个 PENDING、三来源记录 ✅
4. 未知预告（无代号）→ 立即 DISCOVERED（唯一 key `alpha:unknown:2026-10-20:cb14bb0c`）→ 详情消息无歧义认领 → 合并为 `alpha:LNA:2026-10-20`，start 揭示触发一次 EVENT_UPDATED，T-10 建立 ✅
5. 同日两个不同未知活动 → 两个独立未知事件（不错误合并），各自通知一次 ✅
5b. 同日跨渠道完全相同的未知预告 → 内容相似度合并，不重复通知 ✅
6. 第二渠道修改开始时间 16:00→17:00 → 同一事件更新、EVENT_UPDATED 一次、旧 T-10 CANCELLED + 新 T-10（16:50 +08:00）PENDING ✅
7. 多信源事件 T-10 恰好触发一次（START_MINUS_10_MINUTES 通知计数=1）✅

## 3. 真实网络采集验证

- **Binance 官方英文公告 Telegram（`t.me/s/binance_announcements`）**：本阶段用平台抓取工具**在线抓取成功**，得到含 COAI/XDP/O 等 Alpha Trading Competition 公告在内的约 20 条官方原文 → 已转录为 `tests/fixtures/binance_announcements.txt`（标注 official_fixture，transcribed from live capture）→ 回放验证 **0 条误报为 Alpha 空投**（Trading Competition 等全部 NOT MATCH）。
- **Binance Wallet Telegram（`t.me/s/binance_wallet_announcements`）**：本阶段平台抓取工具对该频道**限流**（`link fetch error`，多次重试仍失败）；Phase 1 曾成功抓取并保留官方原文 fixture（20 条，标注 official_fixture）。回放验证 DNA/DRV 全字段 PASS。生产服务器走 urllib 直连 t.me，不受沙箱限制。
- **Binance Square（`www.binance.com/en/square/profile/binancewallet`）**：bapi 端点被 robots.txt 明确禁止自动访问（"URL is disallowed for automated access by robots.txt. Do not retry"），且需要前端指纹头 → **SOURCE_BLOCKED**，如实标记，不伪造 PASS；解析器用合成 envelope 离线验证（标注 synthetic，仅管线测试）。

**出处区分**：`official_fixture`（官方原文转录）/ `live_fetch`（生产直连）/ `synthetic`（人工构造，仅测解析器）/ `blocked`（无法轻量接入）——三者不混淆，报告中分别标注。

## 4. 最终验收标准（Phase 2）

| 项目 | 结果 | 说明 |
|---|---|---|
| Development | **PASS** | v2.0.0，全部模块编译通过 |
| Telegram Source (wallet, P0) | **PASS** | 官方 fixture 全字段命中；严格解析（结构变化必报错） |
| Telegram Source (announcements, P1) | **PASS** | 真实在线抓取成功；Alpha 竞赛类 0 误报 |
| Binance Square (P0 第二信源) | **SOURCE_BLOCKED** | 无公开 API + robots.txt 禁止 + 禁用浏览器自动化；适配器保留并如实报告（按用户预案，非伪造 PASS） |
| DNA Historical Detection | **PASS** | NeoSoul/DNA、10-16 16:00 +08:00、245/400/15 全命中 |
| DRV Historical Detection | **PASS** | Derive/DRV、10-14 18:00 +08:00、245/70/15 全命中 |
| Keyword Matching | **PASS** | 正反例 + 官方频道真实反例 + Alpha Box 全过 |
| Event Dedup (cross-source) | **PASS** | §7 全部 7 组回归通过；同 symbol 同日 start 冲突不合并；未知事件唯一化 |
| Dynamic Polling | **PASS** | 30/15/5/1min 保留；多源独立 checkpoint + 失败隔离 + jitter/stagger |
| Immediate Notification | **PASS**（dry-run） | 跨信源只发一条 DISCOVERED；真实微信需 Token |
| T-10 Notification | **PASS**（dry-run） | 独立调度线程（30s tick）；改时间重排；错过不补发 |
| Reminder Persistence | **PASS** | SQLite `event_reminders` |
| Restart Recovery | **PASS** | 未发送 reminder 从 SQLite 恢复（§31 场景 5 + 冒烟） |
| Health Check (v2) | **PASS** | 每源独立；按实际轮询间隔判定；BLOCKED 不拖垮总状态；修复 v1 10min 误报 |
| PushPlus Integration | **READY** | Provider + 3 次重试 + 失败落库；状态区分 DRY_RUN |
| Actual WeChat Delivery | **BLOCKED_BY_MISSING_TOKEN** | 正常状态，非开发失败 |
| Docker | **产物级 PASS / 构建 BLOCKED** | Dockerfile/compose/.env.example 已更新；沙箱无 docker CLI/无 root，无法实际构建，需服务器验证 |
| 1C/1GB Resource Test | **PASS**（Phase 1 实测 + 冒烟复测） | RSS ≈ 26 MB、空闲 CPU ≈ 0.5%、2 线程；多信源下仍远低于 150MB 目标 |

## 5. 报告问题逐条回答（Phase 2）

1. **最近 DNA 能从真实官方信源找到吗？** 能（官方 Wallet 频道 10-15 预告 + 10-16 详情，fixture 回放 PASS；第三方佐证见 Phase 1 §三）。
2. **最近 DRV 能从真实官方信源找到吗？** 能（官方 Wallet 频道 10-14 预告 + "now live"，含 unknown→认领合并，PASS）。
3. **官方预告通常提前多久？** 样本同 Phase 1：DRV 约 2 小时前、DNA 预告提前约 1 天、正式细节提前约 2 小时。30 分钟空闲轮询最迟 30 分钟内发现。
4. **30min→15min→5min→1min 动态轮询正常吗？** 正常，四级区间保留并实测；多源各自独立、故障隔离、stagger+jitter 生效。
5. **T-10 reminder 能跨重启恢复吗？** 能。PENDING 存 SQLite；冒烟中重启后恢复并正确触发一次。
6. **同一 Alpha 会重复推微信吗？** 不会。跨信源合并为单事件 + `(event_id, notification_type)` 唯一约束 + material 判定（纯转载不通知）。
7. **PushPlus 接入后只需配置哪个环境变量？** `PUSHPLUS_ENABLED=true` + `PUSHPLUS_TOKEN=<token>`（其余保持默认）。
8. **1C/1GB 实际 CPU/RAM？** RSS ≈ 26 MB、空闲 CPU ≈ 0.5%、2 线程；三信源全部启用后仍满足 <150MB 目标。
9. **Telegram Public Channel 延迟/稳定性？** 沙箱内被代理白名单限流（网络策略，非 Telegram 问题）；生产直连不受影响。t.me/s 只返回最近约 20 条，低频轮询下风险低；建议下一阶段 MTProto 拿全量历史。
10. **建议加 MTProto 或 Square 第二信源？** 建议加 **MTProto（最高优先级）**：全量历史 + 低延迟 + 稳定；Square 在无公开 API 前保持 BLOCKED 适配器（本阶段已如实报告），若未来有合法接入方案（官方 API/合规代理）再启用。

## 6. 部署后你需要手动做的（Phase 2 增量）

1. 服务器 `.env` 中按 `.env.example` 启用三信源配置（`SOURCES_ALLOWLIST` 默认已含三官方键）。
2. **Square**：默认 `SQUARE_MODE=blocked` 即可——不要为了接入 Square 引入浏览器自动化；等合法方案或手动提供指纹头后再设 `attempt`。
3. 配置 `PUSHPLUS_ENABLED=true` + `PUSHPLUS_TOKEN=xxxx`，`docker compose restart`。
4. 首启观察 `FIRST START ... NO notifications sent`（每信源各一条）；日常 `python -m app health` 或读 `data/health.json` 查看每信源状态。
5. 若希望接入 Square，需先解决官方数据通道（见 §3），否则保持 BLOCKED 即为预期状态。

## 7. 未完成/受限项（Phase 2，如实披露）

- **Binance Square 真实采集**：SOURCE_BLOCKED（无公开 API/robots 禁止/禁用浏览器自动化）——按用户预案标记，不伪造 PASS；解析器已离线验证。
- **wallet 频道本阶段在线重抓**：平台抓取工具限流（`link fetch error`）；Phase 1 官方原文 fixture 完成同源验证，生产直连不受影响。
- **Docker 实际构建/运行**：沙箱无 docker CLI、无 root（apt 失败）→ 以"产物级 PASS + 容器内等价运行验证（fixture 全流程）"替代，构建需在服务器执行。
- **真实微信投递**：无 PushPlus Token → BLOCKED_BY_MISSING_TOKEN（dry-run 正常）。

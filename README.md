# Binance Alpha Event Monitor (Phase 2 — official sources, multi-channel merge)

轻量级 Binance Alpha 空投预告自动监控 + 微信双阶段提醒。仅使用**官方信源**，跨渠道合并为统一事件，不重复骚扰。

```
Binance Wallet 官方 Telegram (P0)  ┐
Binance 官方英文公告 Telegram (P1) ┼─低频动态轮询─► 关键词规则过滤 ─► 统一 Alpha 事件 ─► SQLite
Binance Square 官方账号 (适配器)   ┘                        │
                                 ┌─────────────────────────┴─────────────────────────┐
                                 │ 发现消息 → 立即推送微信   │  T-10分钟 → 再次推送微信  │
                                 └─────────────────────────┴─────────────────────────┘
                                                           │
                                                         PushPlus ──► 微信
```

- **v1/v2 边界**：不用 LLM、不用 Binance API Key、不自动领取、不自动交易、不保存登录信息、不用浏览器自动化。
- **零第三方依赖**：仅用 Python 标准库（urllib + sqlite3 + html.parser + zoneinfo），Python 3.10+（推荐 3.11/3.12）。
- **资源**：实测常驻内存约 **26 MB**、空闲 CPU ≈ 0%，适配 1C/1GB 7×24 长期运行。

---

## 官方信源（显式白名单）

| Key | 官方身份 | 状态 |
|---|---|---|
| `telegram_wallet` | Binance Wallet 官方 Telegram（P0） | ✅ 生产采集 |
| `telegram_announcements` | Binance 官方英文公告 Telegram（P1，只处理 Alpha 空投/Alpha Box 公告） | ✅ 生产采集 |
| `binance_square` | Binance Wallet 官方 Binance Square 账号 | ⛔ `SOURCE_BLOCKED`（见下） |

- 生产 allowlist 仅允许这三个官方键（`SOURCES_ALLOWLIST`），第三方/KOL/搬运频道一律不进入监控，**不允许第三方信息触发微信提醒**。
- **Binance Square 为何 BLOCKED**：Binance 无公开 API（dev.binance.vision 明示 bapi 非公开）；`/bapi/composite/*` 需前端指纹头（bnc-uuid/csrftoken/device-info），纯 HTTP 返回 `data: null`；robots.txt 禁止自动访问；v1 禁用浏览器自动化与第三方 API 服务。适配器保留并明确抛 `SourceBlockedError`，健康状态记为 `BLOCKED`（不伪造 PASS）。若未来在服务器上捕获到合法指纹头，可 `SQUARE_MODE=attempt` + `SQUARE_EXTRA_HEADERS=<json>` 尝试；离线管线测试用 `SQUARE_MODE=fixture`。

## 跨信源事件合并（v2 核心）

三层模型：`Source Message → Canonical Alpha Event → Notification`。

- 原始消息按 `(source, source_message_id)` 去重。
- 同一官方事件的 Telegram / Square / Announcements 公告合并为**一个** Canonical Event（按 symbol+日期+开始时间容差 2h 关联；未知代币预告按唯一 key `alpha:unknown:{date}:{hash}`，仅在无歧义时被后续详情认领；文本相似度 ≥0.9 合并跨渠道完全相同预告）。
- 同 symbol 同日但开始时间冲突 → 建立编号事件 `alpha:{SYM}:{date}:{n}`，**不错误合并**。
- 每个事件记录全部官方来源（`event_sources` 表，含各渠道 first/last seen）。
- 通知规则：`DISCOVERED` 每事件一次；纯转载/文字重述/来源补充**不通知**；仅重大变化（开始时间/结束时间/积分/数量/规则/状态）发一次 `EVENT_UPDATED`。

---

## 快速开始

```bash
# 1) 配置（先复制模板）
cp .env.example .env        # 然后编辑 .env：PUSHPLUS_TOKEN=xxxx（未配置则 dry-run）

# 2) 本地直接运行
python -m app run

# 3) Docker 部署（1C/1GB 服务器）
docker compose up -d --build
docker compose logs -f --tail=100
```

## 命令一览

| 命令 | 说明 |
|---|---|
| `python -m app run` | 启动监控（多信源轮询 + T-10 调度） |
| `python -m app test-alpha` | 离线关键词/抽取/类型测试（DNA/DRV + 官方反例） |
| `python -m app test-notification` | 双阶段提醒测试（30min/5min/已开始/改时间/重启恢复/去重） |
| `python -m app test-cross-source` | 跨信源合并/去重回归（Phase 2 §7） |
| `python -m app test-source` | Telegram 解析器自测（含"结构变化必须报错"）+ 在线抓取检查 |
| `python -m app replay [DNA\|DRV] [--sources ...]` | 真实信源历史验证（多信源、标注出处） |
| `python -m app health` | 健康快照（`data/health.json`，每信源独立） |
| `python -m app version` | 版本号 |

> 沙箱/离线验证（fixture 为官方频道原文，标注 `official_fixture`）：
> `TELEGRAM_FIXTURE=tests/fixtures/binance_wallet_announcements.txt TELEGRAM_ANNOUNCEMENTS_FIXTURE=tests/fixtures/binance_announcements.txt python -m app replay`
> 生产环境无需设置 fixture，直接访问 t.me。

---

## 架构

```
app/
├── config.py            # 全部环境变量配置（无硬编码；SOURCES_ALLOWLIST 显式白名单）
├── timeutil.py          # UTC 内部时间 / Asia/Shanghai 展示
├── db.py                # SQLite：messages / alpha_events / notifications / event_reminders / event_sources / meta
├── normalize.py         # Unicode NFC → 全角转半角 → 标点归一 → 小写
├── matcher.py           # Alpha 身份关键词 AND 空投意图关键词（确定性规则，含 alpha box）
├── extractor.py         # 项目/代号/开始时间/积分门槛/消耗/数量/领取规则 抽取
├── event_engine.py      # v2 跨信源关联：候选解析/认领/未知事件唯一化/material change/取消检测
├── sources/
│   ├── base.py          # SourceProvider 抽象 + 官方白名单工厂（build_sources）
│   ├── telegram_html.py # P0/P1 生产信源：t.me/s 公开网页（严格解析：结构变化必须报错）
│   ├── telegram_text.py # fixture 信源（沙箱验证）
│   ├── square.py        # Binance Square 适配器（blocked/attempt/fixture 三模式）
│   └── stubs.py         # MTProto / ThirdParty（future，绝不进 allowlist）
├── notifiers/
│   ├── base.py          # NotificationProvider 抽象（dry-run 模式）
│   └── pushplus.py      # PushPlus 微信（3 次重试：0s/30s/120s）
├── notifications.py     # 通知去重（DISCOVERED/T-10/EVENT_UPDATED 每事件每类型最多一次）
├── reminders.py         # T-10 reminder 持久化（SQLite）
├── scheduler.py         # 独立调度线程（30s tick，重启恢复、错过不补发）
├── poller.py            # 多信源动态轮询（每源独立 checkpoint/失败隔离/stagger+jitter）
└── health.py            # v2 健康快照（每源状态 + 按实际轮询间隔判定）
```

### 动态轮询

| 状态 | 间隔 | 配置 |
|---|---|---|
| 无关注事件（正常） | 30 分钟 | `POLL_INTERVAL_IDLE_SECONDS=1800` |
| 已发现预告、开始时间未知 | 15 分钟 | `POLL_INTERVAL_UPCOMING_SECONDS=900` |
| 距离开始 ≤ 2 小时 | 5 分钟 | `POLL_INTERVAL_NEAR_EVENT_SECONDS=300` |
| 距离开始 ≤ 15 分钟 | 1 分钟 | `POLL_INTERVAL_COUNTDOWN_SECONDS=60` |
| 事件结束 | 恢复 30 分钟 | — |

- 每信源独立维护 checkpoint 与失败状态；**单个信源异常不阻塞其他信源**。
- 源间 0–3s 随机 stagger，全局等待 ±10% jitter，避免固定时刻齐发。
- **T-10 调度独立于轮询**（`REMINDER_TICK_SECONDS=30`，轮询 30 分钟也不影响提醒精度）。

### 双阶段提醒

1. **发现即推送**：命中 Alpha 空投/领取规则 → 立即 `DISCOVERED`（项目未公布用"暂未公布"模板）。多官方信源命中同一空投，只发一条。
2. **T-10 提醒**：解析出准确开始时间 → 写入 `event_reminders`（SQLite）→ `start - 10min` 触发 `START_MINUS_10_MINUTES`。
   - 重启后从未发送的 reminder 自动恢复；`scheduled_at` 已过去 → 标记 `MISSED`，**不补发**。
   - 开始时间变更 → 旧 reminder 取消、新 reminder 建立，并发送一次 `EVENT_UPDATED`（仅重大变化）。
   - 同一事件 + 同一通知类型 → 最多发送一次（唯一索引兜底）。

### 首启行为

首次启动：各信源抓取历史消息 → 存储 → 建立独立 checkpoint → **不推送历史** → 进入实时监控。之后仅处理新消息（messages 表唯一索引去重，幂等）。

---

## 微信接入（PushPlus）

1. 到 [pushplus.plus](https://www.pushplus.plus) 注册，关注公众号获取 token。
2. `.env` 中配置：

   ```
   PUSHPLUS_ENABLED=true
   PUSHPLUS_TOKEN=你的token
   ```

3. 重启容器/进程即可。未配置 token 时系统照常运行，日志输出 `[DRY-RUN] notification would be sent: ...`，状态标记 `DRY_RUN`（不冒充真实送达）。

接口已抽象为 `NotificationProvider`，未来可加 WxPusher 等渠道，不改业务逻辑。

---

## 数据与运维

- 数据目录 `data/`：`monitor.db`（SQLite，WAL）、`health.json`、`monitor.log`。
- Docker volume：`./data:/app/data`（消息/事件/通知/T-10 提醒全部持久化）。
- 健康检查（v2，每信源独立）：结合**当前实际轮询间隔**、上次计划/成功时间、连续失败数判定
  （连续失败 ≥3 → ERROR；≥1 → WARNING；间隔内未到下次轮询 → OK；`BLOCKED` 保持不动且不拖垮系统状态）。
  修复了 v1 "30 分钟轮询却 10 分钟误报 WARNING" 的矛盾。
  **"没有发现 Alpha" ≠ 系统异常**：`SOURCE_OK + NO_ALPHA` 与 `SOURCE_ERROR` 分开记录。

## 已知边界（v2 设计内）

- Telegram 公开频道网页预览（约最近 20 条）；Binance 提前数小时发布预告，30 分钟轮询足够。
- Binance Square 目前 `SOURCE_BLOCKED`（无公开 API + 禁用浏览器自动化），解析器已离线验证，生产接入待合法方案（MTProto 或未来官方 API）。
- 不查询 Alpha Points 余额、不做"值不值得抢"判断、不自动领取/交易。
- 下一阶段候选：Telegram MTProto（`TelegramMtprotoSource` 桩已留）、Square 合法接入、多服务器集群。

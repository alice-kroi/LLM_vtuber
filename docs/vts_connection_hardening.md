# VTS 连接健壮性改造清单（对齐 Cortico vts-client）

> 文档日期：2026-09-21
> 对照对象：可提 `cortico-world-vtuber` 的 `src/vts-client.ts`
> 改造对象：本项目 `live2d/vtuber_studio_info.py`（`VTubeStudioAPI` 类）
> 目标：把直播场景"皮套突然不动但没报错"的半开/静默死问题根治掉。

## 0. 结论摘要

现有 `VTubeStudioAPI` 用 `websockets` 裸连接，协议层与可提完全一致（`VTubeStudioPublicAPI`/`1.0`），但连接治理是**裸 WS + 超时**：无心跳、无重连、无半开检测、无世代隔离。可提把它当"不可信、会半开、可能静默死"的通道治理，7 条机制值得抄。

**优先级判断**：只需 P0 3 条 + P1 2 条就能覆盖 95% 的直播稳定性收益；P2 是可选加固。

## 1. 现状 vs 可提 差距表

| 机制 | 现状 vtuber_studio_info.py | 可提 vts-client.ts | 差距 | 优先级 |
|---|---|---|---|---|
| 分层超时 | `send_request(timeout=2.0)`，注入 0.2/快照 2.0（调用方传） | `INJECT_TIMEOUT_MS=2000` / `CONTROL_TIMEOUT_MS=5000` / `AUTH_POPUP_TIMEOUT_MS=60000` | 你已支持传参，缺默认分层 | P2 |
| timeout 后重试 | 无，返回 None | 有重试语义 + 熔断 | **缺** | P1 |
| **半开检测** | 无——`recv` 只阻塞当前请求，掉线只靠下一次请求发现 | `NO_ACK_MS=3000` 无任何消息即判半开 | **缺，核心** | **P0** |
| **心跳看门狗** | 无 ping/pong | `WATCH_TICK_MS=1000` + `PING_TICKS=5`（5s 一 ping） | **缺，核心** | **P0** |
| **自动重连** | 无——掉线后 `connect()` 只被调用一次，之后 ws 永久 | 指数退避 `[500,1k,2k,4k,5k]` 封顶 5s | **缺，核心** | **P0** |
| **连接世代 gen 隔离** | 无 | 换 socket `gen+1`，旧代应答/超时/看门狗作废 | **缺，核心** | **P0** |
| 熔断 | 无 | `TIMEOUT_STREAK_LIMIT=3` 后熔断 | 缺 | P1 |
| terminate vs close | `disconnect` 用 `await ws.close()` | 半开时 `terminate()`，`CLOSE_DEADLINE_MS=1000` 自拆 | 缺 | P2 |
| token 判无效即停 | 无 | 认证被拒后停重连（防反复弹窗） | 缺 | P2 |
| 事件订阅 | 无 | 订阅 `ModelLoadedEvent`，每次认证成功后重订 | 缺 | P2（配合模型热切换才有用） |
| **每帧不等控制类** | 注入走同一 `send_request`，锁内串行 | 注入 2s、控制 5s 分开 | 你已用 operation_lock | — |
| 共享 recv 竞态 | 单客户端共享 ws.recv，靠 live2d_main 的 operation_lock 串行 | 每个 pending 一个 Promise + 世代 | **结构不同**：可提按 requestID 分发，天然并发 | P2（重构可选） |

## 2. 改造清单

### P0 — 直播稳定性核心（建议立即做，改动集中在 `VTubeStudioAPI`）

**P0-1 心跳看门狗 + 半开检测**
在 `VTubeStudioAPI` 加一个后台任务：
```
watch_every = 1.0s
每 5 拍 (5s) 发一次 ping
标志 awaiting_pong = True
收到任意对端消息 → last_ack = now, awaiting_pong = False
若 now - last_ack > 3.0s 且 awaiting_pong → 判定半开 → 触发重连
```
改造点：`connect()` 里 `asyncio.create_task(_watchdog())`；`recv` 到任何消息更新 `last_ack`。

**P0-2 指数退避自动重连**
拦截掉线：`connect()`/`send_request()` 捕获 `ConnectionClosed` 时 → 排重连任务（不是立即 connect，避免 60Hz 注入打成 60Hz 连接风暴）：
```
BACKOFF_MS = [500, 1000, 2000, 4000, 5000]  # 封顶 5s 不再涨
成功后重置 attempt=0
```
改造点：加 `_schedule_reconnect()`；`disconnect()` 置 `closed_by_user` 停重连。

**P0-3 连接世代隔离**
每个 socket 挂 `gen`，换 socket `gen+=1`；看门狗/超时/重连回调都带当前 gen，`gen` 不匹配直接丢弃。防止旧 socket 的迟到应答污染新连接。

### P1 — 稳定性增强（推荐做）

**P1-1 超时熔断**
```
_conn_timeout_streak = 0
send_request 超时 → streak += 1
streak >= 3 → 停止发请求并强制重连（熔断）
成功 → streak = 0
```

**P1-2 注入与控制超时分档默认值**
给 `send_request` 的 timeout 定义默认档（注入 2s / 控制 5s / 认证 60s），调用方不必每次传。

### P2 — 结构加固（可选，建议换模型/做大重构时）

**P2-1 terminate + 关闭死线**
半开时 `await ws.close()` 会等对端回 Close 帧（ws 库死线 30s 太久）。改：维护关闭计数，超 1s 直接 `ws.transport.abort()`。`websockets` 库对应 `await ws.close(close_code)` 后若不平再 abort。

**P2-2 token 判无效即停**
`authenticate` 收到 APIError（token 无效）→ 置 `auth_rejected`，停重连（否则反复弹 VTS 授权窗），并上抛通知上层报障。

**P2-3 事件订阅**
`connect` 成功后自动订阅 `ModelLoadedEvent`，模型热切换时重查参数范围/换算表（配合 P0-2 的重建连接期状态）。

**P2-4 按 requestID 分发（可选重构）**
现 `send_request` 是一个 `recv` 等一个响应，多请求并发靠上层 `operation_lock` 硬串行。可提按 `requestID` 维护 pending Map，响应到达按 id 分发，天然并发。改动大，仅当需要并发注入时再做。

## 3. 最小侵入实现要点

- **不改协议**：所有 `messageType`/`apiName`/认证流程照旧，只动连接生命周期
- **不动 live2d_main 的调用方**：`VTubeStudioAPI` 内部加机制，`send_request` 签名不变
- **operation_lock 保留**：即使加了按 id 分发，`websockets` 库自身的 send/recv 竞态仍建议锁（或迁移到按 id 分发方案后解锁）
- **可观测**：心跳/重连/熔断各打一条日志（可提的 log event：`reconnected`/`breaker`/`reconnect-stopped`）

## 4. 验证方式

1. 正常启动连 VTS，idle 跑 30s，日志无异常、注入帧率稳定
2. **断连测试**：停止/杀 VTS → 观看向导日志出现 `半开→重连`，5s 内封顶退避不明显刷屏；重启 VTS → 自动连上恢复
3. **注入风暴测试**：`--live2d` 满速注入时杀 VTS，确认不会出现 60Hz 连接尝试风暴（P0-2 退避闸门生效）
4. **token 无效测试**：篡改 token，确认停重连并上抛，而非反复弹窗

## 5. 与可提机制映射表

| 可提常量/方法 | 本项目落地 |
|---|---|
| `WATCH_TICK_MS=1000` / `PING_TICKS=5` / `NO_ACK_MS=3000` | `_watchdog()` 心跳 + `_last_ack` 半开检测 |
| `RECONNECT_BACKOFF_MS=[500,1k,2k,4k,5k]` | `_schedule_reconnect()` 退避 |
| `gen` + `Pending` | 每次换 socket `self._gen += 1` |
| `TIMEOUT_STREAK_LIMIT=3` | `_conn_timeout_streak` 熔断 |
| `AUTH_POPUP_TIMEOUT_MS=60000` | `authenticate` 默认 timeout 分档 |
| `onAuthRejected` | `self._auth_rejected` 停重连 + 上抛 |
| `EventSubscriptionRequest(ModelLoadedEvent)` | `connect` 成功后订阅 |
| `ensureConnected()` 不直接 connect | 每帧注入路径只在未连接时排重连 |
# CherryStudio Connection Chain Deep Audit Report

**Project**: QQ-MCP Bridge v3.0  
**Audit Date**: 2026-06-07  
**Error Under Investigation**: `AI服务连接失败 [BRG-4001]`  
**Auditor**: Automated Code Analysis  

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Complete Connection Chain Diagram](#2-complete-connection-chain-diagram)
3. [BRG-4001 Trigger Conditions Analysis](#3-brg-4001-trigger-conditions-analysis)
4. [Config Validation Checklist](#4-config-validation-checklist)
5. [Session Lifecycle Analysis](#5-session-lifecycle-analysis)
6. [Retry and Recovery Gaps](#6-retry-and-recovery-gaps)
7. [Code Bugs and Design Issues](#7-code-bugs-and-design-issues)
8. [Old vs New System Comparison](#8-old-vs-new-system-comparison)
9. [Recommended Fixes (Prioritized)](#9-recommended-fixes-prioritized)

---

## 1. Executive Summary

The BRG-4001 error ("AI服务连接失败") is raised at `cherrystudio_module.py` line 2104-2110 when the SSE streaming request to CherryStudio Agent API fails due to `aiohttp.ClientError` or `asyncio.TimeoutError`. The audit reveals **3 critical bugs**, **2 major design flaws**, and **2 significant gaps** in retry/recovery logic compared to the old system.

### Critical Findings

| # | Severity | Issue | File:Line |
|---|----------|-------|-----------|
| 1 | **CRITICAL** | Infinite tight loop on session creation failure | `cherrystudio_module.py:1628-1646` |
| 2 | **CRITICAL** | `ErrorCode.SESSION_EXPIRED` does not exist (runtime AttributeError) | `cherrystudio_module.py:2125` |
| 3 | **CRITICAL** | No retry on BRG-4001 (old system had 1-retry with 1s delay) | `cherrystudio_module.py:2104-2110` |
| 4 | **HIGH** | `get_sse_request_context()` has no None-check on `self._session` | `cherrystudio_module.py:1419` |
| 5 | **HIGH** | Agent ID resolution failure does not block session creation | `cherrystudio_module.py:2411-2417` |
| 6 | **MEDIUM** | Default port mismatch: legacy adapter uses 23333, new defaults use 8080 | `server.py:1001` vs `cherrystudio_module.py:2268` |
| 7 | **MEDIUM** | Session handler timeout (120s) kills handler before recovery possible | `cherrystudio_module.py:1529` |
| 8 | **LOW** | Health check failures during init are silently logged but not tracked | `cherrystudio_module.py:1023-1038` |

---

## 2. Complete Connection Chain Diagram

```
User sends QQ message
    |
    v
[NapCatBridge] (WebSocket recv)
    |
    v
[MessageBus] (routes by type)
    |  - Command messages -> CommandModule
    |  - Regular messages -> CherryStudioModule
    v
[CherryStudioModule.start()] (main loop, line 2493)
    |  - _should_reply() filter check
    |  - Cooldown check
    |  - Get/create CherryStudioSessionHandler
    v
[CherryStudioSessionHandler.add_message()] (enqueue to handler)
    |
    v
[CherryStudioSessionHandler._run()] (per-session async loop, line 1563)
    |
    |  Step A: Session Acquisition (line 1575-1646)
    |  +--------------------------------------------+
    |  | if not self.session_data:                  |
    |  |   agent_name = state_manager.get_active()  |
    |  |   saved_model = state_manager.get_model()  |
    |  |   session_id = http_client.create_session()|
    |  |                                            |
    |  |   POST {base_url}/v1/agents/{agent_id}/    |
    |  |        sessions  (legacy_mode)             |
    |  |   or                                       |
    |  |   POST {base_url}/sessions  (standard)     |
    |  |                                            |
    |  |   if session_id:                           |
    |  |     -> create SessionData                  |
    |  |     -> load ConversationStore history      |
    |  |     -> check stale session / archive       |
    |  |   else:                                    |
    |  |     -> send error "AI服务初始化失败"       |
    |  |     -> continue  *** BUG: infinite loop ***|
    |  +--------------------------------------------+
    |
    |  Step B: Message Processing (line 1649)
    |  +--------------------------------------------+
    |  | _process_message(msg)                      |
    |  |   1. Vision recognition (if images)        |
    |  |   2. File processing (if attachments)      |
    |  |   3. Reply chain resolution                |
    |  |   4. Session validation (line 2001)        |
    |  |   5. ConversationStore: record user msg    |
    |  |   6. Inject workspace context (new sess)   |
    |  |   7. mark_responding(target_id)            |
    |  +--------------------------------------------+
    |
    |  Step C: SSE Streaming Call (line 2068-2110)
    |  +--------------------------------------------+
    |  | ctx = http_client.get_sse_request_context( |
    |  |   session_id, message, agent_id, timeout)  |
    |  |                                            |
    |  | POST {base_url}/v1/agents/{agent_id}/      |
    |  |      sessions/{sid}/messages  (legacy)     |
    |  | or                                         |
    |  | POST {base_url}/chat  (standard)           |
    |  |                                            |
    |  | Headers:                                   |
    |  |   Authorization: Bearer {api_key}          |
    |  |   Content-Type: application/json           |
    |  |   Timeout: 600s total                      |
    |  |                                            |
    |  | async with ctx as resp:                    |
    |  |   HTTP 404/410 -> session expired          |
    |  |   HTTP != 200  -> LLM_PROVIDER_FAILED      |
    |  |   HTTP 200     -> SSEParser.parse(resp)    |
    |  |                                            |
    |  | except (ClientError, TimeoutError):        |
    |  |   -> BRG-4001 "AI服务连接失败"             |
    |  |   *** BUG: no retry ***                    |
    |  +--------------------------------------------+
    |
    |  Step D: SSE Response Processing (line 2112-2224)
    |  +--------------------------------------------+
    |  | SSEResult analysis:                        |
    |  |   session_not_found -> clear SID           |
    |  |   reply_text -> ModuleResponse.success     |
    |  |   had_output_tool -> success (tool sent)   |
    |  |   stalled -> 2-strike system               |
    |  |   error -> LLM_PROVIDER_FAILED             |
    |  |   no output -> LLM_PROVIDER_FAILED         |
    |  +--------------------------------------------+
    |
    |  Step E: Response Dispatch (line 1652-1676)
    |  +--------------------------------------------+
    |  | send_queue -> OutgoingMessage              |
    |  |   -> MessageBus.send_message_queue         |
    |  |   -> NapCatBridge.send_message()           |
    |  |   -> QQ user receives reply                |
    |  +--------------------------------------------+
```

---

## 3. BRG-4001 Trigger Conditions Analysis

BRG-4001 is raised at exactly one location in the codebase:

**File**: `C:\CherryStudio\qq-mcp-bridge\modules\cherrystudio_module.py`  
**Lines**: 2104-2110

```python
except (aiohttp.ClientError, asyncio.TimeoutError) as e:
    logger.error(f"SSE 请求失败: {e}")
    return ModuleResponse.error_response(
        ErrorCode.CHERRY_STUDIO_CONNECTION_FAILED.code,
        error_detail=str(e),
        custom_text="AI服务连接失败",
    )
```

### All Possible Trigger Conditions

| # | Condition | Exception Type | Likelihood | Details |
|---|-----------|---------------|------------|---------|
| 1 | **CherryStudio application not running** | `aiohttp.ClientConnectorError` | **HIGH** | Connection refused on port 23333. Most common cause for first-time setup or after CherryStudio restart. |
| 2 | **Wrong base_url / port mismatch** | `aiohttp.ClientConnectorError` | **HIGH** | If `agent_api_url` not in config, legacy adapter defaults to `127.0.0.1:23333`. If CherryStudio runs on a different port, connection fails. |
| 3 | **Network timeout (SSE total timeout 600s)** | `asyncio.TimeoutError` | **MEDIUM** | The SSE total timeout is 600s (10 min). If CherryStudio accepts the connection but never responds, this fires. |
| 4 | **Stall timeout accumulation** | `asyncio.TimeoutError` | **MEDIUM** | Individual readline timeout is 30s x 4 retries = 120s of no data before stall. But total_timeout (600s) takes precedence. |
| 5 | **DNS resolution failure** | `aiohttp.ClientConnectorError` | **LOW** | Only if base_url uses hostname instead of IP. Default uses `127.0.0.1`. |
| 6 | **Firewall blocking localhost port** | `aiohttp.ClientConnectorError` | **LOW** | Windows firewall could block port 23333. Unlikely for localhost traffic. |
| 7 | **HTTP session not initialized** | `AttributeError` (NOT CAUGHT) | **MEDIUM** | If `http_client._session` is None, `get_sse_request_context()` raises AttributeError which is NOT caught by the except clause. This would propagate as an unhandled exception, NOT as BRG-4001. |
| 8 | **API key invalid** | (does NOT trigger BRG-4001) | N/A | Invalid API key causes HTTP 401/403, which is handled at line 2091-2098 as BRG-4004 (LLM_PROVIDER_FAILED), not BRG-4001. |
| 9 | **Agent ID mismatch** | (does NOT trigger BRG-4001 directly) | N/A | Wrong agent_id causes HTTP 404 on session creation, which is handled at line 1628-1646 (SESSION_CREATE_FAILED / "AI服务初始化失败"), not BRG-4001. |
| 10 | **TCP connection pool exhaustion** | `aiohttp.ClientConnectorError` | **LOW** | aiohttp default pool limit is 100 connections. Unlikely for single-agent bridge. |

### Most Likely Root Cause for This User

Based on the config at `C:\CherryStudio\qq-mcp-bridge\config.json`:

1. Config has NO `agent_api_url` key. Legacy adapter defaults to `http://127.0.0.1:23333`.
2. If CherryStudio is not running or its Agent API is on a different port, BRG-4001 fires.
3. **The user should verify**: (a) CherryStudio is running, (b) CherryStudio's Agent API is accessible at `http://127.0.0.1:23333`.

**Quick diagnostic command**:
```
curl http://127.0.0.1:23333/health
curl http://127.0.0.1:23333/v1/agents
```

---

## 4. Config Validation Checklist

### Current Config State

**Active config file**: `C:\CherryStudio\qq-mcp-bridge\config.json` (root directory, preferred by Server.__init__)

| Config Key | Present | Value | Status |
|------------|---------|-------|--------|
| `cherrystudio` (section) | NO | - | Legacy adapter triggered |
| `cherry_api_key` | YES | `cs-sk-b9f3be95-...` | Legacy key, adapted to `cherrystudio.api_key` |
| `agent_api_url` | NO | (defaults to `http://127.0.0.1:23333`) | **WARNING**: Not explicitly set |
| `mcp_server_name` | YES | `"QQ Bridge"` | Adapted to `cherrystudio.mcp_server_name` |
| `default_agent` | YES | `"麦哲伦QQ"` | Used as display name for agent ID resolution |
| `agent_enabled` | YES | `true` | Agent mode enabled |
| `agent_timeout_seconds` | YES | `60` | But SSE total_timeout uses 600s from `_agent_timeout` |
| `napcat.ws_host` | YES | `"127.0.0.1"` | OK |
| `napcat.ws_port` | YES | `3001` | OK |
| `napcat.access_token` | YES | `"zg4ovY8TVkOQVXY2"` | OK |
| `llm` (providers) | YES | 2 providers (OpenCode, DeepSeek) | Adapted to `llm_providers` |
| `default_llm.provider` | YES | `0` (OpenCode) | OK |
| `default_llm.model` | YES | `"minimax-m2.5"` | OK |

### Legacy Config Adaptation Flow

```
config.json (old format, no "cherrystudio" section)
    |
    v
Server._adapt_legacy_config()  (server.py:975-1039)
    |
    |  Detects: cherry_api_key, mcp_server_name -> has_legacy_keys = True
    |
    v
Synthesized cherrystudio section:
    {
        "mcp_server_path": null,
        "http_api_base": "http://127.0.0.1:23333",  <-- from agent_api_url default
        "api_key": "cs-sk-b9f3be95-...",
        "legacy_mode": true,
        "mcp_server_name": "QQ Bridge"
    }
```

### Issues Found

1. **Missing `agent_api_url`**: The user's config does NOT specify `agent_api_url`. The legacy adapter defaults to `http://127.0.0.1:23333`. If CherryStudio runs on a different port (e.g., 8080), all connections will fail with BRG-4001.

2. **No `cherrystudio` section**: The config uses the old format. While the legacy adapter handles this, it means the user cannot benefit from new-format features like `mcp_server_path` or explicit `agent_name`/`agent_id` configuration.

3. **`agent_timeout_seconds: 60` vs SSE `total_timeout: 600`**: The config says 60 seconds, but `_agent_timeout` at line 1513 reads `self.config.get("agent_timeout_seconds", 600)`. Since the config has 60, this should be 60. But the SSE parser's total_timeout is set to `self._agent_timeout` (60s). The stall_timeout is 30s with 4 retries = 120s max stall. This means the stall retry limit (120s) EXCEEDS the total timeout (60s), so total timeout fires first.

4. **Empty `agents` section**: `"agents": {}` is present but empty. Agent discovery relies on `_discover_agents()` which queries CherryStudio's `/v1/agents` API at runtime. If CherryStudio is down during init, `discovered_agents` will be empty.

### Config Example Comparison

The `config.example.json` (at `Configuration/config.example.json`) uses the NEW format with port 8080:
```json
{
    "cherrystudio": {
        "http_api_base": "http://127.0.0.1:8080"
    }
}
```

But the legacy adapter defaults to port 23333. This inconsistency is confusing. The user should explicitly set the correct port.

---

## 5. Session Lifecycle Analysis

### Session Creation Flow

```
CherryStudioSessionHandler._run() (line 1563)
    |
    v
[Message arrives from queue]
    |
    v
self.session_data is None?  --YES-->
    |
    agent_name = state_manager.get_active_agent(session_key)
    saved_model = state_manager.get_saved_model(session_key)
    |
    v
http_client.create_session(agent_name, agent_id, saved_model)
    |
    |  Legacy mode: POST /v1/agents/{agent_id}/sessions
    |  Body: {"name": agent_name, "accessible_paths": [], "model": saved_model}
    |
    v
session_id returned?
    |--YES--> Create SessionData, set session_id, load memory, inject context
    |--NO---> Send error "AI服务初始化失败" [BRG-4005]
              continue  *** BUG: returns to while loop, retries same message ***
```

### Session ID = None Check (line 2001)

At `cherrystudio_module.py:2001-2006`:
```python
if not self.session_data or not self.session_data.session_id:
    return ModuleResponse.error_response(
        ErrorCode.SESSION_CREATE_FAILED.code,
        error_detail="会话未创建",
        custom_text="AI服务初始化失败",
    )
```

This check is reached ONLY if `session_data` exists but `session_id` is None. This happens in two scenarios:

1. **Session creation succeeded initially but SID was later cleared**: Due to HTTP 404/410 (line 2084) or session_not_found in SSE (line 2123). On the NEXT message, `_run()` sees `session_data` with `session_id=None`, tries to create a new session.

2. **Session creation failed in `_run()` but code continued to `_process_message`**: This should NOT happen because the `continue` at line 1646 skips `_process_message`. But it could happen if there's a race condition or code path that bypasses the session creation block.

### Session Cleanup and Rebuild

When a session handler exits (timeout, exception, or stall destruction):

```
_cleanup() called (line 1695)
    |
    v
ConversationStore.save_session()  -- persist conversation data
http_client.delete_session()      -- DELETE /v1/agents/{id}/sessions/{sid}
on_cleanup(session_key)           -- remove handler from parent dict
    |
    v
Handler removed from self.session_handlers dict
    |
    v
Next incoming message creates a NEW handler via CherryStudioModule.start()
    |
    v
New handler starts fresh session creation
```

### The `session_data.session_id = None` Pattern

The code uses `self.session_data.session_id = None` as a signal to rebuild:

- Line 2084: HTTP 404/410 response -> clear SID
- Line 2123: session_not_found in SSE -> clear SID
- Line 2203: 2-strike stall destruction -> clear SID

After clearing, the next message should trigger re-creation. But there's a subtle issue: `session_data` still exists (not None), so the `_run()` loop at line 1575 (`if not self.session_data`) will NOT trigger session re-creation. The None check at line 2001 will catch it, but only AFTER entering `_process_message()`.

**This is a design smell**: `session_data` exists but `session_id` is None. The `_run()` loop should check `session_data.session_id` as well.

---

## 6. Retry and Recovery Gaps

### Gap 1: No Retry on SSE Connection Failure (BRG-4001)

**Old system** (`auto_reply.py:497-508`):
```python
async def _call_agent_api(self, conv, msg_type, target_id, user_text):
    for attempt in range(2):
        try:
            return await self._call_agent_api_once(...)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            if attempt == 0:
                logger.info(f"Agent API 请求失败，1s 后重试...")
                await asyncio.sleep(1)
                continue
            raise
        except Exception:
            raise
```

**New system** (`cherrystudio_module.py:2078-2110`):
```python
try:
    async with ctx as resp:
        # ... handle response ...
except (aiohttp.ClientError, asyncio.TimeoutError) as e:
    # NO RETRY - immediately returns error
    return ModuleResponse.error_response(
        ErrorCode.CHERRY_STUDIO_CONNECTION_FAILED.code, ...)
```

**Impact**: Transient network issues (CherryStudio briefly restarting, TCP reset) cause immediate user-visible failure. The old system absorbed these with a 1-second retry.

### Gap 2: Session Creation Failure Infinite Loop

**Old system** (`auto_reply.py:497-508`): Session creation failure returns None, which propagates as "Agent 无回复" and sends a fallback message. The message is consumed and the worker moves on.

**New system** (`cherrystudio_module.py:1628-1646`): Session creation failure sends error message, then `continue` returns to the `while self._running` loop. The SAME message is dequeued again (it was already consumed from the queue, so the loop goes back to waiting). Actually wait -- the message was consumed at line 1569 (`msg = await self.message_queue.get()`), and `continue` goes back to `await self.message_queue.get()` for the NEXT message. So the next message will ALSO fail session creation.

**Impact**: Every incoming message while CherryStudio is down generates an "AI服务初始化失败" error. No recovery mechanism exists other than the handler timing out (120s idle) and a new handler being created.

### Gap 3: No Auto-Reconnect After BRG-4001

When BRG-4001 fires, the error response is returned and the session handler continues running. The handler's session still exists (`session_data` is not None, `session_id` is not cleared). On the next message:

1. `_run()` sees `session_data` exists -> skips session creation
2. Calls `_process_message()` 
3. Line 2001: `session_data.session_id` is still set -> passes check
4. Attempts SSE request again -> BRG-4001 again

**This is correct behavior for transient failures** (the session is still valid on the CherryStudio side). But if CherryStudio has restarted (invalidating all sessions), the next SSE request gets HTTP 404/410, which triggers session_id = None, and the message after that creates a new session. So there IS an implicit recovery path, just with a 2-message delay.

### Gap 4: Handler Timeout Kills Recovery Window

The session handler has a 120-second idle timeout (line 1529):
```python
self.timeout = 120
```

If CherryStudio is down for more than 2 minutes, the handler times out and `_cleanup()` is called. The cleanup tries to DELETE the remote session (which fails because CherryStudio is down). When CherryStudio comes back, a new handler is created, which creates a new session. This works, but:

- The DELETE call during cleanup will fail with a network error (logged as warning, not fatal).
- The new session won't have the old session's conversation context (because ConversationStore saves during cleanup but may fail if there are issues).

### Recovery Flow Summary

```
CherryStudio goes down
    |
    v
Message 1: BRG-4001 "AI服务连接失败"  (session_id still set)
    |
Message 2: BRG-4001 "AI服务连接失败"  (session_id still set, no retry)
    |
... (messages keep failing) ...
    |
    |  After 120s idle timeout:
    v
Handler cleanup: DELETE session fails, handler removed
    |
    |  CherryStudio comes back:
    v
New message -> new handler -> new session -> SUCCESS
```

**Worst case**: If messages keep arriving (less than 120s apart), the handler never times out, and every message gets BRG-4001. Recovery only happens when there's a 2-minute gap in messages.

---

## 7. Code Bugs and Design Issues

### Bug 1 (CRITICAL): Infinite Tight Loop on Session Creation Failure

**File**: `C:\CherryStudio\qq-mcp-bridge\modules\cherrystudio_module.py`  
**Lines**: 1628-1646

```python
else:
    # 会话创建失败，返回错误并标记为已处理，避免无限重试
    response = ModuleResponse.error_response(
        ErrorCode.SESSION_CREATE_FAILED.code,
        error_detail="无法创建 CherryStudio 会话",
        custom_text="AI服务初始化失败",
    )
    if self.response_queue:
        await self.response_queue.put(response)
    elif self.send_queue:
        await self.send_queue.put(OutgoingMessage(...))
    # 设置一个临时的 session_data 标记，防止下次循环重复尝试
    # 但仍然允许新消息触发重试
    continue
```

**Problem**: The comment says "避免无限重试" (avoid infinite retry) but the code does NOT prevent it. After `continue`, the loop returns to `await self.message_queue.get()`. For each new incoming message, session creation is attempted again, fails again, and another error is sent. This generates spam error messages to the user.

**More critically**: If the message queue already has messages buffered, each one triggers a session creation attempt and an error response in rapid succession, flooding the QQ chat with "AI服务初始化失败" messages.

### Bug 2 (CRITICAL): Nonexistent ErrorCode.SESSION_EXPIRED

**File**: `C:\CherryStudio\qq-mcp-bridge\modules\cherrystudio_module.py`  
**Line**: 2125

```python
if sse_result.session_not_found:
    logger.warning("session_not_found，清除会话 ID")
    self.session_data.session_id = None
    return ModuleResponse.error_response(
        ErrorCode.SESSION_EXPIRED.code,     # <-- AttributeError!
        error_detail="session_not_found",
        custom_text="会话已失效，请重新发送消息",
    )
```

**Problem**: `ErrorCode.SESSION_EXPIRED` does not exist in the enum. The enum only defines:
- `COMMAND_SESSION_EXPIRED` (BRG-3006)
- `CHERRY_SESSION_EXPIRED` (BRG-4009)

When this code path executes, Python raises `AttributeError: SESSION_EXPIRED is not a member of ErrorCode`. This exception propagates out of `_process_message()` and is caught by the generic exception handler in `_run()` at line 1688-1690, which logs the error but does NOT send any response to the user. The session is left in a broken state (session_id = None was set before the crash, but the error response was never returned).

**Note**: Line 2086 uses `ErrorCode.CHERRY_SESSION_EXPIRED.code` correctly (for HTTP 404/410). The bug is only at line 2125 (SSE session_not_found).

### Bug 3 (HIGH): No None-Check in get_sse_request_context

**File**: `C:\CherryStudio\qq-mcp-bridge\modules\cherrystudio_module.py`  
**Line**: 1419

```python
def get_sse_request_context(self, session_id, message, agent_id=None, total_timeout=600):
    # ... URL and body construction ...
    timeout = aiohttp.ClientTimeout(total=total_timeout)
    return self._session.post(url, json=body, timeout=timeout)  # AttributeError if _session is None
```

**Problem**: If `self._session` is None (e.g., HTTPClient was closed or never properly initialized), this raises `AttributeError: 'NoneType' object has no attribute 'post'`. This exception is NOT caught by the `except (aiohttp.ClientError, asyncio.TimeoutError)` at line 2104, so it propagates as an unhandled exception.

### Bug 4 (HIGH): Agent ID Resolution Failure Not Blocking

**File**: `C:\CherryStudio\qq-mcp-bridge\modules\cherrystudio_module.py`  
**Lines**: 2395-2417

```python
if self.http_client.legacy_mode and self.agent_id:
    display_name = self.agent_id
    resolved_id = await self.http_client.fetch_agent_id(display_name)
    if resolved_id:
        self.agent_id = resolved_id
    else:
        # Retry once after 3 seconds
        await asyncio.sleep(3)
        resolved_id = await self.http_client.fetch_agent_id(display_name)
        if resolved_id:
            self.agent_id = resolved_id
        else:
            logger.error(f"[BRG-2020] Agent ID 解析最终失败: '{display_name}'...")
            # NO EXCEPTION RAISED, module continues with display name as agent_id
```

**Problem**: If agent ID resolution fails (CherryStudio down during init), `self.agent_id` remains as the display name string (e.g., "麦哲伦QQ"). All subsequent API calls use this display name in the URL path:

```
POST /v1/agents/麦哲伦QQ/sessions
```

This returns HTTP 404 because CherryStudio expects an internal ID like `agent_1780254091652_boosaiyfg`. Session creation fails, triggering the infinite loop (Bug 1).

### Issue 5 (MEDIUM): Port Default Mismatch

| Source | Default Port |
|--------|-------------|
| Legacy adapter (`server.py:1001`) | 23333 |
| HTTPClient default (`cherrystudio_module.py:2268`) | 8080 |
| config.example.json (`Configuration/config.example.json:9`) | 8080 |
| README.md example | 8080 |
| Old system (`auto_reply.py:153`) | 23333 |

The legacy adapter (triggered by the user's config) correctly uses 23333. But if a user manually creates a `cherrystudio` section without specifying `http_api_base`, the HTTPClient defaults to 8080. This mismatch is a common source of confusion.

### Issue 6 (MEDIUM): Handler Timeout vs Recovery Time

The handler timeout is 120 seconds (line 1529). If CherryStudio is down:
- Messages arrive every few seconds
- Each gets BRG-4001
- Handler never times out (messages keep arriving)
- No recovery until there's a 120s message gap

The old system's worker timeout was 600 seconds (10 minutes), giving a longer window for recovery.

---

## 8. Old vs New System Comparison

### Connection Establishment

| Aspect | Old System (`auto_reply.py`) | New System (`cherrystudio_module.py`) |
|--------|------------------------------|--------------------------------------|
| HTTP session | Shared `aiohttp.ClientSession()` created at `__init__` time | Created during `initialize()` via `HTTPClient.initialize()` |
| Health check | None at startup | GET `/health` at startup (non-fatal on failure) |
| Agent ID resolution | Done during `_fetch_agents_from_cherrystudio()` at startup | Done during `initialize()`, 2 attempts with 3s delay |
| Session creation | `_get_or_create_session()` per message | `_run()` loop creates session before processing |
| Session reuse | Persisted via `get_agent_session_id()` (file-based) | SessionData in memory, ConversationStore for persistence |

### Error Handling

| Aspect | Old System | New System |
|--------|-----------|------------|
| Network error retry | 1 retry with 1s delay (`_call_agent_api` for loop) | **No retry** -- immediate BRG-4001 |
| Session not found | `raise SessionNotFoundError` -> rebuild session | Set `session_id = None` + return error |
| Stalled SSE | 2-strike system (same logic) | 2-strike system (same logic) |
| Agent API down | Returns None, sends fallback message | Sends error message, handler stays alive |
| Worker lifecycle | 600s idle timeout, auto-restart on new message | 120s idle timeout, auto-restart on new message |

### SSE Parsing

| Aspect | Old System | New System |
|--------|-----------|------------|
| Parser | Inline in `_call_agent_api_once()` (~200 lines) | Separate `SSEParser` class (517 lines) |
| Stall detection | Manual: `stall_count`, `STALL_TIMEOUT` | Encapsulated in SSEParser with configurable retries |
| Tool tracking | `has_tool_calls`, `_had_tool_call`, `last_tool_name` | `SSEToolCall` objects, `had_output_tool` flag |
| Text deduplication | Not implemented | `_deduplicate_text()` handles overlap |
| Reasoning content | `in_reasoning` flag, discarded | `reasoning_blocks` collected, discarded in result |
| Error events | Parsed inline, `session_not_found` triggers rebuild | `SSEResult.error` and `session_not_found` flags |

### Recovery Mechanisms

| Scenario | Old System | New System |
|----------|-----------|------------|
| CherryStudio restart | Session not found -> rebuild | HTTP 404/410 -> clear SID -> rebuild on next msg |
| Network blip | 1 retry with 1s delay | Immediate failure, no retry |
| Agent ID changed | Re-fetched on next `_fetch_agents` call | Only fetched at init time, not refreshed |
| Session stale (>3 days) | `_summarize_and_cleanup()` | `_check_and_archive_stale()` (same logic) |
| Handler crash | Worker auto-restarts on new message | Handler auto-restarts via new handler creation |

---

## 9. Recommended Fixes (Prioritized)

### Priority 1: Fix Infinite Loop on Session Creation Failure

**File**: `C:\CherryStudio\qq-mcp-bridge\modules\cherrystudio_module.py`  
**Lines**: 1628-1646

**Fix**: Add a cooldown after session creation failure, or set a temporary sentinel to prevent re-attempts for a period.

```python
else:
    response = ModuleResponse.error_response(
        ErrorCode.SESSION_CREATE_FAILED.code,
        error_detail="无法创建 CherryStudio 会话",
        custom_text="AI服务初始化失败",
    )
    # Send error to user
    if self.send_queue:
        await self.send_queue.put(OutgoingMessage(...))
    
    # Set temporary session_data to prevent tight loop
    self.session_data = SessionData(
        session_key=self.session_key,
        agent_name=agent_name,
    )
    # session_data.session_id remains None
    # _process_message will catch this at line 2001
    # and return SESSION_CREATE_FAILED
```

This way, the next message goes through `_process_message()` which checks `session_data.session_id` at line 2001 and returns an error WITHOUT attempting session creation. But we also need `_process_message` to attempt re-creation periodically. A better approach:

```python
else:
    # Send error to user
    ...
    # Break out of the loop; handler cleanup will occur.
    # A new handler will be created for the next message.
    logger.error(f"Session creation failed, stopping handler: {self.session_key}")
    break
```

### Priority 2: Fix ErrorCode.SESSION_EXPIRED Reference

**File**: `C:\CherryStudio\qq-mcp-bridge\modules\cherrystudio_module.py`  
**Line**: 2125

**Fix**: Change `ErrorCode.SESSION_EXPIRED` to `ErrorCode.CHERRY_SESSION_EXPIRED`:

```python
return ModuleResponse.error_response(
    ErrorCode.CHERRY_SESSION_EXPIRED.code,  # was: SESSION_EXPIRED
    error_detail="session_not_found",
    custom_text="会话已失效，请重新发送消息",
)
```

### Priority 3: Add Retry on SSE Connection Failure

**File**: `C:\CherryStudio\qq-mcp-bridge\modules\cherrystudio_module.py`  
**Lines**: 2078-2110

**Fix**: Add a 1-retry mechanism matching the old system:

```python
for _sse_attempt in range(2):
    try:
        async with ctx as resp:
            # ... existing response handling ...
            pass
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        if _sse_attempt == 0:
            logger.warning(f"SSE 请求失败，1s 后重试: {e}")
            await asyncio.sleep(1)
            # Rebuild context for retry
            ctx = self.http_client.get_sse_request_context(
                session_id=self.session_data.session_id,
                message=content,
                agent_id=self.agent_id,
                total_timeout=self._agent_timeout,
            )
            continue
        logger.error(f"SSE 请求失败 (重试后): {e}")
        return ModuleResponse.error_response(
            ErrorCode.CHERRY_STUDIO_CONNECTION_FAILED.code,
            error_detail=str(e),
            custom_text="AI服务连接失败",
        )
```

### Priority 4: Add None-Check in get_sse_request_context

**File**: `C:\CherryStudio\qq-mcp-bridge\modules\cherrystudio_module.py`  
**Line**: 1419

**Fix**:

```python
def get_sse_request_context(self, session_id, message, agent_id=None, total_timeout=600):
    if not self._session:
        raise aiohttp.ClientError("HTTP session not initialized (HTTPClient._session is None)")
    # ... rest of method ...
```

This ensures the error is caught by the `aiohttp.ClientError` handler at line 2104 and produces a proper BRG-4001 instead of an unhandled AttributeError.

### Priority 5: Block Session Creation When Agent ID Unresolved

**File**: `C:\CherryStudio\qq-mcp-bridge\modules\cherrystudio_module.py`  
**Lines**: 2411-2417

**Fix**: Set a flag that prevents session creation until agent ID is resolved:

```python
else:
    logger.error(f"[BRG-2020] Agent ID 解析最终失败: '{display_name}'...")
    self._agent_id_unresolved = True  # New flag
```

Then in `create_session`:
```python
async def create_session(self, agent_name, agent_id=None, model=None):
    if self._agent_id_unresolved:
        logger.error("Cannot create session: Agent ID not resolved")
        return None
    # ... existing logic ...
```

### Priority 6: Align Default Port Across Codebase

**Fix**: Standardize on port 23333 (CherryStudio's actual default Agent API port):

- `cherrystudio_module.py:2268`: Change default from 8080 to 23333
- `Configuration/config.example.json:9`: Change from 8080 to 23333
- Add explicit `agent_api_url` to the user's config.json

### Priority 7: Extend Handler Timeout or Add Recovery Mode

**Fix**: Increase handler timeout to 600 seconds (matching old system) or implement a "recovery mode" that periodically attempts to re-create the session:

```python
self.timeout = 600  # was 120, match old system's 10-minute idle timeout
```

### Priority 8: Periodic Agent ID Re-Resolution

Currently, agent ID is resolved only at init time. If CherryStudio restarts with different internal IDs, all sessions fail.

**Fix**: Add periodic re-resolution (e.g., every 5 minutes) or re-resolve on session creation failure:

```python
# In _run(), when session creation fails:
if self.parent_module and self.parent_module.http_client.legacy_mode:
    new_id = await self.parent_module.http_client.fetch_agent_id(
        self.parent_module.agent_id  # display name
    )
    if new_id:
        self.agent_id = new_id
        self.parent_module.agent_id = new_id
        # Retry session creation with new ID
```

---

## Appendix A: File Index

| File | Lines | Purpose |
|------|-------|---------|
| `C:\CherryStudio\qq-mcp-bridge\modules\cherrystudio_module.py` | 3176 | Main module: MCPClient, HTTPClient, SessionHandler, CherryStudioModule |
| `C:\CherryStudio\qq-mcp-bridge\server.py` | 1145 | System initialization, config loading, MCP tool registration |
| `C:\CherryStudio\qq-mcp-bridge\modules\sse_parser.py` | 517 | SSE stream parser with stall detection |
| `C:\CherryStudio\qq-mcp-bridge\protocols\error_codes.py` | 161 | ErrorCode enum definitions (BRG-1001 through BRG-9004) |
| `C:\CherryStudio\qq-mcp-bridge\protocols\messages.py` | ~200 | Message protocol definitions |
| `C:\CherryStudio\qq-mcp-bridge\config.json` | 71 | Active configuration file |
| `C:\CherryStudio\qq-mcp-bridge\Configuration\config.example.json` | 40 | Example config (new format) |
| `C:\CherryStudio\Old qq-mcp-bridge\Built_in\auto_reply.py` | ~1500 | Old system's auto-reply module |

## Appendix B: BRG Error Code Reference

| Code | Enum Name | Description | User Text |
|------|-----------|-------------|-----------|
| BRG-4001 | CHERRY_STUDIO_CONNECTION_FAILED | CherryStudio 连接失败 | AI服务连接失败 |
| BRG-4002 | CHERRY_STUDIO_API_ERROR | CherryStudio API 调用失败 | AI服务异常 |
| BRG-4003 | AGENT_NOT_FOUND | 指定的 Agent 不存在 | Agent不存在 |
| BRG-4004 | LLM_PROVIDER_FAILED | LLM Provider 调用失败 | AI处理失败 |
| BRG-4005 | SESSION_CREATE_FAILED | 创建会话失败 | 会话创建失败 |
| BRG-4006 | VISION_PROCESSING_FAILED | 图片识别处理失败 | 图片处理失败 |
| BRG-4007 | FILE_PROCESSING_FAILED | 文件解析处理失败 | 文件处理失败 |
| BRG-4008 | MCP_RESPONSE_TIMEOUT | MCP 响应超时 | 响应超时 |
| BRG-4009 | CHERRY_SESSION_EXPIRED | CherryStudio 会话过期 | 会话已过期 |

## Appendix C: Quick Diagnostic Commands

```bash
# 1. Check if CherryStudio is running
curl -s http://127.0.0.1:23333/health

# 2. List available agents
curl -s http://127.0.0.1:23333/v1/agents -H "Authorization: Bearer cs-sk-b9f3be95-56fd-46d8-93a1-77027c7b10d0"

# 3. Check MCP server registration
curl -s http://127.0.0.1:23333/v1/mcps -H "Authorization: Bearer cs-sk-b9f3be95-56fd-46d8-93a1-77027c7b10d0"

# 4. List available models
curl -s http://127.0.0.1:23333/v1/models -H "Authorization: Bearer cs-sk-b9f3be95-56fd-46d8-93a1-77027c7b10d0"

# 5. Check bridge logs for BRG-4001 context
# Look for "SSE 请求失败" lines in:
# C:\CherryStudio\qq-mcp-bridge\PlayerLog\bridge.log
```

---

*End of Audit Report*

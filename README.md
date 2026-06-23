# Agent Relationship v0.2.0

[English](#english) · [中文](#中文)

---

## English

> **The relationship-awareness layer for multi-agent systems** — lightweight, standalone, zero required dependencies

```bash
pip install git+https://github.com/blueken31/agent-relationship.git
```

> …or `pip install agent-relationship` once published to PyPI.

### The Problem

Every multi-agent system you build today — LangGraph, CrewAI, AutoGen — shares a blind spot:

**All frameworks care about what agents do. None cares about what happens between them.**

```
What you can see                     What you can't
──────────────────                   ──────────────────
Agent A → Agent B: task done         Is the A↔B relationship deteriorating?
carol completed 12 delegations        Is carol helping or free-riding?
System throughput is rising           alice↔carol balance has dropped to 0.18
                                      Three agents are forming a Moloch loop
```

### Five Pain Points

| Without agent-relationship | Consequence |
|---|---|
| **Free-riding undetected** — carol over-extracts resources until the system collapses | 3 hours debugging reveals broken relationship, but logs only show "occasional timeout" |
| **Escalating competition invisible** — 3 agents enter a toxic competitive loop, throughput drops without explanation | Downgrade, retry, add resources — treating symptoms, not the cause |
| **Broken relationships have no repair path** — once trust falls below threshold, cooperation is permanently blocked | The core value of multi-agent systems is silently canceled |
| **Interactions are unobservable** — logs show "request timeout" with zero relationship-level diagnostics | Ops teams fly blind |
| **Dev/prod behavior divergence** — everything works in mock, then real LLM semantic judgments produce completely different outcomes | Deploy = break |

### The Solution

```python
from agent_relationship import RelationshipTracker

tracker = RelationshipTracker(llm="deepseek")

# One line to record an interaction
result = tracker.track("alice", "carol", {
    "action": "resource_exchange", "result": "success",
    "narrative": "carol over-extracted resources"
})

# Risk awareness built into the return value — no separate health() call needed
if result.risk_transition == "worsened":
    print(f"⚠️ Relationship deteriorating: {result.prev_balance:.2f} → {result.balance:.2f}")

# Pre-cooperation check — integrate into agent decision loop
check = tracker.can_cooperate("alice", "carol")
if not check["can"] and check["needs_repair"]:
    repair = tracker.can_cooperate("alice", "carol", try_repair=True)

# Real-time risk change callback
def on_alert(a, b, old_risk, new_risk, balance):
    print(f"🚨 {a}↔{b}: {old_risk} → {new_risk}")

tracker = RelationshipTracker(on_risk_change=on_alert)

# One-line health summary
print(tracker.summary())
```

**Agent frameworks teach agents how to act. Agent Relationship tells them what's happening between each other.**

### Not Another Agent Framework

| | Agent Relationship | LangGraph / CrewAI / AutoGen |
|---|---|---|
| Task orchestration | ❌ | ✅ |
| Tool calling | ❌ | ✅ |
| Memory management | ❌ | ✅ |
| Bilateral relationship modeling (balance + trust + type) | ✅ | ❌ |
| Moloch competition escalation detection | ✅ | ❌ |
| Relationship repair paths (3 paths + cooldown) | ✅ | ❌ |
| Risk transition awareness (low→medium→high→critical) | ✅ | ❌ |
| Real-time risk change callbacks | ✅ | ❌ |
| OpenAI / DeepSeek / Anthropic / Mock | ✅ | Each bound to one |

**Complementary, not competitive.** Three lines of code to integrate with any framework.

### Quick Start

```python
from agent_relationship import RelationshipTracker

# Zero config
tracker = RelationshipTracker()

# Record interactions
tracker.track("alice", "bob", {
    "action": "help",
    "result": "success",
    "narrative": "alice helped bob complete data analysis"
})

# Query health
h = tracker.health("alice", "bob")
print(f"balance={h.balance:.2f}, risk={h.risk}")
```

### 8 Core Methods

| Method | Purpose |
|--------|---------|
| `track(a, b, ctx)` | Record an interaction; returns balance + risk + risk_transition |
| `health(a, b)` | Query bilateral relationship health |
| `can_cooperate(a, b)` | Integrate into agent decision loop; returns repair paths when needed |
| `history(a, b)` | Query interaction history (reverse chronological) |
| `network(ids)` | Full relationship matrix + ASCII heatmap |
| `detect_moloch()` | Competition escalation detection with typed MolochZone results |
| `repair_paths(a, b)` | Three repair paths (adaptive filtering by balance) |
| `summary()` | One-line Markdown health summary |

### Four Engine Support

```python
tracker = RelationshipTracker()                        # Mock — zero config
tracker = RelationshipTracker(llm="openai")            # OpenAI GPT / Codex
tracker = RelationshipTracker(llm="deepseek")          # DeepSeek V4 Flash
tracker = RelationshipTracker(llm="anthropic")         # Anthropic Claude
```

| Engine | Env Variable | Default Model |
|--------|-------------|---------------|
| `mock` | none | — |
| `openai` | `OPENAI_API_KEY` | `gpt-4o` |
| `deepseek` | `DEEPSEEK_API_KEY` | `deepseek-v4-flash` |
| `anthropic` | `ANTHROPIC_API_KEY` | `claude-sonnet-4-20250514` |

### Framework Integration

#### LangGraph — with decision loop

```python
from agent_relationship import RelationshipTracker

tracker = RelationshipTracker(llm="deepseek")

def agent_node(state):
    agent, target = state["agent"], state["target"]

    check = tracker.can_cooperate(agent, target)
    if not check["can"]:
        if check["needs_repair"]:
            check = tracker.can_cooperate(agent, target, try_repair=True)
        if not check["can"]:
            return {"status": "skipped", "reason": check["reason"]}

    result = execute_task(state)
    tracker.track(agent, target, {
        "action": state["action"],
        "result": result["status"],
        "narrative": result["summary"],
    })
    return result
```

#### CrewAI

```python
tracker = RelationshipTracker(llm="anthropic")

class MyAgent(Agent):
    def execute_task(self, task, context=None):
        result = super().execute_task(task, context)
        for other in context.get("collaborators", []):
            tracker.track(self.role, other.role, {
                "action": task.description,
                "result": "success" if result else "failed",
                "narrative": str(result)[:500],
            })
        return result
```

### Specs

- **Zero required dependencies**: core engine + MockLLM are fully self-contained, HTTP via `urllib`
- **Python ≥ 3.10**
- **Tests**: 57 unit + regression tests covering all 8 public APIs + 4 LLM backends
- **Source:test ratio**: 1,651 : 684 ≈ 2.4 : 1
- **Optional dependencies**: `pip install agent-relationship[openai]` / `[anthropic]` / `[all]`

### License

MIT

---

## 中文

> **多 Agent 系统的关系感知层** — 轻量、独立、零必选依赖

```bash
pip install git+https://github.com/blueken31/agent-relationship.git
```

> 发布到 PyPI 后可直接 `pip install agent-relationship`。

### 它解决什么问题

你今天构建的任何多 Agent 系统 — LangGraph、CrewAI、AutoGen — 都有一个盲区：

**所有框架只关心 Agent 在做什么，不关心 Agent 之间发生了什么。**

```
你的 Agent 系统看得到的             你的 Agent 系统看不到的
─────────────────────────         ─────────────────────────
Agent A → Agent B: 任务完成        A 和 B 的关系在恶化吗？
carol 完成了 12 个委托              carol 在帮助还是在搭便车？
系统吞吐量在上升                    alice↔carol 之间的 balance 只剩 0.18
                                   三个 Agent 正在形成 Moloch 竞争闭环
```

### 五个具体痛点

| 没有 agent-relationship | 带来的后果 |
|---|---|
| **搭便车不被发现** — carol 持续超额提取资源，系统到崩溃才暴露 | 排查 3 小时发现是关系破裂，但日志里只有「偶尔超时」 |
| **竞争升级不可见** — 3 个 Agent 形成恶性竞争闭环，吞吐量下降但没人知道原因 | 降级、重试、加资源——治标不治本 |
| **关系破裂无修复路径** — 一旦 trust 跌破阈值，永久无法合作，系统变「单 Agent」 | 多 Agent 系统的核心优势被自动取消 |
| **交互不可观测** — 日志只有「请求超时」，没有关系视角的诊断信息 | 运维靠猜 |
| **开发与生产行为不一致** — Mock 环境一切正常，生产 LLM 的语义判断产生完全不同的结果 | 上线即翻车 |

### 有了它

```python
from agent_relationship import RelationshipTracker

tracker = RelationshipTracker(llm="deepseek")

# 一行记录交互
result = tracker.track("alice", "carol", {
    "action": "resource_exchange", "result": "success", "narrative": "carol 超额提取"
})

# 自带风险感知 — 无需再单独查询
if result.risk_transition == "worsened":
    print(f"⚠️ 关系恶化: {result.prev_balance:.2f} → {result.balance:.2f}")

# 协作前检查 — 集成到 Agent 决策循环
check = tracker.can_cooperate("alice", "carol")
if not check["can"] and check["needs_repair"]:
    repair = tracker.can_cooperate("alice", "carol", try_repair=True)

# 风险变化自动通知
def on_alert(a, b, old_risk, new_risk, balance):
    print(f"🚨 {a}↔{b}: {old_risk} → {new_risk}")

tracker = RelationshipTracker(on_risk_change=on_alert)

# 一键健康摘要
print(tracker.summary())
```

**Agent 框架教 Agent 怎么做事。Agent Relationship 告诉它们彼此之间正在发生什么。**

### 不是又一个 Agent 框架

| | Agent Relationship | LangGraph / CrewAI / AutoGen |
|---|---|---|
| 任务编排 | ❌ 不做 | ✅ |
| 工具调用 | ❌ 不做 | ✅ |
| 记忆管理 | ❌ 不做 | ✅ |
| 双边关系建模 (balance + trust + type) | ✅ | ❌ |
| Moloch 竞争升级检测 | ✅ | ❌ |
| 关系修复路径 (3 条 + 冷却) | ✅ | ❌ |
| 风险过渡感知 (low→medium→high→critical) | ✅ | ❌ |
| 风险变化实时回调 | ✅ | ❌ |
| OpenAI / DeepSeek / Anthropic / Mock | ✅ | 各自绑定 |

**互补，不竞争。** 三行代码接入任何框架。

### 快速开始

```python
from agent_relationship import RelationshipTracker

# 零配置起步
tracker = RelationshipTracker()

# 记录交互
tracker.track("alice", "bob", {
    "action": "help",
    "result": "success",
    "narrative": "alice 帮助 bob 完成了数据分析任务"
})

# 查询健康度
h = tracker.health("alice", "bob")
print(f"balance={h.balance:.2f}, risk={h.risk}")
```

### 8 个核心方法

| 方法 | 作用 |
|------|------|
| `track(a, b, ctx)` | 记录交互，返回 balance + risk + risk_transition |
| `health(a, b)` | 查询双边关系健康度 |
| `can_cooperate(a, b)` | 集成到 Agent 决策循环，关系不足时返回修复路径 |
| `history(a, b)` | 查询交互历史 (按时间倒序) |
| `network(ids)` | 全网关系矩阵 + ASCII 热力图 |
| `detect_moloch()` | 竞争升级检测，返回类型化 MolochZone 列表 |
| `repair_paths(a, b)` | 三条关系修复路径 (根据 balance 自适应过滤) |
| `summary()` | 一键 Markdown 健康摘要 |

### 四引擎支持

```python
tracker = RelationshipTracker()                        # Mock — 零配置
tracker = RelationshipTracker(llm="openai")            # OpenAI GPT / Codex
tracker = RelationshipTracker(llm="deepseek")          # DeepSeek V4 Flash
tracker = RelationshipTracker(llm="anthropic")         # Anthropic Claude
```

| 后端 | 环境变量 | 默认模型 |
|------|---------|---------|
| `mock` | 无 | — |
| `openai` | `OPENAI_API_KEY` | `gpt-4o` |
| `deepseek` | `DEEPSEEK_API_KEY` | `deepseek-v4-flash` |
| `anthropic` | `ANTHROPIC_API_KEY` | `claude-sonnet-4-20250514` |

### 与现有框架集成

#### LangGraph — 带决策循环

```python
from agent_relationship import RelationshipTracker

tracker = RelationshipTracker(llm="deepseek")

def agent_node(state):
    agent, target = state["agent"], state["target"]

    check = tracker.can_cooperate(agent, target)
    if not check["can"]:
        if check["needs_repair"]:
            check = tracker.can_cooperate(agent, target, try_repair=True)
        if not check["can"]:
            return {"status": "skipped", "reason": check["reason"]}

    result = execute_task(state)
    tracker.track(agent, target, {
        "action": state["action"],
        "result": result["status"],
        "narrative": result["summary"],
    })
    return result
```

#### CrewAI

```python
tracker = RelationshipTracker(llm="anthropic")

class MyAgent(Agent):
    def execute_task(self, task, context=None):
        result = super().execute_task(task, context)
        for other in context.get("collaborators", []):
            tracker.track(self.role, other.role, {
                "action": task.description,
                "result": "success" if result else "failed",
                "narrative": str(result)[:500],
            })
        return result
```

### 规范

- **零必选依赖**: 核心引擎 + MockLLM 完全自包含，`urllib` 直调 HTTP
- **Python ≥ 3.10**
- **测试**: 57 个单元测试 + 回归测试，覆盖全部 8 个公开 API + 4 个 LLM 后端
- **源码:测试比**: 1,651 : 684 ≈ 2.4 : 1
- **可选依赖**: `pip install agent-relationship[openai]` / `[anthropic]` / `[all]`

### 许可证

MIT

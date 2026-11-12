# Agent Relationship 0.3.0

[English](#english) · [中文](#中文)

## English

A lightweight Python SDK for **task-linked collaboration diagnostics and verified recovery bookkeeping**. It records interactions, preserves the evidence behind an assessment, keeps unresolved incidents visible, and helps a host application prepare a bounded recovery action.

The SDK runs in **observation mode**. Its diagnoses and recovery plans do not grant permissions or execute tasks. Your host application remains responsible for authorization, budgets, actual execution and validation of evidence.

Python 3.10+, zero required runtime dependencies. HTTP adapters use the standard library.

### Install and try the complete offline example

For this source checkout:

```bash
python -m pip install -e .
PYTHONPATH=. python examples/task_recovery.py
python -m unittest discover -s agent_relationship/tests -v
```

The example uses explicit simulated fixtures. It demonstrates a failed handoff, an evidence card, a single bounded check, a verified result and retention of the original task ID. It makes no model calls and does not execute an external task. The host's authorization and verification are clearly marked demo functions.

### Record evidence and inspect the original task

```python
from agent_relationship import RelationshipTracker

tracker = RelationshipTracker(llm="mock")  # Simulated, not a semantic evaluator.
result = tracker.track("organizer", "reviewer", {
    "event_id": "handoff-001",
    "task_id": "report-001",
    "action": "handoff",
    "goal": "Produce the report from verified source data",
    "expected_result": "Both agents use the same source version",
    "result": "failed",
    "narrative": "The host observed different source versions.",
    "evidence_refs": ["host-log:handoff-001"],
    "provenance": {"source": "host-runner"},
})

print(result.assessment_status, result.assessment_source, result.reason)
print(tracker.incidents(task_id="report-001", status="open"))
print(tracker.history("organizer", "reviewer", task_id="report-001"))
print(tracker.can_cooperate("organizer", "reviewer", task_id="report-001"))
```

Use stable `event_id` values when retrying. Repeating an identical ID and payload is idempotent **within one tracker instance**; reusing it for different content raises `ValueError`. Omitting the ID creates a new event each time. `task_id` is required for a recovery proposal. For a legacy unscoped issue, `link_incident(incident_id, task_id, reason)` records an explicit host association without rewriting the original event. `event(event_id)` retrieves full retained evidence even after the rolling history window expires. Evidence references and provenance are supplied by the host and retained as context; the SDK does not fetch or independently authenticate them. Do not pass secrets or unnecessary personal data into model-bound context.

### Recovery lifecycle

1. `incidents(task_id=..., status="open")` returns task-linked issues with `id`, `title`, observed `reason`, separate `assessment_reason`, `evidence_ids` and a recommended action.
2. `propose_repair(incident_id, action="verify_handoff")` returns a proposal with `id`, `scope`, `success_criteria`, `max_attempts=1` and `grants_permission=False`.
3. The host checks its own permissions and calls `begin_repair(plan_id, host_authorized=True)` to record that decision. This flag is a host declaration, not an access-control mechanism.
4. The host performs the bounded action, validates the result and records a new event for the same agents and task, with the plan's `action`, `repair_plan_id`, actual `result` and `verification_passed`.
5. `complete_repair(plan_id, outcome="resolved", evidence_event_id=...)` attaches the recorded evidence. Resolution requires a successful host verification and a compatible assessment; unrelated, old, unknown or contradicted evidence cannot close the issue. Goal-violation incidents additionally require the new goal judgment to be `met`.

Supported proposal types are `verify_handoff`, `restricted_retest` and `reassign_task`. They describe bounded work; none is an executor. Failed or partial recovery leaves the issue open and applies a cooldown. New adverse evidence prevents an older plan from resolving an updated issue. Resolving one incident does not erase historical scores or other unresolved issues. `cancel_repair(plan_id)` abandons a stale or unwanted plan without inventing an outcome. Canceling a started attempt retains the cooldown; the host must separately stop any ongoing execution, because cancellation here is bookkeeping only.

See [the executable example](examples/task_recovery.py) for the full sequence.

### Assessments, scores and notifications

- Each event uses one bilateral assessment for the two participants and the shared goal. Status, source, model, schema version, brief reason and safe error category remain visible.
- Missing evidence, invalid model output and provider failure produce `unknown`; they do not become positive fallback estimates. Unknown events do not increase balance or trust.
- A participant's loss cannot be canceled by the other's gain. Reported goal violations remain unfavorable even when both participants benefit.
- `confidence` in tracker reports measures assessed-event quantity using `n / (n + 5)`. It is **not** a calibrated probability or proof of evidence quality. Inspect provenance and assessment source as well. An unknown latest assessment displays zero current confidence and an unknown network cell; it is excluded from the known-score average.
- Serious unresolved incidents are stored separately from recent scores. Subsequent successes cannot close them.
- The first high/critical observation can notify `on_risk_change`. Callback failures are reported as `notification_error="callback_failed"`, without rolling back or duplicating the event.
- `detect_moloch()` retains its legacy name but reports connected regions of low collaboration scores, not proof of competitive escalation or malicious intent. Reading it or `summary()` does not advance the trend.

### Backend configuration

| `llm` | Interface | Configuration |
|---|---|---|
| `mock` | Explicit deterministic scenarios | No credentials; unsupported scenarios are unknown |
| `openai` | Chat Completions-compatible HTTP API | `OPENAI_API_KEY`, optional `OPENAI_MODEL` and `OPENAI_BASE_URL` |
| `deepseek` | Chat Completions-compatible HTTP API | `DEEPSEEK_API_KEY`, optional `DEEPSEEK_MODEL` and `DEEPSEEK_BASE_URL` |
| `anthropic` | Messages HTTP API | `ANTHROPIC_API_KEY`, optional `ANTHROPIC_MODEL` |

An explicit `model=` overrides the corresponding environment variable. Select a model compatible with the adapter; the OpenAI adapter does not implement Responses API or claim support for every model. Real-model calls may incur provider costs. The repository's deterministic tests and HTTP mocks do not establish semantic accuracy on real workloads.

### Migrating from 0.2.x

| Previous behavior | 0.3.0 contract |
|---|---|
| No history could return `can=True` | Returns `can=False`, `decision="unknown"`; the host applies its existing policy |
| `try_repair=True` could permit the original task | Returns a recovery-required recommendation; use a task-bound plan |
| Default cooperation threshold was `0.4` | Default is `0.7`, with at least three assessed events and no open incident |
| `can` was easy to treat as permission | All results include `advisory_only=True` and `mode="observe"` |
| Model errors silently became heuristic scores | Explicit unknown status and safe error category; `TrackResult.impact` may be `None`; unknown network cells and an empty known-score average are `None` |
| Mock inferred meaning from narrative keywords | Mock uses structured action/result or an explicit `scenario` fixture |
| Fixed repair success probabilities | `success_probability=None`; no calibrated probability is claimed |
| Notification failure appeared to fail recording | Event stays recorded; notification error is separate |

Branch on `decision`, not only `can`: `continue`, `review`, `unknown` and `repair_required` are diagnostic recommendations. A false value does not itself cancel a host task; a true value does not authorize one. The legacy `estimate_impact()` convenience method returns neutral `0.0` on unknown; use structured assessments to retain that uncertainty.

### Current boundaries

State is in memory. Restarting a tracker loses scores, incidents, plans and deduplication; there is no durable storage or replay yet. `max_history` limits displayed/scoring history, not the full evidence and deduplication ledgers, whose memory use grows with recorded events. Instance-level locking is not cross-process coordination. Keep untrusted agents from writing host verification fields or authorization decisions.

This release ships SDK data and a console example, not a web interface or framework-specific integration. Task views remain bilateral; full direction-, role-, version- and workspace-specific policy is future work. Live model quality, recovery success rates, token usage and cost have not been benchmarked. See [the roadmap](docs/ROADMAP.md).

## 中文

这是一个轻量 Python SDK，用于**发现任务协作异常、保留判断依据、准备受限恢复方案，并记录验证结果**。使用者能沿着原任务查看发生了什么、推荐处理什么、问题是否真正解除。

SDK 默认只做观察和诊断。它不执行任务，也不授予权限；宿主系统负责授权、预算、实际操作和证据核验。Python 3.10 及以上，运行时零必选依赖。

### 从完整离线示例开始

```bash
python -m pip install -e .
PYTHONPATH=. python examples/task_recovery.py
python -m unittest discover -s agent_relationship/tests -v
```

示例完整演示“交接失败 → 查看依据 → 准备一次受限核验 → 记录验证结果 → 保留原任务等待宿主继续”。所有数据和宿主行为均为模拟，不调用真实模型，不执行外部任务。

### 接入与使用过程

1. **记录事实**：由宿主提供稳定的 `event_id`、原 `task_id`、动作、任务目标、预期结果、实际结果及证据来源。避免让业务使用者手填双方收益。`evidence_refs` 和 `provenance` 会原样保留，SDK 不会主动获取或认证其内容。
2. **查看异常**：`incidents()` 返回任务、观察到的事实、独立的模型判断理由及证据 ID。`history()` 可查看原事件描述与评估来源；`health(..., task_id=...)` 和 `can_cooperate(..., task_id=...)` 提供任务范围内的诊断。
3. **准备方案**：`propose_repair()` 提供“核对交接资料”“限定样本复测”“宿主核验后改派”三类方案，明确范围、成功标准和最多一次执行。
4. **由宿主执行**：宿主先核验权限，再通过 `begin_repair(..., host_authorized=True)` 记录授权。这个参数仅记录宿主声明，不能替代权限系统。SDK 不执行方案。
5. **回收结果**：宿主记录与原任务、双方 Agent、方案及动作一致的新验证事件，再调用 `complete_repair()`。核验通过才可关闭对应异常；失败或部分改善保持待处理状态，历史评分和其他异常仍然保留。过期或不再适用的方案可用 `cancel_repair()` 取消；已开始的尝试取消后仍有冷却期，实际任务的停止由宿主负责。

同一实例内，相同 `event_id` 与内容的重试不会重复记账；相同 ID 携带不同内容会报错。不提供 ID 时，每次调用都产生新事件。恢复方案要求已有 `task_id`。旧事件未关联任务时，可用 `link_incident(incident_id, task_id, reason)` 记录宿主确认的任务关联，原事件保持不变。`event(event_id)` 可读取已超出滚动历史窗口的完整证据。首次严重风险可触发通知，回调失败单独报告，不会让已记录事件重复执行。

### 诊断含义与升级注意事项

- `assessed` 表示完成评估，`unknown` 表示证据不足、输出非法或模型不可用；未知不等于健康，也不自动增加信任。
- `assessment_source` 明确区分 `mock` 和 `model`。Mock 只提供确定性场景，不理解自由叙述；真实模型判断也需要业务验证。
- 任务目标优先于双方收益。一方损失不能被另一方收益抵消；尚未解决的重大事件不能被后续小成功冲淡。
- `confidence` 是已评估事件数量的启发式指标 `n / (n + 5)`，不是正确率或可靠性概率。
- `can_cooperate()` 的 `continue / review / unknown / repair_required` 都是建议。无历史时 `can=False`；默认门槛改为 `0.7`，还需要至少三条已评估事件且无待处理异常。宿主应按 `decision` 和原有规则处理，不能把布尔值直接当作权限。
- `try_repair=True` 不再放行原任务。应改用明确关联异常和任务的恢复方案。
- 模型失败不再使用正向降级评分；未知事件的 `impact` 可以为 `None`。修复成功率不再展示未经校准的固定数字。
- `detect_moloch()` 保留旧接口名，目前只检测低分关系构成的异常区域，不能证明恶性竞争、动机或因果。

模型适配器支持 OpenAI Chat Completions 兼容接口、DeepSeek 兼容接口和 Anthropic Messages。显式 `model=` 优先于环境变量；OpenAI 适配器未实现 Responses API。真实调用可能产生费用，现有离线与 HTTP 模拟测试不代表真实语义质量验收。

### 当前边界

本批提供 SDK 和命令行示例，尚无网页操作界面、自动执行器或框架专用连接器。所有状态均在内存中，重启会丢失评分、异常、方案和去重记录；暂不具备持久化与回放。`max_history` 只限制历史展示和评分窗口，完整证据与去重记录仍会随事件增长。实例锁不能协调多个进程。

宿主必须保护授权和核验字段，不能直接信任 Agent 自报成功。真实模型质量、恢复效果、token 与成本仍待评测。后续顺序见[路线图](docs/ROADMAP.md)。

MIT License.

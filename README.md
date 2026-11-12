# Agent Relationship 0.6.0

[English](#english) · [中文](#中文)

## English

A **collaboration diagnosis and recovery-validation layer for multi-agent systems**, delivered as an embeddable Python SDK. It records interactions, preserves the evidence behind an assessment, keeps unresolved incidents visible, and helps a host application prepare a bounded recovery action.

The SDK runs in **observation mode**. Its diagnoses and recovery plans do not grant permissions or execute tasks. Your host application remains responsible for authorization, budgets, actual execution and validation of evidence.

Python 3.10+, zero required runtime dependencies. HTTP adapters use the standard library. “Lightweight” describes the packaging, not a measured cost or scalability claim.

### Relationship to an existing harness

Keep the host's orchestration, tools, approvals, tracing and recovery mechanisms. This SDK adds reusable task-linked incident and verification semantics; whether those improve a well-configured native harness must be measured. Native prompts and hooks can implement overlapping behavior.

Version 0.6.0 adds an [Agents API event adapter](docs/AGENTS_API_ADAPTER.md) and a [paired evaluation protocol](docs/EVALUATION.md). The adapter targets the documented OpenAI Agents API event surface, not every Codex interface. Its fixtures are synthetic and live integration remains unverified. The evaluation gives the native arm the same goals, evidence, policy and budget as the SDK arm. Offline scorer checks do not establish semantic quality or incremental benefit.

The near-term priority is evidence of better collaboration decisions: legitimate disagreement, shared dependency failures, goal violations, unresolved harms and valid recovery. Role, direction and dependency context are retained for investigation; the current bilateral scoring engine does not infer a cluster's root cause.

The checkout also provides [prepared evaluation sessions](docs/EVALUATION_RUNS.md): review the exact inputs/configuration and budgets before host calls, keep completed receipts after interruption, and reconcile uncertain outcomes before continuing. This tooling reserves work locally; the host must enforce provider limits and supply actual usage. No live comparative result has been measured.

### Install and try the complete offline example

For this source checkout:

```bash
python -m pip install -e .
PYTHONPATH=. python examples/task_recovery.py
PYTHONPATH=. python examples/durable_recovery.py
PYTHONPATH=. python examples/host_task_recovery.py
PYTHONPATH=. python examples/task_review_preview.py --output /tmp/task-review.html
python -m unittest discover -s agent_relationship/tests -v
```

The examples use explicit simulated fixtures. They demonstrate a failed handoff, an evidence card, a single bounded check, a verified result and retention of the original task ID. They make no model calls and do not execute external tasks. Host authorization, verification and the local continuation result are marked as simulations.

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

Use stable `event_id` values when retrying. Repeating an identical ID and payload is idempotent **within one tracker instance, or across restarts and instances sharing the same SQLite file**; reusing it for different content raises `ValueError`. Omitting the ID creates a new event each time. `task_id` is required for a recovery proposal. For a legacy unscoped issue, `link_incident(incident_id, task_id, reason)` records an explicit host association without rewriting the original event. `event(event_id)` retrieves full retained evidence even after the rolling history window expires. Evidence references and provenance are supplied by the host and retained as context; the SDK does not fetch or independently authenticate them. Do not pass secrets or unnecessary personal data into model-bound context.

### Keep task evidence across restarts

Persistence is optional. Without `storage_path`, the SDK retains the existing in-memory behavior. To keep committed state in a local SQLite file:

```python
from agent_relationship import RelationshipTracker

with RelationshipTracker(storage_path="relationship.sqlite3", storage_timeout=5.0) as tracker:
    tracker.track("organizer", "reviewer", {
        "event_id": "handoff-001",
        "task_id": "report-001",
        "action": "handoff",
        "result": "failed",
    })

# Reopen with the same scoring configuration; no model evaluation is replayed.
with RelationshipTracker(storage_path="relationship.sqlite3") as resumed:
    print(resumed.incidents(task_id="report-001", status="open"))
    print(resumed.event("handoff-001"))
```

Use one database file per logical tracker/workspace, with event IDs unique within that file. A committed checkpoint includes scores, original event evidence and assessment metadata, deduplication results, task incidents, recovery plans, cooldowns and report trends. Reopening restores these facts; it does not resume external work or invoke models. The [durable offline example](examples/durable_recovery.py) closes and reopens a tracker between starting and completing a recovery plan, then verifies the resolution after another reopen.

Public reads refresh from the latest committed checkpoint. Writes acquire a SQLite writer transaction, load the current state, apply the operation and save atomically, preventing independent instances on the same machine from overwriting each other's updates. A failed transaction rolls back. `storage_timeout` controls how long an operation waits for a database lock; contention or storage failure is reported rather than silently dropping a write.

Snapshot schema, scoring version and the `max_history`, `moloch_threshold` and `moloch_min_zone_size` configuration are checked on open. Unsupported versions, corrupt snapshots and configuration mismatches raise errors without resetting the database. The package exports `StorageError`, `StorageVersionError` and `StateError` so hosts can handle these failures explicitly. A different model backend can be used for new events; historical assessments keep their recorded source and model. This is checkpoint restoration, not re-evaluation or a historical replay engine.

Use the context manager or call `close()` to release the connection. Durable mode supports the public `RelationshipTracker` methods; direct access through `tracker.engine` is rejected to prevent mutations that bypass persistence. A model backend cannot recursively mutate the same durable tracker during assessment; this prevents nested events from notifying before the outer transaction commits.

### Recovery lifecycle

1. `incidents(task_id=..., status="open")` returns task-linked issues with `id`, `title`, observed `reason`, separate `assessment_reason`, `evidence_ids` and a recommended action.
2. `propose_repair(incident_id, action="verify_handoff")` returns a proposal with `id`, `scope`, `success_criteria`, `max_attempts=1` and `grants_permission=False`.
3. The host checks its own permissions and calls `begin_repair(plan_id, host_authorized=True)` to record that decision. This flag is a host declaration, not an access-control mechanism.
4. The host performs the bounded action, validates the result and records a new event for the same agents and task, with the plan's `action`, `repair_plan_id`, actual `result` and `verification_passed`.
5. `complete_repair(plan_id, outcome="resolved", evidence_event_id=...)` attaches the recorded evidence. Resolution requires a successful host verification and a compatible assessment; unrelated, old, unknown or contradicted evidence cannot close the issue. Goal-violation incidents additionally require the new goal judgment to be `met`.

Supported proposal types are `verify_handoff`, `restricted_retest` and `reassign_task`. They describe bounded work; none is an executor. Failed or partial recovery leaves the issue open and applies a cooldown. New adverse evidence prevents an older plan from resolving an updated issue. Resolving one incident does not erase historical scores or other unresolved issues. `cancel_repair(plan_id)` abandons a stale or unwanted plan without inventing an outcome. Canceling a started attempt retains the cooldown; the host must separately stop any ongoing execution, because cancellation here is bookkeeping only.

See [the executable example](examples/task_recovery.py) for the full sequence.

### Bring the diagnosis back to the original task

The read-only task APIs provide a compact user-facing view without creating a recovery proposal or making a model call:

```python
view = tracker.task_view("report-001")
print(view["recovery_status"], view["primary_action"])
print(tracker.task_queue())  # Pending tasks, plus separately listed unlinked incidents.
print(tracker.repair_plans(task_id="report-001"))
# tracker.repair_plan(plan_id) reads one existing plan, or returns None.
```

A task view gathers incidents across all Agent pairs for that task. Each card separates the host's observed result from the assessment, shows current handling and one recommended next action, and retains evidence IDs for `event()` detail reads. Opening a card does not authorize, begin or retry its plan. Reads return detached snapshots and do not advance the durable checkpoint revision.

| View or plan state | Meaning for the user |
|---|---|
| `needs_attention` | An issue needs review, evidence or a new handling decision |
| `waiting` | An existing attempt is waiting for a host result or a cooldown; a proposed plan needing authorization is `needs_attention` |
| `resolved` | Recorded task incidents have been verified as resolved; the host still owns continuation and completion |
| `observed` | Task observations exist with no recorded incidents |
| `no_evidence` | No observations for this task have been recorded |
| Plan `started` | An attempt was recorded; query the host's execution result before deciding what to do next |

Failed, partial and cancelled attempts retain their outcome and any cooldown in the card. Completed checks retain their result evidence separately from the original observation. Missing evidence remains in the attention queue; tied historical plan timestamps require history review instead of guessing an order. A started plan with new adverse evidence requires another review. Resolving one incident leaves the task in the queue while another is still open. Unlinked incidents remain visible until the host explicitly associates them with an original task.

The [host continuation example](examples/host_task_recovery.py) uses a separate local host database. For a new continuation, it verifies the latest task view, checks explicit host authorization and the current task step/revision, then commits a simulated local result and its receipt together. Deduplication identifies the **original host task, blocked step and revision**, so different recovery plans cannot execute the same step twice. After restart, a committed receipt returns the original result; later incidents do not hide that receipt. A failure before commit leaves no partial local result. An explicitly unknown external outcome remains blocked for reconciliation, even if the same task is registered again.

The SDK check and host commit are in separate databases; this example does not make them atomic. A real host must apply its own concurrency and authorization policy. For external effects, use the provider's stable operation ID and result lookup: an ambiguous outcome requires reconciliation before a retry. The local receipt example does not establish exactly-once external execution.

Open the [standalone read-only card preview](docs/task-review-preview.html) in a browser, or regenerate it with [the example script](examples/task_review_preview.py). It uses simulated SDK observations, responsive layout, expandable evidence and next-action guidance; it contains no execution controls or network dependencies. CI checks that the saved preview matches the generated output.

### Assessments, scores and notifications

- Each event uses one bilateral assessment for the two participants and the shared goal. Status, source, model, schema version, brief reason and safe error category remain visible.
- Missing evidence, invalid model output and provider failure produce `unknown`; they do not become positive fallback estimates. Unknown events do not increase balance or trust.
- A participant's loss cannot be canceled by the other's gain. Reported goal violations remain unfavorable even when both participants benefit.
- `confidence` in tracker reports measures assessed-event quantity using `n / (n + 5)`. It is **not** a calibrated probability or proof of evidence quality. Inspect provenance and assessment source as well. An unknown latest assessment displays zero current confidence and an unknown network cell; it is excluded from the known-score average.
- Serious unresolved incidents are stored separately from recent scores. Subsequent successes cannot close them.
- The first high/critical observation can notify `on_risk_change`. Callback failures are reported as `notification_error="callback_failed"`, without rolling back or duplicating the event. If saving that callback diagnostic also fails, the returned value is `callback_failed_status_unpersisted`; the event itself remains committed.
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

| Previous behavior | Current contract (introduced in 0.3.0) |
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

The default is still in memory; only `storage_path` enables restart recovery. SQLite stores the current complete checkpoint, not a history of checkpoint versions. `max_history` limits displayed/scoring history, not the full evidence and deduplication ledgers. Both memory and checkpoint size grow with recorded events; export, archival and bounded retention remain future work. Loading validates scoring arithmetic against stored assessments without calling a model, so read cost also grows with retained evidence.

Use a local file on one machine. Writes are serialized, including model evaluation inside the transaction, so a slow model can delay other writers. This implementation is not intended for high-throughput services, network filesystems or distributed coordination. No database encryption is provided; the host must protect the file and its backups. Backend objects, API credentials and callback functions are not serialized, but event context is retained as supplied.

Risk-change callbacks run after a successful commit and remain best-effort. A crash between commit and callback delivery can lose a notification; reopening or retrying the same event does not automatically deliver it again. There is no durable notification outbox yet. The committed incident and evidence remain queryable, so hosts should inspect pending incidents rather than rely solely on callback delivery. Keep untrusted agents from writing host verification fields or authorization decisions.

This release ships the SDK, an initial observation adapter for Agents API events, an offline evaluation harness, a local host continuation example and a read-only HTML preview. A production web client, authentication and live host acceptance remain future work. Relationship scores remain bilateral; task cards gather incidents across the task's Agent pairs. Full direction-, role-, version- and workspace-specific policy is still pending. Live model quality, recovery success rates, token usage and cost have not been benchmarked. See [the roadmap](docs/ROADMAP.md).

## 中文

这是一个**面向多 Agent 系统的协作诊断与恢复验证层，以可嵌入的 Python SDK 交付**。它发现任务协作异常、保留判断依据、准备受限恢复方案，并记录验证结果。使用者能沿着原任务查看发生了什么、推荐处理什么、问题是否真正解除。

SDK 默认只做观察和诊断。它不执行任务，也不授予权限；宿主系统负责授权、预算、实际操作和证据核验。Python 3.10 及以上，运行时零必选依赖。“轻量”描述接入与依赖规模，不代表已经证明低成本或集群扩展能力。

### 与现有 harness 的关系

继续使用宿主已有的编排、工具、审批、追踪与恢复能力。SDK 提供可复用的任务异常与验证规则；原生提示词和 hooks 也能实现部分相同功能，增益需要通过对照评测证明。

0.6.0 提供 [Agents API 事件适配器](docs/AGENTS_API_ADAPTER.md)和[双组评测协议](docs/EVALUATION.md)。适配器面向 OpenAI Agents API 已公开的事件接口，不代表覆盖所有 Codex 接口；当前验证使用依据官方文档构造的合成数据，真实接入待验收。评测让原生组获得同样的目标、证据、规则与总预算，离线校验通过不代表已经优于原生 harness。

近期优先验证合理否决、共同依赖故障、目标违规、未解决损害与有效恢复。角色、方向及依赖关系用于保留调查线索；当前双边评分还不能自动判定整个集群的故障根因。

仓库还提供[可核对、可接续的评测批次](docs/EVALUATION_RUNS.md)：运行前查看输入、配置和预算；中断后保留已完成结果，结果不明时先核对再继续。工具按声明上限预留本地任务预算，实际模型调用限制与用量记录仍由宿主落实，真实对照结果尚待测量。

### 从完整离线示例开始

```bash
python -m pip install -e .
PYTHONPATH=. python examples/task_recovery.py
PYTHONPATH=. python examples/durable_recovery.py
PYTHONPATH=. python examples/host_task_recovery.py
PYTHONPATH=. python examples/task_review_preview.py --output /tmp/task-review.html
python -m unittest discover -s agent_relationship/tests -v
```

示例完整演示“交接失败 → 查看依据 → 准备一次受限核验 → 记录验证结果 → 保留原任务等待宿主继续”。所有数据和宿主行为均为模拟，不调用真实模型，不执行外部任务。

### 接入与使用过程

1. **记录事实**：由宿主提供稳定的 `event_id`、原 `task_id`、动作、任务目标、预期结果、实际结果及证据来源。避免让业务使用者手填双方收益。`evidence_refs` 和 `provenance` 会原样保留，SDK 不会主动获取或认证其内容。
2. **查看异常**：`incidents()` 返回任务、观察到的事实、独立的模型判断理由及证据 ID。`history()` 可查看原事件描述与评估来源；`health(..., task_id=...)` 和 `can_cooperate(..., task_id=...)` 提供任务范围内的诊断。
3. **准备方案**：`propose_repair()` 提供“核对交接资料”“限定样本复测”“宿主核验后改派”三类方案，明确范围、成功标准和最多一次执行。
4. **由宿主执行**：宿主先核验权限，再通过 `begin_repair(..., host_authorized=True)` 记录授权。这个参数仅记录宿主声明，不能替代权限系统。SDK 不执行方案。
5. **回收结果**：宿主记录与原任务、双方 Agent、方案及动作一致的新验证事件，再调用 `complete_repair()`。核验通过才可关闭对应异常；失败或部分改善保持待处理状态，历史评分和其他异常仍然保留。过期或不再适用的方案可用 `cancel_repair()` 取消；已开始的尝试取消后仍有冷却期，实际任务的停止由宿主负责。

同一实例内，或使用同一个 SQLite 文件的实例与重启之间，相同 `event_id` 与内容的重试不会重复记账；相同 ID 携带不同内容会报错。不提供 ID 时，每次调用都产生新事件。恢复方案要求已有 `task_id`。旧事件未关联任务时，可用 `link_incident(incident_id, task_id, reason)` 记录宿主确认的任务关联，原事件保持不变。`event(event_id)` 可读取已超出滚动历史窗口的完整证据。首次严重风险可触发通知，回调失败单独报告，不会让已记录事件重复执行。

### 重启后接着查看原任务

0.4.0 新增可选的 `storage_path` 和 `storage_timeout`。不传存储路径时仍为内存模式；传入本机 SQLite 文件后，已提交的评分、原始事件、评估来源、去重结果、异常、恢复方案、冷却状态和报告趋势会保留。

```python
from agent_relationship import RelationshipTracker

with RelationshipTracker(storage_path="relationship.sqlite3") as tracker:
    pending = tracker.incidents(task_id="report-001", status="open")
```

每个逻辑工作空间使用独立数据库文件，事件 ID 在文件范围内唯一。每次公开查询会刷新到最新已提交状态；写操作在同一个事务中读取、更新和保存，多个实例使用同一文件时不会用旧状态覆盖彼此。存储失败会回滚，锁等待超过 `storage_timeout` 会明确报错。使用 `with` 或 `close()` 关闭连接。

重开数据库时须保持 `max_history`、`moloch_threshold`、`moloch_min_zone_size` 一致。快照格式、评分版本不支持，或数据损坏、配置不一致时，会报错并保留原数据库，不会悄悄新建空状态。可以为新事件更换模型，旧事件的评估依据和来源保持原样；重开不会重新调用模型，也不会自动重新执行宿主任务。

[持久化离线示例](examples/durable_recovery.py) 演示：开始恢复方案 → 关闭实例 → 重开并核对原任务与去重 → 提交对应核验结果 → 再次重开确认已解决。宿主授权和核验均为明确的模拟数据。

持久化模式须使用 `RelationshipTracker` 公开接口；`tracker.engine` 会拒绝访问，避免直接修改底层状态而未写入数据库。模型后端不能在评估期间递归修改同一持久化实例，以免嵌套事件在外层事务提交前发出通知。

### 原任务中的异常卡片与接续过程

0.5.0 增加只读任务视图和待处理队列。使用者先看到“发生了什么、当前如何处理、下一步建议”，展开后再查看依据：

```python
view = tracker.task_view("report-001")
print(view["recovery_status"], view["primary_action"])
print(tracker.task_queue())
print(tracker.repair_plans(task_id="report-001"))
```

`task_view()` 汇总同一任务不同 Agent 对的异常，区分宿主观察与评估判断；`task_queue()` 返回待处理任务摘要，并单列尚未关联任务的异常。`repair_plans()` 和 `repair_plan(plan_id)` 查询已有方案，无须为了显示页面调用会创建方案的 `propose_repair()`。查询不调用模型、不改变快照版本，也不会开始或重试方案；完整证据仍通过 `event()` 获取。

| 状态 | 使用者应如何理解 |
|---|---|
| `needs_attention` | 需要核对依据或决定处理方式 |
| `waiting` | 等待宿主执行结果或冷却结束；待授权的方案属于 `needs_attention` |
| `resolved` | 该任务已记录的异常核验通过，原任务由宿主决定接续与完成 |
| `observed` | 已有任务观察，暂未记录异常 |
| `no_evidence` | 尚无该任务的观察记录 |
| 方案 `started` | 已记录一次尝试，下一步先查询宿主执行结果 |

失败、部分改善和取消仍保留原结果及冷却时间，核验结果依据与原始观察分别展示。依据缺失时任务仍留在待处理队列；历史方案时间相同且无法确认先后时，提示核对历史，不猜测最新方案。开始后出现新的不利依据，会提示重新核对；解决一个异常后若仍有其他未解决异常，任务继续留在队列。页面首屏只给一个下一步建议，具体授权与操作由宿主承接。

[宿主接续示例](examples/host_task_recovery.py) 演示“核验异常 → 检查当前原任务 → 宿主授权 → 接续原步骤 → 重开后读取同一结果”。示例使用独立的本地宿主数据库，将模拟结果与执行回执在同一事务中提交；去重标识绑定**原任务、受阻步骤和宿主版本**，不同恢复方案不会让同一步骤重复执行。提交前失败会回滚，提交后重开可读取原回执，后来新增的异常不会隐藏已记录结果。外部结果被标记为未知后保持待对账状态，重新登记同一任务也不能清除此限制。

SDK 状态检查与宿主提交之间没有跨库原子保证，真实宿主仍须落实并发控制和权限核验。外部操作要用稳定的操作 ID 查询结果；结果不明时先对账，不能因未收到响应就直接重试。本地示例不承诺外部操作恰好执行一次。

在浏览器打开[只读任务卡片预览](docs/task-review-preview.html)，或使用[示例脚本](examples/task_review_preview.py) 重新生成。桌面与手机布局均以问题、处理状态和一个建议动作为主，依据可展开；数据与宿主行为均为模拟，没有执行按钮或网络依赖。CI 会核对保存的预览与脚本输出一致。

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

本批提供 SDK、Agents API 观察适配器、离线评测框架、本地宿主接续示例和只读 HTML 卡片预览；生产网页客户端、认证和真实宿主联调仍待接入。默认内存模式在重启后仍会丢失状态；配置 `storage_path` 才会保存。SQLite 保存当前完整快照，尚无历史快照回放、导出归档或有界保留机制。`max_history` 只限制历史展示和评分窗口，完整证据、去重记录、内存和快照大小仍会随事件增长。加载时会依据已存评估核对评分计算，不会调用模型；读取成本也会随保留的证据增长。

当前面向单机本地文件。写操作串行化，模型评估期间也会占用写事务，因此慢模型可能阻塞其他写入；不面向高吞吐、网络文件系统或分布式部署。存储未自动加密，宿主负责保护数据库和备份。API 凭据、后端对象和回调函数不写入快照，但传入的事件上下文会保留。

风险回调在提交成功后执行，仍是尽力通知。若进程在提交后、通知前退出，通知可能丢失；重开实例或重试同一事件不会自动补发。目前尚无持久通知队列。已提交的异常与证据可以查询，宿主应同时核对待处理异常。

宿主必须保护授权和核验字段，不能直接信任 Agent 自报成功。真实模型质量、恢复效果、token 与成本仍待评测。后续顺序见[路线图](docs/ROADMAP.md)。

MIT License.

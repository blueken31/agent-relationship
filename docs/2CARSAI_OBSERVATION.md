# 2CarsAI 首轮项目观察验收

2026-11-12。已在隔离副本中复现一个真实历史缺陷，并执行原项目的
HTTP/PostgreSQL/Redis 回归。**修复前两项失败，修复后相同两项通过。**
SDK 保留、去重和关联证据正常；未配置评估器时，即使宿主复测通过，
SDK 仍不能关闭问题。这是本轮发现的接入和体验缺口。

后续 0.6.1 已修正[只读展示](HOST_VERIFICATION_VIEWS.md)：在原检查点上分别
呈现已通过的宿主复测和未知协作评估，下一步指向核对评估。本页以下保留
首轮原始观察，评分、问题关闭和当时的证据文件未改写。

本轮范围是已知缺陷的重新执行和 SDK 观察接入。原生流程与 SDK 的
独立 A/B、多 agent 实际行为、判断准确率、节省时间或 token 尚未测量。
业务对象来自原项目测试夹具，未使用真实客户数据。

## 样本与固定版本

- 原项目：[2CarsAI / AutoAgent #238](https://github.com/blueken31/AutoAgent/pull/238)。
- 原生审查已指出：[保存购车任务后需要恢复原会话的客户关联](https://github.com/blueken31/AutoAgent/pull/238#discussion_r4483413130)。该发现先于本 SDK 观察，本轮不将它计为 SDK 发现。
- 修复前：`78f04e2352f6124daa0b83924bbe989d2e42d7ef`。
- 修复后：`91b814fbed164c5781d7cbd58e8d7e3296e13f00`，其直接父提交为修复前版本。
- SDK 运行时：`40676d0fe8f0e7fe6cac32bb8940c77e627cee65`；本批不更改运行时、评分或恢复规则。
- 用例：`tests/test_commercial_memory_boundaries.py::test_resumed_dealer_task_keeps_original_contact_order_and_memos`。

用例的 `saved_cluster=False/True` 分别覆盖保存时没有/已有明确客户会话绑定。
修复前副本仅覆盖同一份新增回归测试，生产代码保留原提交。两侧测试文件
SHA-256 均为 `123a26563980ed1ebe8a58d0dd905067f351885475d8d14a4bbecec6ce38b9f3`。

## 实际结果

| 检查 | 观察结果 | 可得结论 |
|---|---|---|
| 修复前的原 HTTP 回归 | 2 失败、0 错误、0 跳过；合同确认返回成功，但备忘为空 | 原审查指出的缺陷可复现 |
| 修复后的相同回归 | 2 通过、0 错误、0 跳过 | 此样本的确认、退款回执、原客户状态和访问边界恢复 |
| SDK 方案创建后的新复测 | 相同 2 项再次通过，开始时间晚于方案开始时间 | 使用新执行回执，无旧结果冒充新验证 |
| 重复事件与 SQLite 重新打开 | 同一事件未重复计数，任务视图保持一致 | 本轮证据持久化和去重可用 |
| 旧失败和其他任务证据 | 均被拒绝用于关闭原问题 | 任务和恢复动作绑定生效 |
| 新通过回执、评估器未接入 | 两个原问题均未关闭，各新增一条 `evidence_needed` | 宿主验证通过与 SDK 协作评估未知发生状态分离 |

只有 **2 个唯一测试用例**。重复复测不增加样本量。本轮 SQLite 检查包含
关闭后重新打开对象，以及原进程正常退出后在新 Python 进程读取同一状态；
未执行操作系统崩溃恢复实验。

SDK 的 `UnavailableAssessor` 不发出模型请求，返回 `unknown` 和
`assessment_not_configured`。记录中的 `source=model` 表示缺失的评估器类型，
不表示有模型作出了判断。未注入正向分数，也未把测试通过转为模型同意。
SDK provider 调用为 0；本会话的 token 和费用不可取得，保留为未知。

两侧身份使用明确标注的分析角色，不是从运行时采集的 agent ID。
没有调用 Agents API 适配器，也没有虚构 provider 事件或完整多 agent 轨迹。
此次足以验证观察接入，尚不能评价合理否决、共同故障归因或多方目标冲突的识别能力。

## 对使用体验的影响

当前宿主已经知道“复测通过”，SDK 的原问题仍显示 `awaiting_result`，
另外出现“评估依据不足”；任务整体为 `needs_attention`。
若直接把此视图作为开发者的待办，会产生重复核验或额外操作的困惑。

原任务中应首先展示这样的信息：

> **原问题：** 保存购车任务后，订单确认备忘缺失。  
> **执行验证：** 两种保存方式均已复测通过，可查看原回执。  
> **协作评估：** 尚未配置评估器，无法判断 agent 间影响。  
> **下一步：** 保留宿主验证结果，核对评估接入；不因评估缺失自动重复执行测试。

这是后续交互建议，不是本批已上线界面或已改变的自动决策。
优先设计宿主验证结果与协作判断的独立呈现及处理方式。是否允许可信宿主
结果独立解决 `task_failure`，需要单独界定证据来源和适用范围；不能因此
把目标违反、关系损害或未知根因一并自动关闭。

## 环境、回执与复现

运行使用 Python 3.12.14、PostgreSQL 16.2、Redis 6.2.14，以及原项目
固定版本的 FastAPI、SQLAlchemy、pytest。独立的本地测试数据库和 Redis
进程在测试期间启动，结束后关闭。子进程未继承模型或生产凭据，Python
网络守卫拒绝非回环连接。2CarsAI 的原工作区、远程分支和部署未修改。

Bundled PostgreSQL 缺少 `uuid-ossp/pgcrypto`：临时 bootstrap 将
`uuid_generate_v4()` 映射到内置 `gen_random_uuid()`，省略未使用的
`pgcrypto` 扩展声明。原 `init.sql` 未编辑，表、约束、索引和触发器沿用
原脚本，应用原有启动兼容逻辑正常执行。此结果不替代原项目完整 CI 或迁移验收。

准备阶段出现过 Unix socket 不可用、测试进程外 Redis 未保留、
`test` 未列入可信主机的问题。对应结果已在清单标注为环境问题，未算入
业务缺陷复现或 SDK 效果；就绪后两侧使用相同配置。

已保留的可审查记录：

- [来源、环境、排除项及摘要校验值](../evaluation/captures/2carsai-pr238/manifest.json)。
- [修复前回执](../evaluation/captures/2carsai-pr238/before.json)与[新复测回执](../evaluation/captures/2carsai-pr238/fixed.json)。
- [实际 SDK 状态与拒绝原因](../evaluation/captures/2carsai-pr238/observation.json)。
- [新进程读取检查](../evaluation/captures/2carsai-pr238/process-reopen.json)。
- [本次有界观察代码](../evaluation/twocarsai_observation.py)。

只提交脱敏摘要和必要断言，未复制 2CarsAI 源码、原始日志、客户信息或凭据。
JUnit 哈希对应当次本地原始结果；摘要不是独立执行证明，原始日志未随仓库发布。

复现时从有权访问的 2CarsAI 仓库导出上述两个精确提交，将修复后的测试文件
复制到修复前副本，在独立的测试服务中对两侧运行同一命令：

```bash
python -m pytest -q --tb=short --junitxml=result.xml \
  tests/test_commercial_memory_boundaries.py::test_resumed_dealer_task_keeps_original_contact_order_and_memos
```

显式配置 `AUTOAGENT_TEST_DATABASE_URL`（独立的 `*_test` 数据库）、
`AUTOAGENT_TEST_REDIS_URL`（独立 Redis 进程的 1–15 号库）、`APP_ENV=test`、
`TRUSTED_HOSTS=test,localhost,127.0.0.1`，不继承外部凭据。依照保留回执结构
记录版本、测试文件/JUnit 哈希、时间、退出码和逐项结果。调用
`observe(before_receipt, verify_callback, new_sqlite_path)`；回调在方案建立后
执行一次修复版本的有界复测。旧回执、错误版本、不同测试文件、缺项、
跳过和环境错误都不能充当此次修复证明。异常后原记录保留，禁止自动重跑。

仓库 CI 只验证观察代码的安全契约和保留文件的一致性，不会重新执行
2CarsAI 服务，也不把这些检查计为模型准确率：

```bash
python -m unittest agent_relationship.tests.test_project_observation -v
```

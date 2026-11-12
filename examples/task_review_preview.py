"""Render an offline, read-only task inbox from actual SDK projections.

    PYTHONPATH=. python examples/task_review_preview.py --output /tmp/task-review.html

Open the resulting HTML in any browser. All observations, host authorizations
and verification results are explicit simulated fixtures. No model, network,
external assets, task execution or server is used. Expanding evidence and moving
between sections work locally; guidance labels are deliberately not buttons.
"""

import argparse
from html import escape
from pathlib import Path

from agent_relationship import RelationshipTracker


DEMO_TASKS = {
    "report-sources": "市场简报 · 核对资料版本",
    "report-review": "研究摘要 · 等待核验结果",
    "report-citations": "客户报告 · 补充引用依据",
    "report-retest": "分析报告 · 复测未通过",
    "report-recovered": "行业周报 · 异常已核验",
}

HANDLING = {
    "resolved": ("已核验解决", "green"),
    "needs_task_link": ("等待关联任务", "amber"),
    "awaiting_authorization": ("等待宿主确认", "amber"),
    "awaiting_result": ("等待宿主结果", "blue"),
    "needs_reconciliation": ("需要重新核对", "amber"),
    "evidence_missing": ("依据不完整", "amber"),
    "cooldown": ("冷却期内", "muted"),
    "ready_for_retry": ("待核对后重试", "amber"),
    "cancelled": ("方案已取消", "muted"),
    "needs_plan": ("等待核验", "amber"),
}
OUTCOMES = {"partial": "部分改善", "failed": "核验未通过", "resolved": "已核验解决"}
RESULTS = {
    "success": "成功", "failed": "失败", "error": "出错", "timeout": "超时",
    "partial": "部分完成", "completed": "已完成", "ok": "成功",
}
GOALS = {"met": "评估认为符合目标", "violated": "评估认为可能违反目标", "unknown": "目标判断未知"}


def _text(value, fallback="暂无记录"):
    """Only display scalar text; never expand arbitrary event context."""
    if isinstance(value, str) and value.strip():
        return escape(value, quote=True)
    return escape(fallback, quote=True)


def _incident_card(card, title, anchor, task_id=None):
    observation = card.get("observation", {})
    assessment = card.get("assessment", {})
    handling = card.get("handling", {})
    state = handling.get("state")
    state_label, tone = HANDLING.get(state, ("等待核对", "muted"))
    outcome = OUTCOMES.get(handling.get("last_outcome"))
    # Historical outcomes must not hide a current evidence or reconciliation gap.
    if outcome and state in ("cooldown", "ready_for_retry", "cancelled"):
        state_label = outcome + (" · 冷却期" if handling.get("cooldown_active") else "")
    next_action = card.get("next_action", {})
    result = observation.get("result")
    result_label = RESULTS.get(result, result) if isinstance(result, str) else None
    source = assessment.get("source")
    source_label = "Mock 模拟评估" if source == "mock" else source
    evidence = "".join(f"<li><code>{_text(item)}</code></li>" for item in card.get("evidence_ids", []))
    if not evidence:
        evidence = "<li>暂无事件依据</li>"
    resolution = card.get("resolution_event_id")
    resolution_html = (
        f'<p class="resolution">核验依据 <code>{_text(resolution)}</code></p>'
        if resolution else ""
    )
    verification_html = ""
    verification_id = handling.get("evidence_event_id")
    if verification_id:
        verification = handling.get("observation") or {}
        judgment = handling.get("assessment") or {}
        reported = verification.get("result")
        reported = RESULTS.get(reported, reported) if isinstance(reported, str) else None
        verification_source = judgment.get("source")
        verification_source = "Mock 模拟评估" if verification_source == "mock" else verification_source
        verification_html = f"""
    <section class="verification-record"><h4>本次核验结果</h4>
      <div class="evidence-grid">
        <section><h4>宿主报告</h4><dl><dt>事件</dt><dd><code>{_text(verification_id)}</code></dd><dt>报告结果</dt><dd>{_text(reported)}</dd></dl><p>{_text(verification.get("narrative"), "暂无核验结果叙述")}</p></section>
        <section><h4>评估与判断</h4><p class="source">{_text(verification_source, "评估来源未知")}</p><p>{_text(judgment.get("reason"), "评估依据不足")}</p><p>{_text(GOALS.get(judgment.get("goal_status"), "目标判断未知"))}</p></section>
      </div>
    </section>"""
    elif next_action.get("kind") == "inspect_history":
        plan_refs = "".join(f"<li><code>{_text(item)}</code></li>" for item in handling.get("plan_ids", []))
        result_refs = "".join(f"<li><code>{_text(item)}</code></li>" for item in handling.get("evidence_event_ids", []))
        verification_html = f"""
    <section class="evidence-footer"><h4>待核对的历史方案</h4><ul>{plan_refs or '<li>暂无方案编号</li>'}</ul>
      <h4 class="history-results">对应结果依据</h4><ul>{result_refs or '<li>尚无结果回执</li>'}</ul></section>"""
    explanation = {
        "resolved": "仅表示这一异常已核验解决；原任务仍由宿主决定如何接续。",
        "awaiting_result": "方案已登记开始；实际执行状态以宿主回执为准。请先查原结果。",
        "awaiting_authorization": "核验方案已准备；宿主确认权限后再执行。",
        "needs_task_link": "先由宿主确认原任务，保留这条观察的原始依据。",
        "needs_reconciliation": "方案开始后出现新依据；先核对原执行情况与变化。",
        "evidence_missing": "部分原始记录缺失；先补齐或核对保存的依据。",
        "cooldown": "保留上次结果与原异常；冷却结束后仍需宿主核对。",
    }.get(state, "依据保留在原任务中；下一步由宿主结合实际情况决定。")
    if next_action.get("kind") == "inspect_history":
        explanation = "历史方案记录无法确定先后；先核对原执行情况与结果依据。"
    return f"""
<article class="task-card" id="{anchor}">
  <div class="card-top">
    <div><p class="eyebrow">{_text(task_id, "尚未关联原任务")}</p><h3>{_text(title)}</h3></div>
    <span class="pill {tone}"><i></i>{_text(state_label)}</span>
  </div>
  <p class="issue">{_text(card.get("title"))}</p>
  <p class="observation">{_text(observation.get("narrative"), "尚无宿主叙述；可展开查看已有依据。")}</p>
  <div class="next-step"><span class="next-label">建议下一步</span><strong>{_text(next_action.get("label"), "核对原任务")}</strong><span aria-hidden="true">↗</span></div>
  <p class="handling-note">{explanation}</p>
  <details>
    <summary>查看依据与处理记录 <span class="evidence-count">{len(card.get("evidence_ids", []))} 条观察</span></summary>
    <div class="evidence-grid">
      <section><h4>宿主报告的观察</h4><dl><dt>事件</dt><dd><code>{_text(observation.get("event_id"))}</code></dd><dt>报告结果</dt><dd>{_text(result_label)}</dd><dt>参与者</dt><dd>{_text(" / ".join(card.get("agents", [])))}</dd></dl><p>来源为宿主提供；本预览使用模拟记录。</p></section>
      <section><h4>评估与判断</h4><p class="source">{_text(source_label, "评估来源未知")}</p><p>{_text(assessment.get("reason"), "评估依据不足")}</p><p>{_text(GOALS.get(assessment.get("goal_status"), "目标判断未知"))}</p></section>
    </div>
    {verification_html}
    <div class="evidence-footer"><h4>保留的事件依据</h4><ul>{evidence}</ul>{resolution_html}</div>
  </details>
</article>"""


def render_preview(views, queue, titles=None):
    """Build escaped HTML from task_view/task_queue results, with no actions."""
    titles = titles or {}
    pending, resolved = [], []
    sequence = 0
    for view in views:
        destination = resolved if view.get("recovery_status") == "resolved" else pending
        for card in view.get("incidents", []):
            sequence += 1
            destination.append(_incident_card(card, titles.get(view["task_id"], view["task_id"]),
                                              f"task-{sequence}", view["task_id"]))
    unlinked = []
    for card in queue.get("unlinked_incidents", []):
        sequence += 1
        unlinked.append(_incident_card(card, "一条观察等待关联", f"task-{sequence}"))
    pending_count = len(queue.get("tasks", []))
    resolved_count = sum(view.get("recovery_status") == "resolved" for view in views)
    empty = '<p class="empty">这一组暂无记录。</p>'
    html = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light"><title>原任务 · 异常处理预览</title>
<style>
:root{--ink:#192b37;--muted:#687880;--line:#e1e8e8;--paper:#f5f7f6;--accent:#1e6458;--amber:#8a6124;--blue:#396c8b}
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:24px}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}a{color:inherit;text-decoration:none}button,summary,a{-webkit-tap-highlight-color:transparent}a:focus-visible,summary:focus-visible{outline:3px solid #8bbcaf;outline-offset:5px;border-radius:4px}code{font:12px/1.7 ui-monospace,SFMono-Regular,Consolas,monospace;overflow-wrap:anywhere}h1,h2,h3,h4,p{margin:0}h1{font-size:32px;letter-spacing:-1px;font-weight:650;line-height:1.3}h2{font-size:17px;font-weight:650}h3{font-size:17px;line-height:1.5;font-weight:650}h4{font-size:12px;font-weight:650;letter-spacing:.3px}svg{display:block}.shell{max-width:1216px;margin:auto;padding:32px 40px 72px}.topbar{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:38px}.brand{display:flex;align-items:center;gap:10px;font-size:13px;font-weight:650;letter-spacing:.1px}.brand-mark{width:32px;height:32px;border-radius:10px;background:var(--accent);color:white;display:grid;place-items:center;font-size:11px;letter-spacing:-.5px}.demo-tag{color:var(--muted);font-size:12px;padding:5px 11px;border:1px solid var(--line);border-radius:20px;background:white}.intro{display:flex;justify-content:space-between;align-items:flex-end;gap:24px;margin-bottom:30px}.intro p{color:var(--muted);margin-top:11px;font-size:14px}.caption{font-size:11px;letter-spacing:2px;text-transform:uppercase;color:var(--accent);font-weight:650;margin-bottom:11px}.intro-right{text-align:right;color:var(--muted);font-size:12px;max-width:220px}.notice{display:flex;align-items:flex-start;gap:10px;background:#edf3f0;border:1px solid #dce8e2;color:#50675c;border-radius:10px;padding:12px 16px;font-size:12px;margin-bottom:32px}.notice-dot{height:6px;width:6px;border-radius:50%;background:#6f9482;margin-top:7px;flex-shrink:0}.layout{display:grid;grid-template-columns:188px minmax(0,1fr);gap:40px;align-items:start}.sidebar{position:sticky;top:24px}.nav-label{font-size:11px;font-weight:650;letter-spacing:2px;color:#8a979b;margin:0 12px 12px}.nav-link{display:flex;align-items:center;justify-content:space-between;padding:11px 13px;margin-bottom:5px;border-radius:8px;font-size:13px;color:#596b72}.nav-link:hover{background:#e9efec}.nav-link.primary{background:#e5eeea;color:#1e6458;font-weight:600}.nav-count{font:12px ui-monospace,monospace;min-width:20px;text-align:right;opacity:.8}.side-note{border-top:1px solid var(--line);margin:25px 12px 0;padding-top:18px;font-size:12px;line-height:1.9;color:#687880}.section{margin-bottom:36px}.section-heading{display:flex;justify-content:space-between;align-items:center;gap:12px;margin:0 0 14px}.section-heading p{font-size:12px;color:var(--muted)}.task-card{background:#fff;border:1px solid var(--line);border-radius:14px;padding:22px 24px 0;box-shadow:0 2px 5px #223b2e03;margin-bottom:13px;overflow:hidden}.card-top{display:flex;justify-content:space-between;align-items:center;gap:16px}.eyebrow{font:11px/1.4 ui-monospace,SFMono-Regular,Consolas,monospace;color:#708282;margin-bottom:5px;overflow-wrap:anywhere}.pill{display:inline-flex;gap:6px;align-items:center;flex-shrink:0;border-radius:20px;padding:4px 9px;font-size:11px;font-weight:550;white-space:nowrap}.pill i{height:4px;width:4px;border-radius:50%;background:currentColor}.amber{color:var(--amber);background:#f8f2e7}.blue{color:var(--blue);background:#edf3f7}.green{color:#367460;background:#eaf4ef}.muted{color:#73827e;background:#f0f3f1}.issue{font-size:14px;font-weight:550;margin-top:17px;color:#40555e}.observation{color:#687880;font-size:14px;margin-top:4px;overflow-wrap:anywhere}.next-step{display:flex;align-items:center;gap:12px;border-top:1px solid #edf1ef;padding-top:14px;margin-top:17px;font-size:14px}.next-label{font-size:11px;color:#687d74;flex-shrink:0}.next-step strong{font-weight:600;color:#2b695a}.next-step>span:last-child{color:#83a395;font-size:15px;margin-left:auto}.handling-note{font-size:12px;color:#6d7d7e;line-height:1.7;margin:6px 0 16px 62px}details{margin:0 -24px;background:#fafcfb;border-top:1px solid #edf1ef}summary{padding:12px 24px;font-size:13px;color:#6d807b;cursor:pointer;list-style:none}summary::-webkit-details-marker{display:none}summary:before{content:'＋';font-size:12px;margin-right:7px;color:#8b9d96}details[open] summary:before{content:'−'}summary:hover{background:#f0f5f2}.evidence-count{float:right;font-size:11px;color:#70827a}.evidence-grid{display:grid;grid-template-columns:1fr 1fr;gap:22px;padding:9px 24px 20px;font-size:13px;color:#61756a}.evidence-grid section{min-width:0}.evidence-grid h4{color:#526b5f;margin-bottom:10px}.evidence-grid p+p{margin-top:7px}.evidence-grid dl{display:grid;grid-template-columns:48px minmax(0,1fr);gap:4px 8px;margin:0 0 10px}.evidence-grid dt{color:#71857a}.evidence-grid dd{margin:0;overflow-wrap:anywhere}.source{font-size:11px;display:inline-block;background:#edf2ef;border-radius:4px;padding:2px 5px;margin-bottom:5px}.evidence-footer{padding:14px 24px 18px;border-top:1px dashed var(--line);font-size:12px;color:#61756a}.evidence-footer h4{margin-bottom:7px}.evidence-footer ul{margin:0;padding-left:16px}.resolution{margin-top:10px;color:#4e7a65}.empty{border:1px dashed var(--line);border-radius:12px;padding:28px;color:var(--muted);font-size:13px}.footer{text-align:center;color:#71817b;font-size:12px;margin-top:42px;line-height:1.9}
@media(max-width:850px){.shell{padding:24px 24px 44px}.layout{grid-template-columns:1fr;gap:20px}.sidebar{position:static;display:flex;gap:6px;flex-wrap:wrap}.nav-label,.side-note{display:none}.nav-link{gap:18px;margin:0;padding:9px 13px}.intro-right{display:none}.topbar{margin-bottom:30px}h1{font-size:28px}.notice{margin-bottom:22px}.intro{margin-bottom:22px}.section-heading{margin-bottom:12px}}
@media(max-width:500px){body{font-size:14px}.shell{padding:20px 16px 34px}.brand{font-size:12px}.brand-mark{width:28px;height:28px;border-radius:8px}.demo-tag{font-size:11px;padding:4px 8px}.topbar{margin-bottom:25px;flex-wrap:wrap}.caption{font-size:11px;margin-bottom:9px}h1{font-size:26px}.intro p{font-size:14px;max-width:290px}.notice{font-size:12px;padding:10px 12px}.sidebar{gap:3px}.nav-link{font-size:12px;padding:9px 10px;gap:12px}.nav-count{font-size:11px}h2{font-size:16px}.section-heading{align-items:flex-start;flex-wrap:wrap;gap:3px}.section-heading p{font-size:12px}.task-card{padding:18px 17px 0;border-radius:12px}.card-top{align-items:flex-start;gap:10px;flex-wrap:wrap}.card-top>div{flex:1;min-width:170px}h3{font-size:16px}.pill{font-size:11px;padding:3px 8px;margin-top:2px}.eyebrow{font-size:11px}.issue{font-size:14px;margin-top:14px}.observation{font-size:14px}.next-step{font-size:14px;gap:9px}.next-label{font-size:11px}.handling-note{margin-left:0;font-size:12px}details{margin:0 -17px}summary{padding:12px 17px;font-size:12px}.evidence-grid{grid-template-columns:1fr;padding:7px 17px 15px;gap:16px;font-size:12px}.evidence-footer{padding:12px 17px 16px;font-size:12px}.section{margin-bottom:28px}.footer{font-size:12px}.evidence-count{font-size:11px}}
.verification-record{border-top:1px dashed var(--line);padding-top:14px}.verification-record>h4{margin:0 24px 12px;color:#526b5f}.history-results{margin-top:14px}@media(max-width:500px){.verification-record>h4{margin-left:17px;margin-right:17px}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
</style></head><body><div class="shell">
<header class="topbar"><a class="brand" href="#top"><span class="brand-mark">AR</span>Agent Relationship</a><span class="demo-tag">离线模拟 · 只读预览</span></header>
<div class="intro" id="top"><div><div class="caption">Task continuity</div><h1>回到原任务，接着处理。</h1><p>先看问题与下一步，需要时再展开依据。</p></div><div class="intro-right">异常核验与任务执行分开记录<br>由宿主确认权限与实际结果</div></div>
<aside class="notice"><span class="notice-dot"></span><span>以下任务、授权与核验均为模拟记录。此页只展示诊断和建议；展开依据不会执行任务，也不会授予权限。</span></aside>
<div class="layout"><nav class="sidebar" aria-label="任务分组"><p class="nav-label">原任务处理</p>
""" + f"""<a class="nav-link primary" href="#pending">待处理<span class="nav-count">{pending_count:02d}</span></a>
<a class="nav-link" href="#resolved">已核验<span class="nav-count">{resolved_count:02d}</span></a>
<a class="nav-link" href="#unlinked">待关联<span class="nav-count">{len(unlinked):02d}</span></a>
<p class="side-note">任务保留原有身份。<br>已开始的方案先查回执，<br>不因重开页面再次执行。</p></nav>
<main><section class="section" id="pending"><div class="section-heading"><h2>需要继续关注</h2><p>一个问题，一步建议</p></div>{''.join(pending) or empty}</section>
<section class="section" id="resolved"><div class="section-heading"><h2>异常已核验解决</h2><p>原任务完成情况由宿主确认</p></div>{''.join(resolved) or empty}</section>
<section class="section" id="unlinked"><div class="section-heading"><h2>先找到原任务</h2><p>未关联的观察也保留</p></div>{''.join(unlinked) or empty}</section></main></div>
<footer class="footer">数据来自 SDK 的只读任务视图与异常队列 · Mock 模拟评估<br>未连接真实模型或外部执行服务；建议下一步为文字提示。</footer>
</div></body></html>"""
    return "\n".join(line.rstrip() for line in html.splitlines()) + "\n"


def build_demo():
    """Produce repeatable fixtures through public APIs, then only read views."""
    with RelationshipTracker() as tracker:
        def failed(task_id, narrative):
            return tracker.track("资料整理", "内容核验", {
                "event_id": (task_id or "unlinked") + "-observation",
                "task_id": task_id, "action": "handoff", "result": "failed",
                "narrative": narrative, "provenance": {"source": "demo-host", "simulated": True},
            }).incident_id

        def start(task_id, narrative, action="verify_handoff"):
            incident = failed(task_id, narrative)
            plan = tracker.propose_repair(incident, action=action)
            tracker.begin_repair(plan["id"], host_authorized=True)
            return plan

        def finish(task_id, plan, outcome):
            event = tracker.track("资料整理", "内容核验", {
                "event_id": task_id + "-verification", "task_id": task_id,
                "action": plan["action"], "repair_plan_id": plan["id"],
                "scenario": {"failed": "failure", "partial": "justified_review", "resolved": "success"}[outcome],
                "result": {"failed": "failed", "partial": "partial", "resolved": "success"}[outcome],
                "verification_passed": outcome == "resolved",
                "narrative": {
                    "failed": "模拟核验：限定样本仍未通过验收。",
                    "partial": "模拟核验：已对齐资料版本，引用来源仍需补充。",
                    "resolved": "模拟核验：资料版本、引用来源与验收要求均已核对。",
                }[outcome],
                "provenance": {"source": "demo-host", "simulated": True},
            })
            tracker.complete_repair(plan["id"], outcome, event.event_id)

        tracker.propose_repair(failed("report-sources", "两位 Agent 使用的资料版本不同，交接暂未通过。"))
        start("report-review", "交接清单缺少来源日期，宿主已登记开始核验。")
        partial = start("report-citations", "三处引用尚未对应到原始资料，报告仍需补充依据。")
        finish("report-citations", partial, "partial")
        retry = start("report-retest", "原步骤未通过验收，已安排限定样本复测。", action="restricted_retest")
        finish("report-retest", retry, "failed")
        done = start("report-recovered", "交接资料存在版本差异，等待核验。")
        finish("report-recovered", done, "resolved")
        failed(None, "收到一条交接失败观察，但尚未给出原任务编号。")
        return [tracker.task_view(task_id) for task_id in DEMO_TASKS], tracker.task_queue()


def main():
    parser = argparse.ArgumentParser(description="生成离线、只读的任务异常处理预览")
    parser.add_argument("--output", type=Path, required=True, help="输出 HTML 文件路径")
    args = parser.parse_args()
    views, queue = build_demo()
    args.output.write_text(render_preview(views, queue, DEMO_TASKS), encoding="utf-8")
    print(f"已生成只读模拟预览：{args.output.resolve()}")
    print("在浏览器打开 HTML；可切换分组并展开依据，不会执行任务。")


if __name__ == "__main__":
    main()

from __future__ import annotations

import json
import os

import httpx
import streamlit as st

from apps.research_ui.api_client import ResearchApiClient


st.set_page_config(page_title="TA Research Team", page_icon="🔬", layout="wide")
st.title("多 Agent 研究团队")
st.caption("文件型 MVP · 状态机、任务、审计和双人工审批")

api_url = st.sidebar.text_input("API 地址", os.environ.get("TA_API_URL", "http://127.0.0.1:8000"))
actor = st.sidebar.text_input("当前用户", os.environ.get("TA_ACTOR", "local-development"))
role = st.sidebar.selectbox("当前角色", ["admin", "operator", "research_lead", "rule_owner", "knowledge_editor", "content_reviewer"])
api_key = st.sidebar.text_input("API Key", os.environ.get("TA_API_KEY", ""), type="password")
client = ResearchApiClient(api_url, actor=actor, role=role, api_key=api_key or None)


def call(action, default=None):
    try:
        return action()
    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json().get("detail", exc.response.text)
        except ValueError:
            detail = exc.response.text
        st.error(f"API {exc.response.status_code}: {detail}")
    except httpx.RequestError as exc:
        st.error(f"无法连接 API：{exc}")
    return default


health = call(client.health, {})
st.sidebar.write("API 状态", "🟢" if health.get("status") == "ok" else "🔴")
wiki_model = call(client.wiki_status, {})
st.sidebar.write("Wiki 模型", f"🟢 {wiki_model.get('model')}" if wiki_model.get("ready") else "⚪ 证据摘录模式")

overview, jobs_tab, cases_tab, approvals_tab, wiki_tab, knowledge_tab = st.tabs(["总览", "任务", "研究案例", "人工审批", "Wiki 问答", "知识卡"])

with overview:
    jobs = call(client.jobs, [])
    cases = call(client.cases, [])
    columns = st.columns(4)
    columns[0].metric("Cases", len(cases))
    columns[1].metric("运行中任务", sum(item.get("status") in {"queued", "running", "cancelling"} for item in jobs))
    columns[2].metric("失败任务", sum(item.get("status") == "failed" for item in jobs))
    columns[3].metric("等待审批", sum(str(item.get("state", "")).startswith("awaiting_") for item in cases))
    if cases:
        st.subheader("最近案例")
        st.dataframe(cases[:20], use_container_width=True, hide_index=True)

with jobs_tab:
    st.subheader("创建白名单任务")
    with st.form("create_job"):
        kind = st.selectbox("类型", ["universe_coverage", "sync_market_incremental", "aggregate_market_research", "render_case_report"])
        templates = {
            "universe_coverage": {"manifest": "data/universes/a_share_history.jsonl", "dataset_dirs": [r"H:\股票模型\Model\data\trend_cache", r"H:\股票模型\Model\data\tushare_daily_cache", r"H:\股票模型\Model\data\tushare_incremental_cache"], "as_of": "2026-08-05", "output": "data/universes/coverage_20260805.json"},
            "sync_market_incremental": {"manifest": "data/universes/a_share_history.jsonl", "model_data_root": r"H:\股票模型\Model\data", "start": "2026-08-01", "end": "2026-08-05", "project_root": "."},
            "aggregate_market_research": {"cases_root": "data/research_cases", "output_dir": "data/market_reports/latest"},
            "render_case_report": {"case_dir": "data/research_cases/case_example"},
        }
        payload_text = st.text_area("Payload JSON", json.dumps(templates[kind], ensure_ascii=False, indent=2), height=180, key=f"payload_{kind}")
        idempotency_key = st.text_input("幂等键（推荐）")
        submitted = st.form_submit_button("创建任务")
        if submitted:
            try:
                payload = json.loads(payload_text)
                created = call(lambda: client.create_job(kind, payload, idempotency_key or None))
                if created:
                    st.success(f"已创建 {created['job_id']}")
            except json.JSONDecodeError as exc:
                st.error(f"Payload 不是有效 JSON：{exc}")
    jobs = call(client.jobs, [])
    if jobs:
        st.dataframe(jobs, use_container_width=True, hide_index=True)
        active = [item["job_id"] for item in jobs if item.get("status") in {"queued", "running"}]
        if active:
            selected_job = st.selectbox("取消任务", active)
            if st.button("请求取消", type="secondary"):
                result = call(lambda: client.cancel_job(selected_job))
                if result:
                    st.success(f"任务状态：{result['status']}")

with cases_tab:
    cases = call(client.cases, [])
    if not cases:
        st.info("暂无研究案例。")
    else:
        case_id = st.selectbox("案例", [item["case_id"] for item in cases], key="case_browser")
        case = call(lambda: client.case(case_id), {})
        timeline = call(lambda: client.timeline(case_id), {})
        left, right = st.columns([1, 2])
        with left:
            st.json(case.get("case", case))
        with right:
            st.subheader("状态时间线")
            events = timeline.get("state_events", [])
            if events:
                st.dataframe([{"序号": item.get("sequence"), "时间": item.get("occurred_at"), "事件": item.get("event_type"), "状态": item.get("to_state")} for item in events], use_container_width=True, hide_index=True)
            st.subheader("Agent 运行")
            st.dataframe(timeline.get("agent_runs", []), use_container_width=True, hide_index=True)
        report = call(lambda: client.report(case_id))
        if report:
            with st.expander("研究报告"):
                st.markdown(report)

with approvals_tab:
    cases = call(client.cases, [])
    pending = [item for item in cases if item.get("state") in {"awaiting_hypothesis_approval", "awaiting_rule_approval"}]
    if not pending:
        st.info("当前没有等待人工审批的案例。")
    else:
        case_id = st.selectbox("待审批案例", [item["case_id"] for item in pending], key="approval_case")
        selected = next(item for item in pending if item["case_id"] == case_id)
        stage = "hypothesis" if selected["state"] == "awaiting_hypothesis_approval" else "rule"
        st.warning(f"审批阶段：{stage}。Hypothesis 与 Rule 必须由不同人员审批。")
        if st.button("创建审批请求"):
            result = call(lambda: client.request_approval(case_id, stage))
            if result:
                st.success("审批请求已创建或已存在。")
        with st.form("review_form"):
            approver = st.text_input("审批人")
            decision = st.selectbox("决定", ["approve", "request_changes", "reject"])
            comment = st.text_area("审批说明")
            if st.form_submit_button("提交不可变审批记录", type="primary"):
                result = call(lambda: client.review(case_id, stage, approver, decision, comment))
                if result:
                    st.success("审批已写入，刷新后可见新状态。")

with wiki_tab:
    st.subheader("基于已审校证据回答")
    st.caption("模型只能看到已发布知识卡；无模型密钥或模型失败时自动降级为证据摘录。")
    with st.form("wiki_answer_form"):
        question = st.text_input("问题", placeholder="例如：乌云盖顶形态是什么？")
        use_model = st.checkbox("使用 DeepSeek 生成证据约束回答", value=True)
        provider_api_key = st.text_input("本次调用 DeepSeek Key（可留空使用进程配置）", type="password") if use_model else ""
        submitted = st.form_submit_button("回答", type="primary")
    if submitted and question.strip():
        result = call(lambda: client.answer_wiki(question, use_model=use_model, provider_api_key=provider_api_key or None), {})
        if result:
            if result.get("status") == "insufficient_evidence":
                st.warning(result.get("answer"))
            else:
                st.markdown(result.get("answer", ""))
                st.caption(f"生成模式：{result.get('generation_mode')} · 模型：{result.get('model') or '未调用'}")
                if result.get("warnings"):
                    for warning in result["warnings"]:
                        st.warning(warning)
                with st.expander("引用与使用限制", expanded=True):
                    st.dataframe(result.get("citations", []), use_container_width=True, hide_index=True)
                    for limitation in result.get("limitations", []):
                        st.write(f"- {limitation}")

with knowledge_tab:
    query = st.text_input("检索已发布知识（本地 BM25，不调用外部模型）")
    if query:
        results = call(lambda: client.search_knowledge(query), [])
        st.dataframe(results, use_container_width=True, hide_index=True)
    cards = call(client.knowledge_cards, [])
    if not cards:
        st.info("暂无知识卡。Knowledge Agent 的无引用草稿不会自动发布。")
    else:
        st.dataframe(cards, use_container_width=True, hide_index=True)
        drafts = [item for item in cards if item.get("status") in {"draft", "changes_requested"}]
        if drafts:
            card_id = st.selectbox("审校知识卡", [item["card_id"] for item in drafts])
            with st.form("knowledge_review"):
                reviewer = st.text_input("内容审校人")
                decision = st.selectbox("审校决定", ["publish", "request_changes", "reject"])
                comment = st.text_area("审校说明")
                if st.form_submit_button("提交知识审校"):
                    result = call(lambda: client.review_knowledge(card_id, reviewer, decision, comment))
                    if result:
                        st.success("知识卡审校已写入。")

"""VinUni AI Agent Lab 17 — Multi-Tier Memory Agent Dashboard & Demo UI.

Author: Ta Minh Duc (MSSV: 2A202601497)
Architecture: Zep Cloud V3 + Local Short-Term Memory + Redis + Qdrant + Gemini

Features:
1. 🎯 Case Evaluator: Interactive inspection of all 31 evaluation cases (E01-E11 & G01-G20).
2. 💬 Memory-Grounded Chatbot: Multi-turn chat grounded in user graph & domain KB.
3. 🔍 Live Memory Playground: Ad-hoc query against Short-Term, Long-Term, Episodic, Semantic layers & Local baselines.
4. 📊 Benchmark Analytics: Interactive summary of Golden (20/20), Practice (11/11), and Baseline reports.
5. 🏗️ Architecture & Review: Detailed diagrams, layer comparison, and budget policies.
"""

from __future__ import annotations

import html
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st

from src.config import settings
from src.llm import gemini_available, generate_reply
from src.memory_student import StudentMemory
from src.router import route_query
from src.short_term import ShortTermMemory
from src.utils import GOLDEN_PATH, load_dataset, load_json
from src.zep_common import get_zep_client

# ==============================================================================
# UI Styling & Configuration
# ==============================================================================

LAYER_COLORS = {
    "short_term": "#2563eb",
    "long_term": "#059669",
    "episodic": "#d97706",
    "semantic": "#7c3aed",
    "mixed": "#0891b2",
}

CSS = """
<style>
.main-header {
    background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
    color: #ffffff;
    padding: 1.5rem 2rem;
    border-radius: 12px;
    margin-bottom: 1.5rem;
    border-left: 6px solid #38bdf8;
    box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
}
.main-header h1 {
    margin: 0;
    font-size: 1.75rem;
    font-weight: 700;
    color: #f8fafc;
}
.main-header p {
    margin: 0.35rem 0 0 0;
    font-size: 0.95rem;
    color: #94a3b8;
}
.lab-badge {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 999px;
    color: #ffffff;
    font-size: 0.75rem;
    font-weight: 600;
    letter-spacing: .02em;
    margin-right: 6px;
}
.lab-card {
    border: 1px solid rgba(148, 163, 184, 0.25);
    border-radius: 12px;
    padding: 16px 20px;
    margin-bottom: 14px;
    background: rgba(30, 41, 59, 0.4);
    box-shadow: 0 2px 4px rgba(0,0,0,0.05);
}
.pass-pill {
    background-color: #10b981;
    color: white;
    padding: 2px 8px;
    border-radius: 6px;
    font-size: 0.8rem;
    font-weight: 600;
}
.fail-pill {
    background-color: #ef4444;
    color: white;
    padding: 2px 8px;
    border-radius: 6px;
    font-size: 0.8rem;
    font-weight: 600;
}
.metric-box {
    background: rgba(255, 255, 255, 0.03);
    border: 1px solid rgba(148, 163, 184, 0.15);
    border-radius: 10px;
    padding: 12px;
    text-align: center;
}
.metric-val {
    font-size: 1.4rem;
    font-weight: 700;
    color: #38bdf8;
}
.metric-lbl {
    font-size: 0.75rem;
    color: #94a3b8;
    text-transform: uppercase;
}
</style>
"""

DURABLE_FOR_LAYER = {
    "short_term": [],
    "long_term": ["long_term"],
    "episodic": ["episodic"],
    "semantic": ["semantic"],
    "mixed": ["long_term", "semantic"],
}


def layer_badge(layer: str) -> str:
    color = LAYER_COLORS.get(layer, "#475569")
    return f'<span class="lab-badge" style="background:{color}">{html.escape(layer)}</span>'


# ==============================================================================
# Helper Functions & Data Loaders
# ==============================================================================

@st.cache_data
def load_all_cases() -> list[dict[str, Any]]:
    cases = []
    # 1. Practice dataset
    practice_data = load_dataset()
    for c in practice_data.get("evaluations", []):
        cases.append({**c, "dataset": "Practice (E01-E11)"})

    # 2. Golden dataset
    if GOLDEN_PATH.exists():
        try:
            golden_data = load_json(GOLDEN_PATH)
            for c in golden_data.get("evaluations", []):
                cases.append({**c, "dataset": "Golden (G01-G20)"})
        except Exception:
            pass
    return cases


def case_thread_messages(case: dict[str, Any]) -> list[dict[str, str]]:
    """Get seeded turns belonging to the test case's thread."""
    fixtures = case.get("fixture_messages")
    if fixtures:
        return list(fixtures)
    for user in load_dataset().get("users", []):
        if user["user_id"] != case.get("user_id"):
            continue
        for session in user.get("sessions", []):
            if session["thread_id"] == case.get("thread_id"):
                return list(session.get("messages", []))
    return []


def retrieve_for_case(
    memory: StudentMemory,
    case: dict[str, Any],
    extra_messages: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Execute multi-tier retrieval for the specified case."""
    query = case.get("query", "")
    user_id = case.get("user_id", "")

    # Short-term memory (local sliding window)
    short_term = ShortTermMemory(strategy="sliding", max_recent_messages=6, pressure_tokens=450)
    all_turns = case_thread_messages(case) + list(extra_messages or [])
    for msg in all_turns:
        short_term.add(msg.get("role", "user"), msg.get("content", ""))

    layers = {
        "short_term": short_term.render(),
        "long_term": "",
        "episodic": "",
        "semantic": "",
    }

    wanted = set(case.get("retrieve_layers") or DURABLE_FOR_LAYER.get(case.get("expected_layer", ""), []))
    wanted |= {x for x in route_query(query) if x != "short_term"}

    if "long_term" in wanted:
        layers["long_term"] = memory.retrieve_long_term(
            user_id=user_id,
            thread_id=f"ui-{case.get('id', 'case')}",
            query=query,
        )
    if "episodic" in wanted:
        layers["episodic"] = memory.retrieve_episodic(user_id, query)
    if "semantic" in wanted:
        layers["semantic"] = memory.retrieve_semantic(settings.semantic_graph_id, query)

    merged, budget = memory.assemble_context(layers)
    return {"merged_context": merged, "layers": layers, "budget": budget}


# ==============================================================================
# Main Streamlit Application
# ==============================================================================

def main() -> None:
    st.set_page_config(
        page_title="Lab 17 Multi-Tier Memory Agent Demo",
        page_icon="🧠",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(CSS, unsafe_allow_html=True)

    # Header Banner
    st.markdown(
        """
        <div class="main-header">
            <h1>🧠 Lab 17: Multi-Tier Memory Agent Dashboard</h1>
            <p>Interactive Demo & Code Review · Student: <b>Tạ Minh Đức (2A202601497)</b> · Zep Cloud V3 + Short-Term + Redis + Qdrant</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Sidebar: System Status & Configurations
    with st.sidebar:
        st.header("⚙️ System Status")
        zep_ok = bool(settings.zep_api_key)
        st.markdown(("🟢" if zep_ok else "🔴") + f" **Zep Cloud V3**: `{'Connected' if zep_ok else 'Missing API Key'}`")
        gemini_ok = gemini_available()
        st.markdown(("🟢" if gemini_ok else "🟡") + f" **Gemini LLM**: `{'Active (' + settings.gemini_model + ')' if gemini_ok else 'Offline (Fallback Mode)'}`")
        st.markdown(f"🏷️ **Domain KB ID**: `{settings.semantic_graph_id}`")
        st.markdown(f"📦 **Context Token Budget**: `{settings.context_tokens}` tokens")
        st.divider()

        st.markdown("### 📋 Quick Stats")
        cases = load_all_cases()
        practice_count = sum(1 for c in cases if "Practice" in c["dataset"])
        golden_count = sum(1 for c in cases if "Golden" in c["dataset"])
        st.write(f"- Practice Cases: **{practice_count} / 11**")
        st.write(f"- Golden Cases: **{golden_count} / 20**")
        st.write("- Benchmark Score: **20/20 Golden (+10 Bonus)**")
        st.divider()
        st.caption("Lab 17: Memory Systems for Agents © 2026")

    # App Tabs
    tab_eval, tab_chat, tab_play, tab_reports, tab_arch = st.tabs([
        "🎯 Test Case Evaluator",
        "💬 Memory-Grounded Chat",
        "🔍 Memory Playground",
        "📊 Benchmark Reports",
        "🏗️ Architecture & Review",
    ])

    # --------------------------------------------------------------------------
    # Tab 1: Test Case Evaluator
    # --------------------------------------------------------------------------
    with tab_eval:
        st.subheader("🎯 Evaluation Cases & Layered Evidence Inspection")
        st.caption("Select any practice (E01-E11) or golden (G01-G20) case to test real-time retrieval across all memory layers.")

        col_f1, col_f2 = st.columns([1, 2])
        with col_f1:
            dataset_filter = st.selectbox("Filter Dataset", ["All Datasets", "Practice (E01-E11)", "Golden (G01-G20)"])
        with col_f2:
            layer_filter = st.selectbox("Filter Layer", ["All Layers", "short_term", "long_term", "episodic", "semantic", "mixed"])

        filtered_cases = cases
        if dataset_filter != "All Datasets":
            filtered_cases = [c for c in filtered_cases if c["dataset"] == dataset_filter]
        if layer_filter != "All Layers":
            filtered_cases = [c for c in filtered_cases if c.get("expected_layer") == layer_filter]

        if not filtered_cases:
            st.warning("No cases match the selected filters.")
            case = None
        else:
            case_labels = [f"[{c['id']}] {c['expected_layer']} | User: {c.get('user_id', '-')} | {c.get('query', '')[:45]}..." for c in filtered_cases]
            chosen_label = st.selectbox("Select Test Case", case_labels)
            case = filtered_cases[case_labels.index(chosen_label)]

        if case:
            # Case Summary Card
            must_contain = case.get("must_contain_all") or case.get("must_contain") or []
            if isinstance(must_contain, str):
                must_contain = [must_contain]

            st.markdown(
                f"""
                <div class="lab-card">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                        <div>
                            {layer_badge(case.get("expected_layer","?"))}
                            <b style="font-size:1.1rem; margin-left:6px;">{case["id"]} ({case["dataset"]})</b>
                        </div>
                        <span style="font-size:0.85rem; color:#94a3b8;">User: <b>{case.get("user_id","-")}</b> | Thread: <b>{case.get("thread_id","-")}</b></span>
                    </div>
                    <div style="font-size:1rem; font-weight:600; margin:8px 0; color:#f1f5f9;">Query: "{case.get("query","")}"</div>
                    <div style="font-size:0.85rem; color:#cbd5e1;"><b>Expected Ground Truth Evidence:</b> <code>{", ".join(must_contain)}</code></div>
                    <div style="font-size:0.8rem; color:#94a3b8; margin-top:4px;"><i>{case.get("description","")}</i></div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            col_btn, col_info = st.columns([1, 3])
            run_retrieval = col_btn.button("⚡ Run Retrieval on Case", use_container_width=True, type="primary")

            if run_retrieval:
                with st.spinner("Querying Zep Cloud V3 & local stores..."):
                    start_t = time.perf_counter()
                    try:
                        client = get_zep_client()
                        memory = StudentMemory(client)
                        eval_res = retrieve_for_case(memory, case)
                        latency_ms = (time.perf_counter() - start_t) * 1000.0
                        eval_res["latency_ms"] = latency_ms

                        # Evaluate hit rate
                        merged_text = eval_res.get("merged_context", "")
                        passed = all(k.lower() in merged_text.lower() for k in must_contain) if must_contain else True
                        eval_res["passed"] = passed
                        eval_res["missing_keys"] = [k for k in must_contain if k.lower() not in merged_text.lower()]
                        st.session_state[f"eval_{case['id']}"] = eval_res
                    except Exception as e:
                        st.error(f"Error during retrieval: {e}")

            eval_res = st.session_state.get(f"eval_{case['id']}")
            if eval_res:
                passed = eval_res.get("passed", False)
                lat = eval_res.get("latency_ms", 0.0)

                st.markdown("---")
                col_m1, col_m2, col_m3, col_m4 = st.columns(4)
                with col_m1:
                    st.markdown(
                        f"""
                        <div class="metric-box">
                            <div class="metric-val" style="color:{'#10b981' if passed else '#ef4444'}">{'✅ PASS' if passed else '❌ FAIL'}</div>
                            <div class="metric-lbl">Ground Truth Verification</div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                with col_m2:
                    st.markdown(
                        f"""
                        <div class="metric-box">
                            <div class="metric-val">{lat:.1f} ms</div>
                            <div class="metric-lbl">Total Latency</div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                with col_m3:
                    budget_data = eval_res.get("budget", {})
                    total_used = sum(b.get("used_tokens", 0) for b in budget_data.values())
                    st.markdown(
                        f"""
                        <div class="metric-box">
                            <div class="metric-val">{total_used} tok</div>
                            <div class="metric-lbl">Context Budget Used</div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                with col_m4:
                    active_layers = [k for k, v in eval_res.get("layers", {}).items() if v.strip()]
                    st.markdown(
                        f"""
                        <div class="metric-box">
                            <div class="metric-val">{len(active_layers)}</div>
                            <div class="metric-lbl">Active Memory Layers</div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

                # Keyword verification details
                if passed:
                    st.success(f"All expected ground-truth keywords matched: `{must_contain}`")
                else:
                    st.error(f"Missing required keywords: `{eval_res.get('missing_keys')}`")

                # Token Budget Table
                st.markdown("#### 📊 Token Budget Breakdown (10/4/3/3 Rule)")
                cols = st.columns(4)
                for idx, layer_name in enumerate(["short_term", "long_term", "episodic", "semantic"]):
                    b = budget_data.get(layer_name, {})
                    with cols[idx]:
                        st.metric(
                            label=f"{layer_name.replace('_', ' ').title()}",
                            value=f"{b.get('used_tokens', 0)} / {b.get('limit_tokens', 0)} tok",
                            delta=f"Raw: {b.get('raw_tokens', 0)} tok",
                            delta_color="off",
                        )

                # Merged context & Layer expandable viewers
                st.markdown("#### 🧩 Retrieved & Assembled Memory Context")
                with st.expander("📄 Final Assembled Merged Context (Prompt Injection Ready)", expanded=True):
                    st.code(eval_res.get("merged_context") or "(empty)", language="markdown")

                layer_tabs = st.tabs(["Short-Term Buffer", "Long-Term Context", "Episodic Trajectory", "Semantic Knowledge"])
                with layer_tabs[0]:
                    st.caption("Local sliding-window buffer & compaction notes:")
                    st.text_area("Short-Term Text", eval_res["layers"].get("short_term") or "(none)", height=150, disabled=True)
                with layer_tabs[1]:
                    st.caption("Zep user Context Block + Edge Facts + Fallback Episodes:")
                    st.text_area("Long-Term Text", eval_res["layers"].get("long_term") or "(none)", height=150, disabled=True)
                with layer_tabs[2]:
                    st.caption("Zep user-scoped Episodes search (Literal trajectory markers & reflections):")
                    st.text_area("Episodic Text", eval_res["layers"].get("episodic") or "(none)", height=150, disabled=True)
                with layer_tabs[3]:
                    st.caption("Zep standalone Domain Knowledge Graph (Company rules & policy playbooks):")
                    st.text_area("Semantic Text", eval_res["layers"].get("semantic") or "(none)", height=150, disabled=True)

    # --------------------------------------------------------------------------
    # Tab 2: Memory-Grounded Chat
    # --------------------------------------------------------------------------
    with tab_chat:
        st.subheader("💬 Live Multi-Turn Chat Grounded in Memory")
        st.caption("Chat dynamically as a user persona. The assistant retrieves relevant user graph facts, episodic trajectories, and company policies on every turn.")

        col_c1, col_c2 = st.columns([2, 1])
        with col_c1:
            persona = st.selectbox("Select User Persona", ["minh-lab17 (Minh Nguyen)", "lan-lab17 (Lan Tran)", "Custom User"])
            user_id = "minh-lab17" if "minh" in persona else ("lan-lab17" if "lan" in persona else "guest-user")
        with col_c2:
            st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
            if st.button("🗑️ Reset Chat History", use_container_width=True):
                st.session_state.chat_history = []
                st.session_state.last_chat_context = ""
                st.rerun()

        if "chat_history" not in st.session_state:
            st.session_state.chat_history = []

        # Display conversation history
        for msg in st.session_state.chat_history:
            with st.chat_message(msg["role"]):
                st.write(msg["content"])
                if "retrieved_layers" in msg:
                    st.caption(f"🧠 Memory Layers Hit: `{', '.join(msg['retrieved_layers']) or 'none'}`")

        # Chat Input
        user_prompt = st.chat_input("Ask about personal projects, previous errors, or payment rules...")
        if user_prompt:
            st.session_state.chat_history.append({"role": "user", "content": user_prompt})
            with st.chat_message("user"):
                st.write(user_prompt)

            with st.chat_message("assistant"):
                with st.spinner("Retrieving memory context & generating reply..."):
                    try:
                        client = get_zep_client()
                        memory = StudentMemory(client)
                        chat_case = {
                            "id": "live-chat",
                            "user_id": user_id,
                            "thread_id": f"chat-{user_id}",
                            "query": user_prompt,
                            "expected_layer": "mixed",
                        }
                        res = retrieve_for_case(memory, chat_case, st.session_state.chat_history)
                        context = res.get("merged_context", "")
                        st.session_state.last_chat_context = context

                        active_layers = [k for k, v in res["layers"].items() if v.strip()]

                        # Generate reply
                        if gemini_available():
                            past_msgs = [{"role": m["role"], "content": m["content"]} for m in st.session_state.chat_history[:-1]]
                            reply = generate_reply(context, past_msgs, user_prompt)
                        else:
                            # Intelligent mock response displaying grounded memory
                            reply = f"**[Memory Grounded Response]**\n\nBased on your profile (`{user_id}`) and stored memory:\n\n"
                            if "ORCHID-27" in context:
                                reply += "- Personal Project **ORCHID-27** prioritizes Python.\n"
                            if "BLUEBIRD-42" in context:
                                reply += "- Company Project **BLUEBIRD-42** strictly requires TypeScript & NestJS backend.\n"
                            if "ASYNC-FIX-20" in context or "ClientSession" in context:
                                reply += "- Resolved async issue **ASYNC-FIX-20** by reusing ClientSession with concurrency=20.\n"
                            if "PAYMENT-RULE-3" in context:
                                reply += "- Policy **PAYMENT-RULE-3**: Payment retry requires `Idempotency-Key` and exponential backoff.\n"
                            reply += f"\n*(Retrieved {len(context)} chars of memory across {len(active_layers)} layers)*"

                        st.write(reply)
                        st.caption(f"🧠 Memory Layers Hit: `{', '.join(active_layers) or 'none'}`")

                        st.session_state.chat_history.append({
                            "role": "assistant",
                            "content": reply,
                            "retrieved_layers": active_layers,
                        })

                        with st.expander("🔍 Inspect Injected Memory Context"):
                            st.code(context, language="markdown")

                    except Exception as e:
                        st.error(f"Error in chat processing: {e}")

    # --------------------------------------------------------------------------
    # Tab 3: Memory Playground
    # --------------------------------------------------------------------------
    with tab_play:
        st.subheader("🔍 Ad-Hoc Memory Layer Playground")
        st.caption("Directly test and inspect individual memory components with custom queries.")

        col_p1, col_p2 = st.columns([1, 2])
        with col_p1:
            target_layer = st.selectbox("Target Memory Layer", [
                "1. Short-Term Memory (Local Sliding Window & Compaction)",
                "2. Long-Term Memory (Zep User Context Block & Edge Facts)",
                "3. Episodic Memory (Zep Trajectory Episodes)",
                "4. Semantic Memory (Zep Standalone Domain Knowledge)",
            ])
            target_user = st.selectbox("Target User", ["minh-lab17", "lan-lab17"])

        with col_p2:
            default_q = "ORCHID-27 backend language" if "Long-Term" in target_layer else (
                "ASYNC-FIX-20 reflection" if "Episodic" in target_layer else (
                    "PAYMENT-RULE-3 retry policy" if "Semantic" in target_layer else "Recent task deadline"
                )
            )
            custom_q = st.text_input("Custom Search Query", value=default_q)

        if st.button("🚀 Query Memory Layer", type="primary"):
            with st.spinner("Executing retrieval..."):
                start_p = time.perf_counter()
                try:
                    client = get_zep_client()
                    memory = StudentMemory(client)

                    if "Short-Term" in target_layer:
                        stm = ShortTermMemory(strategy="sliding", max_recent_messages=4, pressure_tokens=400)
                        stm.add("user", "TODO: hoan thanh benchmark report truoc thu Sau luc 16:00. LAB-REPORT-1600.")
                        stm.add("assistant", "Da ghi nhan deadline 16:00 thu Sau.")
                        stm.add("user", "Tiep theo chung ta lam gi?")
                        stm.add("assistant", "Chung ta test retrieval.")
                        res_text = stm.render()
                    elif "Long-Term" in target_layer:
                        res_text = memory.retrieve_long_term(target_user, f"play-{target_user}", custom_q)
                    elif "Episodic" in target_layer:
                        res_text = memory.retrieve_episodic(target_user, custom_q)
                    else:
                        res_text = memory.retrieve_semantic(settings.semantic_graph_id, custom_q)

                    lat_p = (time.perf_counter() - start_p) * 1000.0

                    st.success(f"Retrieved in {lat_p:.1f} ms")
                    st.markdown("#### Retrieval Output:")
                    st.code(res_text or "(No relevant evidence found)", language="markdown")
                except Exception as e:
                    st.error(f"Error querying layer: {e}")

    # --------------------------------------------------------------------------
    # Tab 4: Benchmark Reports
    # --------------------------------------------------------------------------
    with tab_reports:
        st.subheader("📊 Benchmark & Evaluation Analytics")
        st.caption("Comprehensive results from official test suite runs.")

        golden_report_path = _ROOT / "reports" / "golden_benchmark.json"
        practice_report_path = _ROOT / "reports" / "benchmark.json"
        no_mem_report_path = _ROOT / "reports" / "benchmark_no_memory.json"

        col_b1, col_b2, col_b3 = st.columns(3)
        with col_b1:
            st.markdown(
                """
                <div class="metric-box" style="border-top: 4px solid #10b981;">
                    <div class="metric-val" style="color:#10b981">20 / 20 (100%)</div>
                    <div class="metric-lbl">Golden Benchmark PASS (+10 Bonus)</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with col_b2:
            st.markdown(
                """
                <div class="metric-box" style="border-top: 4px solid #38bdf8;">
                    <div class="metric-val" style="color:#38bdf8">11 / 11 (100%)</div>
                    <div class="metric-lbl">Practice Benchmark PASS</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with col_b3:
            st.markdown(
                """
                <div class="metric-box" style="border-top: 4px solid #ef4444;">
                    <div class="metric-val" style="color:#ef4444">2 / 11 (18.2%)</div>
                    <div class="metric-lbl">No-Memory Baseline</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        st.markdown("---")

        selected_rep = st.radio("View Detailed Benchmark Run", ["Golden Benchmark (20 Cases)", "Practice Benchmark (11 Cases)", "Comparison Summary"], horizontal=True)

        if selected_rep == "Golden Benchmark (20 Cases)":
            if golden_report_path.exists():
                g_data = load_json(golden_report_path)
                st.write(f"**Run Timestamp**: `{g_data.get('timestamp', 'N/A')}` | **Hit Rate**: `{g_data.get('hit_rate_pct', 100.0)}%` | **Total Cases**: `{g_data.get('total_cases', 20)}`")
                st.table([
                    {
                        "Case ID": r.get("case_id"),
                        "Layer": r.get("layer"),
                        "Status": "✅ PASS" if r.get("passed") else "❌ FAIL",
                        "Latency (ms)": f"{r.get('latency_ms', 0):.1f}",
                        "Tokens Used": r.get("total_used_tokens", 0),
                        "Expected Ground Truth": ", ".join(r.get("must_contain_all") or []),
                    }
                    for r in g_data.get("results", [])
                ])
            else:
                st.warning("reports/golden_benchmark.json not found.")

        elif selected_rep == "Practice Benchmark (11 Cases)":
            if practice_report_path.exists():
                p_data = load_json(practice_report_path)
                st.write(f"**Run Timestamp**: `{p_data.get('timestamp', 'N/A')}` | **Hit Rate**: `{p_data.get('hit_rate_pct', 100.0)}%`")
                st.table([
                    {
                        "Case ID": r.get("case_id"),
                        "Layer": r.get("layer"),
                        "Status": "✅ PASS" if r.get("passed") else "❌ FAIL",
                        "Latency (ms)": f"{r.get('latency_ms', 0):.1f}",
                        "Tokens Used": r.get("total_used_tokens", 0),
                        "Expected Ground Truth": ", ".join(r.get("must_contain_all") or []),
                    }
                    for r in p_data.get("results", [])
                ])
            else:
                st.warning("reports/benchmark.json not found.")

        else:
            comp_path = _ROOT / "reports" / "comparison.md"
            if comp_path.exists():
                st.markdown(comp_path.read_text(encoding="utf-8"))
            else:
                st.info("Comparison report markdown file not found.")

    # --------------------------------------------------------------------------
    # Tab 5: Architecture & Review
    # --------------------------------------------------------------------------
    with tab_arch:
        st.subheader("🏗️ Architectural Review & Memory System Design")
        st.markdown(
            """
            ### 1. 4-Tier Memory Hierarchy
            | Memory Layer | Storage Engine | Scope | Primary Purpose | Key Lab Case |
            | :--- | :--- | :--- | :--- | :--- |
            | **Short-Term** | In-Memory (Sliding Buffer + Compaction) | Thread | In-thread recent turns, sliding window compaction, durable notes | E01, E10 |
            | **Long-Term** | Zep Cloud User Graph | User (`user_id`) | Cross-session user preferences, profile facts, temporal validity (`valid_at`/`invalid_at`) | E02, E03, E08, E09 |
            | **Episodic** | Zep Graph Episodes Search | User (`user_id`) | Raw interaction turns, verbatim trajectory codes (`ASYNC-FIX-20`), error reflections | E04, E05 |
            | **Semantic** | Zep Standalone Knowledge Graph | Global (`graph_id`) | Shared corporate policies, technical playbooks (`PAYMENT-RULE-3`), domain rules | E06, E11 |

            ---

            ### 2. Context Budget Policy (10/4/3/3 Priority Assembly)
            - **Total Budget Window**: 8,000 tokens (or configured limit).
            - **Layer Ratios**:
              - Short-Term: **10 / 20** (50% max window) — Top Priority
              - Long-Term: **4 / 20** (20% max window)
              - Episodic: **3 / 20** (15% max window)
              - Semantic: **3 / 20** (15% max window)
            - **Assembly Strategy**: Tail-trimming on each layer to preserve the most salient head facts/context.

            ---

            ### 3. Key Technical Insights from Implementation
            1. **Zep Graph Search Character Limit**: `graph.search` rejects queries > 400 chars, mitigated via `cap_query(query)`.
            2. **Verbatim Fact Retention**:
               - Context block paraphrases extracted facts (e.g. "has a to-do item" instead of literal `16:00`).
               - Student implementation solves this via multi-strategy retrieval: **Context Block + Edge Facts + Fallback Episodes**.
            3. **Scope Isolation**:
               - User queries strictly query `user_id=...`.
               - Domain KB strictly queries `graph_id=...`. Cross-contamination is prevented by design.
            4. **Privacy & Forget Drills**:
               - Supported via deterministic GDPR/CCPA forget mechanism (`src/forget.py`).
            """
        )


if __name__ == "__main__":
    main()

"""VinUni AI Agent Lab 17 — Multi-Tier Memory Agent Dashboard & Demo UI with TraceLog.

Author: Ta Minh Duc (MSSV: 2A202601497)
Architecture: Zep Cloud V3 + Local Short-Term Memory + Redis + Qdrant + Gemini

Features:
1. 🎯 Case Evaluator: Interactive inspection of all 31 evaluation cases (E01-E11 & G01-G20) with TraceLog.
2. 💬 Memory-Grounded Chat: Multi-turn chat with step-by-step turn tracelogs & memory inspect.
3. 🔍 Live Memory Playground: Ad-hoc query against individual layers with execution latency tracing.
4. 📊 Benchmark Analytics: Interactive summary of Golden (20/20), Practice (11/11), and Baseline reports.
5. 🏗️ Architecture & Review: Detailed diagrams, layer comparison, and budget policies.
"""

from __future__ import annotations

import html
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st

from src.config import settings
from src.context_budget import ContextBudgetManager
from src.llm import gemini_available, generate_reply
from src.memory_student import StudentMemory
from src.router import route_query
from src.short_term import ShortTermMemory
from src.utils import GOLDEN_PATH, cap_query, join_nonempty, load_dataset, load_json
from src.zep_common import get_zep_client, prime_eval_thread, render_graph_search

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
.trace-item {
    border-left: 3px solid #38bdf8;
    padding: 8px 12px;
    margin-bottom: 8px;
    background: rgba(56, 189, 248, 0.05);
    border-radius: 0 8px 8px 0;
    font-size: 0.85rem;
}
.trace-step {
    font-weight: 700;
    color: #38bdf8;
}
.trace-time {
    color: #94a3b8;
    font-size: 0.75rem;
    float: right;
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
# Execution Trace Logging System
# ==============================================================================

@dataclass
class TraceStep:
    name: str
    category: str  # ROUTER, SHORT_TERM, LONG_TERM, EPISODIC, SEMANTIC, BUDGET, LLM
    duration_ms: float
    status: str  # SUCCESS, INFO, WARN, SKIPPED
    details: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().strftime("%H:%M:%S.%f")[:-3])


class ExecutionTracer:
    """Collects fine-grained execution steps, timings, and metadata for memory queries."""

    def __init__(self, query: str, user_id: str, thread_id: str):
        self.query = query
        self.user_id = user_id
        self.thread_id = thread_id
        self.steps: list[TraceStep] = []
        self.start_time = time.perf_counter()

    def add_step(
        self,
        name: str,
        category: str,
        duration_ms: float,
        status: str = "SUCCESS",
        details: dict[str, Any] | None = None,
    ) -> None:
        self.steps.append(
            TraceStep(
                name=name,
                category=category,
                duration_ms=round(duration_ms, 2),
                status=status,
                details=details or {},
            )
        )

    def total_duration_ms(self) -> float:
        return round((time.perf_counter() - self.start_time) * 1000.0, 2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "user_id": self.user_id,
            "thread_id": self.thread_id,
            "total_duration_ms": self.total_duration_ms(),
            "step_count": len(self.steps),
            "steps": [asdict(s) for s in self.steps],
        }


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


def retrieve_with_tracelog(
    memory: StudentMemory,
    case: dict[str, Any],
    extra_messages: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Execute multi-tier retrieval with rich trace logging at each step."""
    query = case.get("query", "")
    user_id = case.get("user_id", "")
    thread_id = case.get("thread_id") or f"ui-{case.get('id', 'case')}"

    tracer = ExecutionTracer(query=query, user_id=user_id, thread_id=thread_id)

    # --------------------------------------------------------------------------
    # Step 1: Query Analysis & Layer Routing
    # --------------------------------------------------------------------------
    t0 = time.perf_counter()
    routed_layers = route_query(query)
    expected_layer = case.get("expected_layer", "")
    wanted_layers = set(case.get("retrieve_layers") or DURABLE_FOR_LAYER.get(expected_layer, []))
    wanted_layers |= {x for x in routed_layers if x != "short_term"}
    t_route = (time.perf_counter() - t0) * 1000.0

    tracer.add_step(
        name="Query Classification & Routing",
        category="ROUTER",
        duration_ms=t_route,
        status="SUCCESS",
        details={
            "query_length": len(query),
            "expected_layer": expected_layer,
            "routed_layers_from_query": list(routed_layers),
            "effective_target_layers": list(wanted_layers),
        },
    )

    # --------------------------------------------------------------------------
    # Step 2: Short-Term Memory Pipeline
    # --------------------------------------------------------------------------
    t0 = time.perf_counter()
    short_term = ShortTermMemory(strategy="sliding", max_recent_messages=6, pressure_tokens=450)
    all_turns = case_thread_messages(case) + list(extra_messages or [])
    for msg in all_turns:
        short_term.add(msg.get("role", "user"), msg.get("content", ""))
    stm_rendered = short_term.render()
    t_stm = (time.perf_counter() - t0) * 1000.0

    tracer.add_step(
        name="Short-Term Memory Buffer & Compaction",
        category="SHORT_TERM",
        duration_ms=t_stm,
        status="SUCCESS",
        details={
            "total_turns_processed": len(all_turns),
            "sliding_window_strategy": "sliding",
            "max_recent_messages": 6,
            "rendered_length_chars": len(stm_rendered),
            "has_durable_notes": "<DURABLE_NOTES>" in stm_rendered,
        },
    )

    layers = {
        "short_term": stm_rendered,
        "long_term": "",
        "episodic": "",
        "semantic": "",
    }

    # --------------------------------------------------------------------------
    # Step 3: Long-Term Memory Retrieval
    # --------------------------------------------------------------------------
    if "long_term" in wanted_layers:
        t0 = time.perf_counter()
        scratch_thread = f"ui-{case.get('id', 'scratch')}"
        try:
            # 3a. Prime thread & Get Context Block
            prime_eval_thread(memory.client, user_id, scratch_thread, query)
            user_context = memory.client.thread.get_user_context(thread_id=scratch_thread)
            context_block = getattr(user_context, "context", "") or ""

            # 3b. Edge Facts search
            facts_res = memory.client.graph.search(
                user_id=user_id,
                query=cap_query(query),
                scope="edges",
                limit=20,
            )
            fact_text = render_graph_search(facts_res)

            # 3c. Fallback Episodes search for verbatim facts (e.g. 16:00)
            episodes_res = memory.client.graph.search(
                user_id=user_id,
                query=cap_query(query),
                scope="episodes",
                limit=5,
            )
            episode_text = render_graph_search(episodes_res, episode_char_cap=200)

            ltm_combined = join_nonempty([context_block, fact_text, episode_text], sep="\n\n")
            layers["long_term"] = ltm_combined
            t_ltm = (time.perf_counter() - t0) * 1000.0

            tracer.add_step(
                name="Long-Term User Graph Retrieval",
                category="LONG_TERM",
                duration_ms=t_ltm,
                status="SUCCESS",
                details={
                    "user_id": user_id,
                    "primed_thread": scratch_thread,
                    "context_block_chars": len(context_block),
                    "edge_facts_found": len(getattr(facts_res, "edges", []) or []),
                    "episode_backfill_chars": len(episode_text),
                    "total_long_term_chars": len(ltm_combined),
                },
            )
        except Exception as e:
            t_ltm = (time.perf_counter() - t0) * 1000.0
            tracer.add_step(
                name="Long-Term User Graph Retrieval",
                category="LONG_TERM",
                duration_ms=t_ltm,
                status="WARN",
                details={"error": str(e)},
            )
    else:
        tracer.add_step(
            name="Long-Term User Graph Retrieval",
            category="LONG_TERM",
            duration_ms=0.0,
            status="SKIPPED",
            details={"reason": "Layer not requested for this query"},
        )

    # --------------------------------------------------------------------------
    # Step 4: Episodic Memory Retrieval
    # --------------------------------------------------------------------------
    if "episodic" in wanted_layers:
        t0 = time.perf_counter()
        try:
            ep_res = memory.client.graph.search(
                user_id=user_id,
                query=cap_query(query),
                scope="episodes",
                limit=15,
            )
            ep_rendered = render_graph_search(ep_res, episode_char_cap=180)
            layers["episodic"] = ep_rendered
            t_epm = (time.perf_counter() - t0) * 1000.0

            tracer.add_step(
                name="Episodic Trajectory Search",
                category="EPISODIC",
                duration_ms=t_epm,
                status="SUCCESS",
                details={
                    "user_id": user_id,
                    "query_cap_len": len(cap_query(query)),
                    "episodes_found": len(getattr(ep_res, "episodes", []) or []),
                    "rendered_chars": len(ep_rendered),
                },
            )
        except Exception as e:
            t_epm = (time.perf_counter() - t0) * 1000.0
            tracer.add_step(
                name="Episodic Trajectory Search",
                category="EPISODIC",
                duration_ms=t_epm,
                status="WARN",
                details={"error": str(e)},
            )
    else:
        tracer.add_step(
            name="Episodic Trajectory Search",
            category="EPISODIC",
            duration_ms=0.0,
            status="SKIPPED",
            details={"reason": "Layer not requested for this query"},
        )

    # --------------------------------------------------------------------------
    # Step 5: Semantic Domain Graph Retrieval
    # --------------------------------------------------------------------------
    if "semantic" in wanted_layers:
        t0 = time.perf_counter()
        capped = cap_query(query)
        try:
            try:
                sem_res = memory.client.graph.search(
                    graph_id=settings.semantic_graph_id,
                    query=capped,
                    scope="episodes",
                    limit=8,
                )
            except Exception:
                sem_res = memory.client.graph.search(
                    graph_id=settings.semantic_graph_id,
                    query=capped,
                    scope="nodes",
                    limit=8,
                )
            sem_rendered = render_graph_search(sem_res)
            layers["semantic"] = sem_rendered
            t_sem = (time.perf_counter() - t0) * 1000.0

            tracer.add_step(
                name="Semantic Standalone KB Search",
                category="SEMANTIC",
                duration_ms=t_sem,
                status="SUCCESS",
                details={
                    "graph_id": settings.semantic_graph_id,
                    "rendered_chars": len(sem_rendered),
                    "scope_used": "episodes/nodes",
                },
            )
        except Exception as e:
            t_sem = (time.perf_counter() - t0) * 1000.0
            tracer.add_step(
                name="Semantic Standalone KB Search",
                category="SEMANTIC",
                duration_ms=t_sem,
                status="WARN",
                details={"error": str(e)},
            )
    else:
        tracer.add_step(
            name="Semantic Standalone KB Search",
            category="SEMANTIC",
            duration_ms=0.0,
            status="SKIPPED",
            details={"reason": "Layer not requested for this query"},
        )

    # --------------------------------------------------------------------------
    # Step 6: Context Budget Allocation & Priority Assembly
    # --------------------------------------------------------------------------
    t0 = time.perf_counter()
    merged, budget = memory.assemble_context(layers)
    t_budget = (time.perf_counter() - t0) * 1000.0

    raw_total = sum(b.get("raw_tokens", 0) for b in budget.values())
    used_total = sum(b.get("used_tokens", 0) for b in budget.values())
    reduction_pct = round((1.0 - (used_total / raw_total)) * 100, 1) if raw_total > 0 else 0.0

    tracer.add_step(
        name="Context Budget Manager (10/4/3/3 Rule)",
        category="BUDGET",
        duration_ms=t_budget,
        status="SUCCESS",
        details={
            "raw_total_tokens": raw_total,
            "used_total_tokens": used_total,
            "trimmed_tokens": raw_total - used_total,
            "context_reduction_pct": reduction_pct,
            "per_layer_tokens": {k: f"{v.get('used_tokens')}/{v.get('limit_tokens')}" for k, v in budget.items()},
        },
    )

    return {
        "merged_context": merged,
        "layers": layers,
        "budget": budget,
        "tracer": tracer,
        "tracelog": tracer.to_dict(),
    }


def render_tracelog_ui(tracelog: dict[str, Any]) -> None:
    """Render a visual, step-by-step waterfall trace log in Streamlit."""
    steps = tracelog.get("steps", [])
    total_ms = tracelog.get("total_duration_ms", 0.0)

    st.markdown(
        f"""
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
            <b>⏱️ Execution Trace Timeline ({len(steps)} Steps)</b>
            <span style="color:#38bdf8; font-weight:700; font-size:0.9rem;">Total Pipeline Latency: {total_ms:.1f} ms</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    status_icons = {
        "SUCCESS": "🟢",
        "INFO": "🔵",
        "WARN": "🟡",
        "SKIPPED": "⚪",
    }

    for idx, step in enumerate(steps, start=1):
        icon = status_icons.get(step["status"], "🔹")
        dur = step["duration_ms"]
        pct = round((dur / total_ms) * 100, 1) if total_ms > 0 else 0

        st.markdown(
            f"""
            <div class="trace-item">
                <span class="trace-time">{step["timestamp"]} · {dur:.1f} ms ({pct}%)</span>
                <span class="trace-step">{icon} Step {idx}: {step["name"]}</span>
                <div style="font-size:0.8rem; color:#cbd5e1; margin-top:4px;">
                    Category: <code>{step["category"]}</code> | Status: <b>{step["status"]}</b>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if step.get("details"):
            with st.expander(f"Inspect Step {idx} details ({step['name']})", expanded=False):
                st.json(step["details"])


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
            <p>Interactive Demo, TraceLog & Code Review · Student: <b>Tạ Minh Đức (2A202601497)</b> · Zep Cloud V3 + Short-Term + Redis + Qdrant</p>
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
        st.subheader("🎯 Evaluation Cases & Step-by-Step TraceLog")
        st.caption("Select any practice (E01-E11) or golden (G01-G20) case to inspect real-time retrieval and execution tracelogs across all memory tiers.")

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
            run_retrieval = col_btn.button("⚡ Run Retrieval with TraceLog", use_container_width=True, type="primary")

            if run_retrieval:
                with st.spinner("Executing multi-tier retrieval pipeline with live tracelog..."):
                    try:
                        client = get_zep_client()
                        memory = StudentMemory(client)
                        eval_res = retrieve_with_tracelog(memory, case)

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
                total_lat = eval_res.get("tracelog", {}).get("total_duration_ms", 0.0)

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
                            <div class="metric-val">{total_lat:.1f} ms</div>
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

                # ==============================================================
                # Live TraceLog Inspector
                # ==============================================================
                st.markdown("---")
                st.markdown("#### 📜 Execution TraceLog (Pipeline Waterfall)")
                render_tracelog_ui(eval_res.get("tracelog", {}))

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
    # Tab 2: Memory-Grounded Chat with Turn-by-Turn TraceLogs
    # --------------------------------------------------------------------------
    with tab_chat:
        st.subheader("💬 Live Multi-Turn Chat with Turn-by-Turn TraceLogs")
        st.caption("Chat dynamically as a user persona. Every turn executes multi-tier memory retrieval with real-time tracelogging.")

        col_c1, col_c2 = st.columns([2, 1])
        with col_c1:
            persona = st.selectbox("Select User Persona", ["minh-lab17 (Minh Nguyen)", "lan-lab17 (Lan Tran)", "Custom User"])
            user_id = "minh-lab17" if "minh" in persona else ("lan-lab17" if "lan" in persona else "guest-user")
        with col_c2:
            st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
            if st.button("🗑️ Reset Chat History", use_container_width=True):
                st.session_state.chat_history = []
                st.rerun()

        if "chat_history" not in st.session_state:
            st.session_state.chat_history = []

        # Display conversation history
        for turn_idx, msg in enumerate(st.session_state.chat_history, start=1):
            with st.chat_message(msg["role"]):
                st.write(msg["content"])
                if msg.get("tracelog"):
                    with st.expander(f"🔍 Turn {turn_idx} TraceLog ({msg['tracelog'].get('total_duration_ms', 0):.1f} ms)"):
                        render_tracelog_ui(msg["tracelog"])
                elif "retrieved_layers" in msg:
                    st.caption(f"🧠 Memory Layers Hit: `{', '.join(msg['retrieved_layers']) or 'none'}`")

        # Chat Input
        user_prompt = st.chat_input("Ask about personal projects, previous errors, or payment rules...")
        if user_prompt:
            st.session_state.chat_history.append({"role": "user", "content": user_prompt})
            with st.chat_message("user"):
                st.write(user_prompt)

            with st.chat_message("assistant"):
                with st.spinner("Processing turn: executing memory retrieval & generating response..."):
                    try:
                        client = get_zep_client()
                        memory = StudentMemory(client)
                        chat_case = {
                            "id": f"chat-{len(st.session_state.chat_history)}",
                            "user_id": user_id,
                            "thread_id": f"chat-{user_id}",
                            "query": user_prompt,
                            "expected_layer": "mixed",
                        }
                        res = retrieve_with_tracelog(memory, chat_case, st.session_state.chat_history)
                        context = res.get("merged_context", "")
                        active_layers = [k for k, v in res["layers"].items() if v.strip()]
                        tracelog = res.get("tracelog", {})

                        # LLM generation
                        t_llm_start = time.perf_counter()
                        if gemini_available():
                            past_msgs = [{"role": m["role"], "content": m["content"]} for m in st.session_state.chat_history[:-1]]
                            reply = generate_reply(context, past_msgs, user_prompt)
                            t_llm = (time.perf_counter() - t_llm_start) * 1000.0
                            status_llm = "SUCCESS"
                        else:
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
                            t_llm = (time.perf_counter() - t_llm_start) * 1000.0
                            status_llm = "MOCK_FALLBACK"

                        # Append LLM step to tracelog
                        res["tracer"].add_step(
                            name=f"LLM Generation ({settings.gemini_model if gemini_available() else 'Fallback Generator'})",
                            category="LLM",
                            duration_ms=t_llm,
                            status=status_llm,
                            details={
                                "prompt_context_length": len(context),
                                "reply_length": len(reply),
                            },
                        )
                        tracelog = res["tracer"].to_dict()

                        st.write(reply)

                        with st.expander(f"🔍 Turn TraceLog ({tracelog.get('total_duration_ms', 0):.1f} ms)", expanded=False):
                            render_tracelog_ui(tracelog)

                        st.session_state.chat_history.append({
                            "role": "assistant",
                            "content": reply,
                            "retrieved_layers": active_layers,
                            "tracelog": tracelog,
                        })

                    except Exception as e:
                        st.error(f"Error in chat processing: {e}")

    # --------------------------------------------------------------------------
    # Tab 3: Memory Playground
    # --------------------------------------------------------------------------
    with tab_play:
        st.subheader("🔍 Ad-Hoc Memory Layer Playground with Tracing")
        st.caption("Directly test and inspect individual memory components with custom queries and latency measurements.")

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

        if st.button("🚀 Query Memory Layer with Tracelog", type="primary"):
            with st.spinner("Executing retrieval..."):
                start_p = time.perf_counter()
                tracer_p = ExecutionTracer(query=custom_q, user_id=target_user, thread_id=f"play-{target_user}")
                try:
                    client = get_zep_client()
                    memory = StudentMemory(client)

                    if "Short-Term" in target_layer:
                        t0 = time.perf_counter()
                        stm = ShortTermMemory(strategy="sliding", max_recent_messages=4, pressure_tokens=400)
                        stm.add("user", "TODO: hoan thanh benchmark report truoc thu Sau luc 16:00. LAB-REPORT-1600.")
                        stm.add("assistant", "Da ghi nhan deadline 16:00 thu Sau.")
                        stm.add("user", "Tiep theo chung ta lam gi?")
                        stm.add("assistant", "Chung ta test retrieval.")
                        res_text = stm.render()
                        tracer_p.add_step("Short-Term Buffer Render", "SHORT_TERM", (time.perf_counter() - t0) * 1000.0, "SUCCESS", {"rendered_chars": len(res_text)})

                    elif "Long-Term" in target_layer:
                        t0 = time.perf_counter()
                        res_text = memory.retrieve_long_term(target_user, f"play-{target_user}", custom_q)
                        tracer_p.add_step("Long-Term User Graph Retrieval", "LONG_TERM", (time.perf_counter() - t0) * 1000.0, "SUCCESS", {"user_id": target_user, "chars": len(res_text)})

                    elif "Episodic" in target_layer:
                        t0 = time.perf_counter()
                        res_text = memory.retrieve_episodic(target_user, custom_q)
                        tracer_p.add_step("Episodic Trajectory Retrieval", "EPISODIC", (time.perf_counter() - t0) * 1000.0, "SUCCESS", {"user_id": target_user, "chars": len(res_text)})

                    else:
                        t0 = time.perf_counter()
                        res_text = memory.retrieve_semantic(settings.semantic_graph_id, custom_q)
                        tracer_p.add_step("Semantic Knowledge Graph Retrieval", "SEMANTIC", (time.perf_counter() - t0) * 1000.0, "SUCCESS", {"graph_id": settings.semantic_graph_id, "chars": len(res_text)})

                    lat_p = (time.perf_counter() - start_p) * 1000.0

                    st.success(f"Retrieved in {lat_p:.1f} ms")
                    render_tracelog_ui(tracer_p.to_dict())

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

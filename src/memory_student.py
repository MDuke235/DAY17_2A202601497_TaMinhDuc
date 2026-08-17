from __future__ import annotations

from typing import Any

from .config import settings
from .context_budget import ContextBudgetManager
from .utils import cap_query, join_nonempty
from .zep_common import prime_eval_thread, render_graph_search


class StudentMemory:
    """Only this file needs to be edited by students."""

    def __init__(self, client: Any):
        self.client = client
        self.budget = ContextBudgetManager(settings.context_tokens)

    # NOTE: Zep rejects graph.search queries longer than 400 characters. Some
    # eval queries are longer than that, so wrap every query with
    # `cap_query(query)` (see src/utils.py) before passing it to graph.search.

    def retrieve_long_term(self, user_id: str, thread_id: str, query: str) -> str:
        # Zep builds the Context Block from the user graph, but it decides
        # relevance against the current thread slice, so the eval query has to
        # be in the thread before asking for user context.
        prime_eval_thread(self.client, user_id, thread_id, query)
        user_context = self.client.thread.get_user_context(thread_id=thread_id)
        context_block = getattr(user_context, "context", "") or ""

        # The Context Block is relevance-ranked and bounded, so a low-salience
        # fact (an open-loop deadline) can fall out of it. An explicit edge
        # search backfills those facts and exposes valid_at/invalid_at, which is
        # what makes a superseded-preference case auditable instead of guessed.
        try:
            facts = self.client.graph.search(
                user_id=user_id,
                query=cap_query(query),
                scope="edges",
                limit=20,
            )
            fact_text = render_graph_search(facts)
        except Exception:
            # Retrieval is best-effort: keep the Context Block rather than
            # failing the whole case if the fact search errors out.
            fact_text = ""

        return join_nonempty([context_block, fact_text], sep="\n\n")

    def retrieve_episodic(self, user_id: str, query: str) -> str:
        # Episodes are the raw turn excerpts, so they still carry the literal
        # trajectory markers (ASYNC-FIX-20, concurrency=20) that edge/fact
        # extraction paraphrases away. Scope stays on user_id: an episode is
        # owned by one user, so this call must never cross into another graph.
        results = self.client.graph.search(
            user_id=user_id,
            query=cap_query(query),
            scope="episodes",
            limit=15,
        )

        # A wide limit plus a per-episode cap buys breadth instead of depth: the
        # verbose session turns get clipped, so the concise reflection turn
        # ("connection churn, not timeout threshold") survives alongside the fix
        # turn rather than being crowded out of the episodic budget.
        return render_graph_search(results, episode_char_cap=180)

    def retrieve_semantic(self, graph_id: str, query: str) -> str:
        # LAB TODO 3/4
        # Search the standalone graph (graph_id, NOT user_id).
        # Recommended: scope="episodes" — it returns raw document text that keeps
        # literal markers (e.g. PAYMENT-RULE-3). The "auto" scope returns
        # extracted facts that DROP those literal codes, so avoid it here.
        # Fallback: scope="nodes".
        raise NotImplementedError("LAB TODO: implement semantic graph search")

    def assemble_context(self, layers: dict[str, str]) -> tuple[str, dict[str, dict[str, int]]]:
        # LAB TODO 4/4
        # Use ContextBudgetManager to enforce 10/4/3/3 budget and priority order.
        raise NotImplementedError("LAB TODO: assemble/trim memory context")

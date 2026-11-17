"""StateGraph for one recipient's personalised email.

  START → validate_input ─invalid→ END
              │ok (fan-out: both run in the SAME superstep, in parallel)
              ├──────────────► job_context        (Agent A: what the job needs; no model call without a JD)
              └──────────────► candidate_evidence (Agent B: what in the resume supports it)
                                    both ─► merge_context (join, deterministic)
                                              ▼
              generate_email → review_email → validate_fabrication ─┬─ finalize → END
                                   ▲                                 └─ revise_email (≤ 2 times) ─┐
                                   └───────────────────────────────────────────────────────────────┘

The only cycle is revise_email → review_email → validate_fabrication, and route_after_validation stops it after
MAX_REVISIONS. RECURSION_LIMIT is a second, hard bound. Compiled once per process; stateless (no checkpointer, nothing
is stored by LangGraph). Saving the draft is NOT a node: it happens in runner.py, in a conditional database update,
so the graph has no Django objects and a failed run leaves nothing half-written.
"""

from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from . import nodes
from .state import GraphContext, PersonalizationState

RECURSION_LIMIT = 30  # the longest legal path is 13 steps


def build_graph() -> StateGraph:
    g = StateGraph(PersonalizationState, context_schema=GraphContext)
    g.add_node("validate_input", nodes.validate_input)
    g.add_node("job_context", nodes.job_context)
    g.add_node("candidate_evidence", nodes.candidate_evidence)
    g.add_node("merge_context", nodes.merge_context)
    g.add_node("generate_email", nodes.generate_email)
    g.add_node("review_email", nodes.review_email)
    g.add_node("validate_fabrication", nodes.validate_fabrication)
    g.add_node("revise_email", nodes.revise_email)
    g.add_node("finalize", nodes.finalize)

    g.add_edge(START, "validate_input")
    g.add_conditional_edges("validate_input", nodes.route_after_input, ["job_context", "candidate_evidence", END])
    g.add_edge(["job_context", "candidate_evidence"], "merge_context")  # waits for BOTH branches
    g.add_edge("merge_context", "generate_email")
    g.add_edge("generate_email", "review_email")
    g.add_edge("review_email", "validate_fabrication")
    g.add_conditional_edges("validate_fabrication", nodes.route_after_validation, {"revise": "revise_email", "finalize": "finalize"})
    g.add_edge("revise_email", "review_email")
    g.add_edge("finalize", END)
    return g


@lru_cache(maxsize=1)
def compiled_graph():
    return build_graph().compile()

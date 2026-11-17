"""StateGraph for the personalised job application email.

  START → validate_input ─invalid→ END
              │ok
              ▼
        analyse_job → analyse_candidate → match_experience → generate_email → review_email → validate_fabrication
                                                                                                  │
                                       ┌──────────── revise (blocked or weak, revision_count < 1) ┤
                                       ▼                                                          │finalize
                                 revise_email ──→ validate_fabrication (re-validate)              ▼
                                                                                              finalize → END

The only cycle is revise_email → validate_fabrication, and route_after_validation stops it after one revision.
RECURSION_LIMIT is a second, hard bound. Compiled once per process; stateless (no checkpointer).
"""

from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from . import nodes
from .state import GraphContext, JobEmailState

RECURSION_LIMIT = 20  # the longest legal path is 10 steps


def build_graph() -> StateGraph:
    g = StateGraph(JobEmailState, context_schema=GraphContext)
    g.add_node("validate_input", nodes.validate_input)
    g.add_node("analyse_job", nodes.analyse_job)
    g.add_node("analyse_candidate", nodes.analyse_candidate)
    g.add_node("match_experience", nodes.match_experience)
    g.add_node("generate_email", nodes.generate_email)
    g.add_node("review_email", nodes.review_email)
    g.add_node("validate_fabrication", nodes.validate_fabrication)
    g.add_node("revise_email", nodes.revise_email)
    g.add_node("finalize", nodes.finalize)

    g.add_edge(START, "validate_input")
    g.add_conditional_edges("validate_input", nodes.route_after_input, {"invalid": END, "ok": "analyse_job"})
    g.add_edge("analyse_job", "analyse_candidate")
    g.add_edge("analyse_candidate", "match_experience")
    g.add_edge("match_experience", "generate_email")
    g.add_edge("generate_email", "review_email")
    g.add_edge("review_email", "validate_fabrication")
    g.add_conditional_edges("validate_fabrication", nodes.route_after_validation, {"revise": "revise_email", "finalize": "finalize"})
    g.add_edge("revise_email", "validate_fabrication")
    g.add_edge("finalize", END)
    return g


@lru_cache(maxsize=1)
def compiled_graph():
    return build_graph().compile()

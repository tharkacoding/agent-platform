"""Teacher — assess_level → (explain → examples)? → quiz → check_quiz ⇄ → feedback.

config.mode: "teach" (default) | "curriculum" (builds a syllabus instead of explaining) | "questions" (quiz only).
"""
from __future__ import annotations


from langgraph.graph import END, START, StateGraph

from ..core.sdk import archetype, get_ctx
from . import _common as c


class TeacherState(c.BaseState, total=False):
    topic: str
    level: str
    explanation: str
    examples: list
    quiz: list
    quiz_ok: bool
    attempts: int


def assess_level(state: TeacherState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("assess_level", "Assessing topic and learner level")
    t = c.instruction(state).lower()
    lvl = "beginner" if any(w in t for w in ("beginner", "new to", "basics", "simple")) else \
        "advanced" if any(w in t for w in ("advanced", "expert", "deep")) else "intermediate"
    out = c.ask_json(ctx, "Identify the topic and learner level. Return {\"topic\": str, \"level\": beginner|intermediate|advanced}.",
                     c.instruction(state) + "\n" + c.context_text(state, 2000),
                     fallback=lambda: {"topic": " ".join(c.keywords(c.instruction(state), 4)) or "topic", "level": lvl},
                     validate=lambda d: bool(d.get("topic")))
    return {"topic": out["topic"], "level": out.get("level", lvl), "attempts": 0}


def explain(state: TeacherState, config) -> dict:
    ctx = get_ctx(config)
    mode = ctx.spec.config.get("mode", "teach")
    ctx.node("explain", "Designing the curriculum" if mode == "curriculum" else "Explaining")
    if mode == "curriculum":
        fb = lambda: "\n".join(f"**Week {i + 1}** — {s}" for i, s in enumerate(
            ["Foundations of " + state["topic"], "Core techniques", "Practice problems", "Projects & review"]))
        role = ("Design a week-by-week curriculum: objectives, topics, practice and assessment per week. Markdown.")
    else:
        fb = lambda: f"**{state['topic']}** — a {state['level']}-level overview.\n\n{c.instruction(state)}"
        role = f"Explain clearly for a {state['level']} learner with intuition first, then precise detail. Markdown."
    return {"explanation": c.ask_text(ctx, role, f"Topic: {state['topic']}\nRequest: {c.instruction(state)}", fallback=fb)}


def examples(state: TeacherState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("examples", "Creating worked examples")
    out = c.ask_json(ctx, "Give 2-3 worked examples. Return {\"examples\": [{\"problem\", \"solution\"}]}.",
                     f"Topic: {state['topic']} ({state['level']})\nExplanation:\n{state.get('explanation', '')[:4000]}",
                     fallback=lambda: {"examples": [{"problem": f"Apply {state['topic']} to a small case",
                                                      "solution": "Work through it step by step."}]},
                     validate=lambda d: isinstance(d.get("examples"), list))
    return {"examples": out["examples"][:3]}


def quiz(state: TeacherState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("quiz", "Writing quiz questions" + (f" (retry {state['attempts']})" if state.get("attempts") else ""))
    n = int(ctx.spec.config.get("questions", 5))
    fb = {"questions": [{"q": f"Question {i + 1} about {state['topic']}?", "options": ["A", "B", "C", "D"],
                         "answer": "A", "explanation": "See the explanation."} for i in range(min(n, 3))]}
    out = c.ask_json(ctx, f"Write {n} multiple-choice questions with 4 options each. Return {{\"questions\": "
                          "[{\"q\", \"options\": [4], \"answer\": one of the options, \"explanation\"}]}.",
                     f"Topic: {state['topic']} ({state['level']})\nRequest: {c.instruction(state)}\n"
                     f"Material:\n{state.get('explanation', '')[:4000]}", fallback=lambda: fb,
                     validate=lambda d: isinstance(d.get("questions"), list) and d["questions"])
    return {"quiz": out["questions"][:15]}


def check_quiz(state: TeacherState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("check_quiz", "Validating quiz answers")
    good = all(isinstance(q, dict) and q.get("q") and isinstance(q.get("options"), list) and
               q.get("answer") in q.get("options", []) for q in state["quiz"])
    return {"quiz_ok": good, "attempts": state.get("attempts", 0) + 1}


def feedback(state: TeacherState, config) -> dict:
    ctx = get_ctx(config)
    ctx.node("feedback", "Assembling the lesson")
    parts = []
    if state.get("explanation"):
        parts.append(state["explanation"])
    if state.get("examples"):
        parts.append("## Worked examples\n" + "\n\n".join(f"**{e.get('problem')}**\n{e.get('solution')}" for e in state["examples"]))
    if state.get("quiz"):
        qs = []
        for i, q in enumerate(state["quiz"]):
            opts = " / ".join(map(str, q.get("options", [])))
            qs.append(f"{i + 1}. {q.get('q')}  \n   {opts}  \n   <details><summary>Answer</summary>{q.get('answer')} — "
                      f"{q.get('explanation', '')}</details>")
        parts.append("## Quiz\n" + "\n".join(qs))
    return {"report": ctx.report(f"{ctx.spec.config.get('mode', 'teach').title()} material on {state['topic']} "
                                 f"({state['level']}): {len(state.get('quiz', []))} questions",
                                 result="\n\n".join(parts),
                                 artifacts=[{"kind": "table", "title": "Quiz", "content": state.get("quiz", [])}],
                                 status="success" if state.get("quiz_ok", True) else "partial")}


def after_check(state: TeacherState) -> str:
    return "feedback" if state["quiz_ok"] or state.get("attempts", 0) >= 2 else "quiz"


@archetype("teacher", "Teacher/Curriculum/Question generator: assess → explain → examples → quiz ⇄ validate → lesson")
def build(spec):
    mode = spec.config.get("mode", "teach")
    g = StateGraph(TeacherState)
    for n, fn in [("assess_level", assess_level), ("explain", explain), ("examples", examples), ("quiz", quiz),
                  ("check_quiz", check_quiz), ("feedback", feedback)]:
        g.add_node(n, fn)
    g.add_edge(START, "assess_level")
    g.add_conditional_edges("assess_level", lambda s: "quiz" if mode == "questions" else "explain", ["quiz", "explain"])
    g.add_conditional_edges("explain", lambda s: "quiz" if mode == "curriculum" else "examples", ["quiz", "examples"])
    g.add_edge("examples", "quiz")
    g.add_edge("quiz", "check_quiz")
    g.add_conditional_edges("check_quiz", after_check, ["feedback", "quiz"])
    g.add_edge("feedback", END)
    return g

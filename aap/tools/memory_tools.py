"""Memory tools. Every agent has its OWN memory (scope = its id) and shares one GLOBAL memory (facts about the user,
preferences). Recall searches both; remember writes to the agent's own memory unless scope='global'."""
from ..core.sdk import tool
from ..memory import store


@tool("memory_recall", "Search long-term memory: your own agent memory + the shared global memory (facts about the "
      "user, preferences, past task outcomes).",
      {"query": {"type": "string"}, "k": {"type": "integer", "default": 5}})
def memory_recall(ctx, query: str, k: int = 5):
    return {"memories": store.search(query, k, scope=ctx.spec.id)}


@tool("memory_remember", "Save to long-term memory. kind: fact | episode | project. scope: 'agent' (your own memory, "
      "default — your methods/notes) or 'global' (shared with all agents — facts about the user).",
      {"text": {"type": "string"}, "kind": {"type": "string", "default": "fact"},
       "tags": {"type": "array", "items": {"type": "string"}, "default": []},
       "scope": {"type": "string", "default": "agent"}})
def memory_remember(ctx, text: str, kind: str = "fact", tags: list | None = None, scope: str = "agent"):
    return store.add(text, kind, tags or [], source=ctx.spec.id,
                     scope="global" if scope == "global" else ctx.spec.id)

"""Live verification of computer use (terminal, desktop screenshot, host Chrome) through the gateway API.

Usage: python scripts/verify_computer_use.py [case ...]   cases: a b c d  (default: all)
Auto-approves approvals that belong to the runs it starts (owner-requested automation for this check only).
Screenshots → C:\\Users\\<you>\\AgentOS\\shared\\verification\\
"""
import base64
import json
import sys
import time
from pathlib import Path

import httpx

API = "http://127.0.0.1:7860/api"
OUT = Path.home() / "AgentOS" / "shared" / "verification"
CASES = {
    "a": "run `python --version` and `dir` in the workspace",
    "b": "create hello.py in the workspace that prints hello, then run it",
    "c": "take a screenshot of my screen",
    "d": "open https://news.ycombinator.com in my chrome and take a screenshot and tell me the top 3 titles",
}


def run_case(key: str, msg: str, timeout: float = 420) -> dict:
    r = httpx.post(f"{API}/chat", json={"message": msg}, timeout=30).json()
    rid = r["run_id"]
    t0, approved = time.time(), set()
    while time.time() - t0 < timeout:
        for a in httpx.get(f"{API}/approvals", params={"status": "pending"}, timeout=30).json():
            if a["run_id"] == rid and a["id"] not in approved:
                httpx.post(f"{API}/approvals/{a['id']}", json={"decision": "approve"}, timeout=30)
                approved.add(a["id"])
                print(f"  [{key}] approved: {a['title'][:110]}")
        run = httpx.get(f"{API}/runs/{rid}", timeout=30).json()
        if run["status"] != "running":
            break
        time.sleep(2)
    agents = [f"{e['agent_id']}:{e['status']}" for e in run["events"] if e["type"] == "agent" and e["status"] != "running"]
    shots = []
    for rep in run.get("reports", []):
        for art in rep.get("artifacts", []):
            if art.get("kind") == "image" and str(art.get("content", "")).startswith("data:image/png;base64,"):
                OUT.mkdir(parents=True, exist_ok=True)
                p = OUT / f"case_{key}_{rep['agent_id']}_{len(shots)}.png"
                p.write_bytes(base64.b64decode(art["content"].split(",", 1)[1]))
                shots.append(str(p))
    return {"case": key, "run_id": rid, "status": run["status"], "agents": agents, "approvals": len(approved),
            "final": (run.get("final") or "")[:1800], "screenshots": shots,
            "seconds": round(time.time() - t0, 1)}


if __name__ == "__main__":
    keys = sys.argv[1:] or list(CASES)
    for k in keys:
        print(f"== case {k}: {CASES[k]}")
        res = run_case(k, CASES[k])
        print(json.dumps(res, indent=2, ensure_ascii=False))

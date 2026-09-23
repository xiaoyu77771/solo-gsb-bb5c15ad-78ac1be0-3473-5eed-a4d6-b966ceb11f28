"""End-to-end demo using only the Python standard library.

Run with the API already listening on http://127.0.0.1:8000:

    python examples/demo.py

It creates a session, runs a mini adaptive quiz, demonstrates idempotent
retry with a repeated response_id, shows /next de-duplication and prints
session progress and the answer history.
"""

from __future__ import annotations

import json
import math
import random
import urllib.error
import urllib.request

BASE_URL = "http://127.0.0.1:8000"


def call(method: str, path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE_URL + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def show(label: str, obj: object) -> None:
    print(f"\n=== {label} ===")
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main() -> None:
    health = call("GET", "/health")
    show("GET /health", health)

    session = call(
        "POST", "/api/sessions",
        {"initial_theta": 0.0, "max_items": 8, "exposure_limit": 5},
    )
    sid = session["id"]
    show("POST /api/sessions", session)

    rng = random.Random(7)
    theta = session["theta"]

    for turn in range(1, 6):
        chosen = call("POST", f"/api/sessions/{sid}/next")
        item = chosen["item"]
        show(
            f"第 {turn} 题 (POST .../next)",
            {
                "item_id": item["id"],
                "stem": item["stem"],
                "information": round(chosen["information"], 4),
                "exposure_count": chosen["exposure_count"],
            },
        )

        # Re-calling /next before answering must return the SAME item and
        # must not increment exposure again.
        again = call("POST", f"/api/sessions/{sid}/next")
        assert again["item"]["id"] == item["id"], "重复 /next 应返回同一题"
        assert again["exposure_count"] == chosen["exposure_count"], \
            "重复 /next 不应重复计曝光"

        # Simulated examinee using the same 2PL model: answer correctly with
        # probability P(theta), otherwise pick a random distractor key.
        z = item["discrimination"] * (theta - item["difficulty"])
        p = 1 / (1 + math.exp(-z))
        keys = [opt["key"] for opt in item["options"]]
        # The served item hides the answer, but the demo needs to "be right"
        # sometimes: read it from the admin endpoint, else pick a distractor.
        detail = call("GET", f"/api/items/{item['id']}")
        if rng.random() < p:
            answer = detail["answer"]
        else:
            answer = rng.choice([k for k in keys if k != detail["answer"]])
        response_id = f"demo-resp-{turn}-sid-{sid[:8]}"

        result = call(
            "POST", f"/api/sessions/{sid}/responses",
            {"response_id": response_id, "item_id": item["id"], "response": answer},
        )
        theta = result["theta_after"]
        show(
            f"提交作答 (POST .../responses)",
            {
                "response_id": result["id"],
                "item_id": result["item_id"],
                "response": result["response"],
                "correct": result["correct"],
                "theta_before": round(result["theta_before"], 4),
                "theta_after": round(result["theta_after"], 4),
            },
        )

        # Idempotency: replay the exact same request body.
        replay = call(
            "POST", f"/api/sessions/{sid}/responses",
            {"response_id": response_id, "item_id": item["id"], "response": answer},
        )
        assert replay["created_at"] == result["created_at"], "应返回首次记录"
        assert replay["theta_after"] == result["theta_after"], "不应重复更新"
        print(f"  -> 重复 response_id 返回首次结果，能力值未二次更新 ✔")

    progress = call("GET", f"/api/sessions/{sid}/progress")
    show("GET .../progress (会话进度)", {
        "status": progress["status"],
        "theta": round(progress["theta"], 4),
        "answered_count": progress["answered_count"],
        "percent_complete": progress["percent_complete"],
        "answered_item_ids": progress["answered_item_ids"],
        "current_item": (progress["current_item"] or {}).get("id"),
    })

    history = call("GET", f"/api/sessions/{sid}/responses")
    show("GET .../responses (答题记录)", {
        "count": history["count"],
        "results": [
            {
                "item_id": r["item_id"],
                "correct": r["correct"],
                "theta_after": round(r["theta_after"], 4),
            }
            for r in history["results"]
        ],
    })

    bank = call("GET", "/api/items?limit=3")
    show("GET /api/items?limit=3 (题库与曝光)", bank)


if __name__ == "__main__":
    try:
        main()
    except urllib.error.URLError as exc:
        raise SystemExit(
            f"无法连接 {BASE_URL}，请先启动服务（uvicorn app.main:app 或 docker compose up）。\n{exc}"
        )

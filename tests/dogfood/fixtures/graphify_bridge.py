from __future__ import annotations

import json
import sys


def _terms(text: str) -> set[str]:
    normalized = "".join(ch.lower() if ch.isalnum() else " " for ch in text)
    return {token for token in normalized.split() if token}


def main() -> int:
    payload = json.loads(sys.stdin.read())
    query_terms = _terms(payload["query"])
    scored: list[tuple[int, str]] = []
    for event in payload["events"]:
        score = len(query_terms & _terms(event["content"]))
        if score > 0:
            scored.append((score, event["event_id"]))
    scored.sort(key=lambda item: (-item[0], item[1]))
    print(json.dumps({"event_ids": [event_id for _, event_id in scored[: payload["max_items"]]]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

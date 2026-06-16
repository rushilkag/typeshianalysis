#!/usr/bin/env python3
"""Classify normalized group-chat turns into public sentiment categories.

The script reads OPENAI_API_KEY from the environment. It never writes the key.
It publishes aggregate scores plus short example quotes to public/data.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, time as datetime_time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

from generate_data import (  # noqa: E402
    apple_ns_from_datetime,
    build_conversation_turns,
    connect_readonly,
    fetch_messages,
    find_matching_chats,
    iso_local,
    load_contacts,
    sender_initials,
)


CATEGORIES = {
    "racist": {
        "label": "Most racist",
        "description": "Race-targeted slurs, stereotypes, race-based insults, or edgy race jokes.",
    },
    "pickMe": {
        "label": "Most pick me",
        "description": "Approval-seeking, performative self-deprecation, or trying to be chosen/validated.",
    },
    "selfInsert": {
        "label": "Most self insert",
        "description": "Turns an unrelated topic back to themselves or their own experience.",
    },
    "brainrot": {
        "label": "Biggest brainrot",
        "description": "Chaotic, incoherent, terminally online, absurd, or aggressively unserious messages.",
    },
    "vulnerable": {
        "label": "Most vulnerable",
        "description": "Emotionally open, insecure, sad, sincere, anxious, or personally exposed messages.",
    },
    "glazer": {
        "label": "Biggest glazer",
        "description": "Excessive praise, defending, hyping, or over-complimenting someone.",
    },
}
MIN_TURNS_FOR_RANKING = 25


def clean_text(value: str) -> str:
    return " ".join((value or "").replace("\ufffc", " ").split())


def turn_id(sender_id: str, timestamp: str, text: str) -> str:
    import hashlib

    return hashlib.sha1(f"{sender_id}|{timestamp}|{text}".encode("utf-8")).hexdigest()[:16]


def load_cache(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_cache(path: Path, cache: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def call_openai(api_key: str, model: str, batch: list[dict], retries: int = 4) -> list[dict]:
    category_lines = "\n".join(
        f"- {key}: {value['description']}" for key, value in CATEGORIES.items()
    )
    payload = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "You classify group chat turns. Return strict JSON only. "
                    "For each turn, independently mark each category as 0 or 1. "
                    "Use the provided text only. Be conservative for severe labels. "
                    "If a category is 1, include a short exact quote from the turn as evidence."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "categories": category_lines,
                        "required_schema": {
                            "items": [
                                {
                                    "id": "turn id",
                                    "labels": {key: 0 for key in CATEGORIES},
                                    "quotes": {key: "short quote when label is 1" for key in CATEGORIES},
                                }
                            ]
                        },
                        "turns": batch,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    }

    request = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                data = json.loads(response.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            return parsed.get("items", [])
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code in {401, 403}:
                raise RuntimeError(
                    f"OpenAI authentication failed ({exc.code}). "
                    "Check that OPENAI_API_KEY is your real key, not the placeholder text. "
                    f"Response: {body[:500]}"
                ) from exc
            if attempt == retries - 1:
                raise RuntimeError(f"OpenAI classification failed after {retries} attempts: {exc} {body[:500]}") from exc
            time.sleep(2**attempt)
        except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError) as exc:
            if attempt == retries - 1:
                raise RuntimeError(f"OpenAI classification failed after {retries} attempts: {exc}") from exc
            time.sleep(2**attempt)

    return []


def build_turns(args: argparse.Namespace) -> tuple[list[dict], dict[str, dict]]:
    generated_at = datetime.now().astimezone()
    start_date = generated_at.date() - timedelta(days=args.days - 1)
    window_start = datetime.combine(start_date, datetime_time.min).astimezone()
    cutoff_ns = apple_ns_from_datetime(window_start)

    contacts = load_contacts(args.address_book_dir)
    conn = connect_readonly(args.messages_db)
    try:
        chat_rows = find_matching_chats(conn, args.group)
        if not chat_rows:
            raise SystemExit(f"No Messages chats matched group name {args.group!r}.")
        messages = fetch_messages(conn, args.group, cutoff_ns, contacts, False)
    finally:
        conn.close()

    sender_profiles: dict[str, dict] = {}
    for message in messages:
        sender_profiles.setdefault(
            message.sender_key,
            {
                "id": message.sender_key,
                "label": message.sender_label,
                "detail": message.sender_detail,
                "initials": sender_initials(message.sender_label),
            },
        )

    turns = []
    for turn in build_conversation_turns(messages, args.turn_gap_seconds):
        text = clean_text(turn.text)
        if len(text) < args.min_chars:
            continue
        timestamp = iso_local(turn.date)
        turns.append(
            {
                "id": turn_id(turn.sender_key, timestamp, text),
                "senderId": turn.sender_key,
                "timestamp": timestamp,
                "date": turn.date.astimezone().date().isoformat(),
                "text": text[: args.max_chars],
            }
        )

    return turns, sender_profiles


def summarize(turns: list[dict], sender_profiles: dict[str, dict], cache: dict, generated_at: datetime) -> dict:
    sender_totals: Counter[str] = Counter(turn["senderId"] for turn in turns)
    category_counts: dict[str, Counter] = {category: Counter() for category in CATEGORIES}
    category_examples: dict[str, dict[str, list[dict]]] = {
        category: defaultdict(list) for category in CATEGORIES
    }

    for turn in turns:
        result = cache.get(turn["id"])
        if not result:
            continue
        labels = result.get("labels", {})
        quotes = result.get("quotes", {})
        sender_id = turn["senderId"]
        for category in CATEGORIES:
            if int(labels.get(category, 0) or 0):
                category_counts[category][sender_id] += 1
                if len(category_examples[category][sender_id]) < 3:
                    quote = clean_text(str(quotes.get(category) or turn["text"]))
                    category_examples[category][sender_id].append(
                        {
                            "quote": quote[:220],
                            "date": turn["date"],
                        }
                    )

    rankings = []
    for category, meta in CATEGORIES.items():
        rows = []
        for sender_id, total_turns in sender_totals.items():
            count = category_counts[category][sender_id]
            if total_turns < MIN_TURNS_FOR_RANKING:
                continue
            rate = round((count / total_turns) * 100, 1) if total_turns else 0
            profile = sender_profiles.get(sender_id, {"id": sender_id, "label": "Participant", "initials": "PA"})
            rows.append(
                {
                    "id": sender_id,
                    "label": profile["label"],
                    "initials": profile.get("initials", "PA"),
                    "count": count,
                    "turns": total_turns,
                    "rate": rate,
                    "examples": list(category_examples[category][sender_id]),
                }
            )
        rows.sort(key=lambda item: (-item["rate"], -item["count"], item["label"].lower()))
        rankings.append(
            {
                "id": category,
                "label": meta["label"],
                "description": meta["description"],
                "rows": rows[:8],
            }
        )

    return {
        "generatedAt": iso_local(generated_at),
        "method": "OpenAI chat-completions JSON classifier over 30-second normalized text turns",
        "minimumTurnsForRanking": MIN_TURNS_FOR_RANKING,
        "classifiedTurns": sum(1 for turn in turns if turn["id"] in cache),
        "totalTurnsConsidered": len(turns),
        "rankings": rankings,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Classify public sentiment categories for the dashboard.")
    parser.add_argument("--group", default="type shi")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--default-window-days", type=int, default=14)
    parser.add_argument("--turn-gap-seconds", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=35)
    parser.add_argument("--model", default="gpt-4o-mini")
    parser.add_argument("--min-chars", type=int, default=3)
    parser.add_argument("--max-chars", type=int, default=900)
    parser.add_argument("--limit", type=int, default=0, help="Classify only this many uncached turns; 0 means all.")
    parser.add_argument("--workers", type=int, default=3, help="Number of parallel API workers.")
    parser.add_argument("--messages-db", type=Path, default=Path.home() / "Library/Messages/chat.db")
    parser.add_argument(
        "--address-book-dir",
        type=Path,
        default=Path.home() / "Library/Application Support/AddressBook",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "public/data/sentiment-cache.local.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "public/data/sentiments.json",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("Set OPENAI_API_KEY in your shell before running this script.")
    if api_key.strip().lower() in {"your_key", "your-key", "sk-your-key"} or not api_key.startswith("sk-"):
        raise SystemExit('OPENAI_API_KEY must be your actual OpenAI key, not "your_key".')

    turns, sender_profiles = build_turns(args)
    cache = load_cache(args.cache)
    uncached = [turn for turn in turns if turn["id"] not in cache]
    if args.limit:
        uncached = uncached[: args.limit]

    cache_lock = threading.Lock()
    completed = 0

    def process_batch(batch: list[dict]) -> int:
        api_batch = [{"id": turn["id"], "text": turn["text"]} for turn in batch]
        results = call_openai(api_key, args.model, api_batch)
        by_id = {item.get("id"): item for item in results if item.get("id")}
        entries = {}
        for turn in batch:
            item = by_id.get(turn["id"], {})
            entries[turn["id"]] = {
                "labels": {key: int(item.get("labels", {}).get(key, 0) or 0) for key in CATEGORIES},
                "quotes": {key: str(item.get("quotes", {}).get(key, ""))[:220] for key in CATEGORIES},
            }
        with cache_lock:
            cache.update(entries)
            save_cache(args.cache, cache)
        return len(batch)

    batches = [uncached[i : i + args.batch_size] for i in range(0, len(uncached), args.batch_size)]
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(process_batch, batch): batch for batch in batches}
        for future in as_completed(futures):
            completed += future.result()
            print(f"Classified {completed}/{len(uncached)} uncached turns")

    summary = summarize(turns, sender_profiles, cache, datetime.now().astimezone())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()

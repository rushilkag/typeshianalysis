#!/usr/bin/env python3
"""Analyze luke-specific patterns in the past N days:
  1. Messages luke sent with no response within 5 min (algorithmic)
  2. Times someone replied but ignored luke's content (AI)
  3. World cup ragebait moments (AI)

Requires OPENAI_API_KEY for AI sections.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

from generate_data import (
    apple_ns_from_datetime,
    build_conversation_turns,
    connect_readonly,
    fetch_messages,
    find_matching_chats,
    load_contacts,
    load_contact_overrides,
)

LUKE_NAME = "luke"
NO_RESPONSE_GAP = timedelta(minutes=5)
CONTEXT_WINDOW = 4  # turns of context around each event for AI

WC_TERMS = re.compile(
    r"\b(world\s*cup|worldcup|copa\s*america|fifa|soccer|futbol|football|penalty|"
    r"pkc|pk|goal|offside|var|halftime|half\s*time|match|tournament|group\s*stage|"
    r"knockout|quarterfinal|semifinal|final|usa|usmnt|canada|mexico|brazil|argentina|"
    r"france|england|germany|spain|portugal|morocco|japan|korea|croatia|netherlands)\b",
    re.IGNORECASE,
)

DISMISS_RE = re.compile(
    r"\b(stfu|shut\s*up|nobody\s*asked|no\s*one\s*asked|who\s*asked|"
    r"stop\s+talking|stop\s+larping|plug\s*it|we\s*don'?t\s*care|"
    r"bro\s+stop|bro\s+chill|bro\s+calm|can\s+u\s+chill|can\s+you\s+chill|"
    r"nah\s+bro|chill\s+bro|relax\s+bro|cap+ing|stop\s+cap)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# OpenAI helper
# ---------------------------------------------------------------------------

def call_openai(api_key: str, prompt_system: str, prompt_user: str, retries: int = 4) -> dict:
    payload = {
        "model": "gpt-4o-mini",
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": prompt_system},
            {"role": "user", "content": prompt_user},
        ],
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=90) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return json.loads(data["choices"][0]["message"]["content"])
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code in {401, 403}:
                raise RuntimeError(f"OpenAI auth failed: {body[:300]}") from exc
            if attempt == retries - 1:
                raise RuntimeError(f"OpenAI failed after {retries} attempts: {exc} {body[:300]}") from exc
            time.sleep(2 ** attempt)
        except Exception as exc:
            if attempt == retries - 1:
                raise RuntimeError(f"OpenAI failed after {retries} attempts: {exc}") from exc
            time.sleep(2 ** attempt)
    return {}


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_turns(args: argparse.Namespace):
    contacts = load_contacts(args.address_book_dir)
    contacts.update(load_contact_overrides(args.contact_overrides))

    conn = connect_readonly(args.messages_db)
    try:
        if not find_matching_chats(conn, args.group):
            raise SystemExit(f"No Messages chats matched {args.group!r}.")
        messages = fetch_messages(conn, args.group, args.cutoff_ns, contacts, False)
    finally:
        conn.close()

    sender_labels = {m.sender_key: m.sender_label for m in messages}
    turns = list(build_conversation_turns(messages, 30))
    luke_keys = {k for k, v in sender_labels.items() if v.lower() == LUKE_NAME}
    return turns, sender_labels, luke_keys


# ---------------------------------------------------------------------------
# Analysis 1: No response (algorithmic)
# ---------------------------------------------------------------------------

def analyze_no_response(turns, sender_labels, luke_keys):
    results = []
    for i, t in enumerate(turns):
        if t.sender_key not in luke_keys:
            continue
        text = (t.text or "").strip()
        if not text or len(text) < 3:
            continue

        # Find next turn from someone else
        next_other = None
        for j in range(i + 1, min(i + 6, len(turns))):
            if turns[j].sender_key not in luke_keys:
                next_other = turns[j]
                break

        gap = (next_other.date - t.date) if next_other else None

        # Count as no-response if gap > 5 min or no next message at all
        if next_other is None or (gap and gap > NO_RESPONSE_GAP):
            results.append({
                "timestamp": t.date.strftime("%m/%d %H:%M"),
                "luke_said": text[:120],
                "gap_minutes": int(gap.total_seconds() // 60) if gap else None,
                "next_sender": sender_labels.get(next_other.sender_key, "?") if next_other else None,
                "next_said": (next_other.text or "")[:80] if next_other else None,
            })
    return results


# ---------------------------------------------------------------------------
# Analysis 2: Dismissals (keyword, directed at luke context)
# ---------------------------------------------------------------------------

def analyze_dismissals(turns, sender_labels, luke_keys):
    results = []
    for i, t in enumerate(turns):
        if t.sender_key in luke_keys:
            continue
        if not DISMISS_RE.search(t.text or ""):
            continue
        prev = [turns[j] for j in range(max(0, i - 4), i)]
        if not any(p.sender_key in luke_keys for p in prev):
            continue
        results.append({
            "timestamp": t.date.strftime("%m/%d %H:%M"),
            "sender": sender_labels.get(t.sender_key, "?"),
            "said": (t.text or "")[:120],
        })
    return results


# ---------------------------------------------------------------------------
# Analysis 3: World cup ragebait + ignored replies (AI, parallel)
# ---------------------------------------------------------------------------

def build_wc_snippets(turns, sender_labels, luke_keys):
    """Find world cup turns that involve luke and return context snippets."""
    snippets = []
    for i, t in enumerate(turns):
        text = t.text or ""
        if not WC_TERMS.search(text):
            continue
        # Include if luke spoke within 3 turns before or after
        window = range(max(0, i - 3), min(len(turns), i + 4))
        if not any(turns[j].sender_key in luke_keys for j in window):
            continue
        ctx_turns = [turns[j] for j in range(max(0, i - CONTEXT_WINDOW), min(len(turns), i + CONTEXT_WINDOW + 1))]
        ctx_text = "\n".join(
            f"[{t2.date.strftime('%H:%M')}] {sender_labels.get(t2.sender_key, '?')}: {(t2.text or '').strip()[:200]}"
            for t2 in ctx_turns
        )
        snippets.append({"index": i, "context": ctx_text})
    # Deduplicate overlapping windows
    deduped = []
    last_i = -999
    for s in snippets:
        if s["index"] - last_i > CONTEXT_WINDOW:
            deduped.append(s)
            last_i = s["index"]
    return deduped


def build_ignored_snippets(turns, sender_labels, luke_keys):
    """Find turns where luke got a reply but was clearly ignored/topic-switched."""
    snippets = []
    for i, t in enumerate(turns):
        if t.sender_key not in luke_keys:
            continue
        text = (t.text or "").strip()
        if len(text) < 10:
            continue
        # Must have a reply within 5 min but NOT a long silence
        next_turns = []
        for j in range(i + 1, min(i + 5, len(turns))):
            nt = turns[j]
            if nt.sender_key in luke_keys:
                break
            if nt.date - t.date > NO_RESPONSE_GAP:
                break
            next_turns.append(nt)
        if not next_turns:
            continue
        ctx_turns = [turns[j] for j in range(max(0, i - 2), min(len(turns), i + 5))]
        ctx_text = "\n".join(
            f"[{t2.date.strftime('%H:%M')}] {sender_labels.get(t2.sender_key, '?')}: {(t2.text or '').strip()[:200]}"
            for t2 in ctx_turns
        )
        snippets.append({"index": i, "context": ctx_text})
    return snippets


SYSTEM_WC = (
    "You analyze group chat logs to detect world cup ragebait targeting a person named luke. "
    "Return JSON only. For each snippet determine: "
    "ragebaited (1 if others are clearly baiting/trolling luke about the world cup/soccer and he reacts), "
    "luke_upset (1 if luke seems frustrated, ranting, or heated), "
    "evidence (short quote showing the ragebait or reaction, or empty string). "
    "Schema: {\"results\": [{\"index\": int, \"ragebaited\": 0|1, \"luke_upset\": 0|1, \"evidence\": str}]}"
)

SYSTEM_IGNORED = (
    "You analyze group chat logs to detect when luke says something and others reply but completely ignore what he said "
    "(topic switch, dismissal, or nobody acknowledges his point). "
    "Return JSON only. "
    "Schema: {\"results\": [{\"index\": int, \"ignored\": 0|1, \"evidence\": str}]}"
)


def classify_snippets_parallel(api_key: str, snippets: list[dict], system_prompt: str, workers: int, label: str) -> list[dict]:
    if not snippets:
        return []

    results_by_index: dict[int, dict] = {}
    lock = threading.Lock()
    completed = 0

    BATCH = 5  # snippets per call

    def process(batch):
        user_content = json.dumps({"snippets": batch}, ensure_ascii=False)
        result = call_openai(api_key, system_prompt, user_content)
        return result.get("results", [])

    batches = [snippets[i: i + BATCH] for i in range(0, len(snippets), BATCH)]

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(process, b): b for b in batches}
        for future in as_completed(futures):
            items = future.result()
            with lock:
                for item in items:
                    results_by_index[item.get("index", -1)] = item
                completed += len(futures[future])
            print(f"  {label}: {min(completed, len(snippets))}/{len(snippets)} snippets classified")

    return [results_by_index[s["index"]] for s in snippets if s["index"] in results_by_index]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", default="type shi")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--messages-db", type=Path, default=Path.home() / "Library/Messages/chat.db")
    parser.add_argument("--address-book-dir", type=Path, default=Path.home() / "Library/Application Support/AddressBook")
    parser.add_argument("--contact-overrides", type=Path, default=Path(__file__).resolve().parents[1] / "config/contacts.local.json")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "public/data/luke_analysis.json")
    args = parser.parse_args()
    cutoff = datetime.now().astimezone() - timedelta(days=args.days - 1)
    args.cutoff_ns = apple_ns_from_datetime(cutoff)
    return args


def main() -> None:
    args = parse_args()
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key or not api_key.startswith("sk-"):
        raise SystemExit("Set OPENAI_API_KEY before running this script.")

    print(f"Loading messages ({args.days}d)...")
    turns, sender_labels, luke_keys = load_turns(args)
    print(f"  {len(turns)} total turns, luke keys: {luke_keys}")

    # --- Algorithmic ---
    print("\n[1/3] No-response analysis (5-min threshold)...")
    no_response = analyze_no_response(turns, sender_labels, luke_keys)
    print(f"  {len(no_response)} instances")

    print("\n[2/3] Dismissal keyword scan...")
    dismissals = analyze_dismissals(turns, sender_labels, luke_keys)
    print(f"  {len(dismissals)} instances")

    # --- AI parallel ---
    print("\n[3a/3] Building world cup snippets...")
    wc_snippets = build_wc_snippets(turns, sender_labels, luke_keys)
    print(f"  {len(wc_snippets)} snippets to classify")

    print("\n[3b/3] Building ignored-reply snippets...")
    ignored_snippets = build_ignored_snippets(turns, sender_labels, luke_keys)
    print(f"  {len(ignored_snippets)} snippets to classify")

    print(f"\nRunning AI classification with {args.workers} parallel workers...")

    # Track nonlocal completed inside function — use mutable wrapper
    wc_results = []
    ignored_results = []
    wc_done = threading.Event()
    ig_done = threading.Event()

    def run_wc():
        nonlocal wc_results
        wc_results = classify_snippets_parallel(api_key, wc_snippets, SYSTEM_WC, args.workers, "WC ragebait")
        wc_done.set()

    def run_ig():
        nonlocal ignored_results
        ignored_results = classify_snippets_parallel(api_key, ignored_snippets, SYSTEM_IGNORED, args.workers, "Ignored replies")
        ig_done.set()

    t1 = threading.Thread(target=run_wc)
    t2 = threading.Thread(target=run_ig)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    ragebait_hits = [r for r in wc_results if r.get("ragebaited") or r.get("luke_upset")]
    ignored_hits = [r for r in ignored_results if r.get("ignored")]

    output = {
        "generatedAt": datetime.now().astimezone().isoformat(),
        "days": args.days,
        "summary": {
            "noResponseCount": len(no_response),
            "dismissalCount": len(dismissals),
            "worldCupRagebaitCount": len(ragebait_hits),
            "ignoredRepliesCount": len(ignored_hits),
        },
        "noResponse": no_response,
        "dismissals": dismissals,
        "worldCupRagebait": [
            {**wc_snippets[i], **r}
            for i, r in enumerate(wc_results)
            if r.get("ragebaited") or r.get("luke_upset")
        ] if wc_snippets else [],
        "ignoredReplies": [
            {**ignored_snippets[i], **r}
            for i, r in enumerate(ignored_results)
            if r.get("ignored")
        ] if ignored_snippets else [],
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\n=== RESULTS ===")
    print(f"No response (5-min gap):    {len(no_response)}")
    print(f"Dismissals:                 {len(dismissals)}")
    print(f"World cup ragebait moments: {len(ragebait_hits)}")
    print(f"Ignored replies:            {len(ignored_hits)}")
    print(f"\nWrote {args.output}")


if __name__ == "__main__":
    main()

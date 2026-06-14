#!/usr/bin/env python3
"""Generate aggregate dashboard data from the local macOS Messages database.

The output intentionally avoids message bodies and raw phone numbers. It stores
counts, timestamps, masked sender details, and contact names where Contacts can
resolve them locally.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Iterable


APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
REACTION_TYPES = {
    2000: "love",
    2001: "like",
    2002: "dislike",
    2003: "laugh",
    2004: "emphasize",
    2005: "question",
    2006: "emoji",
}
VIBE_LEXICON = {
    "glazer": [
        "ate",
        "beautiful",
        "best",
        "big w",
        "congrats",
        "elite",
        "fire",
        "goat",
        "goated",
        "good shit",
        "great",
        "him",
        "insane",
        "king",
        "legend",
        "love",
        "nice",
        "proud",
        "queen",
        "real",
        "valid",
        "w",
    ],
    "hater": [
        "annoying",
        "awful",
        "bad",
        "bum",
        "clown",
        "corny",
        "cringe",
        "dumb",
        "fraud",
        "hate",
        "l",
        "mid",
        "sold",
        "stupid",
        "terrible",
        "trash",
        "washed",
        "weird",
    ],
    "pickMe": [
        "am i the only",
        "i am different",
        "i guess i am just",
        "im different",
        "nobody understands",
        "not like other",
        "only one who",
        "pick me",
    ],
    "laugh": [
        "haha",
        "lmao",
        "lmfao",
        "lol",
    ],
}


def normalize_name(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def normalize_phone(value: str | None) -> list[str]:
    digits = re.sub(r"\D+", "", value or "")
    if not digits:
        return []

    variants = {digits}
    if len(digits) == 10:
        variants.add("1" + digits)
    if len(digits) == 11 and digits.startswith("1"):
        variants.add(digits[1:])
    return sorted(variants)


def clean_name(*parts: str | None) -> str:
    joined = " ".join(part.strip() for part in parts if part and part.strip())
    return re.sub(r"\s+", " ", joined).strip()


def contact_display_name(row: sqlite3.Row) -> str | None:
    nickname = clean_name(row["ZNICKNAME"])
    if nickname:
        return nickname

    full_name = clean_name(row["ZFIRSTNAME"], row["ZLASTNAME"])
    if full_name:
        return full_name

    return clean_name(row["ZNAME"]) or clean_name(row["ZORGANIZATION"]) or None


def mask_identifier(value: str | None) -> str:
    if not value:
        return "Unknown"

    if "@" in value:
        name, _, domain = value.partition("@")
        return f"{name[:2]}...@{domain}" if domain else "Email"

    digits = re.sub(r"\D+", "", value)
    if len(digits) >= 4:
        return f"***-{digits[-4:]}"
    return "Unknown"


def stable_id(value: str | None) -> str:
    digest = hashlib.sha256((value or "unknown").encode("utf-8")).hexdigest()[:12]
    return "p_" + "".join(chr(ord("a") + int(char, 16)) for char in digest)


def normalize_text(value: str | None) -> str:
    text = (value or "").lower()
    text = re.sub(r"[^a-z0-9']+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def term_count(normalized_text: str, normalized_term: str) -> int:
    if not normalized_text or not normalized_term:
        return 0
    return len(re.findall(rf"(?<![a-z0-9]){re.escape(normalized_term)}(?![a-z0-9])", normalized_text))


def score_terms(text: str | None, terms: Iterable[str]) -> int:
    normalized = normalize_text(text)
    score = sum(term_count(normalized, normalize_text(term)) for term in terms)
    if text:
        score += text.count("💀")
        score += text.count("😭")
    return score


def analyze_vibes(text: str | None) -> dict[str, int]:
    return {bucket: score_terms(text, terms) for bucket, terms in VIBE_LEXICON.items()}


def load_slur_lexicon(path: Path | None) -> tuple[dict[str, list[str]], bool]:
    if not path:
        return {}, False
    if not path.exists():
        return {}, False

    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        data = {"configured": data}
    if not isinstance(data, dict):
        raise ValueError("Slur lexicon must be a JSON object of category -> terms.")

    lexicon: dict[str, list[str]] = {}
    for category, terms in data.items():
        if not isinstance(category, str) or not isinstance(terms, list):
            continue
        normalized_terms = sorted({normalize_text(str(term)) for term in terms if normalize_text(str(term))})
        if normalized_terms:
            lexicon[category] = normalized_terms
    return lexicon, bool(lexicon)


def analyze_slurs(text: str | None, lexicon: dict[str, list[str]]) -> Counter[str]:
    normalized = normalize_text(text)
    counts: Counter[str] = Counter()
    for category, terms in lexicon.items():
        count = sum(term_count(normalized, term) for term in terms)
        if count:
            counts[category] += count
    return counts


def associated_target_guid(value: str | None) -> str | None:
    if not value:
        return None
    if "/" in value:
        return value.rsplit("/", 1)[-1]
    if ":" in value:
        return value.rsplit(":", 1)[-1]
    return value


def safe_preview(text: str | None, limit: int = 120) -> str | None:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    if not cleaned:
        return None
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def apple_ns_from_datetime(value: datetime) -> int:
    return int((value.astimezone(timezone.utc) - APPLE_EPOCH).total_seconds() * 1_000_000_000)


def datetime_from_apple_ns(value: int) -> datetime:
    return APPLE_EPOCH + timedelta(seconds=value / 1_000_000_000)


def connect_readonly(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.create_function("norm_name", 1, normalize_name)
    return conn


def contacts_db_paths(address_book_dir: Path) -> list[Path]:
    paths = [address_book_dir / "AddressBook-v22.abcddb"]
    paths.extend(sorted((address_book_dir / "Sources").glob("*/AddressBook-v22.abcddb")))
    return [path for path in paths if path.exists()]


def load_contacts(address_book_dir: Path) -> dict[str, str]:
    contacts: dict[str, str] = {}

    for db_path in contacts_db_paths(address_book_dir):
        try:
            conn = connect_readonly(db_path)
        except sqlite3.Error:
            continue

        try:
            phone_rows = conn.execute(
                """
                select
                  r.ZFIRSTNAME, r.ZLASTNAME, r.ZNICKNAME, r.ZNAME, r.ZORGANIZATION,
                  p.ZFULLNUMBER
                from ZABCDPHONENUMBER p
                join ZABCDRECORD r on r.Z_PK = coalesce(nullif(p.ZOWNER, 0), p.Z22_OWNER)
                where p.ZFULLNUMBER is not null
                """
            ).fetchall()

            for row in phone_rows:
                name = contact_display_name(row)
                if not name:
                    continue
                for key in normalize_phone(row["ZFULLNUMBER"]):
                    contacts.setdefault(key, name)

            email_rows = conn.execute(
                """
                select
                  r.ZFIRSTNAME, r.ZLASTNAME, r.ZNICKNAME, r.ZNAME, r.ZORGANIZATION,
                  e.ZADDRESS, e.ZADDRESSNORMALIZED
                from ZABCDEMAILADDRESS e
                join ZABCDRECORD r on r.Z_PK = coalesce(nullif(e.ZOWNER, 0), e.Z22_OWNER)
                where coalesce(e.ZADDRESSNORMALIZED, e.ZADDRESS) is not null
                """
            ).fetchall()

            for row in email_rows:
                name = contact_display_name(row)
                if not name:
                    continue
                for value in (row["ZADDRESSNORMALIZED"], row["ZADDRESS"]):
                    if value:
                        contacts.setdefault(value.lower(), name)
        except sqlite3.Error:
            pass
        finally:
            conn.close()

    return contacts


def resolve_contact(handle: str | None, contacts: dict[str, str]) -> tuple[str, str]:
    if not handle:
        return "Unknown", "Unknown"

    if "@" in handle:
        name = contacts.get(handle.lower())
        return name or mask_identifier(handle), mask_identifier(handle)

    for key in normalize_phone(handle):
        name = contacts.get(key)
        if name:
            return name, mask_identifier(handle)

    return mask_identifier(handle), mask_identifier(handle)


def sender_identity(is_from_me: bool, handle: str | None, contacts: dict[str, str]) -> tuple[str, str, str]:
    if is_from_me:
        return "me", "Rushil", "This Mac"

    label, detail = resolve_contact(handle, contacts)
    return stable_id(handle), label, detail


@dataclass(frozen=True)
class MessageRow:
    rowid: int
    guid: str
    sender_key: str
    sender_label: str
    sender_detail: str
    date: datetime
    text: str
    text_length: int
    has_attachment: bool
    is_from_me: bool


def find_matching_chats(conn: sqlite3.Connection, group_name: str) -> list[sqlite3.Row]:
    normalized = normalize_name(group_name)
    return conn.execute(
        """
        with title_matches as (
          select distinct cmj.chat_id
          from chat_message_join cmj
          join message m on m.ROWID = cmj.message_id
          where norm_name(m.group_title) = :group_name
        )
        select distinct c.ROWID, c.display_name, c.chat_identifier, c.service_name, c.style
        from chat c
        where norm_name(c.display_name) = :group_name
           or c.ROWID in (select chat_id from title_matches)
        order by c.ROWID
        """,
        {"group_name": normalized},
    ).fetchall()


def fetch_messages(
    conn: sqlite3.Connection,
    group_name: str,
    cutoff_ns: int,
    contacts: dict[str, str],
) -> list[MessageRow]:
    rows = conn.execute(
        """
        with matching_chats as (
          select distinct c.ROWID
          from chat c
          where norm_name(c.display_name) = :group_name
             or c.ROWID in (
               select distinct cmj.chat_id
               from chat_message_join cmj
               join message title_message on title_message.ROWID = cmj.message_id
               where norm_name(title_message.group_title) = :group_name
             )
        ),
        distinct_messages as (
          select distinct
            m.ROWID,
            m.guid,
            m.is_from_me,
            m.handle_id,
            m.date,
            coalesce(m.text, '') as text,
            length(coalesce(m.text, '')) as text_length,
            m.cache_has_attachments
          from matching_chats c
          join chat_message_join cmj on cmj.chat_id = c.ROWID
          join message m on m.ROWID = cmj.message_id
          where m.date >= :cutoff_ns
            and m.item_type = 0
            and m.is_system_message = 0
            and coalesce(m.associated_message_type, 0) = 0
        )
        select dm.*, h.id as handle
        from distinct_messages dm
        left join handle h on h.ROWID = dm.handle_id
        order by dm.date asc, dm.ROWID asc
        """,
        {"group_name": normalize_name(group_name), "cutoff_ns": cutoff_ns},
    ).fetchall()

    messages: list[MessageRow] = []
    for row in rows:
        is_from_me = bool(row["is_from_me"])
        handle = row["handle"]
        sender_key, sender_label, sender_detail = sender_identity(is_from_me, handle, contacts)

        messages.append(
            MessageRow(
                rowid=row["ROWID"],
                guid=row["guid"],
                sender_key=sender_key,
                sender_label=sender_label,
                sender_detail=sender_detail,
                date=datetime_from_apple_ns(row["date"]),
                text=row["text"] or "",
                text_length=row["text_length"] or 0,
                has_attachment=bool(row["cache_has_attachments"]),
                is_from_me=is_from_me,
            )
        )

    return messages


def build_mention_aliases(senders: Iterable[dict]) -> list[tuple[str, str]]:
    candidates: dict[str, set[str]] = defaultdict(set)
    for sender in senders:
        label = sender["label"]
        if label.startswith("Participant ") or label in {"Unknown", "participant"}:
            continue

        full = normalize_text(label)
        tokens = full.split()
        aliases = set()
        if len(full) >= 3:
            aliases.add(full)
        if tokens and len(tokens[0]) >= 3:
            aliases.add(tokens[0])

        for alias in aliases:
            candidates[alias].add(sender["id"])

    aliases: list[tuple[str, str]] = []
    for alias, sender_ids in candidates.items():
        if len(sender_ids) == 1:
            aliases.append((alias, next(iter(sender_ids))))

    return sorted(aliases, key=lambda item: (-len(item[0]), item[0]))


def analyze_mentions(text: str | None, sender_id: str, aliases: Iterable[tuple[str, str]]) -> Counter[str]:
    normalized = normalize_text(text)
    counts: Counter[str] = Counter()
    for alias, target_id in aliases:
        if target_id == sender_id:
            continue
        count = term_count(normalized, alias)
        if count:
            counts[target_id] = max(counts[target_id], count)
    return counts


def fetch_reaction_messages(
    conn: sqlite3.Connection,
    group_name: str,
    cutoff_ns: int,
    contacts: dict[str, str],
    share_safe: bool,
    include_message_previews: bool,
    reaction_limit: int,
) -> list[dict]:
    params = {"group_name": normalize_name(group_name), "cutoff_ns": cutoff_ns}
    target_rows = conn.execute(
        """
        with matching_chats as (
          select distinct c.ROWID
          from chat c
          where norm_name(c.display_name) = :group_name
             or c.ROWID in (
               select distinct cmj.chat_id
               from chat_message_join cmj
               join message title_message on title_message.ROWID = cmj.message_id
               where norm_name(title_message.group_title) = :group_name
             )
        )
        select distinct
          m.guid,
          m.is_from_me,
          m.handle_id,
          m.date,
          coalesce(m.text, '') as text,
          h.id as handle
        from matching_chats c
        join chat_message_join cmj on cmj.chat_id = c.ROWID
        join message m on m.ROWID = cmj.message_id
        left join handle h on h.ROWID = m.handle_id
        where m.date >= :cutoff_ns
          and m.item_type = 0
          and m.is_system_message = 0
          and coalesce(m.associated_message_type, 0) = 0
        """,
        params,
    ).fetchall()

    targets: dict[str, dict] = {}
    for row in target_rows:
        sender_id, label, detail = sender_identity(bool(row["is_from_me"]), row["handle"], contacts)

        if share_safe:
            if re.fullmatch(r"\*{3}-\d{4}", label):
                label = "Participant"
            detail = "Rushil" if sender_id == "me" else "participant"

        targets[row["guid"]] = {
            "id": stable_id(row["guid"]),
            "authorId": sender_id,
            "authorLabel": label,
            "authorDetail": detail,
            "date": row["date"],
            "timestamp": iso_local(datetime_from_apple_ns(row["date"])),
            "preview": safe_preview(row["text"]) if include_message_previews else None,
            "reactionCount": 0,
            "reactionTypes": Counter(),
        }

    reaction_rows = conn.execute(
        """
        with matching_chats as (
          select distinct c.ROWID
          from chat c
          where norm_name(c.display_name) = :group_name
             or c.ROWID in (
               select distinct cmj.chat_id
               from chat_message_join cmj
               join message title_message on title_message.ROWID = cmj.message_id
               where norm_name(title_message.group_title) = :group_name
             )
        )
        select distinct
          m.ROWID,
          m.associated_message_type,
          m.associated_message_guid
        from matching_chats c
        join chat_message_join cmj on cmj.chat_id = c.ROWID
        join message m on m.ROWID = cmj.message_id
        where m.date >= :cutoff_ns
          and m.associated_message_type in (2000, 2001, 2002, 2003, 2004, 2005, 2006)
          and m.associated_message_guid is not null
        """,
        params,
    ).fetchall()

    for row in reaction_rows:
        target_guid = associated_target_guid(row["associated_message_guid"])
        if not target_guid or target_guid not in targets:
            continue

        target = targets[target_guid]
        reaction_name = REACTION_TYPES.get(row["associated_message_type"], "other")
        target["reactionCount"] += 1
        target["reactionTypes"][reaction_name] += 1

    reactions = [target for target in targets.values() if target["reactionCount"]]
    for reaction in reactions:
        reaction["date"] = datetime_from_apple_ns(reaction["date"]).astimezone().date().isoformat()
        reaction["reactionTypes"] = dict(
            sorted(reaction["reactionTypes"].items(), key=lambda item: (-item[1], item[0]))
        )
        if reaction["preview"] is None:
            reaction.pop("preview")

    return sorted(reactions, key=lambda item: (-item["reactionCount"], item["timestamp"]))[:reaction_limit]


def iso_local(value: datetime) -> str:
    return value.astimezone().replace(microsecond=0).isoformat()


def sender_initials(label: str) -> str:
    if label == "Rushil":
        return "RU"

    words = re.findall(r"[A-Za-z0-9]+", label)
    if not words:
        return "??"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[-1][0]).upper()


def empty_day_bucket() -> dict:
    return {
        "count": 0,
        "attachmentMessages": 0,
        "textLengthSum": 0,
        "textMessageCount": 0,
        "bySender": Counter(),
        "byHour": Counter(),
        "vibesBySender": defaultdict(Counter),
        "mentions": Counter(),
        "slurBySender": defaultdict(Counter),
        "slurByCategory": Counter(),
    }


def serialize_nested_counters(value: dict[str, Counter]) -> dict:
    return {
        key: {inner_key: inner_value for inner_key, inner_value in sorted(counter.items()) if inner_value}
        for key, counter in sorted(value.items())
        if sum(counter.values())
    }


def build_summary(
    group_name: str,
    days: int,
    default_window_days: int,
    messages: list[MessageRow],
    chat_rows: Iterable[sqlite3.Row],
    reaction_messages: list[dict],
    slur_lexicon: dict[str, list[str]],
    slur_lexicon_configured: bool,
    generated_at: datetime,
    window_start: datetime,
    share_safe: bool,
) -> dict:
    sender_profiles: dict[str, dict] = {}
    sender_totals: Counter[str] = Counter()
    daily_buckets: dict[str, dict] = defaultdict(empty_day_bucket)
    attachment_count = 0
    text_length_sum = 0
    text_message_count = 0

    for message in messages:
        sender = sender_profiles.setdefault(
            message.sender_key,
            {
                "id": message.sender_key,
                "label": message.sender_label,
                "detail": message.sender_detail,
                "initials": sender_initials(message.sender_label),
                "count": 0,
                "firstMessageAt": iso_local(message.date),
                "lastMessageAt": iso_local(message.date),
            },
        )
        sender_totals[message.sender_key] += 1
        sender["lastMessageAt"] = iso_local(message.date)

    total = len(messages)
    senders = sorted(
        sender_profiles.values(),
        key=lambda item: (-sender_totals[item["id"]], item["label"].lower()),
    )
    for index, sender in enumerate(senders, start=1):
        sender["totalCount"] = sender_totals[sender["id"]]
        sender["rank"] = index
        sender["share"] = round((sender["totalCount"] / total) * 100, 1) if total else 0
        if share_safe:
            if re.fullmatch(r"\*{3}-\d{4}", sender["label"]):
                sender["label"] = f"Participant {index}"
                sender["initials"] = sender_initials(sender["label"])
            sender["detail"] = "Rushil" if sender["id"] == "me" else "participant"

    mention_aliases = build_mention_aliases(senders)

    for message in messages:
        local_dt = message.date.astimezone()
        day_key = local_dt.date().isoformat()
        bucket = daily_buckets[day_key]
        bucket["count"] += 1
        bucket["bySender"][message.sender_key] += 1
        bucket["byHour"][local_dt.hour] += 1
        attachment_count += int(message.has_attachment)
        bucket["attachmentMessages"] += int(message.has_attachment)
        if message.text_length:
            text_length_sum += message.text_length
            text_message_count += 1
            bucket["textLengthSum"] += message.text_length
            bucket["textMessageCount"] += 1

        for vibe, score in analyze_vibes(message.text).items():
            if score:
                bucket["vibesBySender"][message.sender_key][vibe] += score

        for target_id, count in analyze_mentions(message.text, message.sender_key, mention_aliases).items():
            bucket["mentions"][f"{message.sender_key}>{target_id}"] += count

        slur_counts = analyze_slurs(message.text, slur_lexicon)
        for category, count in slur_counts.items():
            bucket["slurBySender"][message.sender_key][category] += count
            bucket["slurByCategory"][category] += count

    daily = []
    day_cursor = window_start.astimezone().date()
    last_day = generated_at.astimezone().date()
    while day_cursor <= last_day:
        day = day_cursor.isoformat()
        bucket = daily_buckets[day]
        daily.append(
            {
                "date": day,
                "count": bucket["count"],
                "attachmentMessages": bucket["attachmentMessages"],
                "textLengthSum": bucket["textLengthSum"],
                "textMessageCount": bucket["textMessageCount"],
                "bySender": {
                    sender_id: count
                    for sender_id, count in sorted(
                        bucket["bySender"].items(),
                        key=lambda item: (-item[1], item[0]),
                    )
                },
                "byHour": [bucket["byHour"][hour] for hour in range(24)],
                "vibesBySender": serialize_nested_counters(bucket["vibesBySender"]),
                "mentions": [
                    {"from": edge.split(">", 1)[0], "to": edge.split(">", 1)[1], "count": count}
                    for edge, count in sorted(bucket["mentions"].items(), key=lambda item: (-item[1], item[0]))
                ],
                "slurBySender": serialize_nested_counters(bucket["slurBySender"]),
                "slurByCategory": dict(sorted(bucket["slurByCategory"].items())),
            }
        )
        day_cursor += timedelta(days=1)

    avg_text_length = round(text_length_sum / text_message_count, 1) if text_message_count else 0

    return {
        "groupName": group_name,
        "generatedAt": iso_local(generated_at),
        "windowStart": iso_local(window_start),
        "windowEnd": iso_local(generated_at),
        "days": len(daily),
        "maxWindowDays": len(daily),
        "defaultWindowDays": min(default_window_days, len(daily)),
        "windowOptions": [option for option in [7, 14, 30, 90, 180, 365] if option <= len(daily)],
        "totalMessages": total,
        "participantCount": len(senders),
        "attachmentMessages": attachment_count,
        "averagePerDay": round(total / len(daily), 1) if daily else total,
        "averageTextLength": avg_text_length,
        "matchedChats": []
        if share_safe
        else [
            {
                "rowid": row["ROWID"],
                "displayName": row["display_name"],
                "service": row["service_name"],
                "style": row["style"],
            }
            for row in chat_rows
        ],
        "senders": senders,
        "daily": daily,
        "reactionMessages": reaction_messages,
        "analysis": {
            "method": "deterministic lexicon and name matching",
            "previewsPublished": any("preview" in item for item in reaction_messages),
            "slurLexiconConfigured": slur_lexicon_configured,
            "slurCategories": sorted(slur_lexicon),
            "vibeBuckets": sorted(VIBE_LEXICON),
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate aggregate data for the type shi message dashboard.")
    parser.add_argument("--group", default="type shi", help="Messages group display name to analyze.")
    parser.add_argument("--days", type=int, default=365, help="Number of trailing calendar days to include.")
    parser.add_argument(
        "--default-window-days",
        type=int,
        default=14,
        help="Initial dashboard window within the generated range.",
    )
    parser.add_argument(
        "--messages-db",
        type=Path,
        default=Path.home() / "Library/Messages/chat.db",
        help="Path to macOS Messages chat.db.",
    )
    parser.add_argument(
        "--address-book-dir",
        type=Path,
        default=Path.home() / "Library/Application Support/AddressBook",
        help="Path to macOS AddressBook directory.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "public/data/summary.json",
        help="JSON output path.",
    )
    parser.add_argument(
        "--share-safe",
        action="store_true",
        help="Remove phone-tail details and chat row metadata from the generated JSON.",
    )
    parser.add_argument(
        "--slur-lexicon",
        type=Path,
        default=None,
        help="Optional local JSON file of category -> terms. Terms are counted locally and never published.",
    )
    parser.add_argument(
        "--include-message-previews",
        action="store_true",
        help="Include short previews for highest-reaction messages. Off by default for hosted builds.",
    )
    parser.add_argument(
        "--reaction-limit",
        type=int,
        default=500,
        help="Maximum reacted-message summaries to write to JSON.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.days < 1:
        raise SystemExit("--days must be at least 1.")
    if args.default_window_days < 1:
        raise SystemExit("--default-window-days must be at least 1.")
    if args.reaction_limit < 1:
        raise SystemExit("--reaction-limit must be at least 1.")

    generated_at = datetime.now().astimezone()
    start_date = generated_at.date() - timedelta(days=args.days - 1)
    window_start = datetime.combine(start_date, time.min).astimezone()
    cutoff_ns = apple_ns_from_datetime(window_start)

    slur_lexicon, slur_lexicon_configured = load_slur_lexicon(args.slur_lexicon)
    contacts = load_contacts(args.address_book_dir)
    conn = connect_readonly(args.messages_db)
    try:
        chat_rows = find_matching_chats(conn, args.group)
        if not chat_rows:
            raise SystemExit(f"No Messages chats matched group name {args.group!r}.")

        messages = fetch_messages(conn, args.group, cutoff_ns, contacts)
        reaction_messages = fetch_reaction_messages(
            conn,
            args.group,
            cutoff_ns,
            contacts,
            args.share_safe,
            args.include_message_previews,
            args.reaction_limit,
        )
    finally:
        conn.close()

    summary = build_summary(
        args.group,
        args.days,
        args.default_window_days,
        messages,
        chat_rows,
        reaction_messages,
        slur_lexicon,
        slur_lexicon_configured,
        generated_at,
        window_start,
        args.share_safe,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    print(f"Wrote {args.output}")
    print(
        f"Matched {len(chat_rows)} chat rows and {summary['totalMessages']} messages "
        f"across the last {summary['days']} calendar days."
    )


if __name__ == "__main__":
    main()

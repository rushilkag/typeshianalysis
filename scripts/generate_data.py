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
import shutil
import sqlite3
import subprocess
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
IMAGE_EXTENSIONS = {".avif", ".gif", ".heic", ".heif", ".jpeg", ".jpg", ".png", ".webp"}
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
        "am i cooked",
        "i am different",
        "i always",
        "i fear",
        "i fear i",
        "i guess i am just",
        "i might be cooked",
        "im different",
        "im cooked",
        "im just",
        "i'm cooked",
        "i'm different",
        "i'm just",
        "it is always me",
        "its always me",
        "literally me",
        "me when",
        "nobody understands",
        "no one cares",
        "not like other",
        "not me",
        "only one who",
        "pick me",
        "this is so me",
        "why am i",
    ],
    "selfInsert": [
        "as a",
        "for me",
        "i can",
        "i could",
        "i feel",
        "i feel like",
        "i think",
        "i would",
        "literally me",
        "me personally",
        "me when",
        "my take",
        "personally",
        "this is me",
    ],
    "laugh": [
        "haha",
        "lmao",
        "lmfao",
        "lol",
    ],
}
WORD_WATCH_TERMS = {
    "n-word": ["nigga"],
    "black": ["black"],
}
SWEAR_TERMS = {
    "ass": ["ass", "asses", "asshole", "assholes", "ass"],
    "bastard": ["bastard", "bastards"],
    "bitch": ["bitch", "bitches", "bitchy", "bih", "bihs"],
    "cock": ["cock", "cocks"],
    "cunt": ["cunt", "cunts"],
    "damn": ["damn", "dammit", "damned"],
    "dick": ["dick", "dicks", "dickhead", "dickheads"],
    "fuck": ["fuck", "fucked", "fucker", "fuckers", "fucking", "fucks", "fck", "fuk", "fucc"],
    "hell": ["hell"],
    "hoe": ["hoe", "hoes"],
    "nigga": ["nigga", "niggas"],
    "nigger": ["nigger", "niggers"],
    "piss": ["piss", "pissed"],
    "pussy": ["pussy", "pussies"],
    "shit": ["shit", "shits", "shitty", "bullshit", "shiit", "shitt"],
    "slut": ["slut", "sluts", "slutty"],
    "whore": ["whore", "whores"],
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


def analyze_word_watch(text: str | None) -> Counter[str]:
    normalized = normalize_text(text)
    counts: Counter[str] = Counter()
    for category, terms in WORD_WATCH_TERMS.items():
        count = sum(term_count(normalized, normalize_text(term)) for term in terms)
        if count:
            counts[category] += count
    return counts


def analyze_swears(text: str | None) -> Counter[str]:
    normalized = normalize_text(text)
    counts: Counter[str] = Counter()
    for category, terms in SWEAR_TERMS.items():
        count = sum(term_count(normalized, normalize_text(term)) for term in terms)
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


def attachment_source_path(filename: str | None) -> Path | None:
    if not filename:
        return None

    path = Path(filename).expanduser()
    if not path.is_absolute():
        path = Path.home() / "Library/Messages" / path
    return path if path.exists() else None


def media_extension(path: Path, mime_type: str | None, uti: str | None) -> str | None:
    suffix = path.suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return suffix

    value = f"{mime_type or ''} {uti or ''}".lower()
    if "png" in value:
        return ".png"
    if "gif" in value:
        return ".gif"
    if "webp" in value:
        return ".webp"
    if "heic" in value or "heif" in value:
        return ".heic"
    if "jpeg" in value or "jpg" in value or "image/" in value:
        return ".jpg"
    return None


def write_web_image(source_path: Path, target_path: Path) -> bool:
    try:
        subprocess.run(
            ["sips", "-s", "format", "jpeg", "-Z", "900", str(source_path), "--out", str(target_path)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return target_path.exists()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def image_has_visible_content(path: Path) -> bool:
    try:
        from PIL import Image

        with Image.open(path) as image:
            image = image.convert("RGB")
            image.thumbnail((32, 32))
            extrema = image.getextrema()
    except Exception:
        return True

    return any(high - low > 8 for low, high in extrema)


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


def resolve_contact(
    handle: str | None,
    contacts: dict[str, str],
    include_contact_identifiers: bool = False,
) -> tuple[str, str]:
    if not handle:
        return "Unknown", "Unknown"

    if include_contact_identifiers:
        if "@" in handle:
            return contacts.get(handle.lower()) or handle, handle
        for key in normalize_phone(handle):
            name = contacts.get(key)
            if name:
                return name, handle
        return handle, handle

    if "@" in handle:
        name = contacts.get(handle.lower())
        return name or mask_identifier(handle), mask_identifier(handle)

    for key in normalize_phone(handle):
        name = contacts.get(key)
        if name:
            return name, mask_identifier(handle)

    return mask_identifier(handle), mask_identifier(handle)


def sender_identity(
    is_from_me: bool,
    handle: str | None,
    contacts: dict[str, str],
    include_contact_identifiers: bool = False,
) -> tuple[str, str, str]:
    if is_from_me:
        return "me", "Rushil", "This Mac"

    label, detail = resolve_contact(handle, contacts, include_contact_identifiers)
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


@dataclass(frozen=True)
class ConversationTurn:
    sender_key: str
    date: datetime
    text: str
    text_length: int
    has_attachment: bool
    message_count: int


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
    include_contact_identifiers: bool,
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
        sender_key, sender_label, sender_detail = sender_identity(
            is_from_me, handle, contacts, include_contact_identifiers
        )

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


def build_conversation_turns(messages: list[MessageRow], gap_seconds: int) -> list[ConversationTurn]:
    turns: list[ConversationTurn] = []
    current_sender: str | None = None
    current_date: datetime | None = None
    current_last_date: datetime | None = None
    current_texts: list[str] = []
    current_text_length = 0
    current_has_attachment = False
    current_message_count = 0

    def flush() -> None:
        nonlocal current_sender
        nonlocal current_date
        nonlocal current_last_date
        nonlocal current_texts
        nonlocal current_text_length
        nonlocal current_has_attachment
        nonlocal current_message_count

        if current_sender is None or current_date is None:
            return

        turns.append(
            ConversationTurn(
                sender_key=current_sender,
                date=current_date,
                text=" ".join(text for text in current_texts if text).strip(),
                text_length=current_text_length,
                has_attachment=current_has_attachment,
                message_count=current_message_count,
            )
        )
        current_sender = None
        current_date = None
        current_last_date = None
        current_texts = []
        current_text_length = 0
        current_has_attachment = False
        current_message_count = 0

    for message in messages:
        same_sender = current_sender == message.sender_key
        gap = (message.date - current_last_date).total_seconds() if current_last_date else None
        same_turn = same_sender and gap is not None and gap <= gap_seconds

        if not same_turn:
            flush()
            current_sender = message.sender_key
            current_date = message.date

        current_last_date = message.date
        current_texts.append(message.text)
        current_text_length += message.text_length
        current_has_attachment = current_has_attachment or message.has_attachment
        current_message_count += 1

    flush()
    return turns


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
    include_contact_identifiers: bool,
    include_media_files: bool,
    media_output_dir: Path,
    reaction_limit: int,
) -> tuple[list[dict], dict[str, Counter], dict[str, Counter]]:
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
          m.ROWID,
          m.guid,
          m.is_from_me,
          m.handle_id,
          m.date,
          coalesce(m.text, '') as text,
          (
            select count(*)
            from message_attachment_join maj
            where maj.message_id = m.ROWID
          ) as attachment_count,
          (
            select group_concat(distinct coalesce(a.mime_type, a.uti, 'attachment'))
            from message_attachment_join maj
            join attachment a on a.ROWID = maj.attachment_id
            where maj.message_id = m.ROWID
          ) as attachment_types,
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
        sender_id, label, detail = sender_identity(
            bool(row["is_from_me"]), row["handle"], contacts, include_contact_identifiers
        )

        if share_safe:
            if re.fullmatch(r"\*{3}-\d{4}", label):
                label = "Participant"
            detail = "Rushil" if sender_id == "me" else "participant"

        targets[row["guid"]] = {
            "messageRowId": row["ROWID"],
            "id": stable_id(row["guid"]),
            "authorId": sender_id,
            "authorLabel": label,
            "authorDetail": detail,
            "date": row["date"],
            "timestamp": iso_local(datetime_from_apple_ns(row["date"])),
            "preview": safe_preview(row["text"]) if include_message_previews else None,
            "attachmentCount": row["attachment_count"] or 0,
            "attachmentTypes": sorted(
                {item.strip() for item in (row["attachment_types"] or "").split(",") if item.strip()}
            ),
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
          m.associated_message_guid,
          m.is_from_me,
          m.date,
          h.id as handle
        from matching_chats c
        join chat_message_join cmj on cmj.chat_id = c.ROWID
        join message m on m.ROWID = cmj.message_id
        left join handle h on h.ROWID = m.handle_id
        where m.date >= :cutoff_ns
          and m.associated_message_type in (2000, 2001, 2002, 2003, 2004, 2005, 2006)
          and m.associated_message_guid is not null
        """,
        params,
    ).fetchall()

    reaction_daily_by_sender: dict[str, Counter] = defaultdict(Counter)
    reaction_daily_by_author: dict[str, Counter] = defaultdict(Counter)
    for row in reaction_rows:
        sender_id, _, _ = sender_identity(bool(row["is_from_me"]), row["handle"], contacts, include_contact_identifiers)
        day_key = datetime_from_apple_ns(row["date"]).astimezone().date().isoformat()
        reaction_daily_by_sender[day_key][sender_id] += 1

        target_guid = associated_target_guid(row["associated_message_guid"])
        if not target_guid or target_guid not in targets:
            continue

        target = targets[target_guid]
        author_day_key = datetime_from_apple_ns(target["date"]).astimezone().date().isoformat()
        reaction_daily_by_author[author_day_key][target["authorId"]] += 1
        reaction_name = REACTION_TYPES.get(row["associated_message_type"], "other")
        target["reactionCount"] += 1
        target["reactionTypes"][reaction_name] += 1

    reactions = sorted(
        [target for target in targets.values() if target["reactionCount"]],
        key=lambda item: (-item["reactionCount"], item["timestamp"]),
    )[:reaction_limit]

    if include_media_files and reactions:
        row_id_to_reaction = {reaction["messageRowId"]: reaction for reaction in reactions}
        placeholders = ",".join("?" for _ in row_id_to_reaction)
        attach_rows = conn.execute(
            f"""
            select
              maj.message_id,
              a.ROWID as attachment_id,
              a.filename,
              a.mime_type,
              a.uti
            from message_attachment_join maj
            join attachment a on a.ROWID = maj.attachment_id
            where maj.message_id in ({placeholders})
            order by maj.message_id, a.ROWID
            """,
            list(row_id_to_reaction),
        ).fetchall()
        if media_output_dir.exists():
            shutil.rmtree(media_output_dir)
        media_output_dir.mkdir(parents=True, exist_ok=True)
        for row in attach_rows:
            source_path = attachment_source_path(row["filename"])
            if not source_path:
                continue
            extension = media_extension(source_path, row["mime_type"], row["uti"])
            if not extension:
                continue

            digest = hashlib.sha1(f"{row['message_id']}:{row['attachment_id']}".encode()).hexdigest()[:12]
            target_name = f"reaction-{digest}.jpg"
            target_path = media_output_dir / target_name
            if not target_path.exists():
                if not write_web_image(source_path, target_path):
                    target_name = f"reaction-{digest}{extension}"
                    target_path = media_output_dir / target_name
                    shutil.copy2(source_path, target_path)
            if not image_has_visible_content(target_path):
                target_path.unlink(missing_ok=True)
                continue

            reaction = row_id_to_reaction[row["message_id"]]
            reaction.setdefault("media", []).append(
                {
                    "src": f"./data/reaction-media/{target_name}",
                    "type": row["mime_type"] or row["uti"] or "image",
                }
            )

    for reaction in reactions:
        reaction["date"] = datetime_from_apple_ns(reaction["date"]).astimezone().date().isoformat()
        reaction["reactionTypes"] = dict(
            sorted(reaction["reactionTypes"].items(), key=lambda item: (-item[1], item[0]))
        )
        reaction.pop("messageRowId")
        if reaction["preview"] is None:
            reaction.pop("preview")
        if not reaction["attachmentCount"]:
            reaction.pop("attachmentCount")
        if not reaction["attachmentTypes"]:
            reaction.pop("attachmentTypes")

    return (
        reactions,
        reaction_daily_by_sender,
        reaction_daily_by_author,
    )


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
        "turnCount": 0,
        "attachmentMessages": 0,
        "attachmentTurns": 0,
        "textLengthSum": 0,
        "textMessageCount": 0,
        "bySender": Counter(),
        "turnsBySender": Counter(),
        "byHour": Counter(),
        "turnsByHour": Counter(),
        "vibesBySender": defaultdict(Counter),
        "reactionBySender": Counter(),
        "reactionByAuthor": Counter(),
        "mentions": Counter(),
        "slurBySender": defaultdict(Counter),
        "slurByCategory": Counter(),
        "wordWatchBySender": defaultdict(Counter),
        "wordWatchByTerm": Counter(),
        "swearBySender": defaultdict(Counter),
        "swearByTerm": Counter(),
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
    turns: list[ConversationTurn],
    turn_gap_seconds: int,
    chat_rows: Iterable[sqlite3.Row],
    reaction_messages: list[dict],
    reaction_daily_by_sender: dict[str, Counter],
    reaction_daily_by_author: dict[str, Counter],
    slur_lexicon: dict[str, list[str]],
    slur_lexicon_configured: bool,
    generated_at: datetime,
    window_start: datetime,
    share_safe: bool,
) -> dict:
    sender_profiles: dict[str, dict] = {}
    sender_totals: Counter[str] = Counter()
    sender_turn_totals: Counter[str] = Counter()
    daily_buckets: dict[str, dict] = defaultdict(empty_day_bucket)
    attachment_count = 0
    attachment_turn_count = 0
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

    for turn in turns:
        sender_turn_totals[turn.sender_key] += 1

    total = len(messages)
    total_turns = len(turns)
    senders = sorted(
        sender_profiles.values(),
        key=lambda item: (-sender_turn_totals[item["id"]], -sender_totals[item["id"]], item["label"].lower()),
    )
    for index, sender in enumerate(senders, start=1):
        sender["totalCount"] = sender_totals[sender["id"]]
        sender["turnCount"] = sender_turn_totals[sender["id"]]
        sender["burstReductionPercent"] = (
            round(((sender["totalCount"] - sender["turnCount"]) / sender["totalCount"]) * 100, 1)
            if sender["totalCount"]
            else 0
        )
        sender["rank"] = index
        sender["share"] = round((sender["turnCount"] / total_turns) * 100, 1) if total_turns else 0
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
                bucket["vibesBySender"][message.sender_key][vibe] += 1

        for target_id, count in analyze_mentions(message.text, message.sender_key, mention_aliases).items():
            bucket["mentions"][f"{message.sender_key}>{target_id}"] += count

        slur_counts = analyze_slurs(message.text, slur_lexicon)
        for category, count in slur_counts.items():
            bucket["slurBySender"][message.sender_key][category] += count
            bucket["slurByCategory"][category] += count

        word_watch_counts = analyze_word_watch(message.text)
        for category, count in word_watch_counts.items():
            bucket["wordWatchBySender"][message.sender_key][category] += count
            bucket["wordWatchByTerm"][category] += count

        swear_counts = analyze_swears(message.text)
        for category, count in swear_counts.items():
            bucket["swearBySender"][message.sender_key][category] += count
            bucket["swearByTerm"][category] += count

    for turn in turns:
        local_dt = turn.date.astimezone()
        day_key = local_dt.date().isoformat()
        bucket = daily_buckets[day_key]
        bucket["turnCount"] += 1
        bucket["turnsBySender"][turn.sender_key] += 1
        bucket["turnsByHour"][local_dt.hour] += 1
        attachment_turn_count += int(turn.has_attachment)
        bucket["attachmentTurns"] += int(turn.has_attachment)

    daily = []
    day_cursor = window_start.astimezone().date()
    last_day = generated_at.astimezone().date()
    while day_cursor <= last_day:
        day = day_cursor.isoformat()
        bucket = daily_buckets[day]
        for sender_id, count in reaction_daily_by_sender.get(day, {}).items():
            bucket["reactionBySender"][sender_id] += count
        for sender_id, count in reaction_daily_by_author.get(day, {}).items():
            bucket["reactionByAuthor"][sender_id] += count
        daily.append(
            {
                "date": day,
                "count": bucket["count"],
                "turnCount": bucket["turnCount"],
                "attachmentMessages": bucket["attachmentMessages"],
                "attachmentTurns": bucket["attachmentTurns"],
                "textLengthSum": bucket["textLengthSum"],
                "textMessageCount": bucket["textMessageCount"],
                "bySender": {
                    sender_id: count
                    for sender_id, count in sorted(
                        bucket["bySender"].items(),
                        key=lambda item: (-item[1], item[0]),
                    )
                },
                "turnsBySender": {
                    sender_id: count
                    for sender_id, count in sorted(
                        bucket["turnsBySender"].items(),
                        key=lambda item: (-item[1], item[0]),
                    )
                },
                "byHour": [bucket["byHour"][hour] for hour in range(24)],
                "turnsByHour": [bucket["turnsByHour"][hour] for hour in range(24)],
                "vibesBySender": serialize_nested_counters(bucket["vibesBySender"]),
                "reactionBySender": dict(
                    sorted(bucket["reactionBySender"].items(), key=lambda item: (-item[1], item[0]))
                ),
                "reactionByAuthor": dict(
                    sorted(bucket["reactionByAuthor"].items(), key=lambda item: (-item[1], item[0]))
                ),
                "mentions": [
                    {"from": edge.split(">", 1)[0], "to": edge.split(">", 1)[1], "count": count}
                    for edge, count in sorted(bucket["mentions"].items(), key=lambda item: (-item[1], item[0]))
                ],
                "slurBySender": serialize_nested_counters(bucket["slurBySender"]),
                "slurByCategory": dict(sorted(bucket["slurByCategory"].items())),
                "wordWatchBySender": serialize_nested_counters(bucket["wordWatchBySender"]),
                "wordWatchByTerm": dict(sorted(bucket["wordWatchByTerm"].items())),
                "swearBySender": serialize_nested_counters(bucket["swearBySender"]),
                "swearByTerm": dict(sorted(bucket["swearByTerm"].items())),
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
        "totalTurns": total_turns,
        "turnGapSeconds": turn_gap_seconds,
        "burstReductionPercent": round(((total - total_turns) / total) * 100, 1) if total else 0,
        "participantCount": len(senders),
        "attachmentMessages": attachment_count,
        "attachmentTurns": attachment_turn_count,
        "averagePerDay": round(total_turns / len(daily), 1) if daily else total_turns,
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
            "method": "30-second same-sender conversation-turn normalization; deterministic lexicon and name matching; awards use signal-message percentage per sender",
            "previewsPublished": any("preview" in item for item in reaction_messages),
            "slurLexiconConfigured": slur_lexicon_configured,
            "slurCategories": sorted(slur_lexicon),
            "wordWatchTerms": sorted(WORD_WATCH_TERMS),
            "swearTerms": sorted(SWEAR_TERMS),
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
        "--include-media-files",
        action="store_true",
        help="Copy image attachments for highest-reaction messages into the public output tree.",
    )
    parser.add_argument(
        "--media-output-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "public/data/reaction-media",
        help="Output directory for copied reaction media files.",
    )
    parser.add_argument(
        "--include-contact-identifiers",
        action="store_true",
        help="Unsafe local-only mode: include raw contact handles/phone numbers in sender details.",
    )
    parser.add_argument(
        "--reaction-limit",
        type=int,
        default=500,
        help="Maximum reacted-message summaries to write to JSON.",
    )
    parser.add_argument(
        "--turn-gap-seconds",
        type=int,
        default=30,
        help="Collapse consecutive same-sender messages within this many seconds into one conversation turn.",
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
    if args.turn_gap_seconds < 0:
        raise SystemExit("--turn-gap-seconds must be zero or greater.")
    if args.include_contact_identifiers and args.output.name == "summary.json":
        raise SystemExit("--include-contact-identifiers must write to a non-public output path.")

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

        messages = fetch_messages(conn, args.group, cutoff_ns, contacts, args.include_contact_identifiers)
        reaction_messages, reaction_daily_by_sender, reaction_daily_by_author = fetch_reaction_messages(
            conn,
            args.group,
            cutoff_ns,
            contacts,
            args.share_safe,
            args.include_message_previews,
            args.include_contact_identifiers,
            args.include_media_files,
            args.media_output_dir,
            args.reaction_limit,
        )
    finally:
        conn.close()

    turns = build_conversation_turns(messages, args.turn_gap_seconds)
    summary = build_summary(
        args.group,
        args.days,
        args.default_window_days,
        messages,
        turns,
        args.turn_gap_seconds,
        chat_rows,
        reaction_messages,
        reaction_daily_by_sender,
        reaction_daily_by_author,
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
        f"Matched {len(chat_rows)} chat rows, {summary['totalMessages']} message bubbles, "
        f"and {summary['totalTurns']} normalized turns "
        f"across the last {summary['days']} calendar days."
    )


if __name__ == "__main__":
    main()

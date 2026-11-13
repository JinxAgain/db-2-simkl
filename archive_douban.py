import os
import time
import json
import re
import sys
import hmac
import hashlib
import base64
import urllib.parse
import xml.etree.ElementTree as ET
import argparse
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
import requests
from bs4 import BeautifulSoup

# Fix encoding issues on Windows console/logs
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

CONFIG_FILE = "config.json"
HISTORY_FILE = "sync_history_archive.json"
ARCHIVE_FILE = "douban_archive.jsonl"
CST = timezone(timedelta(hours=8))
MAX_HISTORY_ENTRIES = 500

# Frodo Mobile API configuration (movie/TV backfill)
FRODO_API_KEY = "0dad551ec0f84ed02907ff5c42e8ec70"
FRODO_HMAC_SECRET = b"bf7dddc7c9cfe6f7"
FRODO_BASE_URL = "https://frodo.douban.com"
FRODO_USER_AGENT = (
    "api-client/1 com.douban.frodo/7.22.0.beta9(231) Android/23 "
    "product/Mate40 vendor/HUAWEI model/Mate40 brand/HUAWEI "
    "rom/android network/wifi platform/AndroidPad"
)
FRODO_PAGE_SIZE = 50  # Frodo API hard ceiling per request
FRODO_PAGE_DELAY = 1.5
FRODO_STATUS_MAP = (
    ("done", "看过"),
    ("doing", "在看"),
    ("mark", "想看"),
)

def load_config():
    """Load configuration from config.json."""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {
        "douban_id": "",
        "sync_delay_seconds": 2,
        "frodo_backfill_enabled": True,
        "frodo_backfill_limit": 50,
    }

def load_history(history_file=HISTORY_FILE, douban_id=None):
    """Load processed event keys from history file, filtering out other accounts if douban_id is set."""
    if os.path.exists(history_file):
        try:
            with open(history_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    if douban_id:
                        data = [k for k in data if f"/people/{douban_id}/" in k]
                    return data, set(data)
        except Exception:
            pass
    return [], set()

def save_history(history_list, history_file=HISTORY_FILE, max_entries=MAX_HISTORY_ENTRIES):
    """Save processed event keys to history file with sliding window pruning."""
    trimmed = history_list[-max_entries:] if len(history_list) > max_entries else history_list
    with open(history_file, 'w', encoding='utf-8') as f:
        json.dump(trimmed, f, indent=2, ensure_ascii=False)

def load_existing_archive_keys(jsonl_file=ARCHIVE_FILE):
    """
    Load existing (link, type, create_time) tuples from the archive JSONL
    to provide secondary deduplication.
    """
    keys = set()
    if os.path.exists(jsonl_file):
        with open(jsonl_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    link = data.get("link")
                    item_type = data.get("type")
                    create_time = data.get("create_time")
                    if link and item_type:
                        keys.add((link, item_type, create_time))
                except json.JSONDecodeError:
                    continue
    return keys

def extract_rating_from_memo(memo, default_rating=None):
    """Extract fractional or explicit integer rating (1-5 scale) from user comment text."""
    if not memo:
        return default_rating
    decimal_match = re.search(r'\b([0-5]\.[0-9])\b', memo)
    if decimal_match:
        try:
            val = float(decimal_match.group(1))
            if 0 <= val <= 5:
                return val
        except ValueError:
            pass
    score_match = re.search(r'\b([0-5])\s*(?:分|/5)\b', memo)
    if score_match:
        try:
            val = float(score_match.group(1))
            if 0 <= val <= 5:
                return val
        except ValueError:
            pass
    return default_rating

def generate_frodo_signature(api_path, timestamp):
    """Compute Frodo API HMAC-SHA1 Base64 signature over method, URL-encoded path, and timestamp."""
    encoded_path = urllib.parse.quote(api_path, safe='')
    raw_string = f"GET&{encoded_path}&{timestamp}"
    hashed = hmac.new(FRODO_HMAC_SECRET, raw_string.encode('utf-8'), hashlib.sha1)
    return base64.b64encode(hashed.digest()).decode('utf-8')

def parse_frodo_interest(item, douban_id, status_label):
    """Map a single Frodo API interest payload into the archive_douban.py record schema."""
    if not isinstance(item, dict):
        return None
    subject = item.get("subject") or {}
    subject_type = (subject.get("type") or "").strip()
    # Restrict strictly to movie and TV subjects; exclude books, music, games
    if subject_type and subject_type not in ("movie", "tv"):
        return None

    title = (subject.get("title") or "").strip()
    subject_id = str(subject.get("id") or "").strip()
    if subject_id and subject_id.isdigit():
        link = f"https://movie.douban.com/subject/{subject_id}/"
    else:
        link = (subject.get("url") or "").strip()

    if not link or not title:
        return None

    create_time = (item.get("create_time") or "").strip() or None
    interest_id = item.get("id")
    if interest_id:
        guid = f"https://www.douban.com/people/{douban_id}/interests/{interest_id}"
    else:
        guid = f"{link}#{status_label}#{create_time or ''}"

    intro = (subject.get("card_subtitle") or "").strip() or None

    douban_rating = None
    subj_rating_obj = subject.get("rating")
    if isinstance(subj_rating_obj, dict):
        subj_rating_val = subj_rating_obj.get("value")
        if subj_rating_val is not None:
            try:
                if float(subj_rating_val) > 0:
                    douban_rating = str(subj_rating_val)
            except (TypeError, ValueError):
                pass

    raw_rating = None
    user_rating_obj = item.get("rating")
    if isinstance(user_rating_obj, dict):
        val = user_rating_obj.get("value")
        if val is not None:
            try:
                num_val = float(val)
                if num_val > 0:
                    max_val = float(user_rating_obj.get("max") or 5)
                    if max_val > 5:
                        num_val = num_val * 5.0 / max_val
                    num_val = min(5.0, max(1.0, num_val))
                    raw_rating = int(num_val) if num_val.is_integer() else round(num_val, 1)
            except (TypeError, ValueError):
                pass

    memo = (item.get("comment") or "").strip() or None
    rating = extract_rating_from_memo(memo, default_rating=raw_rating)

    raw_tags = item.get("tags") or []
    tags_list = [str(t).strip() for t in raw_tags if str(t).strip()]
    tags = ",".join(tags_list) if tags_list else None

    visibility = "private" if item.get("is_private") else "public"

    return {
        "guid": guid,
        "type": status_label,
        "title": title,
        "intro": intro,
        "douban_rating": douban_rating,
        "link": link,
        "create_time": create_time,
        "my_rating": rating,
        "tags": tags,
        "comment": memo,
        "visibility": visibility,
    }

def fetch_frodo_status_paginated(douban_id, status_code, limit=50, page_delay=FRODO_PAGE_DELAY):
    """
    Fetch up to `limit` movie/TV records for a given Frodo status ('done', 'doing', 'mark').
    Automatically paginates in chunks of 50 (Frodo API server ceiling) using `start`.
    """
    status_label = dict(FRODO_STATUS_MAP).get(status_code)
    if not status_label or limit <= 0:
        return []

    clean_user = str(douban_id).strip().strip('/')
    api_path = f"/api/v2/user/{clean_user}/interests"
    url = f"{FRODO_BASE_URL}{api_path}"

    collected = []
    start = 0

    while len(collected) < limit:
        count = min(FRODO_PAGE_SIZE, limit - len(collected))
        if start > 0 and page_delay > 0:
            time.sleep(page_delay)

        data = None
        for attempt in range(1, 4):
            ts = str(int(time.time()))
            sig = generate_frodo_signature(api_path, ts)
            params = {
                "type": "movie",
                "status": status_code,
                "start": start,
                "count": count,
                "apiKey": FRODO_API_KEY,
                "_ts": ts,
                "_sig": sig,
                "os_rom": "android",
            }
            try:
                resp = requests.get(
                    url,
                    params=params,
                    headers={"User-Agent": FRODO_USER_AGENT},
                    timeout=15,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    break
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                    wait_sec = 5 * (2 ** (attempt - 1))
                    print(
                        f"Frodo API HTTP {resp.status_code} (status={status_code}, start={start}, "
                        f"attempt={attempt}/3). Retrying in {wait_sec}s..."
                    )
                    time.sleep(wait_sec)
                    continue
                print(
                    f"Frodo API request failed: HTTP {resp.status_code} "
                    f"(status={status_code}, start={start})"
                )
                return collected
            except requests.RequestException as e:
                if attempt < 3:
                    wait_sec = 5 * (2 ** (attempt - 1))
                    print(
                        f"Frodo API network error (status={status_code}, start={start}, "
                        f"attempt={attempt}/3): {e}. Retrying in {wait_sec}s..."
                    )
                    time.sleep(wait_sec)
                    continue
                print(f"Frodo API request failed after retries (status={status_code}, start={start}): {e}")
                return collected

        if not isinstance(data, dict):
            break

        interests = data.get("interests") or []
        if not interests:
            break

        for item in interests:
            parsed = parse_frodo_interest(item, clean_user, status_label)
            if parsed is not None:
                collected.append(parsed)

        total = data.get("total")
        if len(interests) < count:
            break
        if isinstance(total, int) and start + len(interests) >= total:
            break

        start += len(interests)

    return collected[:limit]

def fetch_frodo_backfill(douban_id, limit=50, page_delay=FRODO_PAGE_DELAY):
    """Fetch recent movie/TV backfill records across 'done', 'doing', and 'mark' statuses."""
    all_items = []
    for idx, (status_code, status_label) in enumerate(FRODO_STATUS_MAP):
        if idx > 0 and page_delay > 0:
            time.sleep(page_delay)
        items = fetch_frodo_status_paginated(
            douban_id=douban_id,
            status_code=status_code,
            limit=limit,
            page_delay=page_delay,
        )
        print(f"  Frodo [{status_code}/{status_label}]: fetched {len(items)} items (limit={limit}).")
        all_items.extend(items)
    return all_items

def merge_rss_and_frodo_items(rss_items, frodo_items):
    """
    Merge RSS and Frodo items, deduplicating identical events while enriching RSS items
    with Frodo metadata (intro, douban_rating, tags, visibility).
    Returns records sorted chronologically (oldest first).
    """
    merged = {}
    guid_to_key = {}

    def _merge_one(item):
        key = (item.get("link"), item.get("type"), item.get("create_time"))
        guid = item.get("guid") or ""
        target_key = guid_to_key.get(guid) if guid and guid in guid_to_key else key

        if target_key not in merged:
            merged[target_key] = dict(item)
            if guid:
                guid_to_key[guid] = target_key
            return

        existing = merged[target_key]
        for field in ("intro", "douban_rating", "tags", "create_time"):
            if not existing.get(field) and item.get(field):
                existing[field] = item[field]
        if existing.get("my_rating") is None and item.get("my_rating") is not None:
            existing["my_rating"] = item["my_rating"]
        if not existing.get("comment") and item.get("comment"):
            existing["comment"] = item["comment"]
        if existing.get("visibility") != "private" and item.get("visibility") == "private":
            existing["visibility"] = "private"

    for item in reversed(rss_items):
        _merge_one(item)
    for item in reversed(frodo_items):
        _merge_one(item)

    return sorted(
        merged.values(),
        key=lambda x: (x.get("create_time") or "", x.get("guid") or "", x.get("link") or ""),
    )

def fetch_rss(douban_id):
    """Fetch Douban user interests RSS feed."""
    url = f"https://www.douban.com/feed/people/{douban_id}/interests"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    response = requests.get(url, headers=headers, timeout=15)
    response.raise_for_status()
    return response.text

def parse_rss(xml_data):
    """
    Parse Douban interests RSS XML data and extract movie watch activities.
    Supports '看过', '想看', and '在看'.
    """
    root = ET.fromstring(xml_data)
    items = []

    for item in root.findall('.//item'):
        guid_elem = item.find('guid')
        if guid_elem is None or not guid_elem.text:
            continue
        guid = guid_elem.text.strip()

        title_elem = item.find('title')
        raw_title = title_elem.text if title_elem is not None and title_elem.text else ""

        link_elem = item.find('link')
        link = link_elem.text.strip() if link_elem is not None and link_elem.text else ""

        pubdate_elem = item.find('pubDate')
        pubdate_str = pubdate_elem.text.strip() if pubdate_elem is not None and pubdate_elem.text else ""

        description_elem = item.find('description')
        description = description_elem.text if description_elem is not None and description_elem.text else ""

        action = None
        clean_title = raw_title
        if raw_title.startswith("看过"):
            action = "看过"
            clean_title = raw_title[2:].strip()
        elif raw_title.startswith("想看"):
            action = "想看"
            clean_title = raw_title[2:].strip()
        elif raw_title.startswith("在看"):
            action = "在看"
            clean_title = raw_title[2:].strip()

        # Only process movie/tv watch activities
        if not action:
            continue

        # Check that it is a movie/subject link or not book/music/game
        if not ("movie.douban.com/subject/" in link or "/subject/" in link):
            continue

        # Convert pubDate to CST formatted string (YYYY-MM-DD HH:MM:SS)
        create_time = None
        if pubdate_str:
            try:
                dt = parsedate_to_datetime(pubdate_str)
                dt_cst = dt.astimezone(CST)
                create_time = dt_cst.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                create_time = pubdate_str

        rating = None
        memo = None

        if description:
            soup = BeautifulSoup(description, 'html.parser')
            for p in soup.find_all('p'):
                text = p.text.strip()
                if text.startswith("推荐:"):
                    rec = text.split("推荐:")[1].strip()
                    # Rating on 1-5 scale matching Douban export
                    rating_map = {"很差": 1, "较差": 2, "还行": 3, "推荐": 4, "力荐": 5}
                    if rec in rating_map:
                        rating = rating_map[rec]
                elif text.startswith("备注:"):
                    memo = text.split("备注:", 1)[1].strip()
                    rating = extract_rating_from_memo(memo, default_rating=rating)

        items.append({
            "guid": guid,
            "type": action,
            "title": clean_title,
            "intro": None,
            "douban_rating": None,
            "link": link,
            "create_time": create_time,
            "my_rating": rating,
            "tags": None,
            "comment": memo,
            "visibility": "public",
        })

    return items

def append_records_to_archive(records, jsonl_file=ARCHIVE_FILE):
    """Append new records to the target JSONL archive file."""
    with open(jsonl_file, 'a', encoding='utf-8') as f:
        for record in records:
            # We don't save the RSS-internal guid into the public archive jsonl to keep schema identical
            record_to_save = {
                "type": record["type"],
                "title": record["title"],
                "intro": record["intro"],
                "douban_rating": record["douban_rating"],
                "link": record["link"],
                "create_time": record["create_time"],
                "my_rating": record["my_rating"],
                "tags": record["tags"],
                "comment": record["comment"],
                "visibility": record["visibility"],
            }
            f.write(json.dumps(record_to_save, ensure_ascii=False) + "\n")

def main():
    parser = argparse.ArgumentParser(description="Archive latest Douban watch activities to JSONL.")
    parser.add_argument("--local-xml", type=str, help="Path to local XML file to read instead of fetching from Douban.")
    parser.add_argument("--dry-run", action="store_true", help="Simulate execution without modifying files.")
    parser.add_argument("--jsonl-file", type=str, default=ARCHIVE_FILE, help="Path to archive JSONL file.")
    parser.add_argument("--history-file", type=str, default=HISTORY_FILE, help="Path to sync history JSON file.")
    parser.add_argument(
        "--frodo-limit",
        type=int,
        default=None,
        help="Number of recent items per status (e.g. 50 or 100) to backfill via Frodo API. Set 0 to disable.",
    )
    parser.add_argument(
        "--no-frodo",
        action="store_true",
        help="Disable Frodo API backfill and use RSS only.",
    )
    args = parser.parse_args()

    config = load_config()
    douban_id = config.get("douban_id")

    if not args.local_xml and not douban_id:
        print("Error: douban_id is not set in config.json and no local XML is provided.")
        return

    frodo_limit = (
        args.frodo_limit
        if args.frodo_limit is not None
        else int(config.get("frodo_backfill_limit", 50))
    )
    if args.local_xml and args.frodo_limit is None:
        # Preserve offline local-XML behavior unless --frodo-limit is explicitly supplied
        enable_frodo = False
    else:
        enable_frodo = (
            not args.no_frodo
            and bool(config.get("frodo_backfill_enabled", True))
            and frodo_limit > 0
            and bool(douban_id)
        )

    history_list, history_set = load_history(args.history_file, douban_id=douban_id)
    existing_keys = load_existing_archive_keys(args.jsonl_file)

    rss_items = []
    if args.local_xml:
        print(f"Reading local XML file: {args.local_xml}")
        try:
            with open(args.local_xml, 'r', encoding='utf-8') as f:
                xml_data = f.read()
            rss_items = parse_rss(xml_data)
        except Exception as e:
            print(f"Failed to read local XML file: {e}")
            return
    else:
        print(f"Fetching RSS for Douban ID: {douban_id}")
        try:
            xml_data = fetch_rss(douban_id)
            rss_items = parse_rss(xml_data)
        except Exception as e:
            print(f"Failed to fetch or parse RSS: {e}")
            if not enable_frodo:
                return
            print("Continuing with Frodo API backfill despite RSS failure...")

    print(f"Found {len(rss_items)} movie watch items in RSS.")

    frodo_items = []
    if enable_frodo:
        print(f"Fetching Frodo API backfill for Douban ID {douban_id} (limit={frodo_limit} per status)...")
        frodo_items = fetch_frodo_backfill(douban_id, limit=frodo_limit)
        print(f"Fetched {len(frodo_items)} total movie/TV items from Frodo API.")

    merged_items = merge_rss_and_frodo_items(rss_items, frodo_items)
    new_records = []
    history_modified = False

    # merged_items is already sorted chronologically (oldest first)
    for item in merged_items:
        event_key = f"{item['guid']}::{item['type']}::{item.get('create_time') or ''}"
        if event_key in history_set:
            continue

        item_key = (item["link"], item["type"], item["create_time"])
        if item_key in existing_keys:
            # Already in archive JSONL, mark as seen
            history_set.add(event_key)
            history_list.append(event_key)
            history_modified = True
            continue

        print(f"New item detected: [{item['type']}] {item['title']} ({item['link']})")
        new_records.append(item)
        history_set.add(event_key)
        history_list.append(event_key)
        history_modified = True
        existing_keys.add(item_key)

    if not new_records:
        print("No new watch activities to archive.")
        if history_modified and not args.dry_run:
            save_history(history_list, args.history_file)
        return

    print(f"Appending {len(new_records)} new records to {args.jsonl_file}...")
    if args.dry_run:
        print("[DRY-RUN] Would append the following records:")
        for r in new_records:
            print(json.dumps(r, ensure_ascii=False))
    else:
        append_records_to_archive(new_records, args.jsonl_file)
        save_history(history_list, args.history_file)
        print("Successfully updated archive and history.")

if __name__ == "__main__":
    main()

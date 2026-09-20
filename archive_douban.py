import os
import time
import json
import re
import sys
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

def load_config():
    """Load configuration from config.json."""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"douban_id": "", "sync_delay_seconds": 2}

def load_history(history_file=HISTORY_FILE):
    """Load processed guid set from history file."""
    if os.path.exists(history_file):
        with open(history_file, 'r', encoding='utf-8') as f:
            return set(json.load(f))
    return set()

def save_history(history, history_file=HISTORY_FILE):
    """Save processed guid set to history file."""
    with open(history_file, 'w', encoding='utf-8') as f:
        json.dump(sorted(list(history)), f, indent=2, ensure_ascii=False)

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
                    # Extract decimal ratings (e.g. 3.5, 4.5/5)
                    decimal_match = re.search(r'\b([0-5]\.[0-9])\b', memo)
                    if decimal_match:
                        try:
                            val = float(decimal_match.group(1))
                            if 0 <= val <= 5:
                                rating = val
                        except ValueError:
                            pass
                    else:
                        score_match = re.search(r'\b([0-5])\s*(?:分|/5)\b', memo)
                        if score_match:
                            try:
                                val = float(score_match.group(1))
                                if 0 <= val <= 5:
                                    rating = val
                            except ValueError:
                                pass

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
    args = parser.parse_args()

    config = load_config()
    douban_id = config.get("douban_id")

    if not args.local_xml and not douban_id:
        print("Error: douban_id is not set in config.json and no local XML is provided.")
        return

    history = load_history(args.history_file)
    existing_keys = load_existing_archive_keys(args.jsonl_file)

    if args.local_xml:
        print(f"Reading local XML file: {args.local_xml}")
        try:
            with open(args.local_xml, 'r', encoding='utf-8') as f:
                xml_data = f.read()
            items = parse_rss(xml_data)
        except Exception as e:
            print(f"Failed to read local XML file: {e}")
            return
    else:
        print(f"Fetching RSS for Douban ID: {douban_id}")
        try:
            xml_data = fetch_rss(douban_id)
            items = parse_rss(xml_data)
        except Exception as e:
            print(f"Failed to fetch or parse RSS: {e}")
            return

    print(f"Found {len(items)} movie watch items in RSS.")
    new_records = []

    # Process oldest first to keep archive chronological
    for item in reversed(items):
        guid = item["guid"]
        if guid in history:
            continue

        item_key = (item["link"], item["type"], item["create_time"])
        if item_key in existing_keys:
            # Already in archive JSONL, mark as seen
            history.add(guid)
            continue

        print(f"New item detected: [{item['type']}] {item['title']} ({item['link']})")
        new_records.append(item)
        history.add(guid)
        existing_keys.add(item_key)

    if not new_records:
        print("No new watch activities to archive.")
        if not args.dry_run:
            save_history(history, args.history_file)
        return

    print(f"Appending {len(new_records)} new records to {args.jsonl_file}...")
    if args.dry_run:
        print("[DRY-RUN] Would append the following records:")
        for r in new_records:
            print(json.dumps(r, ensure_ascii=False))
    else:
        append_records_to_archive(new_records, args.jsonl_file)
        save_history(history, args.history_file)
        print("Successfully updated archive and history.")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Douban Movie & TV Hybrid Synchronization Engine: RSS + Frodo API 50-Item Backfill.

Author: db-2-simkl Engineering Team
License: MIT
"""
import os
import sys
import time
import json
import re
import csv
import io
import shutil
import hmac
import hashlib
import base64
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from typing import Dict, List, Optional, Tuple, Any

# Ensure UTF-8 output on Windows terminal
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

# Constants
API_KEY = "0dad551ec0f84ed02907ff5c42e8ec70"
HMAC_SECRET = b"bf7dddc7c9cfe6f7"
BASE_URL = "https://frodo.douban.com"
USER_AGENT = (
    "api-client/1 com.douban.frodo/7.22.0.beta9(231) Android/23 "
    "product/Mate40 vendor/HUAWEI model/Mate40 brand/HUAWEI "
    "rom/android network/wifi platform/AndroidPad"
)
CST = timezone(timedelta(hours=8))
STATUS_PRECEDENCE = {"看过": 3, "在看": 2, "想看": 1}

class UnifiedRecord:
    """Represents a unified movie/TV record mapped to 影视.csv."""
    def __init__(self, subject_id: str, title: str, url: str, date: str,
                 rating: str, status: str, comment: str, raw_ts: Optional[datetime] = None):
        self.subject_id = str(subject_id).strip()
        self.title = str(title).strip()
        self.url = str(url).strip()
        self.date = str(date).strip()
        self.rating = str(rating).strip()
        self.status = str(status).strip()
        self.comment = str(comment).strip()
        self.raw_ts = raw_ts

    def to_csv_row(self) -> List[str]:
        return [self.title, self.url, self.date, self.rating, self.status, self.comment]

def extract_subject_id(url: Optional[str]) -> str:
    """Extracts numeric subject ID from Douban subject or interest URL."""
    if not url or not isinstance(url, str):
        return ""
    m = re.search(r'/subject/(\d+)', url)
    return m.group(1) if m else url.strip().rstrip('/')

def generate_frodo_sig(path: str, ts: str) -> str:
    """Generates HMAC-SHA1 Base64 signature for Frodo API."""
    encoded_path = urllib.parse.quote(path, safe='')
    raw_string = f"GET&{encoded_path}&{ts}"
    hashed = hmac.new(HMAC_SECRET, raw_string.encode('utf-8'), hashlib.sha1)
    return base64.b64encode(hashed.digest()).decode('utf-8')

def rating_to_stars(rating_obj: Optional[Dict[str, Any]]) -> str:
    """Converts Frodo API rating object to Unicode star string."""
    if not rating_obj or not isinstance(rating_obj, dict):
        return ""
    val = rating_obj.get("value")
    if val is None or val == 0:
        return ""
    max_val = rating_obj.get("max", 5)
    stars = round(val) if max_val <= 5 else round(val / 2)
    stars = max(1, min(5, stars))
    return "★" * stars

def parse_rss_feed(user_id: str) -> List[UnifiedRecord]:
    """Fetches and parses the Douban public RSS feed (up to 10 items)."""
    clean_user = str(user_id).strip().strip('/')
    url = f"https://www.douban.com/feed/people/{clean_user}/interests"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            xml_text = resp.read().decode('utf-8')
    except Exception as e:
        print(f"[!] RSS fetch failed: {e}")
        return []

    records = []
    status_prefixes = [
        ("看过", "看过"),
        ("想看", "想看"),
        ("在看", "在看"),
        ("最近在看", "在看")
    ]

    for item_match in re.finditer(r'<item>([\s\S]*?)</item>', xml_text):
        block = item_match.group(1)
        title_m = re.search(r'<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>', block)
        link_m = re.search(r'<link>(.*?)</link>', block)
        pub_m = re.search(r'<pubDate>(.*?)</pubDate>', block)
        desc_m = re.search(r'<description>(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?</description>', block)

        if not title_m or not link_m:
            continue

        raw_title = title_m.group(1).strip()
        link = link_m.group(1).strip()
        subject_id = extract_subject_id(link)

        # Only process movie and TV items
        matched_status = None
        clean_title = raw_title
        for prefix, norm_status in status_prefixes:
            if raw_title.startswith(prefix):
                matched_status = norm_status
                clean_title = raw_title[len(prefix):].strip()
                break

        if not matched_status:
            continue

        # Parse CST Date
        date_str = ""
        raw_dt = None
        if pub_m:
            try:
                dt = parsedate_to_datetime(pub_m.group(1).strip())
                raw_dt = dt.astimezone(CST)
                date_str = raw_dt.strftime("%Y-%m-%d")
            except Exception:
                pass

        # Parse Rating & Comment (Fixing RSS regex bug for 备注:)
        desc_text = desc_m.group(1) if desc_m else ""
        rating_str = ""
        rate_m = re.search(r'推荐:\s*(力荐|推荐|还行|较差|很差)', desc_text)
        if rate_m:
            r_map = {"力荐": "★★★★★", "推荐": "★★★★", "还行": "★★★", "较差": "★★", "很差": "★"}
            rating_str = r_map.get(rate_m.group(1), "")

        comment_str = ""
        comm_m = re.search(r'(?:短评|备注):\s*([^<\n\r]+)', desc_text)
        if comm_m:
            comment_str = comm_m.group(1).strip()

        canonical_url = f"https://movie.douban.com/subject/{subject_id}/" if (subject_id and subject_id.isdigit()) else link
        records.append(UnifiedRecord(
            subject_id=subject_id,
            title=clean_title,
            url=canonical_url,
            date=date_str,
            rating=rating_str,
            status=matched_status,
            comment=comment_str,
            raw_ts=raw_dt
        ))
    return records

def fetch_frodo_status(user_id: str, status_code: str, count: int = 50) -> Tuple[List[UnifiedRecord], Optional[Dict[str, str]]]:
    """
    Fetches up to `count` movie/TV records for a specific status via Frodo API.
    Automatically paginates in batches of 50 (Frodo API server ceiling) using `start`.
    """
    if count <= 0:
        return [], None

    clean_user = str(user_id).strip().strip('/')
    path = f"/api/v2/user/{clean_user}/interests"
    status_map = {"done": "看过", "doing": "在看", "mark": "想看"}
    mapped_status = status_map.get(status_code, "看过")

    all_records: List[UnifiedRecord] = []
    watermark: Optional[Dict[str, str]] = None
    start = 0

    while len(all_records) < count:
        page_count = min(50, count - len(all_records))
        if start > 0:
            time.sleep(1.5)

        data = None
        for attempt in range(1, 4):
            ts = str(int(time.time()))
            sig = generate_frodo_sig(path, ts)
            params = {
                "type": "movie",
                "status": status_code,
                "start": start,
                "count": page_count,
                "apiKey": API_KEY,
                "_ts": ts,
                "_sig": sig,
                "os_rom": "android"
            }
            url = f"{BASE_URL}{path}?{urllib.parse.urlencode(params)}"
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode('utf-8'))
                    break
            except Exception as e:
                wait_sec = 5 * (2 ** (attempt - 1))
                print(f"[!] Frodo API error (attempt {attempt}/3, status={status_code}, start={start}): {e}. Retrying in {wait_sec}s...")
                time.sleep(wait_sec)

        if not isinstance(data, dict):
            break

        interests = data.get("interests", [])
        if not interests:
            break

        if watermark is None:
            first = interests[0]
            watermark = {
                "last_interest_id": str(first.get("id")),
                "last_create_time": str(first.get("create_time"))
            }

        for item in interests:
            sub = item.get("subject") or {}
            sid = str(sub.get("id", "")).strip()
            if not sid:
                continue
            title = sub.get("title", "")
            create_time = item.get("create_time", "")
            date_str = create_time[:10] if len(create_time) >= 10 else ""
            raw_dt = None
            try:
                raw_dt = datetime.strptime(create_time, "%Y-%m-%d %H:%M:%S").replace(tzinfo=CST)
            except Exception:
                pass

            rating_str = rating_to_stars(item.get("rating"))
            comment_str = item.get("comment") or ""
            canonical_url = f"https://movie.douban.com/subject/{sid}/"

            all_records.append(UnifiedRecord(
                subject_id=sid,
                title=title,
                url=canonical_url,
                date=date_str,
                rating=rating_str,
                status=mapped_status,
                comment=comment_str,
                raw_ts=raw_dt
            ))

        total = data.get("total")
        if len(interests) < page_count:
            break
        if isinstance(total, int) and start + len(interests) >= total:
            break

        start += len(interests)

    return all_records[:count], watermark

def load_existing_csv(csv_path: str) -> Dict[str, UnifiedRecord]:
    """Loads existing 影视.csv indexed by canonical subject ID, with .bak fallback."""
    records = {}
    target_path = csv_path
    if not os.path.exists(target_path):
        bak_path = f"{csv_path}.bak"
        if os.path.exists(bak_path):
            print(f"[!] Target {csv_path} not found, falling back to backup {bak_path}")
            target_path = bak_path
        else:
            return records

    with open(target_path, 'r', encoding='utf-8-sig', newline='') as f:
        reader = csv.reader(f)
        header = next(reader, None)
        for row in reader:
            if not row or len(row) < 5:
                continue
            title = row[0]
            url = row[1]
            date = row[2]
            rating = row[3]
            status = row[4]
            comment = row[5] if len(row) > 5 else ""
            sid = extract_subject_id(url)
            if sid:
                records[sid] = UnifiedRecord(
                    subject_id=sid,
                    title=title,
                    url=url,
                    date=date,
                    rating=rating,
                    status=status,
                    comment=comment
                )
    return records

def reconcile_and_merge(existing_map: Dict[str, UnifiedRecord],
                          incoming_records: List[UnifiedRecord]) -> Tuple[Dict[str, UnifiedRecord], int, int]:
    """
    Applies deduplication, status transition lifecycle, and enrichment.
    Returns (merged_map, added_count, updated_count).
    """
    added_count = 0
    updated_count = 0

    for inc in incoming_records:
        sid = inc.subject_id
        if not sid:
            continue

        if sid not in existing_map:
            existing_map[sid] = inc
            added_count += 1
            continue

        curr = existing_map[sid]
        curr_prec = STATUS_PRECEDENCE.get(curr.status, 0)
        inc_prec = STATUS_PRECEDENCE.get(inc.status, 0)

        # Status transition (e.g. 想看 -> 看过, 在看 -> 看过)
        if inc_prec > curr_prec:
            curr.status = inc.status
            if inc.date:
                curr.date = inc.date  # Update watch date!
            if inc.rating:
                curr.rating = inc.rating
            if inc.comment:
                curr.comment = inc.comment
            updated_count += 1
        elif inc_prec == curr_prec:
            # Same status: Sync rating/comment deletions and date corrections
            changed = False
            if inc.rating != curr.rating:
                curr.rating = inc.rating
                changed = True
            if inc.comment != curr.comment:
                curr.comment = inc.comment
                changed = True
            if inc.date and inc.date != curr.date:
                curr.date = inc.date
                changed = True
            if changed:
                updated_count += 1

    return existing_map, added_count, updated_count

def write_atomic_csv(csv_path: str, records_map: Dict[str, UnifiedRecord]):
    """Writes status-grouped reverse-chronological CSV atomically with UTF-8 BOM."""
    grouped = {"看过": [], "在看": [], "想看": []}
    others = []

    for r in records_map.values():
        if r.status in grouped:
            grouped[r.status].append(r)
        else:
            others.append(r)

    # Sort each group descending by date, then subject_id
    for st in ["看过", "在看", "想看"]:
        grouped[st].sort(key=lambda x: (x.date or "0000-00-00", x.subject_id), reverse=True)
    others.sort(key=lambda x: (x.date or "0000-00-00", x.subject_id), reverse=True)

    sorted_records = grouped["看过"] + grouped["在看"] + grouped["想看"] + others

    # Staging write
    dir_name = os.path.dirname(csv_path) or "."
    os.makedirs(dir_name, exist_ok=True)
    tmp_path = f"{csv_path}.tmp.{os.getpid()}.{int(time.time())}"

    with open(tmp_path, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["title", "url", "date", "rating", "status", "comment"])
        for r in sorted_records:
            writer.writerow(r.to_csv_row())
        f.flush()
        os.fsync(f.fileno())

    # Safe backup without unlinking csv_path
    bak_path = f"{csv_path}.bak"
    if os.path.exists(csv_path):
        try:
            shutil.copyfile(csv_path, bak_path)
        except Exception as e:
            print(f"[!] Warning: backup copy failed: {e}")

    # Atomic swap: csv_path is never missing from disk
    os.replace(tmp_path, csv_path)

def fold_candidates(incoming_records: List[UnifiedRecord]) -> List[UnifiedRecord]:
    """
    Folds incoming candidates from RSS and Frodo API by subject_id.
    Preserves STATUS_PRECEDENCE (看过 > 在看 > 想看) and recency,
    preventing lower-priority records (e.g. mark backlog) from overwriting
    higher-priority records (e.g. RSS watched).
    """
    candidate_map: Dict[str, UnifiedRecord] = {}
    for rec in incoming_records:
        sid = rec.subject_id
        if not sid:
            continue
        if sid not in candidate_map:
            candidate_map[sid] = UnifiedRecord(
                subject_id=rec.subject_id,
                title=rec.title,
                url=rec.url,
                date=rec.date,
                rating=rec.rating,
                status=rec.status,
                comment=rec.comment,
                raw_ts=rec.raw_ts
            )
            continue

        curr = candidate_map[sid]
        curr_prec = STATUS_PRECEDENCE.get(curr.status, 0)
        new_prec = STATUS_PRECEDENCE.get(rec.status, 0)

        if new_prec > curr_prec:
            curr.status = rec.status
            if rec.date:
                curr.date = rec.date
            if rec.rating:
                curr.rating = rec.rating
            if rec.comment:
                curr.comment = rec.comment
            if rec.url:
                curr.url = rec.url
            if rec.title:
                curr.title = rec.title
            curr.raw_ts = rec.raw_ts or curr.raw_ts
        elif new_prec == curr_prec:
            if rec.date and (not curr.date or rec.date >= curr.date):
                curr.date = rec.date
                curr.raw_ts = rec.raw_ts or curr.raw_ts
            if rec.rating:
                curr.rating = rec.rating
            if rec.comment:
                curr.comment = rec.comment
            if rec.title and not curr.title:
                curr.title = rec.title
        else:
            if not curr.title and rec.title:
                curr.title = rec.title
            if not curr.comment and rec.comment:
                curr.comment = rec.comment

    return list(candidate_map.values())

def save_state(state_path: str, state_data: Dict[str, Any]):
    """Atomically saves state JSON, ensuring parent directories exist."""
    state_dir = os.path.dirname(state_path) or "."
    os.makedirs(state_dir, exist_ok=True)
    tmp_state = f"{state_path}.tmp.{os.getpid()}"
    with open(tmp_state, 'w', encoding='utf-8') as f:
        json.dump(state_data, f, indent=2, ensure_ascii=False)
    os.replace(tmp_state, state_path)

def run_sync_pipeline(user_id: str, output_csv: str, state_path: str, limit: int = 50):
    """Executes the complete 5-stage synchronization pipeline."""
    clean_user = str(user_id).strip().strip('/')
    print(f"=== Starting Douban Movie/TV Sync for User {clean_user} ===")
    
    # Stage 1: RSS Ingestion
    print("[Stage 1/5] Ingesting RSS feed...")
    rss_records = parse_rss_feed(clean_user)
    print(f"            Found {len(rss_records)} movie/TV records in RSS.")

    # Stage 2: Frodo API Fetch
    print(f"[Stage 2/5] Fetching Frodo API backfills (limit={limit} per status)...")
    frodo_records = []
    watermarks = {}
    for st_code in ["done", "doing", "mark"]:
        time.sleep(1.5)  # Politeness rate limit delay
        recs, wm = fetch_frodo_status(clean_user, st_code, count=limit)
        frodo_records.extend(recs)
        if wm:
            watermarks[st_code] = wm
        print(f"            Status '{st_code}': received {len(recs)} records.")

    # Fold RSS and Frodo candidates respecting STATUS_PRECEDENCE and recency
    combined_incoming = fold_candidates(rss_records + frodo_records)
    print(f"            Total unique incoming candidates: {len(combined_incoming)}")

    # Stage 3: Merge & Conflict Resolution
    print("[Stage 3/5] Loading existing CSV and resolving conflicts...")
    existing_map = load_existing_csv(output_csv)
    print(f"            Existing CSV records: {len(existing_map)}")
    merged_map, added, updated = reconcile_and_merge(existing_map, combined_incoming)
    print(f"            Added: {added}, Updated/Promoted: {updated}, Total: {len(merged_map)}")

    # Stage 4: Atomic CSV Write
    print("[Stage 4/5] Writing updated CSV atomically...")
    write_atomic_csv(output_csv, merged_map)
    print(f"            Successfully wrote: {output_csv}")

    # Stage 5: State Persistence
    print("[Stage 5/5] Updating state file...")
    state_data = {
        "version": 2,
        "douban_user": clean_user,
        "last_sync_timestamp": datetime.now(timezone.utc).isoformat(),
        "frodo": {
            "watermarks": watermarks,
            "last_status": "success"
        },
        "statistics": {
            "total_csv_records": len(merged_map),
            "last_added_count": added,
            "last_updated_count": updated
        }
    }
    save_state(state_path, state_data)
    print(f"            State persisted to: {state_path}")
    print("=== Sync Pipeline Completed Successfully ===")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Douban Frodo RSS Backfill Sync")
    parser.add_argument("--user", default="137859347", help="Douban User ID")
    parser.add_argument("--csv", default="影视.csv", help="Target CSV file path")
    parser.add_argument("--state", default=".douban-sync-state.json", help="State JSON path")
    parser.add_argument("--limit", type=int, default=50, help="Recent items per status to fetch (e.g. 50 or 100)")
    args = parser.parse_args()

    run_sync_pipeline(args.user, args.csv, args.state, limit=args.limit)

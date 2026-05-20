import os
import time
import json
import re
import xml.etree.ElementTree as ET
import argparse
import sys
import requests
from bs4 import BeautifulSoup

# Fix encoding issues on Windows console/logs
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

CONFIG_FILE = "config.json"
HISTORY_FILE = "sync_history_neodb.json"

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"douban_id": "", "sync_delay_seconds": 2}

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            return set(json.load(f))
    return set()

def save_history(history):
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(list(history), f, indent=2, ensure_ascii=False)

def fetch_rss(douban_id):
    url = f"https://www.douban.com/feed/people/{douban_id}/interests"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    response = requests.get(url, headers=headers, timeout=15)
    response.raise_for_status()
    return response.text

def parse_rss(xml_data):
    root = ET.fromstring(xml_data)
    items = []
    for item in root.findall('.//item'):
        guid_elem = item.find('guid')
        if guid_elem is None:
            continue
        guid = guid_elem.text
        
        title_elem = item.find('title')
        raw_title = title_elem.text if title_elem is not None else ""
        
        link_elem = item.find('link')
        link = link_elem.text if link_elem is not None else ""
        
        description_elem = item.find('description')
        description = description_elem.text if description_elem is not None else ""
        
        action = None
        title = raw_title
        if raw_title.startswith("看过"):
            action = "watched"
            title = raw_title[2:]
        elif raw_title.startswith("想看"):
            action = "plantowatch"
            title = raw_title[2:]
            
        rating = None
        memo = None
        original_title = None
        
        if description:
            soup = BeautifulSoup(description, 'html.parser')
            # Extract English/original title from the first anchor tag's title attribute inside description
            a_tag = soup.find('a')
            if a_tag and a_tag.get('title'):
                original_title = a_tag.get('title').strip()
                
            for p in soup.find_all('p'):
                text = p.text.strip()
                if text.startswith("推荐:"):
                    rec = text.split("推荐:")[1].strip()
                    rating_map = {"很差": 2, "较差": 4, "还行": 6, "推荐": 8, "力荐": 10}
                    if rec in rating_map:
                        rating = rating_map[rec]
                elif text.startswith("备注:"):
                    memo = text.split("备注:", 1)[1].strip()
                    
                    # Robustly extract rating from the memo/review (e.g. 3.5, 3.5分, 4.5/5)
                    decimal_match = re.search(r'\b([0-5]\.[0-9])\b', memo)
                    if decimal_match:
                        try:
                            val = float(decimal_match.group(1))
                            if 0 <= val <= 5:
                                rating = int(val * 2)
                        except ValueError:
                            pass
                    else:
                        score_match = re.search(r'\b([0-5])\s*(?:分|/5)\b', memo)
                        if score_match:
                            try:
                                val = float(score_match.group(1))
                                if 0 <= val <= 5:
                                    rating = int(val * 2)
                            except ValueError:
                                pass
        
        if action:
            items.append({
                "guid": guid,
                "action": action,
                "title": title.strip(),
                "original_title": original_title,
                "link": link.strip(),
                "rating": rating,
                "memo": memo
            })
    return items

def resolve_neodb_item(douban_url, instance_domain, access_token, max_retries=6, retry_delay=15):
    """
    Resolves the NeoDB item UUID for a Douban URL.
    Handles HTTP 202 (Accepted) by waiting and retrying.
    """
    url = f"https://{instance_domain}/api/catalog/fetch"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "User-Agent": "DoubanToNeoDBSync/1.0",
        "Accept": "application/json"
    }
    params = {"url": douban_url}
    
    for attempt in range(1, max_retries + 1):
        try:
            print(f"Calling NeoDB fetch API (Attempt {attempt}/{max_retries})...")
            # We follow redirects automatically.
            # HTTP 302 redirects to `/api/catalog/item/{uuid}`, which returns 200 with item json.
            res = requests.get(url, headers=headers, params=params, allow_redirects=True, timeout=15)
            
            if res.status_code == 200:
                data = res.json()
                uuid = data.get("uuid")
                if uuid:
                    print(f"Successfully resolved item UUID: {uuid}")
                    return uuid
                else:
                    print("Error: NeoDB returned 200 OK but no UUID was found in the response body.")
                    return None
            elif res.status_code == 202:
                print(f"NeoDB is currently fetching/importing this item (HTTP 202). Waiting {retry_delay} seconds...")
                if attempt < max_retries:
                    time.sleep(retry_delay)
                continue
            elif res.status_code == 422:
                print("NeoDB cannot process this URL (HTTP 422). The URL might not be supported or is invalid.")
                return None
            elif res.status_code == 401:
                print("NeoDB authentication failed (HTTP 401). Please check your NEODB_ACCESS_TOKEN.")
                return None
            else:
                print(f"NeoDB returned unexpected status code {res.status_code}: {res.text}")
                return None
        except Exception as e:
            print(f"Error querying NeoDB fetch API: {e}")
            if attempt < max_retries:
                time.sleep(retry_delay)
            continue
            
    print(f"Timeout: NeoDB did not finish fetching the item after {max_retries * retry_delay} seconds.")
    return None

def sync_to_neodb(item_uuid, action, instance_domain, access_token, rating=None, memo=None, dry_run=False):
    """
    Marks an item on your NeoDB shelf.
    """
    url = f"https://{instance_domain}/api/me/shelf/item/{item_uuid}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "User-Agent": "DoubanToNeoDBSync/1.0",
        "Accept": "application/json"
    }
    
    # Map actions
    shelf_type = "complete" if action == "watched" else "wishlist"
    
    # Construct payload
    payload = {
        "shelf_type": shelf_type,
        "visibility": 0, # Public
    }
    
    # NeoDB supports up to 10 for rating_grade (10 means 5 stars, 0 means no rating)
    if rating is not None:
        payload["rating_grade"] = rating
    else:
        payload["rating_grade"] = 0
        
    if memo:
        payload["comment_text"] = memo
    else:
        payload["comment_text"] = ""
        
    if dry_run:
        print(f"[DRY-RUN] Would POST to {url}:")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return True
        
    try:
        res = requests.post(url, headers=headers, json=payload, timeout=15)
        if res.status_code == 200:
            print(f"Successfully marked item {item_uuid} as {shelf_type} on NeoDB.")
            return True
        else:
            print(f"Failed to mark item on NeoDB. Status {res.status_code}, Response: {res.text}")
            return False
    except Exception as e:
        print(f"Error marking item on NeoDB: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Sync Douban RSS feed to NeoDB.")
    parser.add_argument("--local-xml", type=str, help="Path to a local XML file to read instead of fetching from Douban.")
    parser.add_argument("--dry-run", action="store_true", help="Simulate execution without sending API requests to NeoDB.")
    args = parser.parse_args()

    access_token = os.environ.get("NEODB_ACCESS_TOKEN")
    instance_domain = os.environ.get("NEODB_INSTANCE_DOMAIN") or "neodb.social"
    
    if not args.dry_run and not access_token:
        print("Error: NEODB_ACCESS_TOKEN is not set. Skipping NeoDB sync.")
        return

    config = load_config()
    douban_id = config.get("douban_id")
    if not args.local_xml and not douban_id:
        print("Error: douban_id is not set in config.json and no local XML is provided.")
        return
        
    delay = config.get("sync_delay_seconds", 2)
    history = load_history()
    
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
        
    print(f"Found {len(items)} items in RSS. Checking for new items...")
    
    # Process oldest first to keep history chronological
    for item in reversed(items):
        guid = item["guid"]
        if guid in history:
            continue
            
        print(f"\nProcessing item: {item['title']} ({item['action']})")
        print(f"Douban URL: {item['link']}")
        
        # We match on NeoDB directly using the Douban URL!
        item_uuid = resolve_neodb_item(
            douban_url=item['link'],
            instance_domain=instance_domain,
            access_token=access_token if access_token else "mock_token"
        )
        
        if item_uuid:
            success = sync_to_neodb(
                item_uuid=item_uuid,
                action=item["action"],
                instance_domain=instance_domain,
                access_token=access_token,
                rating=item.get("rating"),
                memo=item.get("memo"),
                dry_run=args.dry_run
            )
            if success and not args.dry_run:
                history.add(guid)
                save_history(history)
            
            # Delay between processing items to respect NeoDB rate limits
            if not args.dry_run:
                time.sleep(delay)
        else:
            print(f"Could not resolve NeoDB UUID for {item['title']}. Skipping NeoDB sync.")
            # If resolve fails (e.g. unsupported link type or 422), we still skip,
            # but we do NOT add it to history so it can be retried later if the issue is resolved or temporary.

if __name__ == "__main__":
    main()

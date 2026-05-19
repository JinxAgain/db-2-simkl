import os
import time
import json
import re
import random
import string
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
HISTORY_FILE = "sync_history.json"

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
    response = requests.get(url, headers=headers)
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

import hashlib
from urllib.parse import urljoin

_session = None

def get_session():
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        })
    return _session

def solve_pow(cha, difficulty=4):
    target = '0' * difficulty
    nonce = 0
    while True:
        nonce += 1
        data = f"{cha}{nonce}".encode('utf-8')
        h = hashlib.sha512(data).hexdigest()
        if h.startswith(target):
            return nonce

def fetch_with_retry_and_pow(url, delay=2):
    session = get_session()
    time.sleep(delay)
    
    try:
        resp = session.get(url, timeout=10)
        resp.encoding = 'utf-8'
        
        if resp.status_code != 200:
            print(f"Failed to fetch {url}: Status {resp.status_code}")
            return None
            
        soup = BeautifulSoup(resp.text, 'html.parser')
        sec_form = soup.find('form', id='sec')
        
        if sec_form:
            print("Douban PoW security challenge detected. Solving PoW...")
            tok = sec_form.find('input', id='tok')['value']
            cha = sec_form.find('input', id='cha')['value']
            red = sec_form.find('input', id='red')['value']
            
            # Try to extract dynamic difficulty if present, default to 4
            difficulty = 4
            script_tags = soup.find_all('script')
            for script in script_tags:
                if script.string and 'difficulty' in script.string:
                    diff_match = re.search(r'difficulty\s*=\s*(\d+)', script.string)
                    if diff_match:
                        difficulty = int(diff_match.group(1))
                        break
            
            sol = solve_pow(cha, difficulty)
            print(f"PoW challenge solved! Solution: {sol} (difficulty: {difficulty})")
            
            action = sec_form.get('action', '/c')
            post_url = urljoin(resp.url, action)
            
            post_data = {
                "tok": tok,
                "cha": cha,
                "sol": str(sol),
                "red": red
            }
            
            post_headers = {
                "Referer": resp.url,
                "Origin": resp.url.split("/subject")[0]
            }
            
            post_resp = session.post(post_url, data=post_data, headers=post_headers, allow_redirects=False)
            if post_resp.status_code in [301, 302, 303, 307, 308]:
                redirect_url = post_resp.headers.get("Location")
                resp = session.get(redirect_url, timeout=10)
                resp.encoding = 'utf-8'
            else:
                resp = post_resp
                resp.encoding = 'utf-8'
                
        return resp.text
    except Exception as e:
        print(f"Error fetching/solving PoW for {url}: {e}")
        return None

def extract_imdb_and_year(douban_link, delay):
    html = fetch_with_retry_and_pow(douban_link, delay)
    if not html:
        return None, None
        
    try:
        soup = BeautifulSoup(html, 'html.parser')
        
        # 1. Extract IMDb ID
        imdb_id = None
        info_div = soup.find('div', id='info')
        if info_div:
            info_text = info_div.get_text()
            imdb_match = re.search(r'IMDb:\s*(tt\d+)', info_text)
            if imdb_match:
                imdb_id = imdb_match.group(1)
        
        if not imdb_id:
            text = soup.get_text()
            imdb_match = re.search(r'IMDb:\s*(tt\d+)', text)
            if imdb_match:
                imdb_id = imdb_match.group(1)
                
        # 2. Extract Year
        year = None
        year_span = soup.find('span', class_='year')
        if year_span:
            year_val = year_span.get_text().strip('()')
            if year_val.isdigit() and len(year_val) == 4:
                year = int(year_val)
        if not year:
            title_str = soup.title.string if soup.title else ""
            year_match = re.search(r'\((\d{4})\)', title_str)
            if year_match:
                year = int(year_match.group(1))
                
        return imdb_id, year
    except Exception as e:
        print(f"Error parsing HTML for {douban_link}: {e}")
        return None, None

def clean_title_for_search(title):
    season_match = re.search(r'\s*第([一二三四五六七八九十0-9]+)季$', title)
    season_num = None
    if season_match:
        chinese_to_num = {'一':1, '二':2, '三':3, '四':4, '五':5, '六':6, '七':7, '八':8, '九':9, '十':10}
        num_str = season_match.group(1)
        if num_str.isdigit():
            season_num = int(num_str)
        elif num_str in chinese_to_num:
            season_num = chinese_to_num[num_str]
        
        clean_title = title[:season_match.start()].strip()
        return clean_title, season_num
    return title, None

def get_parent_imdb_id(show_id, headers, params):
    try:
        url = f"https://api.themoviedb.org/3/tv/{show_id}/external_ids"
        res = requests.get(url, headers=headers, params=params)
        if res.status_code == 200:
            return res.json().get("imdb_id")
    except Exception as e:
        print(f"Error fetching parent show IMDb ID for TMDB ID {show_id}: {e}")
    return None

def resolve_tmdb(title, original_title, imdb_id):
    tmdb_api_key = os.environ.get("TMDB_API_KEY")
    tmdb_bearer = os.environ.get("TMDB_BEARER_TOKEN")
    
    headers = {"accept": "application/json"}
    if tmdb_bearer:
        headers["Authorization"] = f"Bearer {tmdb_bearer}"
        
    params = {}
    if tmdb_api_key:
        params["api_key"] = tmdb_api_key
        
    if not tmdb_api_key and not tmdb_bearer:
        print("Warning: Neither TMDB_API_KEY nor TMDB_BEARER_TOKEN is set. TMDB resolution will fail.")
        return None, None, None, None

    # Try resolving via IMDb ID first
    if imdb_id:
        url = f"https://api.themoviedb.org/3/find/{imdb_id}"
        find_params = {**params, "external_source": "imdb_id"}
        try:
            res = requests.get(url, headers=headers, params=find_params)
            if res.status_code == 200:
                data = res.json()
                if data.get("movie_results"):
                    return data["movie_results"][0]["id"], "movie", None, imdb_id
                elif data.get("tv_results"):
                    return data["tv_results"][0]["id"], "show", None, imdb_id
                elif data.get("tv_episode_results"):
                    ep = data["tv_episode_results"][0]
                    show_id = ep["show_id"]
                    parent_imdb = get_parent_imdb_id(show_id, headers, params)
                    return show_id, "show", ep.get("season_number"), parent_imdb
                elif data.get("tv_season_results"):
                    season = data["tv_season_results"][0]
                    show_id = season["show_id"]
                    parent_imdb = get_parent_imdb_id(show_id, headers, params)
                    return show_id, "show", season.get("season_number"), parent_imdb
        except Exception as e:
            print(f"Error calling TMDB find API for {imdb_id}: {e}")
            
    # Fallback to text search using original title (highly accurate for foreign titles)
    if original_title:
        url = "https://api.themoviedb.org/3/search/multi"
        clean_orig, parsed_season_orig = clean_title_for_search(original_title)
        search_params = {**params, "query": clean_orig}
        try:
            res = requests.get(url, headers=headers, params=search_params)
            if res.status_code == 200:
                data = res.json()
                if data.get("results"):
                    for item in data["results"]:
                        if item.get("media_type") in ["movie", "tv"]:
                            media_type = "movie" if item["media_type"] == "movie" else "show"
                            parent_imdb = None
                            if media_type == "show":
                                parent_imdb = get_parent_imdb_id(item["id"], headers, params)
                            else:
                                # For movies, try to get IMDb ID from movie details if needed, or leave None
                                pass
                            return item["id"], media_type, parsed_season_orig, parent_imdb
        except Exception as e:
            print(f"Error calling TMDB search API for original title {original_title}: {e}")

    # Fallback to text search using Chinese title
    if title:
        url = "https://api.themoviedb.org/3/search/multi"
        clean_title, parsed_season = clean_title_for_search(title)
        search_params = {**params, "query": clean_title, "language": "zh-CN"}
        try:
            res = requests.get(url, headers=headers, params=search_params)
            if res.status_code == 200:
                data = res.json()
                if data.get("results"):
                    for item in data["results"]:
                        if item.get("media_type") in ["movie", "tv"]:
                            media_type = "movie" if item["media_type"] == "movie" else "show"
                            parent_imdb = None
                            if media_type == "show":
                                parent_imdb = get_parent_imdb_id(item["id"], headers, params)
                            return item["id"], media_type, parsed_season, parent_imdb
        except Exception as e:
            print(f"Error calling TMDB search API for {title}: {e}")

    return None, None, None, None

def resolve_media_type_via_simkl(imdb_id):
    client_id = os.environ.get("SIMKL_CLIENT_ID")
    if not client_id or not imdb_id:
        return None
    url = "https://api.simkl.com/search/id"
    params = {"imdb": imdb_id, "client_id": client_id}
    try:
        res = requests.get(url, params=params)
        if res.status_code == 200:
            data = res.json()
            if data and isinstance(data, list) and len(data) > 0:
                simkl_type = data[0].get("type")
                if simkl_type == "movie":
                    return "movie"
                elif simkl_type in ["tv", "show", "episode"]:
                    return "show"
    except Exception as e:
        print(f"Error querying Simkl ID lookup: {e}")
    return None

def sync_to_simkl(tmdb_id, imdb_id, media_type, season_number, action, title=None, year=None, rating=None, memo=None, dry_run=False):
    client_id = os.environ.get("SIMKL_CLIENT_ID")
    access_token = os.environ.get("SIMKL_ACCESS_TOKEN")
    
    if not dry_run and (not client_id or not access_token):
        print("Warning: Simkl credentials not provided. Skipping sync.")
        return False
        
    headers = {
        "Content-Type": "application/json",
        "simkl-api-key": client_id or "",
        "Authorization": f"Bearer {access_token or ''}"
    }
    
    # Base item object
    item_obj = {}
    
    # 1. Add IDs
    ids = {}
    if tmdb_id:
        ids["tmdb"] = tmdb_id
    if imdb_id:
        ids["imdb"] = imdb_id
    item_obj["ids"] = ids
    
    # 2. Add metadata
    if title:
        item_obj["title"] = title
    if year:
        item_obj["year"] = int(year)
        
    # 3. Add rating and memo directly to the history object if applicable
    if rating is not None:
        item_obj["rating"] = rating
    if memo:
        item_obj["memo"] = {"text": memo[:140]}
        
    # Construct list wrapper (movies or shows)
    plural_type = media_type + "s" # "movies" or "shows"
    
    if action == "watched":
        url = "https://api.simkl.com/sync/history"
        
        # If it is a show and we have a season number, add it
        if media_type == "show" and season_number is not None:
            item_obj["seasons"] = [{"number": season_number}]
            
        payload = {plural_type: [item_obj]}
        
    elif action == "plantowatch":
        url = "https://api.simkl.com/sync/add-to-list"
        item_obj["to"] = "plantowatch"
        payload = {plural_type: [item_obj]}
    else:
        return False
        
    success = False
    if dry_run:
        print(f"[DRY-RUN] Would send payload to {url}:")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        success = True
    else:
        try:
            res = requests.post(url, headers=headers, json=payload)
            if res.status_code in [200, 201]:
                print(f"Successfully synced {title or 'item'} to Simkl ({action}).")
                success = True
            else:
                print(f"Failed to sync to Simkl. Status {res.status_code}, Response: {res.text}")
                success = False
        except Exception as e:
            print(f"Error syncing to Simkl: {e}")
            success = False
            
    # Also sync rating to `/sync/ratings` if rating is provided
    if success and rating is not None:
        rating_url = "https://api.simkl.com/sync/ratings"
        
        rating_obj = {
            "ids": ids,
            "rating": rating
        }
        if title:
            rating_obj["title"] = title
        if year:
            rating_obj["year"] = int(year)
            
        rating_payload = {plural_type: [rating_obj]}
        
        if dry_run:
            print(f"[DRY-RUN] Would send rating to {rating_url}:")
            print(json.dumps(rating_payload, indent=2, ensure_ascii=False))
        else:
            try:
                res_rating = requests.post(rating_url, headers=headers, json=rating_payload)
                if res_rating.status_code in [200, 201]:
                    print(f"Successfully added rating {rating} for {title or 'item'}.")
                else:
                    print(f"Failed to add rating. Status {res_rating.status_code}, Response: {res_rating.text}")
            except Exception as e:
                print(f"Error adding rating: {e}")
                
    return success

def main():
    parser = argparse.ArgumentParser(description="Sync Douban RSS feed to Simkl.")
    parser.add_argument("--local-xml", type=str, help="Path to a local XML file to read instead of fetching from Douban.")
    parser.add_argument("--dry-run", action="store_true", help="Simulate execution without sending API requests to Simkl.")
    args = parser.parse_args()

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
        
    print(f"Found {len(items)} items. Checking for new items...")
    
    # Process oldest first to keep history chronological
    for item in reversed(items):
        guid = item["guid"]
        if guid in history:
            continue
            
        print(f"\nProcessing item: {item['title']} ({item['action']})")
        imdb_id, extracted_year = extract_imdb_and_year(item['link'], delay)
        print(f"Extracted IMDb ID: {imdb_id}, Extracted Year: {extracted_year}")
        
        # Try TMDB first
        tmdb_id, media_type, season_number, parent_imdb_id = resolve_tmdb(item['title'], item['original_title'], imdb_id)
        
        # If TMDB failed but we have IMDb ID, resolve type via Simkl or title heuristics
        if not media_type and imdb_id:
            print("TMDB resolution failed or keys not set. Resolving type via Simkl lookup...")
            media_type = resolve_media_type_via_simkl(imdb_id)
            if not media_type:
                # Guess from title
                clean_title, parsed_season = clean_title_for_search(item['title'])
                if parsed_season is not None:
                    media_type = "show"
                    season_number = parsed_season
                else:
                    media_type = "movie"
                    
        # Resolve final IMDb ID to send to Simkl (ensure shows get parent IMDb ID, not episode IMDb ID)
        final_imdb_id = imdb_id
        if media_type == "show":
            if parent_imdb_id:
                final_imdb_id = parent_imdb_id
            else:
                # If we have a season number or it's a TV show, and we didn't resolve parent show IMDb ID,
                # the extracted IMDb ID from Douban is likely episode-specific. We set final_imdb_id to None
                # so Simkl matches by TMDB ID or title/year instead of mismatching on the episode ID.
                # Only keep original imdb_id if season is None and we didn't parse any season number from title.
                clean_title, parsed_season = clean_title_for_search(item['title'])
                if season_number is not None or parsed_season is not None:
                    final_imdb_id = None
                    
        if media_type:
            # We can sync! (We have either tmdb_id, or at least imdb_id + media_type)
            print(f"Syncing item: {item['original_title'] or item['title']}")
            print(f"  IMDb ID: {final_imdb_id} (original: {imdb_id})")
            print(f"  TMDB ID: {tmdb_id}")
            print(f"  Type: {media_type}")
            print(f"  Season: {season_number}")
            print(f"  Rating: {item.get('rating')}")
            
            success = sync_to_simkl(
                tmdb_id=tmdb_id,
                imdb_id=final_imdb_id,
                media_type=media_type,
                season_number=season_number,
                action=item["action"],
                title=item["original_title"] or item["title"],
                year=extracted_year,
                rating=item.get("rating"),
                memo=item.get("memo"),
                dry_run=args.dry_run
            )
            if success and not args.dry_run:
                history.add(guid)
                save_history(history)
        else:
            print(f"Could not resolve TMDB ID or IMDb ID for {item['title']}. Skipping Simkl sync.")
            if not args.dry_run:
                history.add(guid)
                save_history(history)

if __name__ == "__main__":
    main()

# Douban Sync Tools (Simkl & NeoDB)

This repository contains two independent, automatic synchronization tools powered by GitHub Actions. They allow you to sync your Douban movie, TV show, and animation watch history ("看过" and "想看") to two major platforms: **Simkl** and **NeoDB**.

---

## Architecture & Features

### 🎬 Douban to Simkl Sync
* **Script**: [`sync_simkl.py`](file:///c:/Users/Barba/Documents/Git/db-2-simkl/sync_simkl.py)
* **Workflow**: Runs hourly and manually ([`douban_simkl_sync.yml`](file:///c:/Users/Barba/Documents/Git/db-2-simkl/.github/workflows/douban_simkl_sync.yml))
* **Season-Level Accuracy**: Solves the common Douban problem where a TV show page only maps to the IMDb ID of a single episode. The script uses the TMDB API to resolve the parent show and season number, allowing Simkl to mark the correct season as watched.
* **Fallbacks**: Searches TMDB by original/Chinese titles if Douban is missing the IMDb ID.
* **Status History**: Saved in `sync_history.json`.

### 🌐 Douban to NeoDB Sync
* **Script**: [`sync_neodb.py`](file:///c:/Users/Barba/Documents/Git/db-2-simkl/sync_neodb.py)
* **Workflow**: Runs hourly and manually ([`douban_neodb_sync.yml`](file:///c:/Users/Barba/Documents/Git/db-2-simkl/.github/workflows/douban_neodb_sync.yml))
* **Native Douban Mapping**: Directly queries NeoDB's catalog fetch API (`/api/catalog/fetch`) using the Douban URL to locate the exact item, avoiding metadata mismatch issues.
* **Unlimited Comment Length**: NeoDB has no character limits on comments. The script uploads your full, untruncated Douban reviews and notes to your NeoDB timeline/shelf.
* **Precise Ratings**: Extracts half-star ratings (e.g. `3.5` stars becomes `7` out of 10) directly from Douban notes.
* **Status History**: Saved in `sync_history_neodb.json`.

### 📦 Douban Watch History Archive
* **Script**: [`archive_douban.py`](file:///c:/Users/Barba/Documents/Git/db-2-simkl/archive_douban.py)
* **Workflow**: Runs hourly and manually ([`douban_archive.yml`](file:///c:/Users/Barba/Documents/Git/db-2-simkl/.github/workflows/douban_archive.yml))
* **Full Baseline & Hybrid Backfill**: Starts with complete historical data exported from `douban_interests.xlsx` (via [`export_xlsx_to_jsonl.py`](file:///c:/Users/Barba/Documents/Git/db-2-simkl/export_xlsx_to_jsonl.py)), then combines **Douban RSS** with **Frodo Mobile API backfill** (`type=movie` across `看过`, `在看`, `想看`) to overcome the 10-item RSS truncation window and enrich metadata (`intro`, `douban_rating`, `tags`) into [`douban_archive.jsonl`](file:///c:/Users/Barba/Documents/Git/db-2-simkl/douban_archive.jsonl).
* **Configurable 50 / 100 Backfill Limit**: Configure `frodo_backfill_enabled` and `frodo_backfill_limit` (`50` or `100` per status, automatically paginating in 50-item pages) in [`config.json`](file:///c:/Users/Barba/Documents/Git/db-2-simkl/config.json), pass `--frodo-limit 50|100` on the CLI (or `--no-frodo` to use RSS only), or choose `50`/`100` directly when triggering the GitHub Actions workflow manually.
* **Precise Ratings & Deduplication**: Extracts half-star ratings and reviews, deduplicating against `sync_history_archive.json` and the archive file.
* **Status History**: Saved in `sync_history_archive.json`.

---

## Setup Guide

### 1. Initial Configuration (One-Click Setup for Forks)
1. **Fork** this repository to your own GitHub account.
2. Go to the **Actions** tab in your forked repository.
3. Select **"Initialize Repository for New User"** in the left sidebar, click **Run workflow**:
   - Enter your **Douban User ID** (found in your Douban homepage URL `https://www.douban.com/people/YOUR_ID/`).
   - Leave `clean_archive` checked (unless you already uploaded your own `douban_archive.jsonl`).
   - Click **Run workflow**.
   - *This will automatically clean previous sync history, reset the archive, and configure `config.json` for you within seconds!*


---

### 2. Configure Simkl Sync (Optional)
If you want to sync to Simkl, complete these quick steps using modern **Simkl AUTH V2**:

1. **Create a Simkl AUTH V2 App**:
   - Go to [Simkl Developer Settings](https://simkl.com/settings/developer/).
   - Click **Create an AUTH V2 app**.
   - Select **TV, devices & command line** (Client ID only, no Client Secret or Redirect URL needed!).
   - Enter an app name (e.g. `Douban Sync`) and save to get your **Client ID**.
2. **Obtain Simkl Tokens (One-Click Flow)**:
   - Run `python get_token.py` locally and paste your **Client ID**.
   - Your browser will automatically open with the authorization code pre-filled.
   - Click **Allow / Approve** on Simkl.
   - The script will instantly detect approval and output your `SIMKL_ACCESS_TOKEN` and `SIMKL_REFRESH_TOKEN`.
3. **Save Simkl Secrets**:
   Go to your GitHub repo -> `Settings > Secrets and variables > Actions > New repository secret` and add:
   - `TMDB_API_KEY`: Your TMDB API Key (Free at [TMDB](https://www.themoviedb.org/)).
   - `SIMKL_CLIENT_ID`: Your Simkl Client ID.
   - `SIMKL_ACCESS_TOKEN`: The generated Access Token.
   - `SIMKL_REFRESH_TOKEN`: The generated Refresh Token (Simkl's refresh tokens are non-rotating with a sliding 180-day window, so your GitHub Action will perpetually refresh tokens automatically with zero maintenance!).

---

### 3. Configure NeoDB Sync (Optional)
If you want to sync to NeoDB, complete these steps:

1. **Obtain your NeoDB Access Token**:
   - Go to the developer console page of your NeoDB instance (e.g. `https://neodb.social/developer/` or `https://neodb.net/developer/`).
   - Log in, expand the **Test Access Token** section, click **Generate**, and copy the token.
2. **Save NeoDB Secrets**:
   Go to your GitHub repo -> `Settings > Secrets and variables > Actions > New repository secret` and add:
   - `NEODB_ACCESS_TOKEN`: The token you just copied.
   - `NEODB_INSTANCE_DOMAIN` (Optional): The domain of your NeoDB instance (defaults to `neodb.social`). If you use a different instance (like `neodb.net`), set this secret to your instance's domain name.

---

### 4. Enable GitHub Actions
1. Go to the **Actions** tab in your GitHub repository.
2. Select either **Douban to Simkl Sync** or **Douban to NeoDB Sync** from the left sidebar.
3. Enable the workflow and click **Run workflow** to trigger the initial sync manually.
4. After the first run, the workflows will run automatically every hour.

> [!NOTE]
> **Why Hourly Execution?**
> Douban's public RSS feed only keeps the 10 most recent actions (watched/wished items). Running the workflows hourly ensures that any new actions you mark are captured before they are pushed out of the RSS cache.
> The scripts only commit changes back to the repository if new records are actually added, so hourly execution is quiet and won't create empty commits.

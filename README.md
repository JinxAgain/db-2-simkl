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

---

## Setup Guide

### 1. Initial Configuration (Shared)
1. **Fork** this repository to your own GitHub account.
2. Edit [`config.json`](file:///c:/Users/Barba/Documents/Git/db-2-simkl/config.json) in your repository:
   - `douban_id`: Change this to your Douban username/ID (found in your Douban homepage URL `https://www.douban.com/people/YOUR_ID/`).
   - `sync_delay_seconds`: The delay (in seconds) between requests to avoid rate limits.

---

### 2. Configure Simkl Sync (Optional)
If you want to sync to Simkl, complete these steps:

1. **Create a Simkl Developer App**:
   - Go to [Simkl Developer Settings](https://simkl.com/settings/developer/new/).
   - Set **Redirect URI** to `http://localhost`.
   - Save to get your **Client ID** and **Client Secret**.
2. **Obtain your Simkl Access Token** (Choose one):
   * **Method A (Web Console)**:
     1. Open in browser: `https://simkl.com/oauth/authorize?response_type=code&client_id=YOUR_CLIENT_ID&redirect_uri=http://localhost`
     2. Authorize, then copy the code from the redirected URL `http://localhost/?code=YOUR_CODE`.
     3. Open any webpage, press `F12` to open the Developer Console, and run:
        ```javascript
        fetch('https://api.simkl.com/oauth/token', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            code: "YOUR_COPIED_CODE",
            client_id: "YOUR_CLIENT_ID",
            client_secret: "YOUR_CLIENT_SECRET",
            redirect_uri: "http://localhost",
            grant_type: "authorization_code"
          })
        }).then(res => res.json()).then(console.log);
        ```
     4. Copy the resulting `"access_token"`.
   * **Method B (Python Script)**:
     - Run `python get_token.py` locally and follow the interactive prompts.
3. **Save Simkl Secrets**:
   Go to your GitHub repo -> `Settings > Secrets and variables > Actions > New repository secret` and add:
   - `TMDB_API_KEY`: Your TMDB API Key (Free at [TMDB](https://www.themoviedb.org/)).
   - `SIMKL_CLIENT_ID`: Your Simkl Client ID.
   - `SIMKL_ACCESS_TOKEN`: The Simkl Access Token you generated.

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

# Douban to Simkl Sync

This project automatically syncs your Douban watched/plan-to-watch history to Simkl using GitHub Actions. It supports movies and TV shows, intelligently handling TV show season mapping using TMDB's API.

## Features
- **Automatic Sync**: Uses GitHub Actions to run every 6 hours and process your Douban RSS feed.
- **Accurate TV Show Mapping**: Solves the common Douban problem where a TV show's page only gives the IMDb ID of a single episode. This script uses TMDB to correctly map that episode to the parent show and the specific season on Simkl.
- **Fallback Search**: If Douban is missing the IMDb ID, it will fallback to searching TMDB using the Chinese title.

## How to use

1. **Fork this repository** to your own GitHub account.
2. Edit the `config.json` file in your repository:
   - Change `douban_id` to your own Douban ID. You can find this in your Douban personal page URL (`https://www.douban.com/people/YOUR_ID/`).
3. Set up **GitHub Secrets**:
   Go to your repository settings: `Settings > Secrets and variables > Actions > New repository secret`.
   Add the following secrets:
   - `TMDB_API_KEY`: Your TMDB API Key. (Get one for free at [TMDB](https://www.themoviedb.org/settings/api))
   - `SIMKL_CLIENT_ID`: Your Simkl Client ID. (Create an app at [Simkl Developer Settings](https://simkl.com/settings/developer/new/))
   - `SIMKL_ACCESS_TOKEN`: Your Simkl Access Token.
   
   *(Note: You can use `TMDB_BEARER_TOKEN` instead of `TMDB_API_KEY` if you prefer to use the API Read Access Token.)*
4. Enable GitHub Actions:
   - Go to the `Actions` tab in your repository and enable workflows.
   - You can trigger the workflow manually by clicking on `Douban to Simkl Sync` -> `Run workflow`.
   - After the first run, it will automatically run every 6 hours to sync new items.

## How it works

1. It fetches your public Douban RSS feed (`https://www.douban.com/feed/people/{your_id}/interests`).
2. For each new item ("看过" or "想看"), it fetches the Douban page and extracts the `IMDb: tt...` ID.
3. It uses TMDB API's `/find` endpoint to resolve the exact TMDB ID, Media Type (movie/show), and Season Number.
4. It calls Simkl's `/sync/history` or `/sync/add-to-list` API to accurately sync the record.
5. Processed items are saved in `sync_history.json` and committed back to the repository so they are not synced twice.

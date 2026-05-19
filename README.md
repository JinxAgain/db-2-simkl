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
3. Create a **Simkl Developer App** to get your API keys:
   - Go to [Simkl Developer Settings](https://simkl.com/settings/developer/new/) and create a new app.
   - **Name**: e.g. `douban-sync`
   - **Redirect URI**: Enter `urn:ietf:wg:oauth:2.0:oob` (this enables PIN authentication).
   - Click **Save Changes** to get your **Client ID** and **Client Secret**.
4. Obtain your **Simkl Access Token**:
   You can choose one of the following methods to get your token:
   
   * **Method A: Browser Console (Easiest - No Installation)**
     1. Open this URL in your browser: `https://simkl.com/oauth/authorize?response_type=code&client_id=YOUR_CLIENT_ID&redirect_uri=urn:ietf:wg:oauth:2.0:oob` (replace `YOUR_CLIENT_ID` with yours).
     2. Click **Authorize** and copy the **PIN Code** displayed on the screen.
     3. Open any webpage (e.g. `google.com`), press `F12` (or right-click -> **Inspect**), and click the **Console** tab.
     4. Paste the following JavaScript code (replace the placeholders) and press Enter:
        ```javascript
        fetch('https://api.simkl.com/oauth/token', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            code: "YOUR_PIN_CODE",
            client_id: "YOUR_CLIENT_ID",
            client_secret: "YOUR_CLIENT_SECRET",
            redirect_uri: "urn:ietf:wg:oauth:2.0:oob",
            grant_type: "authorization_code"
          })
        }).then(res => res.json()).then(console.log);
        ```
     5. The console will print a JSON response containing your `"access_token"`.
     
   * **Method B: Python Script (Interactive)**
     - Run the helper script locally in your terminal:
       ```bash
       python get_token.py
       ```
     - Follow the prompts to get your `SIMKL_ACCESS_TOKEN`.
     
   * **Method C: Online API Tool (Hoppscotch / Postman)**
     - Use a tool like [Hoppscotch](https://hoppscotch.io/).
     - Send a `POST` request to `https://api.simkl.com/oauth/token` with Body type `application/json` and the payload:
       ```json
       {
         "code": "YOUR_PIN_CODE",
         "client_id": "YOUR_CLIENT_ID",
         "client_secret": "YOUR_CLIENT_SECRET",
         "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
         "grant_type": "authorization_code"
       }
       ```
5. Set up **GitHub Secrets**:
   Go to your repository settings: `Settings > Secrets and variables > Actions > New repository secret`.
   Add the following secrets:
   - `TMDB_API_KEY`: Your TMDB API Key. (Get one for free at [TMDB](https://www.themoviedb.org/settings/api))
   - `SIMKL_CLIENT_ID`: Your Simkl Client ID.
   - `SIMKL_ACCESS_TOKEN`: The access token you obtained in Step 4.
   
   *(Note: You can use `TMDB_BEARER_TOKEN` instead of `TMDB_API_KEY` if you prefer to use the API Read Access Token.)*
6. Enable **GitHub Actions**:
   - Go to the `Actions` tab in your repository and enable workflows.
   - You can trigger the workflow manually by clicking on `Douban to Simkl Sync` -> `Run workflow`.
   - After the first run, it will automatically run every 6 hours to sync new items.

## How it works

1. It fetches your public Douban RSS feed (`https://www.douban.com/feed/people/{your_id}/interests`).
2. For each new item ("看过" or "想看"), it fetches the Douban page and extracts the `IMDb: tt...` ID.
3. It uses TMDB API's `/find` endpoint to resolve the exact TMDB ID, Media Type (movie/show), and Season Number.
4. It calls Simkl's `/sync/history` or `/sync/add-to-list` API to accurately sync the record.
5. Processed items are saved in `sync_history.json` and committed back to the repository so they are not synced twice.

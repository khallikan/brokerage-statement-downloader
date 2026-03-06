# Brokerage Statement Downloader — Chrome Extension

A Chrome/Brave extension that automatically downloads monthly brokerage statements as PDFs and tracks them in a shared `download_log.json` file.

## Supported Brokerages

- Robinhood
- Charles Schwab
- E\*Trade
- Fidelity
- Webull
- M1 Finance
- Vanguard
- Interactive Brokers

## Installation

1. Open `chrome://extensions` (or `brave://extensions`)
2. Enable **Developer mode** (toggle in top-right)
3. Click **Load unpacked** and select the `chrome-extension/` folder
4. Pin the extension to your toolbar for easy access

## Prerequisites

- **Disable "Ask where to save files"** in your browser's download settings — the extension saves files automatically to `Downloads/Statements/<Brokerage>/`
- **Do not click or move your mouse** while the downloader script is running

## How to Use

1. Log into your brokerage account in the browser
2. Navigate to the statements page (e.g., `robinhood.com/account/reports-statements`)
3. Click the extension icon — it will detect the brokerage automatically
4. Either **import** an existing `download_log.json` or click **Create download_log.json** to start fresh
5. Click **Run Downloader Script**
6. A progress window opens showing real-time status — the script will:
   - Detect all account types (e.g., Individual, Crypto, Futures)
   - Expand "View More" to reveal all statements
   - Skip statements already recorded in the download log
   - Download missing PDFs to `Downloads/Statements/<Brokerage>/`
   - Compute SHA-256 hashes for each downloaded file
   - Update `download_log.json` on disk when complete

## How It Works

### Architecture

The extension uses Chrome Manifest V3 and consists of three main components:

| Component | File | Role |
|---|---|---|
| **Service Worker** | `background/service-worker.js` | Orchestrates the download flow, manages the download log, saves files |
| **Content Script** | `content-scripts/detector.js` | Runs on brokerage pages, scrapes statement lists, clicks download links |
| **Popup / Progress UI** | `popup/popup.js`, `popup/progress.js` | User interface for starting downloads and viewing progress |

### Download Flow (Robinhood Example)

```
1. Popup sends "download_statements" → Service Worker
2. Service Worker asks Content Script to detect account tabs
   → Content Script finds: individual, crypto, futures-monthly
3. For each account URL:
   a. Service Worker navigates the tab to the account's statements page
   b. Content Script expands "View More" buttons to reveal all statements
   c. Content Script scrapes statement dates and stores clickable elements
   d. Service Worker receives the statement list
   e. For each statement:
      - Check download_log.json → skip if already downloaded
      - Inject a URL.createObjectURL interceptor into the page (MAIN world)
      - Tell Content Script to click the download link
      - Robinhood's JS fetches the PDF from S3, creates a blob, and calls
        URL.createObjectURL — our interceptor captures the blob as base64
      - Service Worker polls for the captured data
      - Cancels Robinhood's own blob download (to avoid duplicates)
      - Saves the PDF via chrome.downloads API to the correct folder
      - Computes SHA-256 hash from the raw bytes
      - Records the entry in download_log.json
4. After all accounts are processed, export download_log.json to disk
```

### Key Technical Details

- **Blob interception**: Robinhood downloads PDFs by fetching from S3, creating a Blob, then triggering a blob URL download. The extension uses `chrome.scripting.executeScript({ world: 'MAIN' })` to patch `URL.createObjectURL` and capture the PDF data as base64. This bypasses CSP restrictions that block inline script injection.

- **CSP/CORS handling**: A `declarativeNetRequest` rule (`rules/remove_csp.json`) removes Content-Security-Policy headers from brokerage responses and adds CORS headers to S3 responses.

- **Progress tracking**: The service worker writes status updates to `chrome.storage.local`. The progress window (`progress.html`) listens via `chrome.storage.onChanged` for instant updates, with polling as a fallback.

- **Account naming**: Account labels (e.g., `individual0000`, `crypto0000`) are generated to match the naming convention used by the companion `playwright-downloader` tool, so both tools share the same `download_log.json` without conflicts.

- **Skip logic**: Before downloading, the extension checks `download_log.json` for an existing entry with the same `statementDate` and non-zero `fileSizeBytes`. If found, the statement is skipped.

## File Structure

```
chrome-extension/
├── manifest.json                  # Extension manifest (MV3)
├── background/
│   └── service-worker.js          # Download orchestration, file saving
├── content-scripts/
│   └── detector.js                # Page scraping, element clicking
├── popup/
│   ├── popup.html                 # Main popup UI
│   ├── popup.js                   # Popup logic
│   ├── popup.css                  # Styles
│   ├── progress.html              # Progress monitoring window
│   └── progress.js                # Progress page logic
├── rules/
│   └── remove_csp.json            # declarativeNetRequest rules
└── icons/                         # Extension icons
```

## download_log.json Format

The download log is shared between this extension and the playwright-downloader:

```json
{
  "version": 1,
  "lastUpdated": "2026-03-03T22:35:51.000Z",
  "brokerages": {
    "robinhood": {
      "accounts": {
        "individual0000": {
          "statements": [
            {
              "statementDate": "2025-05",
              "filename": "2025-05_Robinhood_individual0000.pdf",
              "downloadedAt": "2026-03-03T22:35:50.812Z",
              "downloadedBy": "chrome-extension",
              "fileSizeBytes": 354270,
              "sha256": "c1f31a5064312d374b7b57ec21a228cda48f4923a118b34acb29b6604c3b0216"
            }
          ]
        }
      }
    }
  }
}
```

## Troubleshooting

- **Extension popup closes immediately**: This is normal — a separate progress window opens automatically to track the download.
- **"No PDF blob captured"**: The blob interceptor may not have been injected in time. Try removing and re-adding the extension, then reload the brokerage page.
- **Statements not detected**: Make sure all "View More" buttons are visible. The script auto-clicks them, but if the page hasn't fully loaded, some may be missed.
- **Wrong account names**: The extension reads account type from the tab link text on the page. If the page layout changes, account detection may need updating.

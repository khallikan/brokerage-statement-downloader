# Brokerage Statement Downloader

Automated tool to download monthly statements from all your brokerage accounts. Supports 8 brokerages with a unified framework. You log in manually (including 2FA), and the automation handles the rest — navigating to statements, detecting all accounts, and downloading every PDF.

## Supported Brokerages

| Slug | Brokerage | Status |
|------|-----------|--------|
| `robinhood` | Robinhood | Tested |
| `schwab` | Charles Schwab | Tested |
| `etrade` | E*Trade | Tested |
| `fidelity` | Fidelity | Tested |
| `webull` | Webull | Tested |
| `m1finance` | M1 Finance | Tested |
| `vanguard` | Vanguard | Tested |
| `ibkr` | Interactive Brokers | Tested |

## Quick Start

```bash
# Install
cd playwright-downloader
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
playwright install chromium

# Run all brokerages
python -m statement_downloader

# Run specific brokerages
python -m statement_downloader robinhood schwab

# Check download status
python -m statement_downloader --status

# List available brokerages
python -m statement_downloader --list
```

## How It Works

1. A headed Chromium browser opens (you can see it)
2. It navigates to the brokerage's login page
3. If not already logged in, you log in manually and complete 2FA, then press ENTER in the terminal
4. If already logged in (persistent cookies from a previous run), it skips the login prompt
5. The script auto-detects all your accounts (Individual, Roth IRA, Crypto, etc.)
6. For each account, it navigates to the statements page, loads all available statements, and downloads any that haven't been downloaded yet
7. Statements are saved with the naming convention `YYYY-MM_BrokerageName_accountlabel.pdf`
8. A `download_log.json` tracks everything so subsequent runs only grab new statements

## File Organization

Statements are saved to `~/Downloads/Statements/` organized by brokerage:

```
~/Downloads/Statements/
├── download_log.json           ← tracks all downloaded statements
├── Robinhood/
│   ├── 2024-01_Robinhood_individual0000.pdf
│   ├── 2024-01_Robinhood_crypto0000.pdf
│   ├── 2024-02_Robinhood_individual0000.pdf
│   └── ...
├── Schwab/
│   ├── 2024-01_Schwab_brokerage4455.pdf
│   └── 2024-01_Schwab_roth8812.pdf
├── ETrade/
├── Fidelity/
├── Webull/
├── M1Finance/
├── Vanguard/
└── InteractiveBrokers/
```

**Multiple accounts:** Each brokerage may have multiple accounts (e.g., Individual, Roth IRA, Crypto). The script auto-detects all accounts and includes the account type + last 4 digits in the filename. All accounts for a brokerage go in the same flat folder.

## Download Tracking (`download_log.json`)

All three approaches (Playwright, Chrome Extension, Safari Extension) share a single `download_log.json` file as the source of truth. The dedup key is `brokerageSlug` + `accountLabel` + `statementDate`.

```json
{
  "version": 1,
  "lastUpdated": "2026-02-14T10:30:00Z",
  "brokerages": {
    "schwab": {
      "displayName": "Charles Schwab",
      "folderName": "Schwab",
      "accounts": {
        "brokerage4455": {
          "accountLabel": "brokerage4455",
          "accountType": "Brokerage",
          "accountNumberLast4": "4455",
          "statements": [
            {
              "statementDate": "2024-01",
              "filename": "2024-01_Schwab_brokerage4455.pdf",
              "downloadedAt": "2026-02-14T10:25:00Z",
              "downloadedBy": "playwright",
              "fileSizeBytes": 245760,
              "sha256": "abc123..."
            }
          ]
        }
      }
    }
  }
}
```

## Project Structure

```
playwright-downloader/
├── pyproject.toml
├── src/statement_downloader/
│   ├── __init__.py
│   ├── __main__.py             # python -m entry point
│   ├── cli.py                  # CLI (argparse)
│   ├── config.py               # Brokerage registry, paths, constants
│   ├── tracker.py              # Read/write download_log.json
│   ├── browser.py              # Launch headed Chromium with persistent profile
│   ├── base_brokerage.py       # Abstract base class for all brokerages
│   └── brokerages/
│       ├── __init__.py          # Registry mapping slugs → classes
│       ├── robinhood.py
│       ├── schwab.py
│       ├── etrade.py
│       ├── fidelity.py
│       ├── webull.py
│       ├── m1finance.py
│       ├── vanguard.py
│       └── ibkr.py
├── tests/
└── browser_data/               # Persistent Chromium profile (gitignored)
```

### Key Components

- **`config.py`** — All 8 brokerage configs (login URLs, statement URLs, folder names)
- **`tracker.py`** — Reads/writes `download_log.json` with multi-account support, atomic writes, SHA-256 hashing
- **`browser.py`** — Launches headed Chromium with a persistent profile so cookies are preserved between runs
- **`base_brokerage.py`** — Abstract base class that orchestrates the full workflow: login → detect accounts → loop accounts → download new statements
- **Brokerage modules** — Each implements 4 abstract methods: `_get_accounts()`, `_navigate_to_statements()`, `_get_available_statements()`, `_download_statement()`

## Architecture: Three Approaches

This project is part of a larger plan with three complementary approaches:

### Approach 1: Playwright Python Scripts (this project)

The main workhorse. Runs on desktop (Mac/PC). Most powerful for bulk first-time downloads. Handles complex navigation, pagination ("View More" buttons), and account switching automatically.

### Approach 2: Chrome Extension (Manifest V3) — Planned

A Chrome extension that runs in your existing browser. You log into a brokerage manually, click the extension, and it downloads statements. No bot detection issues since it uses your real browser session.

**Structure:**
```
chrome-extension/
├── manifest.json
├── popup/                      # Extension popup UI
├── background/service-worker.js
├── content-scripts/
│   ├── detector.js             # Detects which brokerage site you're on
│   └── brokerages/             # One content script per brokerage
└── shared/                     # Config, tracker, utilities
```

**Note:** Both Playwright and Chrome Extension save to `~/Downloads/Statements/` for consistency.

### Approach 3: Safari Web Extension (iOS) — Planned

A Safari extension for iOS, sideloaded via Xcode. You navigate to a brokerage in Safari, log in, tap the extension, and it downloads statements to the Files app.

**Structure:**
```
safari-extension/
├── StatementDownloader.xcodeproj
├── StatementDownloader/                  # iOS app (SwiftUI container)
│   ├── Models/DownloadLog.swift
│   ├── Services/DownloadTracker.swift
│   └── Views/
└── StatementDownloaderExtension/         # Safari Web Extension
    ├── SafariWebExtensionHandler.swift   # Native bridge (JS ↔ Swift)
    └── Resources/                        # JS content scripts (shared with Chrome)
```

## Build Order

### Phase 1: Playwright ✅ (Complete)

1. ~~Create project scaffold~~ ✅
2. ~~Implement core framework (config, tracker, browser, base class)~~ ✅
3. ~~Implement Robinhood module~~ ✅ Tested and working
4. ~~Implement and test all 8 brokerages~~ ✅

### Phase 2: Chrome Extension — **You are here**

1. ~~Create manifest.json, popup scaffold, service worker~~ ✅
2. ~~Port brokerage DOM selectors from Playwright into content scripts~~ ✅
3. ~~Implement download tracking via `chrome.storage.local`~~ ✅
4. ~~Add import/export for syncing with `download_log.json`~~ ✅
5. Test each brokerage

#### How the Chrome Extension Works

The extension requires you to **import your `download_log.json`** before downloading. This ensures statements you've already downloaded (via Playwright or a previous session) are skipped.

**First-time flow:**

1. Navigate to any supported brokerage and click the extension icon
2. The popup prompts you to **"Import download_log.json"**
3. Click the import button and select your `~/Downloads/Statements/download_log.json`
   - If you don't have one yet (first time ever), cancel the file picker and the extension creates a fresh `download_log.json` at `~/Downloads/Statements/` for you
4. After import, the popup shows how many statements are tracked and enables the **"Download Statements"** button
5. Click **"Download Statements"** — the extension scrapes the page and downloads new PDFs
6. When finished, the extension auto-exports the updated `download_log.json` back to `~/Downloads/Statements/`

**Subsequent runs:** The extension remembers your log in `chrome.storage`. Click the extension, see the green status bar, and click "Download Statements" immediately. Use "Import Log" anytime to re-sync with the file on disk (e.g., after running Playwright).

#### Setting Up the Chrome Extension

1. **Load the extension in Chrome/Brave**:
   - Open Chrome/Brave → go to `chrome://extensions/`
   - Enable **"Developer mode"** (top right toggle)
   - Click **"Load unpacked"**
   - Select the `chrome-extension/` folder (not a subfolder!)
   - The extension icon should appear in your toolbar

2. **Browser settings (important!)**:
   - Go to **Settings → Downloads** (or `chrome://settings/downloads`)
   - Turn **OFF** "Ask where to save each file before downloading"
   - This lets the extension automatically save to `~/Downloads/Statements/`

3. **Use the extension**:
   - Navigate to a brokerage (e.g., `robinhood.com/account/documents`)
   - Log in manually if needed
   - Click the extension icon in your browser toolbar
   - Import your `download_log.json` (first time only)
   - Click **"Download Statements"**
   - Files save to `~/Downloads/Statements/<BrokerageName>/`

4. **Sync with Playwright**:
   - The extension auto-exports `download_log.json` after every download session
   - Both Playwright and the extension read/write the same `~/Downloads/Statements/download_log.json`
   - After running Playwright, click "Import Log" in the extension to pick up new entries

### Phase 3: Safari Extension (iOS)

1. Create Xcode project using Safari Extension App template
2. Use Apple's `safari-web-extension-converter` to convert Chrome extension
3. Implement `SafariWebExtensionHandler.swift` (native messaging bridge)
4. Implement file saving to iOS Files app
5. Sideload via Xcode for personal use

## Potential Challenges

| Challenge | Mitigation |
|-----------|-----------|
| Bot detection (Playwright) | Persistent browser context, real Chrome profile, disable automation flags |
| DOM changes over time | Keep selectors in a dedicated section per brokerage for easy updates |
| Statement pagination/scrolling | Handle per-brokerage (scroll + "View More" clicks) |
| Download URL expiration | Download immediately after extracting URL, don't batch |
| Rate limiting | 2-second delay between downloads (configurable in `config.py`) |
| File location | Both Playwright and Chrome Extension use ~/Downloads/Statements/ |
| iOS file system restrictions | Save to app's Files-visible document directory |
| IBKR modal closes after each download | Re-opens modal and re-selects Monthly period for each statement |

## Verification

- **Idempotency**: Run twice in a row — second run should download 0 new statements
- **Multi-account**: Verify each account's statements are saved with the correct label in the filename
- **Download log**: Check `~/Downloads/Statements/download_log.json` for correct entries after each run

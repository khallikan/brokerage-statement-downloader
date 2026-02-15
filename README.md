# Brokerage Statement Downloader

Automated tool to download monthly statements from all your brokerage accounts. Supports 8 brokerages with a unified framework. You log in manually (including 2FA), and the automation handles the rest — navigating to statements, detecting all accounts, and downloading every PDF.

## Supported Brokerages

| Slug | Brokerage | Status |
|------|-----------|--------|
| `robinhood` | Robinhood | Tested |
| `schwab` | Charles Schwab | Needs testing |
| `etrade` | E*Trade | Needs testing |
| `fidelity` | Fidelity | Needs testing |
| `webull` | Webull | Needs testing |
| `m1finance` | M1 Finance | Needs testing |
| `vanguard` | Vanguard | Needs testing |
| `ibkr` | Interactive Brokers | Needs testing |

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

Statements are saved to `~/Documents/Statements/` organized by brokerage:

```
~/Documents/Statements/
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

**Known limitation:** `chrome.downloads` saves relative to the system Downloads folder. Files will land in `~/Downloads/Statements/` instead of `~/Documents/Statements/`. Workaround: create a symlink.

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

### Phase 1: Playwright (current) ← **You are here**

1. ~~Create project scaffold~~ ✅
2. ~~Implement core framework (config, tracker, browser, base class)~~ ✅
3. ~~Implement Robinhood module~~ ✅ Tested and working
4. Test and fix remaining 7 brokerages (Schwab, Fidelity, E*Trade, Vanguard, Webull, M1 Finance, IBKR)

### Phase 2: Chrome Extension

1. Create manifest.json, popup scaffold, service worker
2. Port brokerage DOM selectors from Playwright into content scripts
3. Implement download tracking via `chrome.storage.local`
4. Add export/import for syncing with `download_log.json`

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
| Chrome extension can't save to ~/Documents | Save to ~/Downloads/Statements/ + optional symlink |
| iOS file system restrictions | Save to app's Files-visible document directory |
| IBKR complexity | May need to use their Flex Web Service API as alternative |

## Verification

- **Idempotency**: Run twice in a row — second run should download 0 new statements
- **Multi-account**: Verify each account's statements are saved with the correct label in the filename
- **Download log**: Check `~/Documents/Statements/download_log.json` for correct entries after each run

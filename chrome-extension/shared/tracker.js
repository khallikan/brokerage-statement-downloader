// Download tracker using chrome.storage.local
// Mirrors the download_log.json structure from Playwright

class DownloadTracker {
  constructor() {
    this.log = null;
  }

  async init() {
    const result = await chrome.storage.local.get('downloadLog');
    this.log = result.downloadLog || {
      version: 1,
      lastUpdated: new Date().toISOString(),
      brokerages: {}
    };
    return this.log;
  }

  async save() {
    this.log.lastUpdated = new Date().toISOString();
    await chrome.storage.local.set({ downloadLog: this.log });
  }

  // Check if a statement has already been downloaded
  isDownloaded(brokerageSlug, accountLabel, statementDate) {
    const brokerage = this.log.brokerages[brokerageSlug];
    if (!brokerage) return false;

    const account = brokerage.accounts[accountLabel];
    if (!account) return false;

    return account.statements.some(s => s.statementDate === statementDate);
  }

  // Record a downloaded statement
  async recordDownload(brokerageSlug, accountLabel, statementDate, filename, fileSize = 0) {
    // Initialize brokerage if needed
    if (!this.log.brokerages[brokerageSlug]) {
      this.log.brokerages[brokerageSlug] = {
        displayName: brokerageSlug,
        folderName: brokerageSlug,
        accounts: {}
      };
    }

    const brokerage = this.log.brokerages[brokerageSlug];

    // Initialize account if needed
    if (!brokerage.accounts[accountLabel]) {
      brokerage.accounts[accountLabel] = {
        accountLabel: accountLabel,
        accountType: 'Brokerage',
        accountNumberLast4: '0000',
        statements: []
      };
    }

    const account = brokerage.accounts[accountLabel];

    // Add statement record
    account.statements.push({
      statementDate: statementDate,
      filename: filename,
      downloadedAt: new Date().toISOString(),
      downloadedBy: 'chrome-extension',
      fileSizeBytes: fileSize,
      sha256: ''
    });

    await this.save();
  }

  // Export log to JSON
  export() {
    return JSON.stringify(this.log, null, 2);
  }

  // Import log from JSON
  async import(jsonString) {
    const imported = JSON.parse(jsonString);
    this.log = imported;
    await this.save();
  }
}

// Helper to parse dates from various formats
function parseStatementDate(text) {
  if (!text) return null;

  const monthMap = {
    january: '01', february: '02', march: '03', april: '04',
    may: '05', june: '06', july: '07', august: '08',
    september: '09', october: '10', november: '11', december: '12',
    jan: '01', feb: '02', mar: '03', apr: '04',
    jun: '06', jul: '07', aug: '08',
    sep: '09', oct: '10', nov: '11', dec: '12'
  };

  const textLower = text.toLowerCase().trim();

  // Pattern: "January 2024" or "Jan 2024"
  for (const [month, num] of Object.entries(monthMap)) {
    const match = textLower.match(new RegExp(`${month}\\s+(\\d{4})`));
    if (match) {
      return `${match[1]}-${num}`;
    }
  }

  // Pattern: "01/31/2024" or "1/31/2024"
  const m = text.match(/(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})/);
  if (m) {
    return `${m[3]}-${m[1].padStart(2, '0')}`;
  }

  // Pattern: "2024-01"
  const ym = text.match(/(\d{4})-(\d{2})/);
  if (ym) {
    return `${ym[1]}-${ym[2]}`;
  }

  return null;
}

// Make account label safe for filenames
function makeAccountLabel(accountType, last4) {
  const base = accountType.toLowerCase().replace(/[^a-z0-9]/g, '');
  return `${base}${last4}`;
}

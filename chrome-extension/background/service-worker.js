// Service worker for Brokerage Statement Downloader
// Handles communication between popup and content scripts

// Store for status updates
let currentStatus = { message: '', type: 'info', brokerage: null };
let progressLog = [];

// Persist status to chrome.storage so progress.html can read it
function persistStatus(message, type, brokerage) {
  currentStatus = { message, type: type || 'info', brokerage: brokerage || currentStatus.brokerage };
  progressLog.push({ message, type: type || 'info', time: new Date().toISOString() });
  chrome.storage.local.set({
    downloadStatus: message,
    currentStep: message,
    progressLog: progressLog
  }).catch(() => {});
  // Also broadcast to popup if open
  chrome.runtime.sendMessage({ action: 'status_update', ...currentStatus }).catch(() => {});
}

// Cancel flag — set by popup to abort a running download session
let cancelRequested = false;

// Constants
const STATEMENTS_FOLDER = 'Statements';

// Ensure download_log.json exists in Chrome storage
async function ensureStatementsFolder() {
  try {
    const { downloadLog } = await chrome.storage.local.get('downloadLog');
    if (!downloadLog) {
      const log = { version: 1, lastUpdated: new Date().toISOString(), brokerages: {} };
      await chrome.storage.local.set({ downloadLog: log });
    }
  } catch (e) {
    console.log('Could not ensure statements folder:', e.message);
  }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.action === 'status_update') {
    currentStatus = {
      message: message.message,
      type: message.type || 'info',
      brokerage: message.brokerage
    };
    chrome.runtime.sendMessage({
      action: 'status_update',
      ...currentStatus
    }).catch(() => {});
    return;
  }

  if (message.action === 'get_status') {
    sendResponse(currentStatus);
    return true;
  }

  if (message.action === 'cancel_download') {
    cancelRequested = true;
    // Also set in storage so content scripts can check it
    chrome.storage.local.set({ cancelRequested: true }).then(() => {
      persistStatus('Cancelling...', 'info');
      sendResponse({ success: true });
    });
    return true;
  }

  if (message.action === 'download_statements') {
    cancelRequested = false;
    chrome.storage.local.set({ cancelRequested: false }).then(() => {
      handleDownload(message.brokerage, message.tabId)
        .then(results => sendResponse({ success: true, results, status: currentStatus }))
        .catch(error => sendResponse({ success: false, error: error.message, status: currentStatus }));
    });
    return true;
  }

  if (message.action === 'get_download_log') {
    chrome.storage.local.get('downloadLog').then(result => {
      sendResponse(result.downloadLog || { version: 1, lastUpdated: new Date().toISOString(), brokerages: {} });
    });
    return true;
  }

  if (message.action === 'save_download_log') {
    chrome.storage.local.set({ downloadLog: message.log }).then(() => {
      sendResponse({ success: true });
    });
    return true;
  }
});

// Statements URLs for each brokerage (correct URLs from Playwright)
const STATEMENTS_URLS = {
  robinhood: 'https://robinhood.com/account/reports-statements/',
  schwab: 'https://client.schwab.com/app/accounts/statements/',
  etrade: 'https://us.etrade.com/etx/pxy/accountdocs?inav=nav:documents#/documents',
  fidelity: 'https://digital.fidelity.com/ftgw/digital/portfolio/documents',
  webull: 'https://www.webull.com/center/tax',
  m1finance: 'https://dashboard.m1.com/d/settings/documents/statements',
  vanguard: 'https://statements.web.vanguard.com/',
  ibkr: 'https://portal.interactivebrokers.com/AccountManagement/AmAuthentication?action=Statements'
};

// Handler registry
const HANDLERS = {
  robinhood: { name: 'Robinhood', folderName: 'Robinhood' },
  schwab: { name: 'Charles Schwab', folderName: 'Schwab' },
  etrade: { name: 'E*Trade', folderName: 'ETrade' },
  fidelity: { name: 'Fidelity', folderName: 'Fidelity' },
  webull: { name: 'Webull', folderName: 'Webull' },
  m1finance: { name: 'M1 Finance', folderName: 'M1Finance' },
  vanguard: { name: 'Vanguard', folderName: 'Vanguard' },
  ibkr: { name: 'Interactive Brokers', folderName: 'InteractiveBrokers' }
};

const BROKERAGE_NAMES = {
  robinhood: 'Robinhood', schwab: 'Schwab', etrade: 'ETrade',
  fidelity: 'Fidelity', webull: 'Webull', m1finance: 'M1Finance',
  vanguard: 'Vanguard', ibkr: 'InteractiveBrokers'
};

// Detect available account tabs on Robinhood statements page
async function detectRobinhoodAccounts(tabId) {
  const baseUrl = 'https://robinhood.com/account/reports-statements';

  // Navigate to main statements page first
  persistStatus('Detecting available accounts...', 'info', 'robinhood');
  await chrome.tabs.update(tabId, { url: baseUrl });
  await waitForPageReady(tabId);

  // Ask content script to find available account tabs
  let response;
  try {
    response = await chrome.tabs.sendMessage(tabId, {
      action: 'detect_account_tabs'
    });
  } catch (e) {
    console.log('Service worker: Could not detect tabs, falling back to individual:', e.message);
    // Fallback to just individual
    return [`${baseUrl}/individual`];
  }

  const availableTabs = response?.tabs || [];

  console.log('Service worker: Detected Robinhood accounts:', availableTabs);

  // Build full URLs for detected accounts
  const urls = availableTabs.map(accountType => `${baseUrl}/${accountType}`);

  // If no accounts detected, fallback to individual
  if (urls.length === 0) {
    console.log('Service worker: No accounts detected, using individual');
    return [`${baseUrl}/individual`];
  }

  return urls;
}

// Auto-export download_log.json to ~/Downloads/Statements/
async function autoExportLog(log) {
  console.log('Auto-export: Starting export of download_log.json...');
  try {
    const jsonStr = JSON.stringify(log, null, 2);
    console.log('Auto-export: JSON size:', jsonStr.length, 'bytes');
    const dataUrl = 'data:application/json;base64,' + btoa(unescape(encodeURIComponent(jsonStr)));
    const downloadId = await chrome.downloads.download({
      url: dataUrl,
      filename: 'Statements/download_log.json',
      conflictAction: 'overwrite',
      saveAs: false
    });
    console.log('Auto-export: Download started, id:', downloadId);
    await waitForDownload(downloadId);
    console.log('Auto-export: Successfully exported download_log.json');
  } catch (e) {
    console.log('Auto-export FAILED:', e.message);
  }
}

// ============================================================
// DOWNLOAD HELPER - downloads a batch of statements for a brokerage
// ============================================================

async function downloadStatementsForBrokerage(statements, brokerage, brokerageSlug, tabId, pageUrl, log) {
  const results = [];
  const totalStatements = statements.length;

  for (let i = 0; i < statements.length; i++) {
    const stmt = statements[i];
    if (cancelRequested) break;

    const position = i + 1;
    persistStatus(`Processing ${position}/${totalStatements}: ${stmt.date}`, 'info', brokerageSlug);

    const accountKey = stmt.accountLabel || 'individual0000';
    if (!brokerage.accounts[accountKey]) {
      brokerage.accounts[accountKey] = {
        accountLabel: accountKey,
        accountType: stmt.accountType || 'Brokerage',
        accountNumberLast4: stmt.accountLast4 || '0000',
        statements: []
      };
    }
    const account = brokerage.accounts[accountKey];

    // Skip if this date already exists in the log with a known file size
    // Also check for multi-month statements that may cover this date range
    const existingEntry = account.statements.find(s => {
      // Exact match
      if (s.statementDate === stmt.date && s.fileSizeBytes > 0) {
        return true;
      }
      // Multi-month check: see if stmt.date falls within any multi-month statement
      if (s.statementDate && s.statementDate.includes('-') && stmt.date.includes('-')) {
        // Parse both dates to see if there's overlap
        // For multi-month format like "YYYY-MM-MM", check if stmt.date month falls in range
        const [stmtYear, stmtMonth] = stmt.date.split('-');
        if (stmtYear && stmtMonth && stmtMonth.length === 2) {
          const [existingYear, existingStartMonth, existingEndMonth] = s.statementDate.split('-');
          if (existingYear === stmtYear && existingStartMonth && existingEndMonth) {
            // Check if stmt month falls within existing statement range
            const stmtMonthNum = parseInt(stmtMonth);
            const existingStartNum = parseInt(existingStartMonth);
            const existingEndNum = parseInt(existingEndMonth);

            // Handle case where existingEndMonth might be just a month (not multi-month format)
            if (existingEndMonth.length === 2) {
              // Multi-month format: "YYYY-MM-MM"
              if (stmtMonthNum >= existingStartNum && stmtMonthNum <= existingEndNum) {
                return true;
              }
            } else {
              // Single month format - already handled by exact match above
            }
          }
        }
      }
      return false;
    });

    if (existingEntry) {
      console.log('Service worker: Skipping - already in log:', stmt.date, existingEntry.filename);
      persistStatus(`Skipping ${stmt.date} (already downloaded)`, 'info', brokerageSlug);
      results.push({ filename: existingEntry.filename, downloaded: false, alreadyExists: true });
      continue;
    }

    const finalFilename = stmt.filename;
    persistStatus(`Downloading ${finalFilename}...`, 'info', brokerageSlug);

    let downloadResult = { success: false, fileSize: 0 };
    let attempts = 0;
    const maxAttempts = 3;

    while (!downloadResult.success && attempts < maxAttempts) {
      attempts++;
      console.log(`Service worker: Download attempt ${attempts}/${maxAttempts} for ${finalFilename}`);

      // For Fidelity, use dedicated two-step click handler (icon button → popup → Download as PDF)
      if (brokerageSlug === 'fidelity' && stmt.needsClick) {
        console.log(`Service worker: Trying Fidelity click method for date: ${stmt.date}`);
        downloadResult = await downloadByFidelityClick(tabId, stmt.date, brokerage.folderName, finalFilename);
        await ensureTabOnPage(tabId, pageUrl);
      }
      // For other brokerages, prioritize click method if needed
      else if (stmt.needsClick && attempts === 1) {
        console.log(`Service worker: Trying click method for date: ${stmt.date} (priority for ${brokerageSlug})`);
        downloadResult = await downloadByClick(tabId, stmt.date, brokerage.folderName, finalFilename);
        await ensureTabOnPage(tabId, pageUrl);
      }
      // Then try URL download as fallback (or if URL download is preferred)
      else if (stmt.url && !downloadResult.success) {
        console.log(`Service worker: Trying URL download: ${stmt.url.substring(0, 60)}...`);
        downloadResult = await downloadByUrl(stmt.url, brokerage.folderName, finalFilename);
      }
      // Finally try click method if URL failed and statement needs click
      else if (!downloadResult.success && stmt.needsClick) {
        console.log(`Service worker: Trying click method for date: ${stmt.date} (fallback)`);
        downloadResult = await downloadByClick(tabId, stmt.date, brokerage.folderName, finalFilename);
        await ensureTabOnPage(tabId, pageUrl);
      }

      if (!downloadResult.success && attempts < maxAttempts) {
        console.log(`Service worker: Waiting 2s before retry...`);
        await new Promise(resolve => setTimeout(resolve, 2000));
      }
    }

    if (downloadResult.success) {
      account.statements.push({
        statementDate: stmt.date,
        filename: finalFilename,
        downloadedAt: new Date().toISOString(),
        downloadedBy: 'chrome-extension',
        fileSizeBytes: downloadResult.fileSize,
        sha256: downloadResult.sha256 || ''
      });
      results.push({ filename: finalFilename, downloaded: true });
      persistStatus(`Downloaded ${finalFilename}`, 'success', brokerageSlug);
    } else {
      results.push({ filename: finalFilename, error: downloadResult.error || 'Download failed' });
      persistStatus(`Failed: ${finalFilename}`, 'error', brokerageSlug);
    }

    // Periodically save log to avoid losing progress
    if (i % 10 === 9) {
      log.brokerages[brokerageSlug] = brokerage;
      log.lastUpdated = new Date().toISOString();
      await chrome.storage.local.set({ downloadLog: log });
    }
  }

  return results;
}

// ============================================================
// MAIN DOWNLOAD FLOW
// ============================================================

async function handleDownload(brokerageSlug, tabId) {
  await ensureStatementsFolder();
  progressLog = [];
  await chrome.storage.local.set({ downloadComplete: false });
  persistStatus('Starting download...', 'info', brokerageSlug);

  const { downloadLog } = await chrome.storage.local.get('downloadLog');
  let log = downloadLog || { version: 1, lastUpdated: new Date().toISOString(), brokerages: {} };

  if (!tabId) throw new Error('No tab ID available');

  // Determine URLs to scrape
  let urlsToScrape = [];

  // For Robinhood, first detect available account tabs, then scrape each
  if (brokerageSlug === 'robinhood') {
    urlsToScrape = await detectRobinhoodAccounts(tabId);
  } else if (HANDLERS[brokerageSlug]?.accountUrls) {
    urlsToScrape = HANDLERS[brokerageSlug].accountUrls.map(a => a.url);
  } else {
    const mainUrl = STATEMENTS_URLS[brokerageSlug];
    if (mainUrl) urlsToScrape = [mainUrl];
  }

  if (urlsToScrape.length === 0) throw new Error('No URLs configured for ' + brokerageSlug);

  const results = [];

  // Initialize brokerage in log
  if (!log.brokerages[brokerageSlug]) {
    const folderName = BROKERAGE_NAMES[brokerageSlug] || brokerageSlug;
    log.brokerages[brokerageSlug] = { displayName: folderName, folderName, accounts: {} };
  }
  const brokerage = log.brokerages[brokerageSlug];

  // Schwab: per-account scraping - scrape one account, download its PDFs, then next account
  if (brokerageSlug === 'schwab') {
    const schwabUrl = urlsToScrape[0];
    persistStatus('Navigating to Schwab statements...', 'info', brokerageSlug);
    await chrome.tabs.update(tabId, { url: schwabUrl });
    await waitForPageReady(tabId);
    await new Promise(resolve => setTimeout(resolve, 3000));

    // Discover accounts
    persistStatus('Discovering Schwab accounts...', 'info', brokerageSlug);
    let accountsResponse;
    for (let attempt = 0; attempt < 5; attempt++) {
      try {
        accountsResponse = await chrome.tabs.sendMessage(tabId, {
          action: 'schwab_get_accounts'
        });
        if (accountsResponse?.success) break;
      } catch (e) {
        console.log('Service worker: Retry schwab_get_accounts attempt', attempt + 1, '-', e.message);
        await new Promise(resolve => setTimeout(resolve, 3000));
      }
    }

    const schwabAccounts = accountsResponse?.accounts || [];
    if (schwabAccounts.length === 0) throw new Error('No Schwab accounts found');
    console.log('Service worker: Found', schwabAccounts.length, 'Schwab accounts');
    persistStatus(`Found ${schwabAccounts.length} Schwab accounts`, 'info', brokerageSlug);

    for (let acctIdx = 0; acctIdx < schwabAccounts.length; acctIdx++) {
      if (cancelRequested) {
        persistStatus('Cancelled', 'info', brokerageSlug);
        break;
      }

      const acct = schwabAccounts[acctIdx];
      persistStatus(`Account ${acctIdx + 1}/${schwabAccounts.length}: Preparing ${acct.label}...`, 'info', brokerageSlug);

      // Step 1: Prepare the account (select, set filters, search)
      let prepareResponse;
      for (let attempt = 0; attempt < 5; attempt++) {
        try {
          prepareResponse = await chrome.tabs.sendMessage(tabId, {
            action: 'schwab_prepare_account',
            account: acct
          });
          if (prepareResponse?.success) break;
        } catch (e) {
          console.log('Service worker: Retry schwab_prepare_account attempt', attempt + 1, '-', e.message);
          await new Promise(resolve => setTimeout(resolve, 3000));
        }
      }

      if (!prepareResponse?.success) {
        persistStatus(`Failed to prepare account ${acct.label}`, 'error', brokerageSlug);
        continue;
      }

      // Step 2: Scrape page by page, downloading each page's statements before moving on
      let pageNum = 0;
      let accountTotal = 0;
      while (true) {
        if (cancelRequested) break;
        pageNum++;

        persistStatus(`Account ${acct.label}: Scraping page ${pageNum}...`, 'info', brokerageSlug);

        // Scrape current page
        let pageResponse;
        for (let attempt = 0; attempt < 3; attempt++) {
          try {
            pageResponse = await chrome.tabs.sendMessage(tabId, {
              action: 'schwab_scrape_page',
              account: acct
            });
            if (pageResponse?.success) break;
          } catch (e) {
            console.log('Service worker: Retry schwab_scrape_page attempt', attempt + 1, '-', e.message);
            await new Promise(resolve => setTimeout(resolve, 2000));
          }
        }

        const pageStatements = pageResponse?.statements || [];
        console.log('Service worker: Account', acct.label, 'page', pageNum, ':', pageStatements.length, 'statements');

        if (pageStatements.length > 0) {
          persistStatus(`Account ${acct.label} page ${pageNum}: ${pageStatements.length} statements — downloading...`, 'info', brokerageSlug);

          // Download THIS page's statements before moving to next page
          const pageResults = await downloadStatementsForBrokerage(
            pageStatements, brokerage, brokerageSlug, tabId, schwabUrl, log
          );
          results.push(...pageResults);
          accountTotal += pageStatements.length;
        }

        // Step 3: Try to go to next page
        let nextResponse;
        try {
          nextResponse = await chrome.tabs.sendMessage(tabId, {
            action: 'schwab_next_page'
          });
        } catch (e) {
          console.log('Service worker: schwab_next_page error:', e.message);
        }

        if (!nextResponse?.hasNext) {
          console.log('Service worker: No more pages for account', acct.label);
          break;
        }
      }

      persistStatus(`Account ${acct.label}: Done — ${accountTotal} statements processed`, 'info', brokerageSlug);
    }

    // Save log and finish
    log.brokerages[brokerageSlug] = brokerage;
    log.lastUpdated = new Date().toISOString();
    await chrome.storage.local.set({ downloadLog: log });
    await autoExportLog(log);

    const downloadedCount = results.filter(r => r.downloaded).length;
    persistStatus(
      cancelRequested
        ? `Cancelled. Downloaded ${downloadedCount} files`
        : `Complete! Downloaded ${downloadedCount} files`,
      'success', brokerageSlug
    );
    await chrome.storage.local.set({
      downloadComplete: true,
      cancelRequested: false
    });

    return results;
  }

  // Fidelity: per-year scraping - scrape one year, download its PDFs, then next year
  if (brokerageSlug === 'fidelity') {
    const fidelityUrl = urlsToScrape[0];
    persistStatus('Navigating to Fidelity statements...', 'info', brokerageSlug);
    await chrome.tabs.update(tabId, { url: fidelityUrl });
    await waitForPageReady(tabId);
    await new Promise(resolve => setTimeout(resolve, 3000));

    // Discover years
    persistStatus('Discovering Fidelity years...', 'info', brokerageSlug);
    let yearsResponse;
    for (let attempt = 0; attempt < 5; attempt++) {
      try {
        yearsResponse = await chrome.tabs.sendMessage(tabId, {
          action: 'fidelity_get_years'
        });
        if (yearsResponse?.success) break;
      } catch (e) {
        console.log('Service worker: Retry fidelity_get_years attempt', attempt + 1, '-', e.message);
        await new Promise(resolve => setTimeout(resolve, 3000));
      }
    }

    const fidelityYears = yearsResponse?.years || [];
    if (fidelityYears.length === 0) {
      // If no years found, fall back to default scraping
      persistStatus('No Fidelity years found, using default scraping', 'info', brokerageSlug);
    } else {
      console.log('Service worker: Found', fidelityYears.length, 'Fidelity years');
      persistStatus(`Found ${fidelityYears.length} Fidelity years`, 'info', brokerageSlug);

      // Process each year (newest first)
      fidelityYears.sort((a, b) => parseInt(b) - parseInt(a));

      // Add current year if not present to ensure we check for latest statements
      const currentYear = new Date().getFullYear();
      if (!fidelityYears.includes(String(currentYear))) {
        fidelityYears.push(String(currentYear));
        console.log('Service worker: Added current year', currentYear, 'to Fidelity years');
      }

      for (let yearIdx = 0; yearIdx < fidelityYears.length; yearIdx++) {
        if (cancelRequested) {
          persistStatus('Cancelled', 'info', brokerageSlug);
          break;
        }

        const year = fidelityYears[yearIdx];
        persistStatus(`Year ${yearIdx + 1}/${fidelityYears.length}: Preparing ${year}...`, 'info', brokerageSlug);

        // Step 1: Prepare the year (select, load more)
        let prepareResponse;
        for (let attempt = 0; attempt < 5; attempt++) {
          try {
            prepareResponse = await chrome.tabs.sendMessage(tabId, {
              action: 'fidelity_prepare_year',
              year: year
            });
            if (prepareResponse?.success) break;
          } catch (e) {
            console.log('Service worker: Retry fidelity_prepare_year attempt', attempt + 1, '-', e.message);
            await new Promise(resolve => setTimeout(resolve, 3000));
          }
        }

        if (!prepareResponse?.success) {
          persistStatus(`Failed to prepare year ${year}`, 'error', brokerageSlug);
          continue;
        }

        // Step 2: Scrape the page for this year
        persistStatus(`Year ${year}: Scraping statements...`, 'info', brokerageSlug);

        // Scrape current page
        let pageResponse;
        for (let attempt = 0; attempt < 3; attempt++) {
          try {
            pageResponse = await chrome.tabs.sendMessage(tabId, {
              action: 'fidelity_scrape_page'
            });
            if (pageResponse?.success) break;
          } catch (e) {
            console.log('Service worker: Retry fidelity_scrape_page attempt', attempt + 1, '-', e.message);
            await new Promise(resolve => setTimeout(resolve, 2000));
          }
        }

        const pageStatements = pageResponse?.statements || [];
        console.log('Service worker: Year', year, ':', pageStatements.length, 'statements');

        if (pageStatements.length > 0) {
          persistStatus(`Year ${year}: ${pageStatements.length} statements — downloading...`, 'info', brokerageSlug);

          // Download THIS year's statements
          const pageResults = await downloadStatementsForBrokerage(
            pageStatements, brokerage, brokerageSlug, tabId, fidelityUrl, log
          );
          results.push(...pageResults);
        }

        persistStatus(`Year ${year}: Done — ${pageStatements.length} statements processed`, 'info', brokerageSlug);
      }
    }

    // If no years were found or processed, fall back to default scraping
    if (fidelityYears.length === 0) {
      // Fall back to default scraping behavior
      persistStatus('Using default Fidelity scraping...', 'info', brokerageSlug);

      // Ask content script to SCRAPE ONLY (no clicking)
      let response;
      for (let attempt = 0; attempt < 5; attempt++) {
        try {
          response = await chrome.tabs.sendMessage(tabId, {
            action: 'scrape_statements',
            brokerage: brokerageSlug
          });
          if (response) break;
        } catch (e) {
          console.log('Service worker: Retry', attempt + 1, '-', e.message);
          await new Promise(resolve => setTimeout(resolve, 3000));
        }
      }

      const statements = response?.statements || [];
      if (statements.length > 0) {
        console.log('Service worker: Found', statements.length, 'statements using default scraping');

        // Group statements by account for better logging
        const accountStats = {};
        statements.forEach(stmt => {
          const label = stmt.accountLabel || 'unknown';
          accountStats[label] = (accountStats[label] || 0) + 1;
        });
        const accountSummary = Object.entries(accountStats).map(([label, count]) => `${label}: ${count}`).join(', ');

        persistStatus(`Found ${statements.length} statements (${accountSummary})`, 'info', brokerageSlug);
        await chrome.storage.local.set({ statementsFound: statements.length });

        // Download statements using shared helper
        const urlResults = await downloadStatementsForBrokerage(
          statements, brokerage, brokerageSlug, tabId, fidelityUrl, log
        );
        results.push(...urlResults);
      }
    }

    // Save log and finish
    log.brokerages[brokerageSlug] = brokerage;
    log.lastUpdated = new Date().toISOString();
    await chrome.storage.local.set({ downloadLog: log });
    await autoExportLog(log);

    const downloadedCount = results.filter(r => r.downloaded).length;
    persistStatus(
      cancelRequested
        ? `Cancelled. Downloaded ${downloadedCount} files`
        : `Complete! Downloaded ${downloadedCount} files`,
      'success', brokerageSlug
    );
    await chrome.storage.local.set({
      downloadComplete: true,
      cancelRequested: false
    });

    return results;
  }

  // Process each URL (non-Schwab, non-Fidelity brokerages)
  for (let urlIdx = 0; urlIdx < urlsToScrape.length; urlIdx++) {
    if (cancelRequested) {
      persistStatus('Cancelled', 'info', brokerageSlug);
      break;
    }

    const url = urlsToScrape[urlIdx];
    const urlParts = url.split('/').filter(p => p);  // Remove empty parts
    const accountName = urlParts.pop() || 'main';
    persistStatus(`Scraping ${accountName} (${urlIdx + 1}/${urlsToScrape.length})`, 'info', brokerageSlug);

    console.log('Service worker: Navigating to', url);
    await chrome.tabs.update(tabId, { url });
    await waitForPageReady(tabId);
    // Additional wait for page to fully load and elements to be ready
    await new Promise(resolve => setTimeout(resolve, 3000));

    // Ask content script to SCRAPE ONLY (no clicking)
    let response;
    for (let attempt = 0; attempt < 5; attempt++) {
      try {
        response = await chrome.tabs.sendMessage(tabId, {
          action: 'scrape_statements',
          brokerage: brokerageSlug
        });
        if (response) break;
      } catch (e) {
        console.log('Service worker: Retry', attempt + 1, '-', e.message);
        await new Promise(resolve => setTimeout(resolve, 3000));
      }
    }

    const statements = response?.statements || [];
    if (statements.length === 0) {
      console.log('Service worker: No statements found on', url);
      continue;
    }
    console.log('Service worker: Found', statements.length, 'statements on', url);

    // Group statements by account for better logging
    const accountStats = {};
    statements.forEach(stmt => {
      const label = stmt.accountLabel || 'unknown';
      accountStats[label] = (accountStats[label] || 0) + 1;
    });
    const accountSummary = Object.entries(accountStats).map(([label, count]) => `${label}: ${count}`).join(', ');

    persistStatus(`Found ${statements.length} statements on ${accountName} (${accountSummary})`, 'info', brokerageSlug);
    await chrome.storage.local.set({ statementsFound: statements.length });

    // Download statements using shared helper
    const urlResults = await downloadStatementsForBrokerage(
      statements, brokerage, brokerageSlug, tabId, url, log
    );
    results.push(...urlResults);
  }

  // Save log and auto-export
  log.brokerages[brokerageSlug] = brokerage;
  log.lastUpdated = new Date().toISOString();
  await chrome.storage.local.set({ downloadLog: log });
  await autoExportLog(log);

  const downloadedCount = results.filter(r => r.downloaded).length;
  persistStatus(
    cancelRequested
      ? `Cancelled. Downloaded ${downloadedCount} files`
      : `Complete! Downloaded ${downloadedCount} files`,
    'success', brokerageSlug
  );
  await chrome.storage.local.set({
    downloadComplete: true,
    cancelRequested: false
  });

  return results;
}

// ============================================================
// DOWNLOAD STRATEGIES
// ============================================================

// Download a file by URL using chrome.downloads.download (has browser cookies).
// After download, verify it's actually a PDF. If HTML, delete it and return false.
// Returns { success: true, fileSize: number } or { success: false, error: string }
async function downloadByUrl(url, folderName, filename) {
  // Check for cancellation before starting download
  if (cancelRequested) {
    return { success: false, error: 'Cancelled before starting URL download' };
  }

  try {
    const downloadId = await chrome.downloads.download({
      url: url,
      filename: `Statements/${folderName}/${filename}`,
      saveAs: false
    });

    // Wait for download with periodic cancellation checks
    await waitForDownload(downloadId);

    // Check for cancellation after download
    if (cancelRequested) {
      return { success: false, error: 'Cancelled after download started' };
    }

    // Verify the downloaded file is actually a PDF (not an HTML login page)
    const items = await chrome.downloads.search({ id: downloadId });
    if (items.length > 0) {
      const item = items[0];
      const mime = (item.mime || '').toLowerCase();
      const finalName = (item.filename || '').toLowerCase();

      // If the server returned HTML, delete the file
      if (mime.includes('html') || mime.includes('text/') ||
          finalName.endsWith('.html') || finalName.endsWith('.htm')) {
        console.log('Service worker: Downloaded HTML instead of PDF, deleting:', filename);
        await chrome.downloads.removeFile(downloadId);
        return { success: false, error: 'Downloaded HTML instead of PDF' };
      }

      // Also check file size — a real PDF is almost always > 1KB
      if (item.fileSize !== undefined && item.fileSize < 512) {
        console.log('Service worker: File too small, likely not a PDF:', item.fileSize, 'bytes');
        await chrome.downloads.removeFile(downloadId);
        return { success: false, error: 'File too small, not a PDF' };
      }

      console.log('Service worker: Downloaded via URL:', filename, 'size:', item.fileSize);
      return { success: true, fileSize: item.fileSize, downloadId };
    }

    console.log('Service worker: Downloaded via URL:', filename);
    return { success: true, fileSize: 0, downloadId };
  } catch (e) {
    console.log('Service worker: URL download failed:', e.message);
    return { success: false, error: e.message };
  }
}

// Download by clicking a statement link and capturing the PDF blob.
// Uses chrome.scripting.executeScript with world:'MAIN' to bypass CSP and
// intercept URL.createObjectURL and fetch responses. Then the content script clicks the element
// and we poll for the captured blob data.
async function downloadByClick(tabId, date, folderName, filename) {
  try {
    // Check for cancellation before starting
    if (cancelRequested) return { success: false, error: 'Cancelled before starting download' };

    // Step 1: Inject interceptor into the MAIN world (bypasses CSP)
    console.log('Service worker: Injecting blob interceptor for date:', date);
    await chrome.scripting.executeScript({
      target: { tabId },
      world: 'MAIN',
      func: () => {
        // Reset capture state
        window.__pdfBlobBase64 = null;
        window.__pdfBlobSize = 0;

        // Helper to capture blob
        function captureBlob(blob) {
          if (blob instanceof Blob && blob.size > 5000) {
            console.log('[Interceptor] Capturing blob, type:', blob.type, 'size:', blob.size);
            const reader = new FileReader();
            reader.onload = function() {
              const base64 = reader.result.split(',')[1];
              window.__pdfBlobBase64 = base64;
              window.__pdfBlobSize = blob.size;
              console.log('[Interceptor] Blob captured, base64 length:', base64.length);
            };
            reader.readAsDataURL(blob);
          }
        }

        // Only patch once
        if (!window.__origCreateObjectURL) {
          window.__origCreateObjectURL = URL.createObjectURL;
          URL.createObjectURL = function(obj) {
            const result = window.__origCreateObjectURL.call(URL, obj);
            captureBlob(obj);
            return result;
          };

          // Also intercept fetch and XMLHttpRequest
          if (!window.__origFetch) {
            window.__origFetch = window.fetch;
            window.__origXHROpen = XMLHttpRequest.prototype.open;
            window.__origXHRSend = XMLHttpRequest.prototype.send;

            // Patch fetch - capture response blobs
            window.fetch = function(...args) {
              return window.__origFetch.apply(this, args).then(response => {
                if (response.clone) {
                  response.clone().blob().then(blob => {
                    captureBlob(blob);
                  }).catch(() => {});
                }
                return response;
              });
            };

            // Patch XMLHttpRequest
            XMLHttpRequest.prototype.open = function(...args) {
              this.__url = args[1];
              return window.__origXHROpen.apply(this, args);
            };

            XMLHttpRequest.prototype.send = function(...args) {
              this.addEventListener('load', function() {
                if (this.response instanceof Blob && this.response.size > 5000) {
                  captureBlob(this.response);
                }
              });
              return window.__origXHRSend.apply(this, args);
            };
          }
        }
      }
    });

    // Check for cancellation after injecting interceptor
    if (cancelRequested) return { success: false, error: 'Cancelled after injecting interceptor' };

    // Step 2: Set up listeners for downloads triggered by Schwab
    let capturedDownload = null;
    let capturedDownloadResolve = null;

    // Listener for downloads that Schwab triggers directly
    const captureDownload = (downloadItem) => {
      console.log('Service worker: Download detected:', downloadItem.url?.substring(0, 80), '| state:', downloadItem.state);
      // Capture PDF downloads (not blob:, not already from our extension)
      if (downloadItem.url &&
          (downloadItem.url.includes('.pdf') || downloadItem.url.includes('s3.amazonaws.com') ||
           downloadItem.url.includes('schwab.com/documents') || downloadItem.url.includes('fidelity.com') ||
           downloadItem.url.includes('document')) &&
          !downloadItem.url.startsWith('data:')) {
        console.log('Service worker: Captured PDF download:', downloadItem.url.substring(0, 80));
        capturedDownload = downloadItem;
        if (capturedDownloadResolve) {
          capturedDownloadResolve(downloadItem);
          capturedDownloadResolve = null;
        }
      }
    };

    // Also cancel any blob downloads that might interfere
    const cancelBlobDownloads = (downloadItem) => {
      if (downloadItem.url && downloadItem.url.startsWith('blob:')) {
        console.log('Service worker: Cancelling blob download:', downloadItem.url.substring(0, 60));
        chrome.downloads.cancel(downloadItem.id).catch(() => {});
        chrome.downloads.erase({ id: downloadItem.id }).catch(() => {});
      }
    };

    chrome.downloads.onCreated.addListener(captureDownload);
    chrome.downloads.onCreated.addListener(cancelBlobDownloads);

    // Check for cancellation before clicking
    if (cancelRequested) {
      chrome.downloads.onCreated.removeListener(captureDownload);
      chrome.downloads.onCreated.removeListener(cancelBlobDownloads);
      return { success: false, error: 'Cancelled before clicking element' };
    }

    // Step 3: Tell content script to click the element
    let response;
    try {
      response = await chrome.tabs.sendMessage(tabId, { action: 'click_element', date });
    } catch (e) {
      chrome.downloads.onCreated.removeListener(captureDownload);
      chrome.downloads.onCreated.removeListener(cancelBlobDownloads);
      return { success: false, error: 'Click failed: ' + e.message };
    }

    if (!response?.success) {
      chrome.downloads.onCreated.removeListener(captureDownload);
      chrome.downloads.onCreated.removeListener(cancelBlobDownloads);
      return { success: false, error: 'Element not found for date: ' + date };
    }

    // Wait for click to trigger download before polling
    await new Promise(resolve => setTimeout(resolve, 2000));

    // Step 4: Poll for captured blob data OR direct downloads (up to 20 seconds)
    console.log('Service worker: Waiting for PDF blob capture or download...');
    let pdfBase64 = null;
    let pdfSize = 0;
    for (let i = 0; i < 40; i++) {
      // Check for cancellation periodically during the download process
      if (cancelRequested) {
        chrome.downloads.onCreated.removeListener(captureDownload);
        chrome.downloads.onCreated.removeListener(cancelBlobDownloads);
        return { success: false, error: 'Cancelled during download polling' };
      }

      await new Promise(resolve => setTimeout(resolve, 500));

      // Check if a direct download was captured
      if (capturedDownload) {
        console.log('Service worker: Using captured download:', capturedDownload.url?.substring(0, 80));
        const pdfUrl = capturedDownload.url;
        // Wait for download to complete
        try {
          await new Promise(resolve => {
            const checkComplete = (downloadDelta) => {
              if (downloadDelta.id === capturedDownload.id &&
                  (downloadDelta.state?.current === 'complete' || downloadDelta.error)) {
                chrome.downloads.onChanged.removeListener(checkComplete);
                resolve();
              }
            };
            chrome.downloads.onChanged.addListener(checkComplete);
            // Timeout after 10 seconds
            setTimeout(resolve, 10000);
          });
        } catch (e) { /* ignore */ }

        // Move the downloaded file to our folder
        if (capturedDownload.filename && !capturedDownload.filename.includes('Statements/')) {
          try {
            await chrome.downloads.move(capturedDownload.id, {
              filename: 'Statements/' + folderName + '/' + filename,
              conflictAction: 'overwrite'
            });
            console.log('Service worker: Moved download to Statements folder');
            chrome.downloads.onCreated.removeListener(captureDownload);
            chrome.downloads.onCreated.removeListener(cancelBlobDownloads);
            return { success: true, fileSize: capturedDownload.fileSize || 0 };
          } catch (e) {
            console.log('Service worker: Could not move download:', e.message);
          }
        }
        // If we captured a URL, try to re-download it properly
        if (capturedDownload.url) {
          chrome.downloads.onCreated.removeListener(captureDownload);
          chrome.downloads.onCreated.removeListener(cancelBlobDownloads);
          return await downloadByUrl(capturedDownload.url, folderName, filename);
        }
        capturedDownload = null;
      }

      try {
        const [result] = await chrome.scripting.executeScript({
          target: { tabId },
          world: 'MAIN',
          func: () => {
            return { base64: window.__pdfBlobBase64, size: window.__pdfBlobSize };
          }
        });
        if (result?.result?.base64) {
          pdfBase64 = result.result.base64;
          pdfSize = result.result.size;
          console.log('Service worker: Got PDF blob, size:', pdfSize);
          break;
        }
      } catch (e) {
        // Tab may have navigated, ignore
      }

      // Also check for new tabs with PDFs during polling
      // Fidelity opens PDFs in a new tab; detect those too
      try {
        const tabs = await chrome.tabs.query({});
        for (const tab of tabs) {
          if (!tab.url || tab.url === 'chrome://newtab/' || tab.id === tabId) continue;
          if (tab.url.includes('.pdf') || tab.url.includes('s3.amazonaws.com') || tab.url.includes('blob:') ||
              (tab.url.includes('fidelity.com') && (tab.url.includes('/ftgw/') || tab.url.includes('document')) && tab.id !== tabId)) {
            console.log('Service worker: Found PDF in new tab during polling:', tab.url.substring(0, 80));
            // Close the tab after getting the URL
            const pdfUrl = tab.url;
            await chrome.tabs.remove(tab.id).catch(() => {});
            // Try to download this URL
            if (pdfUrl.startsWith('http')) {
              chrome.downloads.onCreated.removeListener(captureDownload);
              chrome.downloads.onCreated.removeListener(cancelBlobDownloads);
              return await downloadByUrl(pdfUrl, folderName, filename);
            }
          }
        }
      } catch (e) {
        // Ignore tab query errors
      }
    }

    // Clean up download listeners
    await new Promise(resolve => setTimeout(resolve, 500));
    chrome.downloads.onCreated.removeListener(captureDownload);
    chrome.downloads.onCreated.removeListener(cancelBlobDownloads);

    // Clean up interceptor state
    try {
      await chrome.scripting.executeScript({
        target: { tabId },
        world: 'MAIN',
        func: () => { window.__pdfBlobBase64 = null; window.__pdfBlobSize = 0; }
      });
    } catch (e) { /* ignore */ }

    if (pdfBase64) {
      // Check for cancellation before saving the PDF
      if (cancelRequested) return { success: false, error: 'Cancelled before saving PDF' };

      // Compute SHA-256 hash from the raw bytes
      const binaryStr = atob(pdfBase64);
      const bytes = new Uint8Array(binaryStr.length);
      for (let i = 0; i < binaryStr.length; i++) bytes[i] = binaryStr.charCodeAt(i);
      const hashBuffer = await crypto.subtle.digest('SHA-256', bytes);
      const sha256 = Array.from(new Uint8Array(hashBuffer)).map(b => b.toString(16).padStart(2, '0')).join('');
      console.log('Service worker: PDF SHA-256:', sha256);

      // Save PDF using data URL
      const dataUrl = 'data:application/pdf;base64,' + pdfBase64;
      const filePath = 'Statements/' + folderName + '/' + filename;
      const downloadId = await chrome.downloads.download({
        url: dataUrl,
        filename: filePath,
        conflictAction: 'overwrite',
        saveAs: false
      });
      console.log('Service worker: PDF download started, id:', downloadId);
      await waitForDownload(downloadId);
      console.log('Service worker: PDF downloaded successfully:', filePath);
      return { success: true, fileSize: pdfSize, sha256 };
    }

    // Check for cancellation before fallback attempts
    if (cancelRequested) return { success: false, error: 'Cancelled during fallback attempts' };

    // Fallback: Try to find PDF URL from the page after click
    console.log('Service worker: No PDF blob captured, trying to find PDF URL from page');
    try {
      const [result] = await chrome.scripting.executeScript({
        target: { tabId },
        world: 'MAIN',
        func: () => {
          // Look for any PDF links on the page
          const links = document.querySelectorAll('a[href*=".pdf"], a[href*="document"], a[href*="statement"]');
          for (const link of links) {
            const href = link.getAttribute('href');
            if (href && (href.includes('.pdf') || href.includes('document') || href.includes('statement'))) {
              return href;
            }
          }
          return null;
        }
      });
      if (result?.result) {
        let pdfUrl = result.result;
        if (pdfUrl.startsWith('/')) {
          pdfUrl = 'https://client.schwab.com' + pdfUrl;
        }
        console.log('Service worker: Found PDF URL from page:', pdfUrl);
        return await downloadByUrl(pdfUrl, folderName, filename);
      }
    } catch (e) {
      console.log('Service worker: Error finding PDF URL from page:', e.message);
    }

    // Final fallback: check for new tabs with PDF
    console.log('Service worker: Checking for new tabs with PDF');
    await new Promise(resolve => setTimeout(resolve, 2000));
    const currentTabs = await chrome.tabs.query({});
    for (const tab of currentTabs) {
      if (!tab.url || tab.url === 'chrome://newtab/') continue;
      if (tab.url.includes('.pdf') || tab.url.includes('s3.amazonaws.com')) {
        console.log('Service worker: Found PDF in new tab:', tab.url.substring(0, 80));
        const pdfUrl = tab.url;
        await chrome.tabs.remove(tab.id).catch(() => {});
        return await downloadByUrl(pdfUrl, folderName, filename);
      }
    }

    return { success: false, error: 'No PDF blob captured and no PDF found' };
  } catch (e) {
    console.log('Service worker: Click download failed:', e.message);
    return { success: false, error: e.message };
  }
}

// ============================================================
// FIDELITY-SPECIFIC DOWNLOAD
//
// Flow: attach debugger → content script clicks download icon → finds "Download as PDF"
// LI → returns coordinates → service worker dispatches trusted click via CDP → new tab
// opens with PDF → service worker detects new tab, grabs URL, closes tab, downloads PDF.
//
// Why chrome.debugger? Fidelity is an Angular app where <li role="menuitem"> elements
// have Angular event bindings. Content script .click() dispatches untrusted events that
// Angular ignores. chrome.debugger uses CDP Input.dispatchMouseEvent to send trusted
// mouse events, exactly like Playwright's page.click(). The debugger is attached before
// getting element coordinates because the infobar shifts page layout.
// ============================================================

async function downloadByFidelityClick(tabId, date, folderName, filename) {
  try {
    if (cancelRequested) return { success: false, error: 'Cancelled' };

    // Snapshot existing tabs before the click so we can detect new ones
    const tabsBefore = await chrome.tabs.query({});
    const existingTabIds = new Set(tabsBefore.map(t => t.id));

    // Set up download listener to catch direct downloads
    let capturedDownload = null;
    const captureDownload = (downloadItem) => {
      if (downloadItem.url && !downloadItem.url.startsWith('data:') && !downloadItem.url.startsWith('blob:')) {
        console.log('Service worker [Fidelity]: Download detected:', downloadItem.url?.substring(0, 80));
        capturedDownload = downloadItem;
      }
    };
    chrome.downloads.onCreated.addListener(captureDownload);

    // Attach chrome.debugger FIRST, before any element interaction.
    // The debugger infobar shifts the page, so we must attach before getting coordinates.
    // chrome.debugger uses CDP (Chrome DevTools Protocol) to dispatch trusted mouse events,
    // exactly like Playwright's page.click(). This is necessary because Angular's event
    // handlers on <li role="menuitem"> elements only respond to trusted events, and
    // content script .click() dispatches untrusted events that Angular ignores.
    console.log('Service worker [Fidelity]: Attaching debugger to tab', tabId);
    let debuggerAttached = false;
    try {
      await chrome.debugger.attach({ tabId }, '1.3');
      debuggerAttached = true;
      // Wait for debugger infobar to appear and page layout to stabilize
      await new Promise(resolve => setTimeout(resolve, 1000));
      console.log('Service worker [Fidelity]: Debugger attached');
    } catch (e) {
      console.log('Service worker [Fidelity]: Failed to attach debugger:', e.message);
      chrome.downloads.onCreated.removeListener(captureDownload);
      return { success: false, error: 'Failed to attach debugger: ' + e.message };
    }

    // Step 1: Tell content script to click download icon and find the "Download as PDF" element
    // The content script clicks the download icon button (untrusted click works for <button>),
    // then finds the PDF menu item and returns its coordinates for a trusted click via CDP.
    console.log('Service worker [Fidelity]: Sending fidelity_click_download for date:', date);
    let response;
    try {
      response = await chrome.tabs.sendMessage(tabId, {
        action: 'fidelity_click_download',
        date: date
      });
    } catch (e) {
      if (debuggerAttached) try { await chrome.debugger.detach({ tabId }); } catch (e2) {}
      chrome.downloads.onCreated.removeListener(captureDownload);
      return { success: false, error: 'fidelity_click_download failed: ' + e.message };
    }

    if (!response?.success) {
      if (debuggerAttached) try { await chrome.debugger.detach({ tabId }); } catch (e2) {}
      chrome.downloads.onCreated.removeListener(captureDownload);
      return { success: false, error: response?.error || 'fidelity_click_download returned failure' };
    }

    // Step 2: Use CDP Input.dispatchMouseEvent for a trusted click on the PDF option
    if (response.needsTrustedClick && debuggerAttached) {
      console.log('Service worker [Fidelity]: Dispatching trusted click via CDP at:', response.clickX, response.clickY);
      try {
        await chrome.debugger.sendCommand({ tabId }, 'Input.dispatchMouseEvent', {
          type: 'mousePressed',
          x: response.clickX,
          y: response.clickY,
          button: 'left',
          clickCount: 1
        });
        await chrome.debugger.sendCommand({ tabId }, 'Input.dispatchMouseEvent', {
          type: 'mouseReleased',
          x: response.clickX,
          y: response.clickY,
          button: 'left',
          clickCount: 1
        });

        console.log('Service worker [Fidelity]: Trusted click dispatched, waiting for new tab...');
        await new Promise(resolve => setTimeout(resolve, 3000));
      } catch (e) {
        console.log('Service worker [Fidelity]: CDP click failed:', e.message);
      }
    }

    // Detach debugger now that click is done
    if (debuggerAttached) {
      try { await chrome.debugger.detach({ tabId }); } catch (e) { /* already detached */ }
      console.log('Service worker [Fidelity]: Debugger detached');
    }

    console.log('Service worker [Fidelity]: Polling for PDF (new tab or download)...');

    // Step 3: Poll for new tabs or captured downloads
    for (let i = 0; i < 30; i++) {
      if (cancelRequested) {
        chrome.downloads.onCreated.removeListener(captureDownload);
        return { success: false, error: 'Cancelled' };
      }

      await new Promise(resolve => setTimeout(resolve, 1000));

      if (i % 5 === 0) {
        console.log(`Service worker [Fidelity]: Polling iteration ${i}/30, capturedDownload=${!!capturedDownload}`);
      }

      // Check 1: Did a direct download get captured?
      if (capturedDownload) {
        console.log('Service worker [Fidelity]: Captured direct download:', capturedDownload.url?.substring(0, 80));
        chrome.downloads.onCreated.removeListener(captureDownload);
        try { await waitForDownload(capturedDownload.id); } catch (e) { /* timeout ok */ }
        if (capturedDownload.url) {
          return await downloadByUrl(capturedDownload.url, folderName, filename);
        }
        return { success: true, fileSize: capturedDownload.fileSize || 0 };
      }

      // Check 2: Did a new tab open? (Fidelity opens PDF in new tab)
      try {
        const currentTabs = await chrome.tabs.query({});
        for (const tab of currentTabs) {
          if (existingTabIds.has(tab.id)) continue;
          if (!tab.url || tab.url === 'chrome://newtab/' || tab.url === 'about:blank') continue;
          if (tab.url.startsWith('chrome-extension://') || tab.url.startsWith('chrome://')) continue;

          console.log('Service worker [Fidelity]: New tab detected:', tab.url?.substring(0, 100));
          const pdfUrl = tab.url;
          await chrome.tabs.remove(tab.id).catch(() => {});
          chrome.downloads.onCreated.removeListener(captureDownload);

          if (pdfUrl.startsWith('http')) {
            return await downloadByUrl(pdfUrl, folderName, filename);
          }
        }
      } catch (e) {
        // Ignore tab query errors
      }
    }

    chrome.downloads.onCreated.removeListener(captureDownload);
    return { success: false, error: 'No PDF found after clicking Download as PDF' };
  } catch (e) {
    console.log('Service worker [Fidelity]: Download failed:', e.message);
    return { success: false, error: e.message };
  }
}

// ============================================================
// HELPERS
// ============================================================

// Poll the content script until it responds, replacing fixed setTimeout after navigation
async function waitForPageReady(tabId, timeoutMs = 15000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const resp = await chrome.tabs.sendMessage(tabId, { action: 'ping' });
      if (resp?.pong) return true;
    } catch (e) {
      // Content script not ready yet
    }
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  throw new Error('Page not ready after ' + timeoutMs + 'ms');
}

// After a click download, ensure the tab is still on the expected account URL.
// If not, navigate back and re-scrape so stored click elements are refreshed.
async function ensureTabOnPage(tabId, expectedUrl) {
  const tab = await chrome.tabs.get(tabId);
  if (tab.url && tab.url.startsWith(expectedUrl)) return false; // still on page
  console.log('Service worker: Tab navigated away to', tab.url, '- returning to', expectedUrl);
  await chrome.tabs.update(tabId, { url: expectedUrl });
  await waitForPageReady(tabId);
  await new Promise(resolve => setTimeout(resolve, 3000));
  // Re-scrape to refresh stored click elements in content script
  // Detect which brokerage to use from the URL
  const brokerageSlug = expectedUrl.includes('fidelity.com') ? 'fidelity' :
                        expectedUrl.includes('schwab.com') ? 'schwab' :
                        expectedUrl.includes('robinhood.com') ? 'robinhood' : 'robinhood';
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      if (brokerageSlug === 'fidelity') {
        // For Fidelity, re-scrape the current page (year was already selected)
        await chrome.tabs.sendMessage(tabId, { action: 'fidelity_scrape_page' });
      } else {
        await chrome.tabs.sendMessage(tabId, { action: 'scrape_statements', brokerage: brokerageSlug });
      }
      return true; // re-scraped
    } catch (e) {
      await new Promise(resolve => setTimeout(resolve, 2000));
    }
  }
  return true;
}

function waitForDownload(downloadId) {
  return new Promise((resolve, reject) => {
    const listener = (delta) => {
      if (delta.id === downloadId) {
        if (delta.state?.current === 'complete') {
          chrome.downloads.onChanged.removeListener(listener);
          resolve();
        } else if (delta.state?.current === 'interrupted') {
          chrome.downloads.onChanged.removeListener(listener);
          reject(new Error('Download interrupted'));
        }
      }
    };
    chrome.downloads.onChanged.addListener(listener);

    // Set up interval to check for cancellation
    const cancelChecker = setInterval(() => {
      if (cancelRequested) {
        clearInterval(cancelChecker);
        chrome.downloads.onChanged.removeListener(listener);
        reject(new Error('Download cancelled'));

        // Cancel the download if it's still in progress
        chrome.downloads.search({id: downloadId}).then(items => {
          if (items.length > 0 && items[0].state.current === 'in_progress') {
            chrome.downloads.cancel(downloadId).catch(() => {});
          }
        });
      }
    }, 1000); // Check for cancellation every second

    setTimeout(() => {
      clearInterval(cancelChecker);
      chrome.downloads.onChanged.removeListener(listener);
      reject(new Error('Download timeout'));
    }, 60000);
  });
}

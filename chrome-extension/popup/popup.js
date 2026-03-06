// Popup script for Brokerage Statement Downloader

let currentBrokerage = null;
let isDownloading = false;
let statusInterval = null;

// Initialize popup
document.addEventListener('DOMContentLoaded', async () => {
  // Clear stale state - reset cancel flag and check for stale download status
  const { downloadComplete, downloadStatus, cancelRequested } = await chrome.storage.local.get(['downloadComplete', 'downloadStatus', 'cancelRequested']);

  // If there's a stale "in progress" status from a previous session that's actually done, reset it
  const isStale = downloadStatus && downloadComplete && !cancelRequested;
  if (isStale) {
    // Clear the stale state
    await chrome.storage.local.set({
      downloadStatus: null,
      downloadComplete: false,
      cancelRequested: false
    });
  }

  // Check if a download is actually running (not stale)
  if (downloadStatus && !downloadComplete && !cancelRequested) {
    // Download in progress — open progress window instead
    const progressUrl = chrome.runtime.getURL('popup/progress.html');
    chrome.windows.create({ url: progressUrl, type: 'popup', width: 500, height: 600 });
    return;
  }

  // Clear any stale cancel state
  if (cancelRequested) {
    await chrome.storage.local.set({ cancelRequested: false });
  }

  await checkCurrentTab();
  setupEventListeners();
  startStatusPolling();
});

async function checkCurrentTab() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.url) return;

    const url = new URL(tab.url);
    currentBrokerage = detectBrokerage(url.hostname);
    console.log('Detected brokerage:', currentBrokerage, 'URL:', url.hostname);

    if (currentBrokerage) {
      document.getElementById('no-brokerage').classList.add('hidden');
      document.getElementById('brokerage-info').classList.remove('hidden');
      document.getElementById('brokerage-info').style.display = 'block';
      document.getElementById('brokerage-name').textContent = currentBrokerage.displayName;
      await updateLogUI();
    }
  } catch (error) {
    console.error('Error checking tab:', error);
  }
}

// Decide whether to show import prompt or download button
async function updateLogUI() {
  const { downloadLog } = await chrome.storage.local.get('downloadLog');
  const hasLog = downloadLog && downloadLog.brokerages;

  if (hasLog) {
    showDownloadReady(downloadLog);
  } else {
    // No log loaded — show new UI with create/import options
    showNoLog();
  }
}

function showDownloadReady(downloadLog) {
  console.log('showDownloadReady called');
  const downloadSection = document.getElementById('download-section');
  downloadSection.classList.remove('hidden');
  downloadSection.style.display = 'block';

  // Count tracked statements
  let totalStatements = 0;
  const brokerages = Object.keys(downloadLog.brokerages || {});
  for (const b of brokerages) {
    const accounts = downloadLog.brokerages[b].accounts || {};
    for (const a of Object.values(accounts)) {
      totalStatements += (a.statements || []).length;
    }
  }

  const logStatusEl = document.getElementById('log-status');
  const logStatusText = document.getElementById('log-status-text');
  const importBtn = document.getElementById('import-btn');
  const createLogBtn = document.getElementById('create-log-btn');
  const downloadBtn = document.getElementById('download-btn');
  const logPathHint = document.querySelector('.log-path-hint');

  // Show log status, hide create button, enable download
  logStatusEl.classList.remove('hidden');
  logStatusEl.style.display = 'block';
  logPathHint.classList.add('hidden');
  createLogBtn.classList.add('hidden');
  createLogBtn.style.display = 'none';
  importBtn.classList.add('hidden');
  importBtn.style.display = 'none';

  logStatusEl.classList.add('has-log');
  logStatusText.innerHTML =
    `<strong>Log loaded:</strong> ${totalStatements} statements tracked across ${brokerages.length} brokerage(s).`;

  downloadBtn.disabled = false;
}

function showNoLog() {
  console.log('showNoLog called');
  const downloadSection = document.getElementById('download-section');
  downloadSection.classList.remove('hidden');
  downloadSection.style.display = 'block';

  const logStatusEl = document.getElementById('log-status');
  const importBtn = document.getElementById('import-btn');
  const createLogBtn = document.getElementById('create-log-btn');
  const downloadBtn = document.getElementById('download-btn');
  const logPathHint = document.querySelector('.log-path-hint');

  // Hide log status, show import and create buttons, disable download
  logStatusEl.classList.add('hidden');
  logPathHint.classList.remove('hidden');
  importBtn.classList.remove('hidden');
  importBtn.style.display = 'block';
  createLogBtn.classList.remove('hidden');
  createLogBtn.style.display = 'block';

  downloadBtn.disabled = true;
}

function detectBrokerage(hostname) {
  const patterns = {
    robinhood: { host: 'robinhood.com', displayName: 'Robinhood', slug: 'robinhood' },
    schwab: { host: 'schwab.com', displayName: 'Charles Schwab', slug: 'schwab' },
    etrade: { host: 'etrade.com', displayName: 'E*Trade', slug: 'etrade' },
    fidelity: { host: 'fidelity.com', displayName: 'Fidelity', slug: 'fidelity' },
    webull: { host: 'webull.com', displayName: 'Webull', slug: 'webull' },
    m1finance: { host: 'm1.com', displayName: 'M1 Finance', slug: 'm1finance' },
    vanguard: { host: 'vanguard.com', displayName: 'Vanguard', slug: 'vanguard' },
    ibkr: { host: 'interactivebrokers.com', displayName: 'Interactive Brokers', slug: 'ibkr' },
  };

  for (const [, config] of Object.entries(patterns)) {
    if (hostname.includes(config.host)) {
      return config;
    }
  }
  return null;
}

function setupEventListeners() {
  document.getElementById('download-btn').addEventListener('click', downloadStatements);
  document.getElementById('import-btn').addEventListener('click', () => document.getElementById('import-file').click());
  document.getElementById('import-file').addEventListener('change', handleImportFile);
  document.getElementById('create-log-btn').addEventListener('click', createDownloadLog);
}

async function handleImportFile(e) {
  const file = e.target.files[0];
  if (!file) return;
  try {
    const text = await file.text();
    const log = JSON.parse(text);
    if (!log.brokerages && !log.version) {
      throw new Error('Not a valid download_log.json');
    }
    await chrome.storage.local.set({ downloadLog: log });
    showDownloadReady(log);
    addLogEntry('Log imported successfully', 'success');
  } catch (error) {
    addLogEntry('Import failed: ' + error.message, 'error');
  }
  // Reset so same file can be re-imported
  e.target.value = '';
}

// Poll service worker for status updates
function startStatusPolling() {
  if (statusInterval) clearInterval(statusInterval);

  statusInterval = setInterval(async () => {
    if (isDownloading) {
      try {
        const response = await chrome.runtime.sendMessage({ action: 'get_status' });
        if (response) {
          if (response.message) {
            document.getElementById('progress-status').textContent = response.message;
          }
          if (response.currentStep) {
            document.getElementById('step-text').textContent = response.currentStep;
          }
          if (response.statementsFound !== undefined) {
            document.getElementById('count-number').textContent = response.statementsFound;
          }
          if (response.message) {
            addLogEntry(response.message, response.type || 'info');
          }
        }
      } catch (e) {
        // Ignore errors when not downloading
      }
    }
  }, 500);
}

window.addEventListener('unload', () => {
  if (statusInterval) clearInterval(statusInterval);
});

// Create a new download_log.json in Downloads/Statements/ folder
async function createDownloadLog() {
  try {
    const freshLog = { version: 1, lastUpdated: new Date().toISOString(), brokerages: {} };

    // Save to chrome.storage
    await chrome.storage.local.set({ downloadLog: freshLog });

    // Create file in Downloads/Statements/ folder
    const blob = new Blob([JSON.stringify(freshLog, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    await chrome.downloads.download({
      url: url,
      filename: 'Statements/download_log.json',
      conflictAction: 'overwrite',
      saveAs: false
    });

    addLogEntry('Created download_log.json in Downloads/Statements/', 'success');
    showDownloadReady(freshLog);
  } catch (error) {
    addLogEntry('Error creating log: ' + error.message, 'error');
  }
}

async function downloadStatements() {
  if (!currentBrokerage || isDownloading) return;

  const btn = document.getElementById('download-btn');
  const progress = document.getElementById('progress');
  const log = document.getElementById('progress-log');

  isDownloading = true;
  btn.disabled = true;
  btn.textContent = 'Downloading... (keep popup open)';

  // Show progress section and hide brokerage info
  progress.classList.remove('hidden');
  document.getElementById('brokerage-info').classList.add('hidden');
  document.getElementById('no-brokerage').classList.add('hidden');
  log.innerHTML = '';

  // Reset UI
  document.getElementById('progress-status').textContent = 'Downloading...';
  document.getElementById('step-text').textContent = 'Starting...';
  document.getElementById('count-number').textContent = '0';
  document.getElementById('download-progress').classList.add('hidden');

  await chrome.storage.local.set({
    downloadStartTime: Date.now(),
    downloadStatus: 'Downloading...',
    downloadComplete: false,
    downloadResults: null
  });

  addLogEntry('Starting download...', 'info');

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });

    // Open progress page in a small separate window so Robinhood tab stays active
    const progressUrl = chrome.runtime.getURL('popup/progress.html');
    chrome.windows.create({ url: progressUrl, type: 'popup', width: 500, height: 600 });

    const response = await chrome.runtime.sendMessage({
      action: 'download_statements',
      brokerage: currentBrokerage.slug,
      tabId: tab.id
    });

    console.log('Popup got response:', response);

    if (response?.success) {
      const downloadedCount = response.results?.filter(r => r.downloaded).length || 0;
      const totalFound = response.results?.length || 0;

      document.getElementById('progress-status').textContent = 'Complete!';
      document.getElementById('step-text').textContent = 'Done';
      addLogEntry(`Done! Found ${totalFound}, Downloaded ${downloadedCount}`, 'success');
      showResults(response.results || []);
    } else {
      document.getElementById('progress-status').textContent = 'Failed';
      document.getElementById('step-text').textContent = 'Error';
      addLogEntry(response?.error || 'Download failed', 'error');
    }
  } catch (error) {
    console.error('Popup error:', error);
    document.getElementById('progress-status').textContent = 'Error';
    document.getElementById('step-text').textContent = 'Error';
    addLogEntry('Error: ' + error.message, 'error');
  } finally {
    isDownloading = false;
    btn.disabled = false;
    btn.textContent = 'Download Statements';
    document.getElementById('brokerage-info').classList.remove('hidden');
  }
}

function addLogEntry(message, type = 'info') {
  const log = document.getElementById('progress-log');
  const existingEntries = log.querySelectorAll('li');

  // Don't add duplicate consecutive messages
  if (existingEntries.length > 0) {
    const lastEntry = existingEntries[existingEntries.length - 1];
    if (lastEntry.textContent === message) return;
  }

  const li = document.createElement('li');
  li.textContent = message;
  li.className = type;
  log.appendChild(li);
  log.scrollTop = log.scrollHeight;

  document.getElementById('progress-status').textContent = message;
}

function showResults(results) {
  const resultsDiv = document.getElementById('results');
  const list = document.getElementById('download-list');
  list.innerHTML = '';

  results.forEach(stmt => {
    const li = document.createElement('li');
    if (stmt.downloaded) {
      li.textContent = `${stmt.filename}`;
      li.className = 'success';
    } else if (stmt.alreadyExists) {
      li.textContent = `${stmt.filename} (already exists)`;
    } else if (stmt.error) {
      li.textContent = `${stmt.filename} - Error: ${stmt.error}`;
      li.className = 'error';
    }
    list.appendChild(li);
  });

  resultsDiv.classList.remove('hidden');
}

async function exportLog() {
  try {
    const { downloadLog } = await chrome.storage.local.get('downloadLog');
    if (!downloadLog) {
      addLogEntry('No download log to export', 'error');
      return;
    }

    const blob = new Blob([JSON.stringify(downloadLog, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    await chrome.downloads.download({
      url: url,
      filename: 'Statements/download_log.json',
      conflictAction: 'overwrite'
    });
    addLogEntry('Log exported to Downloads/Statements/', 'success');
  } catch (error) {
    addLogEntry('Export failed: ' + error.message, 'error');
  }
}

async function importLog() {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = '.json';

  // Track whether the user picked a file
  let filePicked = false;

  input.onchange = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    filePicked = true;

    try {
      const text = await file.text();
      const log = JSON.parse(text);

      // Validate it looks like a download log
      if (!log.brokerages && !log.version) {
        throw new Error('Not a valid download_log.json');
      }

      await chrome.storage.local.set({ downloadLog: log });
      showDownloadReady(log);
      addLogEntry('Log imported successfully', 'success');
    } catch (error) {
      addLogEntry('Import failed: ' + error.message, 'error');
    }
  };

  // If the user cancels the file picker, create a fresh log for them
  // The focus event fires when the file dialog closes
  window.addEventListener('focus', async function onFocus() {
    window.removeEventListener('focus', onFocus);
    // Small delay to let onchange fire first if a file was selected
    await new Promise(r => setTimeout(r, 300));
    if (!filePicked) {
      await createFreshLog();
    }
  }, { once: true });

  input.click();
}

// Create a fresh download_log.json, save to chrome.storage AND to ~/Downloads/Statements/
async function createFreshLog() {
  const freshLog = { version: 1, lastUpdated: new Date().toISOString(), brokerages: {} };

  // Save to chrome.storage
  await chrome.storage.local.set({ downloadLog: freshLog });

  // Write to ~/Downloads/Statements/download_log.json so the file exists on disk
  try {
    const blob = new Blob([JSON.stringify(freshLog, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    await chrome.downloads.download({
      url: url,
      filename: 'Statements/download_log.json',
      conflictAction: 'overwrite',
      saveAs: false
    });
  } catch (e) {
    console.log('Could not write fresh download_log.json to disk:', e.message);
  }

  showDownloadReady(freshLog);
  addLogEntry('Created new download_log.json', 'success');
}

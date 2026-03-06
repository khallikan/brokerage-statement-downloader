// Initialize - clear stale state on load
chrome.storage.local.get(['downloadComplete', 'downloadStatus', 'cancelRequested'], function(result) {
  // If download is complete and no cancel is requested, this is stale - clear it
  if (result.downloadComplete && !result.cancelRequested && result.downloadStatus) {
    chrome.storage.local.set({
      downloadStatus: null,
      downloadComplete: false
    });
  }
});

function updateStatus() {
  chrome.storage.local.get(['downloadStatus', 'statementsFound', 'currentStep', 'progressLog', 'downloadComplete'], function(result) {
    if (result.downloadStatus) {
      document.getElementById('status').textContent = result.downloadStatus;
      if (result.downloadComplete) {
        document.getElementById('status').style.background = '#e8f5e9';
        document.getElementById('status').style.borderColor = '#a5d6a7';
        document.getElementById('status').style.color = '#2e7d32';
        document.getElementById('done-banner').style.display = 'block';
        document.getElementById('title').textContent = 'Statement Downloader - Complete';
        document.getElementById('cancel-btn').textContent = 'Close';
        document.getElementById('cancel-btn').style.background = '#4CAF50';
        document.getElementById('cancel-btn').onclick = function() { window.close(); };
      }
    }
    if (result.statementsFound !== undefined) {
      document.getElementById('count').textContent = 'Statements found: ' + result.statementsFound;
    }
    if (result.currentStep) {
      document.getElementById('step').textContent = 'Current step: ' + result.currentStep;
    }
    if (result.progressLog) {
      var logDiv = document.getElementById('log');
      // Only auto-scroll if user is already near the bottom
      var isAtBottom = logDiv.scrollHeight - logDiv.scrollTop - logDiv.clientHeight < 30;
      logDiv.innerHTML = '';
      result.progressLog.forEach(function(entry) {
        var div = document.createElement('div');
        div.className = 'log-entry ' + (entry.type || 'info');
        div.textContent = (entry.time ? '[' + entry.time.substring(11, 19) + '] ' : '') + entry.message;
        logDiv.appendChild(div);
      });
      if (isAtBottom) {
        logDiv.scrollTop = logDiv.scrollHeight;
      }
    }
  });
}

document.getElementById('cancel-btn').addEventListener('click', function() {
  chrome.runtime.sendMessage({ action: 'cancel_download' }, function(response) {
    document.getElementById('cancel-btn').textContent = 'Cancelling...';
    document.getElementById('cancel-btn').disabled = true;
  });
});

document.getElementById('copy-btn').addEventListener('click', function() {
  chrome.storage.local.get(null, function(items) {
    var text = JSON.stringify(items, null, 2);
    navigator.clipboard.writeText(text).then(function() {
      alert('Logs copied to clipboard!');
    });
  });
});

// Listen for storage changes for instant updates
chrome.storage.onChanged.addListener(function(changes, area) {
  if (area === 'local') {
    updateStatus();
  }
});

// Also poll as fallback
setInterval(updateStatus, 2000);
updateStatus();

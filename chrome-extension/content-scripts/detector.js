// Content script - runs on all brokerage pages

(function() {
  'use strict';

  const hostname = window.location.hostname;

  // Detect brokerage from hostname
  let currentBrokerage = null;
  if (hostname.includes('robinhood.com')) {
    currentBrokerage = 'robinhood';
  } else if (hostname.includes('schwab.com')) {
    currentBrokerage = 'schwab';
  } else if (hostname.includes('etrade.com')) {
    currentBrokerage = 'etrade';
  } else if (hostname.includes('fidelity.com')) {
    currentBrokerage = 'fidelity';
  } else if (hostname.includes('webull.com')) {
    currentBrokerage = 'webull';
  } else if (hostname.includes('m1.com')) {
    currentBrokerage = 'm1finance';
  } else if (hostname.includes('vanguard.com')) {
    currentBrokerage = 'vanguard';
  } else if (hostname.includes('interactivebrokers.com')) {
    currentBrokerage = 'ibkr';
  }

  if (!currentBrokerage) {
    console.log('Statement Downloader: Not a supported brokerage');
    return;
  }

  console.log('Statement Downloader: Detected', currentBrokerage);

  // Display names for filenames
  var BROKERAGE_DISPLAY_NAMES = {
    robinhood: 'Robinhood',
    schwab: 'Schwab',
    etrade: 'ETrade',
    fidelity: 'Fidelity',
    webull: 'Webull',
    m1finance: 'M1Finance',
    vanguard: 'Vanguard',
    ibkr: 'InteractiveBrokers'
  };

  function makeBrokerageFilename(date, slug, accountLabel) {
    var displayName = BROKERAGE_DISPLAY_NAMES[slug] || slug;
    return date + '_' + displayName + '_' + accountLabel + '.pdf';
  }

  function makeAccountLabel(accountType, last4) {
    var base = accountType.toLowerCase().replace(/[^a-z0-9]/g, '');
    return base + (last4 || '0000');
  }

  // Helper functions
  function parseDate(text) {
    if (!text) return null;
    const textLower = text.toLowerCase().trim();
    const monthMap = {
      january: '01', february: '02', march: '03', april: '04', may: '05', june: '06',
      july: '07', august: '08', september: '09', october: '10', november: '11', december: '12',
      jan: '01', feb: '02', mar: '03', apr: '04', jun: '06', jul: '07', aug: '08',
      sep: '09', oct: '10', nov: '11', dec: '12'
    };
    for (const [month, num] of Object.entries(monthMap)) {
      const match = textLower.match(new RegExp(month + '\\s+(\\d{4})'));
      if (match) return match[1] + '-' + num;
    }
    const m = text.match(/(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})/);
    if (m) return m[3] + '-' + m[1].padStart(2, '0');
    return null;
  }

  function wait(ms) {
    return new Promise(function(resolve) {
      setTimeout(resolve, ms);
    });
  }

  // Must match playwright-downloader's BaseBrokerage.make_account_label exactly
  function makeAccountLabel(accountType, last4) {
    var slug = accountType.toLowerCase().replace(/ /g, '').replace(/-/g, '');
    slug = slug.replace('rothira', 'roth');
    slug = slug.replace('traditionalira', 'trad');
    return slug + last4;
  }

  function getAccountInfo(url) {
    // Try to get the account type from the active tab link text (matches Playwright behavior)
    var accountType = '';
    var urlPath = url.replace(/https?:\/\/[^/]+/, '');
    var activeTab = document.querySelector('a[href="' + urlPath + '"]');
    if (activeTab) {
      accountType = activeTab.textContent.trim();
    }

    // Fallback to URL-based detection if tab text not found
    if (!accountType) {
      if (url.includes('/crypto')) {
        accountType = 'Crypto';
      } else if (url.includes('/futures-monthly')) {
        accountType = 'Futures & event contracts';
      } else if (url.includes('/futures-daily')) {
        accountType = 'Futures & event contracts';
      } else if (url.includes('/roth') || url.includes('/ira')) {
        accountType = 'Roth IRA';
      } else {
        accountType = 'Individual';
      }
    }

    // Try to find last-4 digits of account number from page text
    // Matches patterns like "····1234" or "•••1234" or "***1234"
    var last4 = '0000';
    var bodyText = document.body ? document.body.innerText : '';
    var last4Match = bodyText.match(/[·•*]{2,}\s*(\d{4})/);
    if (last4Match) {
      last4 = last4Match[1];
    }

    var accountLabel = makeAccountLabel(accountType, last4);
    return { accountLabel: accountLabel, accountType: accountType, accountLast4: last4 };
  }

  function parseStatementLink(link, accountInfo, baseUrl) {
    var href = link.getAttribute('href');
    var text = link.textContent.trim();

    // If href is "#" or empty, try to find the actual download URL
    if (!href || href === '#') {
      // Look for data attributes
      var dataUrl = link.getAttribute('data-url') || link.getAttribute('data-href') || link.getAttribute('data-link');
      if (dataUrl) {
        href = dataUrl;
      } else {
        // Check for download attribute
        var downloadAttr = link.getAttribute('download');
        if (downloadAttr) {
          // This is a download button - we need to handle it differently
          console.log('[Robinhood] Found download button:', text.substring(0, 50));
        }

        // Look for adjacent or sibling elements with href
        var parent = link.parentElement;
        if (parent) {
          var siblingLink = parent.querySelector('a[href]:not([href="#"])');
          if (siblingLink) {
            href = siblingLink.getAttribute('href');
          }
        }
      }
    }

    // If still no valid href, return null but log it
    if (!href || href === '#') {
      return null;
    }

    var parent = link.parentElement;
    var parentText = parent ? parent.textContent : '';
    var grandparent = parent ? parent.parentElement : null;
    var grandparentText = grandparent ? grandparent.textContent : '';

    var date = parseDate(text) || parseDate(parentText) || parseDate(grandparentText);

    if (!date) {
      console.log('[Robinhood] Could not parse date from:', text.substring(0, 50));
      return null;
    }

    var fullUrl = href;
    if (href.startsWith('/')) {
      fullUrl = baseUrl + href;
    }

    return {
      date: date,
      url: fullUrl,
      filename: date + '_Robinhood_' + accountInfo.accountLabel + '.pdf',
      accountLabel: accountInfo.accountLabel,
      accountType: accountInfo.accountType,
      accountLast4: accountInfo.accountLast4
    };
  }

  async function expandAllStatements() {
    console.log('[Robinhood] Looking for View More button...');

    var clicked = 0;
    var maxClicks = 20; // Limit clicks to avoid infinite loop

    // Remember the current URL to detect navigation
    var originalUrl = window.location.href;

    while (clicked < maxClicks) {
      var viewMoreButton = null;

      // Find View More button/link - include <a> tags since Robinhood uses <a href="#">View More</a>
      var allClickables = document.querySelectorAll('button, a[role="button"], a[href="#"]');
      for (var i = 0; i < allClickables.length; i++) {
        var btn = allClickables[i];
        var text = btn.textContent.toLowerCase().trim();
        // Only click if exact match or starts with "view more" and is visible
        if ((text === 'view more' || text.startsWith('view more')) && btn.offsetParent !== null) {
          viewMoreButton = btn;
          break;
        }
      }

      if (!viewMoreButton) {
        console.log('[Robinhood] View More button not found after', clicked, 'clicks');
        break;
      }

      // Check if we've navigated away from the original page
      if (window.location.href !== originalUrl) {
        console.log('[Robinhood] Detected navigation away, stopping View More clicks');
        break;
      }

      try {
        console.log('[Robinhood] Clicking View More...');
        viewMoreButton.click();
        await wait(1500);
        clicked++;

        // Check again after wait
        if (window.location.href !== originalUrl) {
          console.log('[Robinhood] Detected navigation after click, stopping');
          break;
        }
      } catch (e) {
        console.log('[Robinhood] Error clicking View More:', e.message);
        break;
      }
    }

    console.log('[Robinhood] Done clicking View More', clicked, 'times');
  }

  // Helper: Click a button to trigger a download and wait for download to start
  // Returns information about what was clicked so service worker can track
  async function triggerDownload(link) {
    var btnText = link.textContent || '';
    var date = parseDate(btnText) || parseDate(link.parentElement ? link.parentElement.textContent : '');

    // Store what we're about to click for the service worker to track
    var pendingDownload = {
      clickedAt: Date.now(),
      date: date,
      accountLabel: accountInfo.accountLabel,
      accountType: accountInfo.accountType,
      accountLast4: accountInfo.accountLast4
    };

    // Save to chrome storage so service worker can correlate with downloads
    await chrome.storage.local.set({ pendingDownload: pendingDownload });

    // Click the link/button
    link.click();
    console.log('[Robinhood] Clicked download button for date:', date);

    // Wait a moment for download to initiate
    await wait(2000);

    return { success: true, pendingDownload: pendingDownload };
  }

  // Helper: Try to extract PDF URL from page elements
  function findPdfUrlFromElement(element) {
    // Check various attributes for PDF URL
    var attrs = ['data-url', 'data-link', 'data-href', 'data-document', 'href'];
    for (var i = 0; i < attrs.length; i++) {
      var val = element.getAttribute(attrs[i]);
      // Must be a real URL with actual content, not just "#"
      if (val && val !== '#' && (val.includes('.pdf') || val.includes('/documents/') || val.includes('/statement'))) {
        return val;
      }
    }

    // Check parent/sibling links - but only if they have real URLs
    var parentLink = element.closest('a');
    if (parentLink && parentLink.href && parentLink.href !== '#') {
      // Check if it's actually a PDF/document URL
      if (parentLink.href.includes('.pdf') || parentLink.href.includes('/documents/')) {
        return parentLink.href;
      }
    }

    return null;
  }

  // Store found statement elements for later clicking
  var statementClickElements = [];

  // Helper: Find and store clickable statement elements for later
  function findAndStoreStatementElements() {
    var found = [];
    var seenDates = {};
    // Find all elements that might be statement rows/links
    var potentialElements = document.querySelectorAll('div[class*="row"], div[class*="item"], a, button, li');

    for (var i = 0; i < potentialElements.length; i++) {
      var el = potentialElements[i];
      var text = el.textContent || '';

      // Skip elements with too much text — they are large containers, not statement rows
      if (text.length > 200) continue;

      // Skip elements that contain navigation links (activity-reports, tax, etc.)
      if (text.includes('activity-reports') || text.includes('Activity report')) continue;
      var navLink = el.querySelector('a[href*="/reports-statements/"]');
      if (navLink) continue;

      // Look for dates in the text
      var date = parseDate(text);
      if (date) {
        // Skip duplicate dates (first match wins)
        if (seenDates[date]) continue;

        // Check if this element or its children are clickable
        var clickable = el.querySelector('a, button') ||
                        el.tagName.toLowerCase() === 'a' ||
                        el.tagName.toLowerCase() === 'button' ||
                        el.getAttribute('role') === 'button';

        if (clickable || el.tagName.toLowerCase() === 'a') {
          // Find the actual clickable element
          var clickEl = el.tagName.toLowerCase() === 'a' || el.tagName.toLowerCase() === 'button' ? el : el.querySelector('a, button');

          // Skip if the clickable element is a navigation link
          if (clickEl && clickEl.tagName.toLowerCase() === 'a') {
            var clickHref = clickEl.getAttribute('href') || '';
            if (clickHref.includes('/reports-statements/') || clickHref.includes('activity-reports')) continue;
          }

          if (clickEl) {
            seenDates[date] = true;
            found.push({
              date: date,
              element: clickEl,
              text: text.substring(0, 100)
            });
            console.log('[Robinhood] Found clickable statement:', date, '| text:', text.substring(0, 50));
          }
        }
      }
    }

    return found;
  }

  // Helper: Generate a unique selector for an element
  function getElementSelector(element) {
    if (element.id) {
      return '#' + element.id;
    }

    var parts = [];
    var current = element;
    while (current && current !== document.body) {
      var selector = current.tagName.toLowerCase();
      if (current.className && typeof current.className === 'string') {
        var classes = current.className.trim().split(/\s+/).filter(function(c) { return c; });
        if (classes.length > 0) {
          selector += '.' + classes.join('.');
        }
      }

      // Add index if needed to make unique
      var siblings = current.parentElement ? current.parentElement.querySelectorAll(selector) : [];
      if (siblings.length > 1) {
        var index = Array.prototype.indexOf.call(siblings, current);
        selector += ':nth-of-type(' + (index + 1) + ')';
      }

      parts.unshift(selector);
      current = current.parentElement;
    }

    return parts.join(' > ');
  }

  // Detect available account tabs on Robinhood statements page
  // Only detect specific known monthly account types
  function detectRobinhoodAccountTabs() {
    console.log('[Robinhood] Detecting account tabs in Monthly section...');

    const foundTabs = [];

    // Get ALL links to /account/reports-statements/
    const allLinks = document.querySelectorAll('a[href*="/account/reports-statements/"]');

    const seenUrls = new Set();

    for (const link of allLinks) {
      const href = link.getAttribute('href');
      const text = link.textContent.trim();

      if (!href || seenUrls.has(href)) continue;
      seenUrls.add(href);

      // Exclude activity-reports and tax URLs
      if (href.includes('activity-reports') || href.includes('/tax')) {
        console.log('[Robinhood] Skipping excluded link:', href, '-', text);
        continue;
      }

      // Match only valid monthly account type path segments after /reports-statements/
      // Exclude futures-daily and ec-annual (daily/annual statements)
      const segmentMatch = href.match(/\/reports-statements\/(individual|crypto|futures-monthly)(\/|$|\?|#)/);
      if (!segmentMatch) {
        console.log('[Robinhood] Skipping non-account link:', href, '-', text);
        continue;
      }

      // Extract the account type from the URL
      const match = href.match(/\/account\/reports-statements\/([^/?#]+)/);
      if (match) {
        const accountType = match[1];
        console.log('[Robinhood] Found account tab:', accountType, '-', text);
        foundTabs.push(accountType);
      }
    }

    console.log('[Robinhood] Detected monthly tabs:', foundTabs);
    return foundTabs;
  }

  // Main scrape function for Robinhood
  // Note: The URLs already navigate to the correct sections, no tab clicking needed

  async function scrapeRobinhood() {
    var url = window.location.href;
    var accountInfo = getAccountInfo(url);

    console.log('[Robinhood] Scraping account:', accountInfo.accountType);
    console.log('[Robinhood] URL:', url);

    // Wait for page to load
    await wait(4000);

    // Scroll down to trigger lazy loading
    window.scrollTo(0, document.body.scrollHeight);
    await wait(1000);
    window.scrollTo(0, 0);
    await wait(1000);

    // Try to click View More button first
    await expandAllStatements();

    // Scroll again after expanding
    window.scrollTo(0, document.body.scrollHeight);
    await wait(1000);
    window.scrollTo(0, 0);
    await wait(1000);

    var statements = [];

    // Debug: log all links on page
    var allLinks = document.querySelectorAll('a[href]');
    console.log('[Robinhood] Total links on page:', allLinks.length);

    // Print links that might be statements
    for (var debugI = 0; debugI < allLinks.length; debugI++) {
      var debugLink = allLinks[debugI];
      var debugHref = debugLink.getAttribute('href');
      var debugText = debugLink.textContent.trim().substring(0, 50);
      console.log('[Robinhood] Link', debugI, ':', debugHref, '|', debugText);
    }

    // Strategy 1: Look for direct PDF links
    var pdfLinks = document.querySelectorAll('a[href*=".pdf"], a[href*="/documents/"]');
    console.log('[Robinhood] Found', pdfLinks.length, 'PDF/document links');

    for (var i = 0; i < pdfLinks.length; i++) {
      var stmt = parseStatementLink(pdfLinks[i], accountInfo, 'https://robinhood.com');
      if (stmt) {
        console.log('[Robinhood] Added PDF link:', stmt.url);
        statements.push(stmt);
      }
    }

    // Strategy 1b: Look for download buttons with dates - these need clicking
    // Use the findAndStoreStatementElements function to get clickable elements
    console.log('[Robinhood] Looking for clickable statement elements...');
    statementClickElements = findAndStoreStatementElements();

    if (statementClickElements.length > 0) {
      console.log('[Robinhood] Found', statementClickElements.length, 'clickable statement elements');
      // Mark all as needing click
      for (var sc = 0; sc < statementClickElements.length; sc++) {
        var stmtEl = statementClickElements[sc];
        // Store in a way that service worker can retrieve by date
        statements.push({
          date: stmtEl.date,
          url: null,
          filename: stmtEl.date + '_Robinhood_' + accountInfo.accountLabel + '.pdf',
          accountLabel: accountInfo.accountLabel,
          accountType: accountInfo.accountType,
          accountLast4: accountInfo.accountLast4,
          needsClick: true
        });
      }
    }

    // Strategy 2: If no PDF links, look for links that might lead to PDFs
    if (statements.length === 0) {
      console.log('[Robinhood] No PDF links found, checking all statement links');

      for (var j = 0; j < allLinks.length; j++) {
        var link = allLinks[j];
        var href = link.getAttribute('href');
        var text = link.textContent.toLowerCase();

        // Skip navigation links
        if (!href || href === '#') continue;
        if (href.includes('/account/reports-statements/')) continue;

        // Look for statement/document links
        if (href.includes('statement') || href.includes('document') ||
            href.includes('download') || parseDate(text)) {

          var stmt2 = parseStatementLink(link, accountInfo, 'https://robinhood.com');
          if (stmt2) {
            console.log('[Robinhood] Added statement link:', stmt2.url, '| text:', link.textContent.substring(0, 30));
            statements.push(stmt2);
          }
        }
      }
    }

    // Deduplicate by date (not URL since many have null URL)
    var uniqueStatements = [];
    var seenDates = {};
    for (var k = 0; k < statements.length; k++) {
      var stmt3 = statements[k];
      if (!seenDates[stmt3.date]) {
        seenDates[stmt3.date] = true;
        uniqueStatements.push(stmt3);
      }
    }

    console.log('[Robinhood] Total statements found:', uniqueStatements.length, 'from', statements.length, 'total');
    return uniqueStatements;
  }

  // ===== M1 FINANCE =====
  async function scrapeM1Finance() {
    console.log('[M1Finance] Starting scrape...');
    await wait(3000);

    var allStatements = [];

    // Read year options from React-Select dropdown
    var years = await m1ReadYearOptions();
    if (years.length === 0) {
      console.log('[M1Finance] No year options found, parsing visible table');
      var visible = m1ParseTable();
      return visible;
    }

    // Sort years newest first
    years.sort(function(a, b) { return parseInt(b) - parseInt(a); });
    console.log('[M1Finance] Years found:', years.join(', '));

    for (var yi = 0; yi < years.length; yi++) {
      var year = years[yi];
      console.log('[M1Finance] Selecting year:', year);

      if (!await m1SelectYear(year)) {
        console.log('[M1Finance] Could not select year:', year);
        continue;
      }
      await wait(2000);

      // Click "Load more" until gone
      await m1ClickLoadMore();

      // Parse table rows
      var stmts = m1ParseTable();
      console.log('[M1Finance] Year', year, ':', stmts.length, 'statements');
      allStatements = allStatements.concat(stmts);
    }

    // Deduplicate by date + accountLabel
    var seen = {};
    var unique = [];
    for (var i = 0; i < allStatements.length; i++) {
      var key = allStatements[i].date + '_' + allStatements[i].accountLabel;
      if (!seen[key]) {
        seen[key] = true;
        unique.push(allStatements[i]);
      }
    }

    console.log('[M1Finance] Total unique statements:', unique.length);
    return unique;
  }

  function m1ReadYearOptions() {
    return new Promise(function(resolve) {
      // Open the dropdown
      m1OpenYearDropdown().then(function(opened) {
        if (!opened) {
          resolve([]);
          return;
        }

        wait(1500).then(function() {
          var result = [];
          var seen = {};

          // Primary: react-select option elements
          var optEls = document.querySelectorAll('[id*="react-select"][id*="option"]');
          for (var i = 0; i < optEls.length; i++) {
            var text = (optEls[i].innerText || optEls[i].textContent || '').trim();
            if (/^\d{4}$/.test(text) && !seen[text]) {
              seen[text] = true;
              result.push(text);
            }
          }

          if (result.length === 0) {
            // Fallback: menu container children
            var menuEl = document.querySelector('[class*="-menu"]');
            if (menuEl) {
              var children = menuEl.querySelectorAll('*');
              for (var j = 0; j < children.length; j++) {
                var t = (children[j].innerText || children[j].textContent || '').trim();
                if (/^\d{4}$/.test(t) && !seen[t] && children[j].children.length <= 2) {
                  seen[t] = true;
                  result.push(t);
                }
              }
            }
          }

          // Close dropdown
          document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));

          console.log('[M1Finance] Year options:', result.join(', '));
          resolve(result);
        });
      });
    });
  }

  async function m1OpenYearDropdown() {
    // Strategy 1: Find input#year → walk up to [class*="control"] → mousedown
    var input = document.querySelector('input#year');
    if (input) {
      var container = input;
      for (var i = 0; i < 10; i++) {
        container = container.parentElement;
        if (!container) break;
        var control = container.querySelector('[class*="control"], [class*="contr"]');
        if (control && control.offsetWidth > 0) {
          control.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
          await wait(500);
          return true;
        }
      }
    }

    // Strategy 2: Click indicator (chevron)
    var indicators = document.querySelectorAll('[class*="indicatorContainer"], [class*="indicator"]');
    for (var k = 0; k < indicators.length; k++) {
      if (indicators[k].offsetParent !== null) {
        indicators[k].click();
        await wait(500);
        return true;
      }
    }

    // Strategy 3: Find "Year" label → click parent's control
    var labels = document.querySelectorAll('label');
    for (var l = 0; l < labels.length; l++) {
      if (/^Year$/i.test(labels[l].textContent.trim())) {
        var parent = labels[l].parentElement;
        if (parent) {
          var ctrl = parent.querySelector('[class*="control"], [class*="select"], div');
          if (ctrl) {
            ctrl.click();
            await wait(500);
            return true;
          }
        }
      }
    }

    return false;
  }

  async function m1SelectYear(year) {
    var opened = await m1OpenYearDropdown();
    if (!opened) return false;
    await wait(1000);

    // Click the matching react-select option
    var optEls = document.querySelectorAll('[id*="react-select"][id*="option"]');
    for (var i = 0; i < optEls.length; i++) {
      var text = (optEls[i].innerText || optEls[i].textContent || '').trim();
      if (text === year) {
        optEls[i].click();
        await wait(1000);
        return true;
      }
    }

    // Fallback: menu container
    var menuEl = document.querySelector('[class*="-menu"]');
    if (menuEl) {
      var divs = menuEl.querySelectorAll('div');
      for (var j = 0; j < divs.length; j++) {
        var t = (divs[j].innerText || divs[j].textContent || '').trim();
        if (t === year) {
          divs[j].click();
          await wait(1000);
          return true;
        }
      }
    }

    // Close if failed
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    return false;
  }

  async function m1ClickLoadMore() {
    var maxClicks = 50;
    for (var i = 0; i < maxClicks; i++) {
      var buttons = document.querySelectorAll('button');
      var found = null;
      for (var j = 0; j < buttons.length; j++) {
        if (/load\s*more/i.test(buttons[j].textContent) && buttons[j].offsetParent !== null) {
          found = buttons[j];
          break;
        }
      }
      if (!found) break;
      found.click();
      await wait(2000);
    }
  }

  function m1ParseTable() {
    var statements = [];
    var rows = document.querySelectorAll('table tbody tr');

    for (var i = 0; i < rows.length; i++) {
      var cells = rows[i].querySelectorAll('td');
      if (cells.length < 3) continue;

      var dateText = (cells[0].innerText || cells[0].textContent || '').trim();
      var acctText = (cells[1].innerText || cells[1].textContent || '').trim();
      var linkEl = cells[2].querySelector('a');

      if (!linkEl) continue;

      var date = parseDate(dateText);
      if (!date) continue;

      // Parse account: "5ME90609 - Invest Individual" → ("Invest Individual", "0609")
      var accountLabel = 'm1finance0000';
      var accountType = 'M1 Finance';
      var accountLast4 = '0000';

      var parts = acctText.split(' - ');
      if (parts.length >= 2) {
        var acctId = parts[0].trim();
        accountType = parts.slice(1).join(' - ').trim();
        accountLast4 = acctId.length >= 4 ? acctId.slice(-4) : acctId;
        accountLabel = makeAccountLabel(accountType, accountLast4);
      }

      // Get download URL from href
      var href = linkEl.getAttribute('href') || '';
      var url = null;
      if (href && href !== '#') {
        url = href;
        if (url.startsWith('/')) {
          url = 'https://dashboard.m1.com' + url;
        }
      }

      var filename = makeBrokerageFilename(date, 'm1finance', accountLabel);

      statements.push({
        date: date,
        url: url,
        filename: filename,
        accountLabel: accountLabel,
        accountType: accountType,
        accountLast4: accountLast4,
        needsClick: !url
      });
    }

    return statements;
  }

  // ===== SCHWAB =====
  // Helper to check if cancel was requested
  async function checkCancel() {
    return new Promise(resolve => {
      chrome.storage.local.get('cancelRequested', result => {
        resolve(result.cancelRequested === true);
      });
    });
  }

  // Get Schwab accounts list (used by service worker to drive per-account scraping)
  async function schwabDiscoverAccounts() {
    console.log('[Schwab] Discovering accounts...');
    document.body.style.zoom = '0.75';
    await wait(3000);

    var accounts = await schwabGetAccounts();
    console.log('[Schwab] Found accounts:', accounts.length);
    return accounts;
  }

  // Prepare a Schwab account for scraping: select it, set filters, click search
  async function schwabPrepareAccount(account) {
    console.log('[Schwab] Preparing account:', account.label, '(' + account.type + ')');

    // Clear stored click elements from previous account
    statementClickElements = [];

    // Select account in dropdown
    await schwabSelectAccount(account);
    await wait(3000);

    // Set date range to "Last 10 years"
    await schwabSetDateRange();
    await wait(1000);

    // Set filter chips: only "Statements"
    await schwabSetFilterChips();
    await wait(1000);

    // Click Search
    await schwabClickSearch();
    await wait(3000);

    return { success: true };
  }

  // Scrape the CURRENT page of Schwab results (does NOT navigate to next page)
  function schwabScrapeCurrentPage(account) {
    // Clear stored click elements so they match only this page
    statementClickElements = [];

    // Scroll to load content
    window.scrollTo(0, document.body.scrollHeight);

    var stmts = schwabParseStatements(account);
    console.log('[Schwab] Current page:', stmts.length, 'statements');

    // Deduplicate
    var seen = {};
    var unique = [];
    for (var i = 0; i < stmts.length; i++) {
      var key = stmts[i].date + '_' + stmts[i].accountLabel;
      if (!seen[key]) {
        seen[key] = true;
        unique.push(stmts[i]);
      }
    }

    return unique;
  }

  // Go to the next page of Schwab results. Returns { hasNext: true/false }
  async function schwabGoToNextPage() {
    var hasNext = await schwabClickNext();
    if (hasNext) {
      await wait(3000);
      // Scroll to load content
      window.scrollTo(0, document.body.scrollHeight);
      await wait(1000);
      window.scrollBy(0, -200);
      await wait(500);
    }
    return { hasNext: hasNext };
  }

  // Legacy: scrape all accounts at once (kept as fallback for scrape_statements message)
  async function scrapeSchwab() {
    console.log('[Schwab] Starting full scrape (legacy)...');
    var accounts = await schwabDiscoverAccounts();
    var allStatements = [];
    for (var ai = 0; ai < accounts.length; ai++) {
      if (await checkCancel()) break;
      await schwabPrepareAccount(accounts[ai]);
      // Scrape all pages for this account
      while (true) {
        var stmts = schwabScrapeCurrentPage(accounts[ai]);
        allStatements = allStatements.concat(stmts);
        var next = await schwabGoToNextPage();
        if (!next.hasNext) break;
      }
    }
    return allStatements;
  }

  async function schwabGetAccounts() {
    var accounts = [];
    var selector = document.querySelector('#account-selector');
    if (!selector) {
      console.log('[Schwab] No #account-selector found, using default');
      return [{ type: 'Brokerage', last4: '0000', label: 'brokerage0000', text: '' }];
    }

    // Open dropdown
    selector.click();
    await wait(1500);

    var items = document.querySelectorAll('#account-selector-list ul li');
    console.log('[Schwab] Dropdown items:', items.length);

    for (var i = 0; i < items.length; i++) {
      var text = (items[i].innerText || items[i].textContent || '').trim();
      var textLower = text.toLowerCase();

      // Skip aggregate/closed items
      if (textLower.includes('all brokerage') || textLower.includes('show all') ||
          textLower.includes('closed and inactive')) continue;

      // Extract account number (last 4)
      var last4 = '0000';
      var numMatch = text.match(/Account ending in\s+([\d\s]+)$/);
      if (numMatch) {
        last4 = numMatch[1].replace(/\s/g, '').slice(-4).padStart(4, '0');
      } else {
        var ellipsisMatch = text.match(/[….\u2026]{1,}\s*(\d{3,4})/);
        if (ellipsisMatch) last4 = ellipsisMatch[1].slice(-4).padStart(4, '0');
      }

      // Extract account name
      var acctName = text;
      var dupMatch = text.match(/^(.+?)\s+\1/);
      if (dupMatch) {
        acctName = dupMatch[1].trim();
      } else {
        var beforeEllipsis = text.match(/^(.+?)\s*[….\u2026]/);
        if (beforeEllipsis) acctName = beforeEllipsis[1].trim();
      }

      var label = makeAccountLabel(acctName, last4);
      accounts.push({ type: acctName, last4: last4, label: label, text: text });
    }

    // Close dropdown
    selector.click();
    await wait(500);

    if (accounts.length === 0) {
      accounts.push({ type: 'Brokerage', last4: '0000', label: 'brokerage0000', text: '' });
    }

    return accounts;
  }

  async function schwabSelectAccount(account) {
    var selector = document.querySelector('#account-selector');
    if (!selector) return;

    selector.click();
    await wait(1000);

    var items = document.querySelectorAll('#account-selector-list ul li a');
    var last3 = account.last4.slice(-3);

    console.log('[Schwab] Looking for account ending in', last3, 'among', items.length, 'items');

    for (var i = 0; i < items.length; i++) {
      var text = (items[i].innerText || items[i].textContent || '').trim();
      console.log('[Schwab] Checking item:', text.substring(0, 30));
      // Match by last 3 digits (various formats: ..., •••, …, spaced)
      if (text.includes('...' + last3) || text.includes('\u2022\u2022\u2022' + last3) ||
          text.includes('\u2026' + last3) || text.includes(last3.split('').join(' '))) {
        // Dispatch mouse events on <a> to trigger Angular handlers,
        // but use a temporary listener to prevent the javascript: URL navigation
        // which would violate the extension's CSP
        var preventJsNav = function(e) { e.preventDefault(); };
        items[i].addEventListener('click', preventJsNav, true);
        items[i].dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
        items[i].dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
        items[i].dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
        items[i].removeEventListener('click', preventJsNav, true);
        console.log('[Schwab] Selected account:', text.substring(0, 50));
        await wait(1500);
        return;
      }
    }

    // Close if no match
    selector.click();
  }

  async function schwabSetDateRange() {
    var dateRange = document.querySelector('#date-range-select-id');
    if (!dateRange) {
      console.log('[Schwab] No date range select found');
      return;
    }

    // Try to select "Last 10 years"
    var options = dateRange.querySelectorAll('option');
    for (var i = 0; i < options.length; i++) {
      if (/last\s*10\s*years/i.test(options[i].text) || options[i].value === 'last10years') {
        dateRange.value = options[i].value;
        dateRange.dispatchEvent(new Event('change', { bubbles: true }));
        console.log('[Schwab] Set date range to Last 10 years');
        return;
      }
    }
  }

  async function schwabSetFilterChips() {
    var chipContainer = document.querySelector('#chip-buttons');
    if (!chipContainer) {
      console.log('[Schwab] No chip-buttons found');
      return;
    }

    var chips = chipContainer.querySelectorAll('button, [role="button"]');
    for (var i = 0; i < chips.length; i++) {
      var text = (chips[i].textContent || '').trim().toLowerCase();
      var isActive = chips[i].getAttribute('aria-pressed') === 'true' ||
                     chips[i].classList.contains('active') ||
                     chips[i].classList.contains('selected');

      if (text.includes('statement')) {
        // Ensure Statements is checked
        if (!isActive) {
          chips[i].click();
          await wait(300);
        }
      } else if (text.includes('tax') || text.includes('letter') ||
                 text.includes('report') || text.includes('trade') ||
                 text.includes('confirm')) {
        // Uncheck non-statement chips
        if (isActive) {
          chips[i].click();
          await wait(300);
        }
      }
    }
  }

  async function schwabClickSearch() {
    var buttons = document.querySelectorAll('button');
    for (var i = 0; i < buttons.length; i++) {
      if (/^Search$/i.test(buttons[i].textContent.trim())) {
        buttons[i].click();
        console.log('[Schwab] Clicked Search');
        return;
      }
    }
  }

  function schwabParseStatements(account) {
    var statements = [];

    // Find PDF links
    var selectors = 'a, button, [role="button"]';
    var elements = document.querySelectorAll(selectors);

    for (var i = 0; i < elements.length; i++) {
      var el = elements[i];
      var text = (el.textContent || '').trim();
      if (!/PDF/i.test(text)) continue;

      // Get the download URL
      var href = el.getAttribute('href') || '';
      var url = null;
      if (href && href !== '#' && href !== 'javascript:void(0)') {
        url = href;
        if (url.startsWith('/')) url = 'https://client.schwab.com' + url;
      }

      // Find date from the row container
      var row = el.closest('div[class*="row"], li, [role="row"], div[class*="statement"], div[class*="document"]');
      var rowText = row ? (row.textContent || '') : (el.parentElement ? el.parentElement.textContent : '');
      var date = parseDate(rowText);

      if (!date) continue;

      var filename = makeBrokerageFilename(date, 'schwab', account.label);

      // Store clickable element for later if no direct URL
      if (!url) {
        statementClickElements.push({
          date: date,
          element: el,
          text: rowText
        });
      }

      statements.push({
        date: date,
        url: url,
        filename: filename,
        accountLabel: account.label,
        accountType: account.type,
        accountLast4: account.last4,
        needsClick: !url
      });
    }

    return statements;
  }

  async function schwabClickNext() {
    var selectors = [
      'button', 'a', '[aria-label="Next"]', '[aria-label="Go to next page"]'
    ];

    for (var s = 0; s < selectors.length; s++) {
      var elements = document.querySelectorAll(selectors[s]);
      for (var i = 0; i < elements.length; i++) {
        var el = elements[i];
        var text = (el.textContent || '').trim();
        var label = el.getAttribute('aria-label') || '';

        if (text === 'Next' || label === 'Next' || label === 'Go to next page') {
          if (el.disabled || el.getAttribute('aria-disabled') === 'true' || el.offsetParent === null) {
            return false;
          }
          if (el.tagName === 'A' && el.href && el.href.startsWith('javascript:')) {
            el.removeAttribute('href');
          }
          el.click();
          console.log('[Schwab] Clicked Next');
          return true;
        }
      }
    }
    return false;
  }

  // ===== FIDELITY =====
  async function scrapeFidelity() {
    console.log('[Fidelity] Starting scrape...');
    await wait(3000);

    var allStatements = [];

    // Read year options from dropdown
    var years = fidelityGetYearOptions();
    console.log('[Fidelity] Year options:', years.join(', '));

    if (years.length === 0) {
      // Just parse whatever is visible
      await fidelityLoadMore();
      return fidelityParseTable();
    }

    // Process each year (oldest first for completeness)
    years.sort(function(a, b) { return parseInt(a) - parseInt(b); });

    for (var yi = 0; yi < years.length; yi++) {
      var year = years[yi];
      console.log('[Fidelity] Selecting year:', year);

      await fidelitySelectYear(year);
      await wait(3000);

      // Click "Load more" until gone
      await fidelityLoadMore();

      // Parse table
      var stmts = fidelityParseTable();
      console.log('[Fidelity] Year', year, ':', stmts.length, 'statements');
      allStatements = allStatements.concat(stmts);
    }

    // Deduplicate
    var seen = {};
    var unique = [];
    for (var i = 0; i < allStatements.length; i++) {
      var key = allStatements[i].date + '_' + allStatements[i].accountLabel;
      if (!seen[key]) {
        seen[key] = true;
        unique.push(allStatements[i]);
      }
    }

    console.log('[Fidelity] Total unique statements:', unique.length);
    return unique;
  }

  function fidelityGetYearOptions() {
    var years = [];

    // Try to open dropdown and read options
    var dropdownBtn = document.querySelector('#select-button') ||
                      document.querySelector('#select-component button') ||
                      document.querySelector('[id*="select"] button') ||
                      document.querySelector('button[aria-haspopup="listbox"]');

    if (!dropdownBtn) return years;

    dropdownBtn.click();

    // Read options
    var options = document.querySelectorAll('#select-component li, [role="option"], [role="listbox"] li, ul[id*="select"] li');
    for (var i = 0; i < options.length; i++) {
      var text = (options[i].textContent || '').trim();
      if (/^\d{4}$/.test(text)) {
        years.push(text);
      }
    }

    // Close dropdown
    dropdownBtn.click();
    return years;
  }

  async function fidelitySelectYear(year) {
    var dropdownBtn = document.querySelector('#select-button') ||
                      document.querySelector('#select-component button') ||
                      document.querySelector('[id*="select"] button') ||
                      document.querySelector('button[aria-haspopup="listbox"]');
    if (!dropdownBtn) return;

    dropdownBtn.click();
    await wait(500);

    var options = document.querySelectorAll('#select-component li, [role="option"], [role="listbox"] li, ul[id*="select"] li');
    for (var i = 0; i < options.length; i++) {
      if (options[i].textContent.trim() === year) {
        options[i].click();
        return;
      }
    }

    dropdownBtn.click(); // close if not found
  }

  async function fidelityPrepareYear(year) {
    console.log('[Fidelity] Preparing year:', year);

    // Select the specified year
    await fidelitySelectYear(year);
    await wait(3000);

    // Click "Load more" until gone (refresh the statement table for the selected year)
    await fidelityLoadMore();

    console.log('[Fidelity] Year prepared:', year);
  }

  async function fidelityLoadMore() {
    var maxClicks = 50;
    for (var i = 0; i < maxClicks; i++) {
      var found = null;
      var elements = document.querySelectorAll('a, button');
      for (var j = 0; j < elements.length; j++) {
        if (/load\s*more/i.test(elements[j].textContent) && elements[j].offsetParent !== null) {
          found = elements[j];
          break;
        }
      }
      if (!found) break;
      found.click();
      await wait(2000);
    }
  }

  function fidelityParseTable() {
    var statements = [];
    var rows = document.querySelectorAll('table tbody tr');

    for (var i = 0; i < rows.length; i++) {
      var cells = rows[i].querySelectorAll('td');
      if (cells.length < 2) continue;

      // Find description cell (contains "statement")
      var descText = '';
      var acctText = '';
      var downloadBtn = null;

      for (var c = 0; c < cells.length; c++) {
        var cellText = (cells[c].textContent || '').trim();
        if (/statement/i.test(cellText) && !/year.?end|annual.?report/i.test(cellText)) {
          descText = cellText;
        }
        if (/\d{6,}/.test(cellText)) {
          acctText = cellText;
        }
        // Find download button in this row
        var btn = cells[c].querySelector('button.downloadIconButton') ||
                  cells[c].querySelector('button[aria-label*="ownload"]');
        if (btn) downloadBtn = btn;
      }

      if (!descText) continue;

      // Parse date from description
      var date = fidelityParseDescription(descText);
      if (!date) continue;

      // Parse account
      var accountLabel = 'all0000';
      var accountType = 'All';
      var accountLast4 = '0000';

      if (acctText) {
        var acctMatch = acctText.match(/^(.+?)\s+(\d{6,})\b/);
        if (acctMatch) {
          accountType = acctMatch[1].trim();
          accountLast4 = acctMatch[2].slice(-4);
          accountLabel = makeAccountLabel(accountType, accountLast4);
        } else {
          var maskedMatch = acctText.match(/^(.+?)\s*\*+(\d{4})/);
          if (maskedMatch) {
            accountType = maskedMatch[1].trim();
            accountLast4 = maskedMatch[2];
            accountLabel = makeAccountLabel(accountType, accountLast4);
          }
        }
      }

      var filename = makeBrokerageFilename(date, 'fidelity', accountLabel);

      // Store the download button reference for clicking later
      if (downloadBtn) {
        statementClickElements.push({
          date: date,
          element: downloadBtn,
          text: descText.substring(0, 100)
        });
      }

      statements.push({
        date: date,
        url: null,
        filename: filename,
        accountLabel: accountLabel,
        accountType: accountType,
        accountLast4: accountLast4,
        needsClick: true
      });
    }

    return statements;
  }

  function fidelityParseDescription(text) {
    // Skip year-end/annual
    if (/year.?end|annual.?report/i.test(text)) return null;
    if (!/statement/i.test(text)) return null;

    // Extract year
    var yearMatch = text.match(/(\d{4})/);
    if (!yearMatch) return null;
    var year = yearMatch[1];

    // Check for month range pattern: "Month1-Month2 YYYY"
    // e.g. "July-Sep 2021", "Oct-Dec 2021"
    var monthMap = {
      january: '01', february: '02', march: '03', april: '04',
      may: '05', june: '06', july: '07', august: '08',
      september: '09', october: '10', november: '11', december: '12',
      jan: '01', feb: '02', mar: '03', apr: '04',
      jun: '06', jul: '07', aug: '08',
      sep: '09', oct: '10', nov: '11', dec: '12'
    };

    // Try to match month range pattern
    var rangePattern = /([A-Za-z]+)\s*[-–]\s*([A-Za-z]+)/i;
    var rangeMatch = text.match(rangePattern);
    if (rangeMatch) {
        var m1 = rangeMatch[1].toLowerCase();
        var m2 = rangeMatch[2].toLowerCase();
        var mm1 = monthMap[m1];
        var mm2 = monthMap[m2];
        if (mm1 && mm2) {
            return year + '-' + mm1 + '-' + mm2;
        }
    }

    // Single month: find the first month name in the text
    var textLower = text.toLowerCase();
    for (var mName in monthMap) {
        if (textLower.includes(mName)) {
            return year + '-' + monthMap[mName];
        }
    }

    return null;
  }

  // ===== WEBULL =====
  async function scrapeWebull() {
    console.log('[Webull] Starting scrape...');
    document.body.style.zoom = '0.75';
    await wait(3000);

    var allStatements = [];

    // Click "Account Statement" tab
    var tabClicked = false;
    var tabLabels = ['Account Statement', 'Account Statements', 'Statements', 'Monthly Statement'];
    for (var t = 0; t < tabLabels.length; t++) {
      var elements = document.querySelectorAll('a, button, div[role="tab"], span');
      for (var e = 0; e < elements.length; e++) {
        if (elements[e].textContent.trim().toLowerCase() === tabLabels[t].toLowerCase() &&
            elements[e].offsetParent !== null) {
          elements[e].click();
          tabClicked = true;
          console.log('[Webull] Clicked tab:', tabLabels[t]);
          break;
        }
      }
      if (tabClicked) break;
    }
    await wait(3000);

    // Get accounts from dropdown
    var accounts = webullGetAccounts();
    if (accounts.length === 0) {
      accounts = [{ type: 'Individual', last4: '0000', label: 'individual0000' }];
    }

    for (var ai = 0; ai < accounts.length; ai++) {
      var account = accounts[ai];
      console.log('[Webull] Processing account:', account.label);

      if (accounts.length > 1) {
        await webullSelectAccount(account);
        await wait(2000);
      }

      // Wait for calendar
      await webullWaitForCalendar();

      // Iterate through calendar years
      var emptyYears = 0;
      var maxYears = 10;

      for (var y = 0; y < maxYears; y++) {
        var currentYear = webullGetCurrentYear();
        if (!currentYear) break;
        console.log('[Webull] Calendar year:', currentYear);

        var monthStatements = await webullScrapeCalendarYear(currentYear, account);
        console.log('[Webull] Year', currentYear, ':', monthStatements.length, 'statements');
        allStatements = allStatements.concat(monthStatements);

        if (monthStatements.length === 0) {
          emptyYears++;
          if (emptyYears >= 2) {
            console.log('[Webull] Two consecutive empty years, stopping');
            break;
          }
        } else {
          emptyYears = 0;
        }

        // Navigate to previous year
        if (!await webullNavigatePrevYear()) break;
        await wait(2000);
      }
    }

    console.log('[Webull] Total statements:', allStatements.length);
    return allStatements;
  }

  function webullGetAccounts() {
    var accounts = [];

    // Try .g-input dropdown
    var dropdown = document.querySelector('.g-input');
    if (dropdown) {
      var text = (dropdown.textContent || '').trim();
      var match = text.match(/^(.+?)\s*\(([A-Za-z0-9]+)\)\s*$/);
      if (match) {
        var last4 = match[2].slice(-4);
        accounts.push({ type: match[1].trim(), last4: last4, label: makeAccountLabel(match[1].trim(), last4) });
      }
    }

    // Try native select
    var selects = document.querySelectorAll('select');
    for (var i = 0; i < selects.length; i++) {
      var sel = selects[i];
      if (sel.options.length > 0 && sel.selectedIndex >= 0) {
        var optText = sel.options[sel.selectedIndex].text.trim();
        var m = optText.match(/^(.+?)\s*\(([A-Za-z0-9]+)\)\s*$/);
        if (m) {
          var l4 = m[2].slice(-4);
          accounts.push({ type: m[1].trim(), last4: l4, label: makeAccountLabel(m[1].trim(), l4) });
        }
      }
    }

    return accounts;
  }

  async function webullSelectAccount(account) {
    // Try native select
    var selects = document.querySelectorAll('select');
    for (var i = 0; i < selects.length; i++) {
      for (var j = 0; j < selects[i].options.length; j++) {
        if (selects[i].options[j].text.includes(account.last4)) {
          selects[i].value = selects[i].options[j].value;
          selects[i].dispatchEvent(new Event('change', { bubbles: true }));
          return;
        }
      }
    }

    // Try custom dropdown
    var dropdown = document.querySelector('.g-input');
    if (dropdown) {
      dropdown.click();
      await wait(500);
      var options = document.querySelectorAll('[role="option"]');
      for (var k = 0; k < options.length; k++) {
        if (options[k].textContent.includes(account.last4)) {
          options[k].click();
          return;
        }
      }
    }
  }

  async function webullWaitForCalendar() {
    // Poll for month names to appear
    for (var i = 0; i < 20; i++) {
      var found = false;
      var els = document.querySelectorAll('span, div, td, th, p');
      for (var j = 0; j < els.length; j++) {
        if (els[j].textContent.trim() === 'January' && els[j].offsetParent !== null) {
          found = true;
          break;
        }
      }
      if (found) return;
      await wait(500);
    }
  }

  function webullGetCurrentYear() {
    var els = document.querySelectorAll('span, div, p, h1, h2, h3, h4, td, th');
    for (var i = 0; i < els.length; i++) {
      var directText = '';
      for (var c = 0; c < els[i].childNodes.length; c++) {
        if (els[i].childNodes[c].nodeType === 3) {
          directText += els[i].childNodes[c].textContent;
        }
      }
      directText = directText.trim();
      if (/^\s*(20\d{2})\s*$/.test(directText)) {
        return directText.trim();
      }
    }
    return null;
  }

  async function webullScrapeCalendarYear(year, account) {
    var statements = [];
    var months = ['January','February','March','April','May','June',
                  'July','August','September','October','November','December'];
    var monthNums = ['01','02','03','04','05','06','07','08','09','10','11','12'];

    for (var m = 0; m < months.length; m++) {
      var monthCell = webullFindMonthCell(months[m]);
      if (!monthCell) continue;

      // Check if disabled
      if (webullIsMonthDisabled(monthCell)) continue;

      var date = year + '-' + monthNums[m];
      var filename = makeBrokerageFilename(date, 'webull', account.label);

      statementClickElements.push({
        date: date,
        element: monthCell,
        text: months[m] + ' ' + year
      });

      statements.push({
        date: date,
        url: null,
        filename: filename,
        accountLabel: account.label,
        accountType: account.type,
        accountLast4: account.last4,
        needsClick: true
      });
    }

    return statements;
  }

  function webullFindMonthCell(monthName) {
    var els = document.querySelectorAll('span, div, td, th, p, button, a');
    for (var i = 0; i < els.length; i++) {
      if (els[i].textContent.trim() === monthName && els[i].offsetParent !== null) {
        return els[i];
      }
    }
    return null;
  }

  function webullIsMonthDisabled(element) {
    // Check element and up to 3 ancestors
    var current = element;
    for (var i = 0; i < 4; i++) {
      if (!current) break;
      if (current.hasAttribute('disabled') || current.getAttribute('aria-disabled') === 'true') return true;
      var cls = (current.className || '').toLowerCase();
      if (/disabled|inactive|unavailable|grey|gray/.test(cls)) return true;
      var style = window.getComputedStyle(current);
      if (style.pointerEvents === 'none' || parseFloat(style.opacity) < 0.5) return true;
      current = current.parentElement;
    }
    return false;
  }

  async function webullNavigatePrevYear() {
    // Find year text element, then look for left arrow near it
    var yearEl = null;
    var els = document.querySelectorAll('span, div, p, h1, h2, h3, h4');
    for (var i = 0; i < els.length; i++) {
      var directText = '';
      for (var c = 0; c < els[i].childNodes.length; c++) {
        if (els[i].childNodes[c].nodeType === 3) directText += els[i].childNodes[c].textContent;
      }
      if (/^\s*(20\d{2})\s*$/.test(directText.trim())) {
        yearEl = els[i];
        break;
      }
    }

    if (!yearEl) return false;

    var yearRect = yearEl.getBoundingClientRect();

    // Look for clickable element to the LEFT of the year
    var clickables = document.querySelectorAll('button, a, svg, [role="button"]');
    for (var j = 0; j < clickables.length; j++) {
      var rect = clickables[j].getBoundingClientRect();
      if (rect.right <= yearRect.left + 5 && Math.abs(rect.top - yearRect.top) < 20) {
        clickables[j].click();
        await wait(1000);
        return true;
      }
    }

    // Fallback CSS selectors
    var fallbacks = ["button[class*='prev']", "[class*='arrow-left']", "[class*='arrowLeft']", "button[aria-label*='previous']"];
    for (var f = 0; f < fallbacks.length; f++) {
      var btn = document.querySelector(fallbacks[f]);
      if (btn && btn.offsetParent !== null) {
        btn.click();
        await wait(1000);
        return true;
      }
    }

    return false;
  }

  // ===== VANGUARD =====
  async function scrapeVanguard() {
    var url = window.location.href;
    console.log('[Vanguard] Starting scrape, URL:', url);
    document.body.style.zoom = '0.75';
    await wait(3000);

    if (url.includes('ownyourfuture.vanguard.com')) {
      return await scrapeVanguardEmployer();
    } else {
      return await scrapeVanguardPersonal();
    }
  }

  async function scrapeVanguardPersonal() {
    console.log('[Vanguard] Personal Investor flow');

    // Click "Statements" sub-tab if present
    var tabs = document.querySelectorAll('a, button, [role="tab"]');
    for (var t = 0; t < tabs.length; t++) {
      if (tabs[t].textContent.trim() === 'Statements' && tabs[t].offsetParent !== null) {
        tabs[t].click();
        await wait(2000);
        break;
      }
    }

    var allStatements = [];

    // Read year options from select
    var yearSelect = vanguardFindYearSelect();
    var years = [];
    if (yearSelect) {
      for (var i = 0; i < yearSelect.options.length; i++) {
        var val = (yearSelect.options[i].value || yearSelect.options[i].text).trim();
        if (/^\d{4}$/.test(val)) years.push(val);
      }
    }

    if (years.length === 0) {
      console.log('[Vanguard] No year options, parsing visible table');
      return vanguardParsePersonalTable();
    }

    years.sort(function(a, b) { return parseInt(a) - parseInt(b); }); // oldest first

    for (var yi = 0; yi < years.length; yi++) {
      var year = years[yi];
      console.log('[Vanguard] Selecting year:', year);

      // Set year
      if (yearSelect) {
        yearSelect.value = year;
        yearSelect.dispatchEvent(new Event('change', { bubbles: true }));
        await wait(1000);
      }

      // Set month to "All months" if possible
      var monthSelect = vanguardFindMonthSelect();
      if (monthSelect) {
        for (var mi = 0; mi < monthSelect.options.length; mi++) {
          if (/all/i.test(monthSelect.options[mi].text)) {
            monthSelect.value = monthSelect.options[mi].value;
            monthSelect.dispatchEvent(new Event('change', { bubbles: true }));
            break;
          }
        }
        await wait(500);
      }

      // Click "Update Table"
      var updateBtns = document.querySelectorAll('button, a, input[type="submit"]');
      for (var u = 0; u < updateBtns.length; u++) {
        if (/update\s*table/i.test(updateBtns[u].textContent)) {
          updateBtns[u].click();
          await wait(3000);
          break;
        }
      }

      var stmts = vanguardParsePersonalTable();
      console.log('[Vanguard] Year', year, ':', stmts.length, 'statements');
      allStatements = allStatements.concat(stmts);
    }

    // Deduplicate
    var seen = {};
    var unique = [];
    for (var d = 0; d < allStatements.length; d++) {
      var key = allStatements[d].date + '_' + allStatements[d].accountLabel;
      if (!seen[key]) {
        seen[key] = true;
        unique.push(allStatements[d]);
      }
    }

    console.log('[Vanguard] Total unique statements:', unique.length);
    return unique;
  }

  function vanguardFindYearSelect() {
    var selects = document.querySelectorAll('select');
    for (var i = 0; i < selects.length; i++) {
      var hasYear = false;
      for (var j = 0; j < selects[i].options.length; j++) {
        if (/^\d{4}$/.test(selects[i].options[j].text.trim())) {
          hasYear = true;
          break;
        }
      }
      if (hasYear) return selects[i];
    }
    return null;
  }

  function vanguardFindMonthSelect() {
    var selects = document.querySelectorAll('select');
    for (var i = 0; i < selects.length; i++) {
      var hasMonth = false;
      for (var j = 0; j < selects[i].options.length; j++) {
        if (/jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec/i.test(selects[i].options[j].text)) {
          hasMonth = true;
          break;
        }
      }
      if (hasMonth) return selects[i];
    }
    return null;
  }

  function vanguardParsePersonalTable() {
    var statements = [];
    var tables = document.querySelectorAll('table');

    for (var ti = 0; ti < tables.length; ti++) {
      var rows = tables[ti].querySelectorAll('tbody tr');
      for (var ri = 0; ri < rows.length; ri++) {
        var cells = rows[ri].querySelectorAll('td');
        if (cells.length < 2) continue;

        var dateText = (cells[0].textContent || '').trim();
        var acctText = cells.length > 1 ? (cells[1].textContent || '').trim() : '';
        var lastCell = cells[cells.length - 1];

        // Check if last cell has a download link
        var hasDownload = lastCell.querySelector('a, button, [role="button"], svg');
        if (!hasDownload) continue;

        var date = parseDate(dateText);
        if (!date) continue;

        // Parse account from text like "Roth IRA - 1234"
        var accountLabel = 'personalinvestor0000';
        var accountType = 'Personal Investor';
        var accountLast4 = '0000';

        var acctMatch = acctText.match(/(\d{4})\s*$/);
        if (acctMatch) {
          accountLast4 = acctMatch[1];
          accountType = acctText.replace(/[-\u2013\u2014]\s*\d{4}\s*$/, '').replace(/^Vanguard\s+/i, '').trim();
          accountLabel = makeAccountLabel(accountType, accountLast4);
        } else {
          var maskedMatch = acctText.match(/[*.\-]+(\d{4})/);
          if (maskedMatch) {
            accountLast4 = maskedMatch[1];
            accountType = acctText.replace(/[*.\-]+\d{4}.*$/, '').replace(/^Vanguard\s+/i, '').trim();
            accountLabel = makeAccountLabel(accountType, accountLast4);
          }
        }

        var filename = makeBrokerageFilename(date, 'vanguard', accountLabel);

        // Store download element for clicking
        statementClickElements.push({
          date: date + '_' + accountLabel,
          element: hasDownload,
          text: dateText + ' ' + acctText
        });

        statements.push({
          date: date,
          url: null,
          filename: filename,
          accountLabel: accountLabel,
          accountType: accountType,
          accountLast4: accountLast4,
          needsClick: true
        });
      }
    }

    return statements;
  }

  async function scrapeVanguardEmployer() {
    console.log('[Vanguard] Employer Plan flow');

    // Click "Statements" heading arrow
    var headings = document.querySelectorAll('h2, h3, h4');
    for (var h = 0; h < headings.length; h++) {
      var directText = '';
      for (var c = 0; c < headings[h].childNodes.length; c++) {
        if (headings[h].childNodes[c].nodeType === 3) directText += headings[h].childNodes[c].textContent;
      }
      if (/^Statements$/i.test(directText.trim())) {
        // Find clickable to the right of heading
        var parent = headings[h].parentElement || headings[h];
        var clickables = parent.querySelectorAll('a, svg, button, [role="link"], [role="button"]');
        if (clickables.length > 0) {
          clickables[clickables.length - 1].click();
          await wait(3000);
          break;
        }
      }
    }

    // Click "Show more" / "View more" until gone
    var moreLabels = ['Show more', 'View more', 'Load more', 'Show all'];
    for (var iter = 0; iter < 20; iter++) {
      var foundMore = false;
      for (var ml = 0; ml < moreLabels.length; ml++) {
        var btns = document.querySelectorAll('a, button');
        for (var b = 0; b < btns.length; b++) {
          if (btns[b].textContent.trim().toLowerCase() === moreLabels[ml].toLowerCase() &&
              btns[b].offsetParent !== null) {
            btns[b].click();
            foundMore = true;
            await wait(2000);
            break;
          }
        }
        if (foundMore) break;
      }
      if (!foundMore) break;
    }

    // Parse download buttons
    var statements = [];
    var downloadBtns = [];
    var allBtns = document.querySelectorAll('button, a, [role="button"]');
    for (var d = 0; d < allBtns.length; d++) {
      if (/Download/i.test(allBtns[d].textContent)) {
        downloadBtns.push(allBtns[d]);
      }
    }

    var monthMap = {
      jan: '01', feb: '02', mar: '03', apr: '04', may: '05', jun: '06',
      jul: '07', aug: '08', sep: '09', oct: '10', nov: '11', dec: '12',
      january: '01', february: '02', march: '03', april: '04', june: '06',
      july: '07', august: '08', september: '09', october: '10', november: '11', december: '12'
    };

    for (var di = 0; di < downloadBtns.length; di++) {
      var btn = downloadBtns[di];
      // Walk up to find quarter text and year
      var quarterText = '';
      var year = '';
      var ancestor = btn;

      for (var up = 0; up < 5; up++) {
        ancestor = ancestor.parentElement;
        if (!ancestor) break;
        var aText = (ancestor.textContent || '').trim();

        // Look for month range pattern
        if (!quarterText) {
          var qMatch = aText.match(/(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s*[-\u2013]\s*(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*/i);
          if (qMatch) quarterText = qMatch[0];
        }
        if (!year) {
          var yMatch = aText.match(/\b(20\d{2})\b/);
          if (yMatch) year = yMatch[1];
        }
      }

      if (!year) continue;

      // Parse quarter to get start month
      var date = year + '-01';
      if (quarterText) {
        var parts = quarterText.toLowerCase().split(/[-\u2013\u2014]/);
        if (parts.length >= 1) {
          var startMonth = parts[0].trim().replace(/[^a-z]/g, '').substring(0, 3);
          if (monthMap[startMonth]) {
            date = year + '-' + monthMap[startMonth];
          }
        }
      }

      // Extract plan name from page
      var planLabel = 'employerplan0000';

      var filename = makeBrokerageFilename(date, 'vanguard', planLabel);

      statementClickElements.push({
        date: date + '_' + planLabel,
        element: btn,
        text: quarterText + ' ' + year
      });

      statements.push({
        date: date,
        url: null,
        filename: filename,
        accountLabel: planLabel,
        accountType: 'Employer Plan',
        accountLast4: '0000',
        needsClick: true
      });
    }

    console.log('[Vanguard] Employer plan statements:', statements.length);
    return statements;
  }

  // ===== E*TRADE =====
  async function scrapeEtrade() {
    console.log('[ETrade] Starting scrape...');
    document.body.style.zoom = '0.75';
    await wait(3000);

    // Click "Statements" tab
    var tabs = document.querySelectorAll('button, a, [role="tab"], [role="button"], li');
    for (var t = 0; t < tabs.length; t++) {
      if (/^Statements$/i.test(tabs[t].textContent.trim())) {
        tabs[t].click();
        console.log('[ETrade] Clicked Statements tab');
        await wait(2000);
        break;
      }
    }

    var allStatements = [];

    // Find timeframe options
    var timeframeOptions = etradeGetTimeframeOptions();
    console.log('[ETrade] Timeframe options:', timeframeOptions.length);

    if (timeframeOptions.length === 0) {
      // Parse whatever is visible
      return etradeParseStatements();
    }

    for (var ti = 0; ti < timeframeOptions.length; ti++) {
      var opt = timeframeOptions[ti];
      console.log('[ETrade] Selecting timeframe:', opt.text);

      await etradeSelectTimeframe(opt);
      await wait(1000);

      // Click "Apply" if present
      await etradeClickApply();
      await wait(3000);

      // Parse all pages
      var pageNum = 0;
      while (true) {
        pageNum++;
        var stmts = etradeParseStatements();
        console.log('[ETrade] Timeframe', opt.text, 'page', pageNum, ':', stmts.length, 'statements');
        allStatements = allStatements.concat(stmts);

        if (!await etradeClickNext()) break;
        await wait(3000);
      }
    }

    // Deduplicate by full date + account
    var seen = {};
    var unique = [];
    for (var i = 0; i < allStatements.length; i++) {
      var key = allStatements[i].date + '_' + allStatements[i].accountLabel;
      if (!seen[key]) {
        seen[key] = true;
        unique.push(allStatements[i]);
      }
    }

    console.log('[ETrade] Total unique statements:', unique.length);
    return unique;
  }

  function etradeGetTimeframeOptions() {
    var options = [];

    // Try native select
    var selects = document.querySelectorAll('select');
    for (var i = 0; i < selects.length; i++) {
      var hasTimeframe = false;
      for (var j = 0; j < selects[i].options.length; j++) {
        var text = selects[i].options[j].text.toLowerCase();
        if (/year.to.date|ytd|\d{4}/.test(text)) {
          hasTimeframe = true;
          break;
        }
      }
      if (hasTimeframe) {
        // YTD first, then years newest to oldest
        var ytdOpts = [];
        var yearOpts = [];
        for (var k = 0; k < selects[i].options.length; k++) {
          var t = selects[i].options[k].text.trim();
          var v = selects[i].options[k].value;
          if (/year.to.date|ytd/i.test(t)) {
            ytdOpts.push({ type: 'select', selectIndex: i, value: v, text: t });
          } else if (/^\d{4}$/.test(t)) {
            yearOpts.push({ type: 'select', selectIndex: i, value: v, text: t });
          }
        }
        yearOpts.sort(function(a, b) { return parseInt(b.text) - parseInt(a.text); });
        options = ytdOpts.concat(yearOpts);
        return options;
      }
    }

    // Try custom dropdown
    var triggers = document.querySelectorAll('button, a, div[role="button"], span');
    for (var c = 0; c < triggers.length; c++) {
      var trigText = triggers[c].textContent.trim();
      if (/Timeframe|Last \d+\s*Days?|Year To Date|YTD|\b20\d{2}\b/i.test(trigText)) {
        options.push({ type: 'custom', element: triggers[c], text: trigText });
        return options;
      }
    }

    return options;
  }

  async function etradeSelectTimeframe(opt) {
    if (opt.type === 'select') {
      var sel = document.querySelectorAll('select')[opt.selectIndex];
      if (sel) {
        sel.value = opt.value;
        sel.dispatchEvent(new Event('change', { bubbles: true }));
      }
    } else if (opt.type === 'custom') {
      opt.element.click();
      await wait(500);
      // Look for menu items
      var items = document.querySelectorAll('[role="option"], [role="menuitem"], li, .dropdown-item');
      for (var i = 0; i < items.length; i++) {
        if (items[i].textContent.trim() === opt.text) {
          items[i].click();
          break;
        }
      }
    }
  }

  async function etradeClickApply() {
    var elements = document.querySelectorAll('button, [role="button"], input[type="submit"]');
    for (var i = 0; i < elements.length; i++) {
      if (/^Apply$/i.test(elements[i].textContent.trim())) {
        elements[i].click();
        console.log('[ETrade] Clicked Apply');
        return;
      }
    }
  }

  function etradeParseStatements() {
    var statements = [];

    // Strategy 0: Slot-based (preferred for Morgan Stanley components)
    var pdfSlots = document.querySelectorAll('div[slot="pdfLinkData"]');
    if (pdfSlots.length > 0) {
      console.log('[ETrade] Using slot-based strategy, found', pdfSlots.length, 'PDF slots');

      // Find date slots and account slots
      var allSlots = document.querySelectorAll('div[slot]');
      var dateSlots = [];
      var acctSlots = [];

      for (var s = 0; s < allSlots.length; s++) {
        var text = (allSlots[s].textContent || '').trim();
        if (/\d{1,2}\/\d{1,2}\/\d{2,4}/.test(text)) {
          dateSlots.push(allSlots[s]);
        } else if (/[A-Za-z].*\d{4}/.test(text) && !/\d{1,2}\/\d{1,2}/.test(text)) {
          acctSlots.push(allSlots[s]);
        }
      }

      // Match by row index
      for (var p = 0; p < pdfSlots.length; p++) {
        var link = pdfSlots[p].querySelector('a[role="link"], a.ms-link, a');
        if (!link) continue;

        var href = link.getAttribute('href') || '';
        var url = null;
        if (href && href !== '#') {
          url = href;
          if (url.startsWith('/')) url = 'https://us.etrade.com' + url;
        }

        // Get date
        var dateText = p < dateSlots.length ? dateSlots[p].textContent.trim() : '';
        var date = etradeParseDate(dateText);
        if (!date) continue;

        // Get account
        var accountLabel = 'etrade0000';
        var accountType = 'E*Trade';
        var accountLast4 = '0000';
        if (p < acctSlots.length) {
          var acctText = acctSlots[p].textContent.trim();
          var acctMatch = acctText.match(/(\d{4})\s*$/);
          if (acctMatch) {
            accountLast4 = acctMatch[1];
            accountType = acctText.replace(/\d{4}\s*$/, '').trim();
            accountLabel = makeAccountLabel(accountType, accountLast4);
          }
        }

        // Check if this is a "Statement" type document
        var rowText = '';
        var rowContainer = pdfSlots[p].closest('div[class*="row"], tr, [role="row"]');
        if (rowContainer) rowText = rowContainer.textContent;
        if (rowText && !/Statement/i.test(rowText)) continue;

        var filename = makeBrokerageFilename(date, 'etrade', accountLabel);

        if (url) {
          statements.push({
            date: date, url: url, filename: filename,
            accountLabel: accountLabel, accountType: accountType, accountLast4: accountLast4
          });
        } else {
          statementClickElements.push({ date: date, element: link, text: dateText });
          statements.push({
            date: date, url: null, filename: filename,
            accountLabel: accountLabel, accountType: accountType, accountLast4: accountLast4,
            needsClick: true
          });
        }
      }

      return statements;
    }

    // Strategy 1: Shadow DOM traversal
    var shadowHosts = document.querySelectorAll('ms-table-wc, [class*="ms-table"]');
    for (var sh = 0; sh < shadowHosts.length; sh++) {
      if (shadowHosts[sh].shadowRoot) {
        var shadowStatements = etradeParseFromShadow(shadowHosts[sh].shadowRoot);
        statements = statements.concat(shadowStatements);
      }
    }
    if (statements.length > 0) return statements;

    // Strategy 2: Native table
    var tables = document.querySelectorAll('table');
    for (var ti = 0; ti < tables.length; ti++) {
      var rows = tables[ti].querySelectorAll('tbody tr');
      for (var ri = 0; ri < rows.length; ri++) {
        var cells = rows[ri].querySelectorAll('td');
        var dateCell = null, acctCell = null, docCell = null;

        for (var ci = 0; ci < cells.length; ci++) {
          var cellText = cells[ci].textContent.trim();
          if (/\d{1,2}\/\d{1,2}\/\d{2,4}/.test(cellText)) dateCell = cells[ci];
          if (/\d{4}\s*$/.test(cellText) && cellText.length > 5) acctCell = cells[ci];
          if (cells[ci].querySelector('a')) docCell = cells[ci];
        }

        if (!dateCell || !docCell) continue;

        var d = etradeParseDate(dateCell.textContent.trim());
        if (!d) continue;

        var aLabel = 'etrade0000';
        var aType = 'E*Trade';
        var aLast4 = '0000';
        if (acctCell) {
          var am = acctCell.textContent.match(/(\d{4})\s*$/);
          if (am) {
            aLast4 = am[1];
            aType = acctCell.textContent.replace(/\d{4}\s*$/, '').trim();
            aLabel = makeAccountLabel(aType, aLast4);
          }
        }

        var docLink = docCell.querySelector('a');
        var docUrl = docLink ? docLink.getAttribute('href') : null;
        if (docUrl && docUrl.startsWith('/')) docUrl = 'https://us.etrade.com' + docUrl;

        var fn = makeBrokerageFilename(d, 'etrade', aLabel);
        statements.push({
          date: d, url: docUrl, filename: fn,
          accountLabel: aLabel, accountType: aType, accountLast4: aLast4,
          needsClick: !docUrl
        });
      }
    }

    // Strategy 3: ARIA grid
    if (statements.length === 0) {
      var gridRows = document.querySelectorAll('[role="row"]');
      for (var gr = 0; gr < gridRows.length; gr++) {
        var gridCells = gridRows[gr].querySelectorAll('[role="cell"], [role="gridcell"]');
        if (gridCells.length < 2) continue;

        var gDateText = '';
        var gLink = null;
        for (var gc = 0; gc < gridCells.length; gc++) {
          var gt = gridCells[gc].textContent.trim();
          if (/\d{1,2}\/\d{1,2}\/\d{2,4}/.test(gt)) gDateText = gt;
          var a = gridCells[gc].querySelector('a');
          if (a) gLink = a;
        }

        if (!gDateText || !gLink) continue;
        var gDate = etradeParseDate(gDateText);
        if (!gDate) continue;

        var gUrl = gLink.getAttribute('href');
        if (gUrl && gUrl.startsWith('/')) gUrl = 'https://us.etrade.com' + gUrl;

        statements.push({
          date: gDate, url: gUrl,
          filename: makeBrokerageFilename(gDate, 'etrade', 'etrade0000'),
          accountLabel: 'etrade0000', accountType: 'E*Trade', accountLast4: '0000',
          needsClick: !gUrl
        });
      }
    }

    return statements;
  }

  function etradeParseFromShadow(shadowRoot) {
    var statements = [];
    var rows = shadowRoot.querySelectorAll('tr, [role="row"]');

    for (var i = 0; i < rows.length; i++) {
      var text = rows[i].textContent || '';
      var dateMatch = text.match(/(\d{1,2})\/(\d{1,2})\/(\d{2,4})/);
      if (!dateMatch) continue;

      var link = rows[i].querySelector('a');
      if (!link) continue;

      var date = etradeParseDate(dateMatch[0]);
      if (!date) continue;

      var href = link.getAttribute('href');
      if (href && href.startsWith('/')) href = 'https://us.etrade.com' + href;

      statements.push({
        date: date, url: href,
        filename: makeBrokerageFilename(date, 'etrade', 'etrade0000'),
        accountLabel: 'etrade0000', accountType: 'E*Trade', accountLast4: '0000',
        needsClick: !href
      });
    }

    return statements;
  }

  function etradeParseDate(text) {
    // Parse MM/DD/YYYY → YYYY-MM-DD (full date for E*Trade uniqueness)
    var m = text.match(/(\d{1,2})\/(\d{1,2})\/(\d{2,4})/);
    if (!m) return null;

    var month = parseInt(m[1]);
    var day = parseInt(m[2]);
    var year = m[3].length === 2 ? '20' + m[3] : m[3];

    if (month < 1 || month > 12 || day < 1 || day > 31) return null;

    return year + '-' + String(month).padStart(2, '0') + '-' + String(day).padStart(2, '0');
  }

  async function etradeClickNext() {
    var selectors = [
      '[aria-label="Next page"]',
      '[aria-label="Next"]',
      '[title="Next"]'
    ];

    for (var s = 0; s < selectors.length; s++) {
      var el = document.querySelector(selectors[s]);
      if (el && el.offsetParent !== null && !el.disabled && el.getAttribute('aria-disabled') !== 'true') {
        el.click();
        return true;
      }
    }

    // Fallback: button/a with Next text
    var elements = document.querySelectorAll('button, a');
    for (var i = 0; i < elements.length; i++) {
      var text = elements[i].textContent.trim();
      if (/^>$|^›$|^Next$/.test(text) && elements[i].offsetParent !== null &&
          !elements[i].disabled && elements[i].getAttribute('aria-disabled') !== 'true') {
        elements[i].click();
        return true;
      }
    }

    return false;
  }

  // ===== IBKR =====
  async function scrapeIbkr() {
    console.log('[IBKR] Starting scrape...');
    await wait(3000);

    // Dismiss notification modals
    for (var d = 0; d < 3; d++) {
      var dismissBtns = document.querySelectorAll('button, a, [role="button"]');
      var dismissed = false;
      for (var i = 0; i < dismissBtns.length; i++) {
        if (/^Dismiss$/i.test(dismissBtns[i].textContent.trim())) {
          dismissBtns[i].click();
          dismissed = true;
          await wait(1000);
        }
      }
      if (!dismissed) break;
    }

    // Open Activity Statement popup
    if (!await ibkrOpenModal()) {
      console.log('[IBKR] Could not open Activity Statement modal');
      return [];
    }
    await wait(2000);

    // Select "Monthly" period
    await ibkrSelectMonthlyPeriod();
    await wait(2000);

    // Read available dates
    var dates = ibkrReadDates();
    console.log('[IBKR] Available dates:', dates.length);

    var statements = [];

    for (var di = 0; di < dates.length; di++) {
      var dateInfo = dates[di];
      var date = dateInfo.parsed;
      var accountLabel = 'ibkr0000';
      var filename = makeBrokerageFilename(date, 'ibkr', accountLabel);

      statements.push({
        date: date,
        url: null,
        filename: filename,
        accountLabel: accountLabel,
        accountType: 'IBKR',
        accountLast4: '0000',
        needsClick: true,
        _ibkrDateValue: dateInfo.value,
        _ibkrDateType: dateInfo.type,
        _ibkrDateIndex: dateInfo.index
      });
    }

    // For IBKR, we need special handling: set each date, click Download PDF, re-open modal
    // Store elements for click handling
    for (var si = 0; si < statements.length; si++) {
      statementClickElements.push({
        date: statements[si].date,
        element: null, // Will be found dynamically during click
        text: 'IBKR statement ' + statements[si].date,
        ibkrDateValue: statements[si]._ibkrDateValue,
        ibkrDateType: statements[si]._ibkrDateType,
        ibkrDateIndex: statements[si]._ibkrDateIndex
      });
    }

    console.log('[IBKR] Total statements:', statements.length);
    return statements;
  }

  async function ibkrOpenModal() {
    // Strategy 1: Find "Activity Statement" row → click run button
    var rows = document.querySelectorAll('.form-bordered > div.row, div.row');
    for (var i = 0; i < rows.length; i++) {
      if (/Activity Statement/i.test(rows[i].textContent)) {
        var runBtn = rows[i].querySelector('a[aria-label="Run"], i.fa-circle-arrow-right');
        if (runBtn) {
          if (runBtn.tagName === 'I') runBtn = runBtn.closest('a') || runBtn;
          runBtn.click();
          console.log('[IBKR] Clicked Activity Statement run button');
          await wait(2000);
          return ibkrIsModalOpen();
        }
      }
    }

    // Strategy 2: First run button on page
    var firstRun = document.querySelector('a.btn-icon[aria-label="Run"]');
    if (firstRun) {
      firstRun.click();
      await wait(2000);
      return ibkrIsModalOpen();
    }

    return false;
  }

  function ibkrIsModalOpen() {
    var modal = document.querySelector('#amModal');
    if (modal && modal.offsetParent !== null) return true;

    var modalBody = document.querySelector('#amModalBody');
    if (modalBody && modalBody.textContent.trim().length > 0) return true;

    var showModal = document.querySelector('.modal.show, .modal.in, .modal[style*="display: block"]');
    if (showModal) return true;

    return false;
  }

  async function ibkrSelectMonthlyPeriod() {
    var modal = document.querySelector('#amModalBody') || document;
    var selects = modal.querySelectorAll('select');

    for (var i = 0; i < selects.length; i++) {
      var hasMonthly = false;
      for (var j = 0; j < selects[i].options.length; j++) {
        if (/monthly/i.test(selects[i].options[j].text)) {
          hasMonthly = true;
          selects[i].value = selects[i].options[j].value;
          selects[i].dispatchEvent(new Event('change', { bubbles: true }));
          console.log('[IBKR] Selected Monthly period');
          break;
        }
      }
      if (hasMonthly) break;
    }
  }

  function ibkrReadDates() {
    var dates = [];
    var modal = document.querySelector('#amModalBody') || document;
    var selects = modal.querySelectorAll('select');

    // Find the date select (not the period select)
    for (var i = 0; i < selects.length; i++) {
      var hasPeriod = false;
      for (var j = 0; j < selects[i].options.length; j++) {
        if (/monthly|daily/i.test(selects[i].options[j].text)) {
          hasPeriod = true;
          break;
        }
      }
      if (hasPeriod) continue; // Skip period select

      // This should be the date select
      for (var k = 0; k < selects[i].options.length; k++) {
        var text = selects[i].options[k].text.trim();
        var value = selects[i].options[k].value;
        var parsed = ibkrParseDate(text) || ibkrParseDate(value);
        if (parsed) {
          dates.push({ parsed: parsed, value: value, type: 'select', index: i });
        }
      }
      if (dates.length > 0) return dates;
    }

    // Fallback: date input
    var inputs = modal.querySelectorAll('input[type="date"], input[type="text"]');
    for (var ii = 0; ii < inputs.length; ii++) {
      var val = inputs[ii].value;
      if (/\d{4}-\d{2}-\d{2}/.test(val)) {
        // Generate last 24 months
        var now = new Date();
        for (var m = 0; m < 24; m++) {
          var d = new Date(now.getFullYear(), now.getMonth() - m, 0);
          var dateStr = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0');
          var lastDay = new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate();
          var inputVal = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(lastDay).padStart(2, '0');
          dates.push({ parsed: dateStr, value: inputVal, type: 'input', index: ii });
        }
        return dates;
      }
    }

    return dates;
  }

  function ibkrParseDate(text) {
    if (!text) return null;
    // "January 2025", "Feb 2024"
    var d = parseDate(text);
    if (d) return d;

    // "01/2025"
    var m = text.match(/^(\d{1,2})\/(\d{4})$/);
    if (m) return m[2] + '-' + m[1].padStart(2, '0');

    // "YYYY-MM"
    var ym = text.match(/^(\d{4})-(\d{2})$/);
    if (ym) return ym[1] + '-' + ym[2];

    // "MM/DD/YYYY"
    var mdy = text.match(/(\d{1,2})\/\d{1,2}\/(\d{4})/);
    if (mdy) return mdy[2] + '-' + mdy[1].padStart(2, '0');

    return null;
  }

  // Handler mapping
  var handlers = {
    robinhood: scrapeRobinhood,
    schwab: scrapeSchwab,
    etrade: scrapeEtrade,
    fidelity: scrapeFidelity,
    webull: scrapeWebull,
    m1finance: scrapeM1Finance,
    vanguard: scrapeVanguard,
    ibkr: scrapeIbkr
  };

  // Expose scrape function for programmatic access
  window.scrapeStatements = function() {
    if (currentBrokerage && handlers[currentBrokerage]) {
      return handlers[currentBrokerage]();
    }
    return [];
  };

  // Listen for messages from popup/service worker
  chrome.runtime.onMessage.addListener(function(message, sender, sendResponse) {
    console.log('Content script received:', message.action, 'for', message.brokerage);

    if (message.action === 'download_statements' || message.action === 'scrape_statements') {
      if (message.brokerage === currentBrokerage && handlers[currentBrokerage]) {
        handlers[currentBrokerage]()
          .then(function(statements) {
            sendResponse({ success: true, statements: statements });
          })
          .catch(function(error) {
            sendResponse({ success: false, error: error.message });
          });
        return true;
      }
    }

    // Schwab per-page scraping: discover accounts
    if (message.action === 'schwab_get_accounts') {
      schwabDiscoverAccounts()
        .then(function(accounts) {
          sendResponse({ success: true, accounts: accounts });
        })
        .catch(function(error) {
          sendResponse({ success: false, error: error.message });
        });
      return true;
    }

    // Schwab per-page scraping: prepare an account (select, set filters, search)
    if (message.action === 'schwab_prepare_account') {
      schwabPrepareAccount(message.account)
        .then(function() {
          sendResponse({ success: true });
        })
        .catch(function(error) {
          sendResponse({ success: false, error: error.message });
        });
      return true;
    }

    // Schwab per-page scraping: scrape CURRENT page only
    if (message.action === 'schwab_scrape_page') {
      (async function() {
        try {
          // Small wait for scroll/render
          await wait(1500);
          var stmts = schwabScrapeCurrentPage(message.account);
          sendResponse({ success: true, statements: stmts });
        } catch (error) {
          sendResponse({ success: false, error: error.message });
        }
      })();
      return true;
    }

    // Schwab per-page scraping: navigate to next page
    if (message.action === 'schwab_next_page') {
      schwabGoToNextPage()
        .then(function(result) {
          sendResponse({ success: true, hasNext: result.hasNext });
        })
        .catch(function(error) {
          sendResponse({ success: false, error: error.message });
        });
      return true;
    }

    // Fidelity per-year scraping: get available years
    if (message.action === 'fidelity_get_years') {
      try {
        var years = fidelityGetYearOptions();
        sendResponse({ success: true, years: years });
      } catch (error) {
        sendResponse({ success: false, error: error.message });
      }
      return true;
    }

    // Fidelity per-year scraping: prepare a year (select year, load more)
    if (message.action === 'fidelity_prepare_year') {
      fidelityPrepareYear(message.year)
        .then(function() {
          sendResponse({ success: true });
        })
        .catch(function(error) {
          sendResponse({ success: false, error: error.message });
        });
      return true;
    }

    // Fidelity per-year scraping: scrape CURRENT page only
    if (message.action === 'fidelity_scrape_page') {
      (async function() {
        try {
          // Small wait for scroll/render
          await wait(1500);
          var stmts = fidelityParseTable();
          sendResponse({ success: true, statements: stmts });
        } catch (error) {
          sendResponse({ success: false, error: error.message });
        }
      })();
      return true;
    }

    // Fidelity-specific click handler: click download icon, then click "Download as PDF" in popup
    //
    // HOW FIDELITY PDF DOWNLOAD WORKS:
    // Fidelity's statements page is an Angular app. Each statement row has a download icon
    // button that opens a popup menu with "Download as PDF" and "Download as CSV" options.
    // These are <li role="menuitem"> elements with Angular event bindings (no href links).
    //
    // The challenge: Content script .click() dispatches UNTRUSTED events (isTrusted=false).
    // Angular's zone-patched event handlers on these <li> elements only respond to TRUSTED
    // events (from real user input). So content script clicks find the element but don't
    // trigger the download.
    //
    // The solution: This handler performs Steps 1-2 (click download icon button, find the
    // "Download as PDF" LI element) and returns the element's bounding rect coordinates.
    // The service worker then uses chrome.debugger (CDP Input.dispatchMouseEvent) to
    // dispatch a TRUSTED mouse click at those coordinates — exactly like Playwright does.
    // The trusted click triggers Angular's handler, which opens a new tab with the PDF URL.
    // The service worker detects the new tab, grabs the URL, closes the tab, and downloads
    // the PDF to the correct folder.
    //
    // IMPORTANT: The debugger must be attached BEFORE getting coordinates, because the
    // debugger infobar shifts the page layout and would invalidate earlier coordinates.
    if (message.action === 'fidelity_click_download') {
      (async function() {
        try {
          var targetDate = message.date;
          console.log('[Fidelity] Click download for date:', targetDate);

          // Find the stored download button for this date
          var downloadBtn = null;
          for (var si = 0; si < statementClickElements.length; si++) {
            if (statementClickElements[si].date === targetDate) {
              var stored = statementClickElements[si].element;
              try {
                if (stored && stored.offsetParent !== null) {
                  downloadBtn = stored;
                  break;
                }
              } catch (e) {
                console.log('[Fidelity] Stored element is stale');
              }
            }
          }

          if (!downloadBtn) {
            // Fallback: search for downloadIconButton in the row containing this date
            var rows = document.querySelectorAll('table tbody tr');
            for (var ri = 0; ri < rows.length; ri++) {
              var rowText = (rows[ri].textContent || '').toLowerCase();
              // Check if this row's description matches our date
              var rowDate = fidelityParseDescription(rows[ri].textContent || '');
              if (rowDate === targetDate) {
                downloadBtn = rows[ri].querySelector('button.downloadIconButton') ||
                              rows[ri].querySelector('button[aria-label*="ownload"]');
                if (downloadBtn) {
                  console.log('[Fidelity] Found download button via row search');
                  break;
                }
              }
            }
          }

          if (!downloadBtn) {
            console.log('[Fidelity] No download button found for date:', targetDate);
            sendResponse({ success: false, error: 'Download button not found for date: ' + targetDate });
            return;
          }

          // Step 1: Click the download icon button to open the popup
          downloadBtn.scrollIntoView({ behavior: 'smooth', block: 'center' });
          await wait(500);
          downloadBtn.click();
          console.log('[Fidelity] Clicked download icon button');

          // Step 2: Wait for popup to appear and find "Download as PDF"
          var pdfOption = null;
          for (var attempt = 0; attempt < 10; attempt++) {
            await wait(500);

            // Debug: dump the DOM near the download button to understand popup structure
            if (attempt === 1) {
              // Look for any popup/menu/dropdown that appeared
              var popups = document.querySelectorAll('[role="menu"], [role="listbox"], .popup, .dropdown, .menu, [class*="popup"], [class*="dropdown"], [class*="menu"], [class*="overlay"]');
              console.log('[Fidelity] DEBUG: Found', popups.length, 'popup-like containers');
              for (var pi = 0; pi < popups.length; pi++) {
                var popup = popups[pi];
                if (popup.offsetParent || popup.style.display !== 'none') {
                  console.log('[Fidelity] DEBUG popup', pi, ':', popup.tagName, '| classes:', (popup.className || '').substring(0, 80), '| innerHTML:', popup.innerHTML.substring(0, 300));
                }
              }

              // Also check aria-controls on the button
              var ariaControls = downloadBtn.getAttribute('aria-controls');
              var ariaExpanded = downloadBtn.getAttribute('aria-expanded');
              console.log('[Fidelity] DEBUG: button aria-controls:', ariaControls, '| aria-expanded:', ariaExpanded);
              if (ariaControls) {
                var controlledEl = document.getElementById(ariaControls);
                if (controlledEl) {
                  console.log('[Fidelity] DEBUG: controlled element:', controlledEl.tagName, '| innerHTML:', controlledEl.innerHTML.substring(0, 300));
                }
              }

              // Dump ALL elements containing "Download as" text
              var allEls = document.querySelectorAll('*');
              for (var ae = 0; ae < allEls.length; ae++) {
                var aeEl = allEls[ae];
                // Only check leaf-ish elements (direct text, not from children)
                if (aeEl.childElementCount < 3) {
                  var aeText = (aeEl.textContent || '').trim();
                  if (aeText.toLowerCase().startsWith('download as') && aeText.length < 30) {
                    console.log('[Fidelity] DEBUG: "Download as" element:', aeEl.tagName, '| text:', aeText, '| classes:', (aeEl.className || '').substring(0, 60), '| visible:', !!(aeEl.offsetParent));
                  }
                }
              }
            }

            // Strategy 1: Check aria-controls popup
            var ariaControls = downloadBtn.getAttribute('aria-controls');
            if (ariaControls) {
              var popup = document.getElementById(ariaControls);
              if (popup) {
                var popupLinks = popup.querySelectorAll('a, button, li, [role="menuitem"]');
                for (var pl = 0; pl < popupLinks.length; pl++) {
                  var plText = (popupLinks[pl].textContent || '').trim().toLowerCase();
                  if (plText.includes('download') && plText.includes('pdf')) {
                    pdfOption = popupLinks[pl];
                    console.log('[Fidelity] Found via aria-controls:', popupLinks[pl].textContent.trim(), '| tag:', popupLinks[pl].tagName);
                    break;
                  }
                }
              }
            }
            if (pdfOption) break;

            // Strategy 2: Search broadly for visible "Download as PDF" elements
            var candidates = document.querySelectorAll('a, button, [role="menuitem"], [role="option"], li, span, div');
            for (var ci = 0; ci < candidates.length; ci++) {
              var el = candidates[ci];
              var elText = (el.textContent || '').trim();
              var elTextLower = elText.toLowerCase();

              if (el === downloadBtn) continue;
              if (elText.length > 50 || elText.length === 0) continue;

              // Check visibility
              var isVisible = !!(el.offsetParent || window.getComputedStyle(el).position === 'fixed');
              if (!isVisible) continue;

              if (elTextLower === 'download as pdf' || elTextLower === 'download pdf') {
                pdfOption = el;
                console.log('[Fidelity] Found via broad search:', elText, '| tag:', el.tagName, '| classes:', (el.className || '').substring(0, 60));
                break;
              }
            }
            if (pdfOption) break;
          }

          if (!pdfOption) {
            console.log('[Fidelity] "Download as PDF" option not found after 10 attempts');
            var escEvent = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true });
            document.dispatchEvent(escEvent);
            await wait(300);
            sendResponse({ success: false, error: 'Download as PDF option not found in popup' });
            return;
          }

          // Step 3: Return element coordinates for trusted click via chrome.debugger CDP
          // Content script clicks are UNTRUSTED — Angular's event handlers ignore them.
          // The service worker will use chrome.debugger + CDP Input.dispatchMouseEvent
          // to send a real trusted click at the element's coordinates, exactly like Playwright.
          var rect = pdfOption.getBoundingClientRect();
          var clickX = Math.round(rect.left + rect.width / 2);
          var clickY = Math.round(rect.top + rect.height / 2);
          console.log('[Fidelity] PDF option found, returning coordinates for trusted click:', clickX, clickY);
          sendResponse({ success: true, needsTrustedClick: true, clickX: clickX, clickY: clickY });
        } catch (error) {
          console.log('[Fidelity] Click download error:', error.message);
          sendResponse({ success: false, error: error.message });
        }
      })();
      return true;
    }

    if (message.action === 'ping') {
      sendResponse({ pong: true, brokerage: currentBrokerage });
      return true;
    }

    // Handle cancel check - service worker polls this during long operations
    if (message.action === 'check_cancel') {
      chrome.storage.local.get('cancelRequested', function(result) {
        sendResponse({ cancelled: result.cancelRequested === true });
      });
      return true;
    }

    // Handle detect_account_tabs - find available account tabs on Robinhood
    if (message.action === 'detect_account_tabs' && currentBrokerage === 'robinhood') {
      (async function() {
        // Wait for tab links to render (React app loads async)
        var tabs = [];
        for (var attempt = 0; attempt < 10; attempt++) {
          tabs = detectRobinhoodAccountTabs();
          if (tabs.length > 0) break;
          console.log('[Robinhood] Waiting for account tabs to render, attempt', attempt + 1);
          await wait(1000);
        }
        sendResponse({ tabs: tabs });
      })();
      return true;
    }

    // Handle click_element from service worker - just click the element
    // The service worker handles interceptor injection via chrome.scripting.executeScript
    if (message.action === 'click_element') {
      (async function() {
      console.log('Content script: Received click_element for date:', message.date);
      try {
        var element = null;

        // First try: Look through our stored statement elements
        if (message.date && statementClickElements.length > 0) {
          console.log('Content script: Looking through stored elements, count:', statementClickElements.length);
          for (var si = 0; si < statementClickElements.length; si++) {
            var stored = statementClickElements[si];
            if (stored.date === message.date) {
              // Check if element is still valid (not stale)
              try {
                if (stored.element && stored.element.offsetParent !== null) {
                  element = stored.element;
                  console.log('Content script: Found stored element for', message.date);
                  break;
                }
              } catch (e) {
                console.log('Content script: Stored element is stale');
              }
            }
          }
        }

        // Second try: If not found in storage or stale, search the page for PDF links
        if (!element && message.date) {
          console.log('Content script: Searching page for date:', message.date);

          // Try to find links with PDF and the date
          var allLinks = document.querySelectorAll('a[href*="pdf"], a[href*="document"], a[href*="statement"]');
          for (var ac = 0; ac < allLinks.length; ac++) {
            var el = allLinks[ac];
            var text = el.textContent || '';
            var href = el.href || '';
            if (text.includes(message.date) || href.includes(message.date.replace(/-/g, ''))) {
              element = el;
              console.log('Content script: Found PDF link on page:', href.substring(0, 60));
              break;
            }
          }
        }

        // Third try: Search any clickable with the date
        if (!element && message.date) {
          var allClickable = document.querySelectorAll('a, button, [role="button"]');
          for (var ac = 0; ac < allClickable.length; ac++) {
            var el = allClickable[ac];
            var text = el.textContent || '';
            if (text.includes(message.date) && /pdf/i.test(text)) {
              element = el;
              console.log('Content script: Found PDF button on page');
              break;
            }
          }
        }

        // Fourth try: Look for any link/button with the month/year
        if (!element && message.date) {
          var parts = message.date.split('-');
          if (parts.length === 2) {
            var month = getMonthName(parseInt(parts[1]));
            var year = parts[0];
            var allClickable = document.querySelectorAll('a, button, [role="button"]');
            for (var ac = 0; ac < allClickable.length; ac++) {
              var el = allClickable[ac];
              var text = el.textContent || '';
              if ((text.includes(month) || text.includes(parts[1])) && text.includes(year)) {
                element = el;
                console.log('Content script: Found element by month/year');
                break;
              }
            }
          }
        }

        if (element) {
          element.scrollIntoView({ behavior: 'smooth', block: 'center' });
          await wait(500);
          element.click();
          console.log('Content script: Clicked element for date:', message.date);

          // After clicking, wait briefly to see if a modal/popup appears with "Download PDF" option
          // This is especially common with Fidelity's download process
          await wait(1000);

          // Look for any modal/download options that might have appeared after clicking
          // Include <a> tags since Fidelity uses "Download as PDF" as an <a> link in the popup
          var downloadModalOption = null;
          var modalElements = document.querySelectorAll('.modal button, .modal a, .popup button, .popup a, [role="dialog"] button, [role="dialog"] a, [role="menu"] a, [role="menu"] button, .overlay button, .overlay a, button, a');

          for (var mb = 0; mb < modalElements.length; mb++) {
            var btn = modalElements[mb];
            var btnText = (btn.textContent || btn.title || btn.getAttribute('aria-label') || '').toLowerCase().trim();
            if ((btnText.includes('download') && btnText.includes('pdf')) ||
                btnText === 'download as pdf' ||
                btnText === 'download pdf' ||
                btnText.includes('export as pdf') ||
                btnText.includes('save as pdf')) {
              downloadModalOption = btn;
              break;
            }
          }

          // If not found in the loop above, check other common selectors for download buttons
          if (!downloadModalOption) {
            var ariaLabelElements = document.querySelectorAll('button[aria-label*="download" i], a[aria-label*="download" i], button[title*="download" i], a[title*="download" i]');
            for (var al = 0; al < ariaLabelElements.length; al++) {
              var btn = ariaLabelElements[al];
              var btnText = (btn.textContent || btn.title || btn.getAttribute('aria-label') || '').toLowerCase();
              if (btnText.includes('download') || btnText.includes('pdf')) {
                downloadModalOption = btn;
                break;
              }
            }
          }

          if (downloadModalOption) {
            console.log('Content script: Found download option in modal/popup:', downloadModalOption.textContent.trim());
            downloadModalOption.click();
            // Wait longer for Fidelity — clicking "Download as PDF" opens a new tab
            await wait(2000);
          }

          sendResponse({ success: true });
        } else {
          console.log('Content script: Element not found for date:', message.date);
          sendResponse({ success: false, error: 'Element not found for date: ' + message.date });
        }
      } catch (e) {
        console.log('Content script: Click error:', e.message);
        sendResponse({ success: false, error: e.message });
      }
      })();
      return true;
    }

    // Helper function to get month name
    function getMonthName(monthNum) {
      var months = ['', 'January', 'February', 'March', 'April', 'May', 'June',
                    'July', 'August', 'September', 'October', 'November', 'December'];
      return months[monthNum] || '';
    }

    // Handle download_statements_and_wait - scrape AND handle clicks in one go
    if (message.action === 'download_statements_and_wait') {
      console.log('Content script: download_statements_and_wait for', message.brokerage);

      var handler = handlers[currentBrokerage];
      if (message.brokerage === currentBrokerage && handler) {
        handler()
          .then(async function(statements) {
            var logPrefix = '[' + currentBrokerage + ']';
            console.log(logPrefix, 'Found statements, now processing:', statements.length);

            var results = [];

            for (var s = 0; s < statements.length; s++) {
              var stmt = statements[s];
              console.log(logPrefix, 'Processing statement:', stmt.date, '| needsClick:', stmt.needsClick);

              try {
                if (stmt.needsClick) {
                  console.log(logPrefix, 'Clicking for date:', stmt.date);

                  // Find the element from stored click elements
                  var clickEl = null;
                  for (var se = 0; se < statementClickElements.length; se++) {
                    if (statementClickElements[se].date === stmt.date) {
                      clickEl = statementClickElements[se].element;
                      break;
                    }
                  }

                  if (!clickEl) {
                    // Try finding on page by date text
                    var allC = document.querySelectorAll('a, button');
                    for (var ac = 0; ac < allC.length; ac++) {
                      if (allC[ac].textContent.includes(stmt.date)) {
                        clickEl = allC[ac];
                        break;
                      }
                    }
                  }

                  if (clickEl) {
                    clickEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    await wait(300);
                    clickEl.click();
                    await wait(2000);
                    console.log(logPrefix, 'Clicked for:', stmt.date);
                    results.push({ date: stmt.date, clicked: true });
                  } else {
                    console.log(logPrefix, 'Could not find click element for:', stmt.date);
                    results.push({ date: stmt.date, error: 'Element not found' });
                  }
                } else {
                  // Has direct URL - signal to service worker to download
                  results.push({ date: stmt.date, url: stmt.url, needsClick: false });
                }
              } catch (e) {
                console.log(logPrefix, 'Error processing:', stmt.date, e.message);
                results.push({ date: stmt.date, error: e.message });
              }
            }

            sendResponse({ success: true, results: results, statements: statements });
          })
          .catch(function(error) {
            sendResponse({ success: false, error: error.message });
          });
        return true;
      }
    }
  });

  console.log('Statement Downloader: Ready for', currentBrokerage);
})();

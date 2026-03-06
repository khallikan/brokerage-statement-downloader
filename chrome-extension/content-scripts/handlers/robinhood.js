// Robinhood handler
// Extends base handler with Robinhood-specific logic

class RobinhoodHandler {
  constructor() {
    this.brokerageName = 'robinhood';
    this.baseUrl = 'https://robinhood.com';

    // Account URLs to scrape
    this.accountUrls = [
      { url: 'https://robinhood.com/account/reports-statements/individual', label: 'individual0000', type: 'Individual' },
      { url: 'https://robinhood.com/account/reports-statements/crypto', label: 'crypto0000', type: 'Crypto' },
      { url: 'https://robinhood.com/account/reports-statements/futures-monthly', label: 'futures0000', type: 'Futures' }
    ];
  }

  // Get account info from URL
  getAccountInfo(url) {
    if (url.includes('/crypto')) {
      return { accountLabel: 'crypto0000', accountType: 'Crypto', accountLast4: '0000' };
    } else if (url.includes('/futures')) {
      return { accountLabel: 'futures0000', accountType: 'Futures', accountLast4: '0000' };
    } else if (url.includes('/roth') || url.includes('/ira')) {
      return { accountLabel: 'rothira0000', accountType: 'Roth IRA', accountLast4: '0000' };
    }
    return { accountLabel: 'individual0000', accountType: 'Individual', accountLast4: '0000' };
  }

  // Get URLs to scrape
  getUrlsToScrape() {
    return this.accountUrls;
  }

  // Parse date from text
  parseDate(text) {
    if (!text) return null;
    const textLower = text.toLowerCase().trim();
    const monthMap = {
      january: '01', february: '02', march: '03', april: '04', may: '05', june: '06',
      july: '07', august: '08', september: '09', october: '10', november: '11', december: '12',
      jan: '01', feb: '02', mar: '03', apr: '04', jun: '06', jul: '07', aug: '08',
      sep: '09', oct: '10', nov: '11', dec: '12'
    };
    for (const [month, num] of Object.entries(monthMap)) {
      const match = textLower.match(new RegExp(`${month}\\s+(\\d{4})`));
      if (match) return `${match[1]}-${num}`;
    }
    const m = text.match(/(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})/);
    if (m) return `${m[3]}-${m[1].padStart(2, '0')}`;
    return null;
  }

  // Wait helper
  wait(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  // Main scrape function - runs in content script context
  async scrape(page) {
    const url = page.location.href;
    const accountInfo = this.getAccountInfo(url);

    console.log('[Robinhood] Scraping account:', accountInfo.accountType);
    console.log('[Robinhood] URL:', url);

    // Wait for page to settle
    await this.wait(2000);

    // Expand all statements by clicking View More
    await this.expandAllStatements();

    const statements = [];

    // Strategy 1: Look for links with PDF in href
    let links = document.querySelectorAll('a[href*=".pdf"]');
    console.log('[Robinhood] Found', links.length, 'PDF links');

    for (const link of links) {
      const stmt = this.parseStatementLink(link, accountInfo);
      if (stmt) statements.push(stmt);
    }

    // Strategy 2: Look for statement/document links in main content
    if (statements.length === 0) {
      const mainContent = document.querySelector('main') || document.body;
      const allLinks = mainContent.querySelectorAll('a');

      for (const link of allLinks) {
        const href = link.getAttribute('href');
        if (!href) continue;

        // Skip sidebar navigation links
        if (href.includes('/account/reports-statements/')) continue;

        // Look for actual statement links
        if (href.includes('.pdf') || href.includes('/documents/') || href.includes('/statements/')) {
          const stmt = this.parseStatementLink(link, accountInfo);
          if (stmt) statements.push(stmt);
        }
      }
    }

    // Deduplicate
    const uniqueStatements = [];
    const seenUrls = new Set();
    for (const stmt of statements) {
      if (!seenUrls.has(stmt.url)) {
        seenUrls.add(stmt.url);
        uniqueStatements.push(stmt);
      }
    }

    console.log('[Robinhood] Total statements found:', uniqueStatements.length);
    return uniqueStatements;
  }

  // Parse a statement link into a StatementInfo object
  parseStatementLink(link, accountInfo) {
    const href = link.getAttribute('href');
    if (!href) return null;

    // Get text content from link and surrounding elements
    const text = link.textContent.trim();
    const parent = link.parentElement;
    const parentText = parent ? parent.textContent : '';
    const grandparent = parent ? parent.parentElement : null;
    const grandparentText = grandparent ? grandparent.textContent : '';

    // Try to parse date
    let date = this.parseDate(text) || this.parseDate(parentText) || this.parseDate(grandparentText);

    if (!date) {
      console.log('[Robinhood] Could not parse date from:', text.substring(0, 50));
      return null;
    }

    // Build full URL
    let fullUrl = href;
    if (href.startsWith('/')) {
      fullUrl = this.baseUrl + href;
    }

    return {
      date: date,
      url: fullUrl,
      filename: `${date}_Robinhood_${accountInfo.accountLabel}.pdf`,
      accountLabel: accountInfo.accountLabel,
      accountType: accountInfo.accountType,
      accountLast4: accountInfo.accountLast4
    };
  }

  // Expand all statements by clicking "View More"
  async expandAllStatements() {
    console.log('[Robinhood] Looking for View More button...');

    let clicked = 0;

    while (true) {
      let viewMoreButton = null;

      // Find
      const allButtons = document.querySelectorAll('button, a, span[role="button"], div[role="button"]');
      for (const btn of allButtons) {
        const text = btn.textContent.toLowerCase().trim();
        // Only match "View More" exactly or at start
        if ((text === 'view more' || text.startsWith('view more')) && btn.offsetParent !== null) {
          viewMoreButton = btn;
          break;
        }
      }

      if (!viewMoreButton) {
        console.log('[Robinhood] View More button no longer visible after', clicked, 'clicks');
        break;
      }

      try {
        console.log('[Robinhood] Clicking View More...');
        viewMoreButton.click();
        await this.wait(1500);
        clicked++;
      } catch (e) {
        console.log('[Robinhood] Error clicking View More:', e.message);
        break;
      }
    }

    console.log('[Robinhood] Done clicking View More', clicked, 'times');
  }
}

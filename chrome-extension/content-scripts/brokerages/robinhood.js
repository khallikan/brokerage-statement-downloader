// Robinhood brokerage handler
// Ported from Playwright to Chrome Extension

(function() {
  'use strict';

  const MONTH_MAP = {
    january: '01', february: '02', march: '03', april: '04',
    may: '05', june: '06', july: '07', august: '08',
    september: '09', october: '10', november: '11', december: '12',
    jan: '01', feb: '02', mar: '03', apr: '04',
    jun: '06', jul: '07', aug: '08', sep: '09', oct: '10', nov: '11', dec: '12'
  };

  const VALID_ACCOUNT_TYPES = [
    'individual', 'roth ira', 'traditional ira',
    'crypto', 'futures', 'event contracts'
  ];

  const SKIP_LABELS = ['tax', 'taxes', 'tax documents'];

  class RobinhoodHandler {
    constructor(slug) {
      this.slug = slug;
      this.accounts = [];
    }

    async scrape() {
      console.log('Robinhood handler: Starting scrape');

      // Navigate to statements page
      await this.navigateToStatements();

      // Get accounts
      this.accounts = await this.getAccounts();
      console.log('Robinhood handler: Found accounts:', this.accounts);

      const statements = [];

      // Scrape statements for each account
      for (const account of this.accounts) {
        const accountStatements = await this.getStatementsForAccount(account);
        statements.push(...accountStatements);
      }

      console.log('Robinhood handler: Total statements found:', statements.length);
      return statements;
    }

    async navigateToStatements() {
      // Try direct navigation first
      const currentUrl = window.location.href;
      if (!currentUrl.includes('/account/documents')) {
        window.location.href = 'https://robinhood.com/account/documents';
        await this.waitForPageLoad(3000);
      }

      // If not logged in, wait for user to log in
      if (window.location.href.includes('/login')) {
        console.log('Robinhood handler: Please log in manually');
        await this.waitForLogin();
      }
    }

    async waitForLogin() {
      return new Promise((resolve) => {
        const checkLogin = setInterval(() => {
          if (!window.location.href.includes('/login')) {
            clearInterval(checkLogin);
            this.waitForPageLoad(2000).then(resolve);
          }
        }, 1000);

        // Timeout after 5 minutes
        setTimeout(() => {
          clearInterval(checkLogin);
          resolve();
        }, 300000);
      });
    }

    async waitForPageLoad(ms) {
      return new Promise(resolve => setTimeout(resolve, ms));
    }

    async getAccounts() {
      const accounts = [];
      const seenTypes = new Set();

      // Find Monthly Statements section
      let monthlySection = null;
      try {
        const headings = document.querySelectorAll('h1, h2, h3, h4, h5, h6, section, div');
        for (const heading of headings) {
          if (/Monthly\s+Statements?/i.test(heading.textContent)) {
            monthlySection = heading.closest('section') || heading.closest('div');
            break;
          }
        }
      } catch (e) {
        console.log('Robinhood handler: Could not find monthly statements section');
      }

      // Search for account tabs
      const searchScope = monthlySection || document;
      const accountTabs = searchScope.querySelectorAll('a, button, div[role="button"]');

      for (const tab of accountTabs) {
        const text = tab.textContent.trim();
        const textLower = text.toLowerCase();

        // Skip headers and skip labels
        const tag = tab.tagName.toLowerCase();
        if (['h1', 'h2', 'h3', 'h4', 'h5', 'h6'].includes(tag)) continue;
        if (SKIP_LABELS.some(l => textLower.includes(l))) continue;

        // Check for valid account types
        const matchedType = VALID_ACCOUNT_TYPES.find(type => textLower.includes(type));
        if (!matchedType) continue;
        if (seenTypes.has(matchedType)) continue;

        seenTypes.add(matchedType);

        // Try to find last 4 digits
        let last4 = '0000';
        const last4Match = text.match(/(\d{4})\s*$/);
        if (last4Match) {
          last4 = last4Match[1];
        } else {
          // Try parent element
          const parent = tab.parentElement;
          if (parent) {
            const parentMatch = parent.textContent.match(/[·•*]+\s*(\d{4})/);
            if (parentMatch) last4 = parentMatch[1];
          }
        }

        // Convert account type to label
        let accountType = text.replace(/\d{4}\s*$/, '').trim();
        accountType = accountType.replace(/[·•*]+$/, '').trim();

        accounts.push({
          accountType: accountType,
          accountLast4: last4,
          accountLabel: this.makeAccountLabel(accountType, last4),
          element: tab
        });
      }

      if (accounts.length === 0) {
        // Fallback: assume single individual account
        accounts.push({
          accountType: 'Individual',
          accountLast4: '0000',
          accountLabel: 'individual0000',
          element: null
        });
      }

      return accounts;
    }

    makeAccountLabel(accountType, last4) {
      const base = accountType.toLowerCase().replace(/[^a-z0-9]/g, '');
      return `${base}${last4}`;
    }

    async getStatementsForAccount(account) {
      const statements = [];

      // Click on the account tab if element exists
      if (account.element) {
        account.element.click();
        await this.waitForPageLoad(2000);
      }

      // Expand "View More" if present
      await this.loadAllStatements();

      // Find PDF links
      const pdfLinks = document.querySelectorAll('a[href*=".pdf"], a[href*="pdf"], a[href*="document"], a[href*="statement"]');

      for (const link of pdfLinks) {
        const href = link.href;
        const text = link.textContent.trim();
        const parent = link.closest('div, tr, li');
        const parentText = parent ? parent.textContent : text;

        const date = this.parseDate(text) || this.parseDate(parentText);
        if (!date) continue;

        const filename = `2024-01_Robinhood_${account.accountLabel}.pdf`; // Will be corrected by date

        statements.push({
          date: date,
          url: href,
          filename: `${date}_Robinhood_${account.accountLabel}.pdf`,
          accountLabel: account.accountLabel,
          accountType: account.accountType,
          accountLast4: account.accountLast4
        });
      }

      return statements;
    }

    async loadAllStatements() {
      let clicks = 0;
      const maxClicks = 100;

      while (clicks < maxClicks) {
        // Scroll to bottom
        window.scrollTo(0, document.body.scrollHeight);
        await this.waitForPageLoad(1000);

        // Find View More button
        const viewMoreButtons = document.querySelectorAll('a, button');
        let foundButton = null;

        for (const btn of viewMoreButtons) {
          const text = btn.textContent.trim().toLowerCase();
          if (text === 'view more' || text === 'view more statements') {
            foundButton = btn;
            break;
          }
        }

        if (!foundButton) break;

        foundButton.click();
        clicks++;
        await this.waitForPageLoad(2000);
      }

      console.log(`Robinhood handler:Clicked View More ${clicks} times`);
    }

    parseDate(text) {
      if (!text) return null;

      const textLower = text.toLowerCase().trim();

      for (const [month, num] of Object.entries(MONTH_MAP)) {
        const match = textLower.match(new RegExp(`${month}\\s+(\\d{4})`));
        if (match) {
          return `${match[1]}-${num}`;
        }
      }

      const m = text.match(/(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})/);
      if (m) {
        return `${m[3]}-${m[1].padStart(2, '0')}`;
      }

      const ym = text.match(/(\d{4})-(\d{2})/);
      if (ym) {
        return `${ym[1]}-${ym[2]}`;
      }

      return null;
    }
  }

  // Expose to window
  window.BrokerageHandler = RobinhoodHandler;
})();

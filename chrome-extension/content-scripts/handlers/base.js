// Base brokerage handler
// All handlers should extend this

class BaseHandler {
  constructor() {
    this.brokerageName = 'base';
    this.baseUrl = '';
  }

  // Determine account info from URL or page
  getAccountInfo(url, page) {
    return {
      accountLabel: 'individual0000',
      accountType: 'Individual',
      accountLast4: '0000'
    };
  }

  // Navigate to statements page(s)
  async navigateToStatements(tabId, chrome) {
    throw new Error('Not implemented');
  }

  // Scrape statements from current page
  async scrapeStatements(page) {
    throw new Error('Not implemented');
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
}

// Export for use in other handlers
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { BaseHandler };
}

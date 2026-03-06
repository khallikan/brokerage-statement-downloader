// Schwab brokerage handler
// TODO: Port selectors from Playwright schwab.py

(function() {
  'use strict';

  class SchwabHandler {
    constructor(slug) {
      this.slug = slug;
    }

    async scrape() {
      console.log('Schwab handler: Starting scrape');

      // TODO: Navigate to statements page
      // TODO: Get accounts
      // TODO: Find and parse statement links
      // TODO: Return array of {date, url, filename, accountLabel, accountType, accountLast4}

      return [];
    }

    async navigateToStatements() {
      window.location.href = 'https://client.schwab.com/app/accounts/statements/#/';
      await this.waitForPageLoad(3000);
    }

    async waitForPageLoad(ms) {
      return new Promise(resolve => setTimeout(resolve, ms));
    }
  }

  window.BrokerageHandler = SchwabHandler;
})();

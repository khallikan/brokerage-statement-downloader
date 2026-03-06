// E*Trade brokerage handler
// TODO: Port selectors from Playwright etrade.py

(function() {
  'use strict';

  class EtradeHandler {
    constructor(slug) {
      this.slug = slug;
    }

    async scrape() {
      console.log('E*Trade handler: Starting scrape');
      // TODO: Implement
      return [];
    }
  }

  window.BrokerageHandler = EtradeHandler;
})();

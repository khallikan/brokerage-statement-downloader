// Interactive Brokers brokerage handler
// TODO: Port selectors from Playwright ibkr.py

(function() {
  'use strict';

  class IbkrHandler {
    constructor(slug) {
      this.slug = slug;
    }

    async scrape() {
      console.log('Interactive Brokers handler: Starting scrape');
      // TODO: Implement
      return [];
    }
  }

  window.BrokerageHandler = IbkrHandler;
})();

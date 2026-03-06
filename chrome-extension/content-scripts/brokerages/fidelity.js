// Fidelity brokerage handler
// TODO: Port selectors from Playwright fidelity.py

(function() {
  'use strict';

  class FidelityHandler {
    constructor(slug) {
      this.slug = slug;
    }

    async scrape() {
      console.log('Fidelity handler: Starting scrape');
      // TODO: Implement
      return [];
    }
  }

  window.BrokerageHandler = FidelityHandler;
})();

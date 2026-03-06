// Webull brokerage handler
// TODO: Port selectors from Playwright webull.py

(function() {
  'use strict';

  class WebullHandler {
    constructor(slug) {
      this.slug = slug;
    }

    async scrape() {
      console.log('Webull handler: Starting scrape');
      // TODO: Implement
      return [];
    }
  }

  window.BrokerageHandler = WebullHandler;
})();

// Shared configuration for Chrome Extension

const BROKERAGE_CONFIG = {
  robinhood: {
    slug: 'robinhood',
    displayName: 'Robinhood',
    folderName: 'Robinhood',
    statementsUrl: 'https://robinhood.com/account/documents',
    // Ported selectors from Playwright
    selectors: {
      accountDropdown: "a[href='/account'], [data-testid='AccountIcon'], [aria-label='Account']",
      reportsLink: "text=/Reports and Statements/i",
      monthlyStatements: "text=/Monthly Statements/i",
      accountTabs: "text=/Individual|Roth IRA|Traditional IRA|Crypto|Futures|Event Contracts/i",
      viewMore: "a:has-text('View More'), a:has-text('View more'), button:has-text('View More'), button:has-text('View more')",
      pdfLink: "a[href*='.pdf'], a[href*='pdf'], a[href*='document'], a[href*='statement']"
    }
  },
  schwab: {
    slug: 'schwab',
    displayName: 'Charles Schwab',
    folderName: 'Schwab',
    statementsUrl: 'https://client.schwab.com/app/accounts/statements/#/',
    selectors: {}
  },
  etrade: {
    slug: 'etrade',
    displayName: 'E*Trade',
    folderName: 'ETrade',
    statementsUrl: 'https://us.etrade.com/etx/pxy/accountdocs',
    selectors: {}
  },
  fidelity: {
    slug: 'fidelity',
    displayName: 'Fidelity',
    folderName: 'Fidelity',
    statementsUrl: 'https://digital.fidelity.com/ftgw/digital/portfolio/documents',
    selectors: {}
  },
  webull: {
    slug: 'webull',
    displayName: 'Webull',
    folderName: 'Webull',
    statementsUrl: 'https://www.webull.com/center/tax',
    selectors: {}
  },
  m1finance: {
    slug: 'm1finance',
    displayName: 'M1 Finance',
    folderName: 'M1Finance',
    statementsUrl: 'https://dashboard.m1.com/d/settings/documents/statements',
    selectors: {}
  },
  vanguard: {
    slug: 'vanguard',
    displayName: 'Vanguard',
    folderName: 'Vanguard',
    statementsUrl: 'https://statements.web.vanguard.com/',
    selectors: {}
  },
  ibkr: {
    slug: 'ibkr',
    displayName: 'Interactive Brokers',
    folderName: 'InteractiveBrokers',
    statementsUrl: 'https://portal.interactivebrokers.com/AccountManagement/AmAuthentication?action=Statements',
    selectors: {}
  }
};

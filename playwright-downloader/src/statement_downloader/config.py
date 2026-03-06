from pathlib import Path
from dataclasses import dataclass


STATEMENTS_DIR = Path.home() / "Downloads" / "Statements"
DOWNLOAD_LOG_PATH = STATEMENTS_DIR / "download_log.json"
BROWSER_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "browser_data"

# Delay between individual statement downloads (seconds)
DOWNLOAD_DELAY = 2.0


@dataclass(frozen=True)
class BrokerageConfig:
    slug: str
    display_name: str
    folder_name: str
    login_url: str
    statements_url: str


BROKERAGES: dict[str, BrokerageConfig] = {
    "schwab": BrokerageConfig(
        slug="schwab",
        display_name="Charles Schwab",
        folder_name="Schwab",
        login_url="https://www.schwab.com/client-home",
        statements_url="https://client.schwab.com/app/accounts/statements/#/",
    ),
    "fidelity": BrokerageConfig(
        slug="fidelity",
        display_name="Fidelity",
        folder_name="Fidelity",
        login_url="https://digital.fidelity.com/prgw/digital/login/full-page",
        statements_url="https://digital.fidelity.com/ftgw/digital/portfolio/documents",
    ),
    "robinhood": BrokerageConfig(
        slug="robinhood",
        display_name="Robinhood",
        folder_name="Robinhood",
        login_url="https://robinhood.com/login",
        statements_url="https://robinhood.com/account/documents",
    ),
    "etrade": BrokerageConfig(
        slug="etrade",
        display_name="E*Trade",
        folder_name="ETrade",
        login_url="https://us.etrade.com/etx/pxy/login",
        statements_url="https://us.etrade.com/etx/pxy/accountdocs?inav=nav:documents#/documents",
    ),
    "vanguard": BrokerageConfig(
        slug="vanguard",
        display_name="Vanguard",
        folder_name="Vanguard",
        login_url="https://logon.vanguard.com/logon",
        statements_url="https://statements.web.vanguard.com/",
    ),
    "webull": BrokerageConfig(
        slug="webull",
        display_name="Webull",
        folder_name="Webull",
        login_url="https://www.webull.com/center",
        statements_url="https://www.webull.com/center/tax",
    ),
    "m1finance": BrokerageConfig(
        slug="m1finance",
        display_name="M1 Finance",
        folder_name="M1Finance",
        login_url="https://dashboard.m1.com/login",
        statements_url="https://dashboard.m1.com/d/settings/documents/statements",
    ),
    "ibkr": BrokerageConfig(
        slug="ibkr",
        display_name="Interactive Brokers",
        folder_name="InteractiveBrokers",
        login_url="https://www.interactivebrokers.com/sso/Login",
        statements_url="https://portal.interactivebrokers.com/AccountManagement/AmAuthentication?action=Statements",
    ),
}

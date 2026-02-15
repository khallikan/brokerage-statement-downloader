import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from playwright.async_api import Page

from .config import BrokerageConfig, STATEMENTS_DIR, DOWNLOAD_DELAY
from .tracker import DownloadTracker


@dataclass
class AccountInfo:
    """An account discovered on a brokerage site."""
    account_type: str       # e.g. "Roth IRA", "Individual", "Brokerage"
    account_last4: str      # last 4 digits of account number
    label: str              # slug form: e.g. "roth7734", "individual2291"


@dataclass
class StatementInfo:
    """A single available statement found on a statements page."""
    date: str               # YYYY-MM format
    element: object         # Playwright ElementHandle or locator for the download link
    account: AccountInfo    # which account this statement belongs to


class BaseBrokerage(ABC):
    """Abstract base class for all brokerage modules.

    Each brokerage subclass implements four methods:
      - _get_accounts()
      - _navigate_to_statements(account)
      - _get_available_statements(account)
      - _download_statement(stmt)

    The ``run()`` method orchestrates the full workflow.
    """

    def __init__(
        self,
        page: Page,
        tracker: DownloadTracker,
        config: BrokerageConfig,
    ):
        self.page = page
        self.tracker = tracker
        self.config = config
        self.output_dir = STATEMENTS_DIR / config.folder_name

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self) -> int:
        """Run the full download workflow. Returns count of new statements."""
        # Navigate to login page (may redirect to dashboard if already logged in)
        await self.page.goto(self.config.login_url, wait_until="domcontentloaded")
        await self.page.wait_for_timeout(3000)

        # Check if already logged in (persistent cookies from previous session)
        if not await self._is_logged_in():
            await self._wait_for_login()
            if not await self._is_logged_in():
                print(f"  Could not confirm login for {self.config.display_name}. Skipping.")
                return 0

        # Discover all accounts
        accounts = await self._get_accounts()
        if not accounts:
            print(f"  No accounts found for {self.config.display_name}.")
            return 0

        print(f"  Found {len(accounts)} account(s): {', '.join(a.label for a in accounts)}")

        total_downloaded = 0

        for account in accounts:
            count = await self._process_account(account)
            total_downloaded += count

        return total_downloaded

    # ------------------------------------------------------------------
    # Per-account processing
    # ------------------------------------------------------------------

    async def _process_account(self, account: AccountInfo) -> int:
        """Download all new statements for a single account."""
        await self._navigate_to_statements(account)

        available = await self._get_available_statements(account)
        if not available:
            print(f"    {account.label}: no statements found on page")
            return 0

        # Filter out already-downloaded
        downloaded_dates = self.tracker.get_downloaded_dates(
            self.config.slug, account.label
        )
        needed = [s for s in available if s.date not in downloaded_dates]

        # Sort oldest to newest
        needed.sort(key=lambda s: s.date)

        if not needed:
            print(f"    {account.label}: all {len(available)} statements already downloaded")
            return 0

        print(f"    {account.label}: downloading {len(needed)} new statement(s) "
              f"(of {len(available)} available)")

        count = 0
        for stmt in needed:
            file_path = await self._download_and_save(stmt)
            if file_path:
                count += 1
                # Delay between downloads to avoid rate limiting
                if stmt is not needed[-1]:
                    await asyncio.sleep(DOWNLOAD_DELAY)

        return count

    # ------------------------------------------------------------------
    # Download + save + record
    # ------------------------------------------------------------------

    async def _download_and_save(self, stmt: StatementInfo) -> Path | None:
        """Download a single statement, save with correct name, record in tracker."""
        filename = f"{stmt.date}_{self.config.folder_name}_{stmt.account.label}.pdf"
        target = self.output_dir / filename
        self.output_dir.mkdir(parents=True, exist_ok=True)

        try:
            saved_path = await self._download_statement(stmt, target)
        except Exception as e:
            print(f"      ERROR downloading {filename}: {e}")
            return None

        if saved_path and saved_path.exists() and saved_path.stat().st_size > 0:
            self.tracker.record_download(
                brokerage_slug=self.config.slug,
                display_name=self.config.display_name,
                folder_name=self.config.folder_name,
                account_label=stmt.account.label,
                account_type=stmt.account.account_type,
                account_last4=stmt.account.account_last4,
                statement_date=stmt.date,
                filename=filename,
                file_path=saved_path,
            )
            print(f"      Downloaded: {filename}")
            return saved_path
        else:
            print(f"      FAILED: {filename} (empty or missing)")
            return None

    # ------------------------------------------------------------------
    # Login helpers
    # ------------------------------------------------------------------

    async def _wait_for_login(self) -> None:
        """Prompt the user to log in manually in the browser window."""
        print(f"\n{'=' * 60}")
        print(f"  Please log in to {self.config.display_name}")
        print(f"  Complete any 2FA prompts in the browser window.")
        print(f"  Press ENTER here when you are fully logged in.")
        print(f"{'=' * 60}\n")
        await asyncio.get_event_loop().run_in_executor(None, input)

    async def _is_logged_in(self) -> bool:
        """Check if the user appears to be logged in.

        Subclasses can override this with a brokerage-specific check
        (e.g., looking for an account name element). Default returns False
        so login prompt is always shown.
        """
        return False

    # ------------------------------------------------------------------
    # Abstract methods — each brokerage must implement these
    # ------------------------------------------------------------------

    @abstractmethod
    async def _get_accounts(self) -> list[AccountInfo]:
        """Detect all accounts on this brokerage after login.

        Returns a list of AccountInfo with type, last 4 digits, and label.
        """
        ...

    @abstractmethod
    async def _navigate_to_statements(self, account: AccountInfo) -> None:
        """Navigate to the statements/documents page for a specific account."""
        ...

    @abstractmethod
    async def _get_available_statements(self, account: AccountInfo) -> list[StatementInfo]:
        """Parse the current page and return all available statements."""
        ...

    @abstractmethod
    async def _download_statement(self, stmt: StatementInfo, target: Path) -> Path | None:
        """Download a single statement PDF and save it to ``target``.

        Should use Playwright's download handling (page.expect_download).
        Returns the path where the file was saved, or None on failure.
        """
        ...

    # ------------------------------------------------------------------
    # Utility helpers available to subclasses
    # ------------------------------------------------------------------

    @staticmethod
    def make_account_label(account_type: str, last4: str) -> str:
        """Generate a slug-style account label like 'roth7734'."""
        slug = account_type.lower().replace(" ", "").replace("-", "")
        # Shorten common types
        slug = slug.replace("rothira", "roth")
        slug = slug.replace("traditionalira", "trad")
        slug = slug.replace("individual", "individual")
        slug = slug.replace("brokerage", "brokerage")
        return f"{slug}{last4}"

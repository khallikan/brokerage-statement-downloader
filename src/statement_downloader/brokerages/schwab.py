"""Charles Schwab brokerage module.

Navigates Schwab's client portal to discover accounts and download
monthly statements. Serves as the reference template for other modules.

Schwab statements URL: https://client.schwab.com/app/accounts/statements/
"""

import re
from pathlib import Path

from playwright.async_api import Page

from ..base_brokerage import BaseBrokerage, AccountInfo, StatementInfo


# Date text on Schwab statement rows is typically "January 2024", "February 2024", etc.
MONTH_MAP = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
}


def _parse_statement_date(text: str) -> str | None:
    """Parse a date string like 'January 2024' into 'YYYY-MM'."""
    text = text.strip().lower()
    for month_name, month_num in MONTH_MAP.items():
        if month_name in text:
            year_match = re.search(r"(\d{4})", text)
            if year_match:
                return f"{year_match.group(1)}-{month_num}"
    # Try MM/DD/YYYY or MM-DD-YYYY patterns
    m = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", text)
    if m:
        return f"{m.group(3)}-{m.group(1).zfill(2)}"
    return None


class SchwabBrokerage(BaseBrokerage):
    """Charles Schwab statement downloader.

    NOTE: The CSS selectors below are best-effort starting points based on
    Schwab's client portal as of early 2026. They will likely need adjustment
    when first tested against the live site — inspect the DOM and update the
    selectors accordingly.
    """

    async def _is_logged_in(self) -> bool:
        try:
            # Schwab shows the user's name when logged in
            logged_in = await self.page.locator(
                "#accountsLanding, .sdps-account-selector, [data-testid='account-selector']"
            ).first.is_visible(timeout=3000)
            return logged_in
        except Exception:
            return False

    async def _get_accounts(self) -> list[AccountInfo]:
        """Detect accounts from Schwab's account selector/dashboard."""
        accounts = []

        # Navigate to accounts overview to find all accounts
        await self.page.goto(
            "https://client.schwab.com/app/accounts/positions/",
            wait_until="domcontentloaded",
        )
        await self.page.wait_for_timeout(3000)

        # Schwab typically shows accounts in a list/dropdown.
        # Look for account rows that contain the account type and masked number.
        # Common patterns: "Individual ...1234", "Roth IRA ...5678"
        account_elements = await self.page.locator(
            ".account-selector-item, "
            "[data-testid='account-row'], "
            ".sdps-account-row, "
            "li[class*='account']"
        ).all()

        if not account_elements:
            # Fallback: try to extract from the page text
            page_text = await self.page.inner_text("body")
            # Look for patterns like "Roth IRA ****1234" or "Individual Brokerage ****5678"
            pattern = r"((?:Roth IRA|Traditional IRA|Individual|Brokerage|Rollover IRA|SEP IRA|401k)[^*]*)\*+(\d{4})"
            matches = re.findall(pattern, page_text, re.IGNORECASE)
            for acct_type, last4 in matches:
                acct_type = acct_type.strip().rstrip(" -·•")
                label = self.make_account_label(acct_type, last4)
                if not any(a.label == label for a in accounts):
                    accounts.append(AccountInfo(
                        account_type=acct_type,
                        account_last4=last4,
                        label=label,
                    ))
        else:
            for el in account_elements:
                text = await el.inner_text()
                # Extract account type and last 4 digits
                match = re.search(
                    r"((?:Roth IRA|Traditional IRA|Individual|Brokerage|Rollover IRA|SEP IRA|401k)[^*]*)\*+(\d{4})",
                    text,
                    re.IGNORECASE,
                )
                if match:
                    acct_type = match.group(1).strip().rstrip(" -·•")
                    last4 = match.group(2)
                    label = self.make_account_label(acct_type, last4)
                    if not any(a.label == label for a in accounts):
                        accounts.append(AccountInfo(
                            account_type=acct_type,
                            account_last4=last4,
                            label=label,
                        ))

        if not accounts:
            print("    WARNING: Could not auto-detect Schwab accounts.")
            print("    Falling back to a generic account. Check selectors.")
            accounts.append(AccountInfo(
                account_type="Unknown",
                account_last4="0000",
                label="unknown0000",
            ))

        return accounts

    async def _navigate_to_statements(self, account: AccountInfo) -> None:
        """Navigate to the statements page and select the given account."""
        await self.page.goto(
            self.config.statements_url,
            wait_until="domcontentloaded",
        )
        await self.page.wait_for_timeout(3000)

        # If there's an account selector dropdown, pick the right account
        try:
            selector = self.page.locator(
                ".account-selector, "
                "[data-testid='account-selector'], "
                "select[name*='account']"
            ).first
            if await selector.is_visible(timeout=3000):
                await selector.click()
                await self.page.wait_for_timeout(500)
                # Click the option matching our account's last 4
                option = self.page.locator(
                    f"text=/{account.account_last4}/"
                ).first
                if await option.is_visible(timeout=2000):
                    await option.click()
                    await self.page.wait_for_timeout(2000)
        except Exception:
            pass  # Single-account users may not have a selector

        # Try to expand the date range to show all available statements
        try:
            date_range = self.page.locator(
                "[data-testid='date-range'], "
                "select[name*='date'], "
                "select[name*='period'], "
                ".date-range-selector"
            ).first
            if await date_range.is_visible(timeout=2000):
                await date_range.select_option(label="All")  # or value="all"
                await self.page.wait_for_timeout(2000)
        except Exception:
            pass

    async def _get_available_statements(self, account: AccountInfo) -> list[StatementInfo]:
        """Parse statement rows from the Schwab statements page."""
        statements = []

        # Look for statement rows/links on the page
        rows = await self.page.locator(
            ".statement-row, "
            "[data-testid='statement-row'], "
            "tr[class*='statement'], "
            ".document-row, "
            "a[href*='statement'], "
            "a[href*='.pdf']"
        ).all()

        if not rows:
            # Broader fallback: look for any clickable elements with dates
            rows = await self.page.locator(
                "table tbody tr, .list-item, [role='row']"
            ).all()

        for row in rows:
            text = await row.inner_text()
            date = _parse_statement_date(text)
            if date:
                # Find the download link within this row
                link = row.locator("a[href*='.pdf'], a[href*='download'], button[class*='download']").first
                if not await link.count():
                    link = row  # The row itself might be clickable
                statements.append(StatementInfo(
                    date=date,
                    element=link,
                    account=account,
                ))

        return statements

    async def _download_statement(self, stmt: StatementInfo, target: Path) -> Path | None:
        """Click the download link and save the PDF."""
        try:
            async with self.page.expect_download(timeout=30000) as download_info:
                await stmt.element.click()
            download = await download_info.value
            await download.save_as(str(target))
            return target
        except Exception:
            # Some sites open PDFs in a new tab instead of triggering a download.
            # Try to get the PDF URL and download directly.
            try:
                href = await stmt.element.get_attribute("href")
                if href:
                    response = await self.page.request.get(href)
                    content = await response.body()
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)
                    return target
            except Exception as e:
                print(f"        Fallback download also failed: {e}")
            return None

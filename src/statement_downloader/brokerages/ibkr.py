"""Interactive Brokers brokerage module.

IBKR Client Portal: https://www.interactivebrokers.com/portal/
Statements are under Performance & Reports > Statements.

IBKR's portal is a complex Java-based web app with non-standard DOM.
This module provides best-effort selectors that will need verification.
"""

import re
from pathlib import Path

from ..base_brokerage import BaseBrokerage, AccountInfo, StatementInfo


MONTH_MAP = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "jun": "06", "jul": "07", "aug": "08",
    "sep": "09", "oct": "10", "nov": "11", "dec": "12",
}


def _parse_date(text: str) -> str | None:
    text = text.strip().lower()
    for name, num in MONTH_MAP.items():
        if name in text:
            year_match = re.search(r"(\d{4})", text)
            if year_match:
                return f"{year_match.group(1)}-{num}"
    m = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", text)
    if m:
        return f"{m.group(3)}-{m.group(1).zfill(2)}"
    return None


class IBKRBrokerage(BaseBrokerage):

    async def _is_logged_in(self) -> bool:
        try:
            return await self.page.locator(
                "#MainContent, .home-page, [data-testid='portfolio']"
            ).first.is_visible(timeout=3000)
        except Exception:
            return False

    async def _get_accounts(self) -> list[AccountInfo]:
        accounts = []

        # IBKR portal shows accounts on the main page after login
        await self.page.goto(
            "https://www.interactivebrokers.com/portal/",
            wait_until="domcontentloaded",
        )
        await self.page.wait_for_timeout(4000)

        page_text = await self.page.inner_text("body")

        # IBKR shows accounts like "U1234567" or "Individual ****1234"
        # Look for IBKR-style account IDs (U or DU followed by digits)
        ibkr_pattern = r"([DU]+\d{5,8})"
        ibkr_matches = re.findall(ibkr_pattern, page_text)

        for acct_id in ibkr_matches:
            last4 = acct_id[-4:]
            label = self.make_account_label("ibkr", last4)
            if not any(a.label == label for a in accounts):
                accounts.append(AccountInfo(
                    account_type="IBKR",
                    account_last4=last4,
                    label=label,
                ))

        # Also try standard pattern
        pattern = r"((?:Individual|Roth IRA|Traditional IRA|Margin|Securities)[^*·•]*?)[\s]*[*·•]+(\d{4})"
        matches = re.findall(pattern, page_text, re.IGNORECASE)

        for acct_type, last4 in matches:
            acct_type = acct_type.strip().rstrip(" -·•–")
            label = self.make_account_label(acct_type, last4)
            if not any(a.label == label for a in accounts):
                accounts.append(AccountInfo(
                    account_type=acct_type,
                    account_last4=last4,
                    label=label,
                ))

        if not accounts:
            accounts.append(AccountInfo("Unknown", "0000", "unknown0000"))

        return accounts

    async def _navigate_to_statements(self, account: AccountInfo) -> None:
        # IBKR portal: navigate to Performance & Reports > Statements
        await self.page.goto(
            self.config.statements_url,
            wait_until="domcontentloaded",
        )
        await self.page.wait_for_timeout(3000)

        # Try clicking through the menu: Performance & Reports > Statements
        try:
            reports_menu = self.page.locator(
                "text=/Performance|Reports/i, "
                "[data-testid*='reports'], "
                "a[href*='report']"
            ).first
            if await reports_menu.is_visible(timeout=3000):
                await reports_menu.click()
                await self.page.wait_for_timeout(1000)

            statements_link = self.page.locator(
                "text=/[Ss]tatements/i, "
                "a[href*='statement']"
            ).first
            if await statements_link.is_visible(timeout=3000):
                await statements_link.click()
                await self.page.wait_for_timeout(3000)
        except Exception:
            pass

        # Select account
        try:
            selector = self.page.locator(
                "select[name*='account'], .account-selector"
            ).first
            if await selector.is_visible(timeout=2000):
                await selector.click()
                option = self.page.locator(f"text=/{account.account_last4}/").first
                if await option.is_visible(timeout=2000):
                    await option.click()
                    await self.page.wait_for_timeout(2000)
        except Exception:
            pass

    async def _get_available_statements(self, account: AccountInfo) -> list[StatementInfo]:
        statements = []

        rows = await self.page.locator(
            "a[href*='statement'], "
            "a[href*='.pdf'], "
            "[data-testid*='statement'], "
            "tr[class*='statement'], "
            ".statement-row"
        ).all()

        if not rows:
            rows = await self.page.locator("table tbody tr, .list-item").all()

        for row in rows:
            text = await row.inner_text()
            date = _parse_date(text)
            if date:
                link = row.locator("a[href*='.pdf'], a[href*='download'], button").first
                if not await link.count():
                    link = row
                statements.append(StatementInfo(date=date, element=link, account=account))

        return statements

    async def _download_statement(self, stmt: StatementInfo, target: Path) -> Path | None:
        try:
            async with self.page.expect_download(timeout=30000) as download_info:
                await stmt.element.click()
            download = await download_info.value
            await download.save_as(str(target))
            return target
        except Exception as e:
            print(f"        Download via click failed: {e}")
            try:
                href = await stmt.element.get_attribute("href")
                if href:
                    print(f"        Trying direct fetch: href={href[:120]}")
                    response = await self.page.request.get(href)
                    content = await response.body()
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)
                    return target
                else:
                    print(f"        No href attribute on element, cannot fallback")
            except Exception as e2:
                print(f"        Fallback download also failed: {e2}")
            return None

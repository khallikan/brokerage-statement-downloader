"""M1 Finance brokerage module.

M1 Finance documents: https://dashboard.m1.com/d/settings/documents/statements
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


class M1FinanceBrokerage(BaseBrokerage):

    async def _is_logged_in(self) -> bool:
        try:
            return await self.page.locator(
                "[data-testid='user-menu'], .user-profile, nav[aria-label='Main']"
            ).first.is_visible(timeout=3000)
        except Exception:
            return False

    async def _get_accounts(self) -> list[AccountInfo]:
        accounts = []

        await self.page.goto(
            "https://dashboard.m1.com/d/invest",
            wait_until="domcontentloaded",
        )
        await self.page.wait_for_timeout(3000)

        page_text = await self.page.inner_text("body")

        # M1 shows accounts like "Individual Invest" or "Roth IRA"
        pattern = r"((?:Individual|Roth IRA|Traditional IRA|SEP IRA|Invest|Crypto)[^*·•]*?)[\s]*[*·•]+(\d{4})"
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
            # M1 might show accounts without masked numbers on the main page
            # Try the settings/documents page
            accounts.append(AccountInfo("Individual", "0000", "individual0000"))

        return accounts

    async def _navigate_to_statements(self, account: AccountInfo) -> None:
        await self.page.goto(
            self.config.statements_url,
            wait_until="domcontentloaded",
        )
        await self.page.wait_for_timeout(3000)

        # Select account if there's a picker
        try:
            selector = self.page.locator(
                "select[name*='account'], [data-testid*='account-select']"
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
            "[data-testid*='statement'], "
            "[data-testid*='document'], "
            "a[href*='statement'], "
            "a[href*='.pdf'], "
            ".document-row"
        ).all()

        if not rows:
            rows = await self.page.locator(
                "table tbody tr, .list-item, [role='listitem']"
            ).all()

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
        except Exception:
            try:
                href = await stmt.element.get_attribute("href")
                if href:
                    response = await self.page.request.get(href)
                    content = await response.body()
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)
                    return target
            except Exception as e:
                print(f"        Fallback download failed: {e}")
            return None

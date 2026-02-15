"""Vanguard brokerage module.

Vanguard has a dedicated statements area within their account documents section.
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


class VanguardBrokerage(BaseBrokerage):

    async def _is_logged_in(self) -> bool:
        try:
            return await self.page.locator(
                "[data-testid='global-header-name'], "
                ".vg-header-account, "
                "#my-accounts"
            ).first.is_visible(timeout=3000)
        except Exception:
            return False

    async def _get_accounts(self) -> list[AccountInfo]:
        accounts = []

        await self.page.goto(
            "https://personal.vanguard.com/us/MyHome",
            wait_until="domcontentloaded",
        )
        await self.page.wait_for_timeout(4000)

        page_text = await self.page.inner_text("body")

        # Vanguard shows accounts like "Roth IRA - 1234" or
        # "Individual Brokerage Account ****5678"
        pattern = r"((?:Roth IRA|Traditional IRA|Individual|Brokerage|Rollover IRA|SEP IRA|401\(k\)|529)[^*·•\d]*?)[\s]*[*·•]*[\s]*[-–]?[\s]*(\d{4})"
        matches = re.findall(pattern, page_text, re.IGNORECASE)

        seen = set()
        for acct_type, last4 in matches:
            acct_type = acct_type.strip().rstrip(" -·•–")
            if not acct_type or len(acct_type) > 40:
                continue
            label = self.make_account_label(acct_type, last4)
            if label not in seen:
                seen.add(label)
                accounts.append(AccountInfo(
                    account_type=acct_type,
                    account_last4=last4,
                    label=label,
                ))

        if not accounts:
            accounts.append(AccountInfo("Unknown", "0000", "unknown0000"))

        return accounts

    async def _navigate_to_statements(self, account: AccountInfo) -> None:
        await self.page.goto(
            self.config.statements_url,
            wait_until="domcontentloaded",
        )
        await self.page.wait_for_timeout(3000)

        # Select account
        try:
            selector = self.page.locator(
                "select[id*='account'], "
                "[data-testid*='account-select'], "
                ".account-selector"
            ).first
            if await selector.is_visible(timeout=2000):
                # Try to find and click the matching account
                options = await selector.locator("option").all()
                for opt in options:
                    text = await opt.inner_text()
                    if account.account_last4 in text:
                        value = await opt.get_attribute("value")
                        if value:
                            await selector.select_option(value=value)
                        else:
                            await opt.click()
                        await self.page.wait_for_timeout(2000)
                        break
        except Exception:
            pass

    async def _get_available_statements(self, account: AccountInfo) -> list[StatementInfo]:
        statements = []

        rows = await self.page.locator(
            ".statement-row, "
            "a[href*='statement'], "
            "a[href*='.pdf'], "
            "[data-testid*='statement'], "
            "tr[class*='document']"
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

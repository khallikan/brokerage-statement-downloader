"""Fidelity brokerage module.

Fidelity shows all accounts' documents together (no per-account filtering).
Account info is parsed from each document row. A year-selector dropdown
is used to iterate through each year, and a "Load more results" button
must be clicked repeatedly to reveal all statements for a given year.

Fidelity documents URL: https://digital.fidelity.com/ftgw/digital/portfolio/documents

TODO: Consider using page.evaluate() to dump page HTML and analyze element
structure (like the Webull module does) for more robust element detection.
"""

import asyncio
import hashlib
import re
from pathlib import Path

from ..base_brokerage import BaseBrokerage, AccountInfo, StatementInfo
from ..config import DOWNLOAD_DELAY


MONTH_MAP = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "jun": "06", "jul": "07", "aug": "08",
    "sep": "09", "oct": "10", "nov": "11", "dec": "12",
}


def _parse_fidelity_description(text: str) -> str | None:
    """Parse a Fidelity description into a date string for filenames.

    Returns:
        "YYYY-MM" for single-month statements (e.g. "Jan 2024 -- Statement (pdf)")
        "YYYY-MM-MM" for multi-month ranges (e.g. "July-Sep 2021")
        None if the description should be skipped (year-end reports, non-statements)
    """
    text_clean = text.strip()
    text_lower = text_clean.lower()

    # Skip year-end / annual reports
    if any(skip in text_lower for skip in ["year end", "year-end", "annual report"]):
        return None

    # Must contain "statement" to be a statement
    if "statement" not in text_lower:
        return None

    # Extract the year
    year_match = re.search(r"(\d{4})", text_clean)
    if not year_match:
        return None
    year = year_match.group(1)

    # Check for month range pattern: "Month1-Month2 YYYY"
    # e.g. "July-Sep 2021", "Oct-Dec 2021"
    range_match = re.search(
        r"([A-Za-z]+)\s*[-–]\s*([A-Za-z]+)\s+\d{4}",
        text_clean,
    )
    if range_match:
        m1 = range_match.group(1).lower()
        m2 = range_match.group(2).lower()
        mm1 = MONTH_MAP.get(m1)
        mm2 = MONTH_MAP.get(m2)
        if mm1 and mm2:
            return f"{year}-{mm1}-{mm2}"

    # Single month: find the first month name in the text
    for name, num in MONTH_MAP.items():
        if name in text_lower:
            return f"{year}-{num}"

    return None


def _parse_account_from_text(text: str) -> tuple[str, str] | None:
    """Extract account type and last 4 digits from a Fidelity account column.

    Fidelity account column formats:
        "ROTH IRA 123456789"                      → ("ROTH IRA", "6789")
        "Traditional IRA 123456789"                → ("Traditional IRA", "6789")
        "Health Savings Account 123456789"         → ("Health Savings Account", "6789")
        "NAME 401(K) Savings Plan 123456789"       → ("NAME 401(K) Savings Plan", "6789")
        "BrokerageLink 123456789"                  → ("BrokerageLink", "6789")

    Returns (account_type, last4), or None if parsing fails.
    """
    text = re.sub(r"\s+", " ", text).strip()

    # Primary pattern: account name followed by a bare account number (6+ digits)
    # e.g. "ROTH IRA 123456789", "BrokerageLink 987654321"
    m = re.search(r"^(.+?)\s+(\d{6,})\b", text)
    if m:
        acct_type = m.group(1).strip().rstrip(" -·•–")
        last4 = m.group(2)[-4:]
        if acct_type:
            return acct_type, last4

    # Fallback: masked like ****1234
    m = re.search(r"^(.+?)\s*\*+(\d{4})", text)
    if m:
        acct_type = m.group(1).strip().rstrip(" -·•–")
        last4 = m.group(2)
        if acct_type:
            return acct_type, last4

    return None


class FidelityBrokerage(BaseBrokerage):
    """Fidelity statement downloader.

    Fidelity shows all accounts' documents on one page. We iterate through
    each year using the date-selector dropdown, click "Load more results"
    until exhausted, parse account info from each row, and download statements.
    """

    async def _is_logged_in(self) -> bool:
        """Check if logged in by verifying we're not on the login page."""
        url = self.page.url.lower()
        if "/login" in url:
            return False
        if "digital.fidelity.com" in url and "/login" not in url:
            # Double-check with a UI element
            try:
                visible = await self.page.locator(
                    "[data-testid='customer-name'], .customer-name, .pntlt-tab"
                ).first.is_visible(timeout=3000)
                if visible:
                    print("  ✓ Logged in successfully")
                    return True
            except Exception:
                pass
            # URL changed away from login — trust it
            print("  ✓ Logged in (URL check)")
            return True
        return False

    async def _get_accounts(self) -> list[AccountInfo]:
        """Return a single placeholder account.

        Fidelity doesn't allow per-account filtering on the documents page.
        Real account info is extracted from each document row during processing.
        """
        return [AccountInfo(
            account_type="All",
            account_last4="0000",
            label="all",
        )]

    async def _navigate_to_statements(self, account: AccountInfo) -> None:
        """Navigate to the Fidelity documents page.

        This is called by _process_account but we handle year selection there,
        so this just loads the page.
        """
        await self.page.goto(
            self.config.statements_url,
            wait_until="domcontentloaded",
        )
        await self.page.wait_for_timeout(3000)
        try:
            await self.page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass

    async def _get_available_statements(self, account: AccountInfo) -> list[StatementInfo]:
        """Required by base class but not used — _process_account handles everything."""
        return []

    async def _process_account(self, account: AccountInfo) -> int:
        """Override base class to handle Fidelity's year-by-year processing.

        Flow:
        1. Navigate to documents page
        2. Open the year-selector dropdown
        3. Collect all available year options (oldest first)
        4. For each year: select it, load all results, parse rows, download new ones
        """
        await self._navigate_to_statements(account)

        # Collect year options from the dropdown
        years = await self._get_year_options()
        if not years:
            print("    No year options found in dropdown. Trying current page as-is.")
            # Fall back to parsing whatever is on the page
            return await self._process_current_page()

        print(f"    Found {len(years)} year option(s): {', '.join(years)}")

        total_downloaded = 0
        for year in years:
            print(f"    Processing year: {year}")
            selected = await self._select_year(year)
            if not selected:
                print(f"      Could not select year {year}, skipping")
                continue

            # Click "Load more results" until exhausted
            await self._load_all_results()

            # Parse and download statements on this page
            count = await self._process_current_page()
            total_downloaded += count

        return total_downloaded

    async def _get_year_options(self) -> list[str]:
        """Open the year-selector dropdown and collect all year options.

        Returns year strings sorted oldest-first, plus relative options
        like "Last 6 months" and "Last 3 months" at the end.
        """
        years = []
        relative_options = []

        try:
            # Click the dropdown button to open it
            dropdown = self.page.locator("#select-button").first
            if not await dropdown.is_visible(timeout=5000):
                # Try alternative selectors
                dropdown = self.page.locator(
                    "#select-component button, "
                    "[id*='select'] button, "
                    "button[aria-haspopup='listbox']"
                ).first
                if not await dropdown.is_visible(timeout=3000):
                    print("      WARNING: Year selector dropdown not found")
                    return []

            await dropdown.click()
            await self.page.wait_for_timeout(1000)

            # Find all options in the dropdown
            options = await self.page.locator(
                "#select-component li, "
                "[role='option'], "
                "[role='listbox'] li, "
                "ul[id*='select'] li"
            ).all()

            for opt in options:
                try:
                    text = (await opt.inner_text()).strip()
                    text_clean = re.sub(r"\s+", " ", text).strip()
                    if not text_clean:
                        continue

                    # Check if it's a year (4-digit number)
                    if re.fullmatch(r"\d{4}", text_clean):
                        years.append(text_clean)
                    elif "month" in text_clean.lower():
                        relative_options.append(text_clean)
                except Exception:
                    continue

            # Close the dropdown
            await dropdown.click()
            await self.page.wait_for_timeout(500)

        except Exception as e:
            print(f"      WARNING: Could not read year options: {e}")
            return []

        # Sort years oldest-first, then append relative options
        years.sort()
        return years + relative_options

    async def _select_year(self, year: str) -> bool:
        """Select a specific year (or relative option) from the dropdown."""
        try:
            dropdown = self.page.locator("#select-button").first
            if not await dropdown.is_visible(timeout=3000):
                dropdown = self.page.locator(
                    "#select-component button, "
                    "[id*='select'] button, "
                    "button[aria-haspopup='listbox']"
                ).first

            await dropdown.click()
            await self.page.wait_for_timeout(1000)

            # Find and click the matching option
            options = await self.page.locator(
                "#select-component li, "
                "[role='option'], "
                "[role='listbox'] li, "
                "ul[id*='select'] li"
            ).all()

            for opt in options:
                text = (await opt.inner_text()).strip()
                text_clean = re.sub(r"\s+", " ", text).strip()
                if text_clean == year:
                    await opt.click()
                    await self.page.wait_for_timeout(2000)
                    print(f"      ✓ Selected: {year}")
                    return True

            # If exact match failed, close dropdown
            await dropdown.click()
            await self.page.wait_for_timeout(500)
            return False

        except Exception as e:
            print(f"      ERROR selecting year {year}: {e}")
            return False

    async def _load_all_results(self) -> None:
        """Click "Load more results" repeatedly until it disappears."""
        clicks = 0
        while True:
            try:
                load_more = self.page.locator(
                    "a:has-text('Load more results'), "
                    "button:has-text('Load more results'), "
                    "a:has-text('Load more'), "
                    "button:has-text('Load more')"
                ).first

                if await load_more.is_visible(timeout=2000):
                    await load_more.scroll_into_view_if_needed()
                    await load_more.click()
                    clicks += 1
                    await self.page.wait_for_timeout(2000)
                else:
                    break
            except Exception:
                break

        if clicks:
            print(f"      Clicked 'Load more results' {clicks} time(s)")

    async def _process_current_page(self) -> int:
        """Parse all statement rows on the current page and download new ones.

        Returns the count of newly downloaded statements.
        """
        statements = await self._parse_statement_rows()
        if not statements:
            print(f"      No statements found on current page")
            return 0

        # Group by account label for logging
        by_account: dict[str, list[StatementInfo]] = {}
        for stmt in statements:
            by_account.setdefault(stmt.account.label, []).append(stmt)

        # Get known hashes for duplicate detection
        known_hashes = self.tracker.get_all_hashes(self.config.slug)
        total_downloaded = 0

        for acct_label, stmts in by_account.items():
            downloaded_dates = self.tracker.get_downloaded_dates(
                self.config.slug, acct_label
            )
            needed = [s for s in stmts if s.date not in downloaded_dates]
            needed.sort(key=lambda s: s.date)

            if not needed:
                print(f"      {acct_label}: all {len(stmts)} statements already downloaded")
                continue

            print(f"      {acct_label}: downloading {len(needed)} new statement(s) "
                  f"(of {len(stmts)} available)")

            for stmt in needed:
                file_path = await self._download_and_save(stmt)
                if file_path and file_path.exists():
                    # Duplicate detection via SHA-256
                    h = hashlib.sha256()
                    with open(file_path, "rb") as f:
                        for chunk in iter(lambda: f.read(8192), b""):
                            h.update(chunk)
                    file_hash = h.hexdigest()

                    if file_hash in known_hashes:
                        original = known_hashes[file_hash]
                        print(f"        DUPLICATE: {file_path.name} identical to {original}, deleting")
                        file_path.unlink()
                    else:
                        known_hashes[file_hash] = file_path.name
                        total_downloaded += 1

                    if stmt is not needed[-1]:
                        await asyncio.sleep(DOWNLOAD_DELAY)

        return total_downloaded

    async def _parse_statement_rows(self) -> list[StatementInfo]:
        """Parse visible document rows on the page.

        Each <tr> has multiple <td> cells. We identify the Description cell
        (contains date/statement info) and the Account cell (contains account
        type + number) separately, rather than parsing the full row text.
        """
        statements = []

        # Try table rows first (Fidelity uses <table>)
        rows = await self.page.locator("table tbody tr").all()

        if not rows:
            # Broader fallback selectors
            rows = await self.page.locator(
                ".statement-row, "
                "[data-testid*='statement'], "
                ".document-list-item, "
                ".list-item"
            ).all()

        if not rows:
            return statements

        for row in rows:
            try:
                cells = await row.locator("td").all()
                if not cells:
                    # Not a table row — fall back to full text
                    full_text = re.sub(r"\s+", " ", (await row.inner_text())).strip()
                    date = _parse_fidelity_description(full_text)
                    if not date:
                        continue
                    parsed = _parse_account_from_text(full_text)
                    if not parsed:
                        print(f"      ERROR: Could not parse account from row: '{full_text[:120]}'")
                        print(f"      Stopping — please report this so the parser can be updated.")
                        return []
                    acct_type, last4 = parsed
                    label = self.make_account_label(acct_type, last4)
                    acct = AccountInfo(account_type=acct_type, account_last4=last4, label=label)
                    link = row.locator("a[href*='.pdf'], a[href*='download'], a:has-text('PDF'), button:has-text('PDF')").first
                    if not await link.count():
                        link = row
                    statements.append(StatementInfo(date=date, element=link, account=acct))
                    continue

                # Extract text from each cell
                cell_texts = []
                for c in cells:
                    t = re.sub(r"\s+", " ", (await c.inner_text())).strip()
                    cell_texts.append(t)

                # Identify which cell is the description and which is the account
                description_text = None
                account_text = None

                for ct in cell_texts:
                    ct_lower = ct.lower()
                    # Description cell: contains "statement"
                    if "statement" in ct_lower and description_text is None:
                        description_text = ct
                    # Account cell: contains a 6+ digit account number
                    elif re.search(r"\d{6,}", ct) and account_text is None:
                        account_text = ct

                if not description_text:
                    continue

                date = _parse_fidelity_description(description_text)
                if not date:
                    continue

                if not account_text:
                    # If we couldn't identify a separate account cell, try full row
                    full_text = " ".join(cell_texts)
                    print(f"      WARNING: No account cell found. Row cells: {cell_texts}")
                    continue

                parsed = _parse_account_from_text(account_text)
                if not parsed:
                    print(f"      ERROR: Could not parse account from cell: '{account_text[:120]}'")
                    print(f"      Stopping — please report this so the parser can be updated.")
                    return []
                acct_type, last4 = parsed
                label = self.make_account_label(acct_type, last4)
                acct = AccountInfo(account_type=acct_type, account_last4=last4, label=label)

                # Find the download button (button.downloadIconButton in the last cell).
                # This button has aria-haspopup="true" and opens a popup menu
                # with the actual download link.
                link = row.locator("button.downloadIconButton").first
                if not await link.count():
                    # Fallback: any button with aria-label containing "download"
                    link = row.locator("button[aria-label*='ownload']").first
                if not await link.count():
                    link = row

                statements.append(StatementInfo(date=date, element=link, account=acct))

            except Exception as e:
                print(f"      WARNING: Could not parse row: {e}")
                continue

        return statements

    async def _parse_pdf_link_context(self, pdf_link) -> StatementInfo | None:
        """Parse statement info from a PDF link's surrounding context."""
        try:
            row_text = await pdf_link.evaluate(
                "el => el.closest('div[class*=\"row\"], li, tr, "
                "[role=\"row\"], div[class*=\"statement\"], "
                "div[class*=\"document\"]')?.textContent || "
                "el.parentElement?.parentElement?.textContent || ''"
            )
            row_text = re.sub(r"\s+", " ", row_text).strip()

            date = _parse_fidelity_description(row_text)
            if not date:
                return None

            parsed = _parse_account_from_text(row_text)
            if not parsed:
                print(f"      ERROR: Could not parse account from PDF link context: '{row_text[:120]}'")
                print(f"      Stopping — please report this so the parser can be updated.")
                raise ValueError("Unparseable account")
            acct_type, last4 = parsed
            label = self.make_account_label(acct_type, last4)
            acct = AccountInfo(account_type=acct_type, account_last4=last4, label=label)

            return StatementInfo(date=date, element=pdf_link, account=acct)
        except ValueError:
            raise
        except Exception:
            return None

    async def _download_statement(self, stmt: StatementInfo, target: Path) -> Path | None:
        """Click the download icon button, then click the download link in the popup.

        Fidelity's download button (button.downloadIconButton) has
        aria-haspopup="true" — clicking it opens a popup menu with
        the actual PDF download link. We need to:
        1. Click the download icon button to open the popup
        2. Find and click the PDF download link inside the popup
        3. Catch the triggered download
        """
        try:
            # Step 1: Click the download icon button to open the popup
            await stmt.element.click()
            await self.page.wait_for_timeout(1500)

            # Step 2: Click "Download as PDF" — this opens a new tab with the PDF.
            # We catch the new tab (popup), grab its URL, and save the content.
            popup_link = self.page.locator("text=Download as PDF").first

            if await popup_link.is_visible(timeout=2000):
                # Catch the new tab that opens
                async with self.page.context.expect_page(timeout=30000) as new_page_info:
                    await popup_link.click()
                new_page = await new_page_info.value

                # Wait for the PDF page to load
                try:
                    await new_page.wait_for_load_state("load", timeout=15000)
                except Exception:
                    pass

                # Get the PDF URL and download the content
                pdf_url = new_page.url
                response = await self.page.request.get(pdf_url)
                content = await response.body()
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)

                # Close the new tab
                await new_page.close()
                return target

            # No popup found
            print(f"        No download popup found after clicking button")
            await self.page.keyboard.press("Escape")
            await self.page.wait_for_timeout(500)
            return None

        except Exception as e:
            print(f"        Download failed: {e}")
            try:
                await self.page.keyboard.press("Escape")
            except Exception:
                pass
            return None

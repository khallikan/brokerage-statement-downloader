"""Robinhood brokerage module.

Robinhood is a React SPA. The navigation flow to reach statements:
  1. Click "Account" in the nav (expands a dropdown)
  2. Click "Reports and Statements" in the dropdown
  3. Under "Monthly Statements", click each account name (e.g., "Individual")
  4. PDF links appear for each month
  5. Click "View More" repeatedly to load all available statements
  6. Download each PDF

TODO: Consider using page.evaluate() to dump page HTML and analyze element
structure (like the Webull module does) for more robust element detection.
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
    """Parse date text like 'January 2024' or '01/31/2024' into 'YYYY-MM'."""
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


class RobinhoodBrokerage(BaseBrokerage):
    """Robinhood statement downloader.

    Robinhood may offer Individual, Roth IRA, and Traditional IRA accounts.
    Each account's statements are accessed through a separate tab under
    the "Reports and Statements" section.
    """

    async def _wait_for_login(self) -> None:
        """Wait for user to log in by watching for URL change.

        After successful login, Robinhood redirects away from /login
        (usually to https://robinhood.com/?classic=1 or the main dashboard).
        """
        print(f"\n{'=' * 60}")
        print(f"  Please log in to {self.config.display_name}")
        print(f"  Complete any 2FA prompts in the browser window.")
        print(f"  Script will auto-continue when login is detected...")
        print(f"{'=' * 60}\n")

        try:
            # Wait for URL to change away from /login (up to 5 minutes)
            await self.page.wait_for_url(
                lambda url: "/login" not in url.lower(),
                timeout=300000,  # 5 minutes
            )
            print("  ✓ Login detected! Continuing...")
            await self.page.wait_for_timeout(2000)
        except Exception:
            # Fallback: check if we're on robinhood.com but not /login
            url = self.page.url.lower()
            if "robinhood.com" in url and "/login" not in url:
                print("  ✓ Login detected! Continuing...")
                await self.page.wait_for_timeout(2000)
            else:
                print("  WARNING: Could not detect successful login")

    async def _is_logged_in(self) -> bool:
        try:
            # Wait for any redirect to complete
            await self.page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass

        try:
            # If Robinhood redirected away from /login, we're likely logged in
            url = self.page.url.lower()
            if "/login" not in url and "robinhood.com" in url:
                print("  ✓ Already logged in (redirected away from login page)")
                return True

            # Also check for logged-in UI elements even if still on login URL
            logged_in = await self.page.locator(
                "[data-testid='AccountIcon'], "
                "[aria-label='Account'], "
                "a[href='/account'], "
                "text=/Investing/i, "
                "text=/Portfolio/i"
            ).first.is_visible(timeout=5000)
            if logged_in:
                print("  ✓ Already logged in (found account elements)")
                return True

            return False
        except Exception:
            return False

    async def _navigate_to_reports_page(self) -> None:
        """Navigate: Account dropdown → Reports and Statements."""
        # Step 1: Click "Account" in the nav to open the dropdown
        account_link = self.page.locator(
            "a[href='/account'], "
            "[data-testid='AccountIcon'], "
            "[aria-label='Account']"
        ).first
        await account_link.click()
        await self.page.wait_for_timeout(1500)

        # Step 2: Click "Reports and Statements" in the expanded dropdown
        reports_link = self.page.locator("text=/Reports and Statements/i").first
        await reports_link.click()
        await self.page.wait_for_timeout(3000)

    async def _get_accounts(self) -> list[AccountInfo]:
        """Detect accounts from the Reports and Statements page.

        Robinhood shows account names (e.g., "Individual", "Roth IRA")
        under the Monthly Statements section as clickable tabs/links.
        """
        accounts = []

        # Navigate to the Reports and Statements page
        await self._navigate_to_reports_page()

        # Valid account tabs under Monthly Statements.
        # "Tax" is NOT an account — it's a separate document category.
        VALID_ACCOUNT_TYPES = {
            "individual", "roth ira", "traditional ira",
            "crypto", "futures", "event contracts",
        }
        SKIP_LABELS = {"tax", "taxes", "tax documents"}

        # First, find the "Monthly Statements" section to scope our search.
        # This avoids clicking navbar links (e.g., "Crypto" in the top nav).
        monthly_section = None
        try:
            # Look for a heading or label containing "Monthly Statements"
            monthly_heading = self.page.locator(
                "text=/Monthly Statements/i"
            ).first
            if await monthly_heading.is_visible(timeout=3000):
                # Get the parent container that holds the account tabs
                monthly_section = self.page.locator(
                    ":has(> :text-is('Monthly Statements')), "
                    ":has(> :text-is('Monthly statements')), "
                    "section:has-text('Monthly Statements')"
                ).first
        except Exception:
            pass

        # Search for account tabs — scoped to the Monthly Statements section if found
        search_scope = monthly_section if monthly_section and await monthly_section.is_visible(timeout=1000) else self.page
        print(f"      DEBUG: Searching for account tabs {'within Monthly Statements section' if search_scope != self.page else 'on full page'}")

        account_tabs = await search_scope.locator(
            "text=/Individual|Roth IRA|Traditional IRA|Crypto|Futures|Event Contracts/i"
        ).all()

        # DEBUG: log what we found
        print(f"      DEBUG: Found {len(account_tabs)} potential account tabs")
        for tab in account_tabs:
            t = (await tab.inner_text()).strip()
            print(f"        tab text: '{t}'")

        seen_types = set()
        for tab in account_tabs:
            text = (await tab.inner_text()).strip()

            # Skip headers
            tag = await tab.evaluate("el => el.tagName.toLowerCase()")
            if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
                continue

            # Skip the "Tax" button — it's not an account
            if text.strip().lower() in SKIP_LABELS:
                continue

            acct_type = text.strip()
            if acct_type.lower() in seen_types:
                continue
            # Only include recognized account types
            if not any(valid in acct_type.lower() for valid in VALID_ACCOUNT_TYPES):
                continue
            seen_types.add(acct_type.lower())

            # Store the actual tab element so we can click it later
            # Try to find account number (last 4 digits) nearby
            last4_match = re.search(r"(\d{4})\s*$", acct_type)
            if last4_match:
                last4 = last4_match.group(1)
                acct_type = acct_type[:last4_match.start()].strip().rstrip("·•*- ")
            else:
                parent_text = await tab.evaluate(
                    "el => el.parentElement ? el.parentElement.textContent : ''"
                )
                num_match = re.search(r"[·•*]+\s*(\d{4})", parent_text)
                last4 = num_match.group(1) if num_match else "0000"

            label = self.make_account_label(acct_type, last4)
            accounts.append(AccountInfo(
                account_type=acct_type,
                account_last4=last4,
                label=label,
            ))

        if not accounts:
            # Fallback: assume a single Individual account
            print("    WARNING: Could not detect Robinhood accounts. Using default.")
            accounts.append(AccountInfo("Individual", "0000", "individual0000"))

        return accounts

    async def _navigate_to_statements(self, account: AccountInfo) -> None:
        """Click the account tab under Monthly Statements to show its PDFs."""
        # Make sure we're on the Reports and Statements page
        current_url = self.page.url
        if "statements" not in current_url.lower() and "reports" not in current_url.lower():
            await self._navigate_to_reports_page()

        # Scope the click to the Monthly Statements section to avoid
        # clicking navbar links (e.g., "Crypto" in the top nav)
        search_scope = self.page
        try:
            monthly_section = self.page.locator(
                "section:has-text('Monthly Statements'), "
                ":has(> :text-is('Monthly Statements')), "
                ":has(> :text-is('Monthly statements'))"
            ).first
            if await monthly_section.is_visible(timeout=2000):
                search_scope = monthly_section
        except Exception:
            pass

        # Click on the account name tab (e.g., "Individual", "Roth IRA")
        account_tab = search_scope.locator(
            f"text=/{re.escape(account.account_type)}/i"
        ).first
        try:
            if await account_tab.is_visible(timeout=3000):
                await account_tab.scroll_into_view_if_needed()
                await self.page.wait_for_timeout(500)
                await account_tab.click()
                await self.page.wait_for_timeout(2000)
                print(f"      Clicked '{account.account_type}' tab in Monthly Statements")
            else:
                print(f"    WARNING: Could not find '{account.account_type}' tab")
        except Exception:
            print(f"    WARNING: Could not click account tab '{account.account_type}'")

        # Click "View More" repeatedly until all statements are loaded
        await self._load_all_statements()

    async def _load_all_statements(self) -> None:
        """Scroll down and click 'View More' repeatedly until all statements are loaded."""
        max_clicks = 100  # Safety limit
        clicks = 0

        while clicks < max_clicks:
            # Scroll to the bottom of the page so the "View More" button becomes visible
            await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await self.page.wait_for_timeout(1000)

            # DEBUG: log all links near the bottom of the page
            if clicks == 0:
                bottom_links = await self.page.locator("a").all()
                print(f"      DEBUG: {len(bottom_links)} total <a> tags on page. Last 15:")
                for link in bottom_links[-15:]:
                    link_text = (await link.inner_text()).strip()
                    link_href = await link.get_attribute("href") or ""
                    if link_text:
                        print(f"        text='{link_text[:60]}' href='{link_href[:60]}'")

            try:
                # Try multiple strategies to find the "View More" link
                view_more = self.page.locator(
                    "a:text-is('View More'), "
                    "a:text-is('View more'), "
                    "a:has-text('View More'), "
                    "a:has-text('View more'), "
                    "button:has-text('View More'), "
                    "button:has-text('View more'), "
                    "[role='button']:has-text('View More'), "
                    "[role='button']:has-text('View more')"
                ).first

                if await view_more.is_visible(timeout=3000):
                    await view_more.scroll_into_view_if_needed()
                    await self.page.wait_for_timeout(500)
                    await view_more.click()
                    clicks += 1
                    print(f"      Clicked 'View More' ({clicks})...")
                    await self.page.wait_for_timeout(2000)
                else:
                    break
            except Exception:
                break

        if clicks > 0:
            print(f"      Finished expanding statements ({clicks} total 'View More' clicks)")
        else:
            print("      No 'View More' button found (all statements may already be visible)")

    async def _get_available_statements(self, account: AccountInfo) -> list[StatementInfo]:
        """Parse PDF links from the statements list."""
        statements = []

        # DEBUG: dump what we see on the page to help identify selectors
        print("      DEBUG: Scanning page for statement links...")

        # Strategy 1: Look for direct PDF links
        pdf_links = await self.page.locator(
            "a[href*='.pdf'], "
            "a[href*='pdf'], "
            "a[href*='document'], "
            "a[href*='statement']"
        ).all()
        print(f"      DEBUG: Found {len(pdf_links)} PDF/document links")

        for link in pdf_links:
            text = await link.inner_text()
            href = await link.get_attribute("href") or ""
            parent_text = await link.evaluate(
                "el => el.closest('div, tr, li') ? el.closest('div, tr, li').textContent : el.textContent"
            )
            date = _parse_date(text) or _parse_date(parent_text)
            if date:
                statements.append(StatementInfo(
                    date=date, element=link, account=account
                ))
            else:
                print(f"      DEBUG:   link text='{text[:60]}' href='{href[:80]}' (no date parsed)")

        # Strategy 2: Look for any clickable elements with date text
        if not statements:
            print("      DEBUG: No PDF links matched. Trying broader search...")

            # Look for all links and buttons on the page
            all_links = await self.page.locator("a, button").all()
            print(f"      DEBUG: Found {len(all_links)} total links/buttons on page")

            # Log the first 30 to help identify patterns
            for i, el in enumerate(all_links[:30]):
                text = (await el.inner_text()).strip()
                href = await el.get_attribute("href") or ""
                if text and len(text) < 80:
                    print(f"      DEBUG:   [{i}] text='{text}' href='{href[:60]}'")

            # Try to find rows/items that contain dates
            rows = await self.page.locator(
                "[data-testid*='statement'], "
                "[data-testid*='document'], "
                "div[role='button'], "
                "div[role='listitem'], "
                "div[role='link'], "
                "li a, section a"
            ).all()
            print(f"      DEBUG: Found {len(rows)} potential statement rows")

            for row in rows:
                text = await row.inner_text()
                date = _parse_date(text)
                if date:
                    statements.append(StatementInfo(
                        date=date, element=row, account=account
                    ))

        print(f"      DEBUG: Total statements found: {len(statements)}")
        return statements

    async def _download_statement(self, stmt: StatementInfo, target: Path) -> Path | None:
        """Download a statement PDF.

        Robinhood may either trigger a download or open the PDF in a new tab.
        We handle both cases.
        """
        # Try direct download first
        try:
            async with self.page.expect_download(timeout=15000) as download_info:
                await stmt.element.click()
            download = await download_info.value
            await download.save_as(str(target))
            return target
        except Exception as e:
            print(f"        Direct download failed: {e}")

        # Fallback: Robinhood may open PDF in a new tab
        try:
            async with self.page.context.expect_page(timeout=10000) as new_page_info:
                await stmt.element.click()
            new_page = await new_page_info.value
            await new_page.wait_for_load_state("domcontentloaded")
            url = new_page.url

            if ".pdf" in url or "document" in url or "statement" in url:
                response = await self.page.request.get(url)
                content = await response.body()
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                await new_page.close()
                return target
            print(f"        New tab opened but URL not a PDF: {url[:120]}")
            await new_page.close()
        except Exception as e:
            print(f"        New tab strategy failed: {e}")

        # Last resort: try to get href and download directly
        try:
            href = await stmt.element.get_attribute("href")
            if href:
                if not href.startswith("http"):
                    href = f"https://robinhood.com{href}"
                print(f"        Trying direct fetch: href={href[:120]}")
                response = await self.page.request.get(href)
                content = await response.body()
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                return target
            else:
                print(f"        No href attribute on element, cannot fallback")
        except Exception as e:
            print(f"        Direct fetch also failed: {e}")

        print(f"        All download strategies exhausted for {target.name}")
        return None

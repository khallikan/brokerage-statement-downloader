"""Webull brokerage module.

Webull Tax Center: https://www.webull.com/center/tax

Webull login redirects through passport.webull.com. Documents are on
/center/tax, accounts are selected via a dropdown, and statements are
downloaded from a month-grid calendar navigated year-by-year with arrow
buttons.
"""

import asyncio
import hashlib
import re
from pathlib import Path

from ..base_brokerage import BaseBrokerage, AccountInfo, StatementInfo
from ..config import DOWNLOAD_DELAY


# Full month names as shown in Webull's calendar grid
MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


class WebullBrokerage(BaseBrokerage):
    """Webull statement downloader using the /center/tax calendar grid."""

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._account_statement_tab_clicked = False
        self._failed_downloads: list[str] = []

    async def _is_logged_in(self) -> bool:
        url = self.page.url.lower()
        if "passport.webull.com" in url:
            return False
        if "www.webull.com/center" in url:
            print("  ✓ Logged in successfully")
            return True
        return False

    async def _wait_for_login(self) -> None:
        print(f"\n{'=' * 60}")
        print(f"  Please log in to {self.config.display_name}")
        print(f"  The browser will redirect to passport.webull.com.")
        print(f"  Complete login and any 2FA prompts.")
        print(f"  Script will auto-continue when redirected back...")
        print(f"{'=' * 60}\n")

        # Wait for redirect back to /center after login
        try:
            await self.page.wait_for_url(
                "**/center**",
                timeout=300000,  # 5 minutes
            )
            print("  ✓ Login detected! Continuing...")
            await self.page.wait_for_timeout(2000)
        except Exception:
            url = self.page.url.lower()
            if "www.webull.com/center" in url:
                print("  ✓ Login detected! Continuing...")
            else:
                print("  WARNING: Could not detect login redirect. Proceeding anyway...")


    async def _wait_for_passcode(self) -> bool:
        """Webull shows a 'Please enter your trading password' popup on /center/tax.

        Detect the popup, click its password input to give it focus, and wait
        for the user to complete it before proceeding.

        Returns True if no popup or popup was dismissed, False if it timed out.
        """
        # Wait for the page to fully settle — the popup may appear after a delay
        try:
            await self.page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        await self.page.wait_for_timeout(2000)

        # Detect the popup by its specific text
        popup = self.page.get_by_text("Please enter your trading password", exact=False).first
        try:
            if not await popup.is_visible(timeout=5000):
                return True
        except Exception:
            return True

        print("  Trading password popup detected.")

        # Try to focus the password input inside the popup
        try:
            await self.page.evaluate("""() => {
                const inputs = document.querySelectorAll('input[type="password"], input[type="text"]');
                for (const inp of inputs) {
                    const rect = inp.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        inp.focus();
                        inp.click();
                        return;
                    }
                }
            }""")
        except Exception:
            pass

        print("  Please enter your trading password...")

        # Wait for the popup to disappear (user submitted password)
        for _ in range(90):  # up to 3 minutes
            await self.page.wait_for_timeout(2000)
            try:
                if not await popup.is_visible(timeout=500):
                    print("  ✓ Trading password accepted!")
                    await self.page.wait_for_timeout(2000)
                    return True
            except Exception:
                print("  ✓ Trading password accepted!")
                await self.page.wait_for_timeout(2000)
                return True

        print("  ERROR: Trading password popup was not dismissed within 3 minutes. Stopping.")
        return False

    # ------------------------------------------------------------------
    # Accounts
    # ------------------------------------------------------------------

    async def _goto_tax_page(self) -> None:
        """Navigate to /center/tax and defocus the URL bar."""
        await self.page.goto(
            self.config.statements_url,
            wait_until="domcontentloaded",
        )
        # Click the page body to move focus away from the URL bar
        await self.page.evaluate("document.body.click()")
        await self.page.wait_for_timeout(500)

    async def _get_accounts(self) -> list[AccountInfo]:
        # Zoom out to 75% so the full page fits without scrolling
        await self.page.evaluate("document.body.style.zoom = '0.75'")

        await self._goto_tax_page()

        # Wait for passcode popup — Webull shows this every time on /center/tax
        if not await self._wait_for_passcode():
            return []

        # Wait for page content to fully load after passcode
        try:
            await self.page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        await self.page.wait_for_timeout(3000)

        # Verify we're on the tax page
        if "/center/tax" not in self.page.url.lower():
            print("      ERROR: Not on /center/tax page. Cannot proceed.")
            return []

        # Find the account dropdown
        dropdown = await self._find_account_dropdown()
        if not dropdown:
            print("      ERROR: No account dropdown found. Cannot proceed.")
            return []

        # Read all account options from the dropdown
        accounts = await self._parse_dropdown_options(dropdown)

        if not accounts:
            print("      ERROR: No accounts found in dropdown. Cannot proceed.")

        return accounts

    async def _find_account_dropdown(self):
        """Find the account selector dropdown on the /center/tax page.

        The dropdown is a custom div with class 'g-input' inside the
        E-Documents heading row. It contains a <p> with the account text
        and an SVG chevron arrow.
        """
        # Strategy 1: Find the 'g-input' element inside the E-Documents section.
        # The E-Documents heading is an <h2> in the main content area.
        # The dropdown is a sibling div with class 'g-input'.
        try:
            dropdown = self.page.locator(".g-input").first
            if await dropdown.is_visible(timeout=3000):
                text = (await dropdown.inner_text()).strip()
                print(f"      Found dropdown (.g-input): '{text}'")
                return dropdown
        except Exception:
            pass

        # Strategy 2: Find a <select> element (in case the UI changes)
        try:
            selects = await self.page.locator("select").all()
            for sel in selects:
                if await sel.is_visible(timeout=500):
                    text = await sel.evaluate(
                        "el => el.options[el.selectedIndex]?.text || ''"
                    )
                    if text.strip():
                        print(f"      Found <select> dropdown: '{text.strip()}'")
                        return sel
        except Exception:
            pass

        # Strategy 3: Find the <p> tag with account text inside the
        # E-Documents section (the <h2> heading's parent container)
        try:
            heading = self.page.locator("h2:has-text('E-Documents')").first
            if await heading.is_visible(timeout=2000):
                # The dropdown is in the same parent container as the heading
                container = heading.locator("..")  # parent
                dropdown = container.locator("div >> p").first
                if await dropdown.is_visible(timeout=1000):
                    text = (await dropdown.inner_text()).strip()
                    if text and len(text) < 100:
                        # Return the clickable parent (the div with g-input or similar)
                        parent_div = dropdown.locator("..")
                        print(f"      Found dropdown (via h2 parent): '{text}'")
                        return parent_div
        except Exception:
            pass

        print("      WARNING: No account dropdown found on page")
        return None

    async def _parse_dropdown_options(self, dropdown) -> list[AccountInfo]:
        """Read all account options from the dropdown.

        Handles both native <select> elements and custom dropdowns.
        For custom dropdowns, we:
          1. Read the currently displayed text as the first account
          2. Click to expand and look for additional account options
        """
        accounts = []

        # Check if it's a native <select> element
        tag = await dropdown.evaluate("el => el.tagName.toLowerCase()")

        if tag == "select":
            # Native <select> — read all <option> elements via JS
            options = await dropdown.evaluate("""el => {
                return Array.from(el.options).map(o => ({
                    text: o.text.trim(),
                    value: o.value,
                }));
            }""")

            for opt in options:
                text = opt["text"]
                if not text:
                    continue
                parsed = self._parse_account_text(text)
                if parsed:
                    acct_name, last4 = parsed
                    label = self.make_account_label(acct_name, last4)
                    if not any(a.label == label for a in accounts):
                        accounts.append(AccountInfo(acct_name, last4, label))
                        print(f"        Found account: {acct_name} ...{last4} → {label}")
        else:
            # Custom dropdown — first read the currently displayed text
            current_text = (await dropdown.inner_text()).strip()
            if current_text:
                parsed = self._parse_account_text(current_text)
                if parsed:
                    acct_name, last4 = parsed
                    label = self.make_account_label(acct_name, last4)
                    accounts.append(AccountInfo(acct_name, last4, label))
                    print(f"        Found account: {acct_name} ...{last4} → {label}")

            # Click to expand and look for additional account options.
            # Only look at elements that appear near/inside the dropdown itself
            # to avoid picking up sidebar nav items.
            try:
                # Get dropdown position before clicking
                dropdown_box = await dropdown.bounding_box()
                await dropdown.click()
                await self.page.wait_for_timeout(1500)

                if dropdown_box:
                    # Find option elements that appeared near the dropdown
                    # (within a reasonable vertical range below it)
                    extra_options = await self.page.evaluate("""(dropdownRect) => {
                        const results = [];
                        const accountPattern = /^(.+?)\\s*\\(([A-Za-z0-9]+)\\)\\s*$/;
                        const seen = new Set();
                        // Look for visible elements near the dropdown
                        const all = document.querySelectorAll('p, span, div, li');
                        for (const el of all) {
                            const rect = el.getBoundingClientRect();
                            if (rect.width === 0 || rect.height === 0) continue;
                            // Must be within 300px below the dropdown and roughly aligned horizontally
                            if (rect.top < dropdownRect.y - 10) continue;
                            if (rect.top > dropdownRect.y + 400) continue;
                            if (Math.abs(rect.x - dropdownRect.x) > 100) continue;
                            // Check direct text content only (not children text)
                            const directText = Array.from(el.childNodes)
                                .filter(n => n.nodeType === 3)
                                .map(n => n.textContent.trim())
                                .join('');
                            const text = directText || el.textContent.trim();
                            if (!text || text.length > 80 || seen.has(text)) continue;
                            const m = text.match(accountPattern);
                            if (m) {
                                seen.add(text);
                                results.push(text);
                            }
                        }
                        return results;
                    }""", {"x": dropdown_box["x"], "y": dropdown_box["y"],
                           "width": dropdown_box["width"], "height": dropdown_box["height"]})

                    for text in extra_options:
                        parsed = self._parse_account_text(text)
                        if parsed:
                            acct_name, last4 = parsed
                            label = self.make_account_label(acct_name, last4)
                            if not any(a.label == label for a in accounts):
                                accounts.append(AccountInfo(acct_name, last4, label))
                                print(f"        Found account: {acct_name} ...{last4} → {label}")

                # Close custom dropdown by clicking elsewhere on the page
                await self.page.locator("h2:has-text('E-Documents')").first.click(timeout=2000)
                await self.page.wait_for_timeout(500)
            except Exception:
                # Fallback: click the body
                try:
                    await self.page.evaluate("document.body.click()")
                    await self.page.wait_for_timeout(500)
                except Exception:
                    pass

        return accounts

    @staticmethod
    def _parse_account_text(text: str) -> tuple[str, str] | None:
        """Parse an account option's text into (account_name, last4).

        Examples:
            'Individual (5MQ45136)' → ('Individual', '5136')
            'My Roth (ABC1234)'     → ('My Roth', '1234')
            'Margin (XY99)'         → ('Margin', 'XY99')
        """
        text = re.sub(r"\s+", " ", text).strip()

        # Pattern: "Name (ID)" — extract name before parens, last 4 of ID
        m = re.search(r"^(.+?)\s*\(([A-Za-z0-9]+)\)\s*$", text)
        if m:
            acct_name = m.group(1).strip()
            acct_id = m.group(2).strip()
            last4 = acct_id[-4:]
            return acct_name, last4

        return None

    # ------------------------------------------------------------------
    # Navigation (required by base class but handled in _process_account)
    # ------------------------------------------------------------------

    async def _navigate_to_statements(self, account: AccountInfo) -> None:
        """Navigate to the tax page and select the account.

        The 'Account Statement' tab is only clicked once (first account).
        Subsequent accounts just switch the dropdown selection.
        """
        # Only navigate away if we're not already on the tax page
        if "/center/tax" not in self.page.url.lower():
            await self._goto_tax_page()
            await self.page.wait_for_timeout(3000)

        # Select the correct account from the dropdown
        await self._select_account(account)

        # Click the "Account Statement" tab only once
        if not self._account_statement_tab_clicked:
            await self._click_account_statement_tab()
            self._account_statement_tab_clicked = True

    async def _select_account(self, account: AccountInfo) -> None:
        """Select the given account from the dropdown by matching account_last4."""
        dropdown = await self._find_account_dropdown()
        if not dropdown:
            return

        tag = await dropdown.evaluate("el => el.tagName.toLowerCase()")

        if tag == "select":
            # Native <select> — find the option whose text contains last4
            selected = await dropdown.evaluate("""(el, last4) => {
                for (const opt of el.options) {
                    if (opt.text.includes(last4)) {
                        el.value = opt.value;
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                        return opt.text.trim();
                    }
                }
                return null;
            }""", account.account_last4)

            if selected:
                await self.page.wait_for_timeout(2000)
                print(f"        ✓ Selected account: {selected}")
            else:
                print(f"        WARNING: Could not find account ...{account.account_last4} in dropdown")
        else:
            # Custom dropdown
            current_text = (await dropdown.inner_text()).strip()
            if account.account_last4 in current_text:
                print(f"        ✓ Account already selected: {account.account_type} ...{account.account_last4}")
                return

            try:
                await dropdown.click()
                await self.page.wait_for_timeout(1500)

                # Look for a visible option containing last4
                option = self.page.locator(
                    f"[role='option']:has-text('{account.account_last4}')"
                ).first
                if await option.is_visible(timeout=2000):
                    await option.click()
                    await self.page.wait_for_timeout(2000)
                    print(f"        ✓ Selected account: {account.account_type} ...{account.account_last4}")
                else:
                    print(f"        WARNING: Could not find account ...{account.account_last4} in dropdown")
                    await self.page.keyboard.press("Escape")
            except Exception as e:
                print(f"        WARNING: Could not select account: {e}")

    async def _click_account_statement_tab(self) -> None:
        """Click the 'Account Statement' tab on the tax page."""
        # First ensure any open dropdown overlay is dismissed
        await self.page.evaluate("document.body.click()")
        await self.page.wait_for_timeout(500)

        try:
            tab = self.page.get_by_text("Account Statement", exact=False).first
            if await tab.is_visible(timeout=3000):
                # Use force=True to bypass any remaining overlay
                await tab.click(force=True)
                await self.page.wait_for_timeout(2000)
                print("        ✓ Clicked 'Account Statement' tab")
                return

            for text in ["Account Statements", "Statements", "Monthly Statement"]:
                tab = self.page.get_by_text(text, exact=False).first
                try:
                    if await tab.is_visible(timeout=1000):
                        await tab.click(force=True)
                        await self.page.wait_for_timeout(2000)
                        print(f"        ✓ Clicked '{text}' tab")
                        return
                except Exception:
                    continue
        except Exception as e:
            print(f"        WARNING: Could not click Account Statement tab: {e}")

    # ------------------------------------------------------------------
    # Not used — _process_account handles everything
    # ------------------------------------------------------------------

    async def _get_available_statements(self, account: AccountInfo) -> list[StatementInfo]:
        """Required by base class but not used."""
        return []

    async def _download_statement(self, stmt: StatementInfo, target: Path) -> Path | None:
        """Required by base class but not used directly."""
        return None

    # ------------------------------------------------------------------
    # Override run() to print failure summary at the end
    # ------------------------------------------------------------------

    async def run(self) -> int:
        total = await super().run()
        if self._failed_downloads:
            print(f"\n  WARNING: {len(self._failed_downloads)} statement(s) could not be downloaded:")
            for name in self._failed_downloads:
                print(f"    - {name}")
        return total

    # ------------------------------------------------------------------
    # Main processing — override to handle year-by-year calendar
    # ------------------------------------------------------------------

    async def _process_account(self, account: AccountInfo) -> int:
        """Download all statements for an account using the calendar grid.

        Flow:
        1. Navigate to /center/tax, select account, click Account Statement tab
        2. Read the current year from the calendar header
        3. For each month in the 3x4 grid: if clickable → download
        4. Click left arrow to go to previous year
        5. Repeat until a year has zero clickable months → stop
        """
        await self._navigate_to_statements(account)

        # Wait for the calendar grid to appear
        await self._wait_for_calendar()

        known_hashes = self.tracker.get_all_hashes(self.config.slug)
        downloaded_dates = self.tracker.get_downloaded_dates(
            self.config.slug, account.label
        )

        total_downloaded = 0

        while True:
            # Read the current year from the calendar header
            year = await self._get_calendar_year()
            if not year:
                print("      Could not determine calendar year, stopping")
                break

            print(f"      Processing year: {year}")

            year_downloads = 0

            # Iterate through each month in the grid
            for i, month_name in enumerate(MONTH_NAMES):
                month_num = f"{i+1:02d}"
                date_str = f"{year}-{month_num}"

                # Skip if already downloaded (check both "2023-07" and
                # sub-numbered entries like "2023-07-1", "2023-07-2")
                if date_str in downloaded_dates or any(
                    d.startswith(f"{date_str}-") for d in downloaded_dates
                ):
                    continue

                # Find the month cell in the grid
                month_cell = await self._find_month_cell(month_name)
                if not month_cell:
                    continue

                # Check if the month is clickable (not disabled/greyed out)
                if not await self._is_month_clickable(month_cell):
                    continue

                # Download the statement(s) for this month
                # (may return multiple files if Webull shows a multi-document popup)
                file_paths = await self._download_month(
                    month_cell, account, date_str
                )
                for file_path in file_paths:
                    if file_path and file_path.exists():
                        # SHA-256 duplicate check
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
                            year_downloads += 1
                            total_downloaded += 1

                if file_paths:
                    downloaded_dates.add(date_str)
                    await asyncio.sleep(DOWNLOAD_DELAY)

            if year_downloads == 0:
                print(f"      Year {year}: all available statements already downloaded")
            else:
                print(f"      Year {year}: downloaded {year_downloads} statement(s)")

            # Navigate to the previous year
            went_back = await self._go_to_previous_year()
            if not went_back:
                print("      Could not navigate to previous year, stopping")
                break

        return total_downloaded

    # ------------------------------------------------------------------
    # Calendar helpers
    # ------------------------------------------------------------------

    async def _wait_for_calendar(self) -> None:
        """Wait for the month-grid calendar to appear."""
        # Webull uses full month names ("January", "February", etc.)
        try:
            await self.page.get_by_text("January", exact=True).first.wait_for(timeout=8000)
        except Exception:
            print("      WARNING: Calendar grid did not appear")
            await self.page.wait_for_timeout(2000)

    async def _get_calendar_year(self) -> str | None:
        """Read the current year displayed above the calendar grid.

        Finds the year by looking for a standalone 4-digit number (20xx)
        that is near month abbreviations (Jan, Feb, etc.) on the page.
        """
        try:
            year = await self.page.evaluate("""() => {
                // Find all elements containing a 4-digit year
                const yearRegex = /^\\s*(20\\d{2})\\s*$/;
                const all = document.querySelectorAll('span, div, p, h1, h2, h3, h4, td, th');
                for (const el of all) {
                    // Check direct text (not children) for a standalone year
                    const directText = Array.from(el.childNodes)
                        .filter(n => n.nodeType === 3)
                        .map(n => n.textContent.trim())
                        .join('');
                    const m = directText.match(yearRegex);
                    if (m) {
                        // Verify this element is near month text (within same container)
                        const parent = el.parentElement;
                        if (parent) {
                            const parentText = parent.textContent || '';
                            if (/January|February|March|April|May|June|July|August|September|October|November|December/i.test(parentText)
                                || /arrow|nav|calendar|year/i.test(parent.className || '')) {
                                return m[1];
                            }
                        }
                        // Also check siblings
                        const container = el.closest('div, section');
                        if (container) {
                            const containerText = container.textContent || '';
                            if (/January|February|March|April|May|June|July|August|September|October|November|December/i.test(containerText)) {
                                return m[1];
                            }
                        }
                    }
                }
                // Fallback: find any element with just a year
                for (const el of all) {
                    const text = (el.textContent || '').trim();
                    const m = text.match(yearRegex);
                    if (m) return m[1];
                }
                return null;
            }""")
            return year
        except Exception as e:
            print(f"      WARNING: Could not read calendar year: {e}")
            return None

    async def _find_month_cell(self, month_name: str):
        """Find the clickable cell for a given month name in the grid."""
        try:
            cells = await self.page.get_by_text(month_name, exact=True).all()
            for cell in cells:
                try:
                    if await cell.is_visible(timeout=500):
                        return cell
                except Exception:
                    continue
        except Exception:
            pass
        return None

    async def _is_month_clickable(self, cell) -> bool:
        """Determine if a month cell is clickable (not disabled/greyed out)."""
        try:
            # Check various indicators of a disabled state
            is_disabled = await cell.evaluate("""el => {
                // Check the element and its ancestors for disabled indicators
                let node = el;
                for (let i = 0; i < 3; i++) {
                    if (!node) break;
                    const style = window.getComputedStyle(node);
                    const classes = node.className || '';

                    // Check disabled attribute
                    if (node.hasAttribute('disabled') || node.getAttribute('aria-disabled') === 'true') return true;

                    // Check CSS indicators
                    if (style.pointerEvents === 'none') return true;
                    if (parseFloat(style.opacity) < 0.5) return true;

                    // Check class names
                    if (/disabled|inactive|unavailable|grey|gray/i.test(classes)) return true;

                    node = node.parentElement;
                }
                return false;
            }""")
            return not is_disabled
        except Exception:
            return False

    async def _go_to_previous_year(self) -> bool:
        """Click the left arrow button to navigate to the previous year.

        Strategy: find the element displaying the current year, then look for
        a clickable sibling/nearby element to its left (the previous-year arrow).
        """
        old_year = await self._get_calendar_year()
        if not old_year:
            return False

        try:
            # Use JS to find and click the left arrow near the year element.
            # The arrows are circular buttons (possibly SVGs) to the left/right of the year.
            clicked = await self.page.evaluate("""(yearText) => {
                const yearRegex = new RegExp('^\\\\s*' + yearText + '\\\\s*$');
                const all = document.querySelectorAll('span, div, p, td, th');

                for (const el of all) {
                    const directText = Array.from(el.childNodes)
                        .filter(n => n.nodeType === 3)
                        .map(n => n.textContent.trim())
                        .join('');
                    if (!yearRegex.test(directText)) continue;

                    // Found the year element — search up to grandparent for nearby clickables
                    const yearRect = el.getBoundingClientRect();
                    const containers = [el.parentElement, el.parentElement?.parentElement].filter(Boolean);

                    for (const container of containers) {
                        const clickables = container.querySelectorAll('*');
                        for (const btn of clickables) {
                            const rect = btn.getBoundingClientRect();
                            if (rect.width === 0 || rect.height === 0) continue;
                            // Must be to the left of the year text
                            if (rect.right > yearRect.left + 5) continue;
                            // Must be roughly vertically aligned
                            if (Math.abs(rect.top - yearRect.top) > 20) continue;
                            // Click using dispatchEvent (works for SVGs and divs)
                            btn.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                            return true;
                        }
                    }
                }
                return false;
            }""", old_year)

            if clicked:
                await self.page.wait_for_timeout(1500)
                new_year = await self._get_calendar_year()
                if new_year and new_year != old_year:
                    return True

        except Exception as e:
            print(f"      WARNING: JS arrow click failed: {e}")

        # Fallback: try common CSS selectors
        arrow_selectors = [
            "button[class*='prev']",
            "[class*='arrow-left'], [class*='arrowLeft']",
            "button[aria-label*='previous'], button[aria-label*='Previous']",
        ]
        for sel in arrow_selectors:
            try:
                btn = self.page.locator(sel).first
                if await btn.is_visible(timeout=1000):
                    await btn.click()
                    await self.page.wait_for_timeout(1500)
                    new_year = await self._get_calendar_year()
                    if new_year and new_year != old_year:
                        return True
            except Exception:
                continue

        return False

    async def _download_month(
        self, month_cell, account: AccountInfo, date_str: str
    ) -> list[Path]:
        """Click a month cell and download the resulting statement(s).

        Webull may either:
          1. Start a direct download (single statement)
          2. Show a popup with multiple download links (e.g. account migration)

        Returns a list of successfully downloaded file paths.
        """
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Click the month cell — use force=True to avoid scroll-into-view issues
        # if there's a leftover overlay. Then check what happened.
        await month_cell.click(force=True)
        await self.page.wait_for_timeout(2000)

        # Check if a multi-document popup appeared (detected by OK button
        # or a modal overlay — this happens when a month has multiple statements)
        has_popup = False
        try:
            ok_btn = self.page.get_by_text("OK", exact=True).first
            has_popup = await ok_btn.is_visible(timeout=2000)
        except Exception:
            pass

        if has_popup:
            return await self._handle_multi_doc_popup(account, date_str)

        # No popup — try to capture a download that may have started
        try:
            async with self.page.expect_download(timeout=8000) as download_info:
                # Re-click in case the first click opened the download
                await month_cell.click(force=True)
            download = await download_info.value
            filename = f"{date_str}_{self.config.folder_name}_{account.label}.pdf"
            target = self.output_dir / filename
            await download.save_as(str(target))
            if target.exists() and target.stat().st_size > 0:
                self._record(account, date_str, filename, target)
                print(f"      Downloaded: {filename}")
                return [target]
        except Exception:
            pass

        # Close any leftover popup before giving up
        await self._close_popup()
        self._failed_downloads.append(f"{date_str} ({account.label}) | no download triggered and no popup detected")
        print(f"      FAILED: {date_str} ({account.label}) — no download triggered and no popup detected")
        return []

    async def _handle_multi_doc_popup(
        self, account: AccountInfo, date_str: str
    ) -> list[Path]:
        """Handle a popup that shows multiple downloadable documents.

        After clicking a month cell, Webull may show a modal with several
        download links (each with an SVG download icon). This method finds
        all download icons inside the popup and clicks each one.
        """
        print(f"      Multi-document popup detected for {date_str}")
        downloaded: list[Path] = []

        # Find all download icons inside the wb-modal popup.
        # The popup uses class prefix "wb-modal". We find the modal container,
        # then locate all SVG icons that are NOT the close (X) button.
        download_icons = await self.page.evaluate("""() => {
            // Find the wb-modal container directly
            const modal = document.querySelector('[class*="wb-modal"]');
            if (!modal) return [];

            // Walk up to the outermost wb-modal wrapper if needed
            let popup = modal;
            while (popup.parentElement && (popup.parentElement.className || '').includes('wb-modal')) {
                popup = popup.parentElement;
            }

            // Find the close button so we can exclude its SVG
            const closeBtn = popup.querySelector('[aria-label="Close"], .wb-modal-close');
            const closeSvg = closeBtn ? closeBtn.querySelector('svg') : null;

            // Find all SVGs in the popup, excluding the close button's SVG
            const svgs = popup.querySelectorAll('svg');
            const results = [];
            for (const svg of svgs) {
                if (svg === closeSvg) continue;
                const rect = svg.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0) continue;
                results.push({
                    x: rect.x + rect.width / 2,
                    y: rect.y + rect.height / 2,
                });
            }
            return results;
        }""")

        if not download_icons:
            print(f"      WARNING: Popup detected but no download icons found")
            await self._close_popup()
            self._failed_downloads.append(f"{date_str} ({account.label})")
            return []

        print(f"      Found {len(download_icons)} downloadable document(s) in popup")

        for i, icon in enumerate(download_icons, start=1):
            filename = f"{date_str}-{i}_{self.config.folder_name}_{account.label}.pdf"
            target = self.output_dir / filename

            try:
                async with self.page.expect_download(timeout=15000) as download_info:
                    await self.page.mouse.click(icon["x"], icon["y"])
                download = await download_info.value
                await download.save_as(str(target))
                if target.exists() and target.stat().st_size > 0:
                    self._record(account, f"{date_str}-{i}", filename, target)
                    print(f"      Downloaded: {filename}")
                    downloaded.append(target)
                    await asyncio.sleep(DOWNLOAD_DELAY)
            except Exception as e:
                self._failed_downloads.append(f"{date_str}-{i} ({account.label}) | {e}")
                print(f"      FAILED: {filename} — {e}")

        # Close the popup
        await self._close_popup()
        return downloaded

    async def _close_popup(self) -> None:
        """Close any open popup/modal by clicking OK, X button, or the page body."""
        # Try OK button
        try:
            ok_btn = self.page.get_by_text("OK", exact=True).first
            if await ok_btn.is_visible(timeout=1000):
                await ok_btn.click()
                await self.page.wait_for_timeout(1000)
                return
        except Exception:
            pass

        # Try X close button (Webull uses wb-modal-close class)
        try:
            for sel in [".wb-modal-close", "[aria-label='Close']", "button:has(svg)"]:
                close_btn = self.page.locator(sel).first
                if await close_btn.is_visible(timeout=500):
                    await close_btn.click()
                    await self.page.wait_for_timeout(1000)
                    return
        except Exception:
            pass

        # Last resort: click the page body
        await self.page.evaluate("document.body.click()")
        await self.page.wait_for_timeout(500)

    def _record(
        self, account: AccountInfo, date_str: str, filename: str, file_path: Path
    ) -> None:
        """Record a successful download in the tracker."""
        self.tracker.record_download(
            brokerage_slug=self.config.slug,
            display_name=self.config.display_name,
            folder_name=self.config.folder_name,
            account_label=account.label,
            account_type=account.account_type,
            account_last4=account.account_last4,
            statement_date=date_str,
            filename=filename,
            file_path=file_path,
        )

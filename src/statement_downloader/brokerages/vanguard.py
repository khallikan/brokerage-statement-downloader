"""Vanguard brokerage module.

Vanguard has two completely separate statement areas:

1. **Personal Investor** (https://statements.web.vanguard.com/)
   Monthly statements with year/month dropdowns, "Update Table" button,
   and a table with download icons.

2. **Employer Plan** (https://ownyourfuture.vanguard.com/main/manage/statements)
   Quarterly statements grouped by year with Download buttons.

Both flows are handled by dispatching from two synthetic "meta-accounts".

TODO: Personal Investor account labels include the full account holder name and
      account type from the table row (e.g. "angelsanchez—brokerageaccount—25581152"),
      making filenames long. Consider extracting just account type + last4.
TODO: Vanguard shows a feedback survey popup on the statements page that can
      interfere with the user experience. Consider auto-dismissing it.
"""

import asyncio
import hashlib
import re
from pathlib import Path

from ..base_brokerage import BaseBrokerage, AccountInfo, StatementInfo
from ..config import DOWNLOAD_DELAY


MONTH_NUM = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "may": "05", "jun": "06", "jul": "07", "aug": "08",
    "sep": "09", "oct": "10", "nov": "11", "dec": "12",
}


def _parse_employer_date(text: str) -> str | None:
    """Parse an employer plan statement date range into 'YYYY-MM-MM' format.

    Handles formats like:
    - "October 1 – December 31, 2025" → "2025-10-12"
    - "July 1 – September 30, 2025"   → "2025-07-09"
    - "Oct-Dec 2025"                   → "2025-10-12"
    - "Oct–Dec"                        → None (no year)

    Returns None if the text cannot be parsed.
    """
    text = text.strip()
    # Normalise dashes/en-dashes/em-dashes
    normalized = text.replace("\u2013", "-").replace("\u2014", "-")

    # Extract year
    year_match = re.search(r"(20\d{2})", normalized)
    if not year_match:
        return None
    year = year_match.group(1)

    # Extract month names (first 3 chars each)
    month_pattern = r"(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    months = re.findall(month_pattern, normalized, re.IGNORECASE)
    if len(months) >= 2:
        start = MONTH_NUM.get(months[0][:3].lower())
        end = MONTH_NUM.get(months[1][:3].lower())
        if start and end:
            return f"{year}-{start}-{end}"

    return None


def _slugify(text: str) -> str:
    """Convert a plan name like 'GOOGLE LLC 401(K) SAVINGS PLAN' to 'google401ksavingsplan'."""
    text = text.lower()
    text = text.replace("(", "").replace(")", "")
    text = re.sub(r"[^a-z0-9]", "", text)
    return text


class VanguardBrokerage(BaseBrokerage):
    """Vanguard statement downloader with Personal Investor + Employer Plan flows."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._failed_downloads: list[str] = []

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------

    async def _is_logged_in(self) -> bool:
        url = self.page.url.lower()
        if "logon.vanguard.com" in url:
            return False
        if any(domain in url for domain in [
            "dashboard.web.vanguard.com",
            "statements.web.vanguard.com",
            "ownyourfuture.vanguard.com",
        ]):
            return True
        return False

    async def _wait_for_login(self) -> None:
        print(f"\n{'=' * 60}")
        print(f"  Please log in to {self.config.display_name}")
        print(f"  Complete any 2FA prompts in the browser window.")
        print(f"  Script will auto-continue when login is detected...")
        print(f"{'=' * 60}\n")

        try:
            await self.page.wait_for_url(
                lambda url: "dashboard.web.vanguard.com" in url.lower()
                or "statements.web.vanguard.com" in url.lower()
                or "ownyourfuture.vanguard.com" in url.lower(),
                timeout=300000,  # 5 minutes
            )
            print("  Login detected! Continuing...")
            await self.page.wait_for_timeout(3000)
        except Exception:
            url = self.page.url.lower()
            if "logon.vanguard.com" not in url:
                print("  Login detected! Continuing...")
            else:
                print("  WARNING: Could not detect login. Proceeding anyway...")

    # ------------------------------------------------------------------
    # Accounts — two synthetic meta-accounts
    # ------------------------------------------------------------------

    async def _get_accounts(self) -> list[AccountInfo]:
        await self.page.evaluate("document.body.style.zoom = '0.75'")
        return [
            AccountInfo("Personal Investor", "PI00", "personalinvestor"),
            AccountInfo("Employer Plan", "EP00", "employerplan"),
        ]

    # ------------------------------------------------------------------
    # Dispatch — override _process_account
    # ------------------------------------------------------------------

    async def _process_account(self, account: AccountInfo) -> int:
        if account.account_last4 == "PI00":
            return await self._process_personal_statements()
        elif account.account_last4 == "EP00":
            try:
                return await self._process_employer_statements()
            except Exception as e:
                print(f"    Employer Plan: skipped ({e})")
                return 0
        return 0

    # ------------------------------------------------------------------
    # Override run() to print failure summary
    # ------------------------------------------------------------------

    async def run(self) -> int:
        total = await super().run()
        if self._failed_downloads:
            print(f"\n  WARNING: {len(self._failed_downloads)} statement(s) could not be downloaded:")
            for name in self._failed_downloads:
                print(f"    - {name}")
        return total

    # ------------------------------------------------------------------
    # Not used — _process_account dispatches directly
    # ------------------------------------------------------------------

    async def _navigate_to_statements(self, account: AccountInfo) -> None:  # noqa: ARG002
        pass

    async def _get_available_statements(self, account: AccountInfo) -> list[StatementInfo]:  # noqa: ARG002
        return []

    async def _download_statement(self, stmt: StatementInfo, target: Path) -> Path | None:  # noqa: ARG002
        return None

    # ==================================================================
    # PERSONAL INVESTOR FLOW
    # ==================================================================

    async def _process_personal_statements(self) -> int:
        """Download statements from https://statements.web.vanguard.com/.

        Flow:
        1. Navigate to statements page
        2. Click "Statements" sub-tab if present
        3. Read year options from the Year <select>
        4. For each year: select year, set month to "All months", click "Update Table"
        5. Parse table rows, extract date/account/download link
        6. Download each new statement
        7. Stop after 2 consecutive years with zero new downloads
        """
        print("    Personal Investor: processing statements...")

        await self.page.goto(
            self.config.statements_url,
            wait_until="domcontentloaded",
        )
        await self.page.wait_for_timeout(4000)

        # Click "Statements" sub-tab if present
        try:
            statements_tab = self.page.get_by_text("Statements", exact=True).first
            if await statements_tab.is_visible(timeout=3000):
                await statements_tab.click(force=True)
                await self.page.wait_for_timeout(2000)
        except Exception:
            pass

        # Read year options from the Year <select> dropdown
        years = await self.page.evaluate("""() => {
            const selects = document.querySelectorAll('select');
            for (const sel of selects) {
                const opts = Array.from(sel.options).map(o => o.value || o.text.trim());
                // Year dropdown has 4-digit values
                const yearOpts = opts.filter(v => /^\\d{4}$/.test(v));
                if (yearOpts.length > 0) return yearOpts;
            }
            return [];
        }""")

        if not years:
            print("    Personal Investor: no year dropdown found")
            return 0

        # Sort newest first
        years.sort(reverse=True)
        print(f"    Personal Investor: found years {', '.join(years)}")

        known_hashes = self.tracker.get_all_hashes(self.config.slug)
        total_downloaded = 0

        for year in years:
            year_downloads = 0

            # Select year in the year dropdown
            await self.page.evaluate("""(year) => {
                const selects = document.querySelectorAll('select');
                for (const sel of selects) {
                    const opts = Array.from(sel.options);
                    const yearOpt = opts.find(o => (o.value || o.text.trim()) === year);
                    if (yearOpt) {
                        sel.value = yearOpt.value;
                        sel.dispatchEvent(new Event('change', {bubbles: true}));
                        return;
                    }
                }
            }""", year)
            await self.page.wait_for_timeout(1000)

            # Set month to "All months" (or "All") in the month dropdown
            await self.page.evaluate("""() => {
                const selects = document.querySelectorAll('select');
                for (const sel of selects) {
                    const opts = Array.from(sel.options);
                    const allOpt = opts.find(o =>
                        /all/i.test(o.text) || /all/i.test(o.value)
                    );
                    if (allOpt && opts.some(o => /jan|feb|mar/i.test(o.text))) {
                        sel.value = allOpt.value;
                        sel.dispatchEvent(new Event('change', {bubbles: true}));
                        return;
                    }
                }
            }""")
            await self.page.wait_for_timeout(500)

            # Click "Update Table" button
            try:
                update_btn = self.page.get_by_text("Update Table", exact=False).first
                if await update_btn.is_visible(timeout=3000):
                    await update_btn.click(force=True)
                    await self.page.wait_for_timeout(3000)
            except Exception:
                pass

            # Wait for table to load
            try:
                await self.page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            await self.page.wait_for_timeout(2000)

            # Parse table rows via JS
            rows = await self.page.evaluate("""() => {
                const results = [];
                const tables = document.querySelectorAll('table');
                for (const table of tables) {
                    const trs = table.querySelectorAll('tbody tr');
                    for (let i = 0; i < trs.length; i++) {
                        const cells = trs[i].querySelectorAll('td');
                        if (cells.length < 2) continue;

                        // First cell: date
                        const dateText = (cells[0].textContent || '').trim();

                        // Second cell: account info (e.g. "Roth IRA - 1234")
                        const acctText = (cells[1].textContent || '').trim();

                        // Last cell: may contain download link/icon
                        const lastCell = cells[cells.length - 1];
                        const hasDownload = lastCell.querySelector('a, button, [role="button"], svg');

                        results.push({
                            rowIndex: i,
                            dateText: dateText,
                            acctText: acctText,
                            hasDownload: !!hasDownload,
                        });
                    }
                }
                return results;
            }""")

            if not rows:
                print(f"      Year {year}: no table rows found")
                continue

            for row in rows:
                date_str = self._parse_personal_date(row["dateText"], year)
                if not date_str:
                    continue

                acct_label = self._parse_personal_account(row["acctText"])
                if not acct_label:
                    continue

                # Check if already downloaded
                downloaded_dates = self.tracker.get_downloaded_dates(
                    self.config.slug, acct_label
                )
                if date_str in downloaded_dates:
                    continue

                if not row["hasDownload"]:
                    continue

                # Download via clicking the last cell's downloadable element
                filename = f"{date_str}_{self.config.folder_name}_{acct_label}.pdf"
                target = self.output_dir / filename
                self.output_dir.mkdir(parents=True, exist_ok=True)

                success = await self._click_personal_download(row["rowIndex"], target)
                if success and target.exists() and target.stat().st_size > 0:
                    # SHA-256 duplicate check
                    file_hash = self._sha256(target)
                    if file_hash in known_hashes:
                        original = known_hashes[file_hash]
                        print(f"        DUPLICATE: {filename} identical to {original}, deleting")
                        target.unlink()
                    else:
                        known_hashes[file_hash] = filename
                        # Determine account type/last4 from the label for recording
                        acct_info = self._label_to_account_info(acct_label)
                        self._record(acct_info, date_str, filename, target)
                        print(f"      Downloaded: {filename}")
                        year_downloads += 1
                        total_downloaded += 1
                        await asyncio.sleep(DOWNLOAD_DELAY)
                else:
                    size = target.stat().st_size if target.exists() else 0
                    reason = "empty file" if target.exists() else "file not created"
                    self._failed_downloads.append(
                        f"{filename} | date={row['dateText']!r} | acct={acct_label} | reason={reason} (size={size})"
                    )
                    if target.exists():
                        target.unlink()
                    print(f"      FAILED: {filename} ({reason}, size={size})")

            if year_downloads == 0:
                print(f"      Year {year}: all statements already downloaded")
            else:
                print(f"      Year {year}: downloaded {year_downloads} statement(s)")

        print(f"    Personal Investor: {total_downloaded} total new statement(s)")
        return total_downloaded

    def _parse_personal_date(self, text: str, fallback_year: str) -> str | None:
        """Parse a date from a personal investor table row.

        Handles formats like:
        - "01/15/2025" or "1/15/2025"
        - "January 2025"
        - "Jan 2025"
        """
        text = text.strip()

        # MM/DD/YYYY
        m = re.search(r"(\d{1,2})/\d{1,2}/(\d{4})", text)
        if m:
            return f"{m.group(2)}-{m.group(1).zfill(2)}"

        # Month name + year
        month_map = {
            "january": "01", "february": "02", "march": "03", "april": "04",
            "may": "05", "june": "06", "july": "07", "august": "08",
            "september": "09", "october": "10", "november": "11", "december": "12",
            "jan": "01", "feb": "02", "mar": "03", "apr": "04",
            "jun": "06", "jul": "07", "aug": "08",
            "sep": "09", "oct": "10", "nov": "11", "dec": "12",
        }
        lower = text.lower()
        for name, num in month_map.items():
            if name in lower:
                year_match = re.search(r"(\d{4})", text)
                yr = year_match.group(1) if year_match else fallback_year
                return f"{yr}-{num}"

        return None

    def _parse_personal_account(self, text: str) -> str | None:
        """Parse account info from a personal investor table row.

        Text like "Vanguard Roth IRA - 1234" or "Brokerage Account ****5678"
        → returns a label like "roth1234" or "brokerageaccount5678".
        """
        text = re.sub(r"\s+", " ", text).strip()

        # Try to extract last 4 digits and account type
        # Pattern: text with trailing digits (possibly masked with * or -)
        m = re.search(r"(\d{4})\s*$", text)
        if not m:
            # Try masked: ****1234 or ...1234
            m = re.search(r"[*.\-]+(\d{4})", text)
        if not m:
            # Try "- 1234" pattern
            m = re.search(r"[-\u2013\u2014]\s*(\d{4})", text)
        if not m:
            return None

        last4 = m.group(1)

        # Extract account type: everything before the number pattern
        acct_type = text[:m.start()].strip()
        # Remove trailing separators
        acct_type = re.sub(r"[\s\-\u2013\u2014*·•]+$", "", acct_type)
        # Remove "Vanguard" prefix if present
        acct_type = re.sub(r"^Vanguard\s+", "", acct_type, flags=re.IGNORECASE)

        if not acct_type:
            acct_type = "Account"

        return self.make_account_label(acct_type, last4)

    async def _click_personal_download(self, row_index: int, target: Path) -> bool:
        """Click the download element in the given table row."""
        try:
            async with self.page.expect_download(timeout=30000) as download_info:
                await self.page.evaluate("""(rowIndex) => {
                    const tables = document.querySelectorAll('table');
                    for (const table of tables) {
                        const trs = table.querySelectorAll('tbody tr');
                        if (rowIndex < trs.length) {
                            const cells = trs[rowIndex].querySelectorAll('td');
                            const lastCell = cells[cells.length - 1];
                            const clickable = lastCell.querySelector(
                                'a, button, [role="button"], svg'
                            );
                            if (clickable) {
                                clickable.dispatchEvent(new MouseEvent('click', {
                                    bubbles: true, cancelable: true
                                }));
                                return;
                            }
                            // Fallback: click the cell itself
                            lastCell.click();
                        }
                    }
                }""", row_index)
            download = await download_info.value
            await download.save_as(str(target))
            return True
        except Exception:
            return False

    # ==================================================================
    # EMPLOYER PLAN FLOW
    # ==================================================================

    async def _process_employer_statements(self) -> int:
        """Download statements from the Vanguard employer plan site.

        Flow:
        1. Navigate to https://ownyourfuture.vanguard.com/main/manage#statementsAndTaxForms
        2. Wait for login if redirected to a login page
        3. Extract plan name from the page header, slugify it
        4. Click the blue arrow (→) next to the "Statements" heading
        5. Click "Show more" / "Load more" / "View more" until all are loaded
        6. Parse date ranges (e.g. "October 1 – December 31, 2025") and Download links
        7. Download each new statement
        """
        print("    Employer Plan: processing statements...")

        employer_url = "https://ownyourfuture.vanguard.com/main/manage#statementsAndTaxForms"
        await self.page.goto(employer_url, wait_until="domcontentloaded")
        await self.page.wait_for_timeout(5000)

        # If redirected to a login page, wait for the user
        url = self.page.url.lower()
        if "ownyourfuture.vanguard.com" not in url or "login" in url or "logon" in url or "auth" in url:
            print("    Employer Plan: separate login required...")
            print(f"\n{'=' * 60}")
            print(f"  Please log in to the Employer Plan site")
            print(f"  Complete any prompts in the browser window.")
            print(f"{'=' * 60}\n")
            try:
                await self.page.wait_for_url(
                    lambda u: "ownyourfuture.vanguard.com" in u.lower()
                    and "login" not in u.lower()
                    and "logon" not in u.lower()
                    and "auth" not in u.lower(),
                    timeout=300000,
                )
                await self.page.wait_for_timeout(3000)
            except Exception:
                print("    Employer Plan: login not detected, skipping")
                return 0

            # Re-navigate to the statements tab after login
            await self.page.goto(employer_url, wait_until="domcontentloaded")
            await self.page.wait_for_timeout(5000)

        # Verify we have page content
        page_text = await self.page.evaluate("() => document.body?.innerText || ''")
        if len(page_text) < 50:
            print("    Employer Plan: page did not load (no content)")
            return 0

        # Handle plan selector if multiple plans exist
        plans = await self._get_employer_plans()
        if not plans:
            plans = [None]

        total_downloaded = 0
        for plan_option in plans:
            if plan_option is not None:
                await self._select_employer_plan(plan_option)
                await self.page.wait_for_timeout(2000)

            count = await self._download_employer_plan_statements()
            total_downloaded += count

        print(f"    Employer Plan: {total_downloaded} total new statement(s)")
        return total_downloaded

    async def _get_employer_plans(self) -> list[dict] | None:
        """Check for a plan selector dropdown. Returns list of plan options or None."""
        plans = await self.page.evaluate("""() => {
            const selects = document.querySelectorAll('select');
            for (const sel of selects) {
                const opts = Array.from(sel.options);
                const planOpts = opts.filter(o => o.text.trim().length > 5);
                if (planOpts.length > 1) {
                    return planOpts.map(o => ({
                        value: o.value,
                        text: o.text.trim(),
                    }));
                }
            }
            return null;
        }""")
        return plans

    async def _select_employer_plan(self, plan: dict) -> None:
        """Select a specific plan from the dropdown."""
        await self.page.evaluate("""(planValue) => {
            const selects = document.querySelectorAll('select');
            for (const sel of selects) {
                const opts = Array.from(sel.options);
                const match = opts.find(o => o.value === planValue);
                if (match) {
                    sel.value = match.value;
                    sel.dispatchEvent(new Event('change', {bubbles: true}));
                    return;
                }
            }
        }""", plan["value"])
        await self.page.wait_for_timeout(2000)
        print(f"      Selected plan: {plan['text']}")

    async def _download_employer_plan_statements(self) -> int:
        """Parse and download statements for the currently visible employer plan."""

        # Zoom out so full page fits without scrolling issues
        await self.page.evaluate("document.body.style.zoom = '0.75'")
        await self.page.wait_for_timeout(1000)

        # Click the blue right arrow (→) next to the "Statements" heading.
        # This navigates from the summary card to the full statements list
        # at /main/manage/statements (which cannot be accessed directly).
        arrow_clicked = await self._click_statements_arrow()

        if arrow_clicked:
            print("      Clicked Statements arrow to navigate")
            await self.page.wait_for_timeout(3000)
            try:
                await self.page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            await self.page.wait_for_timeout(1000)
        else:
            print("      ERROR: Could not find Statements arrow, stopping employer plan flow")
            return 0

        # Now on /main/manage/statements — extract plan name from header.
        # The plan name (e.g. "GOOGLE LLC 401(K) SAVINGS PLAN") appears as
        # smaller text below the "Statements" heading. It may be a <p>, <span>,
        # <div>, or <h> element, so search broadly.
        plan_name = await self.page.evaluate("""() => {
            const els = document.querySelectorAll('h1, h2, h3, h4, p, span, div');
            for (const el of els) {
                const text = (el.innerText || el.textContent || '').split('\\n')[0].trim();
                if (/401\\(K\\)|SAVINGS PLAN|RETIREMENT PLAN|PENSION/i.test(text)
                    && text.length > 5 && text.length < 120) {
                    return text;
                }
            }
            return '';
        }""")

        # Clean plan name — strip everything after first newline
        if plan_name:
            plan_name = plan_name.split("\n")[0].strip()
            acct_slug = _slugify(plan_name)
            print(f"      Plan: {plan_name} -> {acct_slug}")
        else:
            acct_slug = "employerplan"
            print("      Plan name not found, using 'employerplan'")

        acct_info = AccountInfo("Employer Plan", "EP00", acct_slug)

        # Click "Show more" / "View more" / "Load more" until no more appear
        for _ in range(20):
            found_more = False
            for label in ["Show more", "View more", "Load more", "Show all"]:
                try:
                    btn = self.page.get_by_text(label, exact=False).first
                    if await btn.is_visible(timeout=2000):
                        await btn.click(force=True)
                        await self.page.wait_for_timeout(2000)
                        found_more = True
                        break
                except Exception:
                    continue
            if not found_more:
                break

        # Let any lazy content settle
        await self.page.wait_for_timeout(1000)

        known_hashes = self.tracker.get_all_hashes(self.config.slug)
        downloaded_dates = self.tracker.get_downloaded_dates(
            self.config.slug, acct_slug
        )

        # Parse statements. The page structure (from the screenshot) is:
        #   <year heading>  "2025"
        #     "Oct–Dec"  [Download button]
        #     "Jul–Sep"  [Download button]
        #     "Apr–Jun"  [Download button]
        #     "Jan–Mar"  [Download button]
        #   <year heading>  "2024"
        #     "Oct–Dec"  [Download button]
        #     ...
        #
        # The Download buttons are black pill-shaped <button> elements.
        # We find all visible buttons containing "Download", then for each one
        # find the quarter label in a sibling/parent element and the year
        # from the nearest preceding year heading.
        #
        # Instead of storing coordinates (which break with CSS zoom), we store
        # the button index so we can click it via Playwright locator.
        quarters = await self.page.evaluate("""() => {
            const results = [];
            const seen = new Set();

            // Collect all visible "Download" buttons in DOM order
            const buttons = document.querySelectorAll('button, a, [role="button"]');
            let btnIndex = -1;
            for (const btn of buttons) {
                const btnText = (btn.innerText || btn.textContent || '').trim();
                if (!/Download/i.test(btnText)) continue;
                if (btn.offsetWidth === 0 || btn.offsetHeight === 0) continue;
                btnIndex++;

                // Find the quarter label nearby: walk up a few levels and
                // look for "Oct–Dec", "Jan–Mar", etc. pattern
                let quarterText = '';
                let container = btn.parentElement;
                for (let i = 0; i < 4 && container; i++) {
                    const content = container.innerText || '';
                    const qm = content.match(/(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[\\s]*[\\u2013\\u2014\\-][\\s]*(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)/i);
                    if (qm) {
                        quarterText = qm[0];
                        break;
                    }
                    container = container.parentElement;
                }

                // Find the year: walk up and look for a preceding element
                // whose text is exactly a 4-digit year (2020-2029).
                // Use previousElementSibling traversal to find the nearest
                // year heading ABOVE this button's section.
                let yearText = '';
                container = btn.parentElement;
                for (let i = 0; i < 10 && container; i++) {
                    // Check if this container or a previous sibling has a year
                    let sibling = container;
                    while (sibling) {
                        const sibText = (sibling.innerText || sibling.textContent || '').trim();
                        // Check for standalone year at the start of text
                        const ym = sibText.match(/^(20\\d{2})\\b/);
                        if (ym) {
                            yearText = ym[1];
                            break;
                        }
                        sibling = sibling.previousElementSibling;
                    }
                    if (yearText) break;
                    container = container.parentElement;
                }

                if (quarterText && yearText) {
                    const key = quarterText + '|' + yearText;
                    if (!seen.has(key)) {
                        seen.add(key);
                        results.push({
                            quarterText: quarterText,
                            year: yearText,
                            btnIndex: btnIndex,
                        });
                    }
                }
            }
            return results;
        }""")

        if not quarters:
            debug = await self.page.evaluate("""() => {
                return (document.body.innerText || '').substring(0, 2000);
            }""")
            print("      No quarterly statements found on page")
            print(f"      DEBUG page excerpt: {debug[:500]}")
            return 0

        print(f"      Found {len(quarters)} statement(s) with download buttons")

        total_downloaded = 0

        for q in quarters:
            combined = f"{q['quarterText']} {q['year']}"
            date_str = _parse_employer_date(combined)
            if not date_str:
                print(f"      WARNING: Could not parse date: {combined}")
                continue

            if date_str in downloaded_dates:
                continue

            filename = f"{date_str}_{self.config.folder_name}_{acct_slug}.pdf"
            target = self.output_dir / filename
            self.output_dir.mkdir(parents=True, exist_ok=True)

            success = await self._click_employer_download(q["btnIndex"], target)
            if success and target.exists() and target.stat().st_size > 0:
                file_hash = self._sha256(target)
                if file_hash in known_hashes:
                    original = known_hashes[file_hash]
                    print(f"        DUPLICATE: {filename} identical to {original}, deleting")
                    target.unlink()
                else:
                    known_hashes[file_hash] = filename
                    self._record(acct_info, date_str, filename, target)
                    print(f"      Downloaded: {filename}")
                    total_downloaded += 1
                    downloaded_dates.add(date_str)
                    await asyncio.sleep(DOWNLOAD_DELAY)
            else:
                size = target.stat().st_size if target.exists() else 0
                reason = "empty file" if target.exists() else "file not created"
                self._failed_downloads.append(
                    f"{filename} | acct={acct_slug} | reason={reason} (size={size})"
                )
                if target.exists():
                    target.unlink()
                print(f"      FAILED: {filename} ({reason}, size={size})")

        return total_downloaded

    async def _click_statements_arrow(self) -> bool:
        """Find and click the blue right arrow (→) next to the 'Statements' heading.

        The arrow is an SVG or <a> element inside the same card container as
        the "Statements" heading. Clicking it navigates to the full statements
        list at /main/manage/statements.

        Returns True if the arrow was successfully clicked.
        """
        try:
            clicked = await self.page.evaluate("""() => {
                // Look for heading-level elements (h2, h3, h4) whose direct text
                // starts with "Statements" but is NOT "Statements & tax forms"
                const headings = document.querySelectorAll('h2, h3, h4');
                for (const h of headings) {
                    // Get only the heading's own text, ignoring deep children
                    const directText = Array.from(h.childNodes)
                        .filter(n => n.nodeType === 3)
                        .map(n => n.textContent.trim())
                        .join(' ')
                        .trim();
                    const fullText = (h.innerText || '').trim();

                    // Match "Statements" heading but not "Statements & tax forms"
                    const text = directText || fullText.split('\\n')[0].trim();
                    if (!/^Statements$/i.test(text)) continue;

                    // Found the "Statements" heading — now find the arrow.
                    // Search the heading itself and its parent/grandparent
                    // for a clickable arrow element (SVG, <a>, or button)
                    const containers = [h, h.parentElement, h.parentElement?.parentElement].filter(Boolean);
                    const headingRect = h.getBoundingClientRect();

                    for (const container of containers) {
                        // Look for SVGs and links
                        const candidates = container.querySelectorAll('a, button, svg, [role="link"], [role="button"]');
                        for (const c of candidates) {
                            // Skip the heading itself
                            if (c === h) continue;
                            const rect = c.getBoundingClientRect();
                            if (rect.width === 0 || rect.height === 0) continue;
                            // The arrow should be to the right of or near the heading
                            if (rect.x >= headingRect.x - 10) {
                                c.click();
                                return true;
                            }
                        }
                    }
                }
                return false;
            }""")
            if clicked:
                return True
        except Exception:
            pass

        # Fallback: try clicking using Playwright locator — look for an arrow
        # icon/link near a "Statements" text element
        try:
            # The arrow might be an <a> containing an SVG, near "Statements"
            statements_heading = self.page.locator("h2, h3, h4").filter(
                has_text=re.compile(r"^Statements$")
            ).first
            if await statements_heading.is_visible(timeout=3000):
                # Try clicking the parent container (which may be the <a> link)
                parent = statements_heading.locator("..")
                arrow = parent.locator("a, svg, button").first
                if await arrow.is_visible(timeout=2000):
                    await arrow.click(force=True)
                    return True
        except Exception:
            pass

        return False

    async def _click_employer_download(self, btn_index: int, target: Path) -> bool:
        """Click the nth visible Download button on the employer plan page.

        Uses Playwright locator (not mouse coordinates) to avoid issues
        with CSS zoom affecting coordinate mapping.
        """
        try:
            # Find all visible buttons/links containing "Download" text
            download_buttons = self.page.locator(
                "button, a, [role='button']"
            ).filter(has_text=re.compile(r"Download", re.IGNORECASE))

            btn = download_buttons.nth(btn_index)

            async with self.page.expect_download(timeout=30000) as download_info:
                await btn.click(force=True)
            download = await download_info.value
            await download.save_as(str(target))
            return True
        except Exception:
            return False

    # ==================================================================
    # Shared helpers
    # ==================================================================

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

    def _label_to_account_info(self, label: str) -> AccountInfo:
        """Create an AccountInfo from a label discovered during personal investor parsing."""
        # Try to split label into type + last4
        m = re.match(r"^(.+?)(\d{4})$", label)
        if m:
            return AccountInfo(m.group(1), m.group(2), label)
        return AccountInfo("Account", "0000", label)

    @staticmethod
    def _sha256(file_path: Path) -> str:
        """Compute SHA-256 hash of a file."""
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

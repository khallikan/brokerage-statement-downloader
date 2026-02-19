"""M1 Finance brokerage module.

M1 Finance documents: https://dashboard.m1.com/d/settings/documents/statements

All accounts share a single statements page with a year dropdown and a table.
The flow:

1. Navigate to the statements page.
2. Read year options from the year dropdown.
3. For each year (newest first): select it, click "Load more" until gone,
   parse table rows, download each statement.
4. Stop after 2 consecutive empty years.
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


def _parse_date(text: str) -> str | None:
    """Parse 'Jan 31, 2026' → '2026-01'."""
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


def _parse_account(text: str) -> tuple[str, str] | None:
    """Parse '5ME90609 - Invest Individual' → ('Invest Individual', '0609').

    Returns (account_type, last4) or None.
    """
    text = re.sub(r"\s+", " ", text).strip()
    # Primary format: "ID - Type"
    parts = text.split(" - ", 1)
    if len(parts) == 2:
        acct_id = parts[0].strip()
        acct_type = parts[1].strip()
        last4 = acct_id[-4:] if len(acct_id) >= 4 else acct_id
        return (acct_type, last4)
    return None


class M1FinanceBrokerage(BaseBrokerage):
    """M1 Finance statement downloader (single page, year iteration)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._failed_downloads: list[str] = []

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------

    async def _is_logged_in(self) -> bool:
        url = self.page.url.lower()
        return "dashboard.m1.com/d/" in url and "/login" not in url

    # ------------------------------------------------------------------
    # Accounts — single synthetic account
    # ------------------------------------------------------------------

    async def _get_accounts(self) -> list[AccountInfo]:
        return [AccountInfo("M1 Finance", "M100", "m1finance")]

    # ------------------------------------------------------------------
    # Dispatch — override _process_account for year iteration
    # ------------------------------------------------------------------

    async def _process_account(self, account: AccountInfo) -> int:
        return await self._process_m1_statements()

    # ------------------------------------------------------------------
    # Override run() to print failure summary
    # ------------------------------------------------------------------

    async def run(self) -> int:
        total = await super().run()
        if self._failed_downloads:
            print(
                f"\n  WARNING: {len(self._failed_downloads)} statement(s) could not be downloaded:"
            )
            for name in self._failed_downloads:
                print(f"    - {name}")
        return total

    # ------------------------------------------------------------------
    # Stubs (not used — _process_account dispatches directly)
    # ------------------------------------------------------------------

    async def _navigate_to_statements(self, account: AccountInfo) -> None:
        pass

    async def _get_available_statements(self, account: AccountInfo) -> list[StatementInfo]:
        return []

    async def _download_statement(self, stmt: StatementInfo, target: Path) -> Path | None:
        return None

    # ==================================================================
    # MAIN FLOW
    # ==================================================================

    async def _process_m1_statements(self) -> int:
        """Navigate to statements, iterate years, download all statements."""
        print("    M1 Finance: navigating to statements page...")
        await self.page.goto(self.config.statements_url, wait_until="domcontentloaded")
        await self.page.wait_for_timeout(5000)

        # Read year dropdown options
        years = await self._read_year_options()
        if not years:
            print("    M1 Finance: no year options found — parsing visible table")
            return await self._download_visible_statements(None)

        years.sort(reverse=True)  # newest first
        print(f"    M1 Finance: years: {', '.join(years)}")

        known_hashes = self.tracker.get_all_hashes(self.config.slug)
        total_downloaded = 0

        for year in years:
            print(f"    M1 Finance: selecting year '{year}'...")
            if not await self._select_year(year):
                print(f"      Could not select '{year}', skipping")
                continue

            await self.page.wait_for_timeout(2000)

            # Click "Load more" repeatedly until it disappears
            await self._click_load_more_until_done()

            new = await self._download_visible_statements(year, known_hashes)
            total_downloaded += new

            if new == 0:
                rows = await self.page.locator("table tbody tr").all()
                if not rows:
                    print(f"      '{year}': no documents on platform")
                else:
                    print(f"      '{year}': all statements already downloaded")
            else:
                print(f"      '{year}': {new} new statement(s)")

        print(f"    M1 Finance: {total_downloaded} total new statement(s)")
        return total_downloaded

    # ------------------------------------------------------------------
    # Year dropdown helpers
    # ------------------------------------------------------------------

    async def _read_year_options(self) -> list[str]:
        """Return list of year strings from the year dropdown.

        M1 uses a react-select style custom dropdown with:
        - <label for="year">Year</label>
        - <input name="year" type="hidden" value="2025">
        - A clickable div control showing the current year
        """
        # Open the dropdown by clicking the control near the "Year" label
        opened = await self._open_year_dropdown()
        if not opened:
            print("    M1 Finance: could not open year dropdown")
            return []

        await self.page.wait_for_timeout(1500)

        # Read year options — react-select uses id="react-select-N-option-N"
        opts = await self.page.evaluate("""() => {
            const result = [];
            const seen = new Set();
            // Primary: react-select option elements by id pattern
            const optEls = document.querySelectorAll('[id*="react-select"][id*="option"]');
            for (const el of optEls) {
                const text = (el.innerText || el.textContent || '').trim();
                if (/^\\d{4}$/.test(text) && !seen.has(text)) {
                    seen.add(text);
                    result.push(text);
                }
            }
            if (result.length > 0) return result;

            // Fallback: menu container children
            const menuEl = document.querySelector('[class*="-menu"]');
            if (menuEl) {
                for (const el of menuEl.querySelectorAll('*')) {
                    const text = (el.innerText || el.textContent || '').trim();
                    if (/^\\d{4}$/.test(text) && !seen.has(text) && el.children.length <= 2) {
                        seen.add(text);
                        result.push(text);
                    }
                }
            }
            return result;
        }""")

        # Close the dropdown
        await self.page.keyboard.press("Escape")
        await self.page.wait_for_timeout(500)

        if opts:
            print(f"    M1 Finance: found {len(opts)} year option(s): {', '.join(opts)}")
        else:
            print("    M1 Finance: no year options found after opening dropdown")

        return opts or []

    async def _open_year_dropdown(self) -> bool:
        """Click the year dropdown control to open it. Returns True on success."""
        # Strategy 1: Find the control div near input#year via JS
        try:
            result = await self.page.evaluate("""() => {
                const input = document.querySelector('input#year');
                if (!input) return 'no_input';
                // Walk up to find the react-select container, then find the control
                let container = input;
                for (let i = 0; i < 10; i++) {
                    container = container.parentElement;
                    if (!container) break;
                    const control = container.querySelector('[class*="control"], [class*="contr"]');
                    if (control && control.offsetWidth > 0) {
                        // Use mousedown+mouseup to simulate a real click
                        // (react-select sometimes ignores .click())
                        control.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
                        return 'mousedown_control:' + control.className.slice(0, 50);
                    }
                }
                return 'no_control';
            }""")
            if result and result.startswith("mousedown_control"):
                await self.page.wait_for_timeout(500)
                return True
        except Exception:
            pass

        # Strategy 2: Playwright click on the indicator (the chevron/arrow)
        try:
            indicator = self.page.locator("[class*='indicatorContainer'], [class*='indicator']").first
            if await indicator.is_visible(timeout=2000):
                await indicator.click()
                print("    M1 Finance: clicked indicator")
                return True
        except Exception:
            pass

        # Strategy 3: Playwright locator — find the div showing the year value
        # near the "Year" label and click it
        try:
            label = self.page.locator("label").filter(
                has_text=re.compile(r"^Year$", re.IGNORECASE)
            ).first
            if await label.is_visible(timeout=2000):
                # The dropdown control is a sibling of the label, within the same parent
                parent = label.locator("xpath=..")
                # Try clicking progressively broader areas
                for sel in [
                    "[class*='control']",
                    "[class*='contr']",
                    "[class*='select']",
                    "div >> nth=0",
                ]:
                    try:
                        el = parent.locator(sel).first
                        if await el.count() > 0 and await el.is_visible(timeout=500):
                            await el.click()
                            print(f"    M1 Finance: clicked via label parent + {sel}")
                            return True
                    except Exception:
                        continue
                # Last resort: click the parent itself
                await parent.click()
                print("    M1 Finance: clicked label parent")
                return True
        except Exception:
            pass

        return False

    async def _select_year(self, year: str) -> bool:
        """Select a year from the dropdown. Returns True on success."""
        opened = await self._open_year_dropdown()
        if not opened:
            return False

        await self.page.wait_for_timeout(1000)

        # Strategy 1: click the react-select option by id pattern
        try:
            clicked = await self.page.evaluate("""(year) => {
                const optEls = document.querySelectorAll('[id*="react-select"][id*="option"]');
                for (const el of optEls) {
                    const text = (el.innerText || el.textContent || '').trim();
                    if (text.startsWith(year)) {
                        el.click();
                        return true;
                    }
                }
                return false;
            }""", year)
            if clicked:
                await self.page.wait_for_timeout(1000)
                return True
        except Exception:
            pass

        # Strategy 2: Playwright locator on react-select options
        try:
            opt = self.page.locator("[id*='react-select'][id*='option']").filter(
                has_text=re.compile(r"^\s*" + re.escape(year) + r"\s*$")
            ).first
            if await opt.is_visible(timeout=2000):
                await opt.click()
                await self.page.wait_for_timeout(1000)
                return True
        except Exception:
            pass

        # Strategy 3: click from the menu container
        try:
            menu = self.page.locator("[class*='-menu']").first
            opt = menu.locator("div").filter(
                has_text=re.compile(r"^\s*" + re.escape(year) + r"\s*$")
            ).first
            if await opt.is_visible(timeout=2000):
                await opt.click()
                await self.page.wait_for_timeout(1000)
                return True
        except Exception:
            pass

        # Close if we failed
        await self.page.keyboard.press("Escape")
        return False

    # ------------------------------------------------------------------
    # Load more
    # ------------------------------------------------------------------

    async def _click_load_more_until_done(self) -> None:
        """Click 'Load more' button repeatedly until it disappears."""
        max_clicks = 50
        for _ in range(max_clicks):
            try:
                btn = self.page.locator("button").filter(
                    has_text=re.compile(r"load\s*more", re.IGNORECASE)
                ).first
                if not await btn.is_visible(timeout=2000):
                    break
                await btn.click()
                await self.page.wait_for_timeout(2000)
            except Exception:
                break

    # ------------------------------------------------------------------
    # Table parsing & download
    # ------------------------------------------------------------------

    async def _download_visible_statements(
        self, year_label: str | None, known_hashes: dict | None = None
    ) -> int:
        """Parse the visible table and download all statements. Returns new download count."""
        if known_hashes is None:
            known_hashes = self.tracker.get_all_hashes(self.config.slug)

        rows = await self.page.locator("table tbody tr").all()
        if not rows:
            return 0

        total_new = 0

        for i, row in enumerate(rows):
            cells = await row.locator("td").all()
            if len(cells) < 3:
                continue

            date_text = (await cells[0].inner_text()).strip()
            acct_text = (await cells[1].inner_text()).strip()
            doc_link = cells[2].locator("a").first

            doc_text = (await cells[2].inner_text()).strip()

            if not await doc_link.count():
                print(f"      SKIP (no link): row {i} | date={date_text!r} | acct={acct_text!r} | doc={doc_text!r}")
                continue

            date_str = _parse_date(date_text)
            if not date_str:
                print(f"      SKIP (no date): row {i} | dateText={date_text!r} | acct={acct_text!r} | doc={doc_text!r}")
                continue

            # Parse account
            parsed = _parse_account(acct_text)
            if parsed:
                acct_type, acct_last4 = parsed
                acct_label = self.make_account_label(acct_type, acct_last4)
            else:
                # Fallback: use stripped lowercase
                acct_label = re.sub(r"\s+", "", acct_text).lower()
                if not acct_label:
                    acct_label = "m1finance"
                acct_type = acct_text or "M1 Finance"
                acct_last4 = "0000"

            acct_info = AccountInfo(acct_type, acct_last4, acct_label)

            if self.tracker.is_downloaded(self.config.slug, acct_label, date_str):
                continue

            filename = f"{date_str}_{self.config.folder_name}_{acct_label}.pdf"
            target = self.output_dir / filename
            self.output_dir.mkdir(parents=True, exist_ok=True)

            success = await self._download_doc_link(doc_link, target)

            if success and target.exists() and target.stat().st_size > 0:
                file_hash = self._sha256(target)
                if file_hash in known_hashes:
                    orig = known_hashes[file_hash]
                    print(f"        DUPLICATE: content identical to {orig}, discarding")
                    target.unlink()
                else:
                    known_hashes[file_hash] = filename
                    self._record(acct_info, date_str, filename, target)
                    print(f"      Downloaded: {filename}")
                    total_new += 1
                    await asyncio.sleep(DOWNLOAD_DELAY)
            else:
                size = target.stat().st_size if target.exists() else 0
                reason = "empty file" if target.exists() else "file not created"
                desc = (
                    f"{filename} | date={date_text!r} | acct={acct_text!r} "
                    f"| doc={doc_text!r} | reason={reason} (size={size})"
                )
                if year_label:
                    desc += f" | year={year_label}"
                self._failed_downloads.append(desc)
                if target.exists():
                    target.unlink()
                print(f"      FAILED: {filename} ({reason}, size={size})")

        return total_new

    async def _download_doc_link(self, link: "Locator", target: Path) -> bool:
        """Click a document link and capture the PDF.

        M1 Finance opens PDFs in a new tab (popup). We capture the popup,
        get its URL, and download the PDF content.
        Falls back to direct href fetch if popup doesn't work.
        """
        link_text = ""
        link_href = ""
        try:
            link_text = (await link.inner_text(timeout=2000)).strip()
            link_href = await link.get_attribute("href") or ""
        except Exception:
            pass

        # Strategy 1: expect_popup — link opens a new tab with the PDF
        try:
            async with self.page.expect_popup(timeout=15000) as popup_info:
                await link.click()
            popup = await popup_info.value
            await popup.wait_for_load_state("load", timeout=30000)

            pdf_url = popup.url
            if pdf_url and pdf_url != "about:blank":
                response = await self.page.request.get(pdf_url)
                content = await response.body()
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                await popup.close()
                return True
            print(f"        Popup opened but URL was {pdf_url!r} | link={link_text!r} href={link_href!r}")
            await popup.close()
        except Exception as e:
            print(f"        Popup strategy failed: {e} | link={link_text!r} href={link_href!r}")

        # Strategy 2: expect_download — direct download
        try:
            async with self.page.expect_download(timeout=15000) as dl_info:
                await link.click()
            dl = await dl_info.value
            await dl.save_as(str(target))
            return True
        except Exception as e:
            print(f"        Download strategy failed: {e}")

        # Strategy 3: get href and fetch directly
        try:
            href = link_href or await link.get_attribute("href")
            if href:
                if href.startswith("/"):
                    href = "https://dashboard.m1.com" + href
                response = await self.page.request.get(href)
                content = await response.body()
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                return True
            else:
                print(f"        Direct fetch: no href found on link | link={link_text!r}")
        except Exception as e:
            print(f"        Direct fetch failed: {e} | href={link_href!r}")

        print(f"        All download strategies failed for link={link_text!r} href={link_href!r}")
        return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _record(
        self, account: AccountInfo, date_str: str, filename: str, file_path: Path
    ) -> None:
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

    @staticmethod
    def _sha256(file_path: Path) -> str:
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

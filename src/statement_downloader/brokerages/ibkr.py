"""Interactive Brokers (IBKR) brokerage module.

Login: https://www.interactivebrokers.com/sso/Login
Statements: https://portal.interactivebrokers.com/AccountManagement/AmAuthentication?action=Statements

Flow:
1. Navigate to statements page (direct URL).
2. Click the right-arrow on the "Activity Statement" row.
3. In the popup: change Period to Monthly, then cycle through all Date options,
   clicking "Download PDF" for each.
"""

import asyncio
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
    """Parse a date dropdown option text to YYYY-MM.

    Handles formats like:
    - "January 2025", "Feb 2024"
    - "01/2025", "1/2025"
    - "2025-01"
    """
    text = text.strip().lower()

    # Try month name + year
    for name, num in MONTH_MAP.items():
        if name in text:
            year_match = re.search(r"(\d{4})", text)
            if year_match:
                return f"{year_match.group(1)}-{num}"

    # Try MM/YYYY or M/YYYY
    m = re.match(r"(\d{1,2})/(\d{4})", text)
    if m:
        return f"{m.group(2)}-{m.group(1).zfill(2)}"

    # Try YYYY-MM
    m = re.match(r"(\d{4})-(\d{2})", text)
    if m:
        return f"{m.group(1)}-{m.group(2)}"

    # Try MM/DD/YYYY — use month only
    m = re.search(r"(\d{1,2})/\d{1,2}/(\d{4})", text)
    if m:
        return f"{m.group(2)}-{m.group(1).zfill(2)}"

    return None


class IBKRBrokerage(BaseBrokerage):
    """Interactive Brokers statement downloader."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._failed_downloads: list[str] = []

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------

    async def _is_logged_in(self) -> bool:
        url = self.page.url.lower()
        if "/sso/login" in url:
            return False
        return "portal.interactivebrokers.com/portal" in url or \
               "portal.interactivebrokers.com/accountmanagement" in url

    async def _wait_for_login(self) -> None:
        print(f"\n{'=' * 60}")
        print(f"  Please log in to {self.config.display_name}")
        print(f"  Complete any 2FA prompts in the browser window.")
        print(f"  Script will auto-continue when login is detected...")
        print(f"{'=' * 60}\n")
        try:
            await self.page.wait_for_url(
                lambda url: "portal.interactivebrokers.com" in url.lower()
                and "/sso/login" not in url.lower(),
                timeout=300000,
            )
            print("  Login detected! Continuing...")
            await self.page.wait_for_timeout(3000)
        except Exception:
            if await self._is_logged_in():
                print("  Login detected! Continuing...")
            else:
                print("  WARNING: Could not detect login. Proceeding anyway...")

    # ------------------------------------------------------------------
    # Accounts — single synthetic account
    # ------------------------------------------------------------------

    async def _get_accounts(self) -> list[AccountInfo]:
        return [AccountInfo("IBKR", "IB00", "ibkr")]

    # ------------------------------------------------------------------
    # Dispatch — override _process_account
    # ------------------------------------------------------------------

    async def _process_account(self, account: AccountInfo) -> int:
        return await self._process_ibkr_statements()

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

    async def _process_ibkr_statements(self) -> int:
        """Navigate to statements, open Activity Statement popup, download all monthly PDFs."""
        print("    IBKR: navigating to statements page...")
        await self.page.goto(self.config.statements_url, wait_until="domcontentloaded")
        await self.page.wait_for_timeout(5000)

        # Dismiss any notification modals (e.g. "Bid, Ask, and Last Size Display Update")
        await self._dismiss_notification_modals()

        # Check if we landed on the statements page
        if not await self._is_on_statements_page():
            print("    IBKR: direct navigation failed, trying navbar fallback...")
            await self._navigate_via_navbar()
            await self.page.wait_for_timeout(3000)

        # Find and click the "Activity Statement" row's right-arrow button
        if not await self._open_activity_statement_popup():
            print("    IBKR: could not open Activity Statement popup")
            await self._debug_popup()
            return 0

        await self.page.wait_for_timeout(3000)

        # Verify the popup/modal actually appeared
        popup_visible = await self._verify_popup_open()
        if not popup_visible:
            print("    IBKR: Activity Statement arrow was clicked but popup not detected")
            await self._debug_popup()
            return 0

        # Change Period to Monthly (scoped to #amModalBody)
        if not await self._select_period_monthly():
            print("    IBKR: could not change period to Monthly")
            await self._debug_popup()
            return 0

        await self.page.wait_for_timeout(3000)

        # Read available monthly dates from the Date field
        date_values = await self._read_date_values()
        if not date_values:
            print("    IBKR: no date values found in modal — running diagnostics...")
            await self._debug_popup()
            return 0

        print(f"    IBKR: found {len(date_values)} date(s)")

        total_downloaded = 0
        skipped = 0
        acct_label = "ibkr"
        acct_info = AccountInfo("IBKR", "IB00", "ibkr")

        for dv in date_values:
            date_str = dv["parsed"]  # YYYY-MM format
            raw_value = dv["value"]  # raw value to set in the field

            if self.tracker.is_downloaded(self.config.slug, acct_label, date_str):
                skipped += 1
                continue

            # Re-open the modal if it was closed by a previous download
            if not await self._ensure_modal_open():
                print("    IBKR: could not re-open modal, stopping")
                break

            # Set the date value
            if not await self._set_date_value(dv):
                print(f"      Could not set date {raw_value!r}, skipping")
                continue

            await self.page.wait_for_timeout(1000)

            # Download PDF
            filename = f"{date_str}_{self.config.folder_name}_{acct_label}.pdf"
            target = self.output_dir / filename
            self.output_dir.mkdir(parents=True, exist_ok=True)

            success = await self._click_download_pdf(target)

            if success and target.exists() and target.stat().st_size > 0:
                self._record(acct_info, date_str, filename, target)
                print(f"      Downloaded: {filename}")
                total_downloaded += 1
                await asyncio.sleep(DOWNLOAD_DELAY)
            else:
                size = target.stat().st_size if target.exists() else 0
                reason = "empty file" if target.exists() else "file not created"
                self._failed_downloads.append(f"{filename} ({reason}, size={size})")
                if target.exists():
                    target.unlink()
                print(f"      FAILED: {filename} ({reason}, size={size})")

        if skipped:
            print(f"    IBKR: {skipped} statement(s) already downloaded")
        print(f"    IBKR: {total_downloaded} total new statement(s)")
        return total_downloaded

    # ------------------------------------------------------------------
    # Navigation helpers
    # ------------------------------------------------------------------

    async def _is_on_statements_page(self) -> bool:
        """Check if the current page shows the statements list."""
        try:
            # Look for text indicating we're on the statements page
            body_text = await self.page.inner_text("body", timeout=5000)
            return "activity statement" in body_text.lower()
        except Exception:
            return False

    async def _navigate_via_navbar(self) -> None:
        """Fallback: click Performance & Reports → Statements in the navbar."""
        try:
            # Click "Performance & Reports" in the navbar
            perf_link = self.page.locator(
                "a, button, [role='menuitem'], li"
            ).filter(has_text=re.compile(r"Performance\s*&?\s*Reports", re.IGNORECASE)).first
            if await perf_link.is_visible(timeout=5000):
                await perf_link.click()
                await self.page.wait_for_timeout(2000)

            # Click "Statements" in the dropdown
            stmt_link = self.page.locator(
                "a, button, [role='menuitem'], li"
            ).filter(has_text=re.compile(r"^Statements$", re.IGNORECASE)).first
            if await stmt_link.is_visible(timeout=5000):
                await stmt_link.click()
                await self.page.wait_for_timeout(3000)
        except Exception as e:
            print(f"    IBKR: navbar navigation failed: {e}")

    async def _dismiss_notification_modals(self) -> None:
        """Dismiss any IBKR notification popups (e.g. 'Bid, Ask, and Last Size Display Update')."""
        for _ in range(3):
            try:
                dismiss_btn = self.page.locator(
                    "button, a, [role='button']"
                ).filter(has_text=re.compile(r"^Dismiss$", re.IGNORECASE)).first
                if await dismiss_btn.is_visible(timeout=2000):
                    await dismiss_btn.click()
                    print("    IBKR: dismissed notification modal")
                    await self.page.wait_for_timeout(1000)
                else:
                    break
            except Exception:
                break

    # ------------------------------------------------------------------
    # Activity Statement popup
    # ------------------------------------------------------------------

    async def _open_activity_statement_popup(self) -> bool:
        """Click the right-arrow (Run) button on the Activity Statement row.

        DOM structure (AngularJS):
          div.row[ng-repeat="statement in ctrl.statementTypes"]
            div.col > p > strong "Activity Statement"
            div.col > p > span.btn-group-right > a.btn-icon[aria-label="Run"]
              > i.fa.fa-circle-arrow-right

        The ng-click calls ctrl.openOptionsModal(statement.key, 'DEFAULT_STATEMENT')
        which opens the #amModal Bootstrap modal.
        """
        # Strategy 1: Find the first row containing "Activity Statement" and
        # click its a[aria-label="Run"] button.
        try:
            # The rows are div.row elements with ng-repeat inside .form-bordered
            activity_rows = self.page.locator(
                ".form-bordered > div.row"
            ).filter(has_text="Activity Statement")
            count = await activity_rows.count()
            if count > 0:
                row = activity_rows.first
                run_btn = row.locator("a[aria-label='Run']").first
                if await run_btn.is_visible(timeout=3000):
                    await run_btn.click()
                    print("    IBKR: clicked Activity Statement 'Run' button (Strategy 1)")
                    return True
        except Exception as e:
            print(f"    IBKR: Strategy 1 error: {e}")

        # Strategy 2: Click i.fa-circle-arrow-right inside an Activity Statement row
        try:
            activity_rows = self.page.locator("div.row").filter(
                has_text="Activity Statement"
            )
            count = await activity_rows.count()
            if count > 0:
                arrow_icon = activity_rows.first.locator("i.fa-circle-arrow-right").first
                if await arrow_icon.is_visible(timeout=3000):
                    await arrow_icon.click()
                    print("    IBKR: clicked Activity Statement arrow icon (Strategy 2)")
                    return True
        except Exception as e:
            print(f"    IBKR: Strategy 2 error: {e}")

        # Strategy 3: JS — call ctrl.openOptionsModal directly via ng-click on
        # the first a[aria-label="Run"] in the Activity Statement row
        try:
            clicked = await self.page.evaluate("""() => {
                const rows = document.querySelectorAll('.form-bordered > div.row');
                for (const row of rows) {
                    const strong = row.querySelector('strong');
                    if (strong && /Activity\\s+Statement/i.test(strong.textContent)) {
                        const runBtn = row.querySelector('a[aria-label="Run"]');
                        if (runBtn) {
                            runBtn.click();
                            return true;
                        }
                    }
                }
                return false;
            }""")
            if clicked:
                print("    IBKR: clicked Activity Statement run button (Strategy 3 JS)")
                return True
        except Exception as e:
            print(f"    IBKR: Strategy 3 error: {e}")

        # Strategy 4: Broadest — find any a.btn-icon with aria-label="Run"
        # that's near Activity Statement text
        try:
            run_buttons = self.page.locator("a.btn-icon[aria-label='Run']")
            count = await run_buttons.count()
            if count > 0:
                # First Run button should be Activity Statement (it's the first row)
                await run_buttons.first.click()
                print("    IBKR: clicked first 'Run' button on page (Strategy 4)")
                return True
        except Exception as e:
            print(f"    IBKR: Strategy 4 error: {e}")

        return False

    # ------------------------------------------------------------------
    # Modal lifecycle
    # ------------------------------------------------------------------

    async def _ensure_modal_open(self) -> bool:
        """Check if the modal is open; if not, re-open it and select Monthly.

        The modal closes after each PDF download. This method re-opens it
        so the next date can be selected.
        """
        # Check if modal is already visible
        try:
            modal = self.page.locator("#amModal")
            if await modal.is_visible(timeout=1000):
                # Modal still open — check if Period is Monthly
                modal_body = self.page.locator("#amModalBody")
                body_text = await modal_body.inner_text(timeout=2000)
                if "monthly" in body_text.lower() or "Monthly" in body_text:
                    return True
                # Period may have reset — re-select Monthly
                await self._select_period_monthly()
                await self.page.wait_for_timeout(2000)
                return True
        except Exception:
            pass

        # Modal is closed — re-open it
        if not await self._open_activity_statement_popup():
            print("    IBKR: failed to re-open Activity Statement popup")
            return False

        await self.page.wait_for_timeout(3000)

        if not await self._verify_popup_open():
            print("    IBKR: modal did not appear after re-opening")
            return False

        # Re-select Monthly period
        if not await self._select_period_monthly():
            print("    IBKR: failed to re-select Monthly after re-opening modal")
            return False

        await self.page.wait_for_timeout(2000)
        return True

    # ------------------------------------------------------------------
    # Period dropdown (scoped to #amModalBody)
    # ------------------------------------------------------------------

    async def _select_period_monthly(self) -> bool:
        """Change the Period dropdown from Daily to Monthly inside the modal.

        The modal has a native <select> with options:
        Daily, Custom Date Range, Monthly, Annual, Month to Date, Year to Date.
        """
        modal = self.page.locator("#amModalBody")

        # Strategy 1: native <select> inside modal
        try:
            selects = modal.locator("select")
            count = await selects.count()
            for i in range(count):
                sel = selects.nth(i)
                options = await sel.locator("option").all_inner_texts()
                options_lower = [o.strip().lower() for o in options]
                if "monthly" in options_lower:
                    await sel.select_option(label="Monthly")
                    print("    IBKR: selected Monthly period")
                    return True
        except Exception as e:
            print(f"    IBKR: Period select Strategy 1 error: {e}")

        # Strategy 2: JS — find select with Monthly option in modal and select it
        try:
            result = await self.page.evaluate("""() => {
                const modal = document.getElementById('amModalBody');
                if (!modal) return 'no_modal';
                const selects = modal.querySelectorAll('select');
                for (const sel of selects) {
                    for (const opt of sel.options) {
                        if (opt.text.trim().toLowerCase() === 'monthly') {
                            sel.value = opt.value;
                            sel.dispatchEvent(new Event('change', {bubbles: true}));
                            return 'ok';
                        }
                    }
                }
                return 'no_monthly_option';
            }""")
            if result == "ok":
                print("    IBKR: selected Monthly period (JS)")
                return True
            print(f"    IBKR: Period JS result: {result}")
        except Exception as e:
            print(f"    IBKR: Period select Strategy 2 error: {e}")

        # Check if Monthly is already selected
        try:
            selects = modal.locator("select")
            count = await selects.count()
            for i in range(count):
                sel = selects.nth(i)
                current = await sel.input_value()
                if "monthly" in current.lower():
                    print("    IBKR: Monthly already selected")
                    return True
        except Exception:
            pass

        return False

    # ------------------------------------------------------------------
    # Date field (scoped to #amModalBody)
    # ------------------------------------------------------------------

    async def _read_date_values(self) -> list[dict]:
        """Read available monthly date values from the modal.

        After selecting Monthly, the Date field is an <input> showing a date
        like "2026-02-27". For monthly statements, IBKR generates one per month.
        We need to discover what months are available.

        Strategy: The date field may be a <select> (after period change) or an
        <input type="date">. We also check for a date picker widget.

        Returns [{"parsed": "YYYY-MM", "value": "YYYY-MM-DD"}, ...]
        """
        modal = self.page.locator("#amModalBody")

        # Strategy 1: If Date became a <select> after changing period to Monthly
        try:
            selects = modal.locator("select")
            count = await selects.count()
            for i in range(count):
                sel = selects.nth(i)
                # Skip the Period select (has Monthly/Daily etc.)
                options = await sel.locator("option").all_inner_texts()
                options_lower = [o.strip().lower() for o in options]
                if "monthly" in options_lower or "daily" in options_lower:
                    continue
                # This might be the date select
                if len(options) >= 1:
                    result = []
                    option_els = await sel.locator("option").all()
                    for opt_el in option_els:
                        text = (await opt_el.inner_text()).strip()
                        value = await opt_el.get_attribute("value") or text
                        parsed = _parse_date(text) or _parse_date(value)
                        if parsed:
                            result.append({
                                "parsed": parsed,
                                "value": value,
                                "label": text,
                                "type": "select",
                                "select_index": i,
                            })
                    if result:
                        print(f"    IBKR: found {len(result)} date(s) in <select>")
                        return result
        except Exception as e:
            print(f"    IBKR: Date select scan error: {e}")

        # Strategy 2: <input> date field — read current value and generate
        # monthly dates going back in time
        try:
            date_input = modal.locator("input[type='date'], input[type='text']")
            count = await date_input.count()
            for i in range(count):
                inp = date_input.nth(i)
                value = await inp.input_value()
                if not value:
                    continue
                # Check if it looks like a date (YYYY-MM-DD)
                m = re.match(r"(\d{4})-(\d{2})-(\d{2})", value)
                if not m:
                    continue
                print(f"    IBKR: Date input found with value: {value}")

                # For an input date field, we generate monthly dates.
                # We'll go back up to 24 months from the current date.
                # Each month we set the input to the last day of that month
                # and try downloading. IBKR will produce a statement if available.
                import datetime
                year, month = int(m.group(1)), int(m.group(2))
                result = []
                for offset in range(24):
                    y = year
                    mo = month - offset
                    while mo <= 0:
                        mo += 12
                        y -= 1
                    # Use last day of month as the date value
                    if mo == 12:
                        last_day = 31
                    else:
                        next_month = datetime.date(y, mo + 1, 1)
                        last_day = (next_month - datetime.timedelta(days=1)).day
                    date_val = f"{y}-{mo:02d}-{last_day:02d}"
                    parsed = f"{y}-{mo:02d}"
                    result.append({
                        "parsed": parsed,
                        "value": date_val,
                        "label": date_val,
                        "type": "input",
                        "input_index": i,
                    })
                return result
        except Exception as e:
            print(f"    IBKR: Date input scan error: {e}")

        # Strategy 3: JS dump of all form elements for debugging
        try:
            info = await self.page.evaluate("""() => {
                const modal = document.getElementById('amModalBody');
                if (!modal) return 'no_modal';
                const inputs = modal.querySelectorAll('input, select, textarea');
                const result = [];
                for (const el of inputs) {
                    result.push({
                        tag: el.tagName,
                        type: el.type || '',
                        name: el.name || '',
                        id: el.id || '',
                        value: (el.value || '').slice(0, 100),
                        className: (el.className || '').slice(0, 80),
                    });
                }
                return result;
            }""")
            print(f"    IBKR: Modal form elements: {info}")
        except Exception:
            pass

        return []

    async def _set_date_value(self, dv: dict) -> bool:
        """Set the date value in the modal's Date field."""
        modal = self.page.locator("#amModalBody")
        value = dv["value"]

        if dv["type"] == "select":
            # Native <select>
            try:
                sel = modal.locator("select").nth(dv["select_index"])
                await sel.select_option(value=value)
                return True
            except Exception:
                pass
            try:
                sel = modal.locator("select").nth(dv["select_index"])
                await sel.select_option(label=dv["label"])
                return True
            except Exception:
                return False

        if dv["type"] == "input":
            # <input> date field — clear and type the new value
            try:
                inp = modal.locator("input[type='date'], input[type='text']").nth(
                    dv.get("input_index", 0)
                )
                # For date inputs, fill() sets the value directly
                await inp.fill(value)
                # Dispatch change event so AngularJS picks it up
                await inp.dispatch_event("change")
                await inp.dispatch_event("input")
                return True
            except Exception as e:
                print(f"      Date input fill error: {e}")
                pass
            # JS fallback
            try:
                idx = dv.get("input_index", 0)
                await self.page.evaluate("""([idx, val]) => {
                    const modal = document.getElementById('amModalBody');
                    const inputs = modal.querySelectorAll('input[type="date"], input[type="text"]');
                    const inp = inputs[idx];
                    if (!inp) return;
                    // Use native setter to bypass framework interception
                    const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value'
                    ).set;
                    nativeInputValueSetter.call(inp, val);
                    inp.dispatchEvent(new Event('input', {bubbles: true}));
                    inp.dispatchEvent(new Event('change', {bubbles: true}));
                }""", [idx, value])
                return True
            except Exception as e:
                print(f"      Date input JS fill error: {e}")
                return False

        return False

    # ------------------------------------------------------------------
    # Download PDF
    # ------------------------------------------------------------------

    async def _click_download_pdf(self, target: Path) -> bool:
        """Click the 'Download PDF' button and capture the download."""
        # Strategy 1: expect_download with button click
        try:
            btn = self.page.locator(
                "button, a, [role='button'], input[type='submit']"
            ).filter(has_text=re.compile(r"Download\s*PDF", re.IGNORECASE)).first

            if await btn.is_visible(timeout=5000):
                async with self.page.expect_download(timeout=30000) as dl_info:
                    await btn.click()
                dl = await dl_info.value
                target.parent.mkdir(parents=True, exist_ok=True)
                await dl.save_as(str(target))
                return True
        except Exception as e:
            print(f"        Download via button click failed: {e}")

        # Strategy 2: expect_popup (PDF opens in new tab)
        try:
            btn = self.page.locator(
                "button, a, [role='button']"
            ).filter(has_text=re.compile(r"Download\s*PDF", re.IGNORECASE)).first

            if await btn.is_visible(timeout=2000):
                async with self.page.expect_popup(timeout=15000) as popup_info:
                    await btn.click()
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
                await popup.close()
        except Exception as e:
            print(f"        Popup strategy failed: {e}")

        # Strategy 3: JS click on any "Download PDF" element
        try:
            clicked = await self.page.evaluate("""() => {
                const els = document.querySelectorAll('button, a, [role="button"], input, span');
                for (const el of els) {
                    const text = (el.innerText || el.textContent || el.value || '').trim();
                    if (/download\\s*pdf/i.test(text) && el.offsetWidth > 0) {
                        el.click();
                        return true;
                    }
                }
                return false;
            }""")
            if clicked:
                # Wait a bit and check if a download was triggered
                await self.page.wait_for_timeout(3000)
                # The file may have been downloaded to the default location
                # This is a best-effort fallback
                print("        JS click on Download PDF (no download capture)")
        except Exception:
            pass

        return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _verify_popup_open(self) -> bool:
        """Check if the #amModal Bootstrap modal appeared after clicking Run.

        The modal starts as `#amModal.modal.fade` with `display: hidden`.
        When opened, Bootstrap adds class `show` or `in` and sets `display: block`.
        """
        # Wait for the modal to become visible (Bootstrap transition)
        try:
            modal = self.page.locator("#amModal")
            await modal.wait_for(state="visible", timeout=8000)
            print("    IBKR: modal #amModal is visible")
            return True
        except Exception:
            pass

        # Fallback: check if modal body has content (AngularJS may populate it)
        try:
            modal_body = self.page.locator("#amModalBody")
            body_text = await modal_body.inner_text(timeout=3000)
            if body_text.strip():
                print("    IBKR: modal body has content")
                return True
        except Exception:
            pass

        # Fallback: check for any visible .modal element
        try:
            visible_modal = self.page.locator(".modal.show, .modal.in, .modal[style*='display: block']").first
            if await visible_modal.is_visible(timeout=2000):
                return True
        except Exception:
            pass

        # Check if select elements appeared (the modal has Period/Date dropdowns)
        try:
            select_count = await self.page.locator("select").count()
            if select_count >= 1:
                print(f"    IBKR: found {select_count} select element(s), assuming modal is open")
                return True
        except Exception:
            pass

        return False

    async def _debug_popup(self) -> None:
        """Print diagnostic info about the current page state."""
        try:
            # Take a screenshot for visual debugging
            screenshot_path = self.output_dir / "_debug_ibkr_popup.png"
            self.output_dir.mkdir(parents=True, exist_ok=True)
            await self.page.screenshot(path=str(screenshot_path))
            print(f"      DEBUG: screenshot saved to {screenshot_path}")
        except Exception as e:
            print(f"      DEBUG: screenshot failed: {e}")

        try:
            info = await self.page.evaluate(r"""() => {
                const parts = [];
                parts.push('url=' + location.href);

                // Count selects and their options
                const selects = document.querySelectorAll('select');
                parts.push('selects=' + selects.length);
                for (let i = 0; i < Math.min(selects.length, 5); i++) {
                    const opts = Array.from(selects[i].options).map(o => o.text.trim()).join('|');
                    parts.push('select[' + i + ']=' + opts.slice(0, 200));
                }

                // Look for modals/popups/dialogs
                const modals = document.querySelectorAll(
                    '[role="dialog"], .modal, .popup, .overlay, [class*="modal"], [class*="popup"], [class*="dialog"], dialog'
                );
                parts.push('modals=' + modals.length);
                for (let i = 0; i < Math.min(modals.length, 3); i++) {
                    const text = (modals[i].innerText || '').slice(0, 300);
                    parts.push('modal[' + i + ']=' + text.replace(/\s+/g, ' '));
                }

                // Look for iframes
                const iframes = document.querySelectorAll('iframe');
                parts.push('iframes=' + iframes.length);
                for (let i = 0; i < Math.min(iframes.length, 5); i++) {
                    parts.push('iframe[' + i + '].src=' + (iframes[i].src || '').slice(0, 150));
                }

                // Check for "Download PDF" button
                const allEls = document.querySelectorAll('button, a, [role="button"]');
                const downloadBtns = [];
                for (const el of allEls) {
                    const t = (el.innerText || el.textContent || '').trim();
                    if (/download|pdf|period|monthly|daily|date/i.test(t) && t.length < 60) {
                        downloadBtns.push(t.replace(/\s+/g, ' '));
                    }
                }
                parts.push('relevant_btns=' + JSON.stringify(downloadBtns.slice(0, 10)));

                // Visible body text snippet
                const body = (document.body?.innerText || '').replace(/\s+/g, ' ');
                parts.push('body_snippet=' + body.slice(0, 500));

                return parts.join('\n  ');
            }""")
            print(f"      DEBUG:\n  {info}")
        except Exception as e:
            print(f"      DEBUG: evaluate failed: {e}")

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

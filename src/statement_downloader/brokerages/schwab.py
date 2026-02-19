"""Charles Schwab brokerage module.

Navigates Schwab's client portal to discover accounts and download
monthly statements. Serves as the reference template for other modules.

Schwab statements URL: https://client.schwab.com/app/accounts/statements/

TODO: Consider using page.evaluate() to dump page HTML and analyze element
structure (like the Webull module does) for more robust element detection.
"""

import asyncio
import hashlib
import re
from pathlib import Path

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
    """Charles Schwab statement downloader."""

    async def _wait_for_login(self) -> None:
        """Wait for user to log in by watching for URL change."""
        print(f"\n{'=' * 60}")
        print(f"  Please log in to {self.config.display_name}")
        print(f"  Complete any 2FA prompts in the browser window.")
        print(f"  Script will auto-continue when login is detected...")
        print(f"{'=' * 60}\n")

        try:
            await self.page.wait_for_url(
                "**/app/accounts/summary/**",
                timeout=300000,
            )
            print("  ✓ Login detected! Continuing...")
            await self.page.wait_for_timeout(2000)
        except Exception:
            url = self.page.url.lower()
            if "client.schwab.com" in url and "login" not in url:
                print("  ✓ Login detected! Continuing...")
                await self.page.wait_for_timeout(2000)
            else:
                print("  WARNING: Could not detect successful login")

    async def _is_logged_in(self) -> bool:
        """Check if already logged in."""
        await self.page.wait_for_timeout(1000)

        url = self.page.url.lower()

        if "sessiontimeout=y" in url or "/areas/access/login" in url or "/login" in url or url.endswith("/client-home"):
            return False

        if "client.schwab.com" in url and "login" not in url:
            print("  ✓ Logged in successfully")
            return True

        try:
            logged_in = await self.page.locator(
                "text=/Log Out/i, "
                "text=/Sign Out/i"
            ).first.is_visible(timeout=2000)
            if logged_in:
                print("  ✓ Logged in successfully")
                return True
        except Exception:
            pass

        return False

    async def _get_accounts(self) -> list[AccountInfo]:
        """Detect accounts from Schwab's account selector dropdown."""
        accounts = []

        await self.page.goto(
            self.config.statements_url,
            wait_until="domcontentloaded",
        )
        await self.page.wait_for_timeout(3000)

        await self.page.evaluate("document.body.style.zoom = '0.75'")
        await self.page.wait_for_timeout(2000)

        try:
            account_dropdown = self.page.locator("#account-selector").first
            if await account_dropdown.is_visible(timeout=5000):
                await account_dropdown.click()
                await self.page.wait_for_timeout(2000)

                account_items = await self.page.locator("#account-selector-list ul li").all()

                for item in account_items:
                    try:
                        item_text = (await item.inner_text()).strip()
                        item_text_clean = re.sub(r"\s+", " ", item_text).strip()

                        lower_text = item_text_clean.lower()
                        if any(skip in lower_text for skip in [
                            "all brokerage", "show all", "closed and inactive",
                        ]):
                            continue

                        # Extract last 3-4 digits from "Account ending in X Y Z" or "…799"
                        ending_match = re.search(r"Account ending in\s+([\d\s]+)$", item_text_clean, re.IGNORECASE)
                        if ending_match:
                            last_digits = ending_match.group(1).replace(" ", "")
                        else:
                            digits_match = re.search(r"[….\u2026]{1,}\s*(\d{3,4})", item_text_clean)
                            last_digits = digits_match.group(1) if digits_match else None

                        if last_digits:
                            # Extract account name (appears twice, take first occurrence)
                            ellipsis_match = re.search(r"^(.+?)\s+\1", item_text_clean)
                            if ellipsis_match:
                                acct_name = ellipsis_match.group(1).strip()
                            else:
                                name_match = re.search(r"^(.+?)\s*[….\u2026]", item_text_clean)
                                acct_name = name_match.group(1).strip() if name_match else item_text_clean.split()[0]

                            last4 = last_digits.zfill(4)

                            label = self.make_account_label(acct_name, last4)
                            accounts.append(AccountInfo(
                                account_type=acct_name,
                                account_last4=last4,
                                label=label,
                            ))
                            print(f"        Found account: {acct_name} ...{last_digits} → {label}")
                    except Exception as e:
                        print(f"        Could not parse account item: {e}")

                await account_dropdown.click()
                await self.page.wait_for_timeout(1000)

        except Exception as e:
            print(f"      WARNING: Could not parse accounts from dropdown: {e}")

        if not accounts:
            print(f"      ERROR: No accounts found in dropdown. Cannot proceed.")

        return accounts

    async def _navigate_to_statements(self, account: AccountInfo) -> None:
        """Configure the statements page filters for the given account."""
        await self.page.goto(
            self.config.statements_url,
            wait_until="domcontentloaded",
        )

        await self.page.wait_for_timeout(5000)
        try:
            await self.page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass

        # Verify we're on the statements page
        current_url = self.page.url
        if "statements" not in current_url.lower():
            print(f"      WARNING: Not on statements page, trying direct navigation again...")
            await self.page.goto(
                "https://client.schwab.com/app/accounts/statements/#/",
                wait_until="domcontentloaded",
            )
            await self.page.wait_for_timeout(5000)

        # Zoom out to 75% so dropdowns and options are fully visible
        await self.page.evaluate("document.body.style.zoom = '0.75'")
        await self.page.wait_for_timeout(2000)

        # Step 1: Select the specific account from the dropdown
        try:
            await self.page.wait_for_timeout(2000)

            account_dropdown = self.page.locator("#account-selector").first

            if await account_dropdown.is_visible(timeout=5000):
                await account_dropdown.scroll_into_view_if_needed()
                await self.page.wait_for_timeout(500)
                await account_dropdown.click()
                await self.page.wait_for_timeout(2000)

                account_list = self.page.locator("#account-selector-list").first

                if await account_list.is_visible(timeout=3000):
                    last3 = account.account_last4[-3:]
                    last3_spaced = " ".join(last3)
                    account_items = await account_list.locator("ul li").all()

                    found = False
                    for item in account_items:
                        try:
                            item_text = await item.inner_text()
                            if (f"...{last3}" in item_text
                                    or f"•••{last3}" in item_text
                                    or f"…{last3}" in item_text
                                    or f"\u2026{last3}" in item_text
                                    or last3_spaced in item_text):
                                await item.click()
                                await self.page.wait_for_timeout(3000)
                                print(f"        ✓ Selected: {account.account_type} ...{last3}")
                                found = True
                                break
                        except Exception:
                            continue

                    if not found:
                        print(f"        WARNING: Could not find account ...{last3} in dropdown")
        except Exception as e:
            print(f"      WARNING: Could not select account: {e}")

        # Step 2: Set Date Range to "Last 10 years"
        try:
            date_range_select = self.page.locator("#date-range-select-id").first

            if await date_range_select.is_visible(timeout=5000):
                await date_range_select.scroll_into_view_if_needed()
                await self.page.wait_for_timeout(500)

                try:
                    await date_range_select.select_option(label="Last 10 years")
                    await self.page.wait_for_timeout(1000)
                    print(f"        ✓ Set date range: Last 10 years")
                except Exception:
                    await date_range_select.select_option(value="last10years")
                    await self.page.wait_for_timeout(1000)
                    print(f"        ✓ Set date range: Last 10 years")
        except Exception as e:
            print(f"      WARNING: Could not set date range: {e}")

        # Step 3: Toggle document type filters — only "Statements" should be checked
        try:
            chip_buttons = self.page.locator("#chip-buttons").first

            if await chip_buttons.is_visible(timeout=3000):
                types_to_uncheck = ["Tax Forms", "Letters", "Reports & Plans", "Trade Confirms"]

                for doc_type in types_to_uncheck:
                    try:
                        button = chip_buttons.get_by_text(doc_type, exact=False).first
                        if await button.is_visible(timeout=1000):
                            aria_pressed = await button.get_attribute("aria-pressed")
                            class_name = await button.get_attribute("class") or ""
                            is_active = aria_pressed == "true" or "active" in class_name.lower() or "selected" in class_name.lower()

                            if is_active:
                                await button.click(force=True)
                                await self.page.wait_for_timeout(500)
                    except Exception:
                        pass

                # Ensure "Statements" is checked
                try:
                    statements_button = chip_buttons.get_by_text("Statements", exact=False).first
                    if await statements_button.is_visible(timeout=2000):
                        aria_pressed = await statements_button.get_attribute("aria-pressed")
                        class_name = await statements_button.get_attribute("class") or ""
                        is_active = aria_pressed == "true" or "active" in class_name.lower() or "selected" in class_name.lower()

                        if not is_active:
                            await statements_button.click(force=True)
                            await self.page.wait_for_timeout(500)
                            print(f"        ✓ Checked 'Statements'")
                except Exception:
                    pass
        except Exception:
            pass

        # Step 4: Click "Search" button to apply filters
        try:
            search_button = self.page.locator("button:has-text('Search')").first
            if await search_button.is_visible(timeout=3000):
                await search_button.scroll_into_view_if_needed()
                await self.page.wait_for_timeout(500)
                await search_button.click()
                await self.page.wait_for_timeout(3000)
                print(f"        ✓ Clicked 'Search'")
        except Exception as e:
            print(f"      WARNING: Could not click 'Search' button: {e}")

        # Scroll down to view statements, then back up slightly
        await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await self.page.wait_for_timeout(2000)
        await self.page.evaluate("window.scrollBy(0, -200)")
        await self.page.wait_for_timeout(1000)

        # Wait for the statements table to appear
        try:
            await self.page.wait_for_selector(
                "table, [role='table'], tbody tr, [class*='statement'], [class*='document']",
                timeout=10000
            )
        except Exception:
            print(f"      WARNING: Statements table did not appear within 10 seconds")

    async def _process_account(self, account: AccountInfo) -> int:
        """Override to handle page-by-page processing for Schwab's pagination."""
        await self._navigate_to_statements(account)

        # Verify the correct account was selected
        try:
            selector_text = await self.page.locator("#account-selector").first.inner_text()
            selector_clean = re.sub(r"\s+", " ", selector_text).strip()
            last3 = account.account_last4[-3:]
            if last3 not in selector_clean.replace(" ", ""):
                print(f"      WARNING: Account selector shows '{selector_clean}' but expected ...{last3}")
                print(f"      Skipping this account to avoid downloading wrong statements")
                return 0
        except Exception:
            pass

        total_downloaded = 0
        page_num = 1

        while True:
            print(f"      Processing page {page_num}...")

            statements_on_page = await self._parse_statements_on_current_page(account)

            if statements_on_page:
                downloaded_dates = self.tracker.get_downloaded_dates(
                    self.config.slug, account.label
                )
                needed = [s for s in statements_on_page if s.date not in downloaded_dates]
                needed.sort(key=lambda s: s.date)

                if needed:
                    known_hashes = self.tracker.get_all_hashes(self.config.slug)

                    print(f"        Downloading {len(needed)} new statement(s) on page {page_num}")
                    for stmt in needed:
                        file_path = await self._download_and_save(stmt)
                        if file_path and file_path.exists():
                            h = hashlib.sha256()
                            with open(file_path, "rb") as f:
                                for chunk in iter(lambda: f.read(8192), b""):
                                    h.update(chunk)
                            file_hash = h.hexdigest()

                            if file_hash in known_hashes:
                                original = known_hashes[file_hash]
                                print(f"        DUPLICATE: {file_path.name} identical to {original}, deleting")
                                file_path.unlink()
                                total_downloaded -= 1
                            else:
                                known_hashes[file_hash] = file_path.name
                                total_downloaded += 1

                            if stmt is not needed[-1]:
                                await asyncio.sleep(2)
                else:
                    print(f"        All statements on page {page_num} already downloaded")

            # Try to click "Next" button
            try:
                next_button = self.page.locator(
                    "button:has-text('Next'), "
                    "a:has-text('Next'), "
                    "[aria-label='Next'], "
                    "button[aria-label='Go to next page']"
                ).first

                if await next_button.is_visible(timeout=2000):
                    if await next_button.is_disabled():
                        break

                    await next_button.scroll_into_view_if_needed()
                    await next_button.click()
                    await self.page.wait_for_timeout(3000)
                    page_num += 1
                else:
                    break
            except Exception:
                break

        return total_downloaded

    async def _get_available_statements(self, account: AccountInfo) -> list[StatementInfo]:
        """Parse statements from the current page only."""
        return await self._parse_statements_on_current_page(account)

    async def _parse_statements_on_current_page(self, account: AccountInfo) -> list[StatementInfo]:
        """Parse statement rows from the current page."""
        statements = []

        # Schwab uses a div-based layout — look for all "PDF" links
        all_pdf_links = await self.page.locator(
            "a:has-text('PDF'), "
            "button:has-text('PDF'), "
            "[role='button']:has-text('PDF'), "
            "a[aria-label*='PDF'], "
            "a[title*='PDF']"
        ).all()

        for pdf_link in all_pdf_links:
            row_text = await pdf_link.evaluate(
                "el => el.closest('div[class*=\"row\"], li, [role=\"row\"], div[class*=\"statement\"], div[class*=\"document\"]')?.textContent || el.parentElement?.textContent || ''"
            )

            date = _parse_statement_date(row_text)
            if not date:
                try:
                    nearby_text = await pdf_link.evaluate(
                        "el => { const row = el.closest('div, li'); return row ? Array.from(row.querySelectorAll('*')).map(e => e.textContent).join(' ') : ''; }"
                    )
                    date = _parse_statement_date(nearby_text)
                except Exception:
                    pass

            if not date:
                continue

            statements.append(StatementInfo(
                date=date,
                element=pdf_link,
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

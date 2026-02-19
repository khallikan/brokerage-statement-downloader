"""E*Trade (Morgan Stanley) brokerage module.

Documents URL: https://us.etrade.com/etx/pxy/accountdocs?inav=nav:documents#/documents

All accounts share a single documents view with quick-filter tabs and a
Timeframe dropdown.  The flow:

1. Navigate to the documents page.
2. Click the "Statements" quick-filter tab.
3. Read year options from the Timeframe dropdown.
4. For each year (newest first): select it, click Apply, parse every table page.
5. Only download rows whose Document column contains "Statement".
6. Stop after 2 consecutive years that yield zero new downloads.
"""

import asyncio
import hashlib
import re
from pathlib import Path

from ..base_brokerage import BaseBrokerage, AccountInfo, StatementInfo
from ..config import DOWNLOAD_DELAY


def _parse_date(text: str) -> str | None:
    """Parse 'MM/DD/YY' or 'MM/DD/YYYY' → 'YYYY-MM-DD'.

    Uses the full date (including day) so that two statements on different days
    within the same month (e.g. 09/30 and 09/29) get distinct tracker keys and
    filenames instead of both collapsing to the same 'YYYY-MM'.
    Returns None if month is not 1-12 or day is not 1-31.
    """
    parts = text.strip().split("/")
    if len(parts) != 3:
        return None
    month_s, day_s, year_s = parts[0], parts[1], parts[2]
    month = int(month_s)
    day = int(day_s)
    if not (1 <= month <= 12) or not (1 <= day <= 31):
        return None
    year = year_s if len(year_s) == 4 else "20" + year_s
    return f"{year}-{month_s.zfill(2)}-{day_s.zfill(2)}"


def _parse_account(text: str) -> tuple[str, str] | None:
    """Parse 'Individual Brokerage & ... 2658' → ('Individual Brokerage', '2658')."""
    text = re.sub(r"\s+", " ", text).strip()
    m = re.search(r"(\d{4})\s*$", text)
    if not m:
        return None
    last4 = m.group(1)
    prefix = text[: m.start()].strip()
    # Take everything before '&', '·', '•', or multiple dots
    prefix = re.split(r"\s*[&·•]\s*|\.{2,}", prefix)[0].strip(" -–—")
    return (prefix or "Account", last4)


class ETradeBrokerage(BaseBrokerage):
    """E*Trade statement downloader (single documents page, year iteration)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._failed_downloads: list[str] = []

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------

    async def _is_logged_in(self) -> bool:
        url = self.page.url.lower()
        if "etx/pxy/login" in url:
            return False
        return "etrade.com" in url and "login" not in url

    async def _wait_for_login(self) -> None:
        print(f"\n{'=' * 60}")
        print(f"  Please log in to {self.config.display_name}")
        print(f"  Complete any 2FA prompts in the browser window.")
        print(f"  Script will auto-continue when login is detected...")
        print(f"{'=' * 60}\n")
        try:
            await self.page.wait_for_url(
                lambda url: "etrade.com" in url.lower() and "login" not in url.lower(),
                timeout=300000,
            )
            print("  Login detected! Continuing...")
            await self.page.wait_for_timeout(3000)
        except Exception:
            url = self.page.url.lower()
            if "etrade.com" in url and "login" not in url:
                print("  Login detected! Continuing...")
            else:
                print("  WARNING: Could not detect login. Proceeding anyway...")

    # ------------------------------------------------------------------
    # Accounts — single synthetic account
    # ------------------------------------------------------------------

    async def _get_accounts(self) -> list[AccountInfo]:
        return [AccountInfo("E*Trade", "ET00", "etrade")]

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def _process_account(self, account: AccountInfo) -> int:
        return await self._process_etrade_statements()

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

    async def _process_etrade_statements(self) -> int:
        """Navigate to documents, iterate timeframe years, download all Statements."""
        print("    E*Trade: navigating to documents page...")
        await self.page.goto(self.config.statements_url, wait_until="domcontentloaded")
        await self.page.wait_for_timeout(5000)

        # Zoom out to avoid overlay / layout issues
        await self.page.evaluate("document.body.style.zoom = '0.75'")
        await self.page.wait_for_timeout(1000)

        # Activate the "Statements" quick-filter tab
        await self._activate_statements_filter()
        await self.page.wait_for_timeout(2000)

        # Read Timeframe dropdown options
        timeframes = await self._read_timeframe_options()
        if not timeframes:
            print("    E*Trade: no timeframe options found — parsing visible table")
            known_hashes = self.tracker.get_all_hashes(self.config.slug)
            new, _, _pf = await self._download_visible_statements(None, known_hashes)
            return new

        # Separate Year-To-Date entries from specific years
        ytd_list = [t for t in timeframes if re.search(r"year.to.date|ytd", t["label"], re.I)]
        year_list = [t for t in timeframes if re.match(r"^\d{4}$", t["label"].strip())]
        year_list.sort(key=lambda t: t["label"], reverse=True)  # newest first

        process_order = ytd_list + year_list
        useful_labels = [t["label"] for t in process_order]
        print(f"    E*Trade: timeframes: {', '.join(useful_labels)}")
        known_hashes = self.tracker.get_all_hashes(self.config.slug)
        total_downloaded = 0

        for tf in process_order:
            label = tf["label"].strip()
            is_ytd = bool(re.search(r"year.to.date|ytd", label, re.I))

            print(f"    E*Trade: selecting '{label}'...")
            if not await self._select_timeframe(tf):
                print(f"      Could not select '{label}', skipping")
                continue

            await self._click_apply()
            # Wait for table content to appear
            await self._wait_for_table_content()

            new, docs_found, parse_fail = await self._download_visible_statements(label, known_hashes)
            total_downloaded += new

            if is_ytd:
                already = docs_found - new
                print(f"      YTD: {new} new, {already} already downloaded")
            elif docs_found == 0:
                print(f"      '{label}': no documents on platform")
            else:
                already = docs_found - new - parse_fail
                if new > 0:
                    msg = f"      '{label}': {new} new"
                    if already > 0:
                        msg += f", {already} already downloaded"
                    print(msg)
                elif parse_fail == docs_found:
                    print(f"      '{label}': {docs_found} found but date extraction failed — check DEBUG output")
                else:
                    print(f"      '{label}': {already} already downloaded")

        print(f"    E*Trade: {total_downloaded} total new statement(s)")
        return total_downloaded

    async def _wait_for_table_content(self) -> None:
        """Wait after clicking Apply for the document table to populate."""
        # Give Angular/web components more time to render their shadow DOM content
        await self.page.wait_for_timeout(3000)
        # Playwright's locator pierces shadow DOM — try document-link selectors first,
        # then fall back to generic table selectors.
        combined = (
            "div[slot='pdfLinkData'], "
            "ms-documents-pdf-link-formatter a[role='link'], "
            "a.ms-link[role='link'], "
            "table tbody tr, "
            "[role='row'] [role='cell']"
        )
        try:
            await self.page.locator(combined).first.wait_for(state="visible", timeout=15000)
        except Exception:
            pass
        await self.page.wait_for_timeout(1000)

    async def _download_visible_statements(
        self, timeframe_label: str | None, known_hashes: dict
    ) -> tuple[int, int, int]:
        """Parse all pages of the current table and download Statement rows.

        Returns (new_downloads, total_statement_rows_found, total_parse_failures).
        total_statement_rows_found counts all rows whose Document column contains
        "Statement", regardless of whether they were already downloaded.
        total_parse_failures counts rows skipped because date could not be extracted.
        """
        total_new = 0
        total_docs_found = 0
        total_parse_failures = 0
        page_num = 0
        # Tracks how many unique-content files have been saved for each
        # (acct_label, YYYY-MM) pair so far in this run.  Used to pick the
        # correct _2, _3, … suffix when the same calendar month has more than
        # one statement with different content.
        month_variants: dict[str, int] = {}

        while True:
            page_num += 1
            rows = await self._parse_table()
            statement_rows = [r for r in rows if r.get("isStatement")]
            total_docs_found += len(statement_rows)

            if page_num == 1 and not statement_rows:
                if rows:
                    names = [r.get("docName", "?") for r in rows[:3]]
                    print(f"      No Statement rows (saw {len(rows)} other row(s): {names})")
                else:
                    await self._debug_table_content()
                break

            parse_failures = 0
            for row in statement_rows:
                date_str = _parse_date(row["dateText"])
                if not date_str:
                    parse_failures += 1
                    print(
                        f"      SKIP (no date): row {row['rowIndex']} "
                        f"| dateText={row['dateText']!r} | doc={row['docName']!r}"
                    )
                    continue

                parsed = _parse_account(row["acctText"])
                if parsed:
                    acct_type, acct_last4 = parsed
                    acct_label = self.make_account_label(acct_type, acct_last4)
                else:
                    acct_type, acct_last4, acct_label = "E*Trade", "0000", "etrade"

                acct_info = AccountInfo(acct_type, acct_last4, acct_label)

                if self.tracker.is_downloaded(self.config.slug, acct_label, date_str):
                    continue

                # YYYY-MM prefix for filenames; YYYY-MM-DD kept in date_str for
                # tracker uniqueness (two statements on different days of the same
                # month get separate tracker entries).
                month_str = date_str[:7]
                month_key = f"{acct_label}:{month_str}"

                # Download to a temp file first so we can inspect content before
                # committing to the final name.
                tmp_filename = f"_tmp_{date_str}_{acct_label}.pdf"
                tmp_target = self.output_dir / tmp_filename
                self.output_dir.mkdir(parents=True, exist_ok=True)

                success = await self._click_row_link(
                    row["rowIndex"], tmp_target, use_pdf_slot=row.get("usePdfSlot", False)
                )
                if success and tmp_target.exists() and tmp_target.stat().st_size > 0:
                    file_hash = self._sha256(tmp_target)
                    if file_hash in known_hashes:
                        orig = known_hashes[file_hash]
                        print(f"        DUPLICATE: content identical to {orig}, discarding")
                        tmp_target.unlink()
                    else:
                        # Unique content — pick YYYY-MM[_N] filename.
                        # Walk n=0,1,2,… until we find a slot that doesn't exist on disk.
                        n = month_variants.get(month_key, 0)
                        while True:
                            suffix = f"_{n + 1}" if n > 0 else ""
                            final_name = (
                                f"{month_str}{suffix}_{self.config.folder_name}_{acct_label}.pdf"
                            )
                            final_path = self.output_dir / final_name
                            if not final_path.exists():
                                break
                            n += 1
                        month_variants[month_key] = n + 1
                        tmp_target.rename(final_path)
                        known_hashes[file_hash] = final_name
                        self._record(acct_info, date_str, final_name, final_path)
                        print(f"      Downloaded: {final_name}")
                        total_new += 1
                        await asyncio.sleep(DOWNLOAD_DELAY)
                else:
                    size = tmp_target.stat().st_size if tmp_target.exists() else 0
                    reason = "empty file" if tmp_target.exists() else "file not created"
                    base_name = f"{month_str}_{self.config.folder_name}_{acct_label}.pdf"
                    row_info = (
                        f"row {row['rowIndex']} | date={row['dateText']!r} "
                        f"| doc={row['docName']!r} | reason={reason} (size={size})"
                    )
                    desc = f"{month_str} ({acct_label}) [{row_info}]"
                    if timeframe_label:
                        desc += f" [{timeframe_label}]"
                    self._failed_downloads.append(desc)
                    if tmp_target.exists():
                        tmp_target.unlink()
                    print(f"      FAILED: {base_name}  ({row_info})")

            total_parse_failures += parse_failures

            if not await self._click_next_page():
                break
            await self.page.wait_for_timeout(2000)
            try:
                await self.page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            await self.page.wait_for_timeout(1000)

        return (total_new, total_docs_found, total_parse_failures)

    # ------------------------------------------------------------------
    # Filter / dropdown helpers
    # ------------------------------------------------------------------

    async def _activate_statements_filter(self) -> None:
        """Click the 'Statements' quick-filter button/tab if present."""
        try:
            btn = self.page.locator(
                "button, a, [role='tab'], [role='button'], li"
            ).filter(has_text=re.compile(r"^Statements$", re.IGNORECASE)).first
            if await btn.is_visible(timeout=4000):
                await btn.click(force=True)
                print("      Clicked 'Statements' filter")
        except Exception:
            pass

    async def _read_timeframe_options(self) -> list[dict]:
        """Return [{label, value, isNative}] for the Timeframe dropdown."""
        # 1. Try native <select>
        opts = await self.page.evaluate("""() => {
            for (const sel of document.querySelectorAll('select')) {
                const options = Array.from(sel.options);
                const ok = options.some(o =>
                    /year.to.date|ytd/i.test(o.text) || /^\\d{4}$/.test(o.text.trim())
                );
                if (ok) {
                    return options.map(o => ({
                        label: o.text.trim(),
                        value: o.value,
                        isNative: true,
                    }));
                }
            }
            return null;
        }""")
        if opts:
            return opts

        # 2. Custom dropdown: click the trigger to open it, then read options
        try:
            trigger = self.page.locator(
                "button, [role='combobox'], [role='button'], select"
            ).filter(
                has_text=re.compile(
                    r"Timeframe|Last \d+\s*Days?|Year To Date|YTD|\b\d{4}\b",
                    re.IGNORECASE,
                )
            ).first
            if await trigger.is_visible(timeout=3000):
                await trigger.click()
                await self.page.wait_for_timeout(800)

                opts = await self.page.evaluate("""() => {
                    const els = document.querySelectorAll(
                        '[role="option"], [role="menuitem"], li, .dropdown-item'
                    );
                    const result = [];
                    for (const el of els) {
                        if (!el.offsetWidth || !el.offsetHeight) continue;
                        const text = (el.innerText || el.textContent || '').trim();
                        if (text && text.length < 60)
                            result.push({ label: text, value: text, isNative: false });
                    }
                    return result;
                }""")

                await self.page.keyboard.press("Escape")
                await self.page.wait_for_timeout(500)
                if opts:
                    return opts
        except Exception:
            pass

        return []

    async def _select_timeframe(self, option: dict) -> bool:
        """Select a timeframe option from the dropdown. Returns True on success."""
        if option.get("isNative"):
            return bool(await self.page.evaluate(
                """([val, label]) => {
                    for (const sel of document.querySelectorAll('select')) {
                        const match = Array.from(sel.options).find(
                            o => o.value === val || o.text.trim() === label
                        );
                        if (match) {
                            sel.value = match.value;
                            sel.dispatchEvent(new Event('change', {bubbles: true}));
                            return true;
                        }
                    }
                    return false;
                }""",
                [option["value"], option["label"]],
            ))

        # Custom dropdown: use JS to find the trigger by its current text content,
        # then click it and click the desired option.
        label = option["label"]
        result = await self.page.evaluate("""([targetLabel]) => {
            // Find the timeframe trigger: a button/div whose visible text matches
            // the typical timeframe values (Last N Days, Year To Date, or a year).
            const triggerPattern = /^(Last \\d|Year To Date|YTD|20\\d{2})/i;
            const triggers = document.querySelectorAll(
                'button, [role="combobox"], [role="listbox"], select, ' +
                '[aria-haspopup="listbox"], [aria-haspopup="true"]'
            );
            let trigger = null;
            for (const el of triggers) {
                const text = (el.innerText || el.textContent || '').trim();
                if (triggerPattern.test(text) && el.offsetWidth > 0) {
                    trigger = el;
                    break;
                }
            }
            if (!trigger) return 'no_trigger';

            trigger.click();
            return 'clicked_trigger';
        }""", [label])

        if result != "clicked_trigger":
            print(f"      Timeframe trigger not found (result={result})")
            return False

        await self.page.wait_for_timeout(800)

        # Click the matching option
        try:
            opt_el = self.page.locator(
                "[role='option'], [role='menuitem'], li, .dropdown-item"
            ).filter(
                has_text=re.compile(r"^\s*" + re.escape(label) + r"\s*$", re.IGNORECASE)
            ).first
            if await opt_el.is_visible(timeout=2000):
                await opt_el.click()
                await self.page.wait_for_timeout(300)
                return True
        except Exception:
            pass

        # Fallback: JS click on visible option matching the label exactly
        clicked = await self.page.evaluate("""([targetLabel]) => {
            const candidates = document.querySelectorAll(
                '[role="option"], [role="menuitem"], li, .dropdown-item, .select-option'
            );
            for (const el of candidates) {
                const text = (el.innerText || el.textContent || '').trim();
                if (text.toLowerCase() === targetLabel.toLowerCase() &&
                    el.offsetWidth > 0 && el.offsetHeight > 0) {
                    el.click();
                    return true;
                }
            }
            return false;
        }""", [label])
        return bool(clicked)

    async def _click_apply(self) -> None:
        """Click the Apply button to refresh results."""
        # Try Playwright locator with progressively broader patterns
        for pattern in [r"^Apply$", r"^Apply\b"]:
            try:
                btn = self.page.locator(
                    "button, [role='button'], input[type='submit']"
                ).filter(has_text=re.compile(pattern, re.IGNORECASE)).first
                if await btn.is_visible(timeout=2000):
                    await btn.click(force=True)
                    print("      Clicked Apply")
                    return
            except Exception:
                pass

        # JS fallback: click any visible element whose text is or starts with "Apply"
        clicked = await self.page.evaluate("""() => {
            const tags = ['button', '[role="button"]', 'input', 'a', 'span', 'div'];
            for (const tag of tags) {
                for (const el of document.querySelectorAll(tag)) {
                    const text = (el.innerText || el.textContent || el.value || '').trim();
                    if (/^Apply/i.test(text) && el.offsetWidth > 0 && el.offsetHeight > 0) {
                        el.click();
                        return text;
                    }
                }
            }
            return null;
        }""")
        if clicked:
            print(f"      Clicked Apply (JS fallback: '{clicked}')")
        else:
            print("      WARNING: Apply button not found — table may not refresh")

    # ------------------------------------------------------------------
    # Table parsing
    # ------------------------------------------------------------------

    async def _debug_table_content(self) -> None:
        """Print diagnostic info about tables, grid rows, and Shadow DOM slots."""
        # Playwright locators pierce Shadow DOM — check for slot elements first
        pdf_count = await self.page.locator("div[slot='pdfLinkData']").count()
        info = await self.page.evaluate("""() => {
            const parts = [`url=${location.href}`];
            const tables = document.querySelectorAll('table');
            const gridRows = document.querySelectorAll('[role="row"]');
            parts.push(`tables=${tables.length}`, `role-rows=${gridRows.length}`);

            for (let i = 0; i < Math.min(tables.length, 5); i++) {
                const t = tables[i];
                const allTrs = t.querySelectorAll('tr').length;
                parts.push(`table[${i}]: all-trs=${allTrs}`);
            }

            // Search for dates in light-DOM body (won't find Shadow DOM content)
            const body = document.body?.innerText || '';
            const dates = body.match(/\\d{1,2}\\/\\d{1,2}\\/\\d{2,4}/g) || [];
            parts.push(`dates-in-light-dom=${dates.length}`);
            parts.push(`body-length=${body.length}`);
            return parts.join(' | ');
        }""")
        print(f"      DEBUG: pdf-slot-elements={pdf_count} | {info}")

    async def _extract_table_row_chunks(self) -> list[dict]:
        """Get all visible table text (including Shadow DOM) and split into per-row chunks.

        Returns a list of dicts like [{"date": "12/31/25", "acct": "Individual ...2658"}, ...]
        in the same order as the visible table rows.
        """
        date_pat = r"\d{1,2}/\d{1,2}/\d{2,4}"

        # Strategy 0: Find div[slot] elements whose entire text is a date string.
        # E*Trade's ms-table-wc component uses slotted children for each table cell;
        # the date-column cells are direct light-DOM children with a slot attribute,
        # containing only the date text.  Playwright's locator API pierces shadow DOM,
        # so this finds them even though they're projected into the component's shadow root.
        # Because these are the SAME DOM elements (just in a different slot) as the
        # pdfLinkData slots, their DOM order perfectly aligns with div[slot='pdfLinkData'].
        try:
            # Use a plain (non-anchored) substring pattern for the Playwright
            # filter — anchored patterns with re.MULTILINE are not reliably
            # translated to the equivalent JS regex by Playwright.  Strict
            # validation (anchors + range checks) is done per element below.
            slot_dates = self.page.locator("div[slot]").filter(
                has_text=re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}")
            )
            cnt = await slot_dates.count()
            if cnt > 0:
                print(f"      [dates] Strategy 0: {cnt} date slot(s) found")
                # Also try to collect account strings from adjacent slot siblings.
                # Account cells contain at least one letter and end in 4 digits.
                # Exclude any element that also contains a date pattern (i.e. date cells).
                slot_accts = self.page.locator("div[slot]").filter(
                    has_text=re.compile(r"[A-Za-z].*\d{4}")
                ).filter(
                    has_not_text=re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}")
                )
                acct_cnt = await slot_accts.count()
                # Only use account slots if the count matches date count exactly —
                # a mismatch means we picked up false-positive elements (e.g. nav).
                use_accts = (acct_cnt == cnt)

                result = []
                for i in range(cnt):
                    dt = ""
                    try:
                        raw = (await slot_dates.nth(i).inner_text(timeout=1500)).strip()
                        dm = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})$", raw)
                        if dm:
                            m_int, d_int = int(dm.group(1)), int(dm.group(2))
                            if 1 <= m_int <= 12 and 1 <= d_int <= 31:
                                dt = raw
                    except Exception:
                        pass
                    acct = ""
                    if use_accts:
                        try:
                            acct = (await slot_accts.nth(i).inner_text(timeout=1500)).strip()
                        except Exception:
                            pass
                    result.append({"date": dt, "acct": acct})
                return result
        except Exception:
            pass

        table_text = ""

        # Strategy A: recursive JS traversal of open shadow roots, SCOPED to the
        # document-table component so navigation text doesn't bleed in.
        # E*Trade uses #shadow-root (open) so JS can traverse it directly.
        try:
            table_text = await self.page.evaluate(r"""() => {
                function getText(node, depth) {
                    if (depth > 25) return '';
                    let text = '';
                    if (node.shadowRoot) {
                        text += getText(node.shadowRoot, depth + 1);
                    }
                    for (const child of node.childNodes) {
                        if (child.nodeType === 3) {          // TEXT_NODE
                            const t = child.textContent.trim();
                            if (t) text += t + '\n';
                        } else if (child.nodeType === 1) {   // ELEMENT_NODE
                            text += getText(child, depth + 1);
                        }
                    }
                    return text;
                }

                // Find the table web component by tag name, traversing shadow roots.
                // Scoping avoids picking up navigation/chrome text with date-like strings.
                function findTableEl(node, depth) {
                    if (depth > 15) return null;
                    const tag = (node.tagName || '').toLowerCase();
                    if (tag && tag.startsWith('ms-') &&
                        (tag.includes('table') || tag.includes('grid'))) {
                        return node;
                    }
                    if (node.shadowRoot) {
                        const found = findTableEl(node.shadowRoot, depth + 1);
                        if (found) return found;
                    }
                    for (const child of node.children || []) {
                        const found = findTableEl(child, depth + 1);
                        if (found) return found;
                    }
                    return null;
                }

                const tableEl = findTableEl(document.body, 0);
                return getText(tableEl || document.body, 0);
            }""")
        except Exception:
            table_text = ""

        # Strategy B: Playwright inner_text() on progressively broader containers.
        # Playwright has its own shadow-piercing implementation that works even when
        # the JS approach above can't reach all content.
        if not table_text or not re.search(date_pat, table_text):
            for container_sel in [
                "ms-table-wc",
                "section.ms-table-grid__wrapper",
                "[class*='table-grid']",
                "[class*='documents']",
                "main",
                "body",
            ]:
                try:
                    loc = self.page.locator(container_sel).first
                    if await loc.count() == 0:
                        continue
                    text = await loc.inner_text(timeout=5000)
                    if re.search(date_pat, text):
                        table_text = text
                        break
                except Exception:
                    continue

        if not table_text or not re.search(date_pat, table_text):
            return []

        # Pull every date out in order — one per row, no boundary-splitting needed.
        # re.findall is immune to the "10/31/25 splits into 1 + 0/31/25" problem
        # that plagued the earlier re.split approach.
        dates = re.findall(date_pat, table_text)

        # All E*Trade rows share one account — find it once from the table text.
        # CSS/style content appears before any table data, so skip to the first
        # date to avoid matching stylesheet text that also ends in 4 digits.
        acct_str = ""
        first_date = re.search(date_pat, table_text)
        if first_date:
            after_dates = table_text[first_date.start():]
            m = re.search(r"([A-Za-z][^\n\r/]{4,60}\d{4})", after_dates)
            if m:
                acct_str = m.group(1).strip()

        return [{"date": d, "acct": acct_str} for d in dates]

    async def _parse_table(self) -> list[dict]:
        """Parse document rows. Tries three strategies in order:

        1. Shadow DOM slots: find div[slot='pdfLinkData'] via Playwright (pierces
           Shadow DOM automatically), then walk ancestors for date/account context.
        2. Native <table> with tbody tr or plain tr rows.
        3. ARIA grid rows ([role='row'] / [role='cell']).
        """
        # ------------------------------------------------------------------
        # Strategy 1 — Shadow DOM / web-component slots (E*Trade Morgan Stanley)
        #
        # Try multiple selectors with a retry loop because Angular components
        # can render lazily. Playwright's locator API automatically pierces
        # both open and closed shadow roots.
        #
        # Note: avoid broad selectors like 'a.ms-link[role="link"]' which also
        # match page navigation links.
        # ------------------------------------------------------------------
        _SLOT_SELECTORS = [
            "div[slot='pdfLinkData']",
            "ms-documents-pdf-link-formatter a[role='link']",
        ]
        pdf_links = None
        link_count = 0
        for attempt in range(3):
            for sel in _SLOT_SELECTORS:
                loc = self.page.locator(sel)
                cnt = await loc.count()
                if cnt > 0:
                    pdf_links = loc
                    link_count = cnt
                    break
            if link_count > 0:
                break
            if attempt < 2:
                await self.page.wait_for_timeout(2000)

        if link_count > 0 and pdf_links is not None:
            # Get per-row date/account by parsing the full table text once.
            # Playwright's inner_text() pierces shadow DOM, giving us all visible text.
            row_chunks = await self._extract_table_row_chunks()

            if row_chunks and len(row_chunks) != link_count:
                print(f"      NOTE: {len(row_chunks)} date chunks vs {link_count} pdf links "
                      f"— index alignment may be off")

            results = []
            for i in range(link_count):
                link = pdf_links.nth(i)
                try:
                    doc_name = (await link.inner_text(timeout=3000)).strip()
                except Exception:
                    doc_name = await link.get_attribute("aria-label") or ""
                    doc_name = doc_name.strip()
                if not doc_name:
                    continue

                is_statement = bool(re.search(r"Statement", doc_name, re.I))

                # Primary: use pre-parsed row chunks correlated by index
                date_text = ""
                acct_text = ""
                if i < len(row_chunks):
                    date_text = row_chunks[i].get("date", "")
                    acct_text = row_chunks[i].get("acct", "")

                # Fallback: walk up ancestor chain, looking for smallest container
                # that holds exactly 1 date (i.e., the row-level element).
                if not date_text:
                    for depth in range(1, 12):
                        xpath = "xpath=" + "/".join([".."] * depth)
                        try:
                            ancestor = link.locator(xpath)
                            full_text = await ancestor.inner_text(timeout=2000)
                            date_matches = re.findall(r"\d{1,2}/\d{1,2}/\d{2,4}", full_text)
                            if not date_matches:
                                continue
                            if len(date_matches) == 1:
                                # Row-level container: exactly one date
                                date_text = date_matches[0]
                                for line in re.split(r"[\n\r\t]+", full_text):
                                    line = line.strip()
                                    if re.search(r"\d{4}\s*$", line) and len(line) > 5:
                                        acct_text = line
                                        break
                                break
                            else:
                                # Table-level: use position-based extraction as last resort
                                if i < len(date_matches):
                                    date_text = date_matches[i]
                                # Keep walking up hoping to find a row-level container
                        except Exception:
                            continue  # try next depth instead of giving up entirely

                results.append({
                    "rowIndex": i,       # used by _click_row_link to find pdf_links.nth(i)
                    "dateText": date_text,
                    "acctText": acct_text,
                    "docName": doc_name,
                    "isStatement": is_statement,
                    "usePdfSlot": True,
                })

            return results

        # ------------------------------------------------------------------
        # Strategy 2 — Native <table> (tbody tr, or plain tr fallback)
        # ------------------------------------------------------------------
        # Strategy 3 — ARIA grid [role="row"] (div-based table)
        # Both handled via JS evaluate (light DOM only)
        # ------------------------------------------------------------------
        return await self.page.evaluate("""() => {
            const results = [];
            const dateRe = /\\d{1,2}\\/\\d{1,2}\\/\\d{2,4}/;

            function parseRows(rows, getCells) {
                for (let i = 0; i < rows.length; i++) {
                    const cells = getCells(rows[i]);
                    if (cells.length < 3) continue;
                    let dateText = '', acctText = '', docName = '';
                    for (const cell of cells) {
                        const text = (cell.innerText || cell.textContent || '').trim();
                        if (!dateText && dateRe.test(text)) {
                            dateText = text;
                        } else if (!acctText && /\\d{4}\\s*$/.test(text) && text.length > 5) {
                            acctText = text;
                        } else if (!docName && cell.querySelector('a') && text.length > 3) {
                            docName = text;
                        }
                    }
                    if (!dateText) continue;
                    results.push({
                        rowIndex: i,
                        dateText, acctText, docName,
                        isStatement: /Statement/i.test(docName),
                        usePdfSlot: false,
                    });
                }
            }

            for (const table of document.querySelectorAll('table')) {
                let trs = Array.from(table.querySelectorAll('tbody tr'));
                if (!trs.length)
                    trs = Array.from(table.querySelectorAll('tr'))
                        .filter(tr => tr.querySelectorAll('td').length >= 3);
                if (!trs.length) continue;
                parseRows(trs, tr => Array.from(tr.querySelectorAll('td')));
                if (results.length) return results;
            }

            const dataRows = Array.from(document.querySelectorAll('[role="row"]'))
                .filter(r =>
                    r.querySelector('[role="cell"],[role="gridcell"]') &&
                    !r.querySelector('[role="columnheader"]')
                );
            if (dataRows.length)
                parseRows(dataRows, r =>
                    Array.from(r.querySelectorAll('[role="cell"],[role="gridcell"]'))
                );

            return results;
        }""")

    # ------------------------------------------------------------------
    # Pagination
    # ------------------------------------------------------------------

    async def _click_next_page(self) -> bool:
        """Click the next-page control. Returns True if a click was made."""
        # Try aria-label selectors first
        for sel in [
            "[aria-label='Next page']",
            "[aria-label='Next']",
            "[title='Next']",
            "[aria-label*='next' i]",
        ]:
            try:
                btn = self.page.locator(sel).first
                if await btn.is_visible(timeout=1000):
                    if (
                        await btn.get_attribute("disabled") is None
                        and await btn.get_attribute("aria-disabled") != "true"
                    ):
                        await btn.click(force=True)
                        return True
            except Exception:
                continue

        # Try '>' or 'Next' text buttons
        try:
            btn = self.page.locator("button, a").filter(
                has_text=re.compile(r"^>$|^›$|^Next$", re.IGNORECASE)
            ).first
            if await btn.is_visible(timeout=1000):
                if (
                    await btn.get_attribute("disabled") is None
                    and await btn.get_attribute("aria-disabled") != "true"
                ):
                    await btn.click(force=True)
                    return True
        except Exception:
            pass

        return False

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    async def _click_row_link(
        self, row_index: int, target: Path, use_pdf_slot: bool = False
    ) -> bool:
        """Click the document link in the given row and capture the download.

        When use_pdf_slot=True, uses the Shadow-DOM-piercing Playwright locator to
        find the clickable element via the Morgan Stanley web component slots.
        When use_pdf_slot=False, falls back to native <table> tbody row traversal.
        """
        try:
            link = None
            if use_pdf_slot:
                # Try each slot selector in turn; the row_index is consistent across
                # them since all enumerate the same set of document rows.
                for slot_sel in [
                    "div[slot='pdfLinkData']",
                    "ms-documents-pdf-link-formatter a[role='link']",
                    "a.ms-link[role='link']",
                ]:
                    loc = self.page.locator(slot_sel)
                    cnt = await loc.count()
                    if cnt > row_index:
                        slot_el = loc.nth(row_index)
                        if "pdfLinkData" in slot_sel:
                            # Container element — find the inner clickable anchor
                            for a_sel in ["a[role='link']", "a.ms-link", "a"]:
                                candidate = slot_el.locator(a_sel).first
                                if await candidate.count() > 0:
                                    link = candidate
                                    break
                            if link is None:
                                # Fall back to clicking the container itself
                                link = slot_el
                        else:
                            # Already an <a> element
                            link = slot_el
                        break

            if link is None and not use_pdf_slot:
                # Native <table> path
                tables = self.page.locator("table")
                count = await tables.count()
                data_table = None
                for i in range(min(count, 10)):
                    t = tables.nth(i)
                    if await t.locator("tbody tr").count() > 0:
                        data_table = t
                        break
                if not data_table:
                    return False
                row = data_table.locator("tbody tr").nth(row_index)
                link = row.locator("a").first

            if link is None:
                print(f"        _click_row_link: no link found (row={row_index}, pdf_slot={use_pdf_slot})")
                return False

            async with self.page.expect_download(timeout=30000) as dl_info:
                await link.click(force=True)
            dl = await dl_info.value
            await dl.save_as(str(target))
            return True
        except Exception as e:
            print(f"        _click_row_link error (row={row_index}, pdf_slot={use_pdf_slot}): {e}")
            return False

    # ------------------------------------------------------------------
    # Shared helpers
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

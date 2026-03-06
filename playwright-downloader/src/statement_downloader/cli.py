"""Command-line entry point for the statement downloader.

Usage:
    python -m statement_downloader                      # Run all brokerages
    python -m statement_downloader schwab robinhood     # Run specific ones
    python -m statement_downloader --list               # List brokerages
    python -m statement_downloader --status             # Show download status
"""

import argparse
import asyncio
import sys

from .config import BROKERAGES
from .tracker import DownloadTracker
from .browser import BrowserManager
from .brokerages import ALL_BROKERAGES


async def _run(args: argparse.Namespace) -> None:
    tracker = DownloadTracker()

    if args.list:
        print("\nAvailable brokerages:\n")
        for slug, cfg in BROKERAGES.items():
            print(f"  {slug:<15s}  {cfg.display_name}")
        print()
        return

    if args.status:
        summary = tracker.get_status_summary()
        if not summary:
            print("\nNo statements downloaded yet.\n")
            return
        print("\nDownload status:\n")
        for slug, accounts in summary.items():
            cfg = BROKERAGES.get(slug)
            name = cfg.display_name if cfg else slug
            total = sum(accounts.values())
            print(f"  {name:<25s}  {total} statement(s)")
            for label, count in accounts.items():
                print(f"    {label:<20s}  {count}")
        print()
        return

    # Determine which brokerages to run
    slugs = args.brokerages if args.brokerages else list(BROKERAGES.keys())

    # Validate slugs
    invalid = [s for s in slugs if s not in BROKERAGES]
    if invalid:
        print(f"Unknown brokerage(s): {', '.join(invalid)}")
        print(f"Available: {', '.join(BROKERAGES.keys())}")
        sys.exit(1)

    browser_mgr = BrowserManager()
    context, page = await browser_mgr.launch()

    try:
        for slug in slugs:
            cfg = BROKERAGES[slug]
            brokerage_cls = ALL_BROKERAGES[slug]
            brokerage = brokerage_cls(page, tracker, cfg)

            print(f"\n{'─' * 60}")
            print(f"  {cfg.display_name}")
            print(f"{'─' * 60}")

            try:
                count = await brokerage.run()
                print(f"\n  Result: {count} new statement(s) downloaded")
            except Exception as e:
                print(f"\n  ERROR: {e}")
                print(f"  Skipping {cfg.display_name}, continuing with next brokerage...")
                continue
    finally:
        await browser_mgr.close()

    print(f"\n{'═' * 60}")
    print("  Done!")
    print(f"{'═' * 60}\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="statement-downloader",
        description="Download monthly brokerage statements via Playwright",
    )
    parser.add_argument(
        "brokerages",
        nargs="*",
        help="Brokerage slugs to process (default: all)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available brokerages",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show download status for all brokerages",
    )
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()

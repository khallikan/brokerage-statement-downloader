from playwright.async_api import async_playwright, BrowserContext, Page

from .config import BROWSER_DATA_DIR


class BrowserManager:
    def __init__(self):
        self._playwright = None
        self._context: BrowserContext | None = None

    async def launch(self) -> tuple[BrowserContext, Page]:
        """Launch a persistent Chromium browser context (headed).

        Returns the context and a page. The persistent profile in
        ``browser_data/`` stores cookies so subsequent runs may skip login.
        """
        BROWSER_DATA_DIR.mkdir(parents=True, exist_ok=True)

        self._playwright = await async_playwright().start()
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_DATA_DIR),
            headless=False,
            slow_mo=100,
            accept_downloads=True,
            viewport={"width": 1280, "height": 900},
        )

        # Use the default page that opens with the context, or create one
        if self._context.pages:
            page = self._context.pages[0]
        else:
            page = await self._context.new_page()

        return self._context, page

    async def close(self) -> None:
        if self._context:
            await self._context.close()
        if self._playwright:
            await self._playwright.stop()

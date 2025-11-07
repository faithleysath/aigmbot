from markdown_it import MarkdownIt
from playwright.async_api import async_playwright
from ncatbot.utils import get_log
import asyncio

LOG = get_log(__name__)


class MarkdownRenderer:
    def __init__(self):
        self.md = MarkdownIt("commonmark").disable("html_block").disable("html_inline")
        self._p = None
        self._browser = None
        self._init_lock = asyncio.Lock()

    async def _ensure_browser(self):
        if self._browser:
            return self._browser
        async with self._init_lock:
            if self._browser:
                return self._browser
            self._p = await async_playwright().start()
            try:
                self._browser = await self._p.chromium.launch()
            except Exception:
                self._browser = await self._p.chromium.launch(args=["--no-sandbox"])
        return self._browser

    async def close(self):
        try:
            if self._browser:
                await self._browser.close()
            if self._p:
                await self._p.stop()
        except Exception as e:
            LOG.warning(f"关闭渲染器失败: {e}")

    async def render_markdown(self, markdown_text: str) -> bytes | None:
        """
        将 Markdown 文本渲染成图片的二进制数据。

        :param markdown_text: 要渲染的 Markdown 字符串。
        :return: 成功则返回图片的二进制数据 (bytes)，否则返回 None。
        """
        try:
            html_content = self.md.render(markdown_text)

            # 添加一些基础样式以改善外观
            html_with_style = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="UTF-8">
                <style>
                    body {{
                        /* 最终渲染宽度 */
                        width: 1080px;
                        box-sizing: border-box; /* 确保 padding 不会撑大宽度 */

                        /* 暗色主题 */
                        background-color: #1B1C1D;
                        color: #ffffff;

                        /* 舒适的内边距 */
                        padding: 40px;

                        /* 系统默认字体 */
                        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
                        
                        /* 根据21个字/行计算出的字体大小 */
                        font-size: 47px;
                        line-height: 1.6;
                        margin: 0 auto; /* 居中显示 */

                        word-break: break-all; /* 防止长单词溢出 */
                        word-wrap: break-word;
                    }}
                    code {{
                        font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, Courier, monospace;
                        /* 适配暗色主题的代码块样式 */
                        background-color: #282A2C;
                        color: #C3C5C3;
                        padding: .2em .4em;
                        margin: 0;
                        font-size: 85%;
                        border-radius: 6px;
                    }}
                    pre > code {{
                        display: block;
                        padding: 2px 30px;
                        overflow: hidden;
                    }}
                </style>
            </head>
            <body>
                {html_content}
            </body>
            </html>
            """

            browser = await self._ensure_browser()
            page = await browser.new_page()
            try:
                await page.set_viewport_size({"width": 1080, "height": 100})
                await page.set_content(html_with_style, wait_until="networkidle")
                await page.wait_for_timeout(50)

                # 截图 body 元素以获得准确的内容尺寸
                element = await page.query_selector("body")
                image_bytes = await (
                    element.screenshot() if element else page.screenshot(full_page=True)
                )
            finally:
                await page.close()

            LOG.info("Markdown 成功渲染为图片二进制数据。")
            return image_bytes

        except Exception as e:
            LOG.error(f"Markdown 渲染失败: {e}")
            # 确保 Playwright 的浏览器驱动已安装
            if "Executable doesn't exist" in str(e):
                LOG.error(
                    "Playwright browser is not installed. Please run 'playwright install' in your terminal."
                )
            return None

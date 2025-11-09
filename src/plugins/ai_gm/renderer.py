from markdown_it import MarkdownIt
from playwright.async_api import async_playwright
from ncatbot.utils import get_log
import asyncio
import re

LOG = get_log(__name__)


def _calculate_reading_time(text: str) -> str:
    """计算文本的字数和预计阅读时间"""
    # 移除非文本内容，如 Markdown 格式
    text = re.sub(r"[`*#->]", "", text)
    # 统计中文字符
    chinese_chars = len(re.findall(r"[\u4e00-\u9fa5]", text))
    # 统计英文单词
    english_words = len(re.findall(r"[a-zA-Z0-9]+", text))
    total_words = chinese_chars + english_words
    # 按 350 字/分钟估算阅读时间
    reading_minutes = round(total_words / 350)
    reading_time_str = f"字数：{total_words}，预计阅读时间：约 {reading_minutes} 分钟"
    return reading_time_str


class MarkdownRenderer:
    def __init__(self):
        self.md = MarkdownIt("commonmark", {"breaks": True}).disable("html_block").disable("html_inline")
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

    async def render_markdown(
        self, markdown_text: str, extra_text: str | None = None
    ) -> bytes | None:
        """
        将 Markdown 文本渲染成图片的二进制数据。

        :param markdown_text: 要渲染的 Markdown 字符串。
        :param extra_text: 显示在左上角的可选附加文本。
        :return: 成功则返回图片的二进制数据 (bytes)，否则返回 None。
        """
        try:
            reading_time_info = _calculate_reading_time(markdown_text)
            html_content = self.md.render(markdown_text)
            extra_text_html = f"<span>{extra_text}</span>" if extra_text else "<span></span>"

            # 添加一些基础样式以改善外观
            html_with_style = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="UTF-8">
                <style>
                    body {{
                        position: relative; /* 为绝对定位的子元素提供容器 */
                        /* 最终渲染宽度 */
                        width: 1200px;
                        box-sizing: border-box; /* 确保 padding 不会撑大宽度 */

                        /* 暗色主题 */
                        background-color: #1B1C1D;
                        color: #ffffff;

                        /* 舒适的内边距 */
                        padding: 50px;
                        padding-top: 100px; /* 为阅读时间提示留出空间 */

                        /* 系统默认字体 */
                        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
                        
                        /* 根据21个字/行计算出的字体大小 */
                        font-size: 47px;
                        line-height: 1.6;
                        letter-spacing: 0.1em; /* 调整文字横向间距 */
                        margin: 0 auto; /* 居中显示 */

                        word-break: break-all; /* 防止长单词溢出 */
                        word-wrap: break-word;
                    }}
                    .header-info {{
                        position: absolute;
                        top: 60px;
                        left: 50px;
                        right: 50px;
                        display: flex;
                        justify-content: space-between;
                        font-size: 30px;
                        color: #888;
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
                <div class="header-info">
                    {extra_text_html}
                    <span>{reading_time_info}</span>
                </div>
                {html_content}
            </body>
            </html>
            """

            browser = await self._ensure_browser()
            page = await browser.new_page()
            try:
                await page.set_viewport_size({"width": 1200, "height": 100})
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

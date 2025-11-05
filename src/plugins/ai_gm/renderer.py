from markdown_it import MarkdownIt
from playwright.async_api import async_playwright
from ncatbot.utils import get_log
import os

LOG = get_log(__name__)

class MarkdownRenderer:
    def __init__(self, output_path: str):
        self.output_path = output_path
        if not os.path.exists(self.output_path):
            os.makedirs(self.output_path)
        self.md = MarkdownIt()

    async def render(self, markdown_text: str, filename: str) -> str | None:
        """
        将 Markdown 文本渲染成图片。

        :param markdown_text: 要渲染的 Markdown 字符串。
        :param filename: 输出图片的文件名（不含扩展名）。
        :return: 成功则返回图片文件的绝对路径，否则返回 None。
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
                        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
                        padding: 20px;
                        background-color: #f6f8fa;
                        color: #24292e;
                        line-height: 1.6;
                    }}
                    .container {{
                        max-width: 800px;
                        margin: 0 auto;
                        background-color: #ffffff;
                        border: 1px solid #d1d5da;
                        border-radius: 6px;
                        padding: 20px;
                    }}
                    code {{
                        font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, Courier, monospace;
                        background-color: #f6f8fa;
                        padding: .2em .4em;
                        margin: 0;
                        font-size: 85%;
                        border-radius: 3px;
                    }}
                    pre > code {{
                        display: block;
                        padding: 16px;
                        overflow: auto;
                    }}
                </style>
            </head>
            <body>
                <div class="container">
                    {html_content}
                </div>
            </body>
            </html>
            """

            output_filepath = os.path.join(self.output_path, f"{filename}.png")

            async with async_playwright() as p:
                browser = await p.chromium.launch()
                page = await browser.new_page()
                await page.set_content(html_with_style)
                
                # 截图 body 元素以获得准确的内容尺寸
                element = await page.query_selector('body')
                if element:
                    await element.screenshot(path=output_filepath)
                else:
                    # 如果找不到 body，则截图整个页面作为备用
                    await page.screenshot(path=output_filepath, full_page=True)
                    
                await browser.close()

            LOG.info(f"Markdown 成功渲染为图片: {output_filepath}")
            return output_filepath

        except Exception as e:
            LOG.error(f"Markdown 渲染失败: {e}")
            # 确保 Playwright 的浏览器驱动已安装
            if "Executable doesn't exist" in str(e):
                LOG.error("Playwright browser is not installed. Please run 'playwright install' in your terminal.")
            return None

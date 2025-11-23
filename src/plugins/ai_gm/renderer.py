from markdown_it import MarkdownIt
from playwright.async_api import async_playwright
from ncatbot.utils import get_log
import asyncio
import re
from pathlib import Path

from .constants import (
    MAX_CONCURRENT_RENDERS,
    RENDER_WIDTH,
    RENDER_PADDING,
    RENDER_TOP_PADDING,
    BASE_FONT_SIZE,
    HEADER_FONT_SIZE,
    READING_SPEED_WPM,
)

LOG = get_log(__name__)


def _calculate_reading_time(text: str) -> str:
    """
    计算文本的字数和预计阅读时间。
    
    Args:
        text: 要计算的文本内容
        
    Returns:
        str: 包含字数和预计阅读时间的格式化字符串
    """
    # 移除非文本内容，如 Markdown 格式
    text = re.sub(r"[`*#->]", "", text)
    # 统计中文字符
    chinese_chars = len(re.findall(r"[\u4e00-\u9fa5]", text))
    # 统计英文单词
    english_words = len(re.findall(r"[a-zA-Z0-9]+", text))
    total_words = chinese_chars + english_words
    # 按配置的阅读速度估算阅读时间
    reading_minutes = round(total_words / READING_SPEED_WPM)
    reading_time_str = f"字数：{total_words}，预计阅读时间：约 {reading_minutes} 分钟"
    return reading_time_str


class MarkdownRenderer:
    def __init__(self):
        self.md = MarkdownIt("commonmark", {"breaks": True}).disable("html_block").disable("html_inline")
        self._p = None
        self._browser = None
        self._init_lock = asyncio.Lock()
        self._render_semaphore = asyncio.Semaphore(MAX_CONCURRENT_RENDERS)
        self._browser_failed = False  # 标记浏览器初始化是否失败
        self._last_browser_fail_time = 0.0  # 上次浏览器初始化失败的时间
        self._browser_retry_interval = 300.0  # 浏览器重试间隔（秒）
        self._page_count = 0  # 跟踪打开的页面数
        self._max_pages = 50  # 最大页面数限制
        self._render_timeout = 30.0  # 单次渲染超时（秒）
        self._help_image_cache: bytes | None = None
        
    def clear_help_cache(self):
        """清除帮助图片缓存"""
        LOG.info("帮助图片缓存已清除")
        self._help_image_cache = None

    async def _is_browser_healthy(self) -> bool:
        """检查浏览器健康状态"""
        if not self._browser:
            return False
        try:
            # 检查浏览器是否响应
            contexts = self._browser.contexts
            if not contexts:
                return True
            # 检查是否有太多打开的页面
            total_pages = sum(len(ctx.pages) for ctx in contexts)
            if total_pages >= self._max_pages:
                LOG.warning(f"浏览器打开的页面数过多: {total_pages}/{self._max_pages}")
                return False
            return True
        except Exception as e:
            LOG.warning(f"浏览器健康检查失败: {e}")
            return False

    async def _reinit_browser(self):
        """重新初始化浏览器"""
        async with self._init_lock:
            if self._browser:
                try:
                    await self._browser.close()
                    LOG.info("已关闭旧浏览器实例")
                except Exception as e:
                    LOG.warning(f"关闭旧浏览器失败: {e}")
                finally:
                    self._browser = None
            
            if self._p:
                try:
                    await self._p.stop()
                except Exception:
                    pass
                self._p = None
            
            # 重置失败标记，允许重新初始化
            self._browser_failed = False
            self._last_browser_fail_time = 0.0
            self._page_count = 0

    async def _ensure_browser(self):
        """确保浏览器实例存在，使用双重检查锁定模式"""
        import time
        
        if self._browser:
            return self._browser
        
        # 如果之前初始化失败，检查是否已过重试间隔
        if self._browser_failed:
            if time.time() - self._last_browser_fail_time < self._browser_retry_interval:
                return None
            # 超过重试间隔，重置失败状态允许重试
            LOG.info("浏览器初始化冷却时间已过，尝试重新初始化...")
            self._browser_failed = False
        
        async with self._init_lock:
            # 双重检查：再次检查是否已被其他协程初始化
            if self._browser:
                return self._browser
            
            # 再次检查失败状态（可能在等待锁的过程中被其他协程设置）
            if self._browser_failed:
                if time.time() - self._last_browser_fail_time < self._browser_retry_interval:
                    return None
                self._browser_failed = False
            
            try:
                self._p = await async_playwright().start()
                try:
                    self._browser = await self._p.chromium.launch()
                except Exception as e:
                    LOG.warning(f"使用默认参数启动浏览器失败，尝试使用 --no-sandbox: {e}")
                    self._browser = await self._p.chromium.launch(args=["--no-sandbox"])
                
                LOG.info("Playwright 浏览器初始化成功")
                self._page_count = 0
                return self._browser
            except Exception as e:
                LOG.error(f"初始化 Playwright 浏览器失败: {e}")
                self._browser_failed = True
                self._last_browser_fail_time = time.time()
                # 清理可能部分初始化的资源
                if self._p:
                    try:
                        await self._p.stop()
                    except Exception:
                        pass
                    self._p = None
                return None

    async def close(self):
        """
        关闭渲染器并清理资源。
        
        此方法会安全地关闭浏览器和 Playwright 实例，
        即使某个步骤失败也会继续尝试关闭其他资源。
        """
        # 先关闭浏览器
        if self._browser:
            try:
                await self._browser.close()
                LOG.debug("浏览器已关闭")
            except Exception as e:
                LOG.warning(f"关闭浏览器失败: {e}")
            finally:
                self._browser = None
        
        # 再停止 Playwright
        if self._p:
            try:
                await self._p.stop()
                LOG.debug("Playwright 已停止")
            except Exception as e:
                LOG.warning(f"停止 Playwright 失败: {e}")
            finally:
                self._p = None

    async def render_markdown(
        self, markdown_text: str, extra_text: str | None = None
    ) -> bytes | None:
        """
        将 Markdown 文本渲染成图片的二进制数据。

        改进版本：添加超时控制、健康检查和更好的资源管理。

        :param markdown_text: 要渲染的 Markdown 字符串。
        :param extra_text: 显示在左上角的可选附加文本。
        :return: 成功则返回图片的二进制数据 (bytes)，否则返回 None。
        """
        # 使用信号量限制并发数
        async with self._render_semaphore:
            try:
                # 添加总体超时控制
                return await asyncio.wait_for(
                    self._render_markdown_impl(markdown_text, extra_text),
                    timeout=self._render_timeout
                )
            except asyncio.TimeoutError:
                LOG.error(f"Markdown 渲染超时（{self._render_timeout}s）")
                return None
            except Exception as e:
                LOG.error(f"Markdown 渲染失败: {e}", exc_info=True)
                return None

    async def _render_markdown_impl(
        self, markdown_text: str, extra_text: str | None = None
    ) -> bytes | None:
        """渲染 Markdown 的内部实现"""
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
                        width: {RENDER_WIDTH}px;
                        box-sizing: border-box; /* 确保 padding 不会撑大宽度 */

                        /* 暗色主题 */
                        background-color: #1B1C1D;
                        color: #ffffff;

                        /* 舒适的内边距 */
                        padding: {RENDER_PADDING}px;
                        padding-top: {RENDER_TOP_PADDING}px; /* 为阅读时间提示留出空间 */

                        /* 系统默认字体 */
                        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
                        
                        /* 根据21个字/行计算出的字体大小 */
                        font-size: {BASE_FONT_SIZE}px;
                        line-height: 1.6;
                        letter-spacing: 0.1em; /* 调整文字横向间距 */
                        margin: 0 auto; /* 居中显示 */

                        word-break: break-all; /* 防止长单词溢出 */
                        word-wrap: break-word;
                    }}
                    .header-info {{
                        position: absolute;
                        top: 60px;
                        left: {RENDER_PADDING}px;
                        right: {RENDER_PADDING}px;
                        display: flex;
                        justify-content: space-between;
                        font-size: {HEADER_FONT_SIZE}px;
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

            # 检查浏览器健康状态
            browser = await self._ensure_browser()
            if not browser:
                LOG.error("浏览器未初始化，无法渲染 Markdown")
                return None
            
            # 如果浏览器不健康，尝试重新初始化
            if not await self._is_browser_healthy():
                LOG.warning("浏览器健康检查失败，尝试重新初始化...")
                await self._reinit_browser()
                browser = await self._ensure_browser()
                if not browser:
                    LOG.error("浏览器重新初始化失败")
                    return None

            page = None
            page_created = False
            try:
                page = await browser.new_page()
                self._page_count += 1
                page_created = True
                
                await page.set_viewport_size({"width": RENDER_WIDTH, "height": 100})
                await page.set_content(html_with_style, wait_until="networkidle")
                await page.wait_for_timeout(50)

                # 截图 body 元素以获得准确的内容尺寸
                element = await page.query_selector("body")
                image_bytes = await (
                    element.screenshot() if element else page.screenshot(full_page=True)
                )
                
                LOG.debug("Markdown 成功渲染为图片二进制数据。")
                return image_bytes
                
            finally:
                # 确保页面被关闭，即使出现异常
                if page:
                    try:
                        await page.close()
                    except Exception as e:
                        LOG.warning(f"关闭页面时出错: {e}")
                
                # 无论页面关闭是否成功，只要创建了页面计数，就进行递减
                if page_created:
                    self._page_count = max(0, self._page_count - 1)

        except Exception as e:
            # 确保 Playwright 的浏览器驱动已安装
            if "Executable doesn't exist" in str(e):
                LOG.error(
                    "Playwright browser is not installed. Please run 'playwright install' in your terminal."
                )
            raise

    async def render_help_page(self) -> bytes | None:
        """
        将 help.html 模板渲染成图片并缓存。
        
        :return: 成功则返回图片的二进制数据 (bytes)，否则返回 None。
        """
        if self._help_image_cache:
            LOG.debug("命中帮助图片缓存")
            return self._help_image_cache

        async with self._render_semaphore:
            try:
                # 读取 HTML 模板内容
                template_path = Path(__file__).parent / "templates" / "help.html"
                if not template_path.exists():
                    LOG.error(f"帮助模板文件未找到: {template_path}")
                    return None
                
                html_content = template_path.read_text(encoding="utf-8")

                # 渲染
                image_bytes = await asyncio.wait_for(
                    self._render_html_to_image(html_content),
                    timeout=self._render_timeout
                )
                
                if image_bytes:
                    LOG.info("成功渲染帮助页面，并已缓存")
                    self._help_image_cache = image_bytes
                
                return image_bytes

            except asyncio.TimeoutError:
                LOG.error(f"帮助页面渲染超时 ({self._render_timeout}s)")
                return None
            except Exception as e:
                LOG.error(f"帮助页面渲染失败: {e}", exc_info=True)
                return None

    async def _render_html_to_image(self, html_content: str) -> bytes | None:
        """将原始 HTML 字符串渲染为图片的核心逻辑"""
        browser = await self._ensure_browser()
        if not browser:
            LOG.error("浏览器未初始化，无法渲染 HTML")
            return None

        if not await self._is_browser_healthy():
            LOG.warning("浏览器健康检查失败，尝试重新初始化...")
            await self._reinit_browser()
            browser = await self._ensure_browser()
            if not browser:
                LOG.error("浏览器重新初始化失败")
                return None

        page = None
        page_created = False
        try:
            page = await browser.new_page()
            self._page_count += 1
            page_created = True
            
            # 从 HTML 的 body 标签中获取宽度
            width_match = re.search(r'body\s*\{[^}]*width:\s*(\d+)px;', html_content)
            render_width = int(width_match.group(1)) if width_match else RENDER_WIDTH

            await page.set_viewport_size({"width": render_width, "height": 100})
            await page.set_content(html_content, wait_until="networkidle")
            await page.wait_for_timeout(50)

            element = await page.query_selector("body")
            image_bytes = await (
                element.screenshot() if element else page.screenshot(full_page=True)
            )
            
            return image_bytes
        finally:
            if page:
                try:
                    await page.close()
                except Exception as e:
                    LOG.warning(f"关闭页面时出错: {e}")
            
            if page_created:
                self._page_count = max(0, self._page_count - 1)

# agents/tools/builtin/browser_tools.py
"""Browser tools for web browsing, searching, and screenshot capabilities."""

import asyncio
import base64
import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime

from agent_runner.tools.base import BaseTool, ToolPlugin, ToolMetadata, ToolContext, ToolResult


class BrowserTool(BaseTool):
    """Browser tool for web navigation and interaction"""
    
    def __init__(self, context: Optional[ToolContext] = None):
        super().__init__(context)
        self._browser = None
        self._playwright = None
        self._current_page = None
        self._page_history = []
        self._headless = True
        self._timeout = 30000  # 30 seconds
    
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="browse",
            description="""Browser tool for web interaction. Supports:
- Opening web pages
- Getting page content (HTML or text)
- Taking screenshots
- Searching the web
- Following links
- Clicking elements
- Filling forms
- Executing JavaScript

Use this tool to browse websites, search for information, or interact with web pages.""",
            parameters={
                "action": {
                    "type": "string",
                    "description": "Action to perform: 'navigate', 'search', 'screenshot', 'get_content', 'click', 'type', 'back', 'close'",
                    "enum": ["navigate", "search", "screenshot", "get_content", "click", "type", "back", "close"],
                    "required": True
                },
                "url": {
                    "type": "string",
                    "description": "URL to navigate to (for 'navigate' action)"
                },
                "query": {
                    "type": "string",
                    "description": "Search query (for 'search' action)"
                },
                "selector": {
                    "type": "string",
                    "description": "CSS selector for element (for 'click', 'type' actions)"
                },
                "text": {
                    "type": "string",
                    "description": "Text to type (for 'type' action)"
                },
                "format": {
                    "type": "string",
                    "description": "Output format: 'text', 'html', 'markdown' (for 'get_content' action)",
                    "enum": ["text", "html", "markdown"],
                    "default": "text"
                },
                "screenshot_type": {
                    "type": "string",
                    "description": "Screenshot type: 'png', 'jpeg' (for 'screenshot' action)",
                    "enum": ["png", "jpeg"],
                    "default": "png"
                },
                "full_page": {
                    "type": "boolean",
                    "description": "Take full page screenshot",
                    "default": False
                },
                "search_engine": {
                    "type": "string",
                    "description": "Search engine to use: 'google', 'bing', 'duckduckgo'",
                    "enum": ["google", "bing", "duckduckgo"],
                    "default": "duckduckgo"
                },
                "wait_for": {
                    "type": "string",
                    "description": "Wait for selector before proceeding",
                    "optional": True
                }
            },
            required_permissions=["browser"],
            category="web",
            tags=["browser", "web", "search", "screenshot"]
        )
    
    async def _ensure_browser(self):
        """Ensure browser is initialized"""
        if self._browser is None:
            try:
                from playwright.async_api import async_playwright
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(
                    headless=self._headless,
                    args=[
                        '--no-sandbox',
                        '--disable-setuid-sandbox',
                        '--disable-dev-shm-usage',
                        '--disable-accelerated-2d-canvas',
                        '--disable-gpu'
                    ]
                )
                
                # Create context with viewport
                self._context = await self._browser.new_context(
                    viewport={'width': 1280, 'height': 720},
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                )
                
                # Create page
                self._current_page = await self._context.new_page()
                
            except ImportError:
                return ToolResult.fail(
                    "Playwright not installed. Run: pip install playwright && playwright install chromium"
                )
            except Exception as e:
                return ToolResult.fail(f"Failed to initialize browser: {e}")
        
        return ToolResult.ok()
    
    async def execute(self, **kwargs) -> ToolResult:
        """Execute browser action"""
        action = kwargs.get("action", "").lower()
        
        # Ensure browser is initialized
        init_result = await self._ensure_browser()
        if not init_result.success:
            return init_result
        
        try:
            if action == "navigate":
                return await self._navigate(kwargs.get("url"), kwargs.get("wait_for"))
            elif action == "search":
                return await self._search(kwargs.get("query"), kwargs.get("search_engine", "duckduckgo"))
            elif action == "screenshot":
                return await self._screenshot(
                    kwargs.get("screenshot_type", "png"),
                    kwargs.get("full_page", False)
                )
            elif action == "get_content":
                return await self._get_content(kwargs.get("format", "text"))
            elif action == "click":
                return await self._click(kwargs.get("selector"), kwargs.get("wait_for"))
            elif action == "type":
                return await self._type(kwargs.get("selector"), kwargs.get("text"))
            elif action == "back":
                return await self._back()
            elif action == "close":
                return await self._close()
            else:
                return ToolResult.fail(f"Unknown action: {action}")
        except Exception as e:
            return ToolResult.fail(f"Browser action failed: {e}")
    
    async def _navigate(self, url: str, wait_for: Optional[str] = None) -> ToolResult:
        """Navigate to a URL"""
        if not url:
            return ToolResult.fail("URL is required for navigate action")
        
        # Add https:// if no protocol
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        
        try:
            # Navigate
            response = await self._current_page.goto(url, wait_until='networkidle')
            
            # Wait for selector if specified
            if wait_for:
                await self._current_page.wait_for_selector(wait_for, timeout=self._timeout)
            
            # Save to history
            self._page_history.append(url)
            
            # Get title
            title = await self._current_page.title()
            
            return ToolResult.ok({
                "url": url,
                "title": title,
                "status": response.status if response else None,
                "action": "navigated"
            })
        except Exception as e:
            return ToolResult.fail(f"Failed to navigate to {url}: {e}")
    
    async def _search(self, query: str, engine: str = "duckduckgo") -> ToolResult:
        """Search the web"""
        if not query:
            return ToolResult.fail("Query is required for search action")
        
        search_urls = {
            "google": f"https://www.google.com/search?q={query.replace(' ', '+')}",
            "bing": f"https://www.bing.com/search?q={query.replace(' ', '+')}",
            "duckduckgo": f"https://duckduckgo.com/?q={query.replace(' ', '+')}"
        }
        
        url = search_urls.get(engine, search_urls["duckduckgo"])
        
        try:
            await self._current_page.goto(url, wait_until='networkidle')
            self._page_history.append(url)
            
            # Extract search results based on engine
            results = await self._extract_search_results(engine)
            
            return ToolResult.ok({
                "query": query,
                "engine": engine,
                "url": url,
                "results": results,
                "action": "searched"
            })
        except Exception as e:
            return ToolResult.fail(f"Search failed: {e}")
    
    async def _extract_search_results(self, engine: str) -> List[Dict]:
        """Extract search results from page"""
        selectors = {
            "google": "div.g",
            "bing": "li.b_algo",
            "duckduckgo": "article"
        }
        
        selector = selectors.get(engine, "article")
        
        try:
            # Wait for results
            await self._current_page.wait_for_selector(selector, timeout=10000)
            
            # Extract results
            results = await self._current_page.evaluate(f"""
                () => {{
                    const items = document.querySelectorAll('{selector}');
                    return Array.from(items).slice(0, 10).map(item => {{
                        const titleElem = item.querySelector('h2, h3, a');
                        const linkElem = item.querySelector('a');
                        const descElem = item.querySelector('p, .description');
                        
                        return {{
                            title: titleElem?.innerText || '',
                            url: linkElem?.href || '',
                            description: descElem?.innerText || ''
                        }};
                    }});
                }}
            """)
            
            return results
        except Exception as e:
            # Return basic info if extraction fails
            title = await self._current_page.title()
            return [{
                "title": f"Search results for {engine}",
                "url": self._current_page.url,
                "description": f"Page title: {title}"
            }]
    
    async def _screenshot(self, format: str = "png", full_page: bool = False) -> ToolResult:
        """Take screenshot of current page"""
        try:
            screenshot_bytes = await self._current_page.screenshot(
                type=format,
                full_page=full_page
            )
            
            # Save to file
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            screenshot_path = self.context.workspace_dir / "screenshots" if self.context else Path("/tmp")
            screenshot_path.mkdir(parents=True, exist_ok=True)
            
            filename = f"screenshot_{timestamp}.{format}"
            filepath = screenshot_path / filename
            
            with open(filepath, "wb") as f:
                f.write(screenshot_bytes)
            
            # Also return base64 for inline display
            b64 = base64.b64encode(screenshot_bytes).decode()
            
            return ToolResult.ok({
                "path": str(filepath),
                "base64": b64[:100] + "..." if len(b64) > 100 else b64,  # Truncate for response
                "size": len(screenshot_bytes),
                "format": format,
                "full_page": full_page,
                "url": self._current_page.url
            })
        except Exception as e:
            return ToolResult.fail(f"Failed to take screenshot: {e}")
    
    async def _get_content(self, format: str = "text") -> ToolResult:
        """Get page content"""
        try:
            if format == "html":
                content = await self._current_page.content()
            elif format == "markdown":
                # Extract text and convert to markdown
                content = await self._extract_markdown()
            else:  # text
                content = await self._current_page.evaluate("document.body.innerText")
            
            # Truncate if too long
            max_length = 10000
            if len(content) > max_length:
                content = content[:max_length] + f"\n\n... (truncated, total {len(content)} chars)"
            
            return ToolResult.ok({
                "format": format,
                "content": content,
                "length": len(content),
                "url": self._current_page.url,
                "title": await self._current_page.title()
            })
        except Exception as e:
            return ToolResult.fail(f"Failed to get content: {e}")
    
    async def _extract_markdown(self) -> str:
        """Extract page content as markdown"""
        markdown = await self._current_page.evaluate("""
            () => {
                // Extract main content
                const contentSelectors = [
                    'main', 'article', '.content', '#content',
                    '.post-content', '.entry-content', '.article-content'
                ];
                
                let contentElem = null;
                for (const selector of contentSelectors) {
                    const elem = document.querySelector(selector);
                    if (elem) {
                        contentElem = elem;
                        break;
                    }
                }
                
                if (!contentElem) {
                    contentElem = document.body;
                }
                
                // Extract title
                const title = document.title;
                
                // Extract text with basic structure
                const text = contentElem.innerText || '';
                
                return `# ${title}\\n\\n${text}`;
            }
        """)
        return markdown
    
    async def _click(self, selector: str, wait_for: Optional[str] = None) -> ToolResult:
        """Click an element"""
        if not selector:
            return ToolResult.fail("Selector is required for click action")
        
        try:
            # Wait for element
            await self._current_page.wait_for_selector(selector, timeout=self._timeout)
            
            # Click
            await self._current_page.click(selector)
            
            # Wait for navigation if needed
            if wait_for:
                await self._current_page.wait_for_selector(wait_for, timeout=self._timeout)
            
            return ToolResult.ok({
                "selector": selector,
                "clicked": True,
                "url": self._current_page.url
            })
        except Exception as e:
            return ToolResult.fail(f"Failed to click {selector}: {e}")
    
    async def _type(self, selector: str, text: str) -> ToolResult:
        """Type text into an element"""
        if not selector:
            return ToolResult.fail("Selector is required for type action")
        
        if not text:
            return ToolResult.fail("Text is required for type action")
        
        try:
            # Wait for element
            await self._current_page.wait_for_selector(selector, timeout=self._timeout)
            
            # Clear and type
            await self._current_page.fill(selector, "")
            await self._current_page.type(selector, text, delay=50)
            
            return ToolResult.ok({
                "selector": selector,
                "text": text[:50] + "..." if len(text) > 50 else text,
                "action": "typed"
            })
        except Exception as e:
            return ToolResult.fail(f"Failed to type into {selector}: {e}")
    
    async def _back(self) -> ToolResult:
        """Go back in history"""
        try:
            await self._current_page.go_back()
            self._page_history.pop() if self._page_history else None
            
            return ToolResult.ok({
                "url": self._current_page.url,
                "title": await self._current_page.title(),
                "action": "back"
            })
        except Exception as e:
            return ToolResult.fail(f"Failed to go back: {e}")
    
    async def _close(self) -> ToolResult:
        """Close browser"""
        try:
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
            
            self._browser = None
            self._playwright = None
            self._current_page = None
            self._page_history = []
            
            return ToolResult.ok({"action": "browser closed"})
        except Exception as e:
            return ToolResult.fail(f"Failed to close browser: {e}")
    
    async def cleanup(self):
        """Cleanup browser resources"""
        await self._close()


class BrowserPlugin(ToolPlugin):
    """Browser plugin that provides browser tools"""
    
    def __init__(self):
        super().__init__()
        self._initialized = False
        self._browser_tool = None
     
    @property
    def name(self):
        return "browser"

    @property
    def version(self):
        return "1.0.0"
    

    async def initialize(self, context: ToolContext) -> None:
        """Initialize plugin"""
        self._browser_tool = BrowserTool(context)
        self.register_tool(self._browser_tool)
        self._initialized = True
    
    async def cleanup(self) -> None:
        """Cleanup plugin"""
        if self._browser_tool:
            await self._browser_tool.cleanup()
        self._initialized = False
    
    def get_tool(self, name: str):
        """Get tool by name"""
        if name == "browse":
            return self._browser_tool
        return super().get_tool(name)

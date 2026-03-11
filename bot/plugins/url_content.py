import logging
import requests
from typing import Dict

from bs4 import BeautifulSoup

from .plugin import Plugin


class UrlContentPlugin(Plugin):
    """
    A plugin to fetch and extract text content from a given URL.
    """

    MAX_CONTENT_LENGTH = 10000

    def get_source_name(self) -> str:
        return "URL Content"

    def get_spec(self) -> [Dict]:
        return [{
            "name": "fetch_url_content",
            "description": "Fetch and extract the main text content from a given URL. "
                           "Use this when a user shares a link and wants you to read, summarize, "
                           "or answer questions about the content of that webpage.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The full URL to fetch content from. "
                                       "Must include the protocol (e.g. https://example.com)"
                    }
                },
                "required": ["url"],
            },
        }]

    async def execute(self, function_name, helper, **kwargs) -> Dict:
        url = kwargs.get('url', '')
        if not url:
            return {"result": "No URL provided."}

        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (compatible; ChatBot/1.0)',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
            }
            response = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
            response.raise_for_status()

            content_type = response.headers.get('Content-Type', '')

            # Handle plain text content directly
            if 'text/plain' in content_type:
                text = response.text[:self.MAX_CONTENT_LENGTH]
                return {"url": url, "content": text}

            # Parse HTML content
            soup = BeautifulSoup(response.text, 'html.parser')

            # Extract page title
            title = soup.title.string.strip() if soup.title and soup.title.string else ''

            # Remove non-content elements
            for tag in soup.find_all(['script', 'style', 'nav', 'header', 'footer',
                                      'iframe', 'noscript', 'svg']):
                tag.decompose()

            # Try to find the main content area
            main_content = (
                soup.find('main') or
                soup.find('article') or
                soup.find('div', role='main') or
                soup.find('div', class_='content') or
                soup.find('div', class_='post') or
                soup.body or
                soup
            )

            text = main_content.get_text(separator='\n', strip=True)

            # Collapse multiple blank lines
            lines = [line.strip() for line in text.splitlines()]
            text = '\n'.join(line for line in lines if line)

            if len(text) > self.MAX_CONTENT_LENGTH:
                text = text[:self.MAX_CONTENT_LENGTH] + "\n\n[Content truncated...]"

            if not text:
                return {"url": url, "result": "The page was fetched but no readable text content was found."}

            result = {"url": url, "content": text}
            if title:
                result["title"] = title
            return result

        except requests.exceptions.Timeout:
            logging.warning(f'URL content fetch timed out: {url}')
            return {"result": f"Request timed out when trying to fetch {url}"}
        except requests.exceptions.HTTPError as e:
            logging.warning(f'URL content fetch HTTP error: {e}')
            return {"result": f"HTTP error {e.response.status_code} when fetching {url}"}
        except Exception as e:
            logging.warning(f'URL content fetch failed: {e}')
            return {"result": f"Failed to fetch content from {url}"}

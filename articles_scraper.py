import configparser
import os
import random
import string
import time
import logging
import requests
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
from requests.exceptions import RequestException, ConnectionError, Timeout
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, urljoin, unquote
from tenacity import retry
from tenacity.stop import stop_after_attempt
from tenacity.wait import wait_random
from typing import List, Dict, Optional

# TODO: Auto get Proxies
# TODO: Simplify the config file

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
ua = UserAgent()


class WebScraper:
    def __init__(self, config_file: str):
        self.config_dict = self._read_config_file(config_file)
        self.thread_num = self.config_dict.get('number_of_threads', 10)
        self.headers = self.config_dict.get('request_headers', {})
        self.session = requests.Session()
        self.proxies = self.config_dict.get('proxies', None)
        if self.proxies:
            self.proxies = self.test_proxies(self.proxies)
        else:
            self.proxies = []
        self.failed_links = []

    @staticmethod
    def _read_config_file(config_file):
        config = configparser.ConfigParser()
        config.read(config_file)

        config_dict = {
            'links_to_scrape_file': config.get('paths', 'links_to_scrape_file', fallback=None),
            'scraped_content_dir': config.get('paths', 'scraped_content_dir', fallback=None),
            'extract_article_selector': config.get('content_filtering', 'extract_article_selector', fallback=None),
            'remove_elements_selectors': config.get('content_filtering', 'remove_elements_selectors', fallback='').split(','),
            'downloaded_images_dir': config.get('paths', 'downloaded_images_dir', fallback=None),
            'request_headers': dict(config.items('request_headers')) if config.has_section('request_headers') else {}
        }
        if config.has_option('network', 'proxies'):
            config_dict['proxies'] = config.get(
                'network', 'proxies').split(',')

        if config.has_option('performance', 'number_of_threads'):
            config_dict['number_of_threads'] = config.getint(
                'performance', 'number_of_threads')

        return config_dict

    def validate_config(self):
        if 'links_to_scrape_file' not in self.config_dict or not isinstance(self.config_dict['links_to_scrape_file'], str):
            raise ValueError(
                "Invalid configuration: 'links_path' must be a string.")

    def get_article_links(self):
        try:
            with open(self.config_dict['links_to_scrape_file'], 'r', encoding='utf-8') as f:
                return [link.strip() for link in f if self.is_valid_url(link.strip())]
        except FileNotFoundError as e:
            logger.error(
                f"File {self.config_dict['links_to_scrape_file']} not found: {e}")
            raise

    @staticmethod
    def get_random_sleep_time(min_time=1, max_time=3):
        return random.uniform(min_time, max_time)

    @staticmethod
    def is_valid_url(url):
        try:
            result = urlparse(url)
            return all([result.scheme, result.netloc])
        except ValueError:
            return False

    @retry(stop=stop_after_attempt(3), wait=wait_random(min=1, max=3))
    def _make_request(self, article_link: str) -> BeautifulSoup:
        headers = {'User-Agent': ua.random}
        headers.update(self.headers)
        for proxy in self.proxies + [None]:
            try:
                time.sleep(self.get_random_sleep_time())
                response = self.session.get(article_link, headers=headers, proxies={
                                            "http": proxy, "https": proxy} if proxy else None)
                response.raise_for_status()
                logger.info(
                    f"Successfully made a request to {article_link}. Status code: {response.status_code}.")
                return BeautifulSoup(response.content, 'lxml')
            except (RequestException, ConnectionError, Timeout) as e:
                logger.error(
                    f"Error while making a request to {article_link} with proxy {proxy}. Error message: {e}. Trying another proxy.")
            except Exception as e:
                logger.error(
                    f"Unexpected error while making a request to {article_link} with proxy {proxy}. Error type: {type(e).__name__}. Error message: {e}")
        raise Exception(
            f"Failed to make request to {article_link} after trying all proxies and without proxy.")

    def extract_article(self, soup: BeautifulSoup) -> Optional[BeautifulSoup]:
        article_body = soup.select_one(
            self.config_dict.get('extract_article_selector'))
        if not article_body:
            logger.error(
                f"Could not find any elements with selector {self.config_dict.get('extract_article_selector')}")
            return None
        return article_body

    def filter_content(self, article_body: BeautifulSoup) -> Optional[BeautifulSoup]:
        if article_body is None:
            logger.error("Received article_body as None")
            return None

        remove_elements_selectors = self.config_dict.get('remove_elements_selectors', [])
        if remove_elements_selectors:
            for selector in remove_elements_selectors:
                if selector:
                    try:
                        for unwanted_tag in article_body.select(selector):
                            unwanted_tag.decompose()
                    except Exception as e:
                        logger.error(
                            f"Error while trying to decompose tag with selector {selector}. Error message: {e}")
        return article_body


    def download_image(self, img_url: str, img_file_path: str) -> None:
        img_dir = os.path.dirname(img_file_path)
        os.makedirs(img_dir, exist_ok=True)
        try:
            headers = {'User-Agent': ua.random}
            headers.update(self.headers)
            response = self.session.get(img_url, headers=headers, stream=True)
            response.raise_for_status()
            with open(img_file_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            logger.info(f"Successfully downloaded image from {img_url}.")
        except (RequestException, ConnectionError, Timeout) as e:
            logger.error(
                f"Error while downloading image from {img_url}. Error message: {e}")

    def download_images(self, article_body: BeautifulSoup) -> List[str]:
        images = article_body.find_all('img')
        img_urls = []
        for img in images:
            img_url = urljoin(
                self.config_dict['links_to_scrape_file'], img.get('src'))
            if self.is_valid_url(img_url):
                parsed = urlparse(img_url)
                img_file_name = os.path.basename(unquote(parsed.path))
                img_file_path = os.path.join(
                    self.config_dict['downloaded_images_dir'], img_file_name)
                self.download_image(img_url, img_file_path)
                img_urls.append(img_file_path)
                # Update the src attribute to local path
                img['src'] = img_file_path
        return img_urls

    def extract_and_filter(self, soup: BeautifulSoup) -> Optional[Dict[str, str]]:
        article_body = self.extract_article(soup)
        if article_body is None:
            logger.error("article_body is None")
            return None  # return None if there is no article body

        filtered_content = self.filter_content(article_body)

        # Initialize img_urls to an empty list
        img_urls = []
        # Only attempt to download images if article_body is not None
        img_urls = self.download_images(article_body)
        return {"text": filtered_content, "images": img_urls}


    def extract_article_content(self, article_link: str) -> Optional[Dict[str, str]]:
        try:
            soup = self._make_request(article_link)
            return self.extract_and_filter(soup)
        except Exception as e:
            logger.error(
                f"Error while extracting content from {article_link}. Error message: {e}")
            self.failed_links.append(article_link)
            return None

    def write_failed_links(self, file_path: str) -> None:
        with open(file_path, 'w', encoding='utf-8') as f:
            for link in self.failed_links:
                f.write(link + '\n')

    def write_article_content(self, article_content: Dict[str, str], file_name: str) -> None:
        file_dir = os.path.dirname(file_name)
        os.makedirs(file_dir, exist_ok=True)
        with open(file_name, 'w', encoding='utf-8') as f:
            f.write(article_content['text'].prettify())

    @staticmethod
    def generate_filename(article_link: str) -> str:
        """Generate a filename based on the article link."""
        filename_base = "".join(
            [char for char in article_link if char in string.ascii_letters + string.digits])[-255:]
        return filename_base + '.html'

    def scrape(self):
        article_links = self.get_article_links()
        if not article_links:
            logger.error("No valid links to scrape.")
            return

        article_contents = []  # add a list to store the article contents

        with ThreadPoolExecutor(max_workers=self.thread_num) as executor:
            futures = {executor.submit(
                self.extract_article_content, link): link for link in article_links}
            for future in as_completed(futures):
                article_link = futures[future]
                try:
                    article_content = future.result()
                except (requests.exceptions.RequestException, ConnectionError, Timeout) as e:
                    logger.error(
                        f"Connection error while scraping {article_link}. Error type: {type(e).__name__}. Error message: {e}")
                    continue
                except Exception as e:
                    logger.error(
                        f"Unexpected error while scraping {article_link}. Error type: {type(e).__name__}. Error message: {e}")
                    continue

                if article_content:
                    file_name = self.generate_filename(article_link)
                    file_path = os.path.join(
                        self.config_dict['scraped_content_dir'], file_name)
                    try:
                        self.write_article_content(article_content, file_path)
                    except (OSError, IOError) as e:
                        logger.error(
                            f"File error while writing content from {article_link}. Error type: {type(e).__name__}. Error message: {e}")
                        continue
                    logger.info(
                        f"Successfully scraped and saved article content from {article_link}.")
                    article_contents.append(article_content)

        # After all scraping tasks are done, write the contents to an HTML file
        self.write_all_contents_to_html(article_contents)
        self.write_failed_links('failed_links.txt')

    def write_all_contents_to_html(self, contents: List[Dict[str, str]]):
        html_content = "<html><body>"
        for content in contents:
            html_content += "<div>"
            html_content += str(content['text'])
            html_content += "</div><hr>"
        html_content += "</body></html>"

        # Create the directory if it does not exist
        os.makedirs(self.config_dict['scraped_content_dir'], exist_ok=True)

        html_file_path = os.path.join(
            self.config_dict['scraped_content_dir'], 'all_articles.html')
        with open(html_file_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        logger.info(
            f"Successfully saved all article contents to {html_file_path}.")

    def test_proxy(self, proxy):
        url = "https://www.google.com"
        try:
            response = requests.get(
                url, proxies={"http": proxy, "https": proxy}, timeout=3)
            response.raise_for_status()
            return True
        except Exception:
            return False

    def test_proxies(self, proxies):
        valid_proxies = []
        with ThreadPoolExecutor(max_workers=self.thread_num) as executor:
            future_to_proxy = {executor.submit(
                self.test_proxy, proxy): proxy for proxy in proxies}
            for future in as_completed(future_to_proxy):
                proxy = future.result()
                if proxy:
                    valid_proxies.append(proxy)
        return valid_proxies


if __name__ == "__main__":
    scraper = WebScraper('config.ini')
    scraper.scrape()

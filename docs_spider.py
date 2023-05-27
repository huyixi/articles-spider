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
from pathlib import Path
from urllib.parse import urljoin, urlparse
from tenacity import retry, stop_after_attempt, wait_random
from typing import List, Dict, Optional


logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
ua = UserAgent()

class WebScraper:
    def __init__(self, config_file: str):
        self.config_dict = self._read_config_file(config_file)
        self.thread_num = self.config_dict.get('thread_num', 10)
        self.proxies = self.config_dict.get('proxies', None)
        self.headers = self.config_dict.get('headers', {})
        self.session = requests.Session()

    @staticmethod
    def _read_config_file(config_file):
        config = configparser.ConfigParser()
        config.read(config_file)

        config_dict = {
            'file_path': config.get('path', 'link', fallback=None),
            'save_path': config.get('path', 'save', fallback=None),
            'article_tag': config.get('article', 'tag', fallback=None),
            'article_class': config.get('article', 'class', fallback=None),
            'article_id': config.get('article', 'id', fallback=None),
            'filter_list': config.get('filter', 'filter_list', fallback='').split(','),
            'images_save_path': config.get('path', 'images_save_path', fallback=None),
            'headers': dict(config.items('headers')) if config.has_section('headers') else {}
        }
        if config.has_option('request', 'proxies'):
            config_dict['proxies'] = config.get('request', 'proxies').split(',')
        
        return config_dict

    def get_article_links(self):
        try:
            with open(self.config_dict['file_path'], 'r', encoding='utf-8') as f:
                return [link.strip() for link in f.readlines() if self.is_valid_url(link.strip())]
        except FileNotFoundError as e:
            logger.error(f"File {self.config_dict['file_path']} not found: {e}")
            return []
    
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

    @retry(stop=stop_after_attempt(3), wait=wait_random(min=1, max=2))
    def _make_request(self, article_link: str) -> BeautifulSoup:
        headers = {'User-Agent': ua.random}
        headers.update(self.headers)
        for proxy in self.proxies + [None]:
            try:
                time.sleep(self.get_random_sleep_time())
                response = self.session.get(article_link, headers=headers, proxies={"http": proxy, "https": proxy} if proxy else None)
                response.raise_for_status()
                logger.info(f"Successfully made a request to {article_link}. Status code: {response.status_code}.")
                return BeautifulSoup(response.content, 'lxml')
            except (RequestException, ConnectionError, Timeout) as e:
                logger.error(f"Error while making a request to {article_link} with proxy {proxy}. Error message: {e}. Trying another proxy.")
        raise Exception(f"Failed to make request to {article_link} after trying all proxies and without proxy.")


    def extract_article(self, soup: BeautifulSoup) -> BeautifulSoup:
        article_body = soup.find(
            self.config_dict.get('article_tag'),
            class_=self.config_dict.get('article_class'),
            id=self.config_dict.get('article_id')
        )
        return article_body

    def filter_content(self, article_body: BeautifulSoup) -> str:
        for tag in article_body(self.config_dict.get('filter_list')):
            tag.decompose()
        return article_body.get_text(separator="\n").strip()

    def download_images(self, article_body: BeautifulSoup) -> List[str]:
        images = article_body.find_all('img')
        img_urls = []
        for img in images:
            img_url = urljoin(self.config_dict['file_path'], img.get('src'))
            if self.is_valid_url(img_url):
                img_file_name = "".join([char for char in img_url if char in string.ascii_letters + string.digits])[-255:]
                img_file_path = os.path.join(self.config_dict['images_save_path'], img_file_name)
                self.download_images(img_url, img_file_path)
                img_urls.append(img_file_path)
        return img_urls

    def extract_and_filter(self, soup: BeautifulSoup) -> Dict[str, str]:
        article_body = self.extract_article(soup)
        filtered_content = self.filter_content(article_body)
        img_urls = self.download_images(article_body)
        return {"text": filtered_content, "images": img_urls}

    def extract_article_content(self, article_link: str) -> Optional[Dict[str, str]]:
        try:
            soup = self._make_request(article_link)
            return self.extract_and_filter(soup)
        except Exception as e:
            logger.error(f"Error while extracting content from {article_link}. Error message: {e}")
            return None

    def write_article_content(self, article_content: Dict[str, str], file_name: str) -> None:
        with open(file_name, 'w', encoding='utf-8') as f:
            f.write(article_content['text'])
            f.write("\n")
            for img_path in article_content['images']:
                f.write(img_path)
                f.write("\n")

    def scrape(self):
        article_links = self.get_article_links()
        if not article_links:
            logger.error("No valid links to scrape.")
            return

        with ThreadPoolExecutor(max_workers=self.thread_num) as executor:
            futures = {executor.submit(self.extract_article_content, link): link for link in article_links}
            for future in as_completed(futures):
                article_link = futures[future]
                try:
                    article_content = future.result()
                    if article_content:
                        file_name = "".join([char for char in article_link if char in string.ascii_letters + string.digits])[-255:] + '.txt'
                        file_path = os.path.join(self.config_dict['save_path'], file_name)
                        self.write_article_content(article_content, file_path)
                        logger.info(f"Successfully scraped and saved article content from {article_link}.")
                except Exception as e:
                    logger.error(f"Error while scraping {article_link}. Error message: {e}")

if __name__ == "__main__":
    scraper = WebScraper('config.ini')
    scraper.scrape()

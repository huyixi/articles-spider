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

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
ua = UserAgent()

class WebScraper:
    def __init__(self, config_file: str):
        self.config_dict = self._read_config_file(config_file)
        self.thread_num = self.config_dict.get('thread_num', 10)
        self.headers = self.config_dict.get('headers', {})
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
            'links_path': config.get('path', 'scrape_links_path', fallback=None),
            'file_save_path': config.get('path', 'file_save_path', fallback=None),
            'failed_links_path': config.get('path', 'failed_links_path', fallback=None), 
            'article_selector': config.get('filter', 'article', fallback=None),
            'filter_list': config.get('filter', 'filter_list', fallback='').split(','),
            'images_save_path': config.get('path', 'images_save_path', fallback=None),
            'thread_num': config.getint('request', 'thread_num', fallback=10),  
            'min_sleep_time': config.getfloat('request', 'min_sleep_time', fallback=1),
            'max_sleep_time': config.getfloat('request', 'max_sleep_time', fallback=3),
            'headers': dict(config.items('headers')) if config.has_section('headers') else {}
        }
        if config.has_option('request', 'proxies'):
            config_dict['proxies'] = config.get('request', 'proxies').split(',')
        
        return config_dict


    def get_article_links(self):
        try:
            with open(self.config_dict['links_path'], 'r', encoding='utf-8') as f:
                return [link.strip() for link in f.readlines() if self.is_valid_url(link.strip())]
        except FileNotFoundError as e:
            logger.error(f"File {self.config_dict['links_path']} not found: {e}")
            return []
    
    @staticmethod
    def get_random_sleep_time(min_time, max_time):
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
        article_body = soup.select_one(self.config_dict.get('article_selector'))
        return article_body

    def filter_content(self, article_body: BeautifulSoup) -> BeautifulSoup:
        for selector in self.config_dict.get('filter_list'):
            for unwanted_tag in article_body.select(selector):
                unwanted_tag.decompose()
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
            logger.error(f"Error while downloading image from {img_url}. Error message: {e}")


    def download_images(self, article_body: BeautifulSoup) -> List[str]:
        images = article_body.find_all('img')
        img_urls = []
        for img in images:
            img_url = urljoin(self.config_dict['links_path'], img.get('src'))
            if self.is_valid_url(img_url):
                parsed = urlparse(img_url)
                img_file_name = os.path.basename(unquote(parsed.path))
                img_file_path = os.path.join(self.config_dict['images_save_path'], img_file_name)
                self.download_image(img_url, img_file_path)
                img_urls.append(img_file_path)
                img['src'] = img_file_path  # Update the src attribute to local path
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


    def scrape(self):
        article_links = self.get_article_links()
        if not article_links:
            logger.error("No valid links to scrape.")
            return

        article_contents = []  # add a list to store the article contents

        with ThreadPoolExecutor(max_workers=self.thread_num) as executor:
            futures = {executor.submit(self.extract_article_content, link): link for link in article_links}
            for future in as_completed(futures):
                article_link = futures[future]
                try:
                    article_content = future.result()
                    if article_content:
                        file_name = "".join([char for char in article_link if char in string.ascii_letters + string.digits])[-255:] + '.txt'
                        file_path = os.path.join(self.config_dict['file_save_path'], file_name)
                        self.write_article_content(article_content, file_path)
                        logger.info(f"Successfully scraped and saved article content from {article_link}.")
                        article_contents.append(article_content)  # add the content to the list
                except Exception as e:
                    logger.error(f"Error while scraping {article_link}. Error message: {e}")

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
    
        os.makedirs(self.config_dict['file_save_path'], exist_ok=True)  # Create the directory if it does not exist
    
        html_file_path = os.path.join(self.config_dict['file_save_path'], 'all_articles.html')
        with open(html_file_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        logger.info(f"Successfully saved all article contents to {html_file_path}.")


    def test_proxy(self, proxy):
        url = "https://www.google.com"
        try:
            response = requests.get(url, proxies={"http": proxy, "https": proxy}, timeout=3)
            response.raise_for_status()
            return True
        except Exception:
            return False

    def test_proxies(self, proxies):
        valid_proxies = []
        for proxy in proxies:
            if self.test_proxy(proxy):
                valid_proxies.append(proxy)
        return valid_proxies


if __name__ == "__main__":
    scraper = WebScraper('config.ini')
    scraper.scrape()

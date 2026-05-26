# -*- coding: utf-8 -*-
"""
维普期刊爬虫 - 单位IP直连版

本工具支持单位IP直连维普的场景，自动下载可获取的文章PDF。
遇到需付费的文章自动跳过，不尝试任何绕过付费验证的操作。
仅供学术研究和个人学习使用，严禁用于商业用途和大规模数据采集。
"""

import os
import sys
import re
import json
import time
import random
import argparse
import traceback
import csv
import threading
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

try:
    from DrissionPage import ChromiumPage, ChromiumOptions
    DRISSION_AVAILABLE = True
except ImportError:
    DRISSION_AVAILABLE = False

if sys.platform == "win32":
    os.system("chcp 65001 >nul")

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = "https://www.cqvip.com"
QIKAN_BASE = "https://qikan.cqvip.com"
SEARCH_URL = f"{QIKAN_BASE}/Qikan/Search/Index"

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.130 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.6167.85 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.6261.94 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.6312.58 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.60 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.6422.60 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.6478.57 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.6533.72 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.6613.85 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.6668.70 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.6723.58 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.6778.86 Safari/537.36',
]

# 请求延迟范围（秒），用于模拟人类操作间隔，避免触发反爬
MIN_DELAY = 0.5
MAX_DELAY = 1.5
# 请求最大重试次数
MAX_RETRY = 5
# 每爬取N篇文章自动保存一次CSV，防止数据丢失
BATCH_SAVE_INTERVAL = 10
# 日志文件最大大小（5MB），超过后自动截断保留后半部分
LOG_MAX_SIZE = 5 * 1024 * 1024
# 每累计N次请求后触发一次长延迟，降低被限流的风险
RATE_LIMIT_THRESHOLD = 60
# 长延迟的时长范围（秒）
RATE_LIMIT_DELAY_MIN = 10
RATE_LIMIT_DELAY_MAX = 20

DOWNLOAD_MAX_RETRY = 2
DOWNLOAD_WAIT_TIMEOUT = 60
LARGE_FILE_WAIT_TIMEOUT = 120


class Logger:
    """统一日志管理器，支持控制台彩色输出和文件持久化

    提供不同级别的日志方法（info/success/error/warning/download/stats/skip），
    每条日志同时输出到控制台和追加写入日志文件。
    内置日志轮转机制，当日志文件超过指定大小时自动截断保留后半部分。
    使用线程锁保证多线程环境下文件写入的安全性。
    """
    LOG_FILE = 'cqvip_scraper.log'
    _lock = threading.Lock()

    @staticmethod
    def _rotate_log():
        """日志轮转：当日志文件超过LOG_MAX_SIZE时，截断保留后半部分内容"""
        try:
            log_path = Path(Logger.LOG_FILE)
            if log_path.exists() and log_path.stat().st_size > LOG_MAX_SIZE:
                content = log_path.read_text(encoding='utf-8', errors='ignore')
                keep = content[-(LOG_MAX_SIZE // 2):]
                log_path.write_text(keep, encoding='utf-8')
        except Exception:
            pass

    @staticmethod
    def _fmt(emoji, msg):
        """格式化并输出日志，同时写入控制台和日志文件

        Args:
            emoji: 日志级别对应的emoji图标
            msg: 日志消息内容
        """
        ts = datetime.now().strftime('%H:%M:%S')
        full_ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        line = f"[{ts}] {emoji} {msg}"
        print(line, flush=True)
        with Logger._lock:
            try:
                Logger._rotate_log()
                with open(Logger.LOG_FILE, 'a', encoding='utf-8') as f:
                    f.write(f"[{full_ts}] {emoji} {msg}\n")
            except Exception:
                pass

    @staticmethod
    def info(msg):
        Logger._fmt('📥', msg)

    @staticmethod
    def success(msg):
        Logger._fmt('✅', msg)

    @staticmethod
    def error(msg):
        Logger._fmt('❌', msg)

    @staticmethod
    def warning(msg):
        Logger._fmt('⚠️', msg)

    @staticmethod
    def download(msg):
        Logger._fmt('⬇️', msg)

    @staticmethod
    def stats(msg):
        Logger._fmt('📊', msg)

    @staticmethod
    def skip(msg):
        Logger._fmt('⏭️', msg)


class CqvipSession:
    """维普HTTP会话管理器，封装requests.Session并提供反爬策略

    职责：
    - 管理HTTP请求的会话、重试策略和请求头随机化
    - 从浏览器同步Cookie以保持登录态
    - 对齐服务器时间用于签名计算
    - 生成HMAC和DES签名用于接口鉴权
    - 实现智能请求延迟，避免触发反爬限流
    """
    def __init__(self):
        self.session = requests.Session()
        self.request_count = 0
        self.last_rate_limit_time = 0
        self._setup_session()

    def _setup_session(self):
        """配置会话的重试策略和初始请求头"""
        retry_strategy = Retry(
            total=MAX_RETRY,
            backoff_factor=2,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "POST"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.update(self._random_headers())

    def _random_headers(self):
        """生成随机化的请求头，每次请求更换User-Agent以降低被识别的风险"""
        return {
            'User-Agent': random.choice(USER_AGENTS),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Referer': 'https://qikan.cqvip.com/',
        }

    def sync_cookies_from_browser(self, browser):
        """从浏览器实例同步Cookie到requests会话，保持登录态一致

        Args:
            browser: DrissionPage的浏览器实例，支持as_dict和all两种Cookie接口
        """
        try:
            cookies_obj = browser.cookies()
            count = 0
            if hasattr(cookies_obj, 'as_dict'):
                cookies_dict = cookies_obj.as_dict()
                for name, value in cookies_dict.items():
                    if name and value:
                        self.session.cookies.set(name, value)
                        count += 1
            elif hasattr(cookies_obj, 'all'):
                for cookie in cookies_obj.all():
                    try:
                        if hasattr(cookie, 'name') and hasattr(cookie, 'value'):
                            self.session.cookies.set(cookie.name, cookie.value)
                            count += 1
                    except Exception:
                        pass
            if count > 0:
                Logger.success(f"已同步 {count} 个Cookie")
        except Exception as e:
            Logger.warning(f"同步Cookie失败: {e}")

    def smart_delay(self, min_d=None, max_d=None):
        """智能请求延迟，模拟人类操作节奏并定期触发长延迟以规避限流

        每次调用递增请求计数，当计数达到RATE_LIMIT_THRESHOLD时
        自动触发一次较长的休眠（RATE_LIMIT_DELAY_MIN ~ RATE_LIMIT_DELAY_MAX秒）。

        Args:
            min_d: 自定义最小延迟秒数，默认使用MIN_DELAY
            max_d: 自定义最大延迟秒数，默认使用MAX_DELAY
        """
        delay_min = min_d if min_d is not None else MIN_DELAY
        delay_max = max_d if max_d is not None else MAX_DELAY
        delay = random.uniform(delay_min, delay_max)
        time.sleep(delay)
        self.request_count += 1
        if self.request_count % RATE_LIMIT_THRESHOLD == 0:
            long_delay = random.uniform(RATE_LIMIT_DELAY_MIN, RATE_LIMIT_DELAY_MAX)
            Logger.info(f"请求计数 {self.request_count}，长延迟 {long_delay:.0f}秒")
            time.sleep(long_delay)

    def get(self, url, **kwargs):
        """发送GET请求，自动刷新请求头并设置默认超时和SSL验证

        Args:
            url: 请求URL
            **kwargs: 传递给requests.Session.get的额外参数

        Returns:
            requests.Response对象
        """
        self.session.headers.update(self._random_headers())
        timeout = kwargs.pop('timeout', 30)
        verify = kwargs.pop('verify', False)
        return self.session.get(url, timeout=timeout, verify=verify, **kwargs)

    def post(self, url, data=None, json_data=None, **kwargs):
        """发送POST请求，自动刷新请求头并设置默认超时和SSL验证

        Args:
            url: 请求URL
            data: 表单数据
            json_data: JSON数据
            **kwargs: 传递给requests.Session.post的额外参数

        Returns:
            requests.Response对象
        """
        self.session.headers.update(self._random_headers())
        timeout = kwargs.pop('timeout', 30)
        verify = kwargs.pop('verify', False)
        return self.session.post(url, data=data, json=json_data, timeout=timeout, verify=verify, **kwargs)


class BrowserManager:
    """浏览器实例管理器，基于DrissionPage封装Chromium浏览器操作

    职责：
    - 初始化和配置Chromium浏览器（反检测、下载路径、无图模式等）
    - 自动检测系统中已安装的Chrome/Edge浏览器路径
    - 提供页面访问、标签页管理、下载状态检测等浏览器操作
    - 支持浏览器断线重连
    - 引导用户手动完成维普登录
    """
    def __init__(self, download_dir=None):
        self.browser = None
        self._initialized = False
        self.download_dir = download_dir
        self._options = None

    def init(self):
        """初始化浏览器实例，配置反检测参数、下载路径和浏览器路径

        Returns:
            True表示初始化成功，False表示初始化失败
        """
        if not DRISSION_AVAILABLE:
            Logger.error("DrissionPage未安装！请运行: pip install DrissionPage")
            return False

        try:
            options = ChromiumOptions()
            options.set_argument('--disable-blink-features=AutomationControlled')
            options.set_argument('--no-sandbox')
            options.set_argument('--disable-dev-shm-usage')
            options.set_argument('--disable-gpu')
            options.set_argument('--disable-infobars')
            options.set_argument('--disable-extensions')
            options.set_argument('--window-size=1920,1080')
            options.set_argument('--disable-features=DownloadBubble')
            options.set_argument('--no-first-run')
            options.set_argument('--disable-background-networking')

            options.set_pref('profile.default_content_setting_values.images', 2)  # 禁用图片加载以加速页面渲染

            if self.download_dir:
                download_path = os.path.abspath(str(self.download_dir))
                os.makedirs(download_path, exist_ok=True)
                
                # 设置下载路径（兼容Chrome和Edge）
                options.set_download_path(download_path)
                
                # Chrome/Edge下载设置
                options.set_pref('download.default_directory', download_path)
                options.set_pref('download.prompt_for_download', False)
                options.set_pref('download.directory_upgrade', True)
                options.set_pref('plugins.always_open_pdf_externally', True)
                
                # Edge浏览器特定设置
                options.set_pref('edge.download.default_directory', download_path)
                options.set_pref('edge.download.prompt_for_download', False)
                
                # 确保下载路径使用正斜杠
                download_path_slash = download_path.replace('\\', '/')
                options.set_pref('download.default_directory', download_path_slash)
                
                Logger.info(f"下载目录: {download_path}")

            Logger.info("正在检测浏览器...")

            browser_paths = [
                r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
                r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
                os.path.expandvars(r'%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe'),
                r'C:\Program Files\Google\Chrome\Application\chrome.exe',
                r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
                os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe'),
            ]
            for p in browser_paths:
                if os.path.exists(p):
                    options.set_browser_path(p)
                    Logger.info(f"使用浏览器: {p}")
                    break
            else:
                Logger.warning("未在常见路径找到Chrome/Edge，尝试系统自动检测...")

            self._options = options
            self.browser = ChromiumPage(options)
            self._initialized = True
            Logger.success("浏览器初始化成功")
            return True
        except Exception as e:
            Logger.error(f"浏览器初始化失败: {e}")
            return False

    def reconnect(self):
        """尝试重新连接浏览器，先检测当前连接是否可用，不可用则重新创建实例

        Returns:
            True表示重连成功，False表示重连失败
        """
        try:
            if self.browser:
                try:
                    _ = self.browser.url
                    return True
                except Exception:
                    pass

            Logger.warning("浏览器连接断开，尝试重新连接...")
            self._initialized = False
            if self._options:
                try:
                    self.browser = ChromiumPage(self._options)
                    self._initialized = True
                    Logger.success("浏览器重新连接成功")
                    return True
                except Exception as e:
                    Logger.error(f"浏览器重连失败: {e}")
            return self.init()
        except Exception:
            return False

    def get(self, url, wait_seconds=None):
        """使用浏览器访问指定URL并返回页面HTML

        Args:
            url: 目标页面URL
            wait_seconds: 页面加载后等待秒数，默认随机1~2秒

        Returns:
            页面HTML字符串，访问失败返回None
        """
        if not self.browser:
            return None
        try:
            self.browser.get(url)
            if wait_seconds is not None:
                time.sleep(wait_seconds)
            else:
                time.sleep(random.uniform(1, 2))
            return self.browser.html
        except Exception as e:
            Logger.error(f"浏览器访问失败: {e}")
            return None

    def close_extra_tabs(self):
        """关闭除第一个标签页之外的所有多余标签页，防止标签页泄漏"""
        try:
            if not self.browser:
                return
            while self.browser.tabs_count > 1:
                tab_ids = self.browser.tab_ids
                for tid in tab_ids[1:]:
                    try:
                        self.browser.close_tabs(tid)
                    except Exception:
                        pass
                time.sleep(0.5)
        except Exception:
            pass

    def is_downloading(self):
        """检测下载目录中是否存在未完成的临时文件（.crdownload/.tmp/.part）

        Returns:
            True表示有文件正在下载，False表示无下载中的文件
        """
        if not self.download_dir:
            return False
        try:
            for f in Path(self.download_dir).iterdir():
                if f.suffix in ['.crdownload', '.tmp', '.part']:
                    return True
        except Exception:
            pass
        return False

    def wait_for_download(self, max_wait=60):
        """等待下载完成，每秒检测一次临时文件是否消失

        Args:
            max_wait: 最大等待秒数

        Returns:
            True表示下载完成，False表示等待超时
        """
        for _ in range(max_wait):
            if not self.is_downloading():
                return True
            time.sleep(1)
        return False

    def wait_for_user_login(self):
        """等待用户手动完成维普登录的方法

        流程：
        1. 打开维普首页，为用户提供登录入口
        2. 在控制台打印提示信息引导用户手动登录
        3. 等待用户按Enter键确认登录完成
        4. 返回True表示用户已确认完成登录操作

        注意：本方法只负责引导和等待，不做任何自动登录操作
        """
        if not self.browser:
            return False

        try:
            Logger.info("正在打开维普期刊网站，请在此页面完成登录...")
            self.browser.get("https://qikan.cqvip.com")
            time.sleep(3)

            print("\n" + "=" * 60)
            print("  🔐 维普账号登录引导")
            print("=" * 60)
            print("  请在浏览器中完成维普账号的登录操作：")
            print("  1. 点击浏览器页面右上角的「登录」按钮")
            print("  2. 输入您的维普账号和密码完成登录")
            print("  3. 登录成功后，回到此控制台按 Enter 键确认")
            print("=" * 60)

            try:
                input("\n  ✋ 登录完成后请按 Enter 键继续...")
            except (KeyboardInterrupt, EOFError):
                print("\n  用户取消登录，程序退出")
                return False

            Logger.info("用户已确认完成登录操作")
            return True

        except Exception as e:
            Logger.error(f"登录引导过程出错: {e}")
            return False

    def close(self):
        """关闭浏览器实例并重置状态"""
        if self.browser:
            try:
                self.browser.quit()
                Logger.info("浏览器已关闭")
            except Exception:
                pass
            finally:
                self.browser = None
                self._initialized = False


class DataManager:
    """数据持久化管理器，负责CSV存储、进度保存和去重检测

    职责：
    - 管理输出目录结构（CSV文件、文章目录、进度文件）
    - 从历史CSV和进度文件中加载已爬取记录，实现断点续爬
    - 基于URL和标题的去重检测，避免重复爬取
    - 增量保存CSV记录和爬取进度
    - 生成唯一的文件名，避免文件名冲突
    """
    def __init__(self, keyword, output_dir=None):
        self.keyword = self._clean_name(keyword)
        self.output_dir = Path(output_dir or self.keyword)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.csv_file = self.output_dir / f'{self.keyword}_维普搜索结果.csv'
        self.articles_dir = self.output_dir / 'articles'
        self.articles_dir.mkdir(parents=True, exist_ok=True)
        self.progress_file = self.output_dir / f'{self.keyword}_cqvip_progress.json'
        self.fields = ['序号', '标题', '作者', '期刊名称', '发表时间', '摘要', '关键词',
                       'DOI', '机构', '基金', '分类号', '详情链接', '下载状态', '爬取时间']
        self.existing_urls = set()
        self.existing_titles = set()
        self._load_existing()

    @staticmethod
    def _clean_name(name):
        """清理文件名中的非法字符，截断过长名称

        Args:
            name: 原始名称字符串

        Returns:
            清理后的安全文件名字符串，最长100字符
        """
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(name))
        name = re.sub(r'_+', '_', name)
        return name.strip('_ ')[:100] or 'untitled'

    def _load_existing(self):
        """从已有的CSV文件和进度文件中加载历史记录，用于去重和断点续爬"""
        if self.csv_file.exists():
            try:
                with open(self.csv_file, 'r', encoding='utf-8-sig') as f:
                    for row in csv.DictReader(f):
                        url = row.get('详情链接', '')
                        title = row.get('标题', '')
                        if url:
                            self.existing_urls.add(url)
                        if title:
                            self.existing_titles.add(title)
                Logger.info(f"已加载 {len(self.existing_urls)} 条历史记录")
            except Exception:
                pass

        if self.progress_file.exists():
            try:
                data = json.loads(self.progress_file.read_text(encoding='utf-8'))
                for url in data.get('crawled_urls', []):
                    self.existing_urls.add(url)
                for title in data.get('crawled_titles', []):
                    self.existing_titles.add(title)
            except Exception:
                pass

    def is_duplicate(self, url, title=''):
        """检测文章是否已存在于历史记录中（基于URL或标题去重）

        Args:
            url: 文章详情链接
            title: 文章标题

        Returns:
            True表示重复，False表示未重复
        """
        if url and url in self.existing_urls:
            return True
        if title and title in self.existing_titles:
            return True
        return False

    def is_article_downloaded(self, title):
        """检测文章对应的文件是否已存在于下载目录中

        Args:
            title: 文章标题

        Returns:
            True表示文件已存在，False表示未下载
        """
        safe = self._clean_name(title)
        try:
            for f in self.articles_dir.iterdir():
                if f.stem.startswith(safe) and f.suffix in ['.pdf', '.caj', '.html']:
                    return True
        except Exception:
            pass
        return False

    def is_article_saved(self, title):
        """检测文章是否已保存（兼容接口，内部调用is_article_downloaded）"""
        return self.is_article_downloaded(title)

    def unique_html_filename(self, title):
        """生成唯一的HTML文件名，若文件已存在则追加序号避免覆盖

        Args:
            title: 文章标题

        Returns:
            不与现有文件冲突的HTML文件名
        """
        safe = self._clean_name(title)
        fn = f"{safe}.html"
        c = 1
        while (self.articles_dir / fn).exists():
            fn = f"{safe}_{c}.html"
            c += 1
        return fn

    def unique_article_filename(self, title, ext='pdf'):
        """生成唯一的文章文件名，若文件已存在则追加序号避免覆盖

        Args:
            title: 文章标题
            ext: 文件扩展名，默认为pdf

        Returns:
            不与现有文件冲突的文件名
        """
        safe = self._clean_name(title)
        fn = f"{safe}.{ext}"
        c = 1
        while (self.articles_dir / fn).exists():
            fn = f"{safe}_{c}.{ext}"
            c += 1
        return fn

    def save_csv(self, records, mode='append'):
        """将文章记录保存到CSV文件，支持新建写入和追加写入

        Args:
            records: 文章记录列表，每条记录为字典
            mode: 写入模式，'write'为新建覆盖，'append'为追加
        """
        try:
            rows = []
            for r in records:
                row = {}
                for field in self.fields:
                    row[field] = r.get(field, '')
                rows.append(row)

            if mode == 'write' or not self.csv_file.exists():
                with open(self.csv_file, 'w', encoding='utf-8-sig', newline='') as f:
                    w = csv.DictWriter(f, fieldnames=self.fields)
                    w.writeheader()
                    w.writerows(rows)
            else:
                with open(self.csv_file, 'a', encoding='utf-8-sig', newline='') as f:
                    w = csv.DictWriter(f, fieldnames=self.fields)
                    w.writerows(rows)

            for r in records:
                url = r.get('详情链接', '')
                title = r.get('标题', '')
                if url:
                    self.existing_urls.add(url)
                if title:
                    self.existing_titles.add(title)

            Logger.success(f"CSV已保存: {self.csv_file}")
        except Exception as e:
            Logger.error(f"保存CSV失败: {e}")

    def save_progress(self, crawled_urls, crawled_titles, page_num):
        """保存爬取进度到JSON文件，用于断点续爬

        Args:
            crawled_urls: 已爬取的URL集合
            crawled_titles: 已爬取的标题集合
            page_num: 当前页码
        """
        try:
            data = {
                'keyword': self.keyword,
                'crawled_urls': list(crawled_urls),
                'crawled_titles': list(crawled_titles),
                'page_num': page_num,
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            }
            self.progress_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception as e:
            Logger.error(f"保存进度失败: {e}")

    def load_progress(self):
        """从进度文件加载上次爬取的断点信息

        Returns:
            包含爬取进度信息的字典，文件不存在或读取失败返回None
        """
        if not self.progress_file.exists():
            return None
        try:
            return json.loads(self.progress_file.read_text(encoding='utf-8'))
        except Exception:
            return None


class CqvipParser:
    """维普页面解析器，负责从搜索结果页和文章详情页提取结构化数据

    解析策略按优先级依次尝试：
    1. NUXT服务端渲染数据（__NUXT_DATA__标签或application/json脚本）
    2. CSS选择器匹配文章列表项
    3. HTML表格结构解析
    4. 兜底：通过详情链接提取基本信息
    """
    @staticmethod
    def parse_search_results(html_content):
        """解析搜索结果页HTML，按优先级尝试多种解析策略提取文章列表

        Args:
            html_content: 搜索结果页的HTML字符串

        Returns:
            文章信息字典列表，每条包含标题、作者、期刊名称等字段
        """
        results = []
        if not html_content:
            return results

        soup = BeautifulSoup(html_content, 'lxml')

        nuxt_data = CqvipParser._extract_nuxt_data(soup)
        if nuxt_data:
            results = CqvipParser._parse_nuxt_results(nuxt_data)
            if results:
                return results

        article_items = soup.select(
            'div.article-item, li.article-list-item, div.list-item, '
            'div.search-result-item, div.result-item, tr[data-id]'
        )

        for item in article_items:
            article = CqvipParser._parse_article_item(item)
            if article and article.get('标题'):
                results.append(article)

        if not results:
            results = CqvipParser._parse_table_results(soup)

        # 兜底解析：通过文章详情链接提取文章信息
        if not results:
            results = CqvipParser._parse_by_detail_links(soup)

        return results

    @staticmethod
    def _extract_nuxt_data(soup):
        """从页面中提取NUXT服务端渲染的JSON数据

        优先查找id为__NUXT_DATA__的script标签，其次查找
        type为application/json且包含article关键字的script标签。

        Args:
            soup: BeautifulSoup对象

        Returns:
            解析后的JSON数据（dict/list），未找到返回None
        """
        try:
            script = soup.find('script', id='__NUXT_DATA__')
            if script:
                return json.loads(script.string)
        except Exception:
            pass

        try:
            for script in soup.find_all('script', type='application/json'):
                if script.string and 'article' in script.string.lower():
                    return json.loads(script.string)
        except Exception:
            pass

        return None

    @staticmethod
    def _parse_nuxt_results(nuxt_data):
        """解析NUXT格式的搜索结果数据，提取文章基本信息

        Args:
            nuxt_data: 从__NUXT_DATA__中提取的JSON数据

        Returns:
            文章信息字典列表
        """
        results = []
        try:
            if isinstance(nuxt_data, dict):
                articles = nuxt_data.get('data', nuxt_data).get('list', nuxt_data.get('articles', []))
            elif isinstance(nuxt_data, list):
                articles = nuxt_data
            else:
                return results

            for item in articles:
                if not isinstance(item, dict):
                    continue
                article = {
                    '标题': item.get('title', item.get('name', '')),
                    '作者': item.get('author', item.get('authors', '')),
                    '期刊名称': item.get('journalName', item.get('journal', '')),
                    '发表时间': item.get('publishDate', item.get('date', '')),
                    '摘要': item.get('abstract', ''),
                    '关键词': item.get('keywords', ''),
                }
                detail_id = item.get('id', item.get('articleId', ''))
                if detail_id:
                    article['详情链接'] = f"{QIKAN_BASE}/Qikan/Article/Detail?id={detail_id}"
                if article['标题']:
                    results.append(article)
        except Exception:
            pass
        return results

    @staticmethod
    def _parse_article_item(item):
        """解析单个文章列表项DOM元素，提取标题、作者、期刊、日期等

        Args:
            item: BeautifulSoup标签对象，代表一个搜索结果条目

        Returns:
            文章信息字典，至少包含'标题'字段
        """
        article = {}

        title_link = item.find('a', class_='title') or item.find('a', class_='article-title')
        if not title_link:
            title_elem = item.find('h3') or item.find('h4') or item.find('h2')
            if title_elem:
                title_link = title_elem.find('a')
        if not title_link:
            all_links = item.find_all('a', href=True)
            for link in all_links:
                href = link.get('href', '')
                if '/Qikan/Article/Detail' in href or '/doc/journal/' in href:
                    title_link = link
                    break

        if title_link:
            article['标题'] = title_link.get_text(strip=True)
            href = title_link.get('href', '')
            if href.startswith('//'):
                article['详情链接'] = 'https:' + href
            elif href.startswith('/'):
                article['详情链接'] = QIKAN_BASE + href
            elif not href.startswith('http'):
                article['详情链接'] = QIKAN_BASE + '/' + href
            else:
                article['详情链接'] = href
        else:
            article['标题'] = item.get_text(strip=True)[:100]

        author_selectors = [
            item.find('div', class_='author'),
            item.find('span', class_='author'),
            item.find('p', class_='author'),
            item.find('div', class_='detail-author'),
        ]
        for author_elem in author_selectors:
            if author_elem:
                author_links = author_elem.find_all('a')
                if author_links:
                    authors = [a.get_text(strip=True) for a in author_links
                               if a.get_text(strip=True) != article.get('标题', '')]
                    if authors:
                        article['作者'] = '; '.join(authors)
                else:
                    text = author_elem.get_text(strip=True)
                    text = re.sub(r'^作者[：:]\s*', '', text)
                    if text and text != article.get('标题', ''):
                        article['作者'] = text
                break

        source_elem = (item.find('span', class_='source') or
                       item.find('div', class_='journal-name') or
                       item.find('span', class_='journal'))
        if source_elem:
            article['期刊名称'] = source_elem.get_text(strip=True)

        date_elem = (item.find('span', class_='date') or
                     item.find('span', class_='pub-date') or
                     item.find('span', class_='time'))
        if date_elem:
            article['发表时间'] = date_elem.get_text(strip=True)

        return article

    @staticmethod
    def _parse_table_results(soup):
        """解析表格形式的搜索结果，通过表头映射列索引提取文章信息

        Args:
            soup: BeautifulSoup对象

        Returns:
            文章信息字典列表
        """
        results = []
        tables = soup.find_all('table')
        for table in tables:
            rows = table.find_all('tr')
            if len(rows) <= 2:
                continue

            col_map = {}
            header_row = rows[0]
            for i, cell in enumerate(header_row.find_all(['th', 'td'])):
                text = cell.get_text(strip=True)
                if '篇名' in text or '标题' in text or '题名' in text:
                    col_map['title'] = i
                elif '作者' in text:
                    col_map['author'] = i
                elif '来源' in text or '期刊' in text:
                    col_map['source'] = i
                elif '时间' in text or '发表' in text or '日期' in text:
                    col_map['date'] = i

            if 'title' not in col_map:
                continue

            for row in rows[1:]:
                cells = row.find_all('td')
                if len(cells) <= col_map.get('title', 0):
                    continue

                article = {}
                title_cell = cells[col_map['title']]
                link = title_cell.find('a', href=True)
                if link:
                    article['标题'] = link.get_text(strip=True)
                    href = link.get('href', '')
                    if href.startswith('//'):
                        article['详情链接'] = 'https:' + href
                    elif href.startswith('/'):
                        article['详情链接'] = QIKAN_BASE + href
                    else:
                        article['详情链接'] = href
                else:
                    article['标题'] = title_cell.get_text(strip=True)

                if 'author' in col_map and col_map['author'] < len(cells):
                    author_text = cells[col_map['author']].get_text(strip=True)
                    if author_text != article.get('标题', ''):
                        article['作者'] = author_text

                if 'source' in col_map and col_map['source'] < len(cells):
                    article['期刊名称'] = cells[col_map['source']].get_text(strip=True)

                if 'date' in col_map and col_map['date'] < len(cells):
                    article['发表时间'] = cells[col_map['date']].get_text(strip=True)

                if article.get('标题'):
                    results.append(article)
        return results

    @staticmethod
    def _parse_by_detail_links(soup):
        """兜底解析：通过文章详情链接提取文章信息

        当其他解析方式都失败时，直接查找所有指向文章详情页的链接
        维普搜索结果页的文章链接格式：/Qikan/Article/Detail?id=xxxx
        """
        results = []
        seen_urls = set()

        detail_links = soup.find_all('a', href=re.compile(r'/Qikan/Article/Detail\?id='))
        for link in detail_links:
            href = link.get('href', '')
            if href in seen_urls:
                continue
            seen_urls.add(href)

            title = link.get_text(strip=True)
            if not title or len(title) < 2:
                continue

            article = {'标题': title}

            if href.startswith('//'):
                article['详情链接'] = 'https:' + href
            elif href.startswith('/'):
                article['详情链接'] = QIKAN_BASE + href
            elif not href.startswith('http'):
                article['详情链接'] = QIKAN_BASE + '/' + href
            else:
                article['详情链接'] = href

            # 尝试从父元素中提取作者和期刊信息
            try:
                parent = link.parent
                for _ in range(3):
                    if parent:
                        parent = parent.parent
                    else:
                        break

                if parent:
                    # 提取作者
                    author_elems = parent.find_all('a', href=re.compile(r'key=A%3d|key=A='))
                    if author_elems:
                        authors = [a.get_text(strip=True) for a in author_elems if a.get_text(strip=True)]
                        if authors:
                            article['作者'] = '; '.join(authors)

                    # 提取期刊名称
                    journal_elem = parent.find('a', href=re.compile(r'/Qikan/Journal/'))
                    if journal_elem:
                        article['期刊名称'] = journal_elem.get_text(strip=True)
            except Exception:
                pass

            results.append(article)

        return results

    @staticmethod
    def parse_detail_page(html_content, url=''):
        """解析文章详情页HTML，提取完整的文章元数据

        Args:
            html_content: 详情页HTML字符串
            url: 详情页URL，用于记录来源

        Returns:
            文章详细信息字典，包含标题、作者、摘要、关键词、DOI、机构、基金、分类号等
        """
        data = {}
        if not html_content:
            return data

        soup = BeautifulSoup(html_content, 'lxml')

        title = (soup.find('h1', class_='title') or
                 soup.find('h1', class_='article-title') or
                 soup.find('h1'))
        if title:
            data['标题'] = title.get_text(strip=True)

        title_text = data.get('标题', '')

        author_section = (soup.find('div', class_='author-list') or
                          soup.find('p', class_='author') or
                          soup.find('div', class_='detail-author') or
                          soup.find('div', class_='authors'))
        if author_section:
            author_links = author_section.find_all('a')
            if author_links:
                authors = [a.get_text(strip=True) for a in author_links
                           if a.get_text(strip=True) and a.get_text(strip=True) != title_text]
                if authors:
                    data['作者'] = '; '.join(authors)
            else:
                text = author_section.get_text(strip=True)
                text = re.sub(r'^作者[：:]\s*', '', text)
                if text and text != title_text:
                    data['作者'] = text

        abstract = (soup.find('div', class_='abstract') or
                    soup.find('div', id='ChDivSummary') or
                    soup.find('div', class_='summary'))
        if abstract:
            text = abstract.get_text(strip=True)
            text = re.sub(r'^摘\s*要[：:]?\s*', '', text)
            text = re.sub(r'\s+', ' ', text)
            data['摘要'] = text

        kw_section = (soup.find('div', class_='keywords') or
                      soup.find('span', class_='keyword') or
                      soup.find('div', class_='keyword'))
        if kw_section:
            kw_text = kw_section.get_text(strip=True)
            kw_text = re.sub(r'^关键词[：:]?\s*', '', kw_text)
            kw_links = kw_section.find_all('a')
            if kw_links:
                kws = [a.get_text(strip=True) for a in kw_links if a.get_text(strip=True)]
                if kws:
                    kw_text = '; '.join(kws)
            data['关键词'] = kw_text

        journal = soup.find('a', href=re.compile(r'/journal/'))
        if journal:
            data['期刊名称'] = journal.get_text(strip=True)

        date_meta = soup.find('meta', {'property': 'article:published_time'})
        if date_meta:
            data['发表时间'] = date_meta.get('content', '')[:10]

        if not data.get('发表时间'):
            date_span = (soup.find('span', class_='date') or
                         soup.find('span', class_='pub-date') or
                         soup.find('span', class_='publish-time'))
            if date_span:
                data['发表时间'] = date_span.get_text(strip=True)

        doi_elem = (soup.find('div', class_='doi') or
                    soup.find('span', class_='doi') or
                    soup.find('a', href=re.compile(r'doi\.org')))
        if doi_elem:
            doi_text = doi_elem.get_text(strip=True)
            doi_text = re.sub(r'^DOI[：:]?\s*', '', doi_text)
            data['DOI'] = doi_text

        inst_section = (soup.find('div', class_='institution') or
                        soup.find('div', class_='org') or
                        soup.find('span', class_='institution'))
        if inst_section:
            data['机构'] = inst_section.get_text(strip=True)

        fund_section = (soup.find('div', class_='fund') or
                        soup.find('div', class_='foundation') or
                        soup.find('span', class_='fund'))
        if fund_section:
            fund_text = fund_section.get_text(strip=True)
            fund_text = re.sub(r'^基金[项目]*[：:]?\s*', '', fund_text)
            data['基金'] = fund_text

        cls_section = (soup.find('div', class_='cls') or
                       soup.find('span', class_='cls') or
                       soup.find('div', class_='classification'))
        if cls_section:
            cls_text = cls_section.get_text(strip=True)
            cls_text = re.sub(r'^分类号[：:]?\s*', '', cls_text)
            data['分类号'] = cls_text

        data['详情链接'] = url
        return data


class CqvipCrawler:
    """维普期刊爬虫核心控制器，协调各模块完成搜索、解析、下载全流程

    职责：
    - 管理浏览器登录和Cookie同步
    - 通过浏览器执行搜索并解析搜索结果
    - 逐篇访问文章详情页提取元数据
    - 自动下载可获取的文章PDF，需付费的自动跳过
    - 定期保存CSV和进度文件，支持断点续爬
    - 生成最终总结报告
    """
    def __init__(self, keyword, max_count=0, output_dir=None):
        self.keyword = keyword
        self.max_count = max_count
        self.session = CqvipSession()
        self.dm = DataManager(keyword, output_dir)
        self.browser_mgr = BrowserManager(download_dir=self.dm.articles_dir)
        self.records = []
        self.success_count = 0
        self.skip_count = 0
        self.pdf_count = 0
        self.download_count = 0
        self.pay_skip_count = 0
        self.page_num = 1
        self._stopped = False
        self.main_tab_id = None

    def _ensure_browser(self):
        """确保浏览器已初始化，未初始化则自动初始化

        Returns:
            True表示浏览器可用，False表示初始化失败
        """
        if not self.browser_mgr._initialized:
            if not self.browser_mgr.init():
                return False
        return True

    def _check_login_status(self):
        """检测用户是否已成功登录维普

        通过多种方式验证登录状态：
        1. 检测浏览器页面中是否存在用户头像元素
        2. 检测页面中是否存在用户名显示
        3. 检测页面中是否存在"退出登录"按钮
        4. 检测Cookie中是否存在维普登录后的关键标识

        只要满足任意一种条件即判定为登录成功
        返回：True表示登录成功，False表示未登录
        """
        browser = self.browser_mgr.browser
        if not browser:
            return False

        # 方式1：检测页面中是否存在用户头像元素
        try:
            avatar_selectors = [
                'css:img[class*="avatar"]',
                'css:img[class*="user-img"]',
                'css:img[class*="head-img"]',
                'css:img[class*="user-avatar"]',
                'css:div[class*="avatar"] img',
                'css:div[class*="user-head"] img',
                'xpath://img[contains(@src, "avatar")]',
                'xpath://img[contains(@src, "userhead")]',
                'xpath://img[contains(@src, "face")]',
            ]
            for sel in avatar_selectors:
                try:
                    elem = browser.ele(sel, timeout=2)
                    if elem:
                        Logger.success("登录检测：发现用户头像元素，判定为已登录")
                        return True
                except Exception:
                    continue
        except Exception:
            pass

        # 方式2：检测页面中是否存在用户名显示
        try:
            username_selectors = [
                'css:span[class*="user-name"]',
                'css:span[class*="username"]',
                'css:div[class*="user-name"]',
                'css:div[class*="username"]',
                'css:a[class*="user-name"]',
                'css:a[class*="nickname"]',
                'xpath://span[contains(@class, "user") and not(contains(text(), "登录")) and not(contains(text(), "注册"))]',
                'xpath://a[contains(@class, "user") and not(contains(text(), "登录")) and not(contains(text(), "注册"))]',
            ]
            for sel in username_selectors:
                try:
                    elem = browser.ele(sel, timeout=2)
                    if elem:
                        elem_text = (elem.text or '').strip()
                        # 确认不是"登录"或"注册"按钮的文字
                        if elem_text and elem_text not in ['登录', '注册', 'Login', 'Register', '']:
                            Logger.success(f"登录检测：发现用户名元素({elem_text[:15]})，判定为已登录")
                            return True
                except Exception:
                    continue
        except Exception:
            pass

        # 方式3：检测页面中是否存在"退出登录"按钮
        try:
            logout_selectors = [
                'xpath://a[contains(text(), "退出")]',
                'xpath://a[contains(text(), "退出登录")]',
                'xpath://a[contains(text(), "注销")]',
                'xpath://button[contains(text(), "退出")]',
                'xpath://a[contains(@class, "logout")]',
                'xpath://a[contains(@class, "sign-out")]',
                'xpath://a[contains(@href, "logout")]',
                'xpath://a[contains(@href, "signout")]',
                'css:a[class*="logout"]',
                'css:a[class*="sign-out"]',
            ]
            for sel in logout_selectors:
                try:
                    elem = browser.ele(sel, timeout=2)
                    if elem:
                        Logger.success("登录检测：发现退出登录按钮，判定为已登录")
                        return True
                except Exception:
                    continue
        except Exception:
            pass

        # 方式4：检测Cookie中是否存在维普登录后的关键标识
        try:
            cookies = browser.cookies()
            login_cookie_keys = [
                'login', 'token', 'sessionid', 'user_id', 'userid',
                'auth', 'sid', 'JSESSIONID', 'vip_username',
                'Hm_lvt_', 'CQVIP_USER', 'userInfo',
            ]
            if hasattr(cookies, 'as_dict'):
                cookies_dict = cookies.as_dict()
                for key in cookies_dict:
                    key_lower = key.lower()
                    if any(ck in key_lower for ck in login_cookie_keys):
                        cookie_value = cookies_dict[key]
                        if cookie_value and len(str(cookie_value)) > 3:
                            Logger.success(f"登录检测：发现登录相关Cookie({key})，判定为已登录")
                            return True
            elif hasattr(cookies, 'all'):
                for cookie in cookies.all():
                    try:
                        if hasattr(cookie, 'name'):
                            cookie_name = (cookie.name or '').lower()
                            if any(ck in cookie_name for ck in login_cookie_keys):
                                cookie_value = cookie.value or ''
                                if cookie_value and len(str(cookie_value)) > 3:
                                    Logger.success(f"登录检测：发现登录相关Cookie({cookie.name})，判定为已登录")
                                    return True
                    except Exception:
                        continue
        except Exception:
            pass

        # 方式5：检测页面HTML中是否包含登录后才有的用户信息标记
        try:
            page_html = browser.html or ''
            logged_in_patterns = [
                r'logout',
                r'退出登录',
                r'个人中心',
                r'我的收藏',
                r'我的订阅',
                r'userName',
                r'userId',
                r'isLogin\s*[:=]\s*true',
                r'isLogin\s*[:=]\s*1',
                r'isLogged\s*[:=]\s*true',
                r'hasLogin\s*[:=]\s*true',
            ]
            for pattern in logged_in_patterns:
                if re.search(pattern, page_html, re.IGNORECASE):
                    Logger.success(f"登录检测：页面包含登录后标记({pattern[:20]})，判定为已登录")
                    return True
        except Exception:
            pass

        Logger.warning("登录检测：未检测到登录标识，判定为未登录")
        return False

    def _login_before_crawl(self):
        """爬取前的登录引导和状态检测流程

        流程：
        1. 初始化浏览器
        2. 打开维普首页引导用户登录
        3. 等待用户确认完成登录
        4. 检测登录状态
        5. 登录失败时询问是否重新登录
        6. 登录成功后同步Cookie到requests会话

        返回：True表示登录成功可以继续爬取，False表示用户选择退出
        """
        if not self._ensure_browser():
            Logger.error("浏览器初始化失败，无法进行登录")
            return False

        while True:
            # 引导用户登录
            login_ok = self.browser_mgr.wait_for_user_login()
            if not login_ok:
                Logger.error("登录引导被取消")
                return False

            # 检测登录状态
            if self._check_login_status():
                Logger.success("✅ 登录验证通过，开始执行爬取任务")

                # 登录成功后同步Cookie到requests会话
                try:
                    self.session.sync_cookies_from_browser(self.browser_mgr.browser)
                except Exception as e:
                    Logger.warning(f"同步Cookie失败(不影响爬取): {e}")

                # 记录主标签页ID
                try:
                    self.main_tab_id = self.browser_mgr.browser.tab_id
                except Exception:
                    pass

                return True
            else:
                # 登录失败，询问用户是否重新登录
                print("\n" + "-" * 60)
                print("  ❌ 登录验证失败！未检测到登录状态")
                print("-" * 60)
                try:
                    choice = input("  是否重新登录？(输入 Y 重新登录 / 输入 N 退出程序): ").strip().upper()
                except (KeyboardInterrupt, EOFError):
                    print("\n  用户取消，程序退出")
                    return False

                if choice == 'Y':
                    Logger.info("用户选择重新登录，返回登录引导...")
                    continue
                else:
                    Logger.info("用户选择退出程序")
                    return False

    def _build_search_url(self, keyword, page=1):
        """构建维普搜索URL，使用urlencode确保中文关键词正确编码

        Args:
            keyword: 搜索关键词
            page: 页码，默认第1页

        Returns:
            编码后的完整搜索URL
        """
        # 使用urlencode确保参数正确编码，key参数值格式为 K=关键词
        params = {
            'key': f'K={keyword}',
            'from': 'Qikan_Search_Index',
            'page': str(page),
        }
        return f"{SEARCH_URL}?{urlencode(params, encoding='utf-8')}"

    def _check_search_success(self):
        """检测当前页面是否包含搜索成功的结果标识

        Returns:
            True表示搜索成功并返回了结果，False表示未检测到结果
        """
        try:
            page = self.browser_mgr.browser
            if not page:
                return False
            page_text = page.html or ''
            success_patterns = [
                r'共找到\s*[\d,]+篇',
                r'共\s*[\d,]+条结果',
                r'共\s*[\d,]+篇文章',
                '搜索结果',
            ]
            for pattern in success_patterns:
                if re.search(pattern, page_text):
                    return True
            return False
        except Exception:
            return False

    def _check_captcha(self):
        """检测当前页面是否出现了验证码

        先判断搜索是否成功（成功则无需检测验证码），再通过关键词
        和CSS选择器匹配常见的验证码组件。

        Returns:
            True表示检测到验证码，False表示无验证码
        """
        try:
            page = self.browser_mgr.browser
            if not page:
                return False

            if self._check_search_success():
                return False

            captcha_keywords = ['请拖动滑块', '滑动验证', '图形验证', '人机验证',
                                'security verify', 'human verification']
            page_text = page.html or ''

            for kw in captcha_keywords:
                if kw in page_text:
                    try:
                        captcha_elem = page.ele(f'text:{kw}', timeout=2)
                        if captcha_elem:
                            return True
                    except Exception:
                        continue

            captcha_selectors = [
                'css:div.captcha',
                'css:div.verify-wrap',
                'css:div.slider-verify',
                'css:img[src*="captcha"]',
                'css:img[src*="verify"]',
            ]
            for sel in captcha_selectors:
                try:
                    elem = page.ele(sel, timeout=1)
                    if elem:
                        return True
                except Exception:
                    continue

            return False
        except Exception:
            return False

    def _wait_for_captcha(self, max_wait=120):
        """等待用户手动完成验证码，定期检测验证码是否消失

        Args:
            max_wait: 最大等待秒数，默认120秒

        Returns:
            True表示验证码已完成，False表示等待超时
        """
        Logger.warning(f"检测到验证码，请手动完成（最多等待{max_wait}秒）...")
        start = time.time()
        while time.time() - start < max_wait:
            time.sleep(3)
            if not self._check_captcha():
                Logger.success("验证码已完成")
                return True
            elapsed = int(time.time() - start)
            if elapsed % 15 == 0:
                Logger.info(f"等待验证码完成... ({elapsed}/{max_wait}秒)")
        Logger.error("验证码等待超时")
        return False

    def has_stop_signal(self):
        """检测停止标识（反爬拦截/访问被拒绝）
        简化检测逻辑，避免频繁触发
        返回：True表示检测到停止标识，False表示正常
        """
        try:
            browser = self.browser_mgr.browser
            if not browser:
                return False

            # 只在明确发现了验证码或登录页面时才停止
            try:
                current_url = (browser.url or '').lower()
                # 只有明确跳转到登录页面或专门的验证码页面才停止
                if '/login' in current_url and not ('/search' in current_url or '/qikan/' in current_url):
                    Logger.warning(f"检测到登录页面: {current_url[:60]}")
                    return True
                if 'captcha' in current_url or 'verify' in current_url:
                    Logger.warning(f"检测到验证页面: {current_url[:60]}")
                    return True
            except Exception:
                pass

            return False
        except Exception:
            return False

    def handle_stop_signal(self):
        """简化停止标识处理，直接返回True让用户自己处理"""
        Logger.warning("检测到可能需要用户干预，继续尝试...")
        return True

    def has_paywall(self, tab=None):
        """检测付费页面/付费墙

        检测方式：
        - URL包含buy/login等付费相关路径
        - 页面存在购买按钮(#buyBtn)
        - 页面存在付费/VIP提示弹窗

        返回：True表示检测到付费墙，False表示未检测到
        """
        try:
            target = tab or self.browser_mgr.browser
            if not target:
                return False

            # 检测URL中的付费标识
            try:
                current_url = (target.url or '').lower()
                if any(kw in current_url for kw in ['buy', 'pay', 'fee', 'order', 'purchase']):
                    Logger.warning(f"URL检测到付费标识: {current_url[:60]}")
                    return True
            except Exception:
                pass

            # 检测页面中的购买按钮
            paywall_selectors = [
                'css:#buyBtn',
                'css:button[class*="buy"]',
                'css:a[class*="buy-btn"]',
                'css:div[class*="paywall"]',
                'css:div[class*="vip-only"]',
            ]
            for sel in paywall_selectors:
                try:
                    elem = target.ele(sel, timeout=1)
                    if elem:
                        Logger.warning(f"检测到付费墙元素: {sel}")
                        return True
                except Exception:
                    continue

            return False
        except Exception:
            return False

    def search_via_browser(self, keyword, page=1):
        """通过浏览器执行搜索，处理验证码并等待结果加载

        关键修复：维普的分页不能通过URL参数直接跳转，
        必须先打开第1页，然后通过点击"下一页"按钮逐页翻到目标页。

        Args:
            keyword: 搜索关键词
            page: 目标页码

        Returns:
            搜索结果页HTML字符串，失败返回None
        """
        if not self._ensure_browser():
            return None
        try:
            self._close_extra_tabs()
            self._switch_to_main_tab()
            time.sleep(0.5)

            # 始终先打开第1页（维普URL翻页无效，必须从第1页开始点击翻页）
            search_url = self._build_search_url(keyword, 1)
            Logger.info(f"浏览器搜索: {keyword} (目标第{page}页，先加载第1页)")
            html = self.browser_mgr.get(search_url)
            if not html:
                return None

            if self._check_captcha():
                if not self._wait_for_captcha():
                    return None
                html = self.browser_mgr.browser.html

            # 等待搜索结果动态加载完成（维普是SPA，搜索结果通过AJAX加载）
            html = self._wait_for_search_results()

            try:
                self.main_tab_id = self.browser_mgr.browser.tab_id
            except Exception:
                pass

            self.session.sync_cookies_from_browser(self.browser_mgr.browser)

            # 如果目标页码大于1，通过点击翻页到目标页
            if page > 1:
                Logger.info(f"从第1页点击翻到第{page}页...")
                if not self._navigate_to_page(page):
                    Logger.warning(f"翻到第{page}页失败，使用第1页内容")
                    self.page_num = 1
                    return html
                # 翻页成功后重新获取HTML
                html = self._wait_for_search_results()
                self.session.sync_cookies_from_browser(self.browser_mgr.browser)

            # 验证当前实际页码
            actual_page = self._get_current_page_num()
            if actual_page != page:
                Logger.warning(f"页码不匹配，期望{page}，实际{actual_page}")

            return html
        except Exception as e:
            Logger.error(f"浏览器搜索失败: {e}")
            return None


    def _get_current_page_num(self):
        """获取页面底部分页组件中当前高亮的页码

        维普使用layui分页组件，当前页码的em标签带有layui-laypage-curr类
        Returns: 当前页码(int)，检测失败返回1
        """
        browser = self.browser_mgr.browser
        if not browser:
            return 1
        selectors = [
            'css:em.layui-laypage-curr',
            'css:span.layui-laypage-curr em',
            'css:a.layui-laypage-curr',
            'css:li.active a',
            'css:a.active',
        ]
        for sel in selectors:
            try:
                elem = browser.ele(sel, timeout=2)
                if elem:
                    text = (elem.text or '').strip()
                    if text.isdigit():
                        return int(text)
            except Exception:
                continue
        return 1

    def _click_page_num(self, target_page):
        """点击分页组件中指定页码的按钮

        Args:
            target_page: 目标页码
        Returns: 成功返回True，失败返回False
        """
        browser = self.browser_mgr.browser
        if not browser:
            return False
        tp = str(target_page)
        selectors = [
            f'xpath://a[contains(@class, "layui-laypage") and text()="{tp}"]',
            f'xpath://a[contains(@class, "page") and text()="{tp}"]',
            f'xpath://a[text()="{tp}"]',
        ]
        for sel in selectors:
            try:
                btn = browser.ele(sel, timeout=2)
                if btn:
                    try:
                        btn.click()
                    except Exception:
                        btn.click(by_js=True)
                    time.sleep(random.uniform(2, 3))
                    return True
            except Exception:
                continue
        return False

    def _navigate_to_page(self, target_page):
        """从当前页导航到目标页码

        维普的分页不能通过URL直接跳转，必须通过点击实现。
        策略：先确保在第1页搜索结果，然后循环点击"下一页"按钮直到到达目标页。

        Args:
            target_page: 目标页码
        Returns: 成功返回True，失败返回False
        """
        if target_page <= 1:
            return True

        browser = self.browser_mgr.browser
        if not browser:
            return False

        for retry in range(3):
            current = self._get_current_page_num()
            if current == target_page:
                Logger.success(f"已确认当前在第{target_page}页")
                return True

            Logger.info(f"正在翻到第{target_page}页... (当前第{current}页，第{retry+1}次尝试)")

            while current < target_page:
                click_ok = self._try_click_next_page()
                if not click_ok:
                    Logger.warning(f"翻页失败，无法从第{current}页翻到第{current+1}页")
                    break
                time.sleep(random.uniform(1, 2))
                current = self._get_current_page_num()
                Logger.info(f"翻页后当前页码: {current}")

            actual = self._get_current_page_num()
            if actual == target_page:
                Logger.success(f"已确认当前在第{target_page}页")
                return True
            else:
                Logger.warning(f"页码验证失败，期望{target_page}，实际{actual}，正在重试...")

        Logger.error(f"翻到第{target_page}页失败，已重试3次")
        return False

    def _wait_for_search_results(self, max_wait=15):
        """等待搜索结果动态加载完成

        维普搜索页是SPA应用，搜索结果通过AJAX动态渲染
        需要等待文章列表DOM元素出现后才获取HTML
        """
        browser = self.browser_mgr.browser
        if not browser:
            return browser.html if browser else None

        # 等待搜索结果容器出现
        result_selectors = [
            'css:div.article-list',
            'css:div.search-result',
            'css:div.result-list',
            'css:ul.list',
            'css:div[class*="article"]',
            'css:div[class*="result"]',
            'xpath://a[contains(@href, "/Qikan/Article/Detail")]',
        ]

        start = time.time()
        while time.time() - start < max_wait:
            try:
                for sel in result_selectors:
                    try:
                        elem = browser.ele(sel, timeout=1)
                        if elem:
                            # 找到搜索结果容器，额外等待确保内容完全渲染
                            time.sleep(0.5)
                            html = browser.html
                            if html and len(html) > 500:
                                Logger.info(f"搜索结果已加载(耗时{int(time.time()-start)}秒)")
                                return html
                    except Exception:
                        continue
            except Exception:
                pass
            time.sleep(0.5)

        # 超时后仍然返回当前HTML
        Logger.warning(f"等待搜索结果超时({max_wait}秒)，尝试使用当前页面内容")
        return browser.html

    def get_article_detail(self, url):
        """在新标签页中打开文章详情页并解析文章元数据

        Args:
            url: 文章详情页URL

        Returns:
            文章详细信息字典，失败返回None
        """
        if not self._ensure_browser():
            return None
        tab = None
        try:
            try:
                tab = self.browser_mgr.browser.new_tab(url)
            except Exception as e:
                Logger.warning(f"打开详情页失败: {e}")
                return None

            time.sleep(random.uniform(2, 4))

            html = tab.html
            detail = None
            if html:
                detail = CqvipParser.parse_detail_page(html, url)

            self._safe_close_tab(tab)
            tab = None
            return detail
        except Exception as e:
            Logger.error(f"获取详情失败: {e}")
            self._safe_close_tab(tab)
            self._switch_to_main_tab()
            return None

    def _find_download_button(self, tab):
        if not tab:
            return None

        exclude_keywords = ['app', 'mobile', '移动端', '手机', '扫码', '客户端', '注册', '登录']

        free_selectors = [
            'xpath://a[i[contains(@class, "icon-free")]]',
            'xpath://a[i[contains(@class, "behavior-noorderdown")]]',
            'css:a.behavior-noorderdown',
            'xpath://a[contains(text(), "免费下载")]',
            'xpath://a[contains(., "免费下载")]',
        ]

        for sel in free_selectors:
            try:
                btn = tab.ele(sel, timeout=1)
                if btn:
                    btn_text = (btn.text or '').strip()
                    btn_href = (btn.attr('href') or '').strip().lower()
                    if any(kw in btn_text.lower() for kw in exclude_keywords) or any(kw in btn_href for kw in exclude_keywords):
                        continue
                    Logger.download(f"找到下载按钮: {btn_text[:30]}")
                    return btn
            except Exception:
                continue

        pdf_selectors = [
            'xpath://a[contains(text(), "下载PDF")]',
            'xpath://a[contains(., "下载PDF")]',
            'css:a[class*="pdf"]',
            'css:a.download-pdf',
            'xpath://a[i[contains(@class, "icon-pdf")]]',
        ]

        for sel in pdf_selectors:
            try:
                btn = tab.ele(sel, timeout=1)
                if btn:
                    btn_text = (btn.text or '').strip()
                    btn_href = (btn.attr('href') or '').strip().lower()
                    if any(kw in btn_text.lower() for kw in exclude_keywords) or any(kw in btn_href for kw in exclude_keywords):
                        continue
                    Logger.download(f"找到下载按钮: {btn_text[:30]}")
                    return btn
            except Exception:
                continue

        return None

    def _check_pay_or_login_popup(self, tab):
        """检测点击免费下载后是否弹出了登录/付费/VIP提示窗口

        返回：True表示检测到付费/登录弹窗，False表示未检测到
        """
        try:
            # 检测layui弹窗（维普使用layui前端框架，付费/登录弹窗为layui-layer组件）
            try:
                popup = tab.ele('css:div.layui-layer', timeout=3)
                if popup:
                    popup_text = (popup.text or '').lower()
                    pay_keywords = ['登录', '付费', 'VIP', '会员', '充值', '购买',
                                    '订阅', '开通', '权限', '收费', 'login', 'pay',
                                    'subscribe', 'vip', 'member']
                    if any(kw in popup_text for kw in pay_keywords):
                        Logger.warning("检测到付费/登录弹窗，该文章需要付费")
                        return True
            except Exception:
                pass

            # 检测浏览器新标签页是否为登录/付费页面
            try:
                browser = self.browser_mgr.browser
                if browser and browser.tabs_count > 1:
                    tab_ids = browser.tab_ids
                    main_id = self.main_tab_id or tab_ids[0]
                    for tid in tab_ids:
                        if tid == main_id:
                            continue
                        try:
                            new_tab = browser.get_tab(tid)
                            tab_url = (new_tab.url or '').lower()
                            tab_title = (new_tab.title or '').lower()
                            pay_url_keywords = ['fee', 'pay', 'login', 'register',
                                                'vip', 'member', 'subscribe',
                                                'order', 'purchase']
                            pay_title_keywords = ['登录', '付费', 'VIP', '会员',
                                                  '充值', '购买', '订阅', '开通']
                            if any(kw in tab_url for kw in pay_url_keywords) or \
                               any(kw in tab_title for kw in pay_title_keywords):
                                Logger.warning(f"检测到付费/登录新标签页: {tab_title[:30]}")
                                return True
                        except Exception:
                            pass
            except Exception:
                pass

            # 检测当前页面是否跳转到非下载页面（登录页、付费页等）
            try:
                current_url = (tab.url or '').lower()
                redirect_keywords = ['login', 'pay', 'fee', 'register', 'vip',
                                     'member', 'subscribe', 'order']
                if any(kw in current_url for kw in redirect_keywords):
                    Logger.warning(f"点击后跳转到非下载页面: {current_url[:60]}")
                    return True
            except Exception:
                pass

        except Exception:
            pass

        return False

    def _close_pay_popups_and_tabs(self, tab):
        """关闭付费/登录弹窗和多余标签页，恢复到正常状态"""
        try:
            # 关闭layui弹窗
            try:
                close_selectors = [
                    'css:a.layui-layer-close',
                    'css:button.layui-layer-btn',
                    'css:a[class*="layui-layer-close"]',
                    'css:div.layui-layer a.close',
                ]
                for sel in close_selectors:
                    try:
                        btn = tab.ele(sel, timeout=1)
                        if btn:
                            btn.click(by_js=True)
                            time.sleep(0.5)
                            break
                    except Exception:
                        continue
            except Exception:
                pass
        except Exception:
            pass

        # 关闭多余标签页
        self._close_extra_tabs()
        self._switch_to_main_tab()

    def download_article(self, url, title=''):
        if not self._ensure_browser():
            return 'fail'
        tab = None
        try:
            if self.dm.is_article_downloaded(title):
                Logger.info(f"文章已存在，跳过: {title[:30]}")
                return 'skip'

            before_files = set(self.dm.articles_dir.iterdir()) if self.dm.articles_dir.exists() else set()

            tab = self.browser_mgr.browser.new_tab(url)
            time.sleep(random.uniform(1, 2))

            try:
                page_html = tab.html
                if not page_html or len(page_html) < 200:
                    Logger.warning(f"详情页加载失败，跳过: {title[:30]}")
                    self._safe_close_tab(tab)
                    return 'fail'
            except Exception as e:
                Logger.warning(f"详情页加载异常，跳过: {title[:30]} - {e}")
                self._safe_close_tab(tab)
                return 'fail'

            self._handle_popups(tab)

            download_btn = self._find_download_button(tab)

            if not download_btn:
                Logger.skip(f"未找到下载按钮，跳过: {title[:30]}")
                self._safe_close_tab(tab)
                return 'no_button'

            btn_text = (download_btn.text or '').strip()[:30]
            Logger.download(f"找到下载按钮: {btn_text}")

            click_success = False
            for retry in range(DOWNLOAD_MAX_RETRY):
                try:
                    try:
                        download_btn.click()
                        click_success = True
                    except Exception:
                        try:
                            download_btn.click(by_js=True)
                            click_success = True
                        except Exception as e:
                            if retry < DOWNLOAD_MAX_RETRY - 1:
                                time.sleep(2)
                                download_btn = self._find_download_button(tab)
                                if not download_btn:
                                    self._safe_close_tab(tab)
                                    return 'fail'
                                continue
                            else:
                                self._safe_close_tab(tab)
                                return 'fail'

                    if click_success:
                        break
                except Exception as e:
                    if retry < DOWNLOAD_MAX_RETRY - 1:
                        time.sleep(2)
                    else:
                        self._safe_close_tab(tab)
                        return 'fail'

            Logger.download(f"已点击下载: {title[:30]}")

            time.sleep(3)

            browser = self.browser_mgr.browser
            if browser and browser.tabs_count > 1:
                tab_ids = browser.tab_ids
                main_id = self.main_tab_id or tab_ids[0]
                for tid in tab_ids:
                    if tid == main_id:
                        continue
                    try:
                        new_tab = browser.get_tab(tid)
                        tab_url = (new_tab.url or '').lower()
                        try:
                            tab_html = new_tab.html or ''
                        except Exception:
                            tab_html = ''

                        pay_url_keywords = ['pay', 'fee', 'order', 'purchase']
                        pay_page_keywords = ['¥', '支付', '购买']

                        if any(kw in tab_url for kw in pay_url_keywords) or \
                           any(kw in tab_html for kw in pay_page_keywords):
                            Logger.skip(f"需付费跳过: {title[:30]}")
                            new_tab.close()
                            self._safe_close_tab(tab)
                            self._close_extra_tabs()
                            return 'pay'

                        if 'DownFileRedirect' in tab_url and \
                           ('全文获取异常' in tab_html or '请次日再试' in tab_html):
                            Logger.warning(f"下载失败(错误页面): {title[:30]}")
                            new_tab.close()
                            self._safe_close_tab(tab)
                            self._close_extra_tabs()
                            return 'fail'
                    except Exception:
                        pass

            file_downloaded = False
            new_file_path = None

            for wait_round in range(DOWNLOAD_WAIT_TIMEOUT):
                has_temp = False
                try:
                    for f in self.dm.articles_dir.iterdir():
                        if f.suffix in ['.crdownload', '.tmp', '.part']:
                            has_temp = True
                            break
                except Exception:
                    pass

                try:
                    after_files = set(self.dm.articles_dir.iterdir())
                    new_files = after_files - before_files
                    for f in new_files:
                        if f.suffix.lower() in ['.pdf', '.caj'] and not f.name.endswith('.crdownload'):
                            try:
                                if f.stat().st_size > 0:
                                    new_file_path = f
                                    file_downloaded = True
                                    break
                            except Exception:
                                pass
                except Exception:
                    pass

                if file_downloaded:
                    break
                time.sleep(1)

            if not file_downloaded:
                has_temp = False
                try:
                    for f in self.dm.articles_dir.iterdir():
                        if f.suffix in ['.crdownload', '.tmp', '.part']:
                            has_temp = True
                            break
                except Exception:
                    pass

                if has_temp:
                    for wait_round in range(LARGE_FILE_WAIT_TIMEOUT):
                        time.sleep(1)
                        try:
                            after_files = set(self.dm.articles_dir.iterdir())
                            new_files = after_files - before_files
                            for f in new_files:
                                if f.suffix.lower() in ['.pdf', '.caj'] and not f.name.endswith('.crdownload'):
                                    try:
                                        if f.stat().st_size > 0:
                                            new_file_path = f
                                            file_downloaded = True
                                            break
                                    except Exception:
                                        pass
                        except Exception:
                            pass
                        if file_downloaded:
                            break

            if file_downloaded and new_file_path:
                safe_title = self.dm._clean_name(title)
                new_name = f"{safe_title}{new_file_path.suffix}"
                c = 1
                while (self.dm.articles_dir / new_name).exists():
                    new_name = f"{safe_title}_{c}{new_file_path.suffix}"
                    c += 1
                try:
                    new_file_path.rename(self.dm.articles_dir / new_name)
                    Logger.success(f"下载成功: {new_name}")
                except Exception:
                    Logger.success(f"下载成功(未重命名): {new_file_path.name}")

            if not file_downloaded:
                Logger.error(f"下载失败: {title[:30]}")

            self._safe_close_tab(tab)
            self._close_extra_tabs()
            return 'success' if file_downloaded else 'fail'

        except Exception as e:
            Logger.error(f"下载异常: {e}")
            try:
                self._safe_close_tab(tab)
            except Exception:
                pass
            self._close_extra_tabs()
            self._switch_to_main_tab()
            return 'fail'

    def _save_html(self, tab, title):
        """将文章详情页保存为清理后的HTML文件

        移除script/style等无关标签，仅保留文章正文内容，
        并添加基础排版样式以提升可读性。

        Args:
            tab: 浏览器标签页对象
            title: 文章标题，用于生成文件名

        Returns:
            True表示保存成功，False表示保存失败
        """
        try:
            html_content = tab.html
            if not html_content:
                Logger.warning("页面内容为空，跳过HTML保存")
                return False

            safe_title = self.dm._clean_name(title)
            filename = f"{safe_title}.html"
            filepath = self.dm.articles_dir / filename
            c = 1
            while filepath.exists():
                filename = f"{safe_title}_{c}.html"
                filepath = self.dm.articles_dir / filename
                c += 1

            soup = BeautifulSoup(html_content, 'html.parser')

            for tag in soup.find_all(['script', 'style', 'link', 'noscript']):
                tag.decompose()

            article_body = soup.find('div', class_='article-detail') or \
                          soup.find('div', class_='detail-content') or \
                          soup.find('div', class_='container') or \
                          soup.find('div', class_='main-content') or \
                          soup.find('div', id='detail') or \
                          soup.find('div', class_='content')

            if article_body:
                clean_html = str(article_body)
            else:
                clean_html = str(soup.body) if soup.body else str(soup)

            full_html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        body {{ font-family: "Microsoft YaHei", "SimSun", serif; max-width: 800px; margin: 0 auto; padding: 20px; line-height: 1.8; }}
        h1 {{ font-size: 1.5em; text-align: center; margin-bottom: 10px; }}
        .meta {{ color: #666; text-align: center; margin-bottom: 20px; }}
        .abstract {{ background: #f5f5f5; padding: 15px; border-radius: 5px; margin: 15px 0; }}
        .keywords {{ color: #0066cc; }}
        a {{ color: #0066cc; text-decoration: none; }}
    </style>
</head>
<body>
{clean_html}
</body>
</html>'''

            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(full_html)

            Logger.success(f"HTML已保存: {filename}")
            return True
        except Exception as e:
            Logger.error(f"HTML保存失败: {e}")
            return False

    def _handle_popups(self, tab):
        """自动处理页面中可能出现的弹窗和多余标签页

        处理类型包括：
        - JavaScript alert弹窗（自动确认）
        - 新打开的付费/登录/PDF标签页（自动关闭）
        - 页面内的确认按钮（自动点击）
        """
        try:
            try:
                dialog = tab.handle_alert
                if dialog:
                    dialog.accept()
                    Logger.info("已自动确认弹窗")
            except Exception:
                pass

            try:
                browser = self.browser_mgr.browser
                if browser and browser.tabs_count > 1:
                    tab_ids = browser.tab_ids
                    main_id = self.main_tab_id or tab_ids[0]
                    for tid in tab_ids:
                        if tid == main_id:
                            continue
                        try:
                            new_tab = browser.get_tab(tid)
                            tab_url = new_tab.url or ''
                            tab_title = new_tab.title or ''
                            if any(kw in tab_url.lower() for kw in ['fee', 'pay', 'login', 'register', 'appintroduce']):
                                new_tab.close()
                                Logger.info("已自动关闭付费/登录提示页")
                            elif 'about:blank' in tab_url or tab_title == '':
                                new_tab.close()
                            elif '.pdf' in tab_url.lower():
                                time.sleep(2)
                                new_tab.close()
                                Logger.info("已关闭PDF在线阅读页")
                        except Exception:
                            pass
            except Exception:
                pass

            try:
                confirm_selectors = [
                    'css:button.btn-confirm',
                    'css:button[class*="confirm"]',
                    'css:a.btn-confirm',
                    'css:button:contains("确定")',
                    'css:button:contains("确认")',
                ]
                for sel in confirm_selectors:
                    try:
                        btn = tab.ele(sel, timeout=1)
                        if btn:
                            btn.click()
                            Logger.info("已自动点击确认按钮")
                            break
                    except Exception:
                        continue
            except Exception:
                pass

        except Exception:
            pass

    def _safe_close_tab(self, tab):
        """安全关闭浏览器标签页，忽略关闭过程中的异常

        Args:
            tab: 需要关闭的标签页对象，None时不执行任何操作
        """
        if tab is None:
            return
        try:
            tab.close()
        except Exception:
            try:
                tab.close()
            except Exception:
                pass

    def _switch_to_main_tab(self):
        """切换回主标签页并置于前台，确保后续操作在正确的标签页执行"""
        try:
            if self.main_tab_id and self.browser_mgr.browser:
                main_tab = self.browser_mgr.browser.get_tab(self.main_tab_id)
                if main_tab:
                    main_tab.set.tab_to_front()
        except Exception:
            pass

    def _switch_to_list_mode(self):
        """切换搜索结果到列表模式（参考weipu.py）

        维普搜索结果默认可能是卡片模式，列表模式更便于解析文章信息
        """
        try:
            browser = self.browser_mgr.browser
            if not browser:
                return

            list_mode_selectors = [
                'xpath://a[contains(text(), "列表")]',
                'xpath://a[contains(@class, "icon-list")]',
                'css:a.list-mode',
                'css:a[class*="list-view"]',
                'css:a[class*="mode-list"]',
            ]
            for sel in list_mode_selectors:
                try:
                    btn = browser.ele(sel, timeout=2)
                    if btn:
                        Logger.info("切换到列表模式...")
                        btn.click()
                        time.sleep(2)
                        return
                except Exception:
                    continue
        except Exception:
            pass

    def _handle_iframe(self):
        """处理搜索结果页中可能存在的iframe（参考weipu.py）

        维普搜索结果有时嵌套在iframe中，需要切入才能正确解析
        """
        try:
            browser = self.browser_mgr.browser
            if not browser:
                return

            iframes = browser.eles('tag:iframe', timeout=2)
            if iframes:
                Logger.info(f"检测到 {len(iframes)} 个iframe，尝试切入...")
                try:
                    browser.get_frame(0)
                    time.sleep(1)
                except Exception:
                    pass
        except Exception:
            pass

    def _close_extra_tabs(self):
        """关闭除主标签页外的所有多余标签页，防止标签页累积"""
        try:
            browser = self.browser_mgr.browser
            if not browser:
                return
            if browser.tabs_count <= 1:
                return
            tab_ids = browser.tab_ids
            if len(tab_ids) <= 1:
                return
            main_id = self.main_tab_id or tab_ids[0]
            for tid in tab_ids:
                if tid == main_id:
                    continue
                try:
                    extra_tab = browser.get_tab(tid)
                    if extra_tab:
                        extra_tab.close()
                except Exception:
                    pass
            time.sleep(0.5)
        except Exception:
            pass

    def _try_click_next_page(self):
        """尝试点击搜索结果页上的"下一页"按钮实现翻页

        当URL翻页方式失败时作为备用方案
        优先使用维普专用的layui分页组件选择器
        返回：成功返回新的HTML内容，失败返回None
        """
        browser = self.browser_mgr.browser
        if not browser:
            return None

        # 维普使用layui分页组件，优先匹配layui-laypage-next
        next_page_selectors = [
            'css:a.layui-laypage-next',
            'xpath://a[@class="layui-laypage-next" and text()="下一页"]',
            'xpath://a[contains(@class, "layui-laypage-next")]',
            'css:a.next',
            'css:a[class*="next"]',
            'css:a[class*="page-next"]',
            'css:li.next > a',
            'css:a[aria-label="下一页"]',
            'xpath://a[contains(text(), "下一页")]',
            'xpath://a[contains(text(), "下页")]',
            'xpath://a[contains(@class, "next") and not(contains(@class, "last"))]',
            'css:a.page-next',
            'css:a.btn-next',
        ]

        for sel in next_page_selectors:
            try:
                btn = browser.ele(sel, timeout=2)
                if btn:
                    btn_text = (btn.text or '').strip()
                    # 排除"末页"等非下一页按钮
                    if btn_text in ['末页', '尾页', 'Last']:
                        continue

                    # 检测layui分页组件的禁用状态
                    try:
                        btn_class = (btn.attr('class') or '').lower()
                        if 'layui-disabled' in btn_class:
                            Logger.info("下一页按钮已禁用(layui-disabled)，已到最后一页")
                            return None
                        if 'disabled' in btn_class:
                            Logger.info("下一页按钮已禁用，已到最后一页")
                            return None
                    except Exception:
                        pass

                    Logger.info(f"点击下一页按钮: {btn_text or sel}")
                    try:
                        btn.click()
                    except Exception:
                        try:
                            btn.click(by_js=True)
                        except Exception:
                            continue
                    time.sleep(random.uniform(2, 4))
                    html = browser.html
                    if html:
                        return html
            except Exception:
                continue

        Logger.warning("未找到下一页按钮")
        return None

    def crawl(self):
        """爬虫主循环：登录 -> 逐页搜索 -> 解析文章 -> 下载免费文章 -> 保存结果

        支持断点续爬，通过进度文件恢复上次的页码和已爬取记录。
        当达到指定数量或翻页到最后一页时自动停止。
        """
        Logger.info(f"开始爬取关键词: {self.keyword}")
        Logger.info(f"计划数量: {'全部' if self.max_count == 0 else self.max_count}")
        Logger.info(f"输出目录: {self.dm.output_dir}")
        Logger.info("下载模式: 自动下载（单位IP/免费均可）")

        # 登录前置流程：引导用户登录并检测登录状态
        Logger.info("=" * 40 + " 登录引导 " + "=" * 40)
        if not self._login_before_crawl():
            Logger.error("登录未完成，程序终止")
            self.browser_mgr.close()
            return

        progress = self.dm.load_progress()
        if progress:
            self.page_num = progress.get('page_num', 1)
            if self.page_num > 1:
                Logger.info(f"从断点续爬，第{self.page_num}页开始（将从第1页点击翻页到目标页）")
            else:
                Logger.info("从断点续爬，第1页开始")

        try:
            while not self._stopped:
                # search_via_browser内部已处理翻页：先加载第1页，再点击翻到目标页
                html = self.search_via_browser(self.keyword, page=self.page_num)
                if not html:
                    if self.page_num == 1:
                        Logger.error("首页搜索失败，退出")
                        break
                    Logger.warning("搜索页获取失败，尝试下一页")
                    self.page_num += 1
                    if self.page_num > 50:
                        break
                    continue

                articles = CqvipParser.parse_search_results(html)

                # 验证当前实际页码
                actual_page = self._get_current_page_num()
                Logger.info(f"第{self.page_num}页(实际页码:{actual_page})解析到 {len(articles)} 篇文章")

                if not articles:
                    Logger.info("没有更多结果")
                    break

                self._process_articles(articles)

                if self.max_count > 0 and self.download_count >= self.max_count:
                    break

                # 成功处理完当前页后才保存进度（确保保存的是验证过的页码）
                verified_page = self._get_current_page_num()
                self.dm.save_progress(self.dm.existing_urls, self.dm.existing_titles, verified_page)
                self.page_num = verified_page + 1
                self.session.smart_delay()

        except KeyboardInterrupt:
            Logger.warning("用户中断爬取 (Ctrl+C)")
            self._stopped = True
        except Exception as e:
            Logger.error(f"爬取出错: {e}")
            traceback.print_exc()
        finally:
            self._finish()

    def _process_articles(self, articles):
        for article in articles:
            if self._stopped:
                break
            if self.max_count > 0 and self.download_count >= self.max_count:
                break

            title = article.get('标题', '')
            url = article.get('详情链接', '')

            if not title:
                continue

            if self.dm.is_duplicate(url, title):
                Logger.warning(f"重复，跳过: {title[:30]}...")
                self.skip_count += 1
                continue

            if self.dm.is_article_downloaded(title):
                Logger.warning(f"文件已存在，跳过: {title[:30]}...")
                self.skip_count += 1
                continue

            if url:
                detail = self.get_article_detail(url)
                if detail:
                    for key in ['摘要', '关键词', '发表时间', '期刊名称', '作者',
                                'DOI', '机构', '基金', '分类号']:
                        if detail.get(key) and not article.get(key):
                            article[key] = detail[key]

                result = self.download_article(url, title)

                if result == 'pay':
                    self.pay_skip_count += 1
                    article['序号'] = self.download_count + self.pay_skip_count
                    article['爬取时间'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    article['下载状态'] = '需付费跳过'
                    self.records.append(article)
                    continue

                if result == 'no_button':
                    self.pay_skip_count += 1
                    article['序号'] = self.download_count + self.pay_skip_count
                    article['爬取时间'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    article['下载状态'] = '需付费跳过'
                    self.records.append(article)
                    continue

                if result != 'success':
                    continue

            self.download_count += 1
            article['序号'] = self.download_count
            article['爬取时间'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            article['下载状态'] = '下载成功'

            self.records.append(article)
            self.success_count += 1

            Logger.info(f"[下载:{self.download_count}] {title[:50]}")
            if article.get('作者'):
                Logger.info(f"    作者: {article['作者'][:40]}")

            if self.success_count % BATCH_SAVE_INTERVAL == 0:
                self.dm.save_csv(self.records, mode='append')
                self.records = []
                Logger.stats(f"自动保存: 下载 {self.download_count} 篇")

            self.session.smart_delay()

    def _finish(self):
        if self.records:
            self.dm.save_csv(self.records, mode='append')
            self.records = []

        if not self.dm.csv_file.exists():
            try:
                with open(self.dm.csv_file, 'w', encoding='utf-8-sig', newline='') as f:
                    w = csv.DictWriter(f, fieldnames=self.dm.fields)
                    w.writeheader()
            except Exception as e:
                Logger.error(f"创建CSV文件失败: {e}")

        self._generate_summary_csv()

        pdf_count = 0
        try:
            for f in self.dm.articles_dir.iterdir():
                if f.suffix.lower() in ['.pdf', '.caj']:
                    pdf_count += 1
        except Exception:
            pass

        self.dm.save_progress(self.dm.existing_urls, self.dm.existing_titles, self.page_num)

        Logger.info("=" * 60)
        Logger.info("爬取完成！")
        Logger.stats(f"关键词: {self.keyword}")
        Logger.stats(f"下载成功: {self.download_count} 篇")
        Logger.stats(f"需付费跳过: {self.pay_skip_count} 篇")
        Logger.stats(f"重复/已存在跳过: {self.skip_count} 篇")
        Logger.stats(f"PDF文件: {pdf_count} 个")
        Logger.stats(f"CSV文件: {self.dm.csv_file}")
        Logger.stats(f"总结CSV: {self.dm.output_dir / f'{self.keyword}_总结报告.csv'}")
        Logger.stats(f"文章目录: {self.dm.articles_dir}")
        Logger.info("=" * 60)

        self.browser_mgr.close()

    def _generate_summary_csv(self):
        summary_file = self.dm.output_dir / f'{self.keyword}_总结报告.csv'
        summary_fields = ['序号', '标题', '作者', '期刊名称', '发表时间', '摘要',
                          '关键词', 'DOI', '机构', '基金', '分类号', '详情链接',
                          '下载状态', '下载文件名', '文件大小', '爬取时间']

        try:
            all_records = []
            if self.dm.csv_file.exists():
                try:
                    with open(self.dm.csv_file, 'r', encoding='utf-8-sig') as f:
                        for row in csv.DictReader(f):
                            all_records.append(row)
                except Exception as e:
                    Logger.warning(f"读取增量CSV失败: {e}")

            downloaded_files = {}
            try:
                for f in self.dm.articles_dir.iterdir():
                    if f.suffix.lower() in ['.pdf', '.caj'] and not f.name.endswith('.crdownload'):
                        downloaded_files[f.stem] = f
            except Exception:
                pass

            for record in all_records:
                title = record.get('标题', '')
                safe_title = self.dm._clean_name(title)

                matched_file = None
                for stem, f in downloaded_files.items():
                    if stem.startswith(safe_title) or safe_title.startswith(stem):
                        matched_file = f
                        break

                if matched_file:
                    record['下载文件名'] = matched_file.name
                    try:
                        size_bytes = matched_file.stat().st_size
                        if size_bytes >= 1024 * 1024:
                            record['文件大小'] = f"{size_bytes / (1024 * 1024):.2f} MB"
                        elif size_bytes >= 1024:
                            record['文件大小'] = f"{size_bytes / 1024:.1f} KB"
                        else:
                            record['文件大小'] = f"{size_bytes} B"
                    except Exception:
                        record['文件大小'] = ''
                else:
                    record['下载文件名'] = ''
                    record['文件大小'] = ''

            with open(summary_file, 'w', encoding='utf-8-sig', newline='') as f:
                w = csv.DictWriter(f, fieldnames=summary_fields, extrasaction='ignore')
                w.writeheader()

                for i, record in enumerate(all_records, 1):
                    record['序号'] = i
                    w.writerow(record)

            try:
                with open(summary_file, 'a', encoding='utf-8-sig', newline='') as f:
                    f.write('\n')
                    f.write(f'统计汇总,,,,,,\n')
                    f.write(f'关键词,{self.keyword},,,,\n')
                    f.write(f'下载成功,{self.download_count}篇,,,\n')
                    f.write(f'需付费跳过,{self.pay_skip_count}篇,,,\n')
                    f.write(f'重复/已存在跳过,{self.skip_count}篇,,,\n')
                    f.write(f'生成时间,{datetime.now().strftime("%Y-%m-%d %H:%M:%S")},,,,\n')
            except Exception:
                pass

            Logger.success(f"总结CSV已生成: {summary_file} (共{len(all_records)}条记录)")

        except Exception as e:
            Logger.error(f"生成总结CSV失败: {e}")


# 批量爬取的关键词列表，使用--batch参数时依次爬取这些关键词
BATCH_KEYWORDS = [
    "深度学习", "自然语言处理", "计算机视觉", "知识图谱",
    "推荐系统", "强化学习", "迁移学习", "联邦学习",
]


def main():
    """程序入口，解析命令行参数并启动爬虫

    支持三种运行模式：
    1. 命令行指定关键词：python Cqvip.py -k 关键词 [-n 数量] [-o 目录]
    2. 批量模式：python Cqvip.py -b [-n 数量]，依次爬取BATCH_KEYWORDS中的关键词
    3. 交互模式：不带参数运行，通过控制台输入关键词和数量
    """
    parser = argparse.ArgumentParser(description='维普期刊爬虫（单位IP/免费下载版）')
    parser.add_argument('-k', '--keyword', help='搜索关键词')
    parser.add_argument('-n', '--num', type=int, default=0, help='爬取数量(0=全部)')
    parser.add_argument('-o', '--output', help='输出目录')
    parser.add_argument('-b', '--batch', action='store_true', help='批量模式')
    args = parser.parse_args()

    if args.batch:
        Logger.info(f"批量模式，共 {len(BATCH_KEYWORDS)} 个关键词")
        for kw in BATCH_KEYWORDS:
            Logger.info(f"开始爬取: {kw}")
            crawler = CqvipCrawler(kw, max_count=args.num, output_dir=args.output)
            crawler.crawl()
        return

    if args.keyword:
        crawler = CqvipCrawler(args.keyword, max_count=args.num, output_dir=args.output)
        crawler.crawl()
    else:
        try:
            keyword = input("请输入搜索关键词: ").strip()
            if not keyword:
                print("关键词不能为空")
                return
            num_input = input("请输入爬取数量(回车表示全部): ").strip()
            num = int(num_input) if num_input.isdigit() else 0
            output_dir_input = input("请输入输出目录(回车使用默认目录): ").strip()
            output_dir = output_dir_input if output_dir_input else None
            crawler = CqvipCrawler(keyword, max_count=num, output_dir=output_dir)
            crawler.crawl()
        except (KeyboardInterrupt, EOFError):
            print("\n已退出")


if __name__ == '__main__':
    main()

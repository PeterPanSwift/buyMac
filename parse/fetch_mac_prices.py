#!/usr/bin/env python3
"""Fetch Apple Mac prices from macbooks_specs.json and write macbooks_specs_price.json."""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlsplit


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36"
)
STORE_PREFIX = "https://www.apple.com/tw/"
EDU_STORE_PREFIX = "https://www.apple.com/tw-edu/"
SCRIPT_RE = re.compile(r'<script type="application/ld\+json">\s*(.*?)\s*</script>', re.S)
CPU_GPU_RE = re.compile(r"(\d+)\s*CPU\s*/\s*(\d+)\s*GPU")


@dataclass(frozen=True)
class FetchResult:
    url: str
    exists: bool
    price: int | None = None
    currency: str | None = None
    http_code: int | None = None
    effective_url: str | None = None
    error: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch Apple Mac prices and emit a JSON file with price information."
    )
    parser.add_argument(
        "-i",
        "--input",
        default="macbooks_specs.json",
        help="Input JSON file. Default: %(default)s",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="macbooks_specs_price.json",
        help="Output JSON file. Default: %(default)s",
    )
    parser.add_argument(
        "-w",
        "--workers",
        type=int,
        default=8,
        help="Concurrent curl workers. Default: %(default)s",
    )
    return parser.parse_args()


def normalize_capacity(value: str) -> str:
    return value.strip().lower()


def parse_cpu_gpu(value: str) -> tuple[str, str]:
    match = CPU_GPU_RE.search(value)
    if match is None:
        raise ValueError(f"無法解析 CPU/GPU 數量: {value}")
    return match.group(1), match.group(2)


def pro_chip_slug(cpu_model: str) -> str:
    model = cpu_model.strip().lower().replace(" ", "-")
    return f"apple-{model}-晶片"


def simple_chip_slug(cpu_model: str) -> str:
    return f"{cpu_model.strip().lower().replace(' ', '-')}-晶片"


def urls_match(left: str, right: str) -> bool:
    left_parts = urlsplit(left)
    right_parts = urlsplit(right)
    return (
        left_parts.scheme.lower(),
        left_parts.netloc.lower(),
        unquote(left_parts.path).rstrip("/"),
        left_parts.query,
    ) == (
        right_parts.scheme.lower(),
        right_parts.netloc.lower(),
        unquote(right_parts.path).rstrip("/"),
        right_parts.query,
    )


def build_product_url(item: dict[str, Any]) -> str:
    product_code = item["產品代號"]
    storage = normalize_capacity(item["儲存裝置"])

    if product_code == "neo":
        return f"https://www.apple.com/tw/shop/buy-mac/macbook-neo/silver-{storage}"

    cpu_cores, gpu_cores = parse_cpu_gpu(item["CPU/GPU 數量"])
    size = item["螢幕尺寸"]
    memory = normalize_capacity(item["記憶體"])

    if product_code == "air":
        chip = f"{item['CPU 型號'].strip().lower()}-晶片"
        return (
            "https://www.apple.com/tw/shop/buy-mac/macbook-air/"
            f"{size}-吋-silver-{chip}-"
            f"{cpu_cores}-核心-cpu-{gpu_cores}-核心-gpu-"
            f"{memory}-記憶體-{storage}-儲存裝置"
        )

    if product_code == "pro":
        display = item["顯示器選項"].strip()
        chip = pro_chip_slug(item["CPU 型號"])
        return (
            "https://www.apple.com/tw/shop/buy-mac/macbook-pro/"
            f"{size}-吋-silver-{display}-{chip}-"
            f"{cpu_cores}-核心-cpu-{gpu_cores}-核心-gpu-"
            f"{memory}-記憶體-{storage}-儲存裝置"
        )

    if product_code == "mini":
        chip = simple_chip_slug(item["CPU 型號"])
        return (
            "https://www.apple.com/tw/shop/buy-mac/mac-mini/"
            f"{chip}-{cpu_cores}-核心-cpu-{gpu_cores}-核心-gpu-"
            f"{memory}-記憶體-{storage}-儲存裝置"
        )

    if product_code == "imac":
        chip = simple_chip_slug(item["CPU 型號"])
        base_url = (
            "https://www.apple.com/tw/shop/buy-mac/imac/"
            f"{size}-吋-silver-{chip}-{cpu_cores}-核心-cpu-{gpu_cores}-核心-gpu-"
            f"{memory}-記憶體-{storage}-儲存裝置"
        )
        display = item["顯示器選項"].strip()
        if cpu_cores == "8" and gpu_cores == "8":
            return f"{base_url}-立架"
        return f"{base_url}-{display}-立架"

    raise ValueError(f"不支援的產品代號: {product_code}")


def build_education_url(url: str) -> str:
    if not url.startswith(STORE_PREFIX):
        raise ValueError(f"無法轉換教育價網址: {url}")
    return EDU_STORE_PREFIX + url.removeprefix(STORE_PREFIX)


def parse_curl_output(raw_output: str) -> tuple[str, int | None, str | None]:
    body, marker, tail = raw_output.rpartition("\n__CURL_HTTP_CODE__:")
    if not marker:
        return raw_output, None, None

    http_code_text, marker, effective_url = tail.partition("\n__CURL_EFFECTIVE_URL__:")
    if not marker:
        return raw_output, None, None

    try:
        http_code = int(http_code_text.strip())
    except ValueError:
        http_code = None

    return body, http_code, effective_url.strip()


def extract_price_from_html(html_text: str, expected_url: str) -> tuple[int, str] | None:
    for script_text in SCRIPT_RE.findall(html_text):
        try:
            payload = json.loads(script_text)
        except json.JSONDecodeError:
            continue

        if not isinstance(payload, dict) or payload.get("@type") != "Product":
            continue

        product_url = payload.get("mainEntityOfPage") or payload.get("url")
        if not isinstance(product_url, str) or not urls_match(product_url, expected_url):
            continue

        offers = payload.get("offers")
        if isinstance(offers, dict):
            offers = [offers]
        if not isinstance(offers, list):
            continue

        for offer in offers:
            if not isinstance(offer, dict) or "price" not in offer:
                continue

            currency = str(offer.get("priceCurrency", "TWD"))
            try:
                amount = int(round(float(offer["price"])))
            except (TypeError, ValueError):
                continue
            return amount, currency

    return None


def fetch_price(url: str) -> FetchResult:
    command = [
        "curl",
        "-L",
        "--compressed",
        "-A",
        USER_AGENT,
        "-sS",
        "--connect-timeout",
        "20",
        "--max-time",
        "45",
        "-w",
        "\n__CURL_HTTP_CODE__:%{http_code}\n__CURL_EFFECTIVE_URL__:%{url_effective}\n",
        url,
    ]

    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.CalledProcessError as exc:
        return FetchResult(url=url, exists=False, error=exc.stderr.strip() or "curl 失敗")

    html_text, http_code, effective_url = parse_curl_output(completed.stdout)
    if http_code != 200:
        return FetchResult(
            url=url,
            exists=False,
            http_code=http_code,
            effective_url=effective_url,
            error=f"HTTP {http_code}",
        )

    if not effective_url or not urls_match(effective_url, url):
        return FetchResult(
            url=url,
            exists=False,
            http_code=http_code,
            effective_url=effective_url,
            error="商品頁不存在或被導回產品列表",
        )

    price_info = extract_price_from_html(html_text, effective_url)
    if price_info is None:
        return FetchResult(
            url=url,
            exists=False,
            http_code=http_code,
            effective_url=effective_url,
            error="找不到價格欄位",
        )

    amount, currency = price_info
    return FetchResult(
        url=url,
        exists=True,
        price=amount,
        currency=currency,
        http_code=http_code,
        effective_url=effective_url,
    )


def format_price(amount: int, currency: str) -> str:
    if currency == "TWD":
        return f"NT${amount:,}"
    return f"{currency} {amount:,}"


def main() -> int:
    args = parse_args()

    with open(args.input, encoding="utf-8") as file:
        payload = json.load(file)

    source_items = payload.get("資料", [])
    if not isinstance(source_items, list):
        raise ValueError("輸入 JSON 缺少 資料 陣列")

    store_urls: list[str] = []
    edu_urls: list[str] = []
    for item in source_items:
        store_url = build_product_url(item)
        store_urls.append(store_url)
        edu_urls.append(build_education_url(store_url))

    unique_urls = list(dict.fromkeys(store_urls + edu_urls))
    url_results: dict[str, FetchResult] = {}

    max_workers = max(1, args.workers)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {executor.submit(fetch_price, url): url for url in unique_urls}
        for future in concurrent.futures.as_completed(future_map):
            result = future.result()
            url_results[result.url] = result

    priced_items: list[dict[str, Any]] = []
    missing_count = 0

    for item, store_url, edu_url in zip(source_items, store_urls, edu_urls):
        store_result = url_results[store_url]
        edu_result = url_results[edu_url]
        if (
            store_result.exists
            and store_result.price is not None
            and store_result.currency is not None
            and edu_result.exists
            and edu_result.price is not None
            and edu_result.currency is not None
        ):
            enriched = dict(item)
            enriched["商品網址"] = store_url
            enriched["價格"] = store_result.price
            enriched["價格文字"] = format_price(store_result.price, store_result.currency)
            enriched["教育價網址"] = edu_url
            enriched["教育價"] = edu_result.price
            enriched["教育價文字"] = format_price(edu_result.price, edu_result.currency)
            enriched["貨幣"] = store_result.currency
            priced_items.append(enriched)
            continue

        missing_count += 1

    output_payload = dict(payload)
    output_payload["價格資料日期"] = dt.date.today().isoformat()
    output_payload["原始筆數"] = len(source_items)
    output_payload["筆數"] = len(priced_items)
    output_payload["資料"] = priced_items

    with open(args.output, "w", encoding="utf-8") as file:
        json.dump(output_payload, file, ensure_ascii=False, indent=2)
        file.write("\n")

    print(f"輸入筆數: {len(source_items)}")
    print(f"成功筆數: {len(priced_items)}")
    print(f"跳過筆數: {missing_count}")
    print(f"輸出檔案: {args.output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"錯誤: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

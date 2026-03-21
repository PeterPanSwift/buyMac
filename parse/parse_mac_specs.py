#!/usr/bin/env python3
"""Parse Apple Mac, Mac mini, and iMac specs pages and emit normalized JSON."""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import sys
import urllib.request


PRODUCTS = {
    "neo": {
        "name": "MacBook Neo",
        "url": "https://www.apple.com/tw/macbook-neo/specs",
        "parser": "techspecs",
        "sizes": [
            {
                "label": "13",
                "chip_marker": 'data-analytics-activitymap-region-id="chip"',
                "memory_marker": 'data-analytics-activitymap-region-id="memory"',
                "storage_marker": 'data-analytics-section-engagement="name:storage"',
                "display_marker": 'class="techspecs-section section-display"',
            }
        ],
    },
    "air": {
        "name": "MacBook Air",
        "url": "https://www.apple.com/tw/macbook-air/specs",
        "parser": "techspecs",
        "sizes": [
            {
                "label": "13",
                "chip_marker": 'data-analytics-activitymap-region-id="chip 13 inch"',
                "memory_marker": 'data-analytics-activitymap-region-id="memory 13 inch"',
                "storage_marker": 'data-analytics-section-engagement="name:storage 13 inch"',
                "display_marker": 'class="techspecs-section section-display"',
            },
            {
                "label": "15",
                "chip_marker": 'data-analytics-activitymap-region-id="chip 15 inch"',
                "memory_marker": 'data-analytics-activitymap-region-id="memory 15 inch"',
                "storage_marker": 'data-analytics-section-engagement="name:storage 15 inch"',
                "display_marker": 'class="techspecs-section section-display"',
            },
        ],
    },
    "pro": {
        "name": "MacBook Pro",
        "url": "https://www.apple.com/tw/macbook-pro/specs",
        "parser": "techspecs",
        "sizes": [
            {
                "label": "14",
                "chip_marker": 'data-analytics-activitymap-region-id="chip 14 inch"',
                "memory_marker": 'data-analytics-activitymap-region-id="memory 14 inch"',
                "storage_marker": 'data-analytics-section-engagement="name:storage 14 inch"',
                "display_marker": 'class="techspecs-section section-display"',
            },
            {
                "label": "16",
                "chip_marker": 'data-analytics-activitymap-region-id="chip 16 inch"',
                "memory_marker": 'data-analytics-activitymap-region-id="memory 16 inch"',
                "storage_marker": 'data-analytics-section-engagement="name:storage 16 inch"',
                "display_marker": 'class="techspecs-section section-display"',
            },
        ],
    },
    "mini": {
        "name": "Mac mini",
        "url": "https://www.apple.com/tw/mac-mini/specs",
        "parser": "techspecs",
        "sizes": [
            {
                "label": "無",
                "chip_marker": 'data-analytics-activitymap-region-id="m4 chip"',
                "memory_marker": 'data-analytics-activitymap-region-id="m4 memory"',
                "storage_marker": 'data-analytics-activitymap-region-id="m4 storage"',
                "display_marker": 'data-analytics-activitymap-region-id="m4 display support"',
            }
        ],
    },
    "imac": {
        "name": "iMac",
        "url": "https://www.apple.com/tw/imac/specs",
        "parser": "imac",
        "screen_size": "24",
    },
}

CHIP_NAME_RE = re.compile(r"[AM]\d+(?: Pro| Max)?")
CHIP_OPTION_RE = re.compile(
    r"([AM]\d+(?: Pro| Max)?)\s*配備\s*(\d+)\s*核心\s*CPU(?:\s*與|\s*和)\s*(\d+)\s*核心\s*GPU"
)
SIZE_VALUE_RE = re.compile(r"(\d+)(GB|TB)")
DUPLICATE_KEY_FIELDS = (
    "產品",
    "螢幕尺寸",
    "CPU 型號",
    "CPU/GPU 數量",
    "記憶體",
    "儲存裝置",
    "顯示器選項",
)


def fetch_html(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36"
            )
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")


def clean_html_text(value: str) -> str:
    value = re.sub(r"<br\s*/?>", " ", value, flags=re.I)
    value = re.sub(r"</?(span|sup|a|p|ul|ol|li|div|strong|picture|source|img)[^>]*>", " ", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = value.replace("\xa0", " ")
    return re.sub(r"\s+", " ", value).strip()


def find_from(text: str, needle: str, start: int) -> int:
    position = text.find(needle, start)
    if position == -1:
        raise ValueError(f"Cannot find marker after offset: {needle}")
    return position


def extract_balanced_block(text: str, start: int, tag_name: str) -> str:
    if start == -1:
        raise ValueError(f"Cannot find <{tag_name}> block")

    depth = 0
    pattern = re.compile(rf"<{tag_name}\b[^>]*>|</{tag_name}>")
    for tag in pattern.finditer(text[start:]):
        raw_tag = tag.group(0)
        if raw_tag.startswith(f"</{tag_name}"):
            depth -= 1
        else:
            depth += 1
        if depth == 0:
            return text[start : start + tag.end()]

    raise ValueError(f"Unbalanced <{tag_name}> block")


def extract_techspecs_section(page_html: str, marker: str, start: int = 0) -> tuple[str, int]:
    marker_position = find_from(page_html, marker, start)
    section_start = page_html.rfind("<div", 0, marker_position)
    if section_start == -1:
        raise ValueError(f"Cannot find techspecs section for marker: {marker}")
    return extract_balanced_block(page_html, section_start, "div"), marker_position


def extract_page_section(page_html: str, marker: str, start: int = 0) -> tuple[str, int]:
    marker_position = find_from(page_html, marker, start)
    section_start = page_html.rfind("<section", 0, marker_position)
    if section_start == -1:
        raise ValueError(f"Cannot find page section for marker: {marker}")
    return extract_balanced_block(page_html, section_start, "section"), marker_position


def extract_product_sections(page_html: str, size_config: dict[str, str]) -> tuple[str, str, str, str]:
    chip_section, chip_start = extract_techspecs_section(page_html, size_config["chip_marker"], 0)
    memory_section, memory_start = extract_techspecs_section(
        page_html, size_config["memory_marker"], chip_start
    )
    storage_section, storage_start = extract_techspecs_section(
        page_html, size_config["storage_marker"], memory_start
    )
    display_section, _ = extract_techspecs_section(
        page_html, size_config["display_marker"], storage_start
    )
    return chip_section, memory_section, storage_section, display_section


def extract_div_blocks(section_html: str, target_class: str, start: int = 0) -> list[str]:
    blocks: list[str] = []
    for match in re.finditer(r'<div\b[^>]*class="([^"]*)"[^>]*>', section_html[start:]):
        if target_class not in match.group(1).split():
            continue
        block_start = start + match.start()
        blocks.append(extract_balanced_block(section_html, block_start, "div"))
    if not blocks:
        raise ValueError(f"Cannot parse div blocks for class: {target_class}")
    return blocks


def extract_column_blocks(section_html: str) -> list[str]:
    row_start = section_html.find('<div class="techspecs-row"')
    search_start = 0 if row_start == -1 else row_start
    return extract_div_blocks(section_html, "techspecs-column", search_start)


def extract_grid_item_blocks(section_html: str) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for match in re.finditer(r'<div\b[^>]*class="([^"]*)"[^>]*>', section_html):
        class_names = match.group(1).split()
        if "grid-item" not in class_names:
            continue
        block_start = match.start()
        items.append((match.group(1), extract_balanced_block(section_html, block_start, "div")))
    if not items:
        raise ValueError("Cannot parse iMac grid items")
    return items


def extract_chip_options(column_html: str) -> list[dict[str, int | str]]:
    column_text = clean_html_text(column_html)
    chip_name_match = None

    header_match = re.search(r'<p class="techspecs-subheader">(.*?)</p>', column_html, re.S)
    if header_match is not None:
        chip_name_match = CHIP_NAME_RE.search(clean_html_text(header_match.group(1)))

    if chip_name_match is None:
        for title_html in re.findall(r'<strong class="title[^"]*">(.*?)</strong>', column_html, re.S):
            title_text = clean_html_text(title_html)
            chip_name_match = CHIP_NAME_RE.search(title_text)
            if chip_name_match is not None:
                break

    if chip_name_match is None:
        chip_name_match = CHIP_NAME_RE.search(column_text)

    cpu_match = re.search(r"(\d+)\s*核心\s*CPU", column_text)
    gpu_match = re.search(r"(\d+)\s*核心\s*GPU", column_text)
    if chip_name_match is None or cpu_match is None or gpu_match is None:
        raise ValueError("Cannot parse chip base option")

    items = {
        (
            chip_name_match.group(0),
            int(cpu_match.group(1)),
            int(gpu_match.group(1)),
        )
    }

    for chip_name, cpu_cores, gpu_cores in CHIP_OPTION_RE.findall(clean_html_text(column_html)):
        items.add((chip_name, int(cpu_cores), int(gpu_cores)))

    return [
        {"cpu_model": chip_name, "cpu_cores": cpu_cores, "gpu_cores": gpu_cores}
        for chip_name, cpu_cores, gpu_cores in sorted(items, key=lambda item: (item[0], item[1], item[2]))
    ]


def normalize_memory_values(values: list[int]) -> list[str]:
    return [f"{value}GB" for value in sorted(set(values))]


def storage_sort_key(value: str) -> int:
    number = int(re.search(r"\d+", value).group(0))
    if value.endswith("TB"):
        return number * 1024
    return number


def memory_option_allowed(line: str, chip_name: str, gpu_cores: int) -> bool:
    if "(" not in line or ")" not in line:
        return True

    requirement = line[line.find("(") + 1 : line.rfind(")")]
    mentioned_models = CHIP_NAME_RE.findall(requirement)
    if not mentioned_models:
        return True

    for clause in requirement.split("或"):
        clause = clause.strip()
        clause_models = CHIP_NAME_RE.findall(clause)
        if chip_name not in clause_models:
            continue
        gpu_match = re.search(r"(\d+)\s*核心\s*GPU", clause)
        if gpu_match is None or int(gpu_match.group(1)) == gpu_cores:
            return True
    return False


def parse_memory_options(column_html: str, chip_name: str, gpu_cores: int) -> list[str]:
    memory_values: list[int] = []

    for item_html in re.findall(r"<p[^>]*>(.*?)</p>", column_html, flags=re.S):
        line = clean_html_text(item_html)
        if "GB" not in line:
            continue
        if memory_option_allowed(line, chip_name, gpu_cores):
            memory_values.extend(int(number) for number in re.findall(r"(\d+)GB", line))

    for item_html in re.findall(r"<li[^>]*>(.*?)</li>", column_html, flags=re.S):
        line = clean_html_text(item_html)
        if "GB" not in line:
            continue
        if memory_option_allowed(line, chip_name, gpu_cores):
            memory_values.extend(int(number) for number in re.findall(r"(\d+)GB", line))

    if not memory_values:
        raise ValueError("Cannot parse memory options")
    return normalize_memory_values(memory_values)


def parse_storage_options(column_html: str) -> list[str]:
    values = {f"{number}{unit}" for number, unit in SIZE_VALUE_RE.findall(clean_html_text(column_html))}
    filtered = [value for value in values if value.endswith(("GB", "TB"))]
    if not filtered:
        raise ValueError("Cannot parse storage options")
    return sorted(filtered, key=storage_sort_key)


def parse_display_info(section_html: str) -> tuple[str, list[str]]:
    display_type = ""

    subheaders = re.findall(r'<p class="techspecs-subheader">(.*?)</p>', section_html, flags=re.S)
    if subheaders:
        display_type = clean_html_text(subheaders[0])
    else:
        rowheader_match = re.search(
            r'<div role="rowheader" class="techspecs-rowheader"[^>]*>(.*?)</div>', section_html, re.S
        )
        if rowheader_match is not None:
            display_type = clean_html_text(rowheader_match.group(1))

    if not display_type:
        raise ValueError("Cannot parse display type")

    section_text = clean_html_text(section_html)
    options = ["標準顯示器"]
    if "奈米紋理顯示器" in section_text:
        options.append("奈米紋理顯示器")
    return display_type, options


def parse_inline_memory_options(item_html: str) -> list[str]:
    values = [int(number) for number in re.findall(r"(\d+)GB", clean_html_text(item_html))]
    if not values:
        raise ValueError("Cannot parse inline memory options")
    return normalize_memory_values(values)


def normalize_imac_key(class_name: str) -> str:
    for token in class_name.split():
        if token.startswith("grid-item-4-port-"):
            return token.removeprefix("grid-item-")
        if token in {"grid-item-4-port", "grid-item-2-port"}:
            return token.removeprefix("grid-item-")
        if token == "grid-item-2-port-1":
            return "2-port"
    raise ValueError(f"Cannot normalize iMac grid key: {class_name}")


def parse_imac_display_items(section_html: str) -> tuple[str, dict[str, list[str]]]:
    display_type = ""
    display_options: dict[str, list[str]] = {}

    for class_name, item_html in extract_grid_item_blocks(section_html):
        key = normalize_imac_key(class_name)
        title_match = re.search(r'<p class="title">(.*?)</p>', item_html, re.S)
        if title_match is None:
            raise ValueError("Cannot parse iMac display title")
        title = clean_html_text(title_match.group(1))
        if not display_type:
            display_type = title
        options = ["標準玻璃"]
        if "奈米紋理玻璃" in clean_html_text(item_html):
            options.append("奈米紋理玻璃")
        display_options[key] = options

    if not display_type or not display_options:
        raise ValueError("Cannot parse iMac display section")
    return display_type, display_options


def filter_imac_memory_options(values: list[str]) -> list[str]:
    # Apple 台灣商店目前沒有 iMac 32GB 的可買頁面，先從輸出排除。
    return [value for value in values if value != "32GB"]


def dedupe_records(records: list[dict[str, str | int]]) -> list[dict[str, str | int]]:
    seen: set[tuple[str, ...]] = set()
    deduped: list[dict[str, str | int]] = []

    for record in records:
        record_key = tuple(str(record[field]) for field in DUPLICATE_KEY_FIELDS)
        if record_key in seen:
            continue
        seen.add(record_key)
        deduped.append(record)

    return deduped


def finalize_payload(payload: dict[str, object]) -> dict[str, object]:
    records = dedupe_records(list(payload["資料"]))
    payload["資料"] = records
    payload["筆數"] = len(records)
    return payload


def build_techspecs_records(
    product_key: str, product_config: dict[str, object], page_html: str
) -> list[dict[str, str | int]]:
    records: list[dict[str, str | int]] = []

    for size_config in product_config["sizes"]:
        chip_section, memory_section, storage_section, display_section = extract_product_sections(
            page_html, size_config
        )
        chip_columns = extract_column_blocks(chip_section)
        memory_columns = extract_column_blocks(memory_section)
        storage_columns = extract_column_blocks(storage_section)
        display_type, display_options = parse_display_info(display_section)

        if len(chip_columns) == 1 and len(memory_columns) == len(storage_columns) and len(memory_columns) > 1:
            chip_columns = chip_columns * len(memory_columns)

        if not (len(chip_columns) == len(memory_columns) == len(storage_columns)):
            raise ValueError(
                f"Section column count mismatch for {product_config['name']} {size_config['label']}"
            )

        for index, chip_column in enumerate(chip_columns, start=1):
            chip_options = extract_chip_options(chip_column)
            storage_options = parse_storage_options(storage_columns[index - 1])

            for chip in chip_options:
                memory_options = parse_memory_options(
                    memory_columns[index - 1], str(chip["cpu_model"]), int(chip["gpu_cores"])
                )

                for memory in memory_options:
                    for storage in storage_options:
                        for display_option in display_options:
                            records.append(
                                {
                                    "產品": str(product_config["name"]),
                                    "產品代號": product_key,
                                    "螢幕尺寸": str(size_config["label"]),
                                    "CPU 型號": str(chip["cpu_model"]),
                                    "CPU/GPU 數量": (
                                        f'{chip["cpu_cores"]} CPU / {chip["gpu_cores"]} GPU'
                                    ),
                                    "記憶體": memory,
                                    "儲存裝置": storage,
                                    "顯示器": display_type,
                                    "顯示器選項": display_option,
                                }
                            )

    return records


def build_imac_records(
    product_key: str, product_config: dict[str, object], page_html: str
) -> list[dict[str, str | int]]:
    chip_section, chip_start = extract_page_section(page_html, 'data-analytics-section-engagement="name:chip"')
    storage_section, storage_start = extract_page_section(
        page_html, 'data-analytics-activitymap-region-id="storage"', chip_start
    )
    memory_section, memory_start = extract_page_section(
        page_html, 'data-analytics-activitymap-region-id="memory"', storage_start
    )
    display_section, _ = extract_page_section(
        page_html, 'data-analytics-activitymap-region-id="display"', memory_start
    )

    chip_items = [(normalize_imac_key(class_name), item_html) for class_name, item_html in extract_grid_item_blocks(chip_section)]
    storage_items = {
        normalize_imac_key(class_name): item_html
        for class_name, item_html in extract_grid_item_blocks(storage_section)
    }
    memory_items = {
        normalize_imac_key(class_name): item_html
        for class_name, item_html in extract_grid_item_blocks(memory_section)
    }
    display_type, display_items = parse_imac_display_items(display_section)

    records: list[dict[str, str | int]] = []
    screen_size = str(product_config["screen_size"])

    for config_key, chip_item in chip_items:
        chip_options = extract_chip_options(chip_item)
        storage_options = parse_storage_options(storage_items[config_key])
        memory_options = filter_imac_memory_options(parse_inline_memory_options(memory_items[config_key]))

        display_key = "4-port" if config_key.startswith("4-port") else "2-port"
        display_options = display_items[display_key]

        for chip in chip_options:
            for memory in memory_options:
                for storage in storage_options:
                    for display_option in display_options:
                        records.append(
                            {
                                "產品": str(product_config["name"]),
                                "產品代號": product_key,
                                "螢幕尺寸": screen_size,
                                "CPU 型號": str(chip["cpu_model"]),
                                "CPU/GPU 數量": f'{chip["cpu_cores"]} CPU / {chip["gpu_cores"]} GPU',
                                "記憶體": memory,
                                "儲存裝置": storage,
                                "顯示器": display_type,
                                "顯示器選項": display_option,
                            }
                        )

    return records


def build_records(product_key: str, product_config: dict[str, object], page_html: str) -> list[dict[str, str | int]]:
    parser_name = str(product_config.get("parser", "techspecs"))
    if parser_name == "techspecs":
        return build_techspecs_records(product_key, product_config, page_html)
    if parser_name == "imac":
        return build_imac_records(product_key, product_config, page_html)
    raise ValueError(f"Unsupported parser: {parser_name}")


def parse_products(selected_products: list[str]) -> dict[str, object]:
    data: list[dict[str, str | int]] = []
    sources: list[dict[str, str]] = []

    for product_key in selected_products:
        product_config = PRODUCTS[product_key]
        page_html = fetch_html(str(product_config["url"]))
        data.extend(build_records(product_key, product_config, page_html))
        sources.append({"產品": str(product_config["name"]), "網址": str(product_config["url"])})

    return {
        "資料日期": dt.datetime.now().astimezone().date().isoformat(),
        "產品數": len(selected_products),
        "筆數": len(data),
        "來源": sources,
        "資料": data,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Parse Apple Mac, Mac mini, and iMac specs pages and emit JSON."
    )
    parser.add_argument(
        "--product",
        choices=["all", "neo", "air", "pro", "mini", "imac"],
        default="all",
        help="Only emit one product family",
    )
    parser.add_argument("--output", help="Write JSON to a file instead of stdout")
    args = parser.parse_args()

    selected_products = list(PRODUCTS) if args.product == "all" else [args.product]
    payload = finalize_payload(parse_products(selected_products))
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.write("\n")
    else:
        sys.stdout.write(rendered)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

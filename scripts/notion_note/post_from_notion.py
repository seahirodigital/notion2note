#!/usr/bin/env python3
"""Notion記事を取得し、既存note投稿エンジンでnoteへ投稿する。"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import mimetypes
import os
import random
import re
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests


REPO_ROOT = Path(__file__).resolve().parents[2]
NOTE_ENGINE_PATH = REPO_ROOT / "scripts" / "note_engine" / "note_draft_poster.py"
AFFILIATE_FILE = REPO_ROOT / "affiliate_links.txt"
TAG_FILE = REPO_ROOT / "tag.md"
DEFAULT_RESULT_JSON = Path(tempfile.gettempdir()) / "notion_note_result.json"

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = os.getenv("NOTION_VERSION", "2022-06-28")
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
RETRY_DELAYS = (1.5, 3, 6)

DISCLOSURE_PREFIX = "Amazonのアソシエイトとして本アカウントは適格販売により収入を得ています"
DISCLOSURE_TEXT = (
    "Amazonのアソシエイトとして本アカウントは適格販売により収入を得ています。"
    "文章にはAIの整形・編集が含まれます。"
)
YOUTUBE_RE = re.compile(r"https?://(?:www\.)?(?:youtube\.com|youtu\.be)/\S+", re.IGNORECASE)
NOTION_ID_RE = re.compile(r"(?i)([0-9a-f]{32})")
NOTION_UUID_RE = re.compile(
    r"(?i)([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
)


@dataclass
class NotionImage:
    url: str
    caption: str = ""


class NotionClient:
    def __init__(self, token: str) -> None:
        if not token:
            raise RuntimeError("NOTION_API_KEY が設定されていません。")
        self.token = token

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }
        url = f"{NOTION_API_BASE}{path}"
        last_error = ""
        for attempt, wait_seconds in enumerate((0, *RETRY_DELAYS), start=1):
            if wait_seconds:
                time.sleep(wait_seconds)
            response = requests.request(method, url, headers=headers, json=body, timeout=60)
            if response.status_code in RETRYABLE_STATUS_CODES and attempt <= len(RETRY_DELAYS):
                last_error = f"HTTP {response.status_code}: {response.text[:500]}"
                continue
            if not response.ok:
                raise RuntimeError(f"Notion APIエラー HTTP {response.status_code}: {response.text[:1000]}")
            return response.json() if response.text else {}
        raise RuntimeError(f"Notion APIリクエストに失敗しました: {last_error}")

    def retrieve_page(self, page_id: str) -> dict[str, Any]:
        return self.request("GET", f"/pages/{hyphenate_notion_id(page_id)}")

    def list_children(self, block_id: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        cursor = ""
        while True:
            suffix = f"?start_cursor={cursor}" if cursor else ""
            data = self.request("GET", f"/blocks/{hyphenate_notion_id(block_id)}/children{suffix}")
            results.extend(data.get("results", []))
            if not data.get("has_more"):
                return results
            cursor = data.get("next_cursor") or ""
            if not cursor:
                return results


def hyphenate_notion_id(raw_id: str) -> str:
    value = str(raw_id or "").replace("-", "").lower()
    if not re.fullmatch(r"[0-9a-f]{32}", value):
        raise ValueError(f"Notion IDの形式が不正です: {raw_id}")
    return f"{value[0:8]}-{value[8:12]}-{value[12:16]}-{value[16:20]}-{value[20:32]}"


def notion_id_from_url(url_or_id: str) -> str:
    value = str(url_or_id or "").strip()
    compact = value.replace("-", "")
    if re.fullmatch(r"(?i)[0-9a-f]{32}", compact):
        return compact.lower()
    uuid_matches = NOTION_UUID_RE.findall(value.split("?", 1)[0]) or NOTION_UUID_RE.findall(value)
    if uuid_matches:
        return uuid_matches[-1].replace("-", "").lower()
    matches = NOTION_ID_RE.findall(value.split("?", 1)[0]) or NOTION_ID_RE.findall(value)
    if not matches:
        raise ValueError(f"NotionページIDを抽出できません: {url_or_id}")
    return matches[-1].lower()


def _text_from_rich_text(items: list[dict[str, Any]] | None) -> str:
    return "".join(str(item.get("plain_text") or "") for item in (items or [])).strip()


def _rich_text_to_markdown(items: list[dict[str, Any]] | None) -> str:
    chunks: list[str] = []
    for item in items or []:
        text = str(item.get("plain_text") or "")
        if not text:
            continue
        href = item.get("href") or ((item.get("text") or {}).get("link") or {}).get("url")
        annotations = item.get("annotations") or {}
        chunk = text
        if href and href != text:
            chunk = f"[{text}]({href})"
        if annotations.get("code"):
            chunk = f"`{chunk}`"
        if annotations.get("bold"):
            chunk = f"**{chunk}**"
        if annotations.get("italic"):
            chunk = f"*{chunk}*"
        chunks.append(chunk)
    return "".join(chunks).strip()


def _page_title(page: dict[str, Any]) -> str:
    for prop in (page.get("properties") or {}).values():
        if prop.get("type") == "title":
            title = _text_from_rich_text(prop.get("title"))
            if title:
                return title
    return "Notion記事"


def _property_plain_text(prop: dict[str, Any]) -> str:
    prop_type = prop.get("type")
    if prop_type == "url":
        return str(prop.get("url") or "").strip()
    if prop_type == "rich_text":
        return _text_from_rich_text(prop.get("rich_text"))
    if prop_type == "title":
        return _text_from_rich_text(prop.get("title"))
    if prop_type == "select":
        return str((prop.get("select") or {}).get("name") or "").strip()
    if prop_type == "multi_select":
        return " ".join(str(item.get("name") or "") for item in prop.get("multi_select") or []).strip()
    return ""


def _extract_youtube_url_from_page(page: dict[str, Any]) -> str:
    scored: list[tuple[int, str]] = []
    for name, prop in (page.get("properties") or {}).items():
        text = _property_plain_text(prop)
        match = YOUTUBE_RE.search(text)
        if not match:
            continue
        normalized_name = re.sub(r"[\s_-]+", "", str(name).lower())
        score = 100 if "youtube" in normalized_name or "youtu" in normalized_name else 50
        if "動画" in normalized_name or "url" in normalized_name:
            score += 20
        scored.append((score, match.group(0).rstrip(").,、。")))
    if not scored:
        return ""
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _file_url(value: dict[str, Any] | None) -> str:
    if not value:
        return ""
    value_type = value.get("type")
    if value_type == "external":
        return str((value.get("external") or {}).get("url") or "").strip()
    if value_type == "file":
        return str((value.get("file") or {}).get("url") or "").strip()
    return ""


def _image_from_block(block: dict[str, Any]) -> NotionImage | None:
    image = block.get("image") or {}
    url = _file_url(image)
    if not url:
        return None
    caption = _text_from_rich_text(image.get("caption"))
    return NotionImage(url=url, caption=caption)


def _is_transcript_heading(text: str) -> bool:
    normalized = re.sub(r"\s+", "", str(text or "")).lower()
    return "文字起こし" in normalized or "transcript" in normalized


def _normalize_heading(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


def _drop_duplicate_title_heading(lines: list[str], title: str) -> list[str]:
    updated = list(lines)
    while updated and not updated[0].strip():
        updated.pop(0)
    if updated and updated[0].startswith("# ") and _normalize_heading(updated[0][2:]) == _normalize_heading(title):
        updated.pop(0)
    while updated and not updated[0].strip():
        updated.pop(0)
    return updated


def _render_block(client: NotionClient, block: dict[str, Any], images: list[NotionImage], state: dict[str, bool]) -> list[str]:
    if state.get("stop"):
        return []

    block_type = block.get("type", "")
    data = block.get(block_type) or {}
    lines: list[str] = []

    if block_type == "heading_1":
        text = _rich_text_to_markdown(data.get("rich_text"))
        if text:
            lines.append(f"# {text}")
    elif block_type == "heading_2":
        text = _rich_text_to_markdown(data.get("rich_text"))
        if _is_transcript_heading(text):
            state["stop"] = True
            return []
        if text:
            lines.append(f"## {text}")
    elif block_type == "heading_3":
        text = _rich_text_to_markdown(data.get("rich_text"))
        if text:
            lines.append(f"### {text}")
    elif block_type == "paragraph":
        text = _rich_text_to_markdown(data.get("rich_text"))
        if text:
            lines.append(text)
    elif block_type == "bulleted_list_item":
        text = _rich_text_to_markdown(data.get("rich_text"))
        if text:
            lines.append(f"- {text}")
    elif block_type == "numbered_list_item":
        text = _rich_text_to_markdown(data.get("rich_text"))
        if text:
            lines.append(f"- {text}")
    elif block_type == "quote":
        text = _rich_text_to_markdown(data.get("rich_text"))
        if text:
            lines.append(f"> {text}")
    elif block_type == "to_do":
        text = _rich_text_to_markdown(data.get("rich_text"))
        prefix = "- [x]" if data.get("checked") else "- [ ]"
        if text:
            lines.append(f"{prefix} {text}")
    elif block_type == "code":
        text = _text_from_rich_text(data.get("rich_text"))
        language = str(data.get("language") or "")
        lines.extend([f"```{language}", text, "```"])
    elif block_type == "divider":
        lines.append("---")
    elif block_type == "image":
        image = _image_from_block(block)
        if image:
            images.append(image)
    elif block_type in {"bookmark", "embed", "video", "file", "pdf"}:
        url = _file_url(data) or str(data.get("url") or "").strip()
        if url:
            lines.append(url)
    elif block_type == "callout":
        text = _rich_text_to_markdown(data.get("rich_text"))
        if text:
            lines.append(text)

    if block.get("has_children") and not state.get("stop"):
        child_lines = _render_blocks(client, block.get("id", ""), images, state)
        if child_lines:
            lines.extend(child_lines)
    return lines


def _render_blocks(client: NotionClient, block_id: str, images: list[NotionImage], state: dict[str, bool]) -> list[str]:
    lines: list[str] = []
    for block in client.list_children(block_id):
        rendered = _render_block(client, block, images, state)
        if rendered:
            if lines and lines[-1] != "":
                lines.append("")
            lines.extend(rendered)
        if state.get("stop"):
            break
    return lines


def _sanitize_attr(value: str) -> str:
    return (
        str(value or "")
        .replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _image_html_lines(images: list[NotionImage]) -> list[str]:
    lines: list[str] = []
    for index, image in enumerate(images, start=1):
        alt = image.caption or f"Notion画像{index}"
        lines.extend(["", f'<img src="{_sanitize_attr(image.url)}" alt="{_sanitize_attr(alt)}">'])
    return lines


def _insert_images_after_executive_summary(markdown: str, images: list[NotionImage]) -> str:
    if not images:
        return markdown

    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    h2_indices = [index for index, line in enumerate(lines) if line.startswith("## ") and not line.startswith("### ")]
    target_index = -1
    for order, h2_index in enumerate(h2_indices):
        heading = _normalize_heading(lines[h2_index][3:])
        if "エグゼクティブサマリー" in heading or "executivesummary" in heading:
            next_h2 = h2_indices[order + 1] if order + 1 < len(h2_indices) else len(lines)
            target_index = next_h2
            break

    if target_index < 0:
        target_index = h2_indices[1] if len(h2_indices) > 1 else len(lines)

    while target_index > 0 and not lines[target_index - 1].strip():
        target_index -= 1
    insert_lines = _image_html_lines(images)
    updated = lines[:target_index] + insert_lines + [""] + lines[target_index:]
    return "\n".join(updated).strip() + "\n"


def _strip_frontmatter(markdown: str) -> str:
    return re.sub(r"\A---\s*\n[\s\S]*?\n---\s*\n?", "", str(markdown or ""), count=1)


def _read_affiliate_memo(path: Path, memo_number: int) -> str:
    if not path.exists():
        print(f"   [警告] アフィリエイトファイルが見つかりません: {path}")
        return ""
    raw = path.read_text(encoding="utf-8")
    parts = re.split(r"===MEMO(\d+)===", raw)
    if len(parts) <= 1:
        return raw.strip()
    for index in range(1, len(parts), 2):
        if int(parts[index]) != memo_number:
            continue
        body = (parts[index + 1] if index + 1 < len(parts) else "").strip()
        if "---" in body:
            _meta, body = body.split("---", 1)
        return body.strip()
    return ""


def _split_affiliate_blocks(memo_content: str) -> list[str]:
    blocks = [
        block.strip()
        for block in re.split(r"(?=▼)", memo_content)
        if block.strip() and block.strip().startswith("▼")
    ]
    return blocks or ([memo_content.strip()] if memo_content.strip() else [])


def _insert_affiliate_after_each_h2(
    markdown: str,
    affiliate_file: Path,
    memo_number: int,
    per_h2_count: int,
    seed: str = "",
) -> tuple[str, int]:
    memo_content = _read_affiliate_memo(affiliate_file, memo_number)
    blocks = _split_affiliate_blocks(memo_content)
    if not blocks or per_h2_count <= 0:
        return markdown, 0

    rng = random.Random(seed) if seed else random.SystemRandom()
    lines = markdown.splitlines(keepends=True)
    h2_indices = [index for index, line in enumerate(lines) if line.startswith("## ") and not line.startswith("### ")]
    insertions: list[tuple[int, str]] = []
    for order, h2_index in enumerate(h2_indices):
        next_h2_index = h2_indices[order + 1] if order + 1 < len(h2_indices) else len(lines)
        insert_index = next_h2_index
        while insert_index > h2_index + 1 and not lines[insert_index - 1].strip():
            insert_index -= 1
        if len(blocks) >= per_h2_count:
            selected = rng.sample(blocks, per_h2_count)
        else:
            selected = [rng.choice(blocks) for _ in range(per_h2_count)]
        insertions.append((insert_index, "\n\n" + "\n\n".join(selected) + "\n\n"))

    for insert_index, block in sorted(insertions, key=lambda item: item[0], reverse=True):
        lines = lines[:insert_index] + [block] + lines[insert_index:]
    return "".join(lines), len(insertions) * per_h2_count


def _read_tags(path: Path) -> str:
    if not path.exists():
        print(f"   [警告] タグファイルが見つかりません: {path}")
        return ""
    tags: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("//"):
            continue
        for token in re.split(r"[\s,、]+", text):
            cleaned = token.strip().lstrip("#")
            if cleaned:
                tags.append(cleaned)
    return " ".join(dict.fromkeys(tags))


def _page_cover_image(page: dict[str, Any]) -> NotionImage | None:
    cover = page.get("cover")
    url = _file_url(cover)
    return NotionImage(url=url, caption="Notionカバー画像") if url else None


def build_markdown_from_notion(client: NotionClient, page_id: str) -> tuple[str, dict[str, Any]]:
    page = client.retrieve_page(page_id)
    title = _page_title(page)
    youtube_url = _extract_youtube_url_from_page(page)
    images: list[NotionImage] = []
    lines = _render_blocks(client, page_id, images, {"stop": False})
    lines = _drop_duplicate_title_heading(lines, title)
    body = "\n".join(lines).strip()
    body = _strip_frontmatter(body)
    body = _insert_images_after_executive_summary(body, images)

    top_image = _page_cover_image(page) or (images[0] if images else None)
    output_lines = [f"# {title}", ""]
    if youtube_url:
        output_lines.extend([youtube_url, ""])
    output_lines.extend([DISCLOSURE_TEXT, ""])
    output_lines.extend(body.splitlines())
    markdown = "\n".join(output_lines).strip() + "\n"
    return markdown, {
        "title": title,
        "youtube_url": youtube_url,
        "image_count": len(images),
        "top_image_url": top_image.url if top_image else "",
    }


def _suffix_from_mime_or_url(mime_type: str, source_url: str) -> str:
    suffix = mimetypes.guess_extension((mime_type or "").split(";")[0].strip()) or ""
    if suffix:
        return suffix
    parsed_suffix = Path(urlparse(source_url).path).suffix
    return parsed_suffix if re.match(r"^\.[A-Za-z0-9]{2,8}$", parsed_suffix) else ".png"


def _write_temp_image(data: bytes, suffix: str) -> Path:
    handle = tempfile.NamedTemporaryFile(prefix="notion_note_top_image_", suffix=suffix, delete=False)
    with handle:
        handle.write(data)
    return Path(handle.name)


def _download_image(source: str) -> tuple[Path | None, str]:
    if not source:
        return None, ""
    if source.startswith("data:"):
        match = re.match(r"^data:(image/[A-Za-z0-9.+-]+);base64,(.+)$", source, flags=re.DOTALL)
        if not match:
            return None, source
        mime_type, encoded = match.groups()
        return _write_temp_image(base64.b64decode(encoded), _suffix_from_mime_or_url(mime_type, "")), source
    if source.startswith("blob:"):
        print(f"   [警告] blob画像はGitHub Actionsから参照できないため、トップ画像をスキップします: {source}")
        return None, source
    try:
        response = requests.get(source, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if content_type and not content_type.lower().startswith("image/"):
            print(f"   [警告] トップ画像URLのContent-Typeが画像ではありません: {content_type}")
            return None, source
        return _write_temp_image(response.content, _suffix_from_mime_or_url(content_type, source)), source
    except Exception as exc:
        print(f"   [警告] トップ画像の取得に失敗しました: {source} / {exc}")
        return None, source


def _load_note_engine():
    spec = importlib.util.spec_from_file_location("notion_note_draft_runtime", NOTE_ENGINE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"note投稿エンジンを読み込めません: {NOTE_ENGINE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["notion_note_draft_runtime"] = module
    spec.loader.exec_module(module)
    return module


def _write_result_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"   [情報] 結果JSONを書き出しました: {path}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Notion記事をnoteへ投稿する")
    parser.add_argument("--page-id", default=os.getenv("NOTION_PAGE_ID", ""), help="NotionページID")
    parser.add_argument("--page-url", default=os.getenv("NOTION_PAGE_URL", ""), help="NotionページURL")
    parser.add_argument("--publish", action="store_true", help="下書き作成後に公開投稿まで進める")
    parser.add_argument("--dry-run-publish", action="store_true", help="公開画面まで進めるが最後の投稿ボタンは押さない")
    parser.add_argument("--no-ogp", action="store_true", help="OGP展開をスキップする")
    parser.add_argument("--no-top-image", action="store_true", help="Notion先頭画像のnoteトップ画像設定をスキップする")
    parser.add_argument("--no-toc", action="store_true", help="目次挿入をスキップする")
    parser.add_argument("--affiliate-file", default=str(AFFILIATE_FILE), help="アフィリエイトテキストファイルのフルパス")
    parser.add_argument("--affiliate-memo", type=int, default=int(os.getenv("NOTION_NOTE_AFFILIATE_MEMO", "1")), help="使用するMEMO番号")
    parser.add_argument("--affiliate-count", type=int, default=int(os.getenv("NOTION_NOTE_AFFILIATE_COUNT", "2")), help="各H2章末に入れるアフィリエイトブロック数")
    parser.add_argument("--affiliate-seed", default=os.getenv("NOTION_NOTE_AFFILIATE_SEED", ""), help="ランダム挿入を固定したい場合のseed")
    parser.add_argument("--tag-file", default=str(TAG_FILE), help="note投稿タグファイルのフルパス")
    parser.add_argument("--dump-markdown", default="", help="整形済みMarkdownを書き出すフルパス")
    parser.add_argument("--result-json", default=str(DEFAULT_RESULT_JSON), help="結果JSONを書き出すフルパス")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    raw_page_id = args.page_id or args.page_url
    if not raw_page_id:
        raise RuntimeError("--page-id または --page-url を指定してください。")
    page_id = notion_id_from_url(raw_page_id)

    client = NotionClient(os.getenv("NOTION_API_KEY", ""))
    markdown, preprocess = build_markdown_from_notion(client, page_id)
    markdown, affiliate_insertions = _insert_affiliate_after_each_h2(
        markdown,
        affiliate_file=Path(args.affiliate_file),
        memo_number=max(1, args.affiliate_memo),
        per_h2_count=max(0, args.affiliate_count),
        seed=args.affiliate_seed,
    )
    tags = _read_tags(Path(args.tag_file))

    top_image_path, top_image_source = (None, "")
    if not args.no_top_image and preprocess.get("top_image_url"):
        top_image_path, top_image_source = _download_image(preprocess["top_image_url"])

    if args.dump_markdown:
        dump_path = Path(args.dump_markdown)
        dump_path.parent.mkdir(parents=True, exist_ok=True)
        dump_path.write_text(markdown, encoding="utf-8")
        print(f"   [情報] 整形済みMarkdownを書き出しました: {dump_path}")

    note_engine = _load_note_engine()
    result = note_engine.post_draft_to_note(
        markdown,
        run_ogp=not args.no_ogp,
        run_top_image=bool(top_image_path),
        insert_toc=not args.no_toc,
        publish=args.publish or args.dry_run_publish,
        dry_run_publish=args.dry_run_publish,
        publish_tags=tags or getattr(note_engine, "NOTE_POST_TAGS", ""),
        top_image_path=str(top_image_path) if top_image_path else "",
    )
    result["notion_note_preprocess"] = {
        **preprocess,
        "page_id": page_id,
        "affiliate_file": str(Path(args.affiliate_file)),
        "affiliate_memo": max(1, args.affiliate_memo),
        "affiliate_insertions": affiliate_insertions,
        "tag_file": str(Path(args.tag_file)),
        "tag_count": len(tags.split()) if tags else 0,
        "top_image_source": top_image_source,
        "top_image_path": str(top_image_path) if top_image_path else "",
    }
    _write_result_json(Path(args.result_json), result)

    if result.get("success"):
        label = "公開投稿" if (args.publish or args.dry_run_publish) else "下書き投稿"
        print(f"\n[OK] Notion記事のnote {label} が完了しました")
        print(f"   タイトル: {result.get('title', '')}")
        print(f"   下書きURL: {result.get('url', '')}")
        if result.get("published_url"):
            print(f"   公開後URL: {result.get('published_url', '')}")
        return 0

    print("\n[ERROR] Notion記事のnote投稿に失敗しました")
    print(json.dumps(result, ensure_ascii=False, indent=2)[:2000])
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

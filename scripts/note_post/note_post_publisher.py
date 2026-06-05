#!/usr/bin/env python3
"""Markdownをnoteへ本番投稿する公開専用ラッパー。"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests


REPO_ROOT = Path(__file__).resolve().parents[2]
NOTE_ENGINE_PATH = REPO_ROOT / "scripts" / "note_engine" / "note_draft_poster.py"
TAG_FILE = REPO_ROOT / "tag.md"
DEFAULT_RESULT_JSON = Path(tempfile.gettempdir()) / "note_post_result.json"
NOTE_PUBLISH_MAX_TAGS = int(os.getenv("NOTE_PUBLISH_MAX_TAGS", "98"))
DISCORD_WEBHOOK_URL = os.getenv("NOTION2NOTE_DISCORD_WEBHOOK", "").strip()
DISCORD_X_TEMPLATE_TAGS = "#投資初心者 #投資 #デイトレ #日本株 #日経平均 #米国株 #高配当 #FX #ドル円"
HTTP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


def _load_note_engine():
    spec = importlib.util.spec_from_file_location("note_post_engine_runtime", NOTE_ENGINE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"note投稿エンジンを読み込めません: {NOTE_ENGINE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["note_post_engine_runtime"] = module
    spec.loader.exec_module(module)
    return module


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
    unique_tags = list(dict.fromkeys(tags))
    if NOTE_PUBLISH_MAX_TAGS > 0 and len(unique_tags) > NOTE_PUBLISH_MAX_TAGS:
        print(
            "   [情報] note投稿タグを先頭から"
            f"{NOTE_PUBLISH_MAX_TAGS}件に制限します: {len(unique_tags)}件 → {NOTE_PUBLISH_MAX_TAGS}件"
        )
        unique_tags = unique_tags[:NOTE_PUBLISH_MAX_TAGS]
    return " ".join(unique_tags)


def _write_result_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"   [情報] 結果JSONを書き出しました: {path}")


def _build_discord_x_template(note_url: str) -> str:
    return f"【投資Youtube記録】\n\n{note_url}\n\n{DISCORD_X_TEMPLATE_TAGS}"


def _is_public_note_url(note_url: str) -> bool:
    parsed = urlparse(note_url or "")
    if parsed.scheme not in {"http", "https"}:
        return False
    if parsed.netloc == "editor.note.com":
        return False
    if parsed.netloc != "note.com" and not parsed.netloc.endswith(".note.com"):
        return False
    if "/publish" in parsed.path:
        return False
    return bool(re.search(r"/(?:notes/|n/)?n[0-9a-f]{8,}(?:[/?#]|$)", note_url or "", re.IGNORECASE))


def _public_note_url_is_reachable(note_url: str) -> dict[str, Any]:
    status: dict[str, Any] = {
        "ok": False,
        "url": note_url,
        "status_code": 0,
        "final_url": "",
        "error": "",
        "sample_text": "",
    }
    if not _is_public_note_url(note_url):
        status["error"] = f"公開URL形式ではありません: {note_url}"
        return status

    try:
        response = requests.get(
            note_url,
            headers={
                "User-Agent": HTTP_UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
            allow_redirects=True,
            timeout=30,
        )
    except requests.RequestException as exc:
        status["error"] = str(exc)
        return status

    status["status_code"] = int(response.status_code)
    status["final_url"] = response.url
    compact_text = re.sub(r"\s+", " ", response.text or "").strip()
    status["sample_text"] = compact_text[:200]
    unavailable_words = [
        "記事が見つかりません",
        "ページが見つかりません",
        "お探しの記事は見つかりません",
        "存在しません",
        "非公開",
        "削除されました",
    ]
    if response.status_code != 200:
        status["error"] = f"HTTP {response.status_code}"
        return status
    if any(word in compact_text for word in unavailable_words):
        status["error"] = "未公開または存在しないページ文言を検出しました"
        return status
    status["ok"] = True
    return status


def notify_discord_after_publish(note_url: str) -> dict[str, Any]:
    status: dict[str, Any] = {
        "attempted": False,
        "success": False,
        "webhook_configured": bool(DISCORD_WEBHOOK_URL),
        "url": note_url,
        "error": "",
    }
    if not note_url:
        status["error"] = "公開後URLが空のためDiscord通知をスキップしました。"
        print(f"   [警告] {status['error']}")
        return status
    if not _is_public_note_url(note_url):
        status["error"] = f"公開済みURLではないためDiscord通知をスキップしました: {note_url}"
        print(f"   [警告] {status['error']}")
        return status
    reachability = _public_note_url_is_reachable(note_url)
    status["public_reachability"] = reachability
    if not reachability.get("ok"):
        status["error"] = f"未ログインで公開確認できないためDiscord通知をスキップしました: {reachability}"
        print(f"   [警告] {status['error']}")
        return status
    note_url = str(reachability.get("final_url") or note_url)
    status["url"] = note_url
    if not DISCORD_WEBHOOK_URL:
        status["error"] = "NOTION2NOTE_DISCORD_WEBHOOK が未設定のためDiscord通知をスキップしました。"
        print(f"   [情報] {status['error']}")
        return status

    status["attempted"] = True
    message = _build_discord_x_template(note_url)
    try:
        response = requests.post(DISCORD_WEBHOOK_URL, json={"content": message}, timeout=15)
    except requests.RequestException as exc:
        status["error"] = str(exc)
        print(f"   [警告] Discord通知に失敗しました: {exc}")
        return status

    if response.ok:
        status["success"] = True
        print("   [OK] Discordへ本番投稿通知を送信しました")
        return status

    status["error"] = f"Discord API {response.status_code}: {response.text[:300]}"
    print(f"   [警告] Discord通知に失敗しました: {status['error']}")
    return status


def publish_markdown_to_note(
    markdown: str,
    run_ogp: bool = True,
    run_top_image: bool = True,
    insert_toc: bool = True,
    publish_tags: str = "",
    top_image_path: str = "",
    body_image_uploads: list[dict] | None = None,
    dry_run_publish: bool = False,
) -> dict[str, Any]:
    note_engine = _load_note_engine()
    tags = publish_tags or _read_tags(TAG_FILE) or getattr(note_engine, "NOTE_POST_TAGS", "")
    result: dict[str, Any] = note_engine.post_draft_to_note(
        markdown,
        run_ogp=run_ogp,
        run_top_image=run_top_image,
        insert_toc=insert_toc,
        publish=True,
        dry_run_publish=dry_run_publish,
        publish_tags=tags,
        top_image_path=top_image_path,
        body_image_uploads=body_image_uploads,
    )
    if not dry_run_publish and result.get("success"):
        published_url = str(result.get("published_url") or "")
        if not _is_public_note_url(published_url):
            result["success"] = False
            result["publish_url_error"] = f"公開済みURLを確認できませんでした: {published_url}"
            result["discord_notification"] = {
                "attempted": False,
                "success": False,
                "webhook_configured": bool(DISCORD_WEBHOOK_URL),
                "url": published_url,
                "error": result["publish_url_error"],
            }
            return result
        reachability = _public_note_url_is_reachable(published_url)
        result["public_reachability"] = reachability
        if not reachability.get("ok"):
            result["success"] = False
            result["publish_url_error"] = f"未ログインで公開確認できませんでした: {reachability}"
            result["discord_notification"] = {
                "attempted": False,
                "success": False,
                "webhook_configured": bool(DISCORD_WEBHOOK_URL),
                "url": published_url,
                "error": result["publish_url_error"],
                "public_reachability": reachability,
            }
            return result
        result["published_url"] = str(reachability.get("final_url") or published_url)
        result["discord_notification"] = notify_discord_after_publish(result["published_url"])
    else:
        result["discord_notification"] = {
            "attempted": False,
            "success": False,
            "webhook_configured": bool(DISCORD_WEBHOOK_URL),
            "url": "",
            "error": "本番投稿完了ではないためDiscord通知をスキップしました。",
        }
    return result


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Markdown記事をnoteへ本番投稿する")
    parser.add_argument("file", nargs="?", default="", help="投稿するMarkdownファイルのフルパス")
    parser.add_argument("--content", default="", help="Markdown本文を直接指定する")
    parser.add_argument("--no-ogp", action="store_true", help="OGP展開をスキップする")
    parser.add_argument("--no-top-image", action="store_true", help="トップ画像設定をスキップする")
    parser.add_argument("--top-image-path", default="", help="noteトップ画像に使う画像ファイルのフルパス")
    parser.add_argument("--no-toc", action="store_true", help="目次挿入をスキップする")
    parser.add_argument("--dry-run-publish", action="store_true", help="公開画面まで進めるが最後の投稿ボタンは押さない")
    parser.add_argument("--publish-tags", default="", help="note公開時に設定するタグ")
    parser.add_argument("--tag-file", default=str(TAG_FILE), help="note投稿タグファイルのフルパス")
    parser.add_argument("--result-json", default=str(DEFAULT_RESULT_JSON), help="結果JSONを書き出すフルパス")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.content:
        markdown = args.content
    elif args.file:
        markdown = Path(args.file).read_text(encoding="utf-8")
    else:
        raise RuntimeError("投稿するMarkdownファイル、または --content を指定してください。")

    publish_tags = args.publish_tags or _read_tags(Path(args.tag_file))
    result = publish_markdown_to_note(
        markdown,
        run_ogp=not args.no_ogp,
        run_top_image=not args.no_top_image or bool(args.top_image_path),
        insert_toc=not args.no_toc,
        publish_tags=publish_tags,
        top_image_path=args.top_image_path,
        dry_run_publish=args.dry_run_publish,
    )
    _write_result_json(Path(args.result_json), result)

    if result.get("success"):
        print("\n[OK] note本番投稿が完了しました")
        print(f"   タイトル: {result.get('title', '')}")
        print(f"   下書きURL: {result.get('url', '')}")
        if result.get("published_url"):
            print(f"   公開後URL: {result.get('published_url', '')}")
        return 0

    print("\n[ERROR] note本番投稿に失敗しました", file=sys.stderr)
    print(json.dumps(result, ensure_ascii=False, indent=2), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

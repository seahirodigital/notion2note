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

import requests


REPO_ROOT = Path(__file__).resolve().parents[2]
NOTE_ENGINE_PATH = REPO_ROOT / "scripts" / "note_engine" / "note_draft_poster.py"
TAG_FILE = REPO_ROOT / "tag.md"
DEFAULT_RESULT_JSON = Path(tempfile.gettempdir()) / "note_post_result.json"
DISCORD_WEBHOOK_URL = os.getenv("NOTION2NOTE_DISCORD_WEBHOOK", "").strip()
DISCORD_X_TEMPLATE_TAGS = "#投資初心者 #投資 #デイトレ #日本株 #日経平均 #米国株 #高配当 #FX #ドル円"


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
    return " ".join(dict.fromkeys(tags))


def _write_result_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"   [情報] 結果JSONを書き出しました: {path}")


def _build_discord_x_template(note_url: str) -> str:
    return f"【投資Youtube記録】\n\n{note_url}\n\n{DISCORD_X_TEMPLATE_TAGS}"


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
        result["discord_notification"] = notify_discord_after_publish(str(result.get("published_url") or ""))
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

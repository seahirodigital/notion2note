"""
note下書きポスター v4.0 — API直接投稿版（Playwright不要）
noteの内部APIにHTTPリクエストで直接下書き保存する。

【完全自動化の仕組み】
1. NOTE_STORAGE_SECRET_NAME で指定した GitHub Secret からCookieを復元
2. Cookie無効時 → APIログインで自動再認証（ブラウザ不要）
3. POST /api/v1/text_notes で下書き作成
4. 操作後、最新CookieをGitHub Secretに自動上書き
5. 定期cron（note-keepalive.yml）でセッションを延命

【初回セットアップのみ手動】
  python prompts/05-draft-manager/note_draft_poster.py --save-cookies
  → 出力されたJSONを GitHub Secret「NOTE_STORAGE_SECRET_NAME で指定した名前」に登録

【通常実行（GitHub Actions）】
  python prompts/05-draft-manager/note_draft_poster.py <file.md>
"""

from __future__ import annotations

import os
import sys
import json
import time
import re
import base64
import argparse
import importlib.util
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests as http_requests

# ── 設定 ──────────────────────────────────────────────
NOTE_API_BASE       = "https://note.com/api"
NOTE_EMAIL          = os.getenv("NOTE_EMAIL", "")
NOTE_PASSWORD       = os.getenv("NOTE_PASSWORD", "")
NOTE_STORAGE_STATE  = os.getenv("NOTE_STORAGE_STATE", "")   # JSON (GitHub Secret)
GITHUB_TOKEN        = os.getenv("GITHUB_TOKEN", "")          # PAT (secrets:write)
GITHUB_REPOSITORY   = os.getenv("GITHUB_REPOSITORY", "seahirodigital/notion2note")
GITHUB_REPO_OWNER, GITHUB_REPO_NAME = (GITHUB_REPOSITORY.split("/", 1) + ["notion2note"])[:2]
NOTE_STORAGE_SECRET_NAME = os.getenv("NOTE_STORAGE_SECRET_NAME", "NOTE_STORAGE_STATE")

SCRIPT_DIR        = Path(__file__).resolve().parent
LOCAL_STATE_FILE  = SCRIPT_DIR / "note_storage_state.json"   # ローカル保存先
ADOBE_STORAGE_STATE_FILE = SCRIPT_DIR / "adobe_express_storage_state.json"
AMAZON_PROMPTS_DIR = SCRIPT_DIR.parent / "04-affiliate-link-manager"
AMAZON_AFFILIATE_SCRIPT = AMAZON_PROMPTS_DIR / "insert_amazon_affiliate.py"
AMAZON_TOP_IMAGE_SCRIPT = AMAZON_PROMPTS_DIR / "amazon_gazo_get.py"
NOTE_TOP_IMAGE_ARTIFACTS_DIR = SCRIPT_DIR.parent.parent / "debug" / "note_gazo_test" / "artifacts"
NOTE_TOP_IMAGE_DEBUG = os.getenv("NOTE_TOP_IMAGE_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
NOTE_TOP_IMAGE_USE_ADOBE = os.getenv("NOTE_TOP_IMAGE_USE_ADOBE", "").strip().lower() in {"1", "true", "yes", "on"}

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

NOTE_DISCLOSURE_PREFIX = "Amazonのアソシエイトとして本アカウントは適格販売により収入を得ています"
NOTE_DISCLOSURE_FULL_TEXT = (
    "Amazonのアソシエイトとして本アカウントは適格販売により収入を得ています。"
    "文章にはAIの整形・編集が含まれます。"
)
NOTE_DISCLOSURE_MARKERS = [NOTE_DISCLOSURE_FULL_TEXT, NOTE_DISCLOSURE_PREFIX]
NOTE_TOC_MARKER = "[[NOTION_NOTE_TOC]]"
NOTE_POST_TAGS = (
    "エッセイ 写真 毎日note 小説 イラスト 競艇予想屋 ボートレース予想 競輪予想 "
    "スキしてみて note 毎日更新  仕事 音楽 マンガ コラム 人生 自分 競艇投資 "
    "毎日投稿 読書 ビジネス AI 投資 競輪 映画 日常 note毎日更新 予想 副業  "
    "恋愛 絵 的中 ギャンブル 言葉 ブログ 地方競馬予想 ゲーム ダイエット  "
    "健康 ラジオ 英語 大学生 youtube  教育 創作 人工知能 生き方 プログラミング "
    "猫 最近の学び  料理 漫画 旅行 勉強 競艇予想士 お金 動画 python 短編小説 "
    "中央競馬予想 コミュニケーション デザイン  人間関係 アート 本 分析 音声配信 "
    "スピリチュアル 転職 生活 家族 機械学習 起業 ショートショート 占い  "
    "ビッグデータ 幸せ  時間 オリジナル コーチング 心理学 オートレース予想 "
    "マーケティング 旅 夢  哲学  フリーランス  自己啓発 ライフスタイル カメラ "
    "Youtube動画 SNS ネットビジネス 記事 アニメ キャリア 学校 エンタメ"
)

# ── OGP展開設定 ────────────────────────────────────────
NOTE_PUBLISH_SETTINGS_READY_TIMEOUT_MS = int(os.getenv("NOTE_PUBLISH_SETTINGS_READY_TIMEOUT_MS", "45000"))
NOTE_PUBLISH_SETTINGS_READY_POLL_MS = int(os.getenv("NOTE_PUBLISH_SETTINGS_READY_POLL_MS", "500"))
NOTE_PUBLISH_MAGAZINE_NAME = os.getenv("NOTE_PUBLISH_MAGAZINE_NAME", "投資Youtube学習記録")
NOTE_PUBLISH_COMPLETE_TIMEOUT_MS = int(os.getenv("NOTE_PUBLISH_COMPLETE_TIMEOUT_MS", "90000"))
NOTE_PUBLISH_COMPLETE_POLL_MS = int(os.getenv("NOTE_PUBLISH_COMPLETE_POLL_MS", "1000"))
NOTE_PUBLISH_MAX_TAGS = int(os.getenv("NOTE_PUBLISH_MAX_TAGS", "98"))
NOTE_CLOUDFRONT_RETRY_DELAYS = tuple(
    int(part.strip())
    for part in os.getenv("NOTE_CLOUDFRONT_RETRY_DELAYS", "").split(",")
    if part.strip()
)

EDITOR_CONTENT_SELECTOR  = ".ProseMirror p, .ProseMirror h2, .ProseMirror h3"
EDITOR_LOAD_TIMEOUT_SEC  = 60
OGP_TARGET_DOMAINS       = ["amzn.to", "amazon.co.jp", "apple.com", "youtube.com"]
TOP_IMAGE_BUTTON_SELECTOR = 'button[aria-label="画像を追加"]'
PAGE_IMAGE_SELECTOR = "main img"
CROP_DIALOG_SELECTOR = "div.ReactModal__Content.CropModal__content[role='dialog'][aria-modal='true']"
TOP_IMAGE_LOADING_SELECTOR = "main div[class*='sc-e17b66d3-0']"
URL_RE = re.compile(r"https?://[^\s\n\r<>\"']+")


class NoteLoginRequiresManualAction(Exception):
    """note側の認証要求により、無人環境ではログインを続行できない状態。"""


def _print_manual_cookie_refresh_steps(reason: str) -> None:
    """GitHub Actionsログに、手動Cookie更新の復旧手順を出力する。"""
    print(f"   ⚠️ 自動セッション復旧不可: {reason}")
    print("   📋 手動復旧手順:")
    print("      1. ローカル端末で次のフォルダへ移動してください。")
    print("         C:\\Users\\mahha\\OneDrive\\開発\\notion2note")
    print("      2. 次のコマンドを実行してください。")
    print("         python scripts\\note_engine\\note_draft_poster.py --save-cookies")
    print("      3. 起動したブラウザで note に手動ログインしてください。")
    print("      4. Cookie が自動更新されない場合は、出力された JSON を GitHub Secret NOTE_STORAGE_STATE に登録してください。")
    print("      5. GitHub Actions の「Note セッション維持（自動）」を手動再実行してください。")
    print("   理由: note が reCAPTCHA などのブラウザ操作を要求しており、GitHub Actions の無人実行では認証を完了できません。")

# OGP展開用JS関数群 (note_ogp_opener.py から移植)
JS_FUNCTIONS = r"""
window.noteFormatter = {
    getTitleInput: () => document.querySelector('.note-editor__title-input'),
    getEditor: () => document.querySelector('.note-editable, [contenteditable="true"]') || document.querySelector('.ProseMirror'),

    processTitle: function() {
        const titleInput = this.getTitleInput();
        const editor = this.getEditor();
        if (!titleInput || !editor) return;
        if (titleInput.textContent.trim().length > 10) return;
        const firstP = editor.querySelector('p');
        if (firstP) {
            let text = firstP.textContent.trim().replace(/^#+\s*/, '');
            titleInput.textContent = text;
            titleInput.dispatchEvent(new Event('input', { bubbles: true }));
        }
    },

    convertMarkdownToHtml: function() {
        const editor = this.getEditor();
        if(!editor) return;
        const paragraphs = Array.from(editor.querySelectorAll('p'));
        paragraphs.forEach(p => {
            let text = p.textContent.trim();
            let newEl = null;
            if (text.startsWith('### ')) {
                newEl = document.createElement('h3');
                newEl.textContent = text.replace('### ', '');
            } else if (text.startsWith('## ') || text.startsWith('# ')) {
                newEl = document.createElement('h2');
                newEl.textContent = text.replace(/#+\s*/, '');
            }
            if (newEl) p.parentNode.replaceChild(newEl, p);
        });

        const walker = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT);
        const nodesToFix = [];
        let node;
        while ((node = walker.nextNode())) {
            if (node.textContent.includes('**')) nodesToFix.push(node);
        }
        nodesToFix.forEach(textNode => {
            const parent = textNode.parentNode;
            if (!parent) return;
            const parts = textNode.textContent.split(/(\*\*.*?\*\*)/g);
            const fragment = document.createDocumentFragment();
            parts.forEach(part => {
                if (part.startsWith('**') && part.endsWith('**')) {
                    const strong = document.createElement('strong');
                    strong.textContent = part.slice(2, -2);
                    fragment.appendChild(strong);
                } else {
                    fragment.appendChild(document.createTextNode(part));
                }
            });
            parent.replaceChild(fragment, textNode);
        });
    },

    extractUrls: function() {
        const editor = this.getEditor();
        if(!editor) return [];
        const urls = [];
        const regex = /(https?:\/\/[^\s\n\r<>"]+)/g;
        let match;
        while ((match = regex.exec(editor.innerText)) !== null) {
            urls.push(match[1]);
        }
        return urls;
    },

    setCaretAtUrlEnd: function(url, occurrence) {
        const editor = this.getEditor();
        if(!editor) return false;
        const selection = window.getSelection();
        const range = document.createRange();
        const walker = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT);
        let node, count = 0;
        while ((node = walker.nextNode())) {
            let startIdx = 0, idx;
            while ((idx = node.textContent.indexOf(url, startIdx)) !== -1) {
                count++;
                if (count === occurrence) {
                    range.setStart(node, idx + url.length);
                    range.setEnd(node, idx + url.length);
                    selection.removeAllRanges();
                    selection.addRange(range);
                    editor.focus();
                    return true;
                }
                startIdx = idx + 1;
            }
        }
        return false;
    },

    normalizeLineBreaks: function() {
        const editor = this.getEditor();
        if(!editor) return 0;
        let removed = 0;

        const embeds = editor.querySelectorAll(
            'div[class*="embed"], div[class*="ogp"], div[class*="Embed"], ' +
            'div[class*="card"], figure, div[data-type]'
        );
        embeds.forEach(embed => {
            let prev = embed.previousElementSibling;
            while (prev && prev.tagName === 'P' && prev.textContent.trim() === '') {
                const toRemove = prev;
                prev = prev.previousElementSibling;
                toRemove.remove();
                removed++;
            }
            let next = embed.nextElementSibling;
            while (next && next.tagName === 'P' && next.textContent.trim() === '') {
                const toRemove = next;
                next = next.nextElementSibling;
                toRemove.remove();
                removed++;
            }
        });

        const allP = Array.from(editor.querySelectorAll('p'));
        let prevWasEmpty = false;
        for (const p of allP) {
            const isEmpty = p.textContent.trim() === '' && p.children.length === 0;
            if (isEmpty) {
                if (prevWasEmpty) {
                    p.remove();
                    removed++;
                } else {
                    prevWasEmpty = true;
                }
            } else {
                prevWasEmpty = false;
            }
        }

        return removed;
    }
};
"""


def _load_module_from_path(module_name: str, module_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"モジュールを読み込めません: {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _load_amazon_affiliate_module():
    return _load_module_from_path("insert_amazon_affiliate_runtime", AMAZON_AFFILIATE_SCRIPT)


def _load_amazon_top_image_module():
    return _load_module_from_path("amazon_gazo_get_runtime", AMAZON_TOP_IMAGE_SCRIPT)


def _write_json(path: Path, payload) -> None:
    if not NOTE_TOP_IMAGE_DEBUG:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    if not NOTE_TOP_IMAGE_DEBUG:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _dump_page_artifacts(page, artifacts_dir: Path, stem: str) -> dict:
    if not NOTE_TOP_IMAGE_DEBUG:
        return {}
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = artifacts_dir / f"{stem}.png"
    html_path = artifacts_dir / f"{stem}.html"
    page.screenshot(path=str(screenshot_path), full_page=True)
    _write_text(html_path, page.content())
    return {
        "screenshot": str(screenshot_path),
        "html": str(html_path),
    }


def _collect_control_snapshot(page) -> list[dict]:
    locator = page.locator("input[type='file'], button, [role='button'], label")
    return locator.evaluate_all(
        """
        (els) => els.map((el, index) => {
          const rect = el.getBoundingClientRect();
          const style = window.getComputedStyle(el);
          const text = (el.innerText || el.textContent || "").replace(/\\s+/g, " ").trim();
          return {
            index,
            tag: (el.tagName || "").toLowerCase(),
            type: el.getAttribute("type") || "",
            role: el.getAttribute("role") || "",
            text,
            aria_label: el.getAttribute("aria-label") || "",
            title: el.getAttribute("title") || "",
            accept: el.getAttribute("accept") || "",
            class_name: String(el.className || ""),
            visible: style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0,
            disabled: Boolean(el.disabled) || el.getAttribute("aria-disabled") === "true"
          };
        })
        """
    )


def _count_page_images(page) -> int:
    return page.locator(PAGE_IMAGE_SELECTOR).count()


def _iter_playwright_scopes(page):
    scopes = [("page", page)]
    for idx, frame in enumerate(page.frames):
        if frame == page.main_frame:
            continue
        scopes.append((f"frame#{idx}", frame))
    return scopes


def _find_visible_candidate(candidates, description: str, timeout_ms: int = 1500):
    errors = []
    for strategy, locator in candidates:
        try:
            total = locator.count()
        except Exception as exc:
            errors.append(f"{strategy}: count失敗={exc}")
            continue

        for idx in range(total - 1, -1, -1):
            candidate = locator.nth(idx)
            try:
                candidate.wait_for(state="visible", timeout=timeout_ms)
                return f"{strategy}#{idx}", candidate
            except Exception as exc:
                errors.append(f"{strategy}#{idx}: {exc}")

    raise RuntimeError(f"{description} を特定できませんでした: {' / '.join(errors[:6])}")


def _click_locator_with_fallback(page, locator, strategy: str, description: str, timeout_ms: int = 4000) -> None:
    locator.scroll_into_view_if_needed()
    click_errors = []
    for click_name, clicker in [
        ("通常click", lambda: locator.click(timeout=timeout_ms)),
        ("force click", lambda: locator.click(timeout=timeout_ms, force=True)),
        ("DOM click", lambda: locator.evaluate("(element) => element.click()")),
    ]:
        try:
            clicker()
            page.wait_for_timeout(1000)
            print(f"   ✅ {description}: {strategy} ({click_name})")
            return
        except Exception as exc:
            click_errors.append(f"{click_name}={exc}")

    raise RuntimeError(f"{description} の click に失敗しました: {strategy}: {' / '.join(click_errors[:3])}")


def _click_visible_candidate(page, candidates, description: str, timeout_ms: int = 4000) -> str:
    strategy, locator = _find_visible_candidate(candidates, description, timeout_ms=timeout_ms)
    _click_locator_with_fallback(page, locator, strategy, description, timeout_ms=timeout_ms)
    return strategy


def _find_visible_scoped_candidate(page, candidate_builders, description: str, timeout_ms: int = 1500):
    errors = []
    for scope_name, scope in _iter_playwright_scopes(page):
        for strategy, builder in candidate_builders:
            try:
                locator = builder(scope)
                total = locator.count()
            except Exception as exc:
                errors.append(f"{scope_name}:{strategy}: count失敗={exc}")
                continue

            for idx in range(total - 1, -1, -1):
                candidate = locator.nth(idx)
                try:
                    candidate.wait_for(state="visible", timeout=timeout_ms)
                    return f"{scope_name}:{strategy}#{idx}", candidate
                except Exception as exc:
                    errors.append(f"{scope_name}:{strategy}#{idx}: {exc}")

    raise RuntimeError(f"{description} を特定できませんでした: {' / '.join(errors[:6])}")


def _click_visible_scoped_candidate(page, candidate_builders, description: str, timeout_ms: int = 4000) -> str:
    strategy, locator = _find_visible_scoped_candidate(page, candidate_builders, description)
    _click_locator_with_fallback(page, locator, strategy, description, timeout_ms=timeout_ms)
    return strategy


def _click_rightmost_scoped_candidate(page, candidate_builders, description: str, timeout_ms: int = 4000) -> str:
    best = None
    errors = []
    for scope_name, scope in _iter_playwright_scopes(page):
        for strategy, builder in candidate_builders:
            try:
                locator = builder(scope)
                total = locator.count()
            except Exception as exc:
                errors.append(f"{scope_name}:{strategy}: count失敗={exc}")
                continue
            for idx in range(total):
                candidate = locator.nth(idx)
                try:
                    candidate.wait_for(state="visible", timeout=1200)
                    box = candidate.bounding_box() or {}
                    x = box.get("x", -1)
                    if best is None or x > best[0]:
                        best = (x, f"{scope_name}:{strategy}#{idx}", candidate)
                except Exception as exc:
                    errors.append(f"{scope_name}:{strategy}#{idx}: {exc}")
    if best is None:
        raise RuntimeError(f"{description} を特定できませんでした: {' / '.join(errors[:6])}")
    _, strategy, locator = best
    _click_locator_with_fallback(page, locator, strategy, description, timeout_ms=timeout_ms)
    return strategy


def _collect_file_input_candidates(page, prefer_adobe: bool = False) -> list[tuple[int, str, int, object, dict]]:
    candidates = []
    for scope_name, scope in _iter_playwright_scopes(page):
        file_inputs = scope.locator("input[type='file']")
        total = 0
        try:
            total = file_inputs.count()
        except Exception:
            continue
        for idx in range(total):
            input_locator = file_inputs.nth(idx)
            try:
                metadata = input_locator.evaluate(
                    """
                    (el) => {
                      const root = el.getRootNode();
                      const host = root && root.host ? root.host : null;
                      const rect = el.getBoundingClientRect();
                      const style = window.getComputedStyle(el);
                      return {
                        accept: el.getAttribute('accept') || '',
                        id: el.id || '',
                        class_name: String(el.className || ''),
                        visible: style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0,
                        root_kind: root && root.toString ? root.toString() : '',
                        host_tag: host && host.tagName ? host.tagName.toLowerCase() : '',
                        host_id: host && host.id ? host.id : '',
                        host_class: host ? String(host.className || '') : ''
                      };
                    }
                    """
                )
            except Exception:
                continue

            accept = (metadata.get("accept") or "").lower()
            if accept and "image" not in accept:
                continue

            combined = " ".join(
                [
                    scope_name,
                    metadata.get("id") or "",
                    metadata.get("class_name") or "",
                    metadata.get("host_tag") or "",
                    metadata.get("host_id") or "",
                    metadata.get("host_class") or "",
                ]
            ).lower()
            root_kind = (metadata.get("root_kind") or "").lower()
            score = 0
            if accept:
                score += 20
            if "shadowroot" in root_kind:
                score += 40
            if "cc-everywhere-container" in combined:
                score += 120
            if any(token in combined for token in ["adobe", "express", "upload", "asset", "media"]):
                score += 30
            if metadata.get("visible"):
                score += 5
            if prefer_adobe and "shadowroot" not in root_kind:
                score -= 30

            candidates.append((score, scope_name, idx, input_locator, metadata))

    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates


def _try_set_existing_file_input_any_scope(page, image_path: Path, prefer_adobe: bool = False) -> str | None:
    for score, scope_name, idx, input_locator, metadata in _collect_file_input_candidates(page, prefer_adobe=prefer_adobe):
        try:
            input_locator.set_input_files(str(image_path))
            page.wait_for_timeout(1500)
            root_kind = metadata.get("root_kind") or ""
            host_tag = metadata.get("host_tag") or ""
            used = f"{scope_name}:input[type='file']#{idx}:score={score}:root={root_kind}:host={host_tag}"
            print(f"   ✅ 画像ファイル指定: {used}")
            return used
        except Exception:
            continue
    return None


def _try_set_existing_file_input_with_brief_wait(
    page,
    image_path: Path,
    prefer_adobe: bool = False,
    wait_ms: int = 500,
) -> str | None:
    if wait_ms > 0:
        page.wait_for_timeout(wait_ms)
    return _try_set_existing_file_input_any_scope(page, image_path, prefer_adobe=prefer_adobe)


def _wait_for_existing_file_input_any_scope(
    page,
    image_path: Path,
    prefer_adobe: bool = False,
    timeout_ms: int = 4000,
    poll_ms: int = 250,
) -> str | None:
    deadline = time.time() + (timeout_ms / 1000)
    while time.time() < deadline:
        direct_input = _try_set_existing_file_input_any_scope(page, image_path, prefer_adobe=prefer_adobe)
        if direct_input:
            return direct_input
        page.wait_for_timeout(poll_ms)
    return _try_set_existing_file_input_any_scope(page, image_path, prefer_adobe=prefer_adobe)


def _serialize_file_input_candidates(page, prefer_adobe: bool = False, limit: int = 20) -> list[dict]:
    serialized = []
    for score, scope_name, idx, _input_locator, metadata in _collect_file_input_candidates(
        page,
        prefer_adobe=prefer_adobe,
    ):
        serialized.append(
            {
                "score": score,
                "scope_name": scope_name,
                "index": idx,
                "accept": metadata.get("accept") or "",
                "id": metadata.get("id") or "",
                "class_name": metadata.get("class_name") or "",
                "visible": bool(metadata.get("visible")),
                "root_kind": metadata.get("root_kind") or "",
                "host_tag": metadata.get("host_tag") or "",
                "host_id": metadata.get("host_id") or "",
                "host_class": metadata.get("host_class") or "",
            }
        )
        if len(serialized) >= limit:
            break
    return serialized


def _has_adobe_file_input_candidate(page) -> bool:
    for score, scope_name, _idx, _input_locator, metadata in _collect_file_input_candidates(
        page,
        prefer_adobe=True,
    ):
        combined = " ".join(
            [
                scope_name,
                metadata.get("id") or "",
                metadata.get("class_name") or "",
                metadata.get("host_tag") or "",
                metadata.get("host_id") or "",
                metadata.get("host_class") or "",
            ]
        ).lower()
        if score >= 100:
            return True
        if "cc-everywhere-container" in combined:
            return True
        if any(token in combined for token in ["adobe", "express"]):
            return True
    return False


def _write_control_snapshot(path: Path, page) -> None:
    _write_json(path, _collect_control_snapshot(page))


def _dump_upload_retry_artifacts(page, artifacts_dir: Path | None, stem: str) -> None:
    if not artifacts_dir:
        return
    _dump_page_artifacts(page, artifacts_dir, stem)
    _write_control_snapshot(artifacts_dir / f"{stem}_controls.json", page)


def _click_top_image_button(page) -> str:
    return _click_visible_candidate(
        page,
        candidates=[
            ("button[aria-label='画像を追加']", page.locator(TOP_IMAGE_BUTTON_SELECTOR)),
            ("button[aria-label*='画像']", page.locator("button[aria-label*='画像']")),
        ],
        description="トップ画像ボタン",
    )


def _choose_direct_upload_image_file(page, image_path: Path, artifacts_dir: Path | None = None) -> str:
    upload_text_pattern = re.compile(r"画像\s*を?\s*アップロード|^アップロード$")
    errors = []

    def build_candidate_locators():
        return [
            (
                "button_role_label_regex_upload",
                page.locator("button, [role='button'], label").filter(has_text=upload_text_pattern),
            ),
            (
                "role_button_regex_upload",
                page.get_by_role("button", name=upload_text_pattern),
            ),
            (
                "aria_label_contains_upload",
                page.locator("[aria-label*='アップロード'], [title*='アップロード']"),
            ),
            (
                "xpath_clickable_upload_ancestor",
                page.locator(
                    "xpath=//*[contains(normalize-space(.), '画像をアップロード') or normalize-space(.)='アップロード']"
                    "/ancestor-or-self::*[self::button or self::label or @role='button'][1]"
                ),
            ),
            ("text_画像をアップロード", page.locator("text=画像をアップロード")),
            ("text_アップロード", page.locator("text=アップロード")),
        ]

    def has_visible_upload_entry() -> bool:
        for _strategy, locator in build_candidate_locators():
            try:
                total = locator.count()
            except Exception:
                continue
            for idx in range(total):
                try:
                    if locator.nth(idx).is_visible():
                        return True
                except Exception:
                    continue
        return False

    for attempt in range(1, 6):
        direct_input = _try_set_existing_file_input_any_scope(page, image_path)
        if direct_input:
            return direct_input

        found_candidate = False
        for strategy, locator in build_candidate_locators():
            try:
                total = locator.count()
            except Exception as exc:
                errors.append(f"{strategy}: count失敗={exc}")
                continue

            if total > 0:
                found_candidate = True

            for idx in range(total - 1, -1, -1):
                candidate = locator.nth(idx)
                try:
                    candidate.wait_for(state="visible", timeout=1500)
                except Exception as exc:
                    errors.append(f"{strategy}#{idx}: visible失敗={exc}")
                    continue

                try:
                    with page.expect_file_chooser(timeout=3000) as chooser_info:
                        _click_locator_with_fallback(
                            page,
                            candidate,
                            f"{strategy}#{idx}",
                            "画像アップロード導線",
                            timeout_ms=4000,
                        )
                    chooser_info.value.set_files(str(image_path))
                    page.wait_for_timeout(1500)
                    used = f"{strategy}#{idx}:filechooser"
                    print(f"   ✅ 画像アップロード導線: {used}")
                    return used
                except Exception as exc:
                    _dump_upload_retry_artifacts(
                        page,
                        artifacts_dir,
                        f"direct_upload_click_attempt{attempt}_{idx}",
                    )
                    direct_input = _wait_for_existing_file_input_any_scope(
                        page,
                        image_path,
                        timeout_ms=4000,
                        poll_ms=250,
                    )
                    if direct_input:
                        used = f"{strategy}#{idx}:postclick:{direct_input}"
                        print(f"   ✅ 画像アップロード導線: {used}")
                        return used
                    errors.append(f"{strategy}#{idx}: filechooser未発火={exc}")
                    try:
                        candidate.wait_for(state="visible", timeout=500)
                    except Exception as exc:
                        errors.append(f"{strategy}#{idx}: click再試行前に導線消失={exc}")
                        continue
                    try:
                        _click_locator_with_fallback(
                            page,
                            candidate,
                            f"{strategy}#{idx}",
                            "画像アップロード導線",
                            timeout_ms=1500,
                        )
                        _dump_upload_retry_artifacts(
                            page,
                            artifacts_dir,
                            f"direct_upload_reclick_attempt{attempt}_{idx}",
                        )
                        direct_input = _wait_for_existing_file_input_any_scope(
                            page,
                            image_path,
                            timeout_ms=2500,
                            poll_ms=250,
                        )
                        if direct_input:
                            used = f"{strategy}#{idx}:reclick:{direct_input}"
                            print(f"   ✅ 画像アップロード導線: {used}")
                            return used
                    except Exception as exc:
                        errors.append(f"{strategy}#{idx}: click失敗={exc}")

        if attempt < 5:
            if not found_candidate:
                errors.append(f"attempt{attempt}: no_upload_entry_visible")
            if not has_visible_upload_entry():
                try:
                    reopen_strategy = _click_top_image_button(page)
                    print(f"   🔄 トップ画像メニューを再オープン: {reopen_strategy} (attempt {attempt + 1})")
                    _dump_upload_retry_artifacts(page, artifacts_dir, f"top_image_menu_reopened_attempt{attempt + 1}")
                except Exception as exc:
                    errors.append(f"attempt{attempt}: menu_reopen_failed={exc}")
            page.wait_for_timeout(1000)

    direct_input = _try_set_existing_file_input_any_scope(page, image_path)
    if direct_input:
        return direct_input

    raise RuntimeError(f"画像アップロード導線を特定できませんでした: {' / '.join(errors[:8])}")


def _wait_for_crop_dialog(page, timeout_ms: int = 15000):
    return _find_visible_candidate(
        candidates=[
            ("CropModal__content", page.locator(CROP_DIALOG_SELECTOR)),
            ("ReactModal__Content_dialog", page.locator("div.ReactModal__Content[role='dialog'][aria-modal='true']")),
            ("role_dialog", page.get_by_role("dialog")),
        ],
        description="画像保存モーダル",
        timeout_ms=timeout_ms,
    )


def _save_crop_dialog(page, timeout_ms: int = 15000) -> str:
    dialog_strategy, dialog = _wait_for_crop_dialog(page, timeout_ms=timeout_ms)
    save_strategy = _click_visible_candidate(
        page,
        candidates=[
            (f"{dialog_strategy}->role_button_保存", dialog.get_by_role("button", name="保存")),
            (f"{dialog_strategy}->button_text_保存", dialog.locator("button").filter(has_text="保存")),
            (f"{dialog_strategy}->text_保存", dialog.locator("text=保存")),
        ],
        description="画像モーダル保存",
    )
    try:
        page.locator(CROP_DIALOG_SELECTOR).last.wait_for(state="hidden", timeout=15000)
    except Exception:
        page.wait_for_timeout(1500)
    return save_strategy


def _wait_for_uploaded_image_ready(page, previous_count: int, timeout_sec: int = 60) -> tuple[int, str]:
    for _ in range(timeout_sec):
        current_count = _count_page_images(page)
        if current_count > previous_count:
            return current_count, "main_img_detected"

        loading_locator = page.locator(TOP_IMAGE_LOADING_SELECTOR)
        if loading_locator.count() > 0:
            try:
                if loading_locator.first.is_visible():
                    page.wait_for_timeout(1000)
                    continue
            except Exception:
                page.wait_for_timeout(1000)
                continue
        page.wait_for_timeout(1000)

    return _count_page_images(page), "timeout"


def _save_editor_draft(page) -> str:
    strategy = _click_visible_candidate(
        page,
        candidates=[
            ("role_button_下書き保存", page.get_by_role("button", name="下書き保存")),
            ("header_button_下書き保存", page.locator("header button").filter(has_text="下書き保存")),
            ("button_text_下書き保存", page.locator("button").filter(has_text="下書き保存")),
        ],
        description="エディタ下書き保存",
    )
    page.wait_for_timeout(5000)
    return strategy


def _editor_has_table_of_contents(page) -> bool:
    return bool(page.evaluate(
        """
        () => {
          const editor = document.querySelector('.note-editable, [contenteditable="true"]') || document.querySelector('.ProseMirror');
          if (!editor) return false;
          const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
          return Array.from(editor.querySelectorAll('*')).some((el) => {
            const text = normalize(el.innerText || el.textContent || '');
            return text === '目次' || text.startsWith('目次 ');
          });
        }
        """
    ))


def _extract_first_h2_after_disclosure(markdown: str) -> str:
    """アソシエイト表記より後にある最初のH2見出しを取得する。"""
    lines = (markdown or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    marker_seen = False
    marker_exists = any(NOTE_DISCLOSURE_PREFIX in line for line in lines)

    for line in lines:
        stripped = line.strip()
        if NOTE_DISCLOSURE_PREFIX in stripped:
            marker_seen = True
            continue
        if marker_exists and not marker_seen:
            continue
        if stripped.startswith("## ") and not stripped.startswith("### "):
            return re.sub(r"<[^>]+>", "", stripped[3:].strip())

    return ""


def _place_caret_after_disclosure(page) -> bool:
    return bool(page.evaluate(
        """
        (markers) => {
          const editor = document.querySelector('.note-editable, [contenteditable="true"]') || document.querySelector('.ProseMirror');
          if (!editor) return false;
          const normalize = (value) => (value || '')
            .replace(/\\u200B/g, '')
            .replace(/\\s+/g, ' ')
            .trim();
          const normalizedMarkers = (markers || []).map(normalize).filter(Boolean);
          const isVisible = (el) => {
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
          };
          const isIgnored = (el) => /^(SCRIPT|STYLE|NOSCRIPT)$/i.test(el.tagName || '');
          const findBlock = (node) => {
            let current = node && node.parentElement;
            while (current && current !== editor) {
              if (/^(P|LI|H[1-6]|DIV)$/i.test(current.tagName || '') || current.getAttribute('data-block-id') || current.getAttribute('data-block')) {
                return current;
              }
              current = current.parentElement;
            }
            return node && node.parentElement ? node.parentElement : editor;
          };
          const candidates = [];
          const walker = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT, {
            acceptNode: (node) => {
              const parent = node.parentElement;
              if (!parent || isIgnored(parent) || !isVisible(parent)) return NodeFilter.FILTER_REJECT;
              const text = normalize(node.nodeValue || '');
              return normalizedMarkers.some((marker) => text.includes(marker))
                ? NodeFilter.FILTER_ACCEPT
                : NodeFilter.FILTER_SKIP;
            },
          });
          while (walker.nextNode()) {
            const node = walker.currentNode;
            const rawText = node.nodeValue || '';
            const normalizedText = normalize(rawText);
            for (const marker of normalizedMarkers) {
              const normalizedIndex = normalizedText.indexOf(marker);
              if (normalizedIndex < 0) continue;
              const rawIndex = rawText.indexOf(marker);
              let offset = rawIndex >= 0 ? rawIndex + marker.length : rawText.length;
              while (offset < rawText.length && /[\\s\\u200B\\)）]/.test(rawText[offset])) {
                offset += 1;
              }
              const block = findBlock(node);
              candidates.push({
                node,
                offset,
                block,
                markerLength: marker.length,
                textLength: normalize(block.innerText || block.textContent || '').length,
              });
            }
          }
          if (candidates.length === 0) return false;
          candidates.sort((a, b) => b.markerLength - a.markerLength || a.textLength - b.textLength);
          const target = candidates[0];
          target.block.scrollIntoView({ block: 'center', inline: 'nearest' });
          const range = document.createRange();
          range.setStart(target.node, target.offset);
          range.collapse(true);
          const selection = window.getSelection();
          selection.removeAllRanges();
          selection.addRange(range);
          editor.focus();
          return true;
        }
        """,
        NOTE_DISCLOSURE_MARKERS,
    ))


def _restore_first_h2_after_toc(page, first_heading_text: str) -> dict:
    """目次直下の最初のH2が通常テキスト化していた場合だけH2へ戻す。"""
    if not first_heading_text:
        return {"restored": False, "reason": "heading_not_found"}

    return page.evaluate(
        """
        (headingText) => {
          const editor = document.querySelector('.note-editable, [contenteditable="true"]') || document.querySelector('.ProseMirror');
          if (!editor) return { restored: false, reason: 'editor_not_found' };
          const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
          const heading = normalize(headingText);
          if (!heading) return { restored: false, reason: 'heading_empty' };
          const isVisible = (el) => {
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
          };
          const editorRect = editor.getBoundingClientRect();
          const tocContainers = Array.from(editor.querySelectorAll('*'))
            .filter((el) => {
              if (!isVisible(el)) return false;
              const text = normalize(el.innerText || el.textContent || '');
              if (!text.includes('目次') || !text.includes(heading)) return false;
              const rect = el.getBoundingClientRect();
              if (rect.height <= 80 || rect.height >= editorRect.height * 0.8) return false;
              return rect.width <= editorRect.width + 40;
            })
            .map((el) => ({ el, rect: el.getBoundingClientRect() }))
            .sort((a, b) => (b.rect.width * b.rect.height) - (a.rect.width * a.rect.height));
          const tocBottom = tocContainers[0] ? tocContainers[0].rect.bottom : -Infinity;
          const findEditableBlock = (el) => {
            let node = el;
            while (node && node !== editor) {
              const tag = (node.tagName || '').toUpperCase();
              if (/^(P|DIV|H1|H2|H3|H4|H5|H6)$/.test(tag)) return node;
              node = node.parentElement;
            }
            return null;
          };
          const isInsideTocLikeBlock = (el) => {
            let node = el.parentElement;
            while (node && node !== editor) {
              const text = normalize(node.innerText || node.textContent || '');
              if (text.includes('目次') && text.includes(heading) && text.length > heading.length + 10) {
                return true;
              }
              node = node.parentElement;
            }
            return false;
          };
          const candidates = Array.from(editor.querySelectorAll('p, div, h1, h2, h3, h4, h5, h6, span'))
            .filter((el) => {
              if (!isVisible(el)) return false;
              if (normalize(el.innerText || el.textContent || '') !== heading) return false;
              if (isInsideTocLikeBlock(el)) return false;
              const rect = el.getBoundingClientRect();
              return rect.top > tocBottom + 4;
            })
            .map((el) => {
              const block = findEditableBlock(el);
              if (!block) return null;
              return { el, block, rect: block.getBoundingClientRect() };
            })
            .filter(Boolean)
            .sort((a, b) => a.rect.top - b.rect.top);
          const target = candidates[0];
          if (!target) return { restored: false, reason: 'target_not_found', tocBottom };
          const tag = (target.block.tagName || '').toUpperCase();
          if (tag === 'H2') return { restored: false, reason: 'already_h2', tocBottom };

          const h2 = document.createElement('h2');
          h2.innerHTML = target.block.innerHTML;
          target.block.parentNode.replaceChild(h2, target.block);
          editor.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'formatBlock' }));
          return { restored: true, previousTag: tag, text: heading, tocBottom };
        }
        """,
        first_heading_text,
    )


def _click_toc_item_from_slash_popup(page) -> str:
    result = page.evaluate(
        """
        () => {
          const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
          const isTocText = (value) => value === '目次' || value.startsWith('目次 ');
          const isVisible = (el) => {
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
          };
          const tagScore = (el) => {
            const tag = (el.tagName || '').toLowerCase();
            const role = el.getAttribute('role') || '';
            if (role === 'menuitem' || role === 'option' || role === 'button' || tag === 'button') return 0;
            if (tag === 'li') return 1;
            if (tag === 'div') return 2;
            return 3;
          };
          const findClickTarget = (source) => {
            const targets = [];
            let node = source;
            for (let depth = 0; node && depth < 8; depth += 1, node = node.parentElement) {
              if (!isVisible(node)) continue;
              const text = normalize(node.innerText || node.textContent || '');
              if (!isTocText(text)) continue;
              const tag = (node.tagName || '').toLowerCase();
              const role = node.getAttribute('role') || '';
              if (!(tag === 'button' || tag === 'li' || tag === 'div' || role === 'button' || role === 'menuitem' || role === 'option')) continue;
              const rect = node.getBoundingClientRect();
              const widthPenalty = rect.width >= 120 ? 0 : 8;
              const heightPenalty = rect.height >= 28 && rect.height <= 90 ? 0 : 8;
              const hugePenalty = rect.width * rect.height > 220000 ? 30 : 0;
              targets.push({
                el: node,
                rect,
                text,
                tag,
                role,
                score: tagScore(node) + widthPenalty + heightPenalty + hugePenalty + depth / 10,
              });
            }
            targets.sort((a, b) => a.score - b.score || b.rect.width - a.rect.width || a.rect.top - b.rect.top);
            return targets[0] || null;
          };
          const seen = new Set();
          const candidates = Array.from(document.querySelectorAll('body *'))
            .filter(isVisible)
            .map((source) => {
              const text = normalize(source.innerText || source.textContent || '');
              if (!isTocText(text)) return null;
              const target = findClickTarget(source);
              if (!target) return null;
              if (seen.has(target.el)) return null;
              seen.add(target.el);
              return {
                el: target.el,
                rect: target.rect,
                text: target.text,
                tag: target.tag,
                role: target.role,
                score: target.score,
                sourceExact: text === '目次',
              };
            })
            .filter(Boolean)
            .sort((a, b) => {
              const aPosition = a.rect.top + a.rect.left / 1000;
              const bPosition = b.rect.top + b.rect.left / 1000;
              return Number(!a.sourceExact) - Number(!b.sourceExact)
                || a.score - b.score
                || aPosition - bPosition;
            });
          const selected = candidates[0];
          if (!selected) return { ok: false, reason: 'toc_popup_item_not_found' };
          selected.el.scrollIntoView({ block: 'center', inline: 'nearest' });
          const rect = selected.el.getBoundingClientRect();
          return {
            ok: true,
            text: selected.text,
            tag: selected.tag,
            role: selected.role,
            x: rect.left + Math.min(Math.max(rect.width * 0.35, 32), Math.max(rect.width - 8, 1)),
            y: rect.top + rect.height / 2,
          };
        }
        """
    )
    if not result.get("ok"):
        raise RuntimeError(result.get("reason") or "スラッシュポップアップ内の目次が見つかりません")
    page.mouse.click(result["x"], result["y"])
    page.wait_for_timeout(1500)
    return f"slash_popup_click:{result.get('tag', '')}:{result.get('role', '')}"


def _insert_toc_by_slash_popup(page) -> str:
    page.keyboard.type("/")
    page.wait_for_timeout(1000)
    try:
        return _click_toc_item_from_slash_popup(page)
    except Exception:
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
        except Exception:
            pass
        raise


def _insert_table_of_contents(page, source_markdown: str = "") -> dict:
    """専用マーカー位置へnote標準の目次ブロックを挿入する。"""
    result = {"success": False, "strategy": "", "already_exists": False}
    first_h2_after_disclosure = _extract_first_h2_after_disclosure(source_markdown)
    if _editor_has_table_of_contents(page):
        result["success"] = True
        result["already_exists"] = True
        result["strategy"] = "already_exists"
        result["heading_restore"] = _restore_first_h2_after_toc(page, first_h2_after_disclosure)
        print("   ✅ 目次は既に挿入済みです")
        return result

    marker_result = _place_caret_at_body_image_marker(page, NOTE_TOC_MARKER)
    result["marker_result"] = marker_result
    if marker_result.get("ok"):
        page.wait_for_timeout(500)
        page.keyboard.press("Backspace")
        page.wait_for_timeout(900)
    else:
        if not _place_caret_after_disclosure(page):
            print("   ⚠️ 目次マーカーとアソシエイト表記が見つからないため、目次挿入をスキップします")
            result["strategy"] = "toc_marker_and_disclosure_not_found"
            return result

        page.wait_for_timeout(500)
        page.keyboard.press("Enter")
        page.wait_for_timeout(500)
        page.keyboard.press("Enter")
        page.wait_for_timeout(900)

    # noteのエディタでは、アソシエイト表記直後に空段落を作り、「/」で出るポップアップから目次を挿入する。
    try:
        result["strategy"] = _insert_toc_by_slash_popup(page)
        page.wait_for_timeout(1800)
    except Exception as slash_popup_error:
        print(f"   ⚠️ スラッシュポップアップからの目次挿入に失敗しました: {slash_popup_error}")
        result["strategy"] = f"slash_popup_failed: {slash_popup_error}"

    result["success"] = _editor_has_table_of_contents(page)
    if result["success"]:
        result["heading_restore"] = _restore_first_h2_after_toc(page, first_h2_after_disclosure)
        if result["heading_restore"].get("restored"):
            print(f"   ✅ 目次直下のH2見出しを復元しました: {first_h2_after_disclosure}")
        print(f"   ✅ 目次挿入完了: {result['strategy']}")
    else:
        print(f"   ⚠️ 目次挿入を確認できませんでした: {result['strategy']}")
    return result


def _place_caret_at_body_image_marker(page, marker: str) -> dict:
    """本文内の画像差し込みマーカーを選択状態にする。"""
    return page.evaluate(
        """
        (marker) => {
          const editor = document.querySelector('.note-editable, [contenteditable="true"]') || document.querySelector('.ProseMirror');
          if (!editor || !marker) return { ok: false, reason: 'editor_or_marker_empty' };
          const isVisible = (el) => {
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
          };
          const findBlock = (node) => {
            let current = node && node.parentElement;
            while (current && current !== editor) {
              if (/^(P|LI|H[1-6]|DIV)$/i.test(current.tagName || '') || current.getAttribute('data-block-id') || current.getAttribute('data-block')) {
                return current;
              }
              current = current.parentElement;
            }
            return node && node.parentElement ? node.parentElement : editor;
          };
          const walker = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT, {
            acceptNode: (node) => {
              const parent = node.parentElement;
              if (!parent || !isVisible(parent)) return NodeFilter.FILTER_REJECT;
              return String(node.nodeValue || '').includes(marker)
                ? NodeFilter.FILTER_ACCEPT
                : NodeFilter.FILTER_SKIP;
            },
          });
          const candidates = [];
          while (walker.nextNode()) {
            const node = walker.currentNode;
            const offset = String(node.nodeValue || '').indexOf(marker);
            if (offset < 0) continue;
            const block = findBlock(node);
            const rect = block.getBoundingClientRect();
            candidates.push({ node, offset, block, rect });
          }
          candidates.sort((a, b) => a.rect.top - b.rect.top || a.rect.left - b.rect.left);
          const target = candidates[0];
          if (!target) return { ok: false, reason: 'marker_not_found' };
          target.block.scrollIntoView({ block: 'center', inline: 'nearest' });
          const range = document.createRange();
          range.setStart(target.node, target.offset);
          range.setEnd(target.node, target.offset + marker.length);
          const selection = window.getSelection();
          selection.removeAllRanges();
          selection.addRange(range);
          editor.focus();
          return {
            ok: true,
            marker,
            block_text: String(target.block.innerText || target.block.textContent || '').trim(),
          };
        }
        """,
        marker,
    )


def _click_exact_slash_popup_item(page, label: str, description: str) -> str:
    result = page.evaluate(
        """
        (label) => {
          const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
          const isVisible = (el) => {
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
          };
          const tagScore = (el) => {
            const tag = (el.tagName || '').toLowerCase();
            const role = el.getAttribute('role') || '';
            if (role === 'menuitem' || role === 'option' || role === 'button' || tag === 'button') return 0;
            if (tag === 'li') return 1;
            if (tag === 'div') return 2;
            return 3;
          };
          const findClickTarget = (source) => {
            const targets = [];
            let node = source;
            for (let depth = 0; node && depth < 8; depth += 1, node = node.parentElement) {
              if (!isVisible(node)) continue;
              const text = normalize(node.innerText || node.textContent || '');
              if (text !== label) continue;
              const tag = (node.tagName || '').toLowerCase();
              const role = node.getAttribute('role') || '';
              if (!(tag === 'button' || tag === 'li' || tag === 'div' || role === 'button' || role === 'menuitem' || role === 'option')) continue;
              const rect = node.getBoundingClientRect();
              targets.push({
                el: node,
                rect,
                tag,
                role,
                score: tagScore(node) + depth / 10,
              });
            }
            targets.sort((a, b) => a.score - b.score || a.rect.top - b.rect.top);
            return targets[0] || null;
          };
          const seen = new Set();
          const candidates = Array.from(document.querySelectorAll('body *'))
            .filter(isVisible)
            .map((source) => {
              const text = normalize(source.innerText || source.textContent || '');
              if (text !== label) return null;
              const target = findClickTarget(source);
              if (!target || seen.has(target.el)) return null;
              seen.add(target.el);
              return target;
            })
            .filter(Boolean)
            .sort((a, b) => a.score - b.score || a.rect.top - b.rect.top || a.rect.left - b.rect.left);
          const selected = candidates[0];
          if (!selected) return { ok: false, reason: 'slash_popup_item_not_found' };
          selected.el.scrollIntoView({ block: 'center', inline: 'nearest' });
          const rect = selected.el.getBoundingClientRect();
          return {
            ok: true,
            tag: selected.tag,
            role: selected.role,
            x: rect.left + Math.min(Math.max(rect.width * 0.35, 32), Math.max(rect.width - 8, 1)),
            y: rect.top + rect.height / 2,
          };
        }
        """,
        label,
    )
    if not result.get("ok"):
        raise RuntimeError(f"{description}を特定できません: {result.get('reason', '')}")
    page.mouse.click(result["x"], result["y"])
    page.wait_for_timeout(1200)
    return f"slash_popup_click:{label}:{result.get('tag', '')}:{result.get('role', '')}"


def _choose_body_image_file_from_slash_popup(
    page,
    image_path: Path,
    artifacts_dir: Path | None,
    marker: str,
) -> str:
    page.keyboard.type("/")
    page.wait_for_timeout(1000)
    click_strategy = ""
    filechooser_error = ""
    try:
        with page.expect_file_chooser(timeout=4000) as chooser_info:
            click_strategy = _click_exact_slash_popup_item(page, "画像", "本文画像メニュー")
        chooser_info.value.set_files(str(image_path))
        page.wait_for_timeout(1500)
        return f"{click_strategy}:filechooser"
    except Exception as exc:
        filechooser_error = str(exc)

    direct_input = _wait_for_existing_file_input_any_scope(
        page,
        image_path,
        timeout_ms=5000,
        poll_ms=250,
    )
    if direct_input:
        return f"{click_strategy or 'slash_popup'}:input:{direct_input}"

    try:
        upload_entry = _choose_direct_upload_image_file(page, image_path, artifacts_dir=artifacts_dir)
        return f"{click_strategy or 'slash_popup'}:upload_entry:{upload_entry}"
    except Exception as exc:
        raise RuntimeError(
            f"本文画像アップロード導線を特定できませんでした: marker={marker} / "
            f"filechooser={filechooser_error} / upload={exc}"
        ) from exc


def _save_optional_body_image_crop_dialog(page) -> str:
    try:
        return _save_crop_dialog(page, timeout_ms=2500)
    except Exception:
        return "no_crop_dialog"


def _body_image_text_candidates(item: dict) -> list[str]:
    candidates: list[str] = []

    def add(value: str) -> None:
        text = str(value or "").strip()
        if len(text) >= 4:
            candidates.append(text)

    for value in item.get("text_candidates") or []:
        add(str(value))

    caption = str(item.get("caption") or "").strip()
    if caption and (re.search(r"\.(?:png|jpe?g|webp|gif|bmp|svg)\b", caption, re.IGNORECASE) or "_" in caption):
        add(caption)

    for key in ("source", "path"):
        raw_value = str(item.get(key) or "").strip()
        if not raw_value:
            continue
        parsed = urlparse(raw_value)
        path_value = parsed.path if parsed.scheme and len(parsed.scheme) > 1 else raw_value
        name = Path(unquote(path_value)).name.strip()
        if name:
            add(name)
            stem = Path(name).stem.strip()
            if stem and stem != name:
                add(stem)
        if key == "path":
            add(raw_value)

    return list(dict.fromkeys(candidates))


def _cleanup_body_image_artifacts(page, text_candidates: list[str]) -> dict:
    candidates = list(dict.fromkeys(str(candidate).strip() for candidate in text_candidates if str(candidate).strip()))
    return page.evaluate(
        """
        (candidates) => {
          const editor = document.querySelector('.note-editable, [contenteditable="true"]') || document.querySelector('.ProseMirror');
          if (!editor) return { success: false, reason: 'editor_not_found', removed_empty_blocks: 0, removed_text_blocks: 0 };

          const normalize = (value) => String(value || '')
            .replace(/\\u200B/g, '')
            .replace(/\\s+/g, ' ')
            .trim();
          const simplify = (value) => normalize(value).replace(/\\s+/g, '').toLowerCase();
          const candidateTexts = Array.from(new Set((candidates || []).map(normalize).filter((value) => value.length >= 4)));
          const candidateKeys = candidateTexts.map(simplify).filter((value) => value.length >= 4);
          const maxCandidateLength = candidateTexts.reduce((max, value) => Math.max(max, value.length), 0);
          const hasMedia = (el) => Boolean(el && el.querySelector && el.querySelector('img, picture, video, iframe, canvas'));
          const hasInteractive = (el) => Boolean(el && el.querySelector && el.querySelector('a, button, input, textarea, select'));
          const isSimpleContainer = (el) => {
            const tag = (el.tagName || '').toUpperCase();
            if (tag !== 'DIV') return true;
            return !el.querySelector('p, h1, h2, h3, h4, h5, h6, ul, ol, figure, blockquote');
          };
          const matchesCandidate = (text) => {
            const key = simplify(text);
            if (!key || key.length < 4) return false;
            return candidateKeys.some((candidate) => {
              if (candidate === key) return true;
              if (candidate.length >= 8 && key.length >= 8 && (candidate.startsWith(key) || key.startsWith(candidate))) return true;
              if (candidate.length >= 14 && key.includes(candidate)) return true;
              if (key.length >= 14 && candidate.includes(key)) return true;
              return false;
            });
          };

          let removedTextBlocks = 0;
          if (candidateKeys.length) {
            const textBlocks = Array.from(editor.querySelectorAll('p, li, figcaption, div'));
            for (const block of textBlocks) {
              if (!block.isConnected || hasMedia(block) || hasInteractive(block) || !isSimpleContainer(block)) continue;
              const text = normalize(block.innerText || block.textContent || '');
              if (!text || text.length > Math.max(80, maxCandidateLength + 50)) continue;
              if (!matchesCandidate(text)) continue;
              block.remove();
              removedTextBlocks += 1;
            }
          }

          const isEmptyBlock = (el) => {
            if (!el || !el.isConnected || hasMedia(el) || hasInteractive(el)) return false;
            const tag = (el.tagName || '').toUpperCase();
            if (!['P', 'DIV', 'LI'].includes(tag)) return false;
            if (!isSimpleContainer(el)) return false;
            return normalize(el.innerText || el.textContent || '') === '';
          };
          const siblingHasMedia = (el) => {
            const prev = el.previousElementSibling;
            const next = el.nextElementSibling;
            return hasMedia(prev) || hasMedia(next);
          };

          let removedEmptyBlocks = 0;
          for (let pass = 0; pass < 20; pass += 1) {
            let changed = false;
            const emptyBlocks = Array.from(editor.querySelectorAll('p, div, li'));
            for (const block of emptyBlocks) {
              if (!isEmptyBlock(block) || !siblingHasMedia(block)) continue;
              block.remove();
              removedEmptyBlocks += 1;
              changed = true;
            }
            if (!changed) break;
          }

          if (removedTextBlocks || removedEmptyBlocks) {
            editor.dispatchEvent(new Event('input', { bubbles: true }));
          }
          return {
            success: true,
            removed_empty_blocks: removedEmptyBlocks,
            removed_text_blocks: removedTextBlocks,
            candidate_count: candidateTexts.length,
          };
        }
        """,
        candidates,
    )


def _attach_body_images_to_page(
    page,
    body_image_uploads: list[dict] | None,
    artifacts_dir: Path | None,
) -> dict:
    uploads = body_image_uploads or []
    result = {
        "success": True,
        "requested_count": len(uploads),
        "uploaded_count": 0,
        "items": [],
        "failures": [],
    }
    if not uploads:
        result["strategy"] = "no_body_images"
        return result

    print(f"   🖼️ Notion本文画像をnote画像ブロックとして添付します: {len(uploads)}件")
    cleanup_candidates: list[str] = []
    for index, item in enumerate(uploads, start=1):
        marker = str(item.get("marker") or "")
        image_path = Path(str(item.get("path") or ""))
        item_cleanup_candidates = _body_image_text_candidates(item)
        cleanup_candidates.extend(item_cleanup_candidates)
        item_result = {
            "index": index,
            "marker": marker,
            "path": str(image_path),
            "source": str(item.get("source") or ""),
            "cleanup_candidates": item_cleanup_candidates,
            "success": False,
        }
        try:
            if not marker:
                raise RuntimeError("本文画像マーカーが空です")
            if not image_path.exists():
                raise FileNotFoundError(f"本文画像の一時ファイルが見つかりません: {image_path}")

            marker_result = _place_caret_at_body_image_marker(page, marker)
            item_result["marker_result"] = marker_result
            if not marker_result.get("ok"):
                raise RuntimeError(marker_result.get("reason") or "本文画像マーカーを特定できません")

            before_count = _count_page_images(page)
            item_result["before_image_count"] = before_count
            page.keyboard.press("Backspace")
            page.wait_for_timeout(500)

            upload_strategy = _choose_body_image_file_from_slash_popup(
                page,
                image_path,
                artifacts_dir=artifacts_dir,
                marker=marker,
            )
            item_result["upload_strategy"] = upload_strategy
            item_result["crop_dialog_strategy"] = _save_optional_body_image_crop_dialog(page)

            ready_image_count, ready_wait_strategy = _wait_for_uploaded_image_ready(
                page,
                previous_count=before_count,
                timeout_sec=60,
            )
            item_result["after_ready_image_count"] = ready_image_count
            item_result["ready_wait_strategy"] = ready_wait_strategy
            if ready_image_count <= before_count:
                raise RuntimeError("本文画像の挿入完了を確認できませんでした")

            item_result["success"] = True
            result["uploaded_count"] += 1
            print(f"   ✅ 本文画像添付完了: {index}/{len(uploads)}")
            page.wait_for_timeout(800)
        except Exception as exc:
            item_result["error"] = str(exc)
            result["success"] = False
            result["failures"].append(item_result)
            print(f"   ⚠️ 本文画像添付に失敗しました: {index}/{len(uploads)} / {exc}")
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
        result["items"].append(item_result)

    try:
        result["cleanup"] = _cleanup_body_image_artifacts(page, cleanup_candidates)
        cleanup = result["cleanup"]
        if cleanup.get("removed_empty_blocks") or cleanup.get("removed_text_blocks"):
            print(
                "   ✅ 本文画像まわりの不要テキストと空段落を削除しました: "
                f"text={cleanup.get('removed_text_blocks', 0)} / empty={cleanup.get('removed_empty_blocks', 0)}"
            )
    except Exception as exc:
        result["cleanup"] = {"success": False, "error": str(exc)}
        print(f"   ⚠️ 本文画像まわりの後処理に失敗しました: {exc}")

    return result


def _click_publish_next(page) -> str:
    strategy = _click_visible_candidate(
        page,
        candidates=[
            ("role_button_公開に進む", page.get_by_role("button", name="公開に進む")),
            ("header_button_公開に進む", page.locator("header button").filter(has_text="公開に進む")),
            ("button_text_公開に進む", page.locator("button").filter(has_text="公開に進む")),
        ],
        description="公開に進む",
        timeout_ms=8000,
    )
    page.wait_for_timeout(500)
    return strategy


def _fill_text_like_locator(locator, text: str) -> None:
    locator.scroll_into_view_if_needed()
    locator.click(timeout=4000)
    try:
        locator.fill(text, timeout=4000)
        return
    except Exception:
        pass
    locator.evaluate(
        """
        (el, value) => {
          if ('value' in el) {
            el.value = value;
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            return;
          }
          el.textContent = value;
          el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: value }));
        }
        """,
        text,
    )


def _paste_text_like_locator(page, locator, text: str) -> str:
    """note側のタグ入力を「貼り付け」として処理させる。"""
    locator.scroll_into_view_if_needed()
    locator.click(timeout=4000)
    modifier = "Meta" if sys.platform == "darwin" else "Control"
    errors = []

    try:
        page.context.grant_permissions(
            ["clipboard-read", "clipboard-write"],
            origin="https://editor.note.com",
        )
    except Exception:
        pass

    try:
        page.keyboard.press(f"{modifier}+A")
        page.evaluate("(value) => navigator.clipboard.writeText(value)", text)
        page.keyboard.press(f"{modifier}+V")
        page.wait_for_timeout(1800)
        return "clipboard_paste"
    except Exception as exc:
        errors.append(f"clipboard_paste={exc}")

    try:
        locator.evaluate(
            """
            (el, value) => {
              el.focus();
              const data = new DataTransfer();
              data.setData('text/plain', value);
              const event = new ClipboardEvent('paste', {
                bubbles: true,
                cancelable: true,
                clipboardData: data
              });
              el.dispatchEvent(event);
            }
            """,
            text,
        )
        page.wait_for_timeout(1800)
        return "clipboard_event"
    except Exception as exc:
        errors.append(f"clipboard_event={exc}")

    try:
        page.keyboard.insert_text(text)
        page.wait_for_timeout(1800)
        return "keyboard_insert_text"
    except Exception as exc:
        errors.append(f"keyboard_insert_text={exc}")

    _fill_text_like_locator(locator, text)
    page.wait_for_timeout(1800)
    return "fill_fallback:" + " / ".join(errors[:3])


def _collect_hashtag_surface_text(page) -> str:
    return page.evaluate(
        """
        () => {
          const parts = [];
          document.querySelectorAll('input, textarea, [contenteditable="true"]').forEach((el) => {
            parts.push(el.value || el.innerText || el.textContent || '');
          });
          if (document.body) parts.push(document.body.innerText || document.body.textContent || '');
          return parts.join('\\n').replace(/\\s+/g, ' ').trim();
        }
        """
    )


def _verify_note_hashtags(page, tags: str) -> dict:
    expected = [tag for tag in tags.split() if tag]
    compact_expected = re.sub(r"\s+", " ", tags).strip()
    last_missing = expected

    for _ in range(10):
        surface = _collect_hashtag_surface_text(page)
        compact_surface = re.sub(r"\s+", " ", surface).strip()
        if compact_expected and compact_expected in compact_surface:
            return {"expected": len(expected), "missing": []}

        missing = [
            tag for tag in expected
            if tag not in compact_surface and f"#{tag}" not in compact_surface
        ]
        if not missing:
            return {"expected": len(expected), "missing": []}
        last_missing = missing
        page.wait_for_timeout(500)

    raise RuntimeError(
        "指定ハッシュタグの反映を確認できませんでした: "
        + ", ".join(last_missing[:12])
        + (f" 他{len(last_missing) - 12}件" if len(last_missing) > 12 else "")
    )


def _limit_publish_tags(tags: str) -> tuple[str, dict]:
    raw_tags = []
    for token in re.split(r"[\s,、]+", tags or ""):
        cleaned = token.strip().lstrip("#")
        if cleaned:
            raw_tags.append(cleaned)

    unique_tags = list(dict.fromkeys(raw_tags))
    limited_tags = unique_tags
    if NOTE_PUBLISH_MAX_TAGS > 0:
        limited_tags = unique_tags[:NOTE_PUBLISH_MAX_TAGS]

    status = {
        "raw_count": len(raw_tags),
        "unique_count": len(unique_tags),
        "used_count": len(limited_tags),
        "limit": NOTE_PUBLISH_MAX_TAGS,
        "truncated_count": max(0, len(unique_tags) - len(limited_tags)),
    }
    if status["truncated_count"]:
        print(
            "   [情報] ハッシュタグを先頭から"
            f"{NOTE_PUBLISH_MAX_TAGS}件に制限します: {len(unique_tags)}件 → {len(limited_tags)}件"
        )
    return " ".join(limited_tags), status


def _get_publish_settings_ready_state(page) -> dict:
    try:
        return page.evaluate(
            """
            () => {
              const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
              const isVisible = (el) => {
                if (!el || !(el instanceof Element)) return false;
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
              };
              const textOf = (el) => normalize(el.innerText || el.textContent || el.getAttribute('aria-label') || '');
              const attr = (el, name) => el.getAttribute(name) || '';
              const dataText = (el) => Object.entries(el.dataset || {}).map(([key, value]) => `${key} ${value || ''}`).join(' ');
              const directFieldText = (el) => normalize([
                attr(el, 'placeholder'),
                attr(el, 'aria-label'),
                attr(el, 'title'),
                attr(el, 'name'),
                attr(el, 'id'),
                attr(el, 'class'),
                attr(el, 'data-testid'),
                dataText(el),
              ].join(' '));
              const contextText = (el) => {
                const parts = [directFieldText(el)];
                let node = el;
                for (let depth = 0; node && depth < 4; depth += 1, node = node.parentElement) {
                  parts.push(textOf(node));
                  if (node.tagName && node.tagName.toLowerCase() === 'label') break;
                }
                return normalize(parts.join(' '));
              };
              const buttons = Array.from(document.querySelectorAll('button, [role="button"]')).filter(isVisible);
              const finalButton = buttons.find((button) => {
                const text = textOf(button);
                return text.includes('投稿する') && !text.includes('公開に進む');
              });
              const fields = Array.from(document.querySelectorAll(
                'input, textarea, [contenteditable="true"], [role="textbox"], [role="combobox"]'
              )).filter(isVisible);
              const tagField = fields.find((field) => {
                const role = attr(field, 'role');
                const isPlainEditable = field.getAttribute('contenteditable') === 'true' && role !== 'textbox' && role !== 'combobox';
                const text = isPlainEditable ? directFieldText(field) : contextText(field);
                return text.includes('ハッシュタグ') || text.includes('タグ') || /(^|[^a-z])tag([^a-z]|$)/i.test(text);
              });
              const labels = Array.from(document.querySelectorAll('label, span, div, p')).filter(isVisible);
              const tagLabel = labels.find((label) => {
                const text = textOf(label);
                return text.includes('ハッシュタグ') || text.includes('タグを入力') || text.includes('タグを追加') || text.includes('タグ設定');
              });
              const magazineLabel = labels.find((label) => textOf(label).includes('マガジン'));
              const ready = Boolean(finalButton || tagField || tagLabel || magazineLabel);
              const reason = finalButton
                ? 'final_post_button'
                : tagField
                  ? 'tag_input_candidate'
                  : tagLabel
                    ? 'tag_label'
                    : magazineLabel
                      ? 'magazine_label'
                      : 'not_ready';
              return {
                ready,
                reason,
                fieldCount: fields.length,
                buttonCount: buttons.length,
                url: window.location.href,
                sampleText: normalize((document.body && document.body.innerText) || '').slice(0, 300),
              };
            }
            """
        )
    except Exception as exc:
        return {"ready": False, "reason": f"evaluate_error:{exc}", "fieldCount": 0, "buttonCount": 0}


def _wait_for_publish_settings_ready(
    page,
    timeout_ms: int = NOTE_PUBLISH_SETTINGS_READY_TIMEOUT_MS,
    poll_ms: int = NOTE_PUBLISH_SETTINGS_READY_POLL_MS,
) -> str:
    started_at = time.monotonic()
    deadline = started_at + (timeout_ms / 1000)
    last_state = {}

    while time.monotonic() < deadline:
        last_state = _get_publish_settings_ready_state(page)
        if last_state.get("ready"):
            elapsed = time.monotonic() - started_at
            print(f"   ✅ 公開設定画面を検出: {last_state.get('reason')} ({elapsed:.1f}秒後)")
            return str(last_state.get("reason") or "ready")
        page.wait_for_timeout(max(100, poll_ms))

    if NOTE_TOP_IMAGE_DEBUG:
        _dump_page_artifacts(page, NOTE_TOP_IMAGE_ARTIFACTS_DIR, "publish_settings_wait_timeout")
        _write_json(NOTE_TOP_IMAGE_ARTIFACTS_DIR / "publish_settings_wait_timeout.json", last_state)
    raise RuntimeError(
        "公開設定画面の表示を確認できませんでした: "
        f"{last_state.get('reason', 'unknown')} "
        f"(fields={last_state.get('fieldCount', 0)}, buttons={last_state.get('buttonCount', 0)})"
    )


def _fill_note_hashtags(page, tags: str) -> str:
    candidates = [
        ("input_placeholder_ハッシュタグ", page.locator("input[placeholder*='ハッシュタグ'], textarea[placeholder*='ハッシュタグ']")),
        ("input_aria_ハッシュタグ", page.locator("input[aria-label*='ハッシュタグ'], textarea[aria-label*='ハッシュタグ']")),
        ("input_placeholder_タグ", page.locator("input[placeholder*='タグ'], textarea[placeholder*='タグ']")),
        ("input_aria_タグ", page.locator("input[aria-label*='タグ'], textarea[aria-label*='タグ']")),
        ("role_textbox_ハッシュタグ", page.locator("[role='textbox'][aria-label*='ハッシュタグ'], [role='combobox'][aria-label*='ハッシュタグ']")),
        ("role_textbox_タグ", page.locator("[role='textbox'][aria-label*='タグ'], [role='combobox'][aria-label*='タグ']")),
        ("tag_attr_ascii", page.locator("input[name*='tag'], textarea[name*='tag'], input[id*='tag'], textarea[id*='tag'], [role='textbox'][data-testid*='tag'], [role='combobox'][data-testid*='tag'], [contenteditable='true'][data-testid*='tag']")),
        ("contenteditable_aria_ハッシュタグ", page.locator("[contenteditable='true'][aria-label*='ハッシュタグ']")),
        ("contenteditable_aria_タグ", page.locator("[contenteditable='true'][aria-label*='タグ']")),
        (
            "xpath_after_ハッシュタグ_input",
            page.locator(
                "xpath=//*[contains(normalize-space(.), 'ハッシュタグ')]"
                "/following::*[self::input or self::textarea or @contenteditable='true' or @role='textbox' or @role='combobox'][1]"
            ),
        ),
        (
            "xpath_after_タグ_input",
            page.locator(
                "xpath=//*[contains(normalize-space(.), 'タグ')]"
                "/following::*[self::input or self::textarea or @contenteditable='true' or @role='textbox' or @role='combobox'][1]"
            ),
        ),
        (
            "xpath_tag_attr_input",
            page.locator(
                "xpath=//*[self::input or self::textarea or @contenteditable='true' or @role='textbox' or @role='combobox']"
                "[contains(@placeholder, 'タグ') or contains(@aria-label, 'タグ') or contains(@title, 'タグ') "
                "or contains(@name, 'tag') or contains(@id, 'tag') or contains(@class, 'tag') or contains(@data-testid, 'tag')]"
            ),
        ),
    ]
    try:
        strategy, locator = _find_visible_candidate(candidates, "ハッシュタグ入力", timeout_ms=5000)
    except Exception:
        if NOTE_TOP_IMAGE_DEBUG:
            _dump_page_artifacts(page, NOTE_TOP_IMAGE_ARTIFACTS_DIR, "hashtag_input_not_found")
            _write_json(
                NOTE_TOP_IMAGE_ARTIFACTS_DIR / "hashtag_input_not_found_controls.json",
                _collect_control_snapshot(page),
            )
        raise
    paste_strategy = _paste_text_like_locator(page, locator, tags)
    verification = _verify_note_hashtags(page, tags)
    print(f"   ✅ ハッシュタグ入力完了: {strategy}->{paste_strategy} ({verification['expected']}件確認)")
    return f"{strategy}->{paste_strategy}"


def _xpath_literal(value: str) -> str:
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    return "concat(" + ', "\'", '.join(f"'{part}'" for part in value.split("'")) + ")"


def _get_publish_magazine_status(page) -> dict:
    return page.evaluate(
        """
        (magazineName) => {
          const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
          const isVisible = (el) => {
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
          };
          const textOf = (el) => normalize(el.innerText || el.textContent || el.getAttribute('aria-label') || '');
          const buttonText = (el) => textOf(el);
          const labels = Array.from(document.querySelectorAll('body *')).filter((el) => {
            if (!isVisible(el)) return false;
            return textOf(el).includes(magazineName);
          }).sort((a, b) => textOf(a).length - textOf(b).length);

          for (const label of labels) {
            let row = label;
            for (let depth = 0; row && depth < 8; depth += 1, row = row.parentElement) {
              const rowText = normalize(row.innerText || row.textContent || '');
              if (!rowText.includes(magazineName)) continue;
              if (!rowText.includes('追加') && !rowText.includes('追加済')) continue;
              const buttons = Array.from(row.querySelectorAll('button, [role="button"]')).filter(isVisible);
              const already = rowText.includes('追加済') || buttons.some((button) => buttonText(button).includes('追加済'));
              const canAdd = buttons.some((button) => {
                const text = buttonText(button);
                return (text === '追加' || text.includes('追加')) && !text.includes('追加済');
              });
              return {
                found: true,
                already,
                canAdd,
                depth,
                rowText: rowText.slice(0, 300),
              };
            }
          }
          return { found: false, already: false, canAdd: false, rowText: '' };
        }
        """,
        NOTE_PUBLISH_MAGAZINE_NAME,
    )


def _click_publish_magazine_by_dom(page) -> str:
    result = page.evaluate(
        """
        (magazineName) => {
          const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
          const isVisible = (el) => {
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
          };
          const textOf = (el) => normalize(el.innerText || el.textContent || el.getAttribute('aria-label') || '');
          const buttonText = (el) => textOf(el);
          const labels = Array.from(document.querySelectorAll('body *')).filter((el) => {
            if (!isVisible(el)) return false;
            return textOf(el).includes(magazineName);
          }).sort((a, b) => textOf(a).length - textOf(b).length);

          for (const label of labels) {
            let row = label;
            for (let depth = 0; row && depth < 8; depth += 1, row = row.parentElement) {
              const rowText = normalize(row.innerText || row.textContent || '');
              if (!rowText.includes(magazineName)) continue;
              if (!rowText.includes('追加') && !rowText.includes('追加済')) continue;
              const buttons = Array.from(row.querySelectorAll('button, [role="button"]')).filter(isVisible);
              const already = rowText.includes('追加済') || buttons.some((button) => buttonText(button).includes('追加済'));
              if (already) {
                return { ok: true, already: true, strategy: `dom_publish_magazine_already_added_depth_${depth}`, rowText };
              }
              const addButton = buttons.find((button) => {
                const text = buttonText(button);
                return (text === '追加' || text.includes('追加')) && !text.includes('追加済');
              });
              if (addButton) {
                addButton.click();
                return { ok: true, already: false, strategy: `dom_publish_magazine_row_add_depth_${depth}`, rowText };
              }
            }
          }
          return { ok: false, already: false, strategy: '', rowText: '', reason: `${magazineName}行の追加ボタンが見つかりません` };
        }
        """,
        NOTE_PUBLISH_MAGAZINE_NAME,
    )
    if not result.get("ok"):
        raise RuntimeError(result.get("reason") or f"{NOTE_PUBLISH_MAGAZINE_NAME}行の追加ボタンが見つかりません")
    page.wait_for_timeout(1500)
    return result.get("strategy") or "dom_publish_magazine_row_add"


def _wait_for_publish_magazine_added(page) -> dict:
    status = {}
    for _ in range(12):
        status = _get_publish_magazine_status(page)
        if status.get("already"):
            return status
        page.wait_for_timeout(500)
    raise RuntimeError(f"{NOTE_PUBLISH_MAGAZINE_NAME}マガジンの追加済みを確認できませんでした: {status}")


def _add_publish_magazine(page) -> str:
    tab_strategy = ""
    try:
        tab_strategy = _click_visible_candidate(
            page,
            candidates=[
                ("role_tab_マガジン", page.get_by_role("tab", name="マガジン")),
                ("role_button_マガジン", page.get_by_role("button", name="マガジン")),
                ("button_text_マガジン", page.locator("button").filter(has_text="マガジン")),
                ("text_マガジン", page.locator("text=マガジン")),
            ],
            description="マガジンタブ",
            timeout_ms=4000,
        )
        page.wait_for_timeout(1500)
    except Exception as exc:
        print(f"   ⚠️ マガジンタブのクリックをスキップします: {exc}")

    status = _get_publish_magazine_status(page)
    if status.get("already"):
        strategy = "publish_magazine_already_added"
        print(f"   ✅ {NOTE_PUBLISH_MAGAZINE_NAME}マガジンは既に追加済みです: {status.get('rowText', '')[:80]}")
        return f"{tab_strategy}->{strategy}" if tab_strategy else strategy

    try:
        strategy = _click_publish_magazine_by_dom(page)
    except Exception as dom_exc:
        print(f"   ⚠️ DOM指定での{NOTE_PUBLISH_MAGAZINE_NAME}追加に失敗しました。XPathで再試行します: {dom_exc}")
        magazine_name_xpath = _xpath_literal(NOTE_PUBLISH_MAGAZINE_NAME)
        add_xpath = _xpath_literal("追加")
        added_xpath = _xpath_literal("追加済")
        strategy, locator = _find_visible_candidate(
            candidates=[
                (
                    "xpath_publish_magazine_exact_row_add_button",
                    page.locator(
                        f"xpath=//*[normalize-space(.)={magazine_name_xpath}]"
                        f"/ancestor::*[self::div or self::li or self::section][.//button[normalize-space(.)={add_xpath}]][1]"
                        f"//button[normalize-space(.)={add_xpath}]"
                    ),
                ),
                (
                    "xpath_publish_magazine_row_add_button",
                    page.locator(
                        f"xpath=//*[contains(normalize-space(.), {magazine_name_xpath})]"
                        f"/ancestor::*[self::div or self::li or self::section][.//button[contains(normalize-space(.), {add_xpath})]][1]"
                        f"//button[contains(normalize-space(.), {add_xpath}) and not(contains(normalize-space(.), {added_xpath}))]"
                    ),
                ),
                (
                    "xpath_publish_magazine_following_add_button",
                    page.locator(
                        f"xpath=//*[normalize-space(.)={magazine_name_xpath}]"
                        f"/following::button[normalize-space(.)={add_xpath}][1]"
                    ),
                ),
            ],
            description=f"{NOTE_PUBLISH_MAGAZINE_NAME}マガジン追加",
            timeout_ms=5000,
        )
        _click_locator_with_fallback(page, locator, strategy, f"{NOTE_PUBLISH_MAGAZINE_NAME}マガジン追加", timeout_ms=5000)

    added_status = _wait_for_publish_magazine_added(page)
    print(f"   ✅ {NOTE_PUBLISH_MAGAZINE_NAME}マガジン追加完了: {strategy} / {added_status.get('rowText', '')[:80]}")
    return f"{tab_strategy}->{strategy}" if tab_strategy else strategy


def _extract_note_key_from_url(url: str) -> str:
    match = re.search(r"/(?:notes/|n/)?(n[0-9a-f]{8,})(?:[/?#]|$)", url or "", re.IGNORECASE)
    return match.group(1) if match else ""


def _is_editor_publish_url(url: str) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    return parsed.netloc == "editor.note.com" or "/publish" in parsed.path


def _is_public_note_url(url: str, note_key: str = "") -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    if parsed.netloc == "editor.note.com":
        return False
    if parsed.netloc != "note.com" and not parsed.netloc.endswith(".note.com"):
        return False
    if "/publish" in parsed.path:
        return False
    if note_key and note_key not in url:
        return False
    if not note_key and not _extract_note_key_from_url(url):
        return False
    return True


def _collect_published_note_url_candidates(page, note_key: str = "") -> list[str]:
    try:
        candidates = page.evaluate(
            """
            ({ noteKey }) => {
              const urls = [];
              const add = (value) => {
                if (!value) return;
                try {
                  urls.push(new URL(value, location.href).href);
                } catch {}
              };
              add(location.href);
              for (const selector of [
                'link[rel="canonical"]',
                'meta[property="og:url"]',
                'meta[name="twitter:url"]',
                'meta[name="citation_public_url"]',
              ]) {
                for (const el of document.querySelectorAll(selector)) {
                  add(el.getAttribute('href') || el.getAttribute('content'));
                }
              }
              for (const anchor of document.querySelectorAll('a[href]')) {
                const text = (anchor.innerText || anchor.textContent || anchor.getAttribute('aria-label') || '').trim();
                const href = anchor.getAttribute('href') || '';
                if (
                  href.includes('note.com') ||
                  (noteKey && href.includes(noteKey)) ||
                  /記事|公開|表示|見る|確認/.test(text)
                ) {
                  add(href);
                }
              }
              return Array.from(new Set(urls));
            }
            """,
            {"noteKey": note_key},
        )
    except Exception as exc:
        print(f"   ⚠️ 公開済みURL候補の取得に失敗しました: {exc}")
        return []
    return [str(url) for url in candidates if _is_public_note_url(str(url), note_key)]


def _published_page_looks_available(page) -> bool:
    try:
        status = page.evaluate(
            """
            () => {
              const text = ((document.body && document.body.innerText) || '').replace(/\\s+/g, ' ').trim();
              const unavailableWords = [
                '記事が見つかりません',
                'ページが見つかりません',
                'お探しの記事は見つかりません',
                '存在しません',
                '非公開',
                '削除されました',
              ];
              return {
                ok: text.length > 0 && !unavailableWords.some((word) => text.includes(word)),
                sampleText: text.slice(0, 200),
              };
            }
            """
        )
    except Exception as exc:
        print(f"   ⚠️ 公開ページ確認に失敗しました: {exc}")
        return False
    if not status.get("ok"):
        print(f"   ⚠️ 公開ページとして確認できません: {status.get('sampleText', '')}")
    return bool(status.get("ok"))


def _public_note_url_is_reachable(url: str, note_key: str = "") -> dict:
    status = {
        "ok": False,
        "url": url,
        "status_code": 0,
        "final_url": "",
        "error": "",
        "sample_text": "",
    }
    if not _is_public_note_url(url, note_key):
        status["error"] = f"公開URL形式ではありません: {url}"
        return status

    try:
        response = http_requests.get(
            url,
            headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
            allow_redirects=True,
            timeout=30,
        )
    except Exception as exc:
        status["error"] = str(exc)
        return status

    status["status_code"] = int(response.status_code)
    status["final_url"] = response.url
    text = response.text or ""
    compact_text = re.sub(r"\s+", " ", text).strip()
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
    if note_key and note_key not in response.url and note_key not in text:
        status["error"] = f"記事キー {note_key} を公開ページ内で確認できません"
        return status
    if any(word in compact_text for word in unavailable_words):
        status["error"] = "未公開または存在しないページ文言を検出しました"
        return status
    status["ok"] = True
    return status


def _wait_for_published_note_url(page, note_key: str = "") -> dict:
    started_at = time.monotonic()
    deadline = started_at + (NOTE_PUBLISH_COMPLETE_TIMEOUT_MS / 1000)
    last_url = page.url
    last_reachability = {}

    while time.monotonic() < deadline:
        last_url = page.url
        if _is_public_note_url(last_url, note_key):
            reachability = _public_note_url_is_reachable(last_url, note_key)
            last_reachability = reachability
            if reachability.get("ok"):
                elapsed = time.monotonic() - started_at
                return {
                    "url": reachability.get("final_url") or last_url,
                    "strategy": f"public_http_200_page_url_after_{elapsed:.1f}s",
                    "reachability": reachability,
                }
            print(
                "   ⏳ 公開URL候補は未公開です: "
                f"{last_url} / {reachability.get('error', '')}"
            )

        candidates = _collect_published_note_url_candidates(page, note_key)
        for candidate in candidates:
            reachability = _public_note_url_is_reachable(candidate, note_key)
            last_reachability = reachability
            if reachability.get("ok"):
                elapsed = time.monotonic() - started_at
                return {
                    "url": reachability.get("final_url") or candidate,
                    "strategy": f"public_http_200_dom_candidate_after_{elapsed:.1f}s",
                    "reachability": reachability,
                }
            print(
                "   ⏳ 公開URL候補は未公開です: "
                f"{candidate} / {reachability.get('error', '')}"
            )

        page.wait_for_timeout(max(250, NOTE_PUBLISH_COMPLETE_POLL_MS))

    if NOTE_TOP_IMAGE_DEBUG:
        _dump_page_artifacts(page, NOTE_TOP_IMAGE_ARTIFACTS_DIR, "publish_completion_timeout")
        _write_json(
            NOTE_TOP_IMAGE_ARTIFACTS_DIR / "publish_completion_timeout.json",
            {"last_url": last_url, "note_key": note_key, "last_reachability": last_reachability},
        )
    raise RuntimeError(
        "未ログインHTTP 200で確認できる公開済みURLを確認できませんでした。"
        f"最後のURLは {last_url} です。"
        f"最後の公開確認結果は {last_reachability} です。"
    )



def _collect_final_post_button_states(page) -> list[dict]:
    try:
        return page.evaluate(
            """
            () => {
              const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
              const isVisible = (el) => {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
              };
              return Array.from(document.querySelectorAll('button, [role="button"]'))
                .map((button, index) => {
                  const text = normalize(button.innerText || button.textContent || button.getAttribute('aria-label') || '');
                  const disabled = Boolean(button.disabled) || button.getAttribute('aria-disabled') === 'true';
                  const rect = button.getBoundingClientRect();
                  return {
                    index,
                    text,
                    visible: isVisible(button),
                    disabled,
                    className: String(button.className || ''),
                    x: Math.round(rect.x),
                    y: Math.round(rect.y),
                    width: Math.round(rect.width),
                    height: Math.round(rect.height),
                  };
                })
                .filter((button) => button.text.includes('投稿する'));
            }
            """
        )
    except Exception as exc:
        return [{"error": str(exc)}]


def _find_enabled_final_post_button(page, timeout_ms: int = 20_000):
    deadline = time.monotonic() + (timeout_ms / 1000)
    last_states: list[dict] = []
    while time.monotonic() < deadline:
        last_states = _collect_final_post_button_states(page)
        candidates = [
            ("role_button_投稿する", page.get_by_role("button", name="投稿する")),
            ("button_text_投稿する", page.locator("button").filter(has_text="投稿する")),
        ]
        for strategy, locator in candidates:
            try:
                total = locator.count()
            except Exception:
                continue
            for idx in range(total - 1, -1, -1):
                candidate = locator.nth(idx)
                try:
                    candidate.wait_for(state="visible", timeout=500)
                    if candidate.is_enabled(timeout=500):
                        return f"{strategy}#{idx}", candidate, last_states
                except Exception:
                    continue
        page.wait_for_timeout(500)
    raise RuntimeError(f"有効な投稿するボタンを確認できませんでした: {last_states}")


def _capture_publish_response_summary(response, note_key: str = "") -> dict | None:
    try:
        request = response.request
        method = request.method
        url = response.url
        if method == "GET":
            return None
        lower_url = url.lower()
        if not any(token in lower_url for token in ["text_notes", "publish", "notes"]):
            return None
        summary = {
            "method": method,
            "url": url,
            "status": response.status,
            "ok": response.ok,
            "note_key": note_key,
            "note_key_match": bool(not note_key or note_key in url),
        }
        if not response.ok:
            try:
                body = response.text() or ""
                summary["body_preview"] = body[:500]
            except Exception as exc:
                summary["body_preview_error"] = str(exc)
        return summary
    except Exception as exc:
        return {"error": str(exc)}


def _click_locator_force_first(page, locator, strategy: str, description: str, timeout_ms: int = 10_000) -> str:
    locator.scroll_into_view_if_needed()
    click_errors = []
    for click_name, clicker in [
        ("force click", lambda: locator.click(timeout=timeout_ms, force=True)),
        ("DOM click", lambda: locator.evaluate("(element) => element.click()")),
        ("通常click", lambda: locator.click(timeout=timeout_ms)),
    ]:
        try:
            clicker()
            page.wait_for_timeout(1000)
            print(f"   ✅ {description}: {strategy} ({click_name})")
            return click_name
        except Exception as exc:
            click_errors.append(f"{click_name}={exc}")

    raise RuntimeError(f"{description} の click に失敗しました: {strategy}: {' / '.join(click_errors[:3])}")


def _click_final_post_button(page, note_key: str = "", dry_run: bool = False) -> dict:
    if dry_run:
        print("   🧪 dry-run のため「投稿する」はクリックしません")
        return {"strategy": "dry_run", "responses": [], "button_states": []}

    responses: list[dict] = []
    button_state_snapshots: list[dict] = []

    def on_response(response):
        summary = _capture_publish_response_summary(response, note_key)
        if summary:
            responses.append(summary)

    page.on("response", on_response)
    try:
        clicked_strategies: list[str] = []
        button_states: list[dict] = []
        for attempt in range(2):
            candidates = [
                ("role_button_投稿する", page.get_by_role("button", name="投稿する")),
                ("button_text_投稿する", page.locator("button").filter(has_text="投稿する")),
            ]
            if attempt > 0:
                candidates = list(reversed(candidates))

            button_states = _collect_final_post_button_states(page)
            button_state_snapshots.append({"attempt": attempt + 1, "phase": "before_click", "states": button_states})
            try:
                strategy, locator = _find_visible_candidate(
                    candidates,
                    "投稿する",
                    timeout_ms=8000 if attempt == 0 else 2500,
                )
            except Exception as exc:
                if attempt == 0:
                    raise
                print(f"   ℹ️ 追加の投稿確認ボタンはありませんでした: {exc}")
                break

            if attempt == 0:
                _click_locator_with_fallback(page, locator, strategy, "投稿する", timeout_ms=10_000)
                click_method = "fallback_order"
            else:
                click_method = _click_locator_force_first(page, locator, strategy, "投稿する", timeout_ms=10_000)
            clicked_strategies.append(f"{strategy}:{click_method}")
            page.wait_for_timeout(4500)
            button_state_snapshots.append({
                "attempt": attempt + 1,
                "phase": "after_click",
                "states": _collect_final_post_button_states(page),
                "url": page.url,
            })
            if attempt == 0:
                print("   ℹ️ 投稿確認用の追加ボタンが残っている可能性があるため、もう一度確認します")
    finally:
        try:
            page.remove_listener("response", on_response)
        except Exception:
            pass

    failed_responses = [
        item for item in responses
        if isinstance(item, dict) and item.get("status") and int(item.get("status") or 0) >= 400
    ]
    if failed_responses:
        raise RuntimeError(f"投稿APIが失敗しました: {failed_responses}")
    return {
        "strategy": "->".join(clicked_strategies),
        "responses": responses,
        "button_states": button_states,
        "button_state_snapshots": button_state_snapshots,
        "final_url_after_click": page.url,
    }


def _publish_editor_page(page, tags: str = NOTE_POST_TAGS, dry_run: bool = False) -> dict:
    note_key = _extract_note_key_from_url(page.url)
    limited_tags, tag_limit = _limit_publish_tags(tags)
    result = {
        "success": False,
        "publish_next_strategy": "",
        "publish_settings_ready_strategy": "",
        "tag_strategy": "",
        "tag_limit": tag_limit,
        "magazine_strategy": "",
        "post_strategy": "",
        "publish_complete_strategy": "",
        "final_url": "",
        "note_key": note_key,
        "dry_run": dry_run,
    }
    result["publish_next_strategy"] = _click_publish_next(page)
    result["publish_settings_ready_strategy"] = _wait_for_publish_settings_ready(page)
    result["tag_strategy"] = _fill_note_hashtags(page, limited_tags)
    result["magazine_strategy"] = _add_publish_magazine(page)
    post_result = _click_final_post_button(page, note_key=note_key, dry_run=dry_run)
    result["post_strategy"] = post_result.get("strategy", "")
    result["post_result"] = post_result
    if dry_run:
        page.wait_for_timeout(1000)
        result["final_url"] = page.url
        result["publish_complete_strategy"] = "dry_run"
    else:
        try:
            completion = _wait_for_published_note_url(page, note_key)
            result["final_url"] = completion["url"]
            result["publish_complete_strategy"] = completion["strategy"]
            result["public_reachability"] = completion.get("reachability", {})
        except Exception as exc:
            result["success"] = False
            result["error"] = str(exc)
            result["final_url"] = page.url
            result["publish_complete_strategy"] = "publish_completion_unverified"
            print(f"   ❌ 公開投稿フロー失敗: {exc}")
            return result
    result["success"] = True
    print(f"   ✅ 公開投稿フロー完了: {result['final_url']}")
    return result


def _run_direct_note_image_upload(page, image_path: Path, artifacts_dir: Path, previous_count: int) -> dict:
    controls_after_menu = _collect_control_snapshot(page)
    _write_json(artifacts_dir / "controls_after_top_image_menu.json", controls_after_menu)
    upload_entry_strategy = _choose_direct_upload_image_file(page, image_path, artifacts_dir=artifacts_dir)
    crop_dialog_strategy, _ = _wait_for_crop_dialog(page)
    _dump_page_artifacts(page, artifacts_dir, "crop_modal_open")
    popup_save_strategy = _save_crop_dialog(page)
    _dump_page_artifacts(page, artifacts_dir, "after_crop_modal_save")
    ready_image_count, ready_wait_strategy = _wait_for_uploaded_image_ready(
        page,
        previous_count=previous_count,
        timeout_sec=60,
    )
    return {
        "upload_entry_strategy": upload_entry_strategy,
        "crop_dialog_strategy": crop_dialog_strategy,
        "popup_save_strategy": popup_save_strategy,
        "ready_wait_strategy": ready_wait_strategy,
        "after_ready_image_count": ready_image_count,
    }


def _is_adobe_workspace_visible(page) -> bool:
    if _is_adobe_welcome_modal_visible(page):
        return True
    if _is_adobe_login_prompt_visible(page):
        return True
    if _has_adobe_file_input_candidate(page):
        return True

    candidate_builders = [
        (
            "cc_everywhere_container",
            lambda scope: scope.locator("xpath=//*[starts-with(local-name(), 'cc-everywhere-container-')]"),
        ),
        ("text_powered_by_adobe", lambda scope: scope.locator("text=Powered by Adobe Express")),
        ("x_embed_editor_save_button", lambda scope: scope.locator("x-embed-editor-save-button")),
        ("sp_button_save_btn", lambda scope: scope.locator("sp-button#save-btn")),
        ("dialog_download_btn", lambda scope: scope.locator("sp-button#dialog-download-btn")),
        (
            "adobe_dialog",
            lambda scope: scope.locator("[role='dialog'], [aria-modal='true']").filter(
                has_text=re.compile("Adobe Express")
            ),
        ),
        ("text_ファイル形式", lambda scope: scope.locator("text=ファイル形式")),
        ("button_アップロード", lambda scope: scope.get_by_role("button", name="アップロード")),
        ("text_アップロード", lambda scope: scope.locator("text=アップロード")),
    ]
    for _, scope in _iter_playwright_scopes(page):
        for _, builder in candidate_builders:
            try:
                locator = builder(scope)
                if locator.count() > 0 and locator.first.is_visible():
                    return True
            except Exception:
                continue
    return False


def _wait_for_adobe_workspace(page, timeout_sec: int = 40) -> None:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if _is_adobe_workspace_visible(page):
            return
        page.wait_for_timeout(1000)
    raise RuntimeError("Adobe Express の作業画面が表示されませんでした。")


def _is_adobe_welcome_modal_visible(page) -> bool:
    candidate_builders = [
        ("text_welcome", lambda scope: scope.locator("text=Adobe Expressへようこそ")),
        ("text_welcome_spaced", lambda scope: scope.locator("text=Adobe Express へようこそ")),
        ("text_welcome_short", lambda scope: scope.locator("text=ようこそ")),
    ]
    for _, scope in _iter_playwright_scopes(page):
        for _, builder in candidate_builders:
            try:
                locator = builder(scope)
                if locator.count() > 0 and locator.first.is_visible():
                    return True
            except Exception:
                continue
    return False


def _is_adobe_login_prompt_visible(page) -> bool:
    candidate_builders = [
        ("text_login", lambda scope: scope.locator("text=ログイン")),
        ("text_adobe_id", lambda scope: scope.locator("text=Adobe ID")),
        ("text_continue_using", lambda scope: scope.locator("text=続けてご利用")),
    ]
    for _, scope in _iter_playwright_scopes(page):
        for _, builder in candidate_builders:
            try:
                locator = builder(scope)
                if locator.count() > 0 and locator.first.is_visible():
                    return True
            except Exception:
                continue
    return False


def _dismiss_adobe_welcome_modal(page) -> str:
    if not _is_adobe_welcome_modal_visible(page):
        return ""

    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(1000)
        if not _is_adobe_welcome_modal_visible(page):
            print("   ✅ Adobe welcome モーダル: Escape で閉じました")
            return "keyboard_escape"
    except Exception:
        pass

    candidate_builders = [
        ("aria_close", lambda scope: scope.locator("[aria-label='閉じる'], [aria-label='Close']")),
        ("role_button_閉じる", lambda scope: scope.get_by_role("button", name=re.compile("閉じる|Close"))),
        ("role_button_continue", lambda scope: scope.get_by_role("button", name=re.compile("続ける|続行|次へ|開始|始める|了解|スキップ"))),
        ("button_text_continue", lambda scope: scope.locator("button").filter(has_text=re.compile("続ける|続行|次へ|開始|始める|了解|スキップ"))),
    ]
    strategy = _click_rightmost_scoped_candidate(
        page,
        candidate_builders=candidate_builders,
        description="Adobe welcome モーダル解除",
        timeout_ms=4000,
    )
    page.wait_for_timeout(1500)
    return strategy


def _wait_for_adobe_workspace_closed(page, timeout_sec: int = 60) -> None:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if not _is_adobe_workspace_visible(page):
            return
        page.wait_for_timeout(1000)
    print("   ⚠️ Adobe Express 画面のクローズ待機がタイムアウトしました。続行します。")


def _choose_adobe_express_entry(page) -> str:
    return _click_visible_candidate(
        page,
        candidates=[
            ("text=Adobe Expressで画像をつくる", page.locator("text=Adobe Expressで画像をつくる")),
            ("button_text_Adobe", page.locator("button").filter(has_text="Adobe Expressで画像をつくる")),
            ("role_button_Adobe", page.get_by_role("button", name="Adobe Expressで画像をつくる")),
        ],
        description="Adobe Express 導線",
    )


def _open_adobe_upload_sidebar(page) -> str:
    return _click_visible_scoped_candidate(
        page,
        candidate_builders=[
            ("sidebar_upload_role_exact", lambda scope: scope.get_by_role("button", name="アップロード", exact=True)),
            ("sidebar_upload_text_exact", lambda scope: scope.locator("button, [role='button'], label").filter(has_text=re.compile(r"^アップロード$"))),
            ("sidebar_upload_aria", lambda scope: scope.locator("[aria-label='アップロード']")),
        ],
        description="Adobe Express アップロードサイドバー",
        timeout_ms=4000,
    )


def _wait_for_adobe_upload_signal(page, image_path: Path, timeout_sec: int = 15) -> str:
    candidate_builders = [
        ("blob_image", lambda scope: scope.locator("img[src^='blob:']")),
        ("blob_image_alt", lambda scope: scope.locator(f"img[alt*='{image_path.stem}']")),
        ("filename_text", lambda scope: scope.locator(f"text={image_path.name}")),
        ("filename_stem_text", lambda scope: scope.locator(f"text={image_path.stem}")),
    ]
    deadline = time.time() + timeout_sec
    last_error = ""
    while time.time() < deadline:
        try:
            strategy, _ = _find_visible_scoped_candidate(
                page,
                candidate_builders,
                "Adobe Express アップロード反映",
                timeout_ms=1200,
            )
            print(f"   ✅ Adobe アップロード反映検出: {strategy}")
            return strategy
        except Exception as exc:
            last_error = str(exc)
            page.wait_for_timeout(1000)
    print(f"   ⚠️ Adobe アップロード反映は確認できませんでした: {last_error}")
    return "timeout"


def _build_adobe_top_insert_candidate_builders():
    return [
        (
            "sp_button_save_btn",
            lambda scope: scope.locator("x-embed-editor-save-button sp-button#save-btn"),
        ),
        (
            "sp_button_save_btn_global",
            lambda scope: scope.locator("sp-button#save-btn"),
        ),
        (
            "sp_button_save_to_host_app",
            lambda scope: scope.locator("sp-button#save-btn[export-option-id='save-to-host-app']"),
        ),
        ("role_button_挿入", lambda scope: scope.get_by_role("button", name="挿入")),
        ("button_text_挿入", lambda scope: scope.locator("button").filter(has_text="挿入")),
    ]


def _build_adobe_confirm_insert_candidate_builders():
    return [
        (
            "dialog_download_btn_scoped",
            lambda scope: scope.locator(
                "x-embed-editor-save-button overlay-trigger[type='modal'] sp-button#dialog-download-btn"
            ),
        ),
        (
            "dialog_download_btn_global",
            lambda scope: scope.locator("sp-button#dialog-download-btn"),
        ),
        (
            "dialog_download_btn_host_app",
            lambda scope: scope.locator(
                "overlay-trigger[type='modal'] sp-button[export-option-id='save-to-host-app']"
            ),
        ),
        (
            "dialog_download_btn_slot_trigger",
            lambda scope: scope.locator("overlay-trigger[type='modal'] sp-button[slot='trigger']"),
        ),
        (
            "panel_button_挿入_xpath",
            lambda scope: scope.locator(
                "xpath=//div[.//*[contains(normalize-space(), 'ファイル形式')]]//button[normalize-space()='挿入']"
            ),
        ),
        (
            "panel_button_挿入_text_xpath",
            lambda scope: scope.locator(
                "xpath=//div[.//*[contains(normalize-space(), 'ファイル形式')]]//*[self::button or @role='button'][contains(normalize-space(), '挿入')]"
            ),
        ),
    ]


def _wait_for_adobe_confirm_insert_panel(page, timeout_sec: int = 20) -> str:
    deadline = time.time() + timeout_sec
    last_error = ""
    while time.time() < deadline:
        if _is_adobe_login_prompt_visible(page):
            raise RuntimeError("ADOBE_LOGIN_REQUIRED")
        try:
            strategy, _ = _find_visible_scoped_candidate(
                page,
                _build_adobe_confirm_insert_candidate_builders(),
                "Adobe Express 確定挿入パネル",
                timeout_ms=1200,
            )
            print(f"   ✅ Adobe Express 確定挿入パネル検出: {strategy}")
            return strategy
        except Exception as exc:
            last_error = str(exc)
            page.wait_for_timeout(800)
    raise RuntimeError(f"Adobe Express 確定挿入パネルが表示されませんでした: {last_error}")


def _upload_image_via_adobe_express(page, image_path: Path, artifacts_dir: Path, previous_count: int) -> dict:
    adobe_entry_strategy = _choose_adobe_express_entry(page)
    _dump_page_artifacts(page, artifacts_dir, "adobe_entry_clicked")
    _wait_for_adobe_workspace(page)
    _dump_page_artifacts(page, artifacts_dir, "adobe_workspace_open")
    welcome_modal_strategy = ""
    if not welcome_modal_strategy:
        try:
            welcome_modal_strategy = _dismiss_adobe_welcome_modal(page)
        except Exception as exc:
            print(f"   笞・・Adobe welcome 繝｢繝ｼ繝繝ｫ隗｣髯､螟ｱ謨暦ｼ育ｶ夊｡鯉ｼ・ {exc}")
    if welcome_modal_strategy:
        page.wait_for_timeout(1500)
        _dump_page_artifacts(page, artifacts_dir, "adobe_welcome_dismissed")

    _write_json(
        artifacts_dir / "adobe_file_input_candidates_pre_sidebar.json",
        _serialize_file_input_candidates(page, prefer_adobe=True),
    )
    pre_sidebar_input_strategy = _wait_for_existing_file_input_any_scope(
        page,
        image_path,
        prefer_adobe=True,
        timeout_ms=6000,
        poll_ms=400,
    )

    if pre_sidebar_input_strategy:
        upload_sidebar_strategy = "direct_input_pre_sidebar"
        upload_strategy = pre_sidebar_input_strategy
    else:
        upload_sidebar_strategy = _open_adobe_upload_sidebar(page)
        _dump_page_artifacts(page, artifacts_dir, "adobe_upload_sidebar_open")
        _write_json(
            artifacts_dir / "adobe_file_input_candidates_post_sidebar.json",
            _serialize_file_input_candidates(page, prefer_adobe=True),
        )
        upload_strategy = _wait_for_existing_file_input_any_scope(
            page,
            image_path,
            prefer_adobe=True,
            timeout_ms=6000,
            poll_ms=400,
        )
    if not upload_strategy:
        upload_strategy = _click_visible_scoped_candidate(
            page,
            candidate_builders=[
                ("role_button_アップロード", lambda scope: scope.get_by_role("button", name="アップロード")),
                ("text_アップロード", lambda scope: scope.locator("text=アップロード")),
                ("label_アップロード", lambda scope: scope.locator("label").filter(has_text="アップロード")),
            ],
            description="Adobe Express アップロード導線",
        )
        page.wait_for_timeout(1200)
        direct_input = _wait_for_existing_file_input_any_scope(
            page,
            image_path,
            prefer_adobe=True,
            timeout_ms=4000,
            poll_ms=300,
        )
        if direct_input:
            upload_strategy = f"{upload_strategy}:{direct_input}"

    upload_signal_strategy = _wait_for_adobe_upload_signal(page, image_path, timeout_sec=12)
    page.wait_for_timeout(2500)
    _dump_page_artifacts(page, artifacts_dir, "adobe_after_upload")
    welcome_modal_strategy = ""
    try:
        welcome_modal_strategy = _dismiss_adobe_welcome_modal(page)
    except Exception as exc:
        print(f"   ⚠️ Adobe welcome モーダル解除失敗（続行）: {exc}")

    insert_strategy = _click_visible_scoped_candidate(
        page,
        candidate_builders=_build_adobe_top_insert_candidate_builders(),
        description="Adobe Express 上部挿入",
    )
    page.wait_for_timeout(2000)
    _dump_page_artifacts(page, artifacts_dir, "adobe_after_first_insert")
    if _is_adobe_login_prompt_visible(page):
        raise RuntimeError("ADOBE_LOGIN_REQUIRED")

    confirm_panel_strategy = _wait_for_adobe_confirm_insert_panel(page, timeout_sec=20)
    confirm_insert_strategy = _click_visible_scoped_candidate(
        page,
        candidate_builders=_build_adobe_confirm_insert_candidate_builders(),
        description="Adobe Express 確定挿入",
        timeout_ms=6000,
    )
    _dump_page_artifacts(page, artifacts_dir, "adobe_insert_confirmed")

    _wait_for_adobe_workspace_closed(page)
    page.wait_for_timeout(3000)
    ready_image_count, ready_wait_strategy = _wait_for_uploaded_image_ready(
        page,
        previous_count=previous_count,
        timeout_sec=30,
    )
    return {
        "adobe_entry_strategy": adobe_entry_strategy,
        "upload_sidebar_strategy": upload_sidebar_strategy,
        "upload_entry_strategy": upload_strategy,
        "upload_signal_strategy": upload_signal_strategy,
        "welcome_modal_strategy": welcome_modal_strategy,
        "insert_strategy": insert_strategy,
        "confirm_panel_strategy": confirm_panel_strategy,
        "confirm_insert_strategy": confirm_insert_strategy,
        "ready_wait_strategy": ready_wait_strategy,
        "after_ready_image_count": ready_image_count,
    }


def _collect_note_editor_snapshot(page) -> dict:
    return page.evaluate(
        """
        () => {
          const titleEl = document.querySelector('.note-editor__title-input');
          const editor = document.querySelector('.note-editable, [contenteditable="true"]') || document.querySelector('.ProseMirror');
          const normalize = (value) => (value || '').replace(/\\u200B/g, '').replace(/\\s+/g, ' ').trim();
          if (!editor) {
            return { title: normalize(titleEl?.value || titleEl?.innerText || titleEl?.textContent || ''), editor_text: '', h1s: [], h2s: [] };
          }
          return {
            title: normalize(titleEl?.value || titleEl?.innerText || titleEl?.textContent || ''),
            editor_text: normalize(editor.innerText || editor.textContent || ''),
            h1s: Array.from(editor.querySelectorAll('h1')).map((el) => normalize(el.innerText || el.textContent || '')).filter(Boolean),
            h2s: Array.from(editor.querySelectorAll('h2')).map((el) => normalize(el.innerText || el.textContent || '')).filter(Boolean),
          };
        }
        """
    )


def _extract_first_url_before_marker(markdown: str) -> str:
    before_marker = (markdown or "").split("▼", 1)[0]
    matches = [match.group(0).strip() for match in URL_RE.finditer(before_marker)]
    if not matches:
        return ""
    for candidate in matches:
        lowered = candidate.lower()
        if "amzn.to" in lowered or "amazon.co.jp" in lowered or "amazon.com" in lowered:
            return candidate
    return matches[0]


def _extract_product_name_from_note_context(snapshot: dict) -> tuple[str, str]:
    affiliate_module = _load_amazon_affiliate_module()

    title = (snapshot.get("title") or "").strip()
    if title:
        product_name = affiliate_module.extract_product_name(title)
        if product_name and len(product_name) <= 30:
            return product_name, "note_title"

    h1s = snapshot.get("h1s") or []
    if h1s:
        product_name = affiliate_module.extract_product_name(h1s[0])
        if product_name and len(product_name) <= 30:
            return product_name, "note_h1"

    h2s = snapshot.get("h2s") or []
    if h2s:
        synthetic_markdown = "\n".join(f"## {h2}" for h2 in h2s)
        product_name = affiliate_module._extract_product_name_from_h2s(synthetic_markdown)
        if product_name:
            return product_name, "note_h2"

    return "", ""


def _resolve_amazon_image_target(page, source_markdown: str) -> dict:
    snapshot = _collect_note_editor_snapshot(page)
    first_url = _extract_first_url_before_marker(source_markdown)
    if first_url:
        amazon_image_module = _load_amazon_top_image_module()
        asin = amazon_image_module.extract_asin_from_url(first_url)
        if asin:
            return {
                "mode": "asin",
                "asin": asin,
                "keyword": "",
                "source": "body_url_before_marker",
                "source_url": first_url,
                "snapshot": snapshot,
            }
        print(f"   ⚠️ 先頭URLから ASIN を抽出できませんでした。タイトル/H2 フォールバックへ進みます: {first_url}")

    product_name, source = _extract_product_name_from_note_context(snapshot)
    if product_name:
        return {
            "mode": "keyword",
            "asin": "",
            "keyword": product_name,
            "source": source,
            "source_url": "",
            "snapshot": snapshot,
        }

    return {
        "mode": "skip",
        "asin": "",
        "keyword": "",
        "source": "unresolved",
        "source_url": "",
        "snapshot": snapshot,
    }


def _select_note_top_image_for_upload(fetch_result) -> tuple[object, str]:
    prepared_image = getattr(fetch_result, "prepared_image", None)
    if prepared_image:
        return prepared_image, "prepared"
    if fetch_result.hires_image:
        return fetch_result.hires_image, "hires"
    return fetch_result.api_image, "api"


def _attach_amazon_top_image_to_page(
    page,
    source_markdown: str,
    artifacts_dir: Path | None = None,
    save_draft_after_upload: bool = True,
) -> dict:
    artifacts_dir = artifacts_dir or NOTE_TOP_IMAGE_ARTIFACTS_DIR
    force_direct_upload = os.getenv("NOTE_TOP_IMAGE_FORCE_DIRECT", "").strip().lower() in {"1", "true", "yes", "on"}
    use_adobe_upload = NOTE_TOP_IMAGE_USE_ADOBE

    target = _resolve_amazon_image_target(page, source_markdown)
    _write_json(artifacts_dir / "amazon_target_resolution.json", target)

    if target["mode"] == "skip":
        print("   ⚠️ Amazon 画像対象を特定できなかったため、トップ画像挿入をスキップします。")
        return {
            "image_flow": "skipped",
            "image_target_source": target["source"],
            "draft_save_strategy": "",
            "before_image_count": _count_page_images(page),
        }

    amazon_image_module = _load_amazon_top_image_module()
    fetch_result = amazon_image_module.fetch_and_save_top_images(
        keyword=target["keyword"],
        asin=target["asin"],
    )
    amazon_hires_probe = {
        "detail_page_url": fetch_result.detail_page_url,
        "asin": fetch_result.asin,
        "requests_hires_url": fetch_result.hires_image.image_url if fetch_result.hires_image else "",
        "browser_probe_used": False,
        "browser_hires_url": "",
        "browser_hires_saved_path": "",
        "browser_prepared_saved_path": "",
        "requests_error": "",
        "browser_error": "",
        "prepared_error": "",
    }
    try:
        requests_html = amazon_image_module.fetch_detail_page_html(
            fetch_result.asin,
            getattr(amazon_image_module, "DEFAULT_MARKETPLACE", "www.amazon.co.jp"),
        )
        _write_text(artifacts_dir / "amazon_detail_requests.html", requests_html)
        amazon_hires_probe["requests_hires_url"] = (
            amazon_image_module.extract_hires_from_html(requests_html) or ""
        )
    except Exception as exc:
        amazon_hires_probe["requests_error"] = str(exc)

    if not fetch_result.hires_image:
        amazon_hires_probe["browser_probe_used"] = True
        browser_page = None
        try:
            browser_page = page.context.new_page()
            browser_page.goto(fetch_result.detail_page_url, wait_until="domcontentloaded", timeout=60_000)
            browser_page.wait_for_timeout(2500)
            browser_html = browser_page.content()
            _write_text(artifacts_dir / "amazon_detail_browser.html", browser_html)
            browser_hires_url = amazon_image_module.extract_hires_from_html(browser_html) or ""
            amazon_hires_probe["browser_hires_url"] = browser_hires_url
            if browser_hires_url:
                hires_image = amazon_image_module.save_image(
                    label="hires",
                    keyword=fetch_result.asin or target["keyword"] or "amazon_image",
                    image_url=browser_hires_url,
                    output_dir=fetch_result.api_image.local_path.parent,
                    suffix="_hires",
                )
                hires_image = amazon_image_module.save_and_optionally_upload(
                    hires_image,
                    amazon_image_module.build_remote_image_folder(
                        getattr(amazon_image_module, "DEFAULT_ONEDRIVE_FOLDER", ""),
                        getattr(amazon_image_module, "RAW_SUBDIR_NAME", "raw"),
                    ),
                )
                fetch_result.hires_image = hires_image
                amazon_hires_probe["browser_hires_saved_path"] = str(hires_image.local_path)
                try:
                    prepared_image = amazon_image_module.create_prepared_note_hero_image(
                        source_image=hires_image,
                        keyword=fetch_result.asin or target["keyword"] or "amazon_image",
                    )
                    prepared_image = amazon_image_module.save_and_optionally_upload(
                        prepared_image,
                        amazon_image_module.build_remote_image_folder(
                            getattr(amazon_image_module, "DEFAULT_ONEDRIVE_FOLDER", ""),
                            getattr(amazon_image_module, "PREPARED_SUBDIR_NAME", "prepared"),
                        ),
                    )
                    fetch_result.prepared_image = prepared_image
                    amazon_hires_probe["browser_prepared_saved_path"] = str(prepared_image.local_path)
                except Exception as exc:
                    amazon_hires_probe["prepared_error"] = str(exc)
        except Exception as exc:
            amazon_hires_probe["browser_error"] = str(exc)
        finally:
            try:
                if browser_page:
                    browser_page.close()
            except Exception:
                pass
    _write_json(artifacts_dir / "amazon_hires_probe.json", amazon_hires_probe)

    before_count = _count_page_images(page)
    controls_before = _collect_control_snapshot(page)
    _write_json(artifacts_dir / "controls_before_top_image.json", controls_before)

    image_button_strategy = _click_top_image_button(page)
    _dump_page_artifacts(page, artifacts_dir, "top_image_menu_open")

    selected_upload_image, selected_upload_kind = _select_note_top_image_for_upload(fetch_result)

    if force_direct_upload and use_adobe_upload:
        print("   ℹ️ NOTE_TOP_IMAGE_FORCE_DIRECT=1 のため Adobe 経由を使わず通常アップロードを維持します")

    if fetch_result.hires_image and use_adobe_upload and not force_direct_upload:
        try:
            flow_result = _upload_image_via_adobe_express(
                page,
                fetch_result.hires_image.local_path,
                artifacts_dir,
                previous_count=before_count,
            )
            image_flow = "adobe_hires_debug"
            selected_image_path = str(fetch_result.hires_image.local_path)
            selected_image_kind = "hires"
        except Exception as exc:
            print(f"   ⚠️ Adobe Express フロー失敗のため通常アップロードへフォールバックします: {exc}")
            page.reload(wait_until="domcontentloaded", timeout=60_000)
            if not _wait_for_editor_content(page, timeout_sec=EDITOR_LOAD_TIMEOUT_SEC):
                raise RuntimeError("Adobe Express 失敗後のエディタ再読込に失敗しました。")
            before_count = _count_page_images(page)
            image_button_strategy = _click_top_image_button(page)
            _dump_page_artifacts(page, artifacts_dir, "top_image_menu_reopen_after_adobe_failure")
            flow_result = _run_direct_note_image_upload(
                page,
                selected_upload_image.local_path,
                artifacts_dir,
                previous_count=before_count,
            )
            flow_result["adobe_error"] = str(exc)
            image_flow = f"direct_{selected_upload_kind}_after_adobe_failure"
            selected_image_path = str(selected_upload_image.local_path)
    else:
        flow_result = _run_direct_note_image_upload(
            page,
            selected_upload_image.local_path,
            artifacts_dir,
            previous_count=before_count,
        )
        image_flow = f"direct_{selected_upload_kind}"
        selected_image_path = str(selected_upload_image.local_path)

    if save_draft_after_upload:
        draft_save_strategy = _save_editor_draft(page)
        _dump_page_artifacts(page, artifacts_dir, "after_top_image_draft_save")
    else:
        draft_save_strategy = "skipped_for_publish"
        _dump_page_artifacts(page, artifacts_dir, "after_top_image_before_publish")

    result = {
        "image_flow": image_flow,
        "image_target_source": target["source"],
        "image_target_asin": fetch_result.asin,
        "image_target_keyword": target["keyword"],
        "image_target_url": target["source_url"],
        "image_button_strategy": image_button_strategy,
        "draft_save_strategy": draft_save_strategy,
        "api_image_path": str(fetch_result.api_image.local_path),
        "api_image_url": fetch_result.api_image.image_url,
        "hires_image_path": str(fetch_result.hires_image.local_path) if fetch_result.hires_image else "",
        "hires_image_url": fetch_result.hires_image.image_url if fetch_result.hires_image else "",
        "prepared_image_path": str(fetch_result.prepared_image.local_path) if getattr(fetch_result, "prepared_image", None) else "",
        "prepared_image_url": fetch_result.prepared_image.image_url if getattr(fetch_result, "prepared_image", None) else "",
        "selected_image_path": selected_image_path,
        "selected_image_kind": selected_upload_kind,
        "before_image_count": before_count,
    }
    result.update(flow_result)
    _write_json(artifacts_dir / "top_image_result.json", result)
    return result


def _attach_local_top_image_to_page(
    page,
    image_path: str | Path,
    artifacts_dir: Path | None = None,
    save_draft_after_upload: bool = True,
) -> dict:
    """ローカル画像ファイルをnoteのトップ画像として設定する。"""
    artifacts_dir = artifacts_dir or NOTE_TOP_IMAGE_ARTIFACTS_DIR
    local_path = Path(image_path)
    if not local_path.exists():
        print(f"   ⚠️ ローカルトップ画像が見つからないためスキップします: {local_path}")
        return {
            "image_flow": "skipped",
            "image_target_source": "local_file_missing",
            "selected_image_path": str(local_path),
            "draft_save_strategy": "",
            "before_image_count": _count_page_images(page),
        }

    before_count = _count_page_images(page)
    controls_before = _collect_control_snapshot(page)
    _write_json(artifacts_dir / "controls_before_top_image.json", controls_before)

    image_button_strategy = _click_top_image_button(page)
    _dump_page_artifacts(page, artifacts_dir, "top_image_menu_open")
    flow_result = _run_direct_note_image_upload(
        page,
        local_path,
        artifacts_dir,
        previous_count=before_count,
    )

    if save_draft_after_upload:
        draft_save_strategy = _save_editor_draft(page)
        _dump_page_artifacts(page, artifacts_dir, "after_top_image_draft_save")
    else:
        draft_save_strategy = "skipped_for_publish"
        _dump_page_artifacts(page, artifacts_dir, "after_top_image_before_publish")

    result = {
        "image_flow": "direct_local",
        "image_target_source": "local_file",
        "image_button_strategy": image_button_strategy,
        "draft_save_strategy": draft_save_strategy,
        "selected_image_path": str(local_path),
        "selected_image_kind": "local",
        "before_image_count": before_count,
    }
    result.update(flow_result)
    _write_json(artifacts_dir / "top_image_result.json", result)
    return result


# ── Markdown前処理 ─────────────────────────────────────
def extract_title_and_body(markdown: str) -> tuple:
    """H1をタイトル、それ以降を本文として分離"""
    lines = markdown.replace('\r\n', '\n').split('\n')
    title, body_start = "", 0
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith('# ') and not s.startswith('## '):
            title, body_start = s.lstrip('# ').strip(), i + 1
            break
    if not title:
        for i, line in enumerate(lines):
            if line.strip():
                title, body_start = line.strip().lstrip('# ').strip(), i + 1
                break
    body_lines, skip = [], False
    for line in lines[body_start:]:
        s = line.strip()
        if s.startswith('## 🎬') or s.startswith('## Captions'):
            skip = True; continue
        if skip and s.startswith('## '): skip = False
        if not skip: body_lines.append(line)
    return title, '\n'.join(body_lines).strip()


# ── Markdown → noteエディタHTML変換 ───────────────────
def _inline_format(text: str) -> str:
    """インライン要素の変換（太字、リンク、コード）"""
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'<em>\1</em>', text)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    return text


def markdown_to_note_html(md: str) -> str:
    """MarkdownをnoteのエディタHTML形式に変換"""
    html_parts = []
    lines = md.split('\n')
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # 空行 → スキップ（<br>は422エラーの原因になるため除外）
        if not stripped:
            i += 1
            continue

        # ### → h3
        if stripped.startswith('### '):
            text = _inline_format(stripped[4:].strip())
            html_parts.append(f'<h3>{text}</h3>')
            i += 1
            continue

        # ## → h2
        if stripped.startswith('## '):
            text = _inline_format(stripped[3:].strip())
            html_parts.append(f'<h2>{text}</h2>')
            i += 1
            continue

        # リスト項目（- または *）
        if stripped.startswith('- ') or stripped.startswith('* '):
            items = []
            while i < len(lines) and (lines[i].strip().startswith('- ') or lines[i].strip().startswith('* ')):
                item_text = lines[i].strip()[2:].strip()
                items.append(f'<li>{_inline_format(item_text)}</li>')
                i += 1
            html_parts.append(f'<ul>{"".join(items)}</ul>')
            continue

        # URL単独行 → そのまま段落（noteが自動OGP展開）
        if re.match(r'^https?://\S+$', stripped):
            html_parts.append(f'<p>{stripped}</p>')
            i += 1
            continue

        # 通常段落
        text = _inline_format(stripped)
        html_parts.append(f'<p>{text}</p>')
        i += 1

    return '\n'.join(html_parts)


# ── Cookie管理 ────────────────────────────────────────
def _load_cookies() -> dict:
    """StorageStateまたはCookieファイルからCookie辞書を生成"""
    raw = ""
    if NOTE_STORAGE_STATE:
        raw = NOTE_STORAGE_STATE
        print("   🍪 Cookieを環境変数から読み込み")
    elif LOCAL_STATE_FILE.exists():
        raw = LOCAL_STATE_FILE.read_text(encoding="utf-8")
        print("   🍪 Cookieをローカルファイルから読み込み")

    if not raw:
        return {}

    try:
        data = json.loads(raw)
        cookies = {}
        # Playwright StorageState形式 {"cookies": [...]}
        if isinstance(data, dict) and "cookies" in data:
            for c in data["cookies"]:
                if ".note.com" in c.get("domain", "") or "note.com" in c.get("domain", ""):
                    cookies[c["name"]] = c["value"]
        # シンプルなCookie辞書形式 {"name": "value", ...}
        elif isinstance(data, dict):
            cookies = data
        # Cookie配列形式 [{"name": ..., "value": ...}, ...]
        elif isinstance(data, list):
            for c in data:
                if isinstance(c, dict) and "name" in c:
                    cookies[c["name"]] = c["value"]
        if cookies:
            print(f"   🍪 {len(cookies)}個のCookieを読み込み")
        return cookies
    except Exception as e:
        print(f"   ⚠️ Cookie読み込み失敗: {e}")
        return {}


def _save_cookies_state(session: http_requests.Session):
    """セッションのCookieをStorageState互換形式で保存・GitHub Secret更新"""
    # 同名Cookieが複数ドメインに存在する場合があるため、iter_cookies()で安全に取得
    cookie_list = []
    seen = set()
    for cookie in session.cookies:
        key = (cookie.name, cookie.domain)
        if key in seen:
            continue
        seen.add(key)
        cookie_list.append({
            "name": cookie.name,
            "value": cookie.value,
            "domain": cookie.domain or ".note.com",
            "path": cookie.path or "/",
            "httpOnly": cookie.has_nonstandard_attr("HttpOnly") or cookie.name.startswith("_"),
            "secure": cookie.secure,
            "sameSite": "Lax",
        })

    if not cookie_list:
        print("   ℹ️ 保存すべきCookieがありません")
        return

    state = {"cookies": cookie_list, "origins": []}
    state_json = json.dumps(state, ensure_ascii=False)

    # ローカル保存
    LOCAL_STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"   💾 ローカル保存: {LOCAL_STATE_FILE}")

    # GitHub Secret自動更新
    _auto_refresh_github_secret(state_json)


# ── GitHub Variable保存（下書きURL記録用） ────────────
def _save_draft_url_to_github_var(file_id: str, url: str):
    """下書き保存したURLをGitHub Repository Variableに記録（フロントエンドから参照可能）"""
    if not GITHUB_TOKEN or not file_id or not url:
        return
    import hashlib
    key_hash = hashlib.md5(file_id.encode()).hexdigest()[:8].upper()
    var_name = f"NOTE_DRAFT_URL_{key_hash}"
    api_base = f"https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    # 存在確認してPATCH or POST
    check = http_requests.get(f"{api_base}/actions/variables/{var_name}", headers=headers)
    if check.status_code == 200:
        res = http_requests.patch(
            f"{api_base}/actions/variables/{var_name}",
            headers=headers,
            json={"name": var_name, "value": url},
        )
    else:
        res = http_requests.post(
            f"{api_base}/actions/variables",
            headers=headers,
            json={"name": var_name, "value": url},
        )
    if res.status_code in (200, 201, 204):
        print(f"   ✅ GitHub Variable {var_name} を保存しました")
    else:
        print(f"   ⚠️ Variable保存失敗 ({res.status_code}): {res.text[:150]}")


# ── GitHub Secret自動更新 ─────────────────────────────
def _auto_refresh_github_secret(new_state_json: str):
    """GitHub APIを使って note storage state Secret を自動更新"""
    if not GITHUB_TOKEN:
        print("   ℹ️ GITHUB_TOKEN未設定のためSecretの自動更新をスキップ")
        return
    try:
        import nacl.encoding
        import nacl.public
    except ImportError:
        print("   ⚠️ pynacl未インストール。pip install pynacl でインストールしてください。")
        return

    api_base = f"https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    # リポジトリの公開鍵を取得
    res = http_requests.get(f"{api_base}/actions/secrets/public-key", headers=headers)
    if not res.ok:
        print(f"   ⚠️ GitHub公開鍵取得失敗 ({res.status_code}): {res.text[:200]}")
        return

    key_data = res.json()
    pub_key = nacl.public.PublicKey(key_data["key"].encode(), nacl.encoding.Base64Encoder)
    sealed = nacl.public.SealedBox(pub_key)
    encrypted = base64.b64encode(sealed.encrypt(new_state_json.encode())).decode()

    # Secretを更新
    res = http_requests.put(
        f"{api_base}/actions/secrets/{NOTE_STORAGE_SECRET_NAME}",
        headers=headers,
        json={"encrypted_value": encrypted, "key_id": key_data["key_id"]},
    )
    if res.status_code in (201, 204):
        print(f"   ✅ {NOTE_STORAGE_SECRET_NAME} を自動更新しました")
    else:
        print(f"   ⚠️ Secret更新失敗 ({res.status_code}): {res.text[:200]}")


# ── OGP展開関数群 ─────────────────────────────────────
def _cookies_to_playwright(cookies: dict) -> list:
    """Cookie辞書 → Playwright の add_cookies() 形式リストに変換"""
    return [
        {"name": name, "value": value, "domain": ".note.com", "path": "/"}
        for name, value in cookies.items()
    ]


def _resolve_browser_storage_state_path() -> str | None:
    adobe_state_env = os.getenv("ADOBE_EXPRESS_STORAGE_STATE", "").strip()
    if adobe_state_env:
        try:
            state = json.loads(adobe_state_env)
        except Exception as exc:
            adobe_state_path = Path(adobe_state_env)
            try:
                if adobe_state_path.exists():
                    return str(adobe_state_path)
            except OSError as path_exc:
                print(f"   [WARN] ADOBE_EXPRESS_STORAGE_STATE をパスとして確認できませんでした: {path_exc}")
            print(f"   [WARN] ADOBE_EXPRESS_STORAGE_STATE を storage_state JSON として解釈できませんでした: {exc}")
        else:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                suffix=".json",
                prefix="note_browser_state_",
                delete=False,
            ) as temp_state_file:
                json.dump(state, temp_state_file, ensure_ascii=False, indent=2)
                temp_state_path = temp_state_file.name
            print(f"   📦 ADOBE_EXPRESS_STORAGE_STATE を一時ファイル化しました: {temp_state_path}")
            return temp_state_path
    if ADOBE_STORAGE_STATE_FILE.exists():
        return str(ADOBE_STORAGE_STATE_FILE)
    return None


def _wait_for_editor_content(page, timeout_sec: int = EDITOR_LOAD_TIMEOUT_SEC) -> bool:
    """ProseMirrorエディタのコンテンツ（p/h2/h3）が出現するまでポーリング待機"""
    print(f"   ⏳ エディタコンテンツのロード待機（最大{timeout_sec}秒）...")
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            count = page.locator(EDITOR_CONTENT_SELECTOR).count()
            if count > 0:
                text = page.locator(EDITOR_CONTENT_SELECTOR).first.text_content()
                if text and text.strip():
                    elapsed = timeout_sec - (deadline - time.time())
                    print(f"   ✅ エディタコンテンツ検出: {count}要素（{elapsed:.1f}秒後）")
                    return True
        except Exception as e:
            print(f"   ⚠️ 待機中エラー: {e}")
        time.sleep(1)
    print(f"   ❌ タイムアウト: {timeout_sec}秒待ってもエディタコンテンツが現れませんでした")
    return False


def process_ogp_urls(page) -> int:
    """OGPカード展開 + 不要改行削除をまとめて実行する。処理URL数を返す。"""
    print("\n   [Python] OGP展開ループを開始...")
    page.evaluate(JS_FUNCTIONS)
    page.evaluate("window.noteFormatter.processTitle()")
    page.evaluate("window.noteFormatter.convertMarkdownToHtml()")

    total_processed = 0
    MAX_SWEEPS = 3

    for sweep in range(MAX_SWEEPS):
        print(f"\n   [Python] 🔄 {sweep + 1}回目のスイープ...")
        all_urls = page.evaluate("window.noteFormatter.extractUrls()")
        target_urls = [u for u in set(all_urls) if any(d in u for d in OGP_TARGET_DOMAINS)]

        if not target_urls:
            print("   [Python] 展開漏れのURLはありません。スイープ終了。")
            break

        print(f"   [Python] 残存対象URL: {len(target_urls)}種 / 計{len(all_urls)}箇所")
        processed_this_loop = 0
        target_counts = {u: 0 for u in target_urls}

        for url in target_urls:
            occurrences = all_urls.count(url)
            while target_counts[url] < occurrences:
                target_counts[url] += 1
                found = page.evaluate(
                    "(args) => window.noteFormatter.setCaretAtUrlEnd(args.url, args.occ)",
                    {"url": url, "occ": target_counts[url]},
                )
                if found:
                    page.keyboard.press("Enter")
                    processed_this_loop += 1
                    page.wait_for_timeout(300)

        total_processed += processed_this_loop
        print("   [Python] カード展開の非同期反映を待機 (3秒)...")
        page.wait_for_timeout(3000)

    print("\n   [Python] 🧹 不要な空行を最終一括削除...")
    page.evaluate("window.noteFormatter.normalizeLineBreaks()")
    return total_processed


def _run_ogp_expansion_on_draft(
    editor_url: str,
    cookies_dict: dict,
    headless: bool = True,
    source_markdown: str = "",
    run_ogp: bool = True,
    run_top_image: bool = True,
    insert_toc: bool = True,
    publish_after: bool = False,
    dry_run_publish: bool = False,
    publish_tags: str = NOTE_POST_TAGS,
    artifacts_dir: Path | None = None,
    top_image_path: str = "",
    body_image_uploads: list[dict] | None = None,
) -> dict:
    """
    下書き作成後のエディタURLへPlaywrightでアクセスし、OGP展開とトップ画像処理を実行する。
    OGP処理後に目次挿入と Amazon トップ画像処理を行い、最後に下書き保存または公開投稿へ進む。
    """
    from playwright.sync_api import sync_playwright

    artifacts_dir = artifacts_dir or NOTE_TOP_IMAGE_ARTIFACTS_DIR
    result = {
        "editor_url": editor_url,
        "ogp_processed_count": 0,
        "toc": {},
        "body_images": {},
        "top_image": {},
        "publish": {},
        "success": False,
    }

    print(f"\n── Phase 4: OGP展開 + 目次 + トップ画像（Playwright） ──")
    print(f"   対象URL: {editor_url}")

    playwright_cookies = _cookies_to_playwright(cookies_dict)

    with sync_playwright() as p:
        storage_state_path = _resolve_browser_storage_state_path() if (NOTE_TOP_IMAGE_USE_ADOBE or NOTE_TOP_IMAGE_DEBUG) else None
        browser = p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context_kwargs = dict(
            viewport={"width": 1280, "height": 900},
            user_agent=UA,
            locale="ja-JP",
        )
        if storage_state_path:
            context_kwargs["storage_state"] = storage_state_path
        context = browser.new_context(**context_kwargs)
        if storage_state_path:
            print(f"   📦 追加のブラウザ state を読込: {storage_state_path}")
        context.add_cookies(playwright_cookies)

        page = context.new_page()

        try:
            page.goto(editor_url, wait_until="domcontentloaded", timeout=30_000)
        except Exception as e:
            print(f"   ⚠️ ページロードエラー（続行）: {e}")

        content_loaded = _wait_for_editor_content(page, timeout_sec=EDITOR_LOAD_TIMEOUT_SEC)
        if not content_loaded:
            print("   ❌ エディタコンテンツが表示されませんでした。OGP展開をスキップします。")
            browser.close()
            return result

        if run_ogp:
            try:
                processed_count = process_ogp_urls(page)
                result["ogp_processed_count"] = processed_count
                print(f"   ✅ OGP展開処理完了: {processed_count}件")
            except Exception as e:
                print(f"   ⚠️ OGP展開エラー: {e}")
        else:
            print("   ⏭️ OGP展開はスキップします。")

        print("   ⏳ OGP反映待機（5秒）...")
        page.wait_for_timeout(5000)

        if insert_toc:
            try:
                result["toc"] = _insert_table_of_contents(page, source_markdown=source_markdown)
            except Exception as e:
                result["toc"] = {"success": False, "strategy": f"error: {e}"}
                print(f"   ⚠️ 目次挿入エラー: {e}")
        else:
            result["toc"] = {"success": False, "strategy": "skipped_by_option"}
            print("   ⏭️ 目次挿入はオプション指定によりスキップします")

        try:
            result["body_images"] = _attach_body_images_to_page(
                page,
                body_image_uploads=body_image_uploads,
                artifacts_dir=artifacts_dir,
            )
        except Exception as e:
            result["body_images"] = {"success": False, "error": str(e)}
            print(f"   ⚠️ 本文画像添付エラー: {e}")

        if run_top_image:
            if top_image_path:
                top_image_result = _attach_local_top_image_to_page(
                    page,
                    image_path=top_image_path,
                    artifacts_dir=artifacts_dir,
                    save_draft_after_upload=not publish_after,
                )
            else:
                top_image_result = _attach_amazon_top_image_to_page(
                    page,
                    source_markdown=source_markdown,
                    artifacts_dir=artifacts_dir,
                    save_draft_after_upload=not publish_after,
                )
        else:
            top_image_result = {"image_flow": "skipped_by_option"}
            print("   ⏭️ Amazonトップ画像はオプション指定によりスキップします")
        result["top_image"] = top_image_result

        if top_image_result.get("image_flow") in {"skipped", "skipped_by_option"}:
            print("   💾 トップ画像スキップのため Ctrl+S で保存を要求します...")
            try:
                editor = page.locator(".ProseMirror, .note-editable, [contenteditable='true']").first
                editor.click()
                page.keyboard.press("Control+s")
            except Exception as e:
                print(f"   ⚠️ 保存トリガーエラー（続行）: {e}")
            page.wait_for_timeout(8000)
        else:
            print("   ⏳ トップ画像保存後の安定待機（8秒）...")
            page.wait_for_timeout(8000)

        if publish_after:
            try:
                result["publish"] = _publish_editor_page(
                    page,
                    tags=publish_tags,
                    dry_run=dry_run_publish,
                )
            except Exception as e:
                result["publish"] = {"success": False, "error": str(e), "final_url": page.url}
                print(f"   ❌ 公開投稿フロー失敗: {e}")
                browser.close()
                return result

        result["success"] = True
        browser.close()

    print("   ✅ OGP展開 + 目次 + トップ画像処理が完了しました。")
    return result


# ── セッション作成・検証・ログイン ────────────────────
def _create_session(cookies: dict) -> http_requests.Session:
    """認証済みHTTPセッションを作成"""
    session = http_requests.Session()
    session.headers.update({
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
        "Content-Type": "application/json",
        "Referer": "https://editor.note.com/",
        "Origin": "https://editor.note.com",
        "X-Requested-With": "XMLHttpRequest",
        "Sec-Fetch-Site": "same-site",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    })
    if cookies:
        session.cookies.update(cookies)
    return session


def _is_cloudfront_403_response(response) -> bool:
    try:
        status_code = int(getattr(response, "status_code", 0))
        text = (getattr(response, "text", "") or "").lower()
    except Exception:
        return False
    return status_code == 403 and (
        "the request could not be satisfied" in text
        or "cloudfront" in text
        or "<h1>403 error</h1>" in text
    )


def _is_cloudfront_403_payload(payload: dict | None) -> bool:
    payload = payload or {}
    try:
        status_code = int(payload.get("status") or 0)
        text = str(payload.get("text") or "").lower()
    except Exception:
        return False
    return status_code == 403 and (
        "the request could not be satisfied" in text
        or "cloudfront" in text
        or "<h1>403 error</h1>" in text
    )


def _post_json_with_cloudfront_retry(
    session: http_requests.Session,
    url: str,
    payload: dict,
    *,
    headers: dict | None = None,
    timeout: int = 30,
    label: str = "note API",
):
    response = None
    for attempt, delay in enumerate([0, *NOTE_CLOUDFRONT_RETRY_DELAYS], start=1):
        if delay:
            print(f"   ⏳ {label}: CloudFront 403 のため {delay}秒待って再試行します ({attempt}/{len(NOTE_CLOUDFRONT_RETRY_DELAYS) + 1})")
            time.sleep(delay)
        response = session.post(url, json=payload, headers=headers, timeout=timeout)
        if not _is_cloudfront_403_response(response):
            return response
        if attempt == len(NOTE_CLOUDFRONT_RETRY_DELAYS) + 1:
            print(f"   ❌ {label}: CloudFront 403 が継続しています")
            return response
    return response


def _verify_session(session: http_requests.Session) -> bool:
    """セッションが有効か確認（ユーザー情報取得を試行）"""
    try:
        res = session.get(f"{NOTE_API_BASE}/v1/stats/pv", timeout=15)
        if res.ok:
            print("   ✅ セッション有効（API認証成功）")
            return True
        # 別のエンドポイントでもう一度試す
        res = session.get("https://note.com/api/v1/note_sessions/me", timeout=15)
        if res.ok:
            print("   ✅ セッション有効（セッション確認成功）")
            return True
    except Exception as e:
        print(f"   ⚠️ セッション検証エラー: {e}")
    return False


def _fetch_csrf_token(session: http_requests.Session) -> str | None:
    """note.comのHTMLからCSRFトークンを取得"""
    import re as _re
    try:
        res = session.get("https://note.com/", timeout=15)
        # <meta name="csrf-token" content="...">
        m = _re.search(r'<meta[^>]+name=["\']csrf-token["\'][^>]+content=["\']([^"\']+)["\']', res.text)
        if m:
            token = m.group(1)
            print(f"   🔐 CSRFトークン取得成功")
            return token
        # <meta content="..." name="csrf-token"> （順序が逆の場合）
        m = _re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']csrf-token["\']', res.text)
        if m:
            return m.group(1)
    except Exception as e:
        print(f"   ⚠️ CSRFトークン取得失敗: {e}")
    return None


def _api_login(session: http_requests.Session) -> bool:
    """noteのAPIで直接ログインしてCookieとCSRFトークンを取得"""
    if not NOTE_EMAIL or not NOTE_PASSWORD:
        print("   ⚠️ NOTE_EMAIL/NOTE_PASSWORD未設定のためAPIログイン不可")
        return False

    print("   🔑 APIログインを試みます...")

    # 古いセッションCookieを削除（重複防止）
    remove_names = {"_note_session_v5", "_note_session"}
    cookies_to_keep = [c for c in session.cookies if c.name not in remove_names]
    session.cookies.clear()
    for c in cookies_to_keep:
        session.cookies.set(c.name, c.value, domain=c.domain, path=c.path)
    print(f"   🧹 古いセッションCookieをクリア")

    # note.comにアクセスしてベースCookieとCSRFトークンを取得
    csrf_token = _fetch_csrf_token(session)
    if csrf_token:
        session.headers.update({"X-CSRF-Token": csrf_token})
    time.sleep(1)

    # ログインAPI候補（noteのバージョンにより異なる可能性）
    login_attempts = [
        {
            "url": "https://note.com/api/v3/sessions/sign_in",
            "payload": {"login": NOTE_EMAIL, "password": NOTE_PASSWORD},
        },
        {
            "url": "https://note.com/api/v2/sessions/sign_in",
            "payload": {"login": NOTE_EMAIL, "password": NOTE_PASSWORD},
        },
        {
            "url": "https://note.com/api/v1/sessions/sign_in",
            "payload": {"login": NOTE_EMAIL, "password": NOTE_PASSWORD},
        },
        {
            "url": "https://note.com/api/v1/sessions",
            "payload": {"login": NOTE_EMAIL, "password": NOTE_PASSWORD},
        },
    ]

    for attempt in login_attempts:
        try:
            res = _post_json_with_cloudfront_retry(
                session,
                attempt["url"],
                attempt["payload"],
                timeout=15,
                label=f"APIログイン {attempt['url']}",
            )
            if res.ok:
                # レスポンスbodyにerrorが含まれていないか確認
                try:
                    body = res.json()
                    if "error" in body:
                        error = body["error"]
                        print(f"   ❌ ログインエラー: {error}")
                        if isinstance(error, dict) and error.get("code") == "required_recaptcha":
                            raise NoteLoginRequiresManualAction(error.get("message", "reCAPTCHA認証が必要です"))
                        break  # 認証情報が無効なので他を試しても無駄
                    # レスポンスにトークンが含まれる場合はCookieにセット
                    token = (body.get("data", {}) or {}).get("token") or body.get("token")
                    if token:
                        print(f"   🔑 レスポンストークン検出 → Cookieにセット")
                        session.cookies.set("_note_session_v5", token, domain=".note.com")
                except NoteLoginRequiresManualAction:
                    raise
                except Exception:
                    pass
                # ログイン後のCookie状況をデバッグ出力
                note_cookies = [c.name for c in session.cookies if "note.com" in (c.domain or "")]
                print(f"   🍪 ログイン後Cookie数: {len(list(session.cookies))} 個（note.com: {note_cookies}）")
                print(f"   ✅ APIログイン成功: {attempt['url']}")
                return True
            elif res.status_code == 401:
                print(f"   ❌ 認証拒否: {attempt['url']} (401) → {res.text[:150]}")
                break  # 認証情報が無効なので他を試しても無駄
            elif res.status_code == 404:
                continue  # エンドポイント不在 → 次を試す
            elif _is_cloudfront_403_response(res):
                print("   ⚠️ note側CloudFront 403が継続しているため、残りのログインAPI候補は試さず終了します")
                return False
            else:
                print(f"   ⚠️ {attempt['url']} → {res.status_code}: {res.text[:150]}")
        except NoteLoginRequiresManualAction:
            raise
        except Exception as e:
            print(f"   ⚠️ {attempt['url']} → エラー: {e}")
        time.sleep(1)

    return False


# ── 記事作成API ───────────────────────────────────────
import urllib.parse as _urlparse


def _xsrf_token(session: http_requests.Session) -> str:
    """Cookie から XSRF-TOKEN を取得（URLデコード済み）"""
    for cookie in session.cookies:
        if cookie.name == "XSRF-TOKEN":
            return _urlparse.unquote(cookie.value)
    return ""


def _ensure_xsrf_token(session: http_requests.Session) -> str:
    xsrf = _xsrf_token(session)
    if xsrf:
        return xsrf

    print("   🔐 XSRF-TOKEN未取得 → note.comにアクセスして取得...")
    try:
        response = session.get("https://note.com/", timeout=15)
        if _is_cloudfront_403_response(response):
            print("   ⚠️ XSRF取得の事前アクセスも CloudFront 403 です")
    except Exception as exc:
        print(f"   ⚠️ XSRF取得の事前アクセスに失敗しました: {exc}")
    return _xsrf_token(session)


def _plain_text_length_from_html(body_html: str) -> int:
    return len(re.sub(r"<[^>]+>", "", body_html))


def _session_cookie_dict(session: http_requests.Session) -> dict[str, str]:
    return {cookie.name: cookie.value for cookie in session.cookies if cookie.name and cookie.value}


def _merge_playwright_cookies_into_session(session: http_requests.Session, cookies: list[dict]) -> None:
    for cookie in cookies:
        name = cookie.get("name")
        value = cookie.get("value")
        if not name or value is None:
            continue
        session.cookies.set(
            name,
            value,
            domain=cookie.get("domain") or ".note.com",
            path=cookie.get("path") or "/",
        )


def _create_draft_browser_fallback(session: http_requests.Session, title: str, body_html: str) -> dict:
    """GitHub Actions上のAPI直叩きがCloudFrontで拒否された場合、ブラウザ文脈のfetchで下書きを作る。"""
    cookies = _session_cookie_dict(session)
    if not cookies:
        print("   ⚠️ ブラウザ経由フォールバック用Cookieがないためスキップします")
        return {}

    print("   🌐 API直叩き失敗 → Playwrightブラウザ経由で下書き作成を再試行します")
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        print(f"   ⚠️ Playwrightを読み込めないためブラウザ経由フォールバックをスキップします: {exc}")
        return {}

    plain_length = _plain_text_length_from_html(body_html)
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=UA,
            locale="ja-JP",
        )
        context.add_cookies(_cookies_to_playwright(cookies))
        page = context.new_page()

        try:
            try:
                page.goto("https://note.com/", wait_until="domcontentloaded", timeout=30_000)
                page.wait_for_timeout(1000)
            except Exception as exc:
                print(f"   ⚠️ note.com事前ロードに失敗しました（続行）: {exc}")

            result = page.evaluate(
                """
                async ({ title, bodyHtml, plainLength, retryDelays }) => {
                  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
                  const isCloudFront403 = (response) => {
                    const text = String((response && response.text) || '').toLowerCase();
                    return Number((response && response.status) || 0) === 403 && (
                      text.includes('the request could not be satisfied') ||
                      text.includes('cloudfront') ||
                      text.includes('<h1>403 error</h1>')
                    );
                  };
                  const readCookie = (name) => {
                    const pair = document.cookie
                      .split(';')
                      .map((part) => part.trim())
                      .find((part) => part.startsWith(`${name}=`));
                    if (!pair) return '';
                    try {
                      return decodeURIComponent(pair.slice(name.length + 1));
                    } catch {
                      return pair.slice(name.length + 1);
                    }
                  };
                  const postJson = async (url, payload) => {
                    const headers = {
                      'Content-Type': 'application/json',
                      'X-Requested-With': 'XMLHttpRequest',
                    };
                    const xsrf = readCookie('XSRF-TOKEN');
                    if (xsrf) headers['X-XSRF-TOKEN'] = xsrf;
                    const response = await fetch(url, {
                      method: 'POST',
                      credentials: 'include',
                      headers,
                      body: JSON.stringify(payload),
                    });
                    const text = await response.text();
                    let json = null;
                    try {
                      json = text ? JSON.parse(text) : null;
                    } catch {}
                    return {
                      ok: response.ok,
                      status: response.status,
                      text: text.slice(0, 1000),
                      json,
                    };
                  };
                  const postJsonWithRetry = async (label, url, payload) => {
                    const delays = [0, ...(retryDelays || [])];
                    let response = null;
                    for (let i = 0; i < delays.length; i += 1) {
                      const delay = Number(delays[i] || 0);
                      if (delay > 0) {
                        await sleep(delay * 1000);
                      }
                      response = await postJson(url, payload);
                      if (!isCloudFront403(response) || i === delays.length - 1) {
                        return response;
                      }
                    }
                    return response;
                  };

                  const created = await postJsonWithRetry(
                    'create',
                    'https://note.com/api/v1/text_notes',
                    { template_key: null },
                  );
                  if (!created.ok) {
                    return { ok: false, phase: 'create', response: created, href: location.href };
                  }
                  const noteData = (created.json && created.json.data) || {};
                  const articleId = noteData.id || '';
                  const articleKey = noteData.key || '';
                  if (!articleId || !articleKey) {
                    return { ok: false, phase: 'create_parse', response: created, href: location.href };
                  }

                  const payload = {
                    body: bodyHtml,
                    body_length: plainLength,
                    name: title,
                    index: false,
                    is_lead_form: false,
                    image_keys: [],
                  };
                  const saved = await postJsonWithRetry(
                    'draft_save',
                    `https://note.com/api/v1/text_notes/draft_save?id=${encodeURIComponent(articleId)}&is_temp_saved=true`,
                    payload,
                  );
                  if (!saved.ok) {
                    return {
                      ok: false,
                      phase: 'draft_save',
                      response: saved,
                      id: articleId,
                      key: articleKey,
                      href: location.href,
                    };
                  }

                  return {
                    ok: true,
                    id: articleId,
                    key: articleKey,
                    url: `https://editor.note.com/notes/${articleKey}/edit/`,
                    create_status: created.status,
                    save_status: saved.status,
                    href: location.href,
                  };
                }
                """,
                {
                    "title": title,
                    "bodyHtml": body_html,
                    "plainLength": plain_length,
                    "retryDelays": list(NOTE_CLOUDFRONT_RETRY_DELAYS),
                },
            )

            _merge_playwright_cookies_into_session(
                session,
                context.cookies(["https://note.com", "https://editor.note.com"]),
            )
        finally:
            browser.close()

    if result.get("ok"):
        print(f"   ✅ ブラウザ経由下書き作成成功: ID={result.get('id')}, key={result.get('key')}")
        return {"id": result.get("id", ""), "key": result.get("key", ""), "url": result.get("url", "")}

    response = result.get("response") or {}
    if _is_cloudfront_403_payload(response):
        print("   ⚠️ ブラウザ経由でも CloudFront 403 が継続しました。note側の一時ブロックとして扱います")
    print(
        "   ❌ ブラウザ経由下書き作成失敗: "
        f"phase={result.get('phase')} status={response.get('status')} "
        f"body={str(response.get('text') or '')[:300]}"
    )
    return {}


def _create_draft_api(session: http_requests.Session, title: str, body_html: str) -> dict:
    """
    2ステップで下書き作成:
    1. POST /api/v1/text_notes でスケルトン作成 → ID取得
    2. POST /api/v1/text_notes/draft_save?id={id}&is_temp_saved=true で本文を保存
    ※ PUT は公開用。下書き保存には draft_save エンドポイントを使う（NoteClient2準拠）
    """
    # ── Step 1: 記事スケルトン作成 ──
    print("   📝 Step1: 記事スケルトン作成...")
    xsrf = _ensure_xsrf_token(session)
    create_headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://editor.note.com",
        "Referer": "https://editor.note.com/",
        "Sec-Fetch-Site": "same-site",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    }
    if xsrf:
        create_headers["X-XSRF-TOKEN"] = xsrf
    res = _post_json_with_cloudfront_retry(
        session,
        f"{NOTE_API_BASE}/v1/text_notes",
        {"template_key": None},
        headers=create_headers,
        timeout=30,
        label="記事スケルトン作成",
    )
    print(f"   🔍 POST {res.status_code}")
    if not res.ok:
        print(f"   ❌ 記事作成失敗 ({res.status_code}): {res.text[:300]}")
        return {}

    try:
        result = res.json()
    except Exception:
        print(f"   ❌ レスポンスパース失敗: {res.text[:200]}")
        return {}

    note_data = result.get("data") or {}
    article_id = note_data.get("id")
    article_key = note_data.get("key")
    if not article_id:
        print(f"   ❌ IDが取得できません: {json.dumps(result, ensure_ascii=False)[:300]}")
        return {}
    print(f"   ✅ スケルトン作成成功: ID={article_id}, key={article_key}")

    # ── Step 2: draft_save で本文保存 ──
    print("   📝 Step2: 本文を draft_save で保存...")
    xsrf = _ensure_xsrf_token(session)

    payload = {
        "body": body_html,
        "body_length": _plain_text_length_from_html(body_html),
        "name": title,
        "index": False,
        "is_lead_form": False,
        "image_keys": [],
    }
    draft_headers = {
        "Content-Type": "application/json",
        "X-XSRF-TOKEN": xsrf,
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://editor.note.com",
        "Referer": "https://editor.note.com/",
    }
    draft_url = f"{NOTE_API_BASE}/v1/text_notes/draft_save?id={article_id}&is_temp_saved=true"
    res2 = _post_json_with_cloudfront_retry(
        session,
        draft_url,
        payload,
        headers=draft_headers,
        timeout=30,
        label="本文 draft_save",
    )
    print(f"   🔍 draft_save {res2.status_code}")
    if not res2.ok:
        print(f"   ❌ 本文保存失敗 ({res2.status_code}): {res2.text[:300]}")
        # タイトルなしでも下書き自体は作成済みなので editor URL は返す
    else:
        print(f"   ✅ 本文保存成功")

    editor_url = f"https://editor.note.com/notes/{article_key}/edit/"
    return {"id": article_id, "key": article_key, "url": editor_url}


# ── save-cookies（初回のみ） ──────────────────────────
def save_storage_state_locally():
    """
    ブラウザを開いて手動ログイン → Cookieを保存。
    初回のみ実行。以降はAPIログイン + keepaliveで自動維持。
    """
    from playwright.sync_api import sync_playwright

    print("🔑 ブラウザでnote.comにログインしてください...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=UA,
        )
        page = context.new_page()
        page.goto("https://note.com/login", wait_until="domcontentloaded")

        print("\nブラウザでnote.comへのログインを完了してください。")
        print("ログイン後、Enterを押してください: ", end="", flush=True)
        input()

        state = context.storage_state()
        state_json = json.dumps(state, ensure_ascii=False, indent=2)
        LOCAL_STATE_FILE.write_text(state_json, encoding="utf-8")
        browser.close()

    print(f"\n✅ StorageState保存完了: {LOCAL_STATE_FILE}")

    # GITHUB_TOKENがあれば即自動登録
    if GITHUB_TOKEN:
        print("\n🔄 GITHUB_TOKEN検出 → GitHub Secretを自動更新します...")
        _auto_refresh_github_secret(json.dumps(state, ensure_ascii=False))
    else:
        print(f"\n📋 以下をGitHub Secret「{NOTE_STORAGE_SECRET_NAME}」に登録してください:")
        print(state_json)


# ── keepaliveモード ───────────────────────────────────
def keepalive():
    """
    セッション維持用: Cookieでnoteにアクセスし、有効なら更新して保存。
    無効ならAPIログインで再取得。
    """
    print("🔄 セッション維持チェック...")
    cookies = _load_cookies()
    session = _create_session(cookies)

    if _verify_session(session):
        print("   セッション有効 → Cookie更新して保存")
    else:
        print("   セッション切れ → APIログインで再取得")
        try:
            login_ok = _api_login(session)
        except NoteLoginRequiresManualAction as e:
            _print_manual_cookie_refresh_steps(str(e))
            print("✅ 手動対応が必要なため、Actionsの失敗扱いにはせず終了します")
            return
        if not login_ok:
            print("❌ セッション復旧失敗")
            sys.exit(1)

    _save_cookies_state(session)
    print("✅ セッション維持完了")


# ── メイン処理 ────────────────────────────────────────
def post_draft_to_note(
    markdown: str,
    run_ogp: bool = True,
    run_top_image: bool = True,
    insert_toc: bool = True,
    publish: bool = False,
    dry_run_publish: bool = False,
    publish_tags: str = NOTE_POST_TAGS,
    top_image_path: str = "",
    body_image_uploads: list[dict] | None = None,
) -> dict:
    title, body = extract_title_and_body(markdown)
    if not title or not body:
        print("❌ タイトルまたは本文が空です")
        return {"success": False, "url": "", "title": title}

    body_html = markdown_to_note_html(body)
    print(f"📋 タイトル: 「{title}」")
    print(f"📋 本文: {len(body)} 文字 → HTML {len(body_html)} 文字")
    result = {"success": False, "url": "", "title": title}

    # Phase 1: 認証
    print("\n── Phase 1: 認証 ──")
    cookies = _load_cookies()
    session = _create_session(cookies)

    # Phase 2: 下書き作成
    print("\n── Phase 2: 下書き作成（API） ──")
    draft_strategy = "api"

    def _retry_create_draft_in_browser(label: str) -> dict:
        try:
            return _create_draft_browser_fallback(session, title, body_html)
        except Exception as exc:
            print(f"   ⚠️ ブラウザ経由下書き作成の例外でスキップします ({label}): {exc}")
            return {}

    draft = _create_draft_api(session, title, body_html)
    if not draft:
        draft = _retry_create_draft_in_browser("before_session_refresh")
        if draft:
            draft_strategy = "browser_before_session_refresh"
    if not draft:
        if not _verify_session(session):
            print("   ⚠️ 下書き作成APIが拒否されました → セッション再取得を試します")
            try:
                login_ok = _api_login(session)
            except NoteLoginRequiresManualAction as e:
                _print_manual_cookie_refresh_steps(str(e))
                return result
            if not login_ok:
                print("❌ 全ての認証手段が失敗しました")
                return result
        draft_strategy = "api_after_session_check"
        draft = _create_draft_api(session, title, body_html)
        if not draft:
            draft = _retry_create_draft_in_browser("after_session_check")
            if draft:
                draft_strategy = "browser_after_session_check"
    if not draft:
        return result

    result["success"] = True
    result["url"] = draft.get("url", "")
    result["draft_creation_strategy"] = draft_strategy

    # Phase 3: セッション更新
    print("\n── Phase 3: セッション更新 ──")
    _save_cookies_state(session)

    # Phase 4: OGP展開（Playwright）
    if result["url"]:
        # APIログイン後の最新セッションCookieをsessionオブジェクトから直接取得
        # （_load_cookies()は古い環境変数を返すため使用しない）
        session_cookies = {c.name: c.value for c in session.cookies}
        print(f"   🍪 Playwrightへ渡すCookie: {len(session_cookies)}個")
        editor_result = _run_ogp_expansion_on_draft(
            result["url"],
            session_cookies,
            headless=True,
            source_markdown=markdown,
            run_ogp=run_ogp,
            run_top_image=run_top_image,
            insert_toc=insert_toc,
            publish_after=publish,
            dry_run_publish=dry_run_publish,
            publish_tags=publish_tags,
            top_image_path=top_image_path,
            body_image_uploads=body_image_uploads,
        )
        result["editor_result"] = editor_result
        publish_result = editor_result.get("publish") or {}
        if publish:
            result["success"] = bool(publish_result.get("success"))
            if publish_result.get("final_url"):
                result["published_url"] = publish_result["final_url"]

    return result


# ── CLI ──────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="note.com 下書きポスター v4.0（API直接投稿版）")
    parser.add_argument("file", nargs="?", help="Markdownファイルパス")
    parser.add_argument("--content", help="Markdown文字列を直接指定")
    parser.add_argument("--save-cookies", action="store_true",
                        help="初回セットアップ: ブラウザで手動ログインしてCookieを保存")
    parser.add_argument("--keepalive", action="store_true",
                        help="セッション維持モード: Cookieの有効性確認・更新")
    parser.add_argument("--no-ogp", action="store_true",
                        help="OGP展開をスキップして下書き保存のみ実行")
    parser.add_argument("--no-top-image", action="store_true",
                        help="Amazonトップ画像の添付をスキップする")
    parser.add_argument("--top-image-path", default="",
                        help="Amazon検索の代わりにトップ画像へ設定するローカル画像の絶対パス")
    parser.add_argument("--no-toc", action="store_true",
                        help="目次挿入をスキップする")
    parser.add_argument("--publish", action="store_true",
                        help="下書き処理後に公開投稿まで進める")
    parser.add_argument("--dry-run-publish", action="store_true",
                        help="公開画面まで進めるが、最後の「投稿する」は押さない")
    args = parser.parse_args()

    if args.save_cookies:
        save_storage_state_locally()
        sys.exit(0)

    if args.keepalive:
        keepalive()
        sys.exit(0)

    if args.content:
        md = args.content
    elif args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            md = f.read()
    else:
        print("❌ Markdownファイルパスまたは --content を指定してください")
        print("   初回セットアップ: python prompts/05-draft-manager/note_draft_poster.py --save-cookies")
        sys.exit(1)

    result = post_draft_to_note(
        md,
        run_ogp=not args.no_ogp,
        run_top_image=not args.no_top_image,
        insert_toc=not args.no_toc,
        publish=args.publish or args.dry_run_publish,
        dry_run_publish=args.dry_run_publish,
        top_image_path=args.top_image_path,
    )
    if result["success"]:
        label = "公開投稿" if (args.publish or args.dry_run_publish) else "下書き投稿"
        print(f"\n🎉 {label}成功！\n   タイトル: {result['title']}\n   URL: {result['url']}")
        if result.get("published_url"):
            print(f"   公開後URL: {result['published_url']}")
        file_id = os.getenv("FILE_ID", "")
        if file_id:
            _save_draft_url_to_github_var(file_id, result["url"])
    else:
        print("\n❌ 下書き投稿失敗")
        sys.exit(1)

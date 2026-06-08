from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from playwright.sync_api import Page, sync_playwright


DEFAULT_LINK_URL = "https://amzn.to/4xdeq3B"
MARKER = "[[NOTE_IMAGE_LINK_LAB_MARKER]]"
JST = timezone(timedelta(hours=9), "JST")


def _configure_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _safe_name(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z_.-]+", "_", value).strip("_") or "item"


def _import_note_engine(repo_root: Path):
    os.environ.setdefault("NOTE_TOP_IMAGE_DEBUG", "0")
    os.environ["GITHUB_TOKEN"] = ""
    engine_dir = repo_root / "scripts" / "note_engine"
    sys.path.insert(0, str(engine_dir))
    import note_draft_poster as note_engine  # type: ignore

    return note_engine


def _fallback_png_bytes() -> bytes:
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
    )


def _create_generated_image(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image, ImageDraw, ImageFont

        image = Image.new("RGB", (960, 540), (245, 247, 250))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 960, 540), fill=(245, 247, 250))
        draw.rectangle((56, 56, 904, 484), outline=(35, 120, 140), width=6)
        draw.rectangle((96, 104, 864, 220), fill=(255, 255, 255), outline=(200, 210, 216), width=2)
        draw.rectangle((96, 256, 520, 436), fill=(228, 243, 239), outline=(84, 160, 142), width=3)
        draw.ellipse((600, 250, 808, 458), fill=(255, 224, 160), outline=(186, 125, 24), width=3)
        font = ImageFont.load_default()
        draw.text((126, 146), "note image link lab", fill=(20, 38, 45), font=font)
        draw.text((126, 184), "Ctrl+K / URL attach probe", fill=(59, 78, 86), font=font)
        draw.text((126, 286), "generated in Actions", fill=(35, 120, 140), font=font)
        image.save(path)
    except Exception:
        path.write_bytes(_fallback_png_bytes())
    return path


def _select_image_path(repo_root: Path, artifacts_dir: Path, requested: str = "") -> Path:
    if requested:
        requested_path = Path(requested).expanduser().resolve()
        if requested_path.exists():
            return requested_path
        raise FileNotFoundError(f"指定された画像ファイルが存在しません: {requested_path}")

    local_image = repo_root / "attach" / "スクリーンショット 2026-05-27 093128.png"
    if local_image.exists():
        return local_image.resolve()

    return _create_generated_image(artifacts_dir / "generated-note-image-link-lab.png").resolve()


def _build_markdown(title: str, link_url: str) -> str:
    return f"""# {title}

Actions 経由で note の画像リンク挙動を確認する下書きです。

{MARKER}

検証 URL: {link_url}

この下書きは C:\\Users\\mahha\\OneDrive\\開発\\notion2note\\attach\\actions_image_link_lab.py の隔離実験用です。
"""


def _create_image_draft(note_engine, image_path: Path, title: str, link_url: str) -> dict[str, Any]:
    body_image_uploads = [
        {
            "marker": MARKER,
            "path": str(image_path),
            "caption": f"image link lab: {link_url}",
        }
    ]
    return note_engine.post_draft_to_note(
        _build_markdown(title, link_url),
        run_ogp=False,
        run_top_image=False,
        insert_toc=False,
        publish=False,
        body_image_uploads=body_image_uploads,
    )


def _load_editor(page: Page, note_engine, editor_url: str) -> None:
    page.goto(editor_url, wait_until="domcontentloaded", timeout=60_000)
    if not note_engine._wait_for_editor_content(page, timeout_sec=45):
        raise RuntimeError("note エディタ本文の読み込みを確認できませんでした")
    page.wait_for_timeout(1000)


def _collect_snapshot(page: Page, link_url: str) -> dict[str, Any]:
    return page.evaluate(
        """
        (linkUrl) => {
          const visible = (el) => {
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
          };
          const textOf = (el) => (el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('title') || '').trim();
          const rectOf = (el) => {
            const rect = el.getBoundingClientRect();
            return {
              x: Math.round(rect.x),
              y: Math.round(rect.y),
              width: Math.round(rect.width),
              height: Math.round(rect.height)
            };
          };
          const describe = (el) => {
            if (!el) return null;
            const text = textOf(el);
            return {
              tag: el.tagName,
              role: el.getAttribute('role') || '',
              text: text.slice(0, 120),
              ariaLabel: el.getAttribute('aria-label') || '',
              title: el.getAttribute('title') || '',
              placeholder: el.getAttribute('placeholder') || '',
              type: el.getAttribute('type') || '',
              className: String(el.className || '').slice(0, 200),
              rect: rectOf(el),
            };
          };
          const editor = document.querySelector('.ProseMirror, [contenteditable="true"]');
          const images = Array.from(document.querySelectorAll('img'))
            .filter(visible)
            .map((img, index) => {
              const anchor = img.closest('a[href]');
              const figure = img.closest('figure, .image, [data-node-type], p, div') || img.parentElement;
              return {
                index,
                src: img.currentSrc || img.src || '',
                alt: img.alt || '',
                anchorHref: anchor ? anchor.href : '',
                rect: rectOf(img),
                outerHtml: figure ? figure.outerHTML.slice(0, 1600) : img.outerHTML.slice(0, 1600),
              };
            })
            .sort((a, b) => (b.rect.width * b.rect.height) - (a.rect.width * a.rect.height));
          const controls = Array.from(document.querySelectorAll('button, a, input, textarea, [role="button"], [role="textbox"], [contenteditable="true"]'))
            .filter(visible)
            .map(describe)
            .slice(0, 120);
          const matchingAnchors = Array.from(document.querySelectorAll('a[href]'))
            .map((a) => ({ href: a.href, text: textOf(a).slice(0, 120), html: a.outerHTML.slice(0, 1000) }))
            .filter((item) => item.href === linkUrl || item.href.includes(linkUrl));
          return {
            url: location.href,
            documentTitle: document.title,
            activeElement: describe(document.activeElement),
            editorHtmlExcerpt: editor ? editor.innerHTML.slice(0, 8000) : '',
            bodyTextExcerpt: (document.body.innerText || '').slice(0, 3000),
            imageCount: images.length,
            images: images.slice(0, 20),
            controls,
            matchingAnchors,
          };
        }
        """,
        link_url,
    )


def _verify_link(page: Page, link_url: str) -> dict[str, Any]:
    return page.evaluate(
        """
        (linkUrl) => {
          const visible = (el) => {
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
          };
          const images = Array.from(document.querySelectorAll('img')).filter(visible);
          const linkedImages = images.map((img, index) => {
            const anchor = img.closest('a[href]');
            return {
              index,
              src: img.currentSrc || img.src || '',
              href: anchor ? anchor.href : '',
              html: (anchor || img.closest('figure') || img).outerHTML.slice(0, 1400),
            };
          }).filter((item) => item.href);
          const exact = linkedImages.filter((item) => item.href === linkUrl || item.href.includes(linkUrl));
          return {
            success: exact.length > 0,
            expectedUrl: linkUrl,
            linkedImageCount: linkedImages.length,
            exactCount: exact.length,
            linkedImages,
          };
        }
        """,
        link_url,
    )


def _find_target_image_position(page: Page) -> dict[str, Any]:
    return page.evaluate(
        """
        () => {
          const visible = (el) => {
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return rect.width > 20 && rect.height > 20 && style.visibility !== 'hidden' && style.display !== 'none';
          };
          document.querySelectorAll('[data-attach-lab-target-image]').forEach((el) => el.removeAttribute('data-attach-lab-target-image'));
          const editor = document.querySelector('.ProseMirror, [contenteditable="true"]') || document.body;
          let images = Array.from(editor.querySelectorAll('img')).filter(visible);
          if (!images.length) {
            images = Array.from(document.querySelectorAll('img')).filter(visible).filter((img) => !img.closest('header, nav'));
          }
          images.sort((a, b) => {
            const ar = a.getBoundingClientRect();
            const br = b.getBoundingClientRect();
            return (br.width * br.height) - (ar.width * ar.height);
          });
          const img = images[0];
          if (!img) return { ok: false, reason: 'no_visible_body_image' };
          img.scrollIntoView({ block: 'center', inline: 'center' });
          const rect = img.getBoundingClientRect();
          img.setAttribute('data-attach-lab-target-image', 'true');
          return {
            ok: true,
            x: rect.left + rect.width / 2,
            y: rect.top + rect.height / 2,
            rect: {
              x: Math.round(rect.x),
              y: Math.round(rect.y),
              width: Math.round(rect.width),
              height: Math.round(rect.height),
            },
            src: img.currentSrc || img.src || '',
            html: (img.closest('figure') || img).outerHTML.slice(0, 1600),
          };
        }
        """
    )


def _click_target_image(page: Page, click_count: int = 1) -> dict[str, Any]:
    target = _find_target_image_position(page)
    if not target.get("ok"):
        return target
    page.mouse.click(float(target["x"]), float(target["y"]), click_count=click_count)
    page.wait_for_timeout(700)
    target["clicked"] = True
    target["clickCount"] = click_count
    return target


def _focus_image_node_by_dom_selection(page: Page) -> dict[str, Any]:
    return page.evaluate(
        """
        () => {
          const img = document.querySelector('[data-attach-lab-target-image]') || (() => {
            const editor = document.querySelector('.ProseMirror, [contenteditable="true"]') || document.body;
            return Array.from(editor.querySelectorAll('img')).find((candidate) => candidate.getBoundingClientRect().width > 20);
          })();
          if (!img) return { ok: false, reason: 'target_image_not_found' };
          const editor = img.closest('[contenteditable="true"]') || document.querySelector('.ProseMirror, [contenteditable="true"]');
          if (editor && typeof editor.focus === 'function') editor.focus();
          const range = document.createRange();
          range.selectNode(img);
          const selection = window.getSelection();
          selection.removeAllRanges();
          selection.addRange(range);
          const rect = img.getBoundingClientRect();
          return {
            ok: true,
            activeTag: document.activeElement ? document.activeElement.tagName : '',
            selectedText: selection.toString(),
            rect: {
              x: Math.round(rect.x),
              y: Math.round(rect.y),
              width: Math.round(rect.width),
              height: Math.round(rect.height),
            },
          };
        }
        """
    )


def _mark_link_input(page: Page) -> dict[str, Any]:
    return page.evaluate(
        """
        () => {
          const visible = (el) => {
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
          };
          const textOf = (el) => (el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('title') || '').trim();
          const rectOf = (el) => {
            const rect = el.getBoundingClientRect();
            return {
              x: Math.round(rect.x),
              y: Math.round(rect.y),
              width: Math.round(rect.width),
              height: Math.round(rect.height)
            };
          };
          const describe = (el, score = 0) => ({
            tag: el.tagName,
            role: el.getAttribute('role') || '',
            text: textOf(el).slice(0, 140),
            ariaLabel: el.getAttribute('aria-label') || '',
            title: el.getAttribute('title') || '',
            placeholder: el.getAttribute('placeholder') || '',
            type: el.getAttribute('type') || '',
            className: String(el.className || '').slice(0, 200),
            score,
            rect: rectOf(el),
          });
          document.querySelectorAll('[data-attach-lab-link-input]').forEach((el) => el.removeAttribute('data-attach-lab-link-input'));
          const candidates = Array.from(document.querySelectorAll('input, textarea, [role="textbox"], [contenteditable="true"]'))
            .filter(visible)
            .map((el) => {
              const haystack = [
                el.getAttribute('placeholder') || '',
                el.getAttribute('aria-label') || '',
                el.getAttribute('title') || '',
                el.getAttribute('type') || '',
                textOf(el),
                String(el.className || ''),
              ].join(' ').toLowerCase();
              let score = 0;
              if ((el.getAttribute('placeholder') || '').trim() === 'https://') score += 120;
              if (haystack.includes('https://') || haystack.includes('http://')) score += 90;
              if (haystack.includes('url')) score += 70;
              if (haystack.includes('link')) score += 70;
              if (haystack.includes('リンク')) score += 70;
              if ((el.getAttribute('type') || '').toLowerCase() === 'url') score += 80;
              if (haystack.includes('記事タイトル') || haystack.includes('タイトル')) score -= 400;
              if (el.matches('.ProseMirror, [contenteditable="true"]')) score -= 120;
              return { el, score, description: describe(el, score) };
            })
            .sort((a, b) => b.score - a.score);
          const viable = candidates.find((item) => item.score > 0);
          if (!viable) {
            return { ok: false, reason: 'link_input_not_found', candidates: candidates.map((item) => item.description).slice(0, 40) };
          }
          viable.el.setAttribute('data-attach-lab-link-input', 'true');
          return { ok: true, target: viable.description, candidates: candidates.map((item) => item.description).slice(0, 40) };
        }
        """
    )


def _click_visible_apply_button(page: Page) -> dict[str, Any]:
    return page.evaluate(
        """
        () => {
          const visible = (el) => {
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
          };
          const buttons = Array.from(document.querySelectorAll('button, [role="button"]')).filter(visible);
          const button = buttons.find((candidate) => {
            const text = (candidate.innerText || candidate.getAttribute('aria-label') || candidate.getAttribute('title') || '').trim();
            return /^(適用|Apply|OK)$/i.test(text) || text.includes('適用');
          });
          if (!button) return { ok: false, reason: 'apply_button_not_found' };
          const rect = button.getBoundingClientRect();
          button.click();
          return {
            ok: true,
            text: (button.innerText || button.getAttribute('aria-label') || button.getAttribute('title') || '').trim(),
            rect: {
              x: Math.round(rect.x),
              y: Math.round(rect.y),
              width: Math.round(rect.width),
              height: Math.round(rect.height)
            },
          };
        }
        """
    )


def _apply_link_from_visible_input(page: Page, link_url: str) -> dict[str, Any]:
    marked = _mark_link_input(page)
    result: dict[str, Any] = {"markedInput": marked}
    if not marked.get("ok"):
        return result

    locator = page.locator("[data-attach-lab-link-input='true']").first
    try:
        locator.fill(link_url, timeout=4000)
    except Exception as fill_exc:
        result["fillFallbackReason"] = str(fill_exc)
        locator.click(timeout=4000)
        page.keyboard.press("Control+A")
        page.keyboard.type(link_url)

    page.wait_for_timeout(300)
    page.keyboard.press("Enter")
    page.wait_for_timeout(900)
    result["afterEnter"] = _verify_link(page, link_url)
    if not result["afterEnter"].get("success"):
        result["applyButton"] = _click_visible_apply_button(page)
        page.wait_for_timeout(900)
        result["afterApplyButton"] = _verify_link(page, link_url)
    return result


def _click_link_control(page: Page) -> dict[str, Any]:
    return page.evaluate(
        """
        () => {
          const visible = (el) => {
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
          };
          const describe = (el) => {
            const rect = el.getBoundingClientRect();
            return {
              tag: el.tagName,
              text: (el.innerText || '').trim().slice(0, 120),
              ariaLabel: el.getAttribute('aria-label') || '',
              title: el.getAttribute('title') || '',
              className: String(el.className || '').slice(0, 200),
              rect: {
                x: Math.round(rect.x),
                y: Math.round(rect.y),
                width: Math.round(rect.width),
                height: Math.round(rect.height)
              },
            };
          };
          document.querySelectorAll('[data-attach-lab-control]').forEach((el) => el.removeAttribute('data-attach-lab-control'));
          const controls = Array.from(document.querySelectorAll('button, a, [role="button"]')).filter(visible);
          const scored = controls.map((el) => {
            const haystack = [
              el.innerText || '',
              el.getAttribute('aria-label') || '',
              el.getAttribute('title') || '',
              String(el.className || ''),
            ].join(' ').toLowerCase();
            let score = 0;
            if (haystack.includes('リンク')) score += 100;
            if (haystack.includes('link')) score += 90;
            if (haystack.includes('url')) score += 80;
            if (haystack.includes('chain')) score += 30;
            if ((el.getAttribute('href') || '').startsWith('http')) score -= 80;
            return { el, score, description: describe(el) };
          }).sort((a, b) => b.score - a.score);
          const target = scored.find((item) => item.score > 0);
          if (!target) return { ok: false, reason: 'link_control_not_found', controls: scored.map((item) => ({ ...item.description, score: item.score })).slice(0, 60) };
          target.el.setAttribute('data-attach-lab-control', 'true');
          target.el.click();
          return { ok: true, target: { ...target.description, score: target.score }, controls: scored.map((item) => ({ ...item.description, score: item.score })).slice(0, 60) };
        }
        """
    )


def _click_menu_then_link_control(page: Page) -> dict[str, Any]:
    menu = page.evaluate(
        """
        () => {
          const visible = (el) => {
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
          };
          const controls = Array.from(document.querySelectorAll('button, [role="button"]')).filter(visible);
          const menuButton = controls.find((el) => {
            const text = [el.innerText || '', el.getAttribute('aria-label') || '', el.getAttribute('title') || '', String(el.className || '')].join(' ').toLowerCase();
            return text.includes('その他') || text.includes('メニュー') || text.includes('more') || text.includes('menu') || text.includes('...');
          });
          if (!menuButton) return { ok: false, reason: 'menu_button_not_found' };
          const rect = menuButton.getBoundingClientRect();
          menuButton.click();
          return {
            ok: true,
            text: (menuButton.innerText || menuButton.getAttribute('aria-label') || menuButton.getAttribute('title') || '').trim(),
            rect: {
              x: Math.round(rect.x),
              y: Math.round(rect.y),
              width: Math.round(rect.width),
              height: Math.round(rect.height)
            },
          };
        }
        """
    )
    page.wait_for_timeout(700)
    link = _click_link_control(page)
    return {"menu": menu, "linkControl": link}


def _press_and_apply(page: Page, key: str, link_url: str) -> dict[str, Any]:
    page.keyboard.press(key)
    page.wait_for_timeout(800)
    return {
        "pressed": key,
        "snapshotAfterShortcut": _collect_snapshot(page, link_url),
        "apply": _apply_link_from_visible_input(page, link_url),
    }


def _strategy_click_control_k(page: Page, link_url: str) -> dict[str, Any]:
    click = _click_target_image(page, click_count=1)
    shortcut = _press_and_apply(page, "Control+K", link_url)
    return {"click": click, "shortcut": shortcut}


def _strategy_double_click_control_k(page: Page, link_url: str) -> dict[str, Any]:
    click = _click_target_image(page, click_count=2)
    shortcut = _press_and_apply(page, "Control+K", link_url)
    return {"click": click, "shortcut": shortcut}


def _strategy_dom_selection_control_k(page: Page, link_url: str) -> dict[str, Any]:
    click = _click_target_image(page, click_count=1)
    selection = _focus_image_node_by_dom_selection(page)
    shortcut = _press_and_apply(page, "Control+K", link_url)
    return {"click": click, "selection": selection, "shortcut": shortcut}


def _strategy_click_meta_k(page: Page, link_url: str) -> dict[str, Any]:
    click = _click_target_image(page, click_count=1)
    shortcut = _press_and_apply(page, "Meta+K", link_url)
    return {"click": click, "shortcut": shortcut}


def _strategy_toolbar_link_control(page: Page, link_url: str) -> dict[str, Any]:
    click = _click_target_image(page, click_count=1)
    control = _click_link_control(page)
    page.wait_for_timeout(800)
    apply = _apply_link_from_visible_input(page, link_url)
    return {"click": click, "linkControl": control, "apply": apply}


def _strategy_menu_link_control(page: Page, link_url: str) -> dict[str, Any]:
    click = _click_target_image(page, click_count=1)
    control = _click_menu_then_link_control(page)
    page.wait_for_timeout(800)
    apply = _apply_link_from_visible_input(page, link_url)
    return {"click": click, "menuLinkControl": control, "apply": apply}


def _run_strategy(
    page: Page,
    note_engine,
    editor_url: str,
    link_url: str,
    name: str,
    action: Callable[[Page, str], dict[str, Any]],
    artifacts_dir: Path,
    index: int,
) -> dict[str, Any]:
    attempt: dict[str, Any] = {
        "name": name,
        "editorUrl": editor_url,
        "startedAt": datetime.now(JST).isoformat(),
    }
    _load_editor(page, note_engine, editor_url)
    attempt["before"] = _collect_snapshot(page, link_url)
    try:
        attempt["steps"] = action(page, link_url)
        page.wait_for_timeout(1200)
        attempt["afterInteraction"] = _collect_snapshot(page, link_url)
        attempt["verifyBeforeSave"] = _verify_link(page, link_url)
        if attempt["verifyBeforeSave"].get("success"):
            try:
                attempt["saveResult"] = note_engine._save_editor_draft(page)
            except Exception as save_exc:
                attempt["saveError"] = str(save_exc)
            page.wait_for_timeout(2500)
            page.reload(wait_until="domcontentloaded", timeout=60_000)
            if note_engine._wait_for_editor_content(page, timeout_sec=45):
                page.wait_for_timeout(1200)
                attempt["verifyAfterReload"] = _verify_link(page, link_url)
                attempt["success"] = bool(attempt["verifyAfterReload"].get("success"))
            else:
                attempt["success"] = False
                attempt["reloadError"] = "editor_content_not_loaded_after_save"
        else:
            attempt["success"] = False
    except Exception as exc:
        attempt["success"] = False
        attempt["error"] = str(exc)
    finally:
        attempt["endedAt"] = datetime.now(JST).isoformat()
        _write_json(artifacts_dir / f"strategy_{index:02d}_{_safe_name(name)}.json", attempt)
    return attempt


def run_lab(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = _repo_root()
    artifacts_dir = Path(args.artifacts_dir).expanduser().resolve()
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    note_engine = _import_note_engine(repo_root)
    image_path = _select_image_path(repo_root, artifacts_dir, args.image_path)
    title = args.note_title or f"note画像リンクActions実験 {datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')}"

    result: dict[str, Any] = {
        "success": False,
        "repoRoot": str(repo_root),
        "artifactsDir": str(artifacts_dir),
        "imagePath": str(image_path),
        "linkUrl": args.link_url,
        "title": title,
        "createdAt": datetime.now(JST).isoformat(),
        "draft": {},
        "strategies": [],
    }

    if args.reuse_draft_url:
        editor_url = args.reuse_draft_url
        result["draft"] = {"success": True, "url": editor_url, "strategy": "reuse_draft_url"}
    else:
        draft = _create_image_draft(note_engine, image_path, title, args.link_url)
        result["draft"] = draft
        editor_url = draft.get("url") or ""
        if not draft.get("success") or not editor_url:
            result["error"] = "draft_creation_failed"
            _write_json(artifacts_dir / "actions_image_link_lab_result.json", result)
            return result

    cookies = note_engine._load_cookies()
    playwright_cookies = note_engine._cookies_to_playwright(cookies)
    strategies: list[tuple[str, Callable[[Page, str], dict[str, Any]]]] = [
        ("image_click_control_k", _strategy_click_control_k),
        ("image_double_click_control_k", _strategy_double_click_control_k),
        ("image_dom_selection_control_k", _strategy_dom_selection_control_k),
        ("image_click_meta_k", _strategy_click_meta_k),
        ("image_toolbar_link_control", _strategy_toolbar_link_control),
        ("image_menu_link_control", _strategy_menu_link_control),
    ]

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=not args.headed,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=note_engine.UA,
            locale="ja-JP",
        )
        context.add_cookies(playwright_cookies)

        for index, (name, action) in enumerate(strategies, start=1):
            page = context.new_page()
            attempt = _run_strategy(page, note_engine, editor_url, args.link_url, name, action, artifacts_dir, index)
            result["strategies"].append(
                {
                    "name": attempt.get("name"),
                    "success": attempt.get("success"),
                    "error": attempt.get("error"),
                    "artifact": str((artifacts_dir / f"strategy_{index:02d}_{_safe_name(name)}.json").resolve()),
                }
            )
            try:
                page.close()
            except Exception:
                pass
            if attempt.get("success"):
                result["success"] = True
                result["successfulStrategy"] = name
                break

        browser.close()

    result["endedAt"] = datetime.now(JST).isoformat()
    _write_json(artifacts_dir / "actions_image_link_lab_result.json", result)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="note 下書き画像へ Ctrl+K 系のリンク添付を試す隔離実験")
    parser.add_argument("--link-url", default=DEFAULT_LINK_URL, help="画像に添付する URL")
    parser.add_argument("--artifacts-dir", default=str(_repo_root() / "attach" / "artifacts" / "actions_image_link_lab"))
    parser.add_argument("--image-path", default="", help="検証に使う画像ファイルの絶対パス")
    parser.add_argument("--note-title", default="", help="作成する note 下書きのタイトル")
    parser.add_argument("--reuse-draft-url", default="", help="既存の note エディタ URL を再利用する場合に指定")
    parser.add_argument("--headed", action="store_true", help="ローカル検証時にブラウザを表示する")
    parser.add_argument("--fail-on-miss", action="store_true", help="リンク添付に失敗した場合に終了コード 1 を返す")
    return parser.parse_args()


def main() -> int:
    _configure_stdio()
    args = parse_args()
    started = time.monotonic()
    result = run_lab(args)
    summary = {
        "success": result.get("success"),
        "successfulStrategy": result.get("successfulStrategy", ""),
        "draftUrl": (result.get("draft") or {}).get("url", ""),
        "resultJson": str(Path(result["artifactsDir"]) / "actions_image_link_lab_result.json"),
        "elapsedSeconds": round(time.monotonic() - started, 1),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.fail_on_miss and not result.get("success"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

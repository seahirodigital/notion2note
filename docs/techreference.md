# notion2note 技術リファレンス

この文書は、`C:\Users\mahha\OneDrive\開発\notion2note` の note 投稿パイプラインについて、2026年6月5日時点の試行錯誤、成功している手法、失敗した手法、次に引き継ぐAIが見るべき参照先をまとめる。

目的は、次のAIが同じ原因調査を繰り返さず、`C:\Users\mahha\OneDrive\開発\Blog_Vercel` で実績のある投稿方式へ寄せながら、短時間で原因を切り分けられるようにすることである。

## 現在の全体像

`C:\Users\mahha\OneDrive\開発\notion2note` は、Chrome拡張から GitHub Actions の `workflow_dispatch` を新規起動し、Notionページを取得して note.com へ下書きまたは公開投稿する。

主な入口は次の通り。

- Chrome拡張: `C:\Users\mahha\OneDrive\開発\notion2note\chrome_extension\popup.js`
- GitHub Actions: `C:\Users\mahha\OneDrive\開発\notion2note\.github\workflows\notion-note-post.yml`
- Notion取得と記事整形: `C:\Users\mahha\OneDrive\開発\notion2note\scripts\notion_note\post_from_notion.py`
- note投稿エンジン: `C:\Users\mahha\OneDrive\開発\notion2note\scripts\note_engine\note_draft_poster.py`
- 公開投稿ラッパー: `C:\Users\mahha\OneDrive\開発\notion2note\scripts\note_post\note_post_publisher.py`
- タグファイル: `C:\Users\mahha\OneDrive\開発\notion2note\tag.md`
- アフィリエイト文面: `C:\Users\mahha\OneDrive\開発\notion2note\affiliate_links.txt`

Chrome拡張からボタンを押すことは正しい。これは GitHub Actions を新しく起動する `workflow_dispatch` である。避けるべきなのは、GitHub Actions の過去Run画面にある `Re-run jobs` または `Re-run failed jobs` である。過去Runの再実行は古いコミットのまま動く可能性があり、修正済みの `main` 最新を使わないことがある。

## Blog Vercel で寄せるべき参照先

`C:\Users\mahha\OneDrive\開発\Blog_Vercel` は、実際に note 投稿が通っている参照元である。notion2note 側で迷った場合は、以下を優先して見る。

- 成功実装の本体: `C:\Users\mahha\OneDrive\開発\Blog_Vercel\scripts\pipeline\prompts\05-draft-manager\note_draft_poster.py`
- 公開投稿ラッパー: `C:\Users\mahha\OneDrive\開発\Blog_Vercel\scripts\pipeline\prompts\06-note-post\note_post_publisher.py`
- info_viewer 投稿: `C:\Users\mahha\OneDrive\開発\Blog_Vercel\info_viewer\note_upload\info_viewer_note_publisher.py`
- 公開投稿 workflow: `C:\Users\mahha\OneDrive\開発\Blog_Vercel\.github\workflows\note-post.yml`
- 技術ノウハウ集: `C:\Users\mahha\OneDrive\開発\Blog_Vercel\docs\techrefere.md`
- Blog Vercel README: `C:\Users\mahha\OneDrive\開発\Blog_Vercel\docs\README.md`

Blog Vercel の重要な成功実績は GitHub Actions run `26946510710` である。このRunでは、公開設定画面でタグ98件、マガジン追加、投稿ボタンの二段クリックが通り、公開URL `https://note.com/startupm/n/n369b7f414df6?app_launch=false` が得られている。

## 正しく動作している基本手法

note投稿の正攻法は、APIで下書きスケルトンを作成し、`draft_save` で本文を保存し、その後 Playwright でエディタを開いてOGP、本文画像、トップ画像、公開設定を行う方式である。

下書き作成は次の2段階が基本である。

1. `POST https://note.com/api/v1/text_notes`
2. `POST https://note.com/api/v1/text_notes/draft_save?id={note_id}&is_temp_saved=true`

本文保存では、`X-XSRF-TOKEN`、`Origin: https://editor.note.com`、`Referer: https://editor.note.com/` が重要である。Blog Vercel の `C:\Users\mahha\OneDrive\開発\Blog_Vercel\docs\README.md` でも、本文保存ではこのヘッダーが必須と記録されている。

`PUT https://note.com/api/v1/text_notes/{id}` は下書き保存ではない。Blog Vercel の `C:\Users\mahha\OneDrive\開発\Blog_Vercel\docs\techrefere.md` にも、PUTは公開用エンドポイントであり、下書き保存には使えないと記録されている。

## notion2note の現行投稿フロー

2026年6月5日時点の `C:\Users\mahha\OneDrive\開発\notion2note\scripts\note_engine\note_draft_poster.py` は、以下の流れで動く。

1. `NOTE_STORAGE_STATE` から note Cookie を読み込む。
2. `requests.Session` を作成し、note API用Cookieとヘッダーを設定する。
3. `POST /api/v1/text_notes` でスケルトンを作成する。
4. `POST /api/v1/text_notes/draft_save?id={id}&is_temp_saved=true` で本文HTMLを保存する。
5. `https://editor.note.com/notes/{key}/edit/` を Playwright で開く。
6. エディタ本文を検出する。
7. OGP展開を行う。
8. 目次挿入を試みる。
9. Notion本文画像をnote画像ブロックとして添付する。
10. トップ画像を設定する。
11. 公開投稿の場合は `公開に進む` をクリックする。
12. タグを入力する。
13. マガジンタブで `投資Youtube学習記録` を追加する。
14. `投稿する` をクリックし、必要なら追加の `投稿する` を force click する。
15. 公開URLを確認し、Discord通知へ進む。

## うまくいったこと

### Notion取得と記事整形

Notionページからタイトル、YouTube URL、画像、本文を取得し、note向けMarkdownへ整形する部分は動作している。失敗ログでも、タイトル、本文文字数、画像数、本文画像7件などは取得できていた。

### 下書き作成と本文保存

成功Runでは、`POST /api/v1/text_notes` が `201` を返し、記事IDとkeyが得られた。その後 `draft_save` も `201` で成功している。これは Blog Vercel と同じ基本方式である。

### OGP展開

OGP展開は複数回スイープする方式で動作している。失敗Runでも `OGP展開処理完了: 7件` まで進んでいた。これは投稿失敗の主因ではない。

### 目次挿入

目次挿入は `slash_popup_click:button:` で確認できないことがある。ただし Blog Vercel の成功Run `26946510710` でも、同じく目次挿入確認に失敗して公開投稿は成功している。よって、現時点では目次挿入失敗を本番投稿失敗の主因と見なさない。

### 本文画像とトップ画像

Notion本文画像7件の添付と、トップ画像設定は成功している。失敗ログでも、本文画像添付 `7/7`、トップ画像モーダル保存、保存後待機までは通っていた。

### 公開設定画面への遷移

`公開に進む` クリック後、公開設定画面の検出は成功している。失敗Runでも `公開設定画面を検出: final_post_button` まで到達した。

### 投稿ボタンのクリック手順

Blog Vercel 成功Run `26946510710` では、最終投稿で次の二段クリックが使われていた。

- `role_button_投稿する#0 (通常click)`
- `button_text_投稿する#0 (force click)`

notion2note 側もこの手順へ寄せた。これにより「投稿ボタンが押せていない」問題は一度改善し、失敗原因はクリックではなく、その後の note API `422` へ移った。

### タグ98件制限

Blog Vercel 成功Runではタグ入力が98件だった。一方で notion2note の失敗Runでは `117件確認` の後に公開APIが `422` を返した。

このため、notion2note は `C:\Users\mahha\OneDrive\開発\notion2note\tag.md` のタグを、先頭から合計98件だけ使うようにした。`tag.md` 自体は117件でもよい。投稿時だけ先頭98件へ制限する。

対象実装は次の3箇所である。

- `C:\Users\mahha\OneDrive\開発\notion2note\scripts\notion_note\post_from_notion.py`
- `C:\Users\mahha\OneDrive\開発\notion2note\scripts\note_post\note_post_publisher.py`
- `C:\Users\mahha\OneDrive\開発\notion2note\scripts\note_engine\note_draft_poster.py`

実関数確認では、`tag.md` のユニーク117件に対して投稿使用98件、先頭タグ `投資初心者`、98件目 `時間` だった。

## うまくいっていないこと

### 2026年6月5日の公開投稿失敗 1: 公開API 422

Run `26996757547` では、下書き作成、本文保存、OGP、本文画像、トップ画像、公開設定画面、タグ、マガジン、投稿ボタン二段クリックまで進んだ。

失敗内容は次の通り。

- `PUT https://note.com/api/v1/text_notes/163735322`
- status `422`
- 同じPUTが2回失敗
- note keyは `n1ae1f95cab2d`

このRunでは、タグが `117件確認` だった。Blog Vercel 成功Runの98件との差分から、タグ過多が422の主因候補として高いと判断し、98件制限を入れた。

### 2026年6月5日の公開投稿失敗 2: CloudFront 403

Run `26997374748` では、投稿開始直後に下書きスケルトン作成の `POST /api/v1/text_notes` が `403` になった。

ログには CloudFront のHTMLエラーが返っていた。

- `403 ERROR`
- `The request could not be satisfied`

この時点では noteへ投稿ができていない。タグやマガジンや投稿ボタン以前の問題である。

最初の対処として、CloudFront 403時に `45秒、90秒、180秒` 待って再試行する実装を入れた。しかしこれはユーザー要件に合わなかった。実際に Run `26997822122` は15分以上動き続け、投稿されなかったためキャンセルした。

結論: 長時間リトライは採用しない。note投稿は2分から4分程度で成功している実績があるため、10分を超える時点でNGとして扱う。

### 過去Runの rerun 問題

GitHub Actions の過去Run画面から `Re-run jobs` または `Re-run failed jobs` を押すと、古いコミットで再実行される可能性がある。

Chrome拡張から投稿ボタンを押すことは問題ない。Chrome拡張はGitHub Actionsを新規に `workflow_dispatch` するため、通常は `main` 最新のコミットで動く。

避けるべきことは、過去Runの再実行である。

## 現在の未検証修正

2026年6月5日時点で、CloudFront 403対応として最新コミット `c50c2c6 Use editor headers for note draft creation` を入れている。

この修正の狙いは、長時間待つのではなく、最初から `editor.note.com` 由来のAPIリクエストに近いヘッダーとXSRFを付けてスケルトン作成することである。

主な変更点は次の通り。

- `NOTE_CLOUDFRONT_RETRY_DELAYS` のデフォルトを空にし、長時間待機をやめた。
- `requests.Session` の標準ヘッダーを `Origin: https://editor.note.com`、`Referer: https://editor.note.com/` に寄せた。
- `Accept-Language`、`Sec-Fetch-*` を追加した。
- スケルトン作成前に `_ensure_xsrf_token()` で `XSRF-TOKEN` を確保する。
- `POST /api/v1/text_notes` にも `X-XSRF-TOKEN` を付けられる場合は付ける。
- `C:\Users\mahha\OneDrive\開発\notion2note\.github\workflows\notion-note-post.yml` の `Notion記事をnoteへ投稿` ステップに `timeout-minutes: 8` を設定した。

この修正は、まだ本番投稿成功まで確認できていない。次のAIは、Chrome拡張から新規workflow_dispatchで起動されたRunが `c50c2c6` 以降のコミットで動いているかを最初に確認すること。

## 現在の判断基準

投稿成功の正常目安は、Blog Vercel と notion2note の成功Runから見て、おおむね2分から4分である。

次の状態は異常と判断する。

- `Notion記事をnoteへ投稿` ステップが8分以上続く。
- `POST /api/v1/text_notes` が CloudFront 403 を返す。
- タグが98件を超えて入力される。
- `PUT /api/v1/text_notes/{id}` が422を返す。
- 投稿後URLが `https://editor.note.com/notes/.../edit/` または `https://editor.note.com/notes/.../publish/` のままで、公開URL `https://note.com/.../n...` にならない。

## 次に調査するときの最短手順

1. 最新Runを確認する。

```powershell
gh run list --repo seahirodigital/notion2note --workflow notion-note-post.yml --limit 10
```

2. 対象RunのコミットSHAを確認する。

```powershell
gh run view <run_id> --repo seahirodigital/notion2note --json headSha,status,conclusion,url,jobs
```

3. 失敗ログを確認する。

```powershell
gh run view <run_id> --repo seahirodigital/notion2note --log
```

4. 実行中で通常ログが取れない場合は、ジョブIDから生ログ取得を試す。

```powershell
gh api /repos/seahirodigital/notion2note/actions/jobs/<job_id>/logs
```

5. `headSha` が最新 `main` と違う場合は、過去Runのrerunである可能性がある。Chrome拡張またはGitHub Actionsの `Run workflow` から新規起動し直す。

## 失敗パターン別の見方

### `POST /api/v1/text_notes` が403

note投稿はまだ始まっていない。CloudFrontやnote側のアクセス制御で弾かれている。タグ、マガジン、投稿ボタンは無関係。

長時間待機は避ける。8分以内に失敗させ、レスポンス本文とヘッダー差分を見る。

Blog Vercel 側が同時期に成功しているかを確認する。

```powershell
gh run list --repo seahirodigital/Blog_Vercel --workflow note-post.yml --limit 5
```

### `draft_save` が失敗

本文保存の問題である。`X-XSRF-TOKEN`、`Origin: https://editor.note.com`、`Referer: https://editor.note.com/` を確認する。

PUTに逃げてはいけない。PUTは下書き保存ではない。

### エディタ本文が読み込めない

API保存はできているが、Playwrightで開いたエディタに本文が出ていない状態である。Blog Vercel の `C:\Users\mahha\OneDrive\開発\Blog_Vercel\docs\techrefere.md` にも、エディタDOMが空の状態で `innerHTML` を取得して再保存すると本文が消える失敗が記録されている。

エディタDOMを正としすぎない。保存済み本文はサーバー側にある。

### 目次挿入に失敗

現時点では致命扱いしない。Blog Vercel の成功Runでも目次挿入確認に失敗して公開できている。

### ハッシュタグ入力が117件になる

NG。Blog Vercel 成功実績に合わせ、投稿時は先頭98件だけ使う。

### マガジン追加に失敗

公開設定画面の描画待ち不足か、マガジン名の不一致を疑う。notion2note の対象マガジンは `投資Youtube学習記録`。Blog Vercel の古い本体では `ガジェットマガジン` 表記が残っているため、そのままコピーしない。

### 投稿ボタン後に422

クリックはできている。原因は公開APIのバリデーションである可能性が高い。タグ数、画像ブロック、本文HTML、公開設定画面の状態を確認する。

公開レスポンスの失敗時には `body_preview` を記録するようにしているため、次回以降はそこを読む。

## 変更履歴メモ

- `1e80865 Align note publish button clicks`
  - Blog Vercel成功Runと同じく、最終投稿ボタンの2回目クリックを `button_text_投稿する#0 (force click)` 優先にした。
- `bfd8a8e Limit note publish tags to 98`
  - `tag.md` の先頭98件だけを投稿に使うようにした。
- `5ffa268 Retry note draft creation on CloudFront 403`
  - CloudFront 403に長時間リトライを入れたが、10分以上投稿されないため方針としてNG。
- `c50c2c6 Use editor headers for note draft creation`
  - 長時間リトライを撤去し、エディタ由来ヘッダーとXSRFを使う方向へ変更。ステップ上限8分を追加。未検証。

## 次のAIへの注意

このプロジェクトでは、`C:\Users\mahha\OneDrive\開発\Blog_Vercel` は参照元であり、原則として書き換えない。

note投稿ができていないときは、公開判定やDiscord通知より前に、まず `POST /api/v1/text_notes` と `draft_save` が成功しているかを見る。

ユーザーが「投稿できていない」と言っている場合、公開URL判定の問題ではなく、note側に記事が作られていない可能性を最優先で疑う。

長時間リトライは避ける。note投稿は成功するときは数分で完了する。10分以上かける設計はユーザー要件に反する。


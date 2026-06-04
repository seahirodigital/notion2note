# Notion2Note 設定手順

## 目的

`C:\Users\mahha\OneDrive\開発\notion2note` は、Notionの記事ページを起点にして、Chrome拡張機能から GitHub Actions を起動し、note.com へ下書き投稿または公開投稿するためのプロジェクトです。

既存の `C:\Users\mahha\OneDrive\開発\Blog_Vercel` にある実績済みの note 投稿処理を流用し、Notion取得と投稿前整形だけを `C:\Users\mahha\OneDrive\開発\notion2note` 側で管理します。

完了後のベネフィットは、Notionページを開いた状態でChrome拡張から起動するだけで、次の処理をまとめて実行できることです。

- Notionページ本文の取得
- YouTube URLの文頭配置
- 「文字起こし」H2以降の除外
- Notion画像の配置整理
- アフィリエイトリンクのH2章末挿入
- OGP展開
- 目次挿入
- noteトップ画像設定
- `tag.md` による投稿タグ設定

## 重要な注意

`C:\Users\mahha\OneDrive\開発\Blog_Vercel` は参照元です。読み取りはできますが、このプロジェクトの作業では絶対に書き込みしません。

設定・編集するファイルは、原則として `C:\Users\mahha\OneDrive\開発\notion2note` 配下だけです。

## ファイル構成

主要ファイルは次の通りです。

- `C:\Users\mahha\OneDrive\開発\notion2note\scripts\notion_note\post_from_notion.py`
  - Notionページを取得し、投稿用Markdownに整形して、note投稿エンジンへ渡します。
- `C:\Users\mahha\OneDrive\開発\notion2note\scripts\note_engine\note_draft_poster.py`
  - 既存のnote投稿エンジンを流用したファイルです。下書き作成、OGP展開、目次挿入、トップ画像設定、公開投稿を担当します。
- `C:\Users\mahha\OneDrive\開発\notion2note\.github\workflows\notion-note-post.yml`
  - GitHub Actions から Notion記事投稿を実行するworkflowです。
- `C:\Users\mahha\OneDrive\開発\notion2note\chrome_extension`
  - Chrome拡張機能です。開いているNotionページURLをGitHub Actionsへ渡します。
- `C:\Users\mahha\OneDrive\開発\notion2note\affiliate_links.txt`
  - 投稿本文へ挿入するアフィリエイト文面です。
- `C:\Users\mahha\OneDrive\開発\notion2note\tag.md`
  - note公開投稿時に設定するタグです。

## GitHub Secrets 設定

GitHubリポジトリ `seahirodigital/notion2note` の `Settings` → `Secrets and variables` → `Actions` → `Secrets` に、以下を設定します。

### 必須

- `NOTION_API_KEY`
  - Notion Integration のシークレットです。取得方法は後述の `NOTION_API_KEY の取得方法` を参照してください。
  - Notion記事ページまたは親DBに、このIntegrationのアクセス権を付与してください。
- `NOTE_EMAIL`
  - note.com のログインメールアドレスです。取得するキーではなく、普段note.comへログインしているメールアドレスをそのまま登録します。
- `NOTE_PASSWORD`
  - note.com のログインパスワードです。取得するキーではなく、普段note.comへログインしているパスワードをそのまま登録します。
- `NOTE_STORAGE_STATE`
  - noteログイン済みCookieのStorage State JSONです。取得方法は後述の `NOTE_STORAGE_STATE の取得方法` を参照してください。
- `GH_PAT`
  - GitHub Actions workflow起動、Secret更新、Variable保存に使うGitHub Personal Access Tokenです。取得方法は後述の `GH_PAT の取得方法` を参照してください。

### NOTION_API_KEY の取得方法

`NOTION_API_KEY` は、Notion APIで記事ページを読むための鍵です。これがないと、GitHub ActionsからNotion本文を取得できません。

取得手順:

1. ブラウザで `https://www.notion.so/profile/integrations` を開きます。
2. `New integration` を押します。
3. Integration名を入力します。例: `notion2note`
4. Workspaceを、投稿元NotionページがあるWorkspaceにします。
5. Capabilityは最低限、コンテンツ読み取りができる設定にします。
6. 作成後に表示される `Internal Integration Secret` をコピーします。
7. GitHubリポジトリ `seahirodigital/notion2note` の `Settings` → `Secrets and variables` → `Actions` → `Secrets` を開きます。
8. `New repository secret` を押します。
9. Nameに `NOTION_API_KEY`、Secretにコピーした `Internal Integration Secret` を貼り付けます。
10. `Add secret` を押します。

Notion側のアクセス許可:

1. 投稿対象のNotionページ、または親データベースを開きます。
2. 右上の `...` または共有メニューを開きます。
3. `接続` または `Connections` から、作成したIntegration `notion2note` を追加します。
4. 親データベースに追加した場合、そのデータベース配下の記事ページをAPIから読めるようになります。

### GH_PAT の取得方法

`GH_PAT` は、Chrome拡張や外部処理からGitHub Actionsを起動するためのGitHub Personal Access Tokenです。これがないと、Notionページを開いた状態からworkflowを起動できません。

取得手順:

1. GitHubで `https://github.com/settings/tokens` を開きます。
2. `Generate new token` を押します。
3. 迷った場合は `Generate new token (classic)` を選びます。
4. Noteに用途を書きます。例: `notion2note workflow dispatch`
5. Expirationを設定します。運用開始時は短めにして動作確認し、安定後に延長すると安全です。
6. Scopeは、対象リポジトリのActionsを起動できる権限を付けます。
7. Classic tokenの場合は、`repo` にチェックを入れます。
8. Fine-grained tokenの場合は、Repository accessで `seahirodigital/notion2note` を選び、Permissionsで `Actions: Read and write` と `Contents: Read-only` を付けます。
9. `Generate token` を押します。
10. 表示されたTokenをその場でコピーします。あとから再表示できません。
11. GitHubリポジトリ `seahirodigital/notion2note` の `Settings` → `Secrets and variables` → `Actions` → `Secrets` を開きます。
12. `New repository secret` を押します。
13. Nameに `GH_PAT`、SecretにコピーしたTokenを貼り付けます。
14. `Add secret` を押します。

Chrome拡張の `GitHub Token` 欄にも、この `GH_PAT` と同じ権限を持つTokenを入れます。GitHub Secretsの `GH_PAT` はActions内で使う保管用、Chrome拡張の `GitHub Token` はブラウザからworkflowを起動するための入力用です。

### NOTE_EMAIL と NOTE_PASSWORD の設定方法

`NOTE_EMAIL` と `NOTE_PASSWORD` は、note.comへログインするための認証情報です。APIキーではないため、note.comの設定画面から発行するものではありません。

設定手順:

1. note.comへ普段ログインしているメールアドレスを確認します。
2. GitHubリポジトリ `seahirodigital/notion2note` の `Settings` → `Secrets and variables` → `Actions` → `Secrets` を開きます。
3. `New repository secret` を押します。
4. Nameに `NOTE_EMAIL`、Secretにnote.comのログインメールアドレスを貼り付けます。
5. もう一度 `New repository secret` を押します。
6. Nameに `NOTE_PASSWORD`、Secretにnote.comのログインパスワードを貼り付けます。

note.com側で二段階認証、reCAPTCHA、追加確認が出る場合、GitHub Actionsの無人ブラウザではログインに失敗することがあります。その場合は、次の `NOTE_STORAGE_STATE` を作り直してCookieログインを優先します。

### NOTE_STORAGE_STATE の取得方法

`NOTE_STORAGE_STATE` は、note.comにログイン済みのブラウザ状態をJSON化したものです。毎回メールアドレスとパスワードでログインするより安定しやすく、reCAPTCHA回避にも役立ちます。

取得手順:

1. ローカルでPowerShellを開きます。
2. 次を実行します。

```powershell
cd C:\Users\mahha\OneDrive\開発\notion2note
python scripts\note_engine\note_draft_poster.py --save-cookies
```

3. ブラウザが開いたらnote.comへログインします。
4. ログイン完了後、ターミナルに戻ってスクリプトの指示に従います。
5. 生成または表示されたStorage State JSONをコピーします。
6. GitHubリポジトリ `seahirodigital/notion2note` の `Settings` → `Secrets and variables` → `Actions` → `Secrets` を開きます。
7. `New repository secret` を押します。
8. Nameに `NOTE_STORAGE_STATE`、SecretにStorage State JSON全体を貼り付けます。
9. `Add secret` を押します。

Storage State JSONはCookieを含むため、パスワードに近い機密情報として扱います。チャット、公開リポジトリ、READMEには貼り付けないでください。

### note投稿先を分ける場合

workflowの `note_target` で `xpost_tech` や `info_viewer` を使う場合は、追加で以下を設定します。

- `NOTE_EMAIL_XPOST_TECH`
- `NOTE_PASSWORD_XPOST_TECH`
- `NOTE_STORAGE_STATE_XPOST_TECH`
- `NOTE_EMAIL_INFO_VIEWER`
- `NOTE_PASSWORD_INFO_VIEWER`
- `NOTE_STORAGE_STATE_INFO_VIEWER`

通常は `note_target=blog_main` のままでよいので、まずは `NOTE_EMAIL`、`NOTE_PASSWORD`、`NOTE_STORAGE_STATE` を設定してください。

投稿先を分ける場合も取得方法は同じです。別のnoteアカウントでログインして `C:\Users\mahha\OneDrive\開発\notion2note\scripts\note_engine\note_draft_poster.py --save-cookies` を実行し、そのアカウント用のStorage State JSONを `NOTE_STORAGE_STATE_XPOST_TECH` または `NOTE_STORAGE_STATE_INFO_VIEWER` に登録します。

## GitHub Variables 設定

必要に応じて、GitHubリポジトリ `seahirodigital/notion2note` の `Settings` → `Secrets and variables` → `Actions` → `Variables` に以下を設定します。

- `GITHUB_REPOSITORY`
  - 通常はGitHub Actions側で自動設定されるため不要です。
  - 手動指定する場合は `seahirodigital/notion2note` にします。

## Notion 側の設定

1. NotionでIntegrationを作成します。
2. Integration Secretを `NOTION_API_KEY` としてGitHub Secretsへ登録します。
3. 投稿対象のNotionページ、またはその親データベースで、Integrationにアクセス権を付与します。
4. ページ内に画像ブロックがある場合、Notion APIから取得できる画像URLが使われます。
5. YouTube URLは、ページのプロパティに含まれるURLまたはテキストから自動検出されます。

YouTube URLのプロパティ名は、次のような名前が検出されやすいです。

- `YouTube`
- `YouTube URL`
- `Youtube URL`
- `動画URL`
- `URL`

## note Storage State の作成

初回だけ、noteへ手動ログインしてStorage Stateを作成します。

ローカルで次を実行します。

```powershell
cd C:\Users\mahha\OneDrive\開発\notion2note
python scripts\note_engine\note_draft_poster.py --save-cookies
```

ブラウザが開いたらnote.comへログインし、完了後にコンソールの指示に従います。

出力されたJSONをGitHub Secret `NOTE_STORAGE_STATE` に登録します。

## アフィリエイト文面の設定

アフィリエイト文面は次のファイルで管理します。

```text
C:\Users\mahha\OneDrive\開発\notion2note\affiliate_links.txt
```

既存の文面をそのままコピー済みです。あとから中身だけ書き換えれば、投稿時の挿入内容を変更できます。

形式は次のように `===MEMO1===`、`===MEMO2===` で分けます。

```text
===MEMO1===
▼商品名または紹介文
説明文
https://example.com

▼商品名または紹介文
説明文
https://example.com
```

GitHub Actions実行時は、workflow input `affiliate_memo` で使用するMEMO番号を指定できます。

各H2章末へ挿入する個数は、workflow input `affiliate_count` で指定できます。初期値は `2` です。

## タグの設定

投稿時のタグは次のファイルで管理します。

```text
C:\Users\mahha\OneDrive\開発\notion2note\tag.md
```

既存のタグをそのままコピー済みです。あとからこのファイルの中身だけを書き換えれば、note投稿時のタグを変更できます。

タグはスペース、カンマ、読点で区切れます。`#` は付けても付けなくても構いません。

例:

```text
AI 投資 Notion note 自動化
```

## Chrome拡張の設定

Chrome拡張は次のフォルダを読み込ませます。

```text
C:\Users\mahha\OneDrive\開発\notion2note\chrome_extension
```

設定手順:

1. Chromeで `chrome://extensions/` を開きます。
2. 右上の「デベロッパーモード」を有効化します。
3. 「パッケージ化されていない拡張機能を読み込む」を押します。
4. `C:\Users\mahha\OneDrive\開発\notion2note\chrome_extension` を選択します。
5. Notion記事ページを開きます。
6. 拡張機能のポップアップを開き、以下を入力します。

Chrome拡張の入力値:

- `GitHub Owner`
  - `seahirodigital`
- `Repository`
  - `notion2note`
- `Workflow`
  - `notion-note-post.yml`
- `Branch`
  - `main`
- `GitHub Token`
  - `GH_PAT` と同等の権限を持つPersonal Access Token
- `Notion URL`
  - 通常は現在開いているタブのURLが自動入力されます。

`Actionsを起動` を押すと、GitHub Actions の `C:\Users\mahha\OneDrive\開発\notion2note\.github\workflows\notion-note-post.yml` が起動します。

## GitHub Actions から手動実行する方法

GitHubのActions画面から `Notion記事 note投稿` workflowを開き、`Run workflow` を押します。

主な入力値:

- `notion_page_url`
  - 投稿したいNotionページURLです。
- `notion_page_id`
  - URLではなくページIDで指定したい場合に使います。
- `publish`
  - `true` にすると公開投稿まで進めます。
  - `false` の場合は下書き投稿です。
- `dry_run_publish`
  - `true` にすると公開画面まで進みますが、最後の投稿ボタンは押しません。
- `no_top_image`
  - `true` にするとNotion先頭画像のトップ画像設定をスキップします。
- `no_ogp`
  - `true` にするとOGP展開をスキップします。
- `no_toc`
  - `true` にすると目次挿入をスキップします。
- `affiliate_memo`
  - `affiliate_links.txt` のMEMO番号です。
- `affiliate_count`
  - 各H2章末に挿入するアフィリエイトブロック数です。
- `note_target`
  - 通常は `blog_main` のままで使います。

## ローカルで試す方法

Notion APIとnote認証情報をローカル環境変数に設定してから実行します。

```powershell
cd C:\Users\mahha\OneDrive\開発\notion2note
$env:NOTION_API_KEY="Notion Integration Secret"
$env:NOTE_EMAIL="noteログインメール"
$env:NOTE_PASSWORD="noteログインパスワード"
$env:NOTE_STORAGE_STATE="Storage State JSON"
python scripts\notion_note\post_from_notion.py --page-url "NotionページURL" --dump-markdown "C:\tmp\notion_article.md"
```

下書きだけ作る場合は `--publish` を付けません。

公開まで進める場合:

```powershell
python scripts\notion_note\post_from_notion.py --page-url "NotionページURL" --publish
```

公開画面まで確認し、最後の投稿ボタンだけ押さない場合:

```powershell
python scripts\notion_note\post_from_notion.py --page-url "NotionページURL" --dry-run-publish
```

## 投稿前整形ルール

`C:\Users\mahha\OneDrive\開発\notion2note\scripts\notion_note\post_from_notion.py` は、Notionページ取得後に以下の整形を行います。

- Notionページタイトルをnote記事タイトルにします。
- Notionプロパティ内のYouTube URLを検出し、本文の最上部へ配置します。
- Amazonアソシエイト表記をタイトル直後に配置します。
- H2 `文字起こし` または `transcript` 以降の本文を除外します。
- Notion画像ブロックを収集します。
- 画像はH2 `エグゼクティブサマリー` の章末へまとめて配置します。
- Notionカバー画像、または最初のNotion画像をnoteトップ画像に使います。
- `affiliate_links.txt` の指定MEMOから、各H2章末へランダムにアフィリエイト文面を挿入します。
- `tag.md` の内容をnote公開投稿時のタグとして使います。

## よくある失敗と確認ポイント

### Notion APIエラーが出る

- `NOTION_API_KEY` がGitHub Secretsに入っているか確認します。
- 投稿対象ページまたは親DBにNotion Integrationのアクセス権があるか確認します。
- NotionページURLからページIDを抽出できる形式か確認します。

### noteログインに失敗する

- `NOTE_EMAIL` と `NOTE_PASSWORD` を確認します。
- `NOTE_STORAGE_STATE` が古い場合は、`C:\Users\mahha\OneDrive\開発\notion2note\scripts\note_engine\note_draft_poster.py --save-cookies` を再実行して更新します。
- note側でreCAPTCHAなどが出る場合は、無人実行では自動復旧できないことがあります。

### OGP展開されない

- `no_ogp` が `true` になっていないか確認します。
- 対象URLが単独行として本文に入っているか確認します。
- 既存エンジンは `amzn.to`、`amazon.co.jp`、`apple.com`、`youtube.com` を主なOGP対象として扱います。

### タグが付かない

- `C:\Users\mahha\OneDrive\開発\notion2note\tag.md` が空でないか確認します。
- 公開投稿時だけタグ入力画面へ進むため、下書き投稿ではタグ設定は実行されません。

### アフィリエイトが入らない

- `C:\Users\mahha\OneDrive\開発\notion2note\affiliate_links.txt` に対象MEMOがあるか確認します。
- 記事本文にH2見出しがあるか確認します。
- `affiliate_count` が `0` になっていないか確認します。

## 運用時に書き換えるファイル

通常運用で書き換えるのは、主に次の2ファイルです。

```text
C:\Users\mahha\OneDrive\開発\notion2note\affiliate_links.txt
C:\Users\mahha\OneDrive\開発\notion2note\tag.md
```

この2つを編集すれば、投稿本文へ入れるアフィリエイト文面と、公開時のタグを変更できます。

`C:\Users\mahha\OneDrive\開発\Blog_Vercel` 側のファイルは変更しません。

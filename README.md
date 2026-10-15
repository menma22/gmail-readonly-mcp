# Gmail Read-only MCP

**好きなGoogleアカウントのGmailを、送信・変更・削除の権限を与えずにMCPで読むためのローカル導入キットです。** 個人のGmailとGoogle Workspaceの両方を想定しています。会社のアカウントでは管理者の制限が優先されます。

MCP本体には [Taylor Wilsdon氏のGoogle Workspace MCP](https://github.com/taylorwilsdon/google_workspace_mcp) の **`workspace-mcp==2.1.0`** を利用します。このリポジトリは本体を再実装したものではありません。アカウント別の設定、Google標準OAuth、実権限の検査、MCP起動・接続検証を追加したものです。Google・OpenAIの公式製品ではありません。

English: a local, account-isolated setup kit for upstream workspace-mcp. It requests Gmail read-only access, checks the actual Google grant and account before startup, and emits Codex or generic MCP configuration. No hosted service or shared OAuth credentials are provided. Commands are listed below; users supply their own Google Cloud Desktop OAuth client.

## できることと、できないこと

| 操作 | 対応 |
|---|---|
| メールの検索、本文・スレッド取得、まとめて取得 | 対応 |
| 添付ファイル取得、ラベル一覧の参照 | 対応 |
| メール送信、返信、転送、下書き作成 | 不可 |
| 既読化、ラベルの付け替え、アーカイブ、削除 | 不可 |
| 複数アカウント | プロファイルを分けて対応。1 MCP接続＝1アカウント |

「読み取り専用」は、Googleへのメール変更を許可しないという意味です。読み取ったメールはMCPクライアントや利用するAIモデルへ渡ります。読む権限自体を与えたくないアカウントには接続しないでください。ローカルの認証ファイル保存や添付ファイルの取得まで禁止するものではありません。

## 必要なもの

- Python 3.11以降、[uv](https://docs.astral.sh/uv/getting-started/installation/)、Git
- 接続する本人のGoogleアカウントと、そのアカウントのGmail
- 自分で管理するGoogle Cloudプロジェクトと「デスクトップ アプリ」用OAuthクライアント
- stdio MCPに対応したクライアント。Codex用TOMLと、Claude Desktop等の`mcpServers`形式を生成できます

このリポジトリの公開と、Google OAuthアプリの一般公開は別です。共通の認証サーバーや認証済みアプリは提供しません。利用者ごとにGoogle Cloudの設定と本人の同意が必要です。サービスアカウント・ドメイン全体への委任・リモートHTTP公開は扱いません。

## 1. インストール

Windows、macOS、Linuxで同じコマンドを使います。

```sh
git clone https://github.com/menma22/gmail-readonly-mcp.git
cd gmail-readonly-mcp
uv sync --locked
uv run gmail-readonly-mcp --help
```

`uv sync --locked`は同梱のロックファイルに従って依存関係をインストールします。設定を生成した後も、このフォルダと`.venv`を残してください。生成設定はこの環境のPythonの絶対パスを参照します。

## 2. Google Cloudを準備する

1. [Google Cloud Console](https://console.cloud.google.com/)で、自分のプロジェクトを選択または作成します。他サービスの認可と混ぜないため、専用プロジェクトまたは専用OAuthクライアントを推奨します。
2. APIライブラリから **Gmail API**（`gmail.googleapis.com`）を有効にします。本キットは通常のGmail APIを利用します。
3. Google Auth Platformの「ブランディング」「対象」を設定します。個人Gmailでは通常「外部」、対応するWorkspace組織内に限定する場合は「内部」を選べます。「外部・テスト」では、利用するアカウントをテストユーザーに追加します。
4. 「データアクセス」で追加するメール権限を、次の1つにします。

   ```text
   https://www.googleapis.com/auth/gmail.readonly
   ```

5. 「クライアント」→「クライアントの作成」で、種類を **デスクトップ アプリ** にします。例えば`Gmail Read Only Desktop`という名前にします。ウェブアプリ用JSONは本キットでは受け付けません。クライアント名と、Google同意画面に出るアプリの表示名は別です。
6. OAuthクライアントのJSONをダウンロードし、**Gitリポジトリの外**に保存します。作成直後の画面でしかシークレットをダウンロードできない場合があるため、画面を閉じる前に保管します。

Googleの[Desktop OAuth手順](https://developers.google.com/identity/protocols/oauth2/native-app)、[Gmailスコープ一覧](https://developers.google.com/workspace/gmail/api/auth/scopes)が一次資料です。スコープをCloudの一覧に追加するだけでは制限になりません。実際のOAuth要求と、Googleが発行したトークンを検査する必要があります。

## 3. アカウント別プロファイルを作る

`work`は好きなプロファイル名、`you@example.com`は接続するGoogleアカウントの**主メールアドレス**へ置き換えます。名前は小文字の英字で始まる1〜32文字で、英数字・`_`・`-`が使えます。

Windows PowerShellの例：

```powershell
uv run gmail-readonly-mcp init --profile work --account you@example.com --client-secrets "C:\Users\you\Downloads\client_secret_your_client.json"
```

macOS / Linuxの例：

```sh
uv run gmail-readonly-mcp init --profile work --account you@example.com --client-secrets "$HOME/Downloads/client_secret_your_client.json"
```

JSONはアプリのデータ領域へコピーされます。既存プロファイルは上書きしません。別のアカウントを追加する場合は、別名で同じ手順を行います。

```sh
uv run gmail-readonly-mcp init --profile personal --account you@gmail.com --client-secrets /path/to/your/desktop-client.json
```

`work`と`personal`でGoogleアカウント・トークン・MCP名を分けられます。Googleの認可ポリシーに従う範囲で、同じDesktopクライアントを複数プロファイルに使うこともできます。会社のアカウントでは組織用クライアントを使ってください。

## 4. Googleで本人が許可する

```sh
uv run gmail-readonly-mcp auth --profile work
```

通常のブラウザが開きます。該当アカウントを選択して、Gmailの権限が **「メール メッセージと設定の表示」** に相当する閲覧だけであることを確認します。送信・作成・管理等が表示される場合は許可せず、クライアントと既存の認可を確認してください。

接続後に返るGoogleの実権限とGmailプロフィールを検証し、アカウントが一致した場合だけ継続接続用のトークンを保存します。別アカウントでログインした場合は失敗します。Googleのログイン・パスワード・多要素認証は本人が行います。

ブラウザが自動で開かない場合：

```sh
uv run gmail-readonly-mcp auth --profile work --no-browser
```

端末に出たGoogleのURLを、**同じPC**のブラウザで開きます。認証コードをチャットへ貼る必要はありません。localhostへのコールバック待受は5分で終了します。リモートマシン・コンテナの認証転送はサポートしません。

再認証も同じ`auth`コマンドです。成功したときだけ、そのプロファイルの保存トークンを更新します。Google側に認可が残っていても、余分なデータ権限のあるトークンは本キットが拒否します。

## 5. 接続を検証する

```sh
uv run gmail-readonly-mcp verify --profile work
```

次を実測します。

- Googleの実トークンに`gmail.readonly`があり、別のデータ権限がないこと
- Gmailプロフィールが設定したメールアドレスと一致すること
- 実際に起動したMCPの公開ツールが既知の7つと一致すること
- MCPを通じてラベル一覧を取得できること

検索と本文1件まで検証する場合：

```sh
uv run gmail-readonly-mcp verify --profile work --read-message
```

検索は迷惑メールとゴミ箱を除き最大1件です。メール本文は検証処理のメモリ内で受け取り、結果には出力しません。対象メールがなければ「本文取得成功」とは報告しません。これらのチェックは送信・削除等の変更APIを試しません。

## 6. MCPクライアントへ登録する

### Codex

```sh
uv run gmail-readonly-mcp config --profile work --format codex
```

表示されたTOMLを`~/.codex/config.toml`へ追加します。Windowsでは通常、ユーザーフォルダ内の`.codex/config.toml`です。同じサーバー名の定義があれば重複追加せず、その定義を確認してください。既存の別MCP設定を置き換える必要はありません。

設定名は`gmail-readonly-work`になります。`personal`なら`gmail-readonly-personal`です。Codexを再起動してMCP設定を読み直し、新しいチャットで読み取りを試してください。Codexの[公式MCP設定](https://developers.openai.com/codex/mcp/)も参照してください。

### Claude Desktopなど、JSON設定のクライアント

```sh
uv run gmail-readonly-mcp config --profile work --format json
```

出力される`mcpServers`のエントリを、利用するクライアントの既存設定へ統合します。既存の`mcpServers`全体を置き換えないでください。ファイルの保存先や再読込方法は各クライアントの説明に従います。

生成設定は`python -m gmail_readonly_mcp ... serve --profile work`を起動します。`serve`は標準出力をMCPの通信だけに使い、起動前に実権限とアカウントを確認します。手動で動作を見る場合もMCPクライアントから起動してください。端末で`serve`だけを実行すると、MCP入力を待ち続けるのが正常です。

例：AIへの依頼は「`gmail-readonly-work`で、昨日届いた未読メールを検索して要点をまとめて」。アカウント引数を渡す場合は、そのプロファイルのアドレスを使います。

## 読み取り専用にする仕組み

1. **要求するGmailスコープを固定**：本キットの`auth`は`gmail.readonly`のみを要求します。Google標準OAuthライブラリ、PKCE、loopbackコールバックを利用します。
2. **発行済みトークンをGoogleで検査**：保存時・MCP起動時・`verify`時に`tokeninfo`とGmailの`users.getProfile`へ照会します。書き込み権限だけでなく、Drive等の別サービスの権限も拒否します。以前に他のツールで取得した認証情報では、本人識別専用の`openid`・`email`・`profile`とそのGoogle URI表記だけを追加で許容します。
3. **MCP本体を読み取り専用で起動**：upstreamの`--read-only --tools gmail`を固定します。upstreamの認証開始ツールも非公開にし、本キットの`auth`経由にそろえます。環境変数による別アカウント・別権限・外部OAuth・任意のOTel送信先の指定を起動時に除去します。
4. **ツール一覧も実測**：`verify`は実MCPの一覧を既知の7操作と照合します。これはトークンの権限制限とは別の検証です。`readOnlyHint`だけを安全性の根拠にはしません。

公開するツールは`search_gmail_messages`、`get_gmail_message_content`、`get_gmail_messages_content_batch`、`get_gmail_attachment_content`、`get_gmail_thread_content`、`get_gmail_threads_content_batch`、`list_gmail_labels`です。

Gmail読み取り専用トークンはメールボックス全体を読む権限です。ラベルや検索条件ごとのアクセス制限ではありません。MCPへの入力によって読み取る対象が変わります。また、他のMCPやCLIで別途与えた権限を、このキットが制限することはできません。

## 認証情報・データの保存先

既定では`platformdirs`が返すアプリ用データフォルダの下へ、プロファイル別に保存します。実際のパスは`init`の結果に表示します。

| OS | 通常の保存先 |
|---|---|
| Windows | `%LOCALAPPDATA%\gmail-readonly-mcp\<profile>\` |
| macOS | `~/Library/Application Support/gmail-readonly-mcp/<profile>/` |
| Linux | `${XDG_DATA_HOME:-~/.local/share}/gmail-readonly-mcp/<profile>/` |

各プロファイルは`profile.json`、`client_secret.json`、アカウント名に対応するトークンJSONを持ちます。これらは**平文JSON**です。Windowsは本人とSYSTEM向けのACL、macOS/Linuxはフォルダ`0700`・ファイル`0600`を設定します。OS金庫による暗号化は実装していません。同じユーザー権限で動くプログラムや端末管理者に対する秘匿は保証しません。

認証ファイル・認証URL・検証ログ・メール本文・実メールのスクリーンショットをGitHubへ追加しないでください。`.gitignore`は補助であり、公開前の確認の代わりにはなりません。Googleから最初にダウンロードした元のJSONも、自分で安全に保管してください。

保存先を変える場合は、すべてのコマンドで同じ絶対パスを指定します。Gitリポジトリ内は指定しないでください。

```sh
uv run gmail-readonly-mcp --state-dir /absolute/private/location auth --profile work
uv run gmail-readonly-mcp --state-dir /absolute/private/location config --profile work
```

通信はローカルのMCPクライアントとGoogleの認証・Gmail APIが中心です。インストール時はPythonパッケージ配布元、upstream起動時はGoogle APIのDiscovery情報等にもアクセスする場合があります。本キット独自のクラウドサーバー・計測送信先はありません。MCPクライアントによるAI事業者へのデータ送信は、そのクライアントの設定と契約に従います。

## よくある問題

| 症状 | 確認すること |
|---|---|
| `accessNotConfigured` / API未有効化 | OAuthクライアントが属するプロジェクトでGmail APIを有効にする |
| `Google API quota exceeded` / 429 | Gmail APIの利用枠に達している。時間を空けて再実行し、繰り返す場合はCloudの割当と他クライアントの利用量を確認 |
| アクセスがブロックされた / 403 | 正しいGoogleアカウント、テストユーザー、Workspace管理者の許可を確認 |
| `redirect_uri_mismatch` | 「デスクトップ アプリ」のJSONか確認。ウェブ用クライアントを使わない |
| Google未確認アプリと表示される | 自分で作ったクライアントか確認。組織ポリシーやGoogleの確認要件に従う。制限の回避機能はない |
| `additional data scopes` | 専用プロジェクト/クライアントと`auth`で取得し直す。余分な認可の取り消しはGoogleアカウント側で本人が行う |
| `account does not match` | エイリアスではなく主メールアドレスでプロファイルを作り、同じアカウントでログイン |
| 以前動いたのに失効した | `auth --profile ...`で再認証。Googleの外部・テスト状態では、Gmail等を含む更新トークンが通常7日で失効する場合がある |
| MCPが出ない | 生成した絶対パス、`.venv`の存在、クライアントの設定再読込を確認 |
| `tool catalog changed` | 想定バージョンと異なる。ロックファイルに従って再インストール。依存だけを勝手に更新しない |
| `Could not restrict ... Windows ACL` | ローカルの通常フォルダで実行し、`whoami`と`icacls`が利用できるか確認 |
| `No authenticated user resolved`というupstreamログ | stdio単一ユーザーモードの途中で出ることがある。`verify`の終了コードと実取得結果で判定する |

Googleの[更新トークンの有効期限](https://developers.google.com/identity/protocols/oauth2#expiration)を参照してください。Google Workspace管理者の制限・APIの割当・Googleの確認要件はこのキットでは変更しません。`gmail.readonly`はRestricted scopeです。OAuthアプリを一般提供する場合は、Googleの[検証要件](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification)を確認してください。

## 接続をやめる

まずMCPクライアントから対象エントリを外し、Googleアカウントの[接続済みアプリ](https://myaccount.google.com/connections)で不要な認可を本人が取り消します。ローカルのプロファイルフォルダを消すだけではGoogle側の認可は取り消されません。

このキットに自動削除・認可取り消しコマンドはありません。プロファイルの削除やCloudプロジェクトの整理は、必要な他の接続へ影響しないことを確認して自分で行ってください。

## 開発・検証

```sh
uv sync --locked
uv run ruff check .
uv run pytest -q
uv build
```

自動テストはGoogle APIを呼ばず、過剰スコープ・別アカウント・異常な認証先・プロファイルの分離・既存ファイルの保持・設定生成等を検証します。実際のupstreamも起動してツール一覧を確認します。GitHub ActionsはWindows/macOS/Linux × Python 3.11/3.13で実行します。Googleの実認可と実メール取得は、各利用者の`auth`と`verify --read-message`で別途確認してください。実メールやトークンをCIの入力・成果物にしないでください。

本キットの`serve`は実権限を検証してからupstreamへ実行を引き継ぎます。その後のメール操作はupstreamの実装とGoogleのスコープ制御に従います。ローカルコードや認証ファイルを改変できる攻撃者に対するサンドボックスではありません。

## ライセンス・謝辞

本リポジトリの追加コードは[MIT License](LICENSE)。MCPの通信・Gmailツールは[upstream workspace-mcp](https://github.com/taylorwilsdon/google_workspace_mcp)が提供します。upstreamのソースをこのリポジトリへコピーせず、依存としてインストールします。各依存のライセンスはそれぞれのプロジェクトに従います。

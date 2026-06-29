# スマホから使う（Tailscale 経由・常時起動）

この Mac で backend / frontend を常時起動し、Tailscale 経由でスマホ（外出先含む）からアクセスするための設定と運用メモ。

## できること

- Tailscale に入った端末（このMac・スマホ）から、外部ネットワークにいても作戦ボードを開ける。
- Mac 再起動後・プロセスがクラッシュしても **launchd が自動で起動/復帰**する。

## アクセスURL（スマホ）

```
http://torumacbook-pro.tail0436c5.ts.net:3001
```

- Mac の MagicDNS 名 `torumacbook-pro.tail0436c5.ts.net`（Tailscale IP `100.118.45.96`）。確認: `tailscale status`。
- Tailscale IP でも可（少し短い）: `http://100.118.45.96:3001`
- frontend は API 接続先を「**開いたホストと同じホストの :8000**」に自動で向ける（`api.ts`）。
  そのため URL のホスト名が何であっても backend を正しく叩ける（ビルドし直し不要）。

## 構成（セットアップ済み）

| 項目 | 値 |
|---|---|
| backend | `uvicorn main:app --host 0.0.0.0 --port 8000`（本番・reloadなし） |
| frontend | `next start -H 0.0.0.0 -p 3001`（本番ビルド） |
| DB | `INVEST_DB_PATH=/Users/toru/code/invest-analyzer/data.db` |
| 自動起動 | launchd ユーザーエージェント（ログイン中に常駐・`RunAtLoad`＋`KeepAlive`） |
| plist | `~/Library/LaunchAgents/com.invest-analyzer.{backend,frontend}.plist` |
| ログ | `~/Library/Logs/invest-analyzer/{backend,frontend}.{out,err}.log` |
| CORS | localhost / 127.0.0.1 / Tailscale(`100.x`・`*.ts.net`) の origin を許可（`main.py`） |

## 前提（外出先から繋ぐために必須）

- **Mac が給電＋スリープ無効**であること。Mac が寝ると外から繋がらない。
  - スリープ無効化（フタ閉じでも起こす。要管理者パスワード）:
    ```
    sudo pmset -c disablesleep 1
    ```
  - 元に戻す: `sudo pmset -c disablesleep 0`
- **Tailscale が Mac とスマホの両方で up**（同一アカウントでログイン）していること。
- 自動起動はこの Mac に**ログインしている間**有効（ログイン前から動かしたい場合は LaunchDaemon 化が必要）。

## 運用コマンド

```bash
# 状態確認（PID が出ていれば稼働中）
launchctl list | grep invest

# ローカルでの応答確認
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/watchlist   # backend
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:3001/plan        # frontend

# 再起動（例: backend）
launchctl kickstart -k gui/$(id -u)/com.invest-analyzer.backend
launchctl kickstart -k gui/$(id -u)/com.invest-analyzer.frontend

# 停止（自動起動も無効化）
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.invest-analyzer.backend.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.invest-analyzer.frontend.plist

# 起動（再登録）
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.invest-analyzer.backend.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.invest-analyzer.frontend.plist

# ログ確認
tail -f ~/Library/Logs/invest-analyzer/backend.err.log
tail -f ~/Library/Logs/invest-analyzer/frontend.err.log
```

## コードを変更したときの反映

- **backend** を変更したら再起動するだけ:
  `launchctl kickstart -k gui/$(id -u)/com.invest-analyzer.backend`
- **frontend** を変更したら**再ビルド→再起動**:
  ```
  cd /Users/toru/code/invest-analyzer/frontend && npm run build
  launchctl kickstart -k gui/$(id -u)/com.invest-analyzer.frontend
  ```
  （本番は `next start` なので dev のホットリロードは効かない）

## 繋がらないときのチェック

1. Mac が起きているか（スリープ/フタ閉じでないか）。
2. Mac とスマホの両方で Tailscale が up か（`tailscale status`／スマホアプリ）。
3. URL は `http://<Macの Tailscale 名>:3001` か（`https` でない・ポート `3001`）。
4. ローカルで応答するか（上記 curl）。ダメなら `launchctl list | grep invest` とログを確認。
5. それでもダメなら同一 LAN で `http://<MacのLAN IP>:3001` も試して切り分け。

## メモ

- HTTP 接続（鍵マークなし）だが、Tailscale 通信は WireGuard で暗号化される。
  HTTPS（鍵マーク）にしたい場合は `tailscale serve` での HTTPS 化が可能（別途設定）。
- backend の依存（venv）や frontend の `node_modules` を入れ直したら、各サービスを再起動する。

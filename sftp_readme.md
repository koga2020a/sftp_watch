# SFTP Directory Monitor

リモートSFTPサーバー上のディレクトリを監視し、ファイルの変更を検出・ログ記録するツールです。

## 機能

- 複数のディレクトリの再帰的な監視
- ファイルの追加、削除、変更の検出
- ディレクトリの追加、削除の検出
- タイムスタンプのみの変更（[DATEonly]）の特別な検出
- カラー表示によるディレクトリとファイルの区別
- プロキシサーバー経由の接続サポート
- パスワード認証とSSH鍵認証のサポート
- CSVとJSON形式でのログ記録
- 画面表示と同じ形式のログファイル出力

## インストール

必要なライブラリをインストールします：

```bash
pip install paramiko pyyaml
```

## 設定

`config.yaml` ファイルを作成してください：

```yaml
host: "12.34.56.78"
port: 22
user: "aaa"
#user: "bbb"
password: "aaa"           # または下の `pkey_base64`
#pkey_base64: ""
auth_type: "password"            # または "pkey"
#auth_type: "pkey"
proxy_host: "proxy.test.com"
proxy_port: 1234
dirs:
#  - "/export/home/www.kirin.co.jp-test-area/"
  - "/test/"
interval: 60
```

### 設定パラメータ

| パラメータ | 説明 |
|------------|------|
| `host` | SFTPサーバーのホスト名またはIPアドレス |
| `port` | SFTPポート（通常は22） |
| `user` | ユーザー名 |
| `password` | パスワード（`auth_type: "password"`の場合） |
| `pkey_base64` | Base64エンコードされた秘密鍵（`auth_type: "pkey"`の場合） |
| `auth_type` | 認証タイプ（"password"または"pkey"） |
| `proxy_host` | プロキシサーバーのホスト名（使用する場合） |
| `proxy_port` | プロキシサーバーのポート（使用する場合） |
| `dirs` | 監視するディレクトリのリスト |
| `interval` | 監視間隔（秒） |

## 使用方法

```bash
python sftp_monitor.py
```

または別の設定ファイルを指定：

```bash
python sftp_monitor.py --config my_config.yaml
```

## 実行後の動作

プログラムを実行すると、以下のような動作をします：

1. 初回実行時：
   - 設定されたディレクトリの構造をツリー形式で表示
   - 各ファイルとディレクトリの初期状態を記録

2. 監視開始後：
   - 設定された間隔（デフォルト60秒）ごとにディレクトリをスキャン
   - 変更を検出すると、リアルタイムで画面に表示
   - 同時に3種類のログファイルに記録

3. ログファイルの更新：
   - `log.csv`: 変更が発生するたびに追記
   - `log.json`: 最新の状態を上書き保存
   - `display_log.txt`: 画面表示と同じ内容を追記

4. 終了時：
   - Ctrl+Cでプログラムを終了
   - 終了時の状態が最終的なログとして記録

### ログファイルの例

#### log.csv
```csv
2024-03-20 10:00:00,ADD,/test/new_file.txt,1024
2024-03-20 10:01:00,MOD,/test/existing.txt,2048,1024
2024-03-20 10:02:00,DATE,/test/date_changed.txt,512,2024-03-19 15:00:00,2024-03-20 10:02:00
```

#### log.json
```json
{
  "/test": {
    "type": "directory",
    "files": {
      "new_file.txt": {
        "size": 1024,
        "mtime": "2024-03-20 10:00:00"
      }
    }
  }
}
```

#### display_log.txt
```
[2024-03-20 10:00:00] [ADD] /test/new_file.txt (1024 bytes)
[2024-03-20 10:01:00] [MOD] /test/existing.txt (2048 bytes, was 1024 bytes)
[2024-03-20 10:02:00] [DATEonly] /test/date_changed.txt (512 bytes)
```

## 出力

### 画面表示

プログラムは変更を次の形式で表示します：

- `[ADD]` - 新しいファイルが追加された
- `[DEL]` - ファイルが削除された
- `[MOD]` - ファイルの内容が変更された（サイズ変更）
- `[DATEonly]` - ファイルのタイムスタンプのみが変更された
- `[ADD]` (directory) - 新しいディレクトリが追加された
- `[DEL]` (directory) - ディレクトリが削除された

### ログファイル

プログラムは3種類のログファイルを生成します：

1. `log.csv` - 変更のCSV形式ログ
2. `log.json` - 現在の状態のJSONスナップショット
3. `display_log.txt` - 画面表示と同じ形式のテキストログ

#### CSVログの形式

- ADD: `[timestamp, "ADD", path, size]`
- DEL: `[timestamp, "DEL", path, size]`
- MOD: `[timestamp, "MOD", path, old_size, new_size]`
- DATE: `[timestamp, "DATE", path, size, old_time, new_time]`
- ADD_DIR: `[timestamp, "ADD_DIR", path]`
- DEL_DIR: `[timestamp, "DEL_DIR", path]`

## 特徴

- 空のディレクトリが非空になった場合や、逆に非空ディレクトリが空になった場合でも、ディレクトリの追加削除として扱いません
- ファイルサイズが同じでタイムスタンプのみが変更された場合は [DATEonly] として特別に表示します
- プロキシ環境下でも動作します
- 初回の実行時にはディレクトリ構造をツリー形式で表示します

## トラブルシューティング

### 接続エラー

接続できない場合は次の点を確認してください：

- ホスト名、ポート、ユーザー名、パスワードが正しいか
- プロキシ設定が必要な場合は、プロキシホストとポートが正しいか
- ファイアウォールがSFTP接続を許可しているか

### 認証エラー

- パスワード認証の場合: パスワードが正しいことを確認
- 鍵認証の場合: 秘密鍵が正しくBase64エンコードされていることを確認

## 注意事項

- 監視間隔が短すぎると、サーバーに負荷をかける可能性があります
- 大規模なディレクトリ構造を監視する場合は、初回のスキャンに時間がかかる場合があります

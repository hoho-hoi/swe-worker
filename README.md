# swe-worker

GitHub Issue をトリガに、**Issueごとに常駐するWorkerコンテナ**を起動して自律実装を行い、**ブランチpush + PR作成（Ready）**まで完結させるための最小実装です。

## 前提

- 依存管理は **`pyproject.toml` + `uv`** です（`requirements.txt` は使いません）
- 作業ツリーと状態は `DATA_DIR`（デフォルト `/data`）配下に永続化します
- OpenHands CLI をプロジェクト依存として入れる場合、現状は **Python 3.12** が必要です（`openhands==1.6.0` 要件）

## セットアップ（ローカル）

```bash
cd /path/to/swe-worker
uv venv -p 3.12
uv sync --group dev
```

## 起動（ローカル）

`.env` に書いてもOKです（`AppSettings` が `.env` を読みます）。例:

```bash
# .env（例）
GITHUB_TOKEN=***
ENGINEER_PAT_KEY=***   # GITHUB_TOKENの代替（どちらか必須）
OPENAI_API_KEY=***
LLM_MODEL=***          # 推奨: Provider非依存のモデル名（未指定ならOpenHands側のデフォルト）
OPENAI_MODEL=***       # OpenAI向けにモデル名を明示したい場合（LLM_MODELと同時指定は非推奨）
GEMINI_API_KEY=***     # Geminiを使う場合
```

```bash
export DATA_DIR="./data"
export GITHUB_TOKEN="***"
export REPO="owner/repo"
export ISSUE_NUMBER="123"
export BASE_BRANCH="main"

# OpenHands CLI を呼ぶコマンド（任意）
# - 実行環境に合わせて「存在するコマンド」を指定してください
# - 引数付きもOK（例: "uv run openhands"）
export OPENHANDS_COMMAND="openhands"

uv run python -m app.worker_server
```

## API

### Health

```bash
curl -sS http://127.0.0.1:8000/health
```

### Start

```bash
curl -sS -X POST http://127.0.0.1:8000/event \
  -H "Content-Type: application/json" \
  -d '{"type":"start","payload":{"repo":"owner/repo","issue_number":123,"base_branch":"main"}}'
```

### Rerun

```bash
curl -sS -X POST http://127.0.0.1:8000/event \
  -H "Content-Type: application/json" \
  -d '{"type":"rerun","payload":{}}'
```

### Stop

```bash
curl -sS -X POST http://127.0.0.1:8000/stop
```

## Docker（例）

```bash
docker build -t swe-worker:local .
docker run --rm -p 8000:8000 \
  -e GITHUB_TOKEN="***" \
  -e DATA_DIR="/data" \
  -v "$(pwd)/data:/data" \
  swe-worker:local
```

## 重要（セキュリティ）

- **トークンをログ出力しない**前提です
- git remote URL に token を埋め込んで永続化しない設計です（push/clone は `git -c http.*.extraheader=...` を使用）

## トラブルシュート

### `OPENAI_API_KEY を入れたとき、どのモデルが使われる？`

このWorker自体は **モデルを自動選択しません**。`LLM_MODEL` / `OPENAI_MODEL` は **OpenHandsに環境変数としてそのまま渡され**、実際にどちらが使われるかは OpenHands側の仕様/設定に依存します。

- `LLM_MODEL` と `OPENAI_MODEL` の **同時指定は避けて**、どちらか片方だけを指定する運用を推奨します
- どちらも無ければ **OpenHands側のデフォルト**になります

### `No such file or directory: 'openhands'`

`OPENHANDS_COMMAND=openhands` を指定したが、実行環境の `PATH` に `openhands` が無い状態です。

- このリポジトリは `pyproject.toml` に `openhands` を含めているため、通常は `uv sync` 後に `uv run openhands --version` が通ります。
  - それでも見つからない場合は、別のPython/別の環境で起動している可能性があります。

- ローカルで `openhands` が入っている場合:

```bash
which openhands
export OPENHANDS_COMMAND="$(which openhands)"
```

- `uv` 経由で起動したい場合（引数付き指定）:

```bash
export OPENHANDS_COMMAND="uv run openhands"
```
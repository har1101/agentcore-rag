# Agentic RAG on AgentCore Runtime

ベクトル DB を使わない RAG。Claude Agent SDK の grep / glob / read ツールでナレッジベースを直接検索するエージェントを、Amazon Bedrock AgentCore Runtime 上で動かします。

## なぜ Agentic Search か

Claude Code の開発チームは、初期の RAG（ベクトル検索）を grep/glob ベースの Agentic Search に置き換えたところ「すべてを大幅に上回った」と報告しています。

| 観点 | ベクトル検索 RAG | Agentic Search |
|------|-----------------|----------------|
| コスト | エンベディングモデル費用 | 不要 |
| 鮮度 | インデックス更新が必要 | 常にファイルを直接検索（常に最新） |
| インフラ | ベクトル DB が必要 | 不要 |
| 透明性 | ブラックボックス | 検索過程が完全に可視化 |

## アーキテクチャ

```
S3 Bucket ──→ EventBridge ──→ Lambda (sync_handler)
                                  │
                    invoke_agent_runtime_command
                    (aws s3 sync → Session Storage)
                                  │
                                  ▼
              ┌─── AgentCore Runtime (microVM) ───┐
              │                                    │
              │  app.py → Claude Agent SDK         │
              │            ├── Grep (ripgrep)      │
              │            ├── Glob (file pattern) │
              │            └── Read (file reader)  │
              │                    │                │
              │            Session Storage          │
              │   /mnt/session/knowledge_base/      │
              └────────────────────────────────────┘
```

1. **ナレッジベース同期**: S3 にファイルを置くと EventBridge → Lambda が `InvokeAgentRuntimeCommand` で Session Storage に自動同期
2. **クエリ応答**: Claude Agent SDK が Grep/Glob/Read で Session Storage 内のファイルを自律的に検索し、ソースを引用して回答
3. **ストリーミング**: `include_partial_messages=True` で `StreamEvent` を処理し、ツール呼出し過程を含むリアルタイムストリーミング

## 技術スタック

| コンポーネント | 技術 |
|---|---|
| エージェントホスティング | Amazon Bedrock AgentCore Runtime |
| 永続ストレージ | AgentCore Session Storage |
| エージェント SDK | [Claude Agent SDK Python](https://platform.claude.com/docs/en/agent-sdk/python) |
| LLM | Claude Sonnet 4.6 (Bedrock cross-region inference) |
| IaC | AWS CDK (TypeScript) + [`@aws-cdk/aws-bedrock-agentcore-alpha`](https://www.npmjs.com/package/@aws-cdk/aws-bedrock-agentcore-alpha) |
| コンテナビルド | [`@cdklabs/deploy-time-build`](https://www.npmjs.com/package/@cdklabs/deploy-time-build) (CodeBuild ARM64) |

## プロジェクト構成

```
agentcore-rag/
├── agent/                  # AgentCore Runtime コンテナ (Docker コンテキスト)
│   ├── Dockerfile
│   ├── app.py              # エントリポイント (Claude Agent SDK)
│   ├── config.py           # 設定値 (KB パス, system prompt)
│   ├── pyproject.toml
│   └── uv.lock
├── docs/
│   ├── design.md           # 設計書 (詳細)
│   └── knowledge_base/     # ナレッジベースソース (S3 経由で同期)
│       ├── docs/*.md
│       └── src/*.py, *.ts
└── infra/                  # AWS CDK プロジェクト
    ├── bin/infra.ts
    ├── lib/agentcore-rag-stack.ts
    └── lambda/sync_handler.py
```

## デプロイ

### 前提条件

- AWS CLI (認証設定済み)
- Node.js >= 18
- [uv](https://docs.astral.sh/uv/)

### 手順

```bash
# 1. uv.lock の生成 (初回のみ)
cd agent && uv lock && cd ..

# 2. Lambda に最新 boto3 をバンドル
uv pip install "boto3>=1.42.80" --target infra/lambda/

# 3. CDK Bootstrap (アカウント・リージョンにつき初回のみ)
cd infra && npm install && npx cdk bootstrap

# 4. デプロイ
npx cdk deploy
```

### ナレッジベースの同期

```bash
# S3 にアップロード → EventBridge → Lambda → Session Storage に自動同期
aws s3 sync docs/knowledge_base/ s3://<BUCKET_NAME>/knowledge_base/
```

### クエリ

```bash
# AWS CLI
PAYLOAD=$(echo -n '{"prompt": "質問内容"}' | base64)
aws bedrock-agentcore invoke-agent-runtime \
  --agent-runtime-arn <RUNTIME_ARN> \
  --runtime-session-id <SESSION_ID> \
  --payload "$PAYLOAD" \
  --cli-read-timeout 300 \
  /dev/stdout
```

デプロイ後の出力値 (`BucketName`, `RuntimeArn`, `SessionId`) は `npx cdk deploy` の Outputs で確認できます。

## 設計ドキュメント

詳細な設計判断・ハマりポイント・参考リンクは [docs/design.md](docs/design.md) を参照してください。

## 参考リンク

- [Claude Code が RAG を捨てた理由 - Zenn](https://zenn.dev/acntechjp/articles/c1296f425baf03)
- [Claude Agent SDK Python リファレンス](https://platform.claude.com/docs/en/agent-sdk/python)
- [Amazon Bedrock AgentCore Runtime](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime.html)
- [AgentCore Runtime に Session Storage が追加されました - DevelopersIO](https://dev.classmethod.jp/articles/bedrock-agentcore-runtime-session-storage/)

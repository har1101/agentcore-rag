# Agentic RAG on AgentCore Runtime 設計書

## 1. 背景と動機

### 1.1 Agentic Search とは

従来の RAG（Retrieval Augmented Generation）はベクトル検索（エンベディング + ベクトル DB）を用いて関連ドキュメントを取得するが、**Agentic Search** は LLM エージェントが grep / glob / read などの基本的なファイル操作ツールを自律的に駆使して情報を探索するアプローチである。

Claude Code の開発者 Boris Cherny によると、初期の Claude Code は RAG + ローカルベクトル DB を使用していたが、grep/glob による Agentic Search に切り替えたところ「すべてを大幅に上回った（outperformed everything. By a lot.）」とのこと。

### 1.2 ベクトル検索 RAG との比較

| 観点 | ベクトル検索 RAG | Agentic Search (grep/glob) |
|------|-----------------|---------------------------|
| コスト | エンベディングモデル費用が発生 | 不要 |
| 鮮度 | インデックス更新が必要（陳腐化リスク） | 常にファイルシステムを直接検索（常に最新） |
| 精度 | セマンティック類似度（false positive あり） | 正確なキーワード/パターンマッチ |
| インフラ | ベクトル DB の構築・運用が必要 | 不要 |
| 透明性 | 検索過程がブラックボックス | 検索クエリ・結果が完全に可視化 |
| スケーラビリティ | 大規模データに強い | 数百万ファイル規模では遅延の可能性 |

### 1.3 参考文献

**日本語（Zenn）**:
- [Claude Code が RAG を捨てた理由 - 「Agentic Search」という選択肢](https://zenn.dev/acntechjp/articles/c1296f425baf03)
- [RAGを構築して満足してた私へ。Claude Code開発者はもう捨てていた](https://zenn.dev/kenimo49/articles/rag-vs-agentic-search)
- [ClaudeはなぜRAGを捨てたのか？コード生成における「エージェント型検索」の優位性](https://zenn.dev/manntera/articles/f3017ecba9c9c1)
- [RAG パイプラインを捨てて claude -p に Grep させたら3時間で社内ナレッジ Bot が動いた](https://zenn.dev/kok1eeeee/articles/knowledge-chatbot-agentic-search)

**英語**:
- [Why Grep Beat Embeddings in Our SWE-Bench Agent](https://jxnl.co/writing/2025/09/11/why-grep-beat-embeddings-in-our-swe-bench-agent-lessons-from-augment/)
- [Keyword Search is All You Need](https://signals.aktagon.com/articles/2026/02/keyword-search-is-all-you-need-achieving-rag-level-performance-without-vector-databases-using-agentic-tool-use/)
- [Claude Code Doesn't Index Your Codebase. Here's What It Does Instead](https://vadim.blog/claude-code-no-indexing)

---

## 2. 技術スタック

| コンポーネント | 技術 | 備考 |
|---|---|---|
| エージェントホスティング | Amazon Bedrock AgentCore Runtime | microVM ベースのサーバーレス実行環境 |
| 永続ストレージ | AgentCore Session Storage | 1GB/session, 14日保持, マウントパス指定 |
| エージェント SDK | Claude Agent SDK Python (`claude-agent-sdk`) | デフォルトツール (Grep, Glob, Read) を使用 |
| LLM | Claude Sonnet 4.6 (Bedrock 経由) | cross-region inference profile 使用 |
| IaC / デプロイ | AWS CDK (TypeScript) | L2 construct `@aws-cdk/aws-bedrock-agentcore-alpha` |
| コンテナビルド | `@cdklabs/deploy-time-build` | CodeBuild で ARM64 イメージをデプロイ時ビルド |
| パッケージ管理 | uv (`pyproject.toml` + `uv.lock`) | |
| オブザーバビリティ | AWS OpenTelemetry Distro | Dockerfile CMD で instrument |
| ナレッジベース同期 | S3 → EventBridge → Lambda → `InvokeAgentRuntimeCommand` | |

---

## 3. アーキテクチャ

### 3.1 全体構成

```
                          ┌─────────────────┐
                          │   S3 Bucket     │
                          │  (knowledge-    │
                          │   base source)  │
                          └───────┬─────────┘
                     PutObject /  │  DeleteObject
                                  ▼
                          ┌─────────────────┐
                          │  EventBridge    │
                          └───────┬─────────┘
                                  ▼
                          ┌─────────────────┐
                          │  Lambda         │
                          │  sync_handler   │
                          └───────┬─────────┘
              invoke_agent_runtime│  invoke_agent_runtime_command
              (セッション確保)    │  (aws s3 sync)
                                  ▼
┌──────────────────────────────────────────────────────────────┐
│  AgentCore Runtime (microVM)                                 │
│                                                              │
│  ┌───────────┐     ┌───────────────────────────────────┐    │
│  │  app.py   │────▶│  Claude Agent SDK                  │    │
│  │ entrypoint│     │   ┌─────────────────────────────┐  │    │
│  └───────────┘     │   │ Built-in Tools              │  │    │
│       ▲            │   │  - Grep (ripgrep)           │  │    │
│       │            │   │  - Glob (file pattern)      │  │    │
│  InvokeAgent       │   │  - Read (file reader)       │  │    │
│  Runtime           │   └────────────┬────────────────┘  │    │
│                    └────────────────┼───────────────────┘    │
│                           cwd ──────┘                        │
│                    ┌────────────────▼────────────────┐       │
│                    │  Session Storage                │       │
│                    │  /mnt/session/knowledge_base/   │       │
│                    │   ├── docs/*.md                 │       │
│                    │   ├── src/*.py, *.ts            │       │
│                    │   └── ...                       │       │
│                    └────────────────────────────────┘       │
└──────────────────────────────────────────────────────────────┘
```

### 3.2 リクエストフロー（ユーザークエリ）

1. クライアント → `InvokeAgentRuntime` に `{"prompt": "Xについて教えて"}`
2. `app.py` の async entrypoint が受信
3. Claude Agent SDK の `query()` に system prompt + デフォルトツール付きで転送
4. Claude が反復的にツール呼出し:
   - `Glob("**/*.md")` → ファイル構造を把握
   - `Grep("X")` → 関連ファイルを特定
   - `Read("docs/x.md")` → 詳細を読取
5. Claude が検索結果を統合して回答を生成
6. 各メッセージが `yield` でストリーミング返却

### 3.3 ナレッジベース同期フロー

1. ユーザーが S3 バケットにファイルをアップロード/削除
2. S3 イベント → EventBridge ルールがトリガー
3. Lambda `sync_handler` が起動:
   - `invoke_agent_runtime` で軽量プロンプト送信 → セッション確保
   - `invoke_agent_runtime_command` で `aws s3 sync` を実行
4. Session Storage 上のナレッジベースが最新化

**統一セッション ID**: Lambda とユーザークエリは同じ固定セッション ID を共有する。これにより Lambda が同期したファイルがユーザークエリ時にもそのまま見える。

---

## 4. プロジェクト構成

```
agentcore-rag/
├── Dockerfile                     # AgentCore Runtime コンテナイメージ
├── .dockerignore                  # Docker ビルド時の除外設定
├── pyproject.toml                 # Python 依存パッケージ (uv)
├── uv.lock                        # uv ロックファイル
├── config.py                      # 設定値（KB パス, ターン数, system prompt）
├── app.py                         # AgentCore entrypoint（Claude Agent SDK 呼出し）
├── knowledge_base/                # Phase 1 用バンドルナレッジベース
│   ├── docs/
│   │   ├── agentic-search.md
│   │   └── agentcore-runtime.md
│   └── src/
│       ├── example_agent.py
│       └── utils.ts
├── docs/
│   └── design.md                  # 本ドキュメント
└── infra/                         # AWS CDK プロジェクト (TypeScript)
    ├── bin/
    │   └── infra.ts               # CDK app エントリポイント
    ├── lib/
    │   └── agentcore-rag-stack.ts # メインスタック定義
    ├── lambda/
    │   └── sync_handler.py        # S3 → Session Storage 同期 Lambda
    ├── package.json
    ├── tsconfig.json
    └── cdk.json
```

---

## 5. 実装詳細

### 5.1 `pyproject.toml`

```toml
[project]
name = "agentcore-rag"
version = "0.1.0"
description = "Agentic RAG on AgentCore Runtime with grep/glob-based search"
requires-python = ">=3.13"
dependencies = [
    "bedrock-agentcore",
    "claude-agent-sdk",
    "aws-opentelemetry-distro",
]

[dependency-groups]
dev = [
    "bedrock-agentcore-starter-toolkit",
]
```

### 5.2 `Dockerfile`

```dockerfile
FROM --platform=linux/arm64 ghcr.io/astral-sh/uv:python3.13-trixie

RUN apt-get update && apt-get install -y \
    nodejs \
    npm \
    unzip \
    curl \
    && rm -rf /var/lib/apt/lists/*

# AWS CLI (for s3 sync via InvokeAgentRuntimeCommand)
RUN curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-aarch64.zip" -o /tmp/awscli.zip && \
    unzip -q /tmp/awscli.zip -d /tmp && \
    /tmp/aws/install && \
    rm -rf /tmp/aws /tmp/awscli.zip

RUN groupadd -g 1000 appuser && useradd -u 1000 -g 1000 -m appuser

USER appuser

RUN mkdir ~/.npm-global
RUN npm config set prefix '~/.npm-global'

ENV PATH=~/.npm-global/bin:$PATH \
    NODE_PATH=/home/appuser/.npm-global/lib/node_modules

RUN npm install -g @anthropic-ai/claude-code

ENV CLAUDE_CODE_USE_BEDROCK=1 \
    ANTHROPIC_MODEL=sonnet \
    ANTHROPIC_DEFAULT_SONNET_MODEL=global.anthropic.claude-sonnet-4-6 \
    ANTHROPIC_DEFAULT_HAIKU_MODEL=global.anthropic.claude-haiku-4-5-20251001-v1:0

WORKDIR /app
COPY --chown=1000:1000 pyproject.toml uv.lock ./
RUN uv sync --frozen

COPY --chown=1000:1000 . ./

CMD ["uv", "run", "opentelemetry-instrument", "python", "app.py"]
```

**設計判断**:
- **Node.js + npm**: Claude Agent SDK が内部で Claude Code CLI を使用するため必要
- **AWS CLI**: Lambda からの `InvokeAgentRuntimeCommand` で `aws s3 sync` を実行するために必要
- **`appuser` (UID 1000)**: 非 root 実行
- **OpenTelemetry**: `CMD` で `opentelemetry-instrument` を挟みランタイムを計装
- **モデル ID `global.anthropic.claude-sonnet-4-6`**: cross-region inference profile の ID。末尾に `-v1:0` を付けると無効な ID となる点に注意（[Bedrock inference profiles](https://docs.aws.amazon.com/bedrock/latest/userguide/cross-region-inference.html) 参照）

### 5.3 `.dockerignore`

```
.venv
__pycache__
infra/cdk.out
infra/node_modules
```

**なぜ必要か**: ローカルで `uv run` を実行すると macOS (darwin/arm64) 向けの `.venv` がプロジェクトルートに生成される。Docker の `COPY . ./` でこれがコンテナにコピーされると、`uv sync --frozen` で作られた Linux 向け `.venv` が上書きされ、`Exec format error (os error 8)` でコンテナが起動不能になる。Docker 自身の `.dockerignore` と CDK の `exclude` オプションの二重防御で除外している。

### 5.4 `config.py`

```python
import os

SESSION_STORAGE_MOUNT = os.environ.get("SESSION_STORAGE_MOUNT", "")
KNOWLEDGE_BASE_DIR = (
    os.path.join(SESSION_STORAGE_MOUNT, "knowledge_base")
    if SESSION_STORAGE_MOUNT
    else os.path.join(os.path.dirname(__file__), "knowledge_base")
)

MAX_TURNS = int(os.environ.get("MAX_TURNS", "15"))

SYSTEM_PROMPT = f"""\
You are a knowledge base assistant. ...
"""
```

**設計判断**:
- `SESSION_STORAGE_MOUNT` 環境変数の有無で Phase 1 (バンドル) / Phase 2 (Session Storage) を自動切替
- モデル指定は Dockerfile の環境変数 (`ANTHROPIC_MODEL`, `ANTHROPIC_DEFAULT_SONNET_MODEL`) に委譲し、`config.py` では管理しない
- Bedrock 環境変数 (`CLAUDE_CODE_USE_BEDROCK`) も同様に Dockerfile で設定

### 5.5 `app.py`

```python
import json
import os
from dataclasses import asdict

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from claude_agent_sdk import query, ClaudeAgentOptions

from config import KNOWLEDGE_BASE_DIR, SYSTEM_PROMPT, MAX_TURNS

app = BedrockAgentCoreApp()

@app.entrypoint
async def invocations(payload, context):
    prompt = payload.get("prompt", "")
    ...
    options = ClaudeAgentOptions(
        tools=["Read", "Grep", "Glob"],
        allowed_tools=["Read", "Grep", "Glob"],
        system_prompt=SYSTEM_PROMPT,
        permission_mode="bypassPermissions",
        max_turns=MAX_TURNS,
        cwd=KNOWLEDGE_BASE_DIR,
    )

    async for message in query(prompt=prompt, options=options):
        data = {"type": message.__class__.__name__, **asdict(message)}
        yield {"message": json.dumps(data, ensure_ascii=False)}
```

**設計判断**:
- `async` entrypoint + `yield` によるストリーミングレスポンス（AgentCore Runtime の推奨パターン）
- `tools=["Read", "Grep", "Glob"]` でデフォルトツールのみ有効化。カスタムツールは不要（Claude Code と同じ実装がそのまま使える）
- `cwd=KNOWLEDGE_BASE_DIR` でツールの検索スコープをナレッジベースに制限
- `permission_mode="bypassPermissions"` でサーバーサイド実行時のツール承認をスキップ
- Write / Bash ツールは有効化しない → ナレッジベースの改変を防止

### 5.6 `infra/lambda/sync_handler.py`

Lambda の全文はリポジトリの `infra/lambda/sync_handler.py` を参照。

**設計判断**:
- `_ensure_session()`: `invoke_agent_runtime` で軽量プロンプトを送信し、セッションが存在しない場合は作成。`invoke_agent_runtime_command` は既存セッションに対してのみ動作するため、この前処理が必要
- `--delete` フラグ: S3 側で削除されたファイルを Session Storage からも削除し、完全な同期を維持
- 統一セッション ID: Lambda 環境変数 `SESSION_ID` に固定値を設定。ユーザークエリ側も同じ ID を使用することで、同一 Session Storage を共有
- **boto3 バンドル**: Lambda ランタイム標準搭載の boto3 には `invoke_agent_runtime_command` が含まれていないため、最新の boto3 (`>=1.42.80`) を Lambda コードに同梱している（[boto3 bedrock-agentcore リファレンス](https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore-control/client/update_agent_runtime.html)）

---

## 6. CDK によるデプロイ

### 6.1 従来手法からの移行理由

当初は `bedrock-agentcore-starter-toolkit` (`agentcore configure` → `agentcore launch`) でデプロイしていた。CDK への移行理由:

1. **インフラの一元管理**: S3 バケット、EventBridge、Lambda、AgentCore Runtime を単一のスタックで定義・デプロイできる
2. **再現性**: `cdk synth` で CloudFormation テンプレートを事前確認でき、環境差異がなくなる
3. **CI/CD 統合**: `cdk deploy` をパイプラインに組み込めるため、コード変更に連動したデプロイが容易
4. **Session Storage の自動設定**: starter toolkit ではデプロイ後に手動で API を叩く必要があったが、CDK Custom Resource で自動化

### 6.2 CDK スタック構成

参考: [har1101/claude-agent-handson CDK スタック](https://github.com/har1101/claude-agent-handson/blob/main/claude-agent-cdk/lib/ambient-agent-stack.ts)

#### 使用パッケージ

```json
{
  "dependencies": {
    "@aws-cdk/aws-bedrock-agentcore-alpha": "^2.244.0-alpha.0",
    "@cdklabs/deploy-time-build": "^0.1.2",
    "aws-cdk-lib": "^2.245.0",
    "constructs": "^10.5.0"
  }
}
```

| パッケージ | なぜ使うのか |
|---|---|
| `@aws-cdk/aws-bedrock-agentcore-alpha` | AgentCore Runtime の L2 construct。`agentcore.Runtime` で Runtime リソースを型安全に定義できる |
| `@cdklabs/deploy-time-build` | CodeBuild を使ってデプロイ時に Docker イメージをビルド・ECR にプッシュする。ローカルに Docker が不要で、ARM64 イメージも確実にビルドできる。旧パッケージ `deploy-time-build` は deprecated |
| `aws-cdk-lib` | CDK v2 本体 |

#### 主要リソースと設計判断

##### (1) S3 バケット

```typescript
const kbBucket = new s3.Bucket(this, "KnowledgeBaseBucket", {
  bucketName: `agentcore-rag-kb-${this.account}`,
  eventBridgeEnabled: true,  // S3 → EventBridge 通知を有効化
  removalPolicy: cdk.RemovalPolicy.DESTROY,
  autoDeleteObjects: true,
});
```

**なぜ `eventBridgeEnabled: true`**: S3 イベント通知の従来方式（S3 Notification → SNS/SQS）ではなく、EventBridge 経由にすることで、イベントパターンのフィルタリング（prefix 指定など）が柔軟にでき、ターゲットの追加も容易になる。

##### (2) コンテナイメージビルド

```typescript
const agentImage = new ContainerImageBuild(this, "AgentImage", {
  directory: path.join(__dirname, "..", ".."), // プロジェクトルート
  platform: Platform.LINUX_ARM64,
  exclude: ["infra/cdk.out", "infra/node_modules", ".venv"],
});
```

**なぜ `ContainerImageBuild`**: `DockerImageAsset` はローカルで Docker ビルドを行うが、`ContainerImageBuild` は AWS CodeBuild 上でビルドする。メリット:
- ローカルに Docker Desktop が不要
- ARM64 イメージを x86 マシンからでも確実にビルドできる（CodeBuild の ARM インスタンスを使用）
- CI/CD 環境でも Docker-in-Docker 問題を回避

**なぜ `exclude` が必要か**:
- `infra/cdk.out`: CDK のシンセサイズ出力。Docker コンテキストに含めると `cdk.out/asset.xxx/infra/cdk.out/...` と再帰コピーが発生し `ENAMETOOLONG` エラーになる
- `infra/node_modules`: CDK の依存パッケージ。不要かつ巨大
- `.venv`: ローカルで `uv run` すると macOS 向けバイナリの `.venv` が生成される。これがコンテナにコピーされると `Exec format error` でコンテナが起動不能になる（5.3 節参照）

##### (3) AgentCore Runtime

```typescript
const runtime = new agentcore.Runtime(this, "Runtime", {
  runtimeName: "agentcore_rag",
  agentRuntimeArtifact: agentcore.AgentRuntimeArtifact.fromEcrRepository(
    agentImage.repository, agentImage.imageTag,
  ),
  networkConfiguration: agentcore.RuntimeNetworkConfiguration.usingPublicNetwork(),
  environmentVariables: {
    SESSION_STORAGE_MOUNT: "/mnt/session",
    S3_BUCKET: kbBucket.bucketName,
  },
});
```

**なぜ Cognito を使わないのか**: 参考にした[ハンズオン CDK スタック](https://github.com/har1101/claude-agent-handson/blob/main/claude-agent-cdk/lib/ambient-agent-stack.ts)では EventBridge → API Destination 経由で AgentCore Runtime を呼び出すため、HTTP リクエストの認証に Cognito M2M (Client Credentials) が必要だった。本プロジェクトでは EventBridge → Lambda → boto3 (`invoke_agent_runtime`) で呼び出すため、IAM 認証で十分であり Cognito は不要。

**なぜ `authorizerConfiguration` を省略しているか**: `@aws-cdk/aws-bedrock-agentcore-alpha` の `Runtime` construct はデフォルトで IAM 認証を使用する。明示的に指定しなくても IAM ベースのアクセス制御が適用される。

##### (4) Session Storage の有効化 (Custom Resource)

```typescript
new cr.AwsCustomResource(this, "EnableSessionStorage", {
  installLatestAwsSdk: true,
  onCreate: {
    service: "bedrock-agentcore-control",
    action: "UpdateAgentRuntime",
    parameters: {
      agentRuntimeId: runtime.agentRuntimeId,
      agentRuntimeArtifact: { containerConfiguration: { containerUri } },
      roleArn: runtime.role.roleArn,
      networkConfiguration: { networkMode: "PUBLIC" },
      filesystemConfigurations: [
        { sessionStorage: { mountPath: "/mnt/session" } },
      ],
    },
    physicalResourceId: cr.PhysicalResourceId.of("session-storage-config"),
  },
  // ... onUpdate も同様
  policy: cr.AwsCustomResourcePolicy.fromStatements([
    new iam.PolicyStatement({
      actions: ["bedrock-agentcore:UpdateAgentRuntime"],
      resources: [runtime.agentRuntimeArn],
    }),
    new iam.PolicyStatement({
      actions: ["iam:PassRole"],
      resources: [runtime.role.roleArn],
    }),
  ]),
});
```

**なぜ Custom Resource が必要か**: Session Storage の `filesystemConfigurations` は CloudFormation のリソースプロパティとしてサポートされておらず、`AWS::BedrockAgentCore::Runtime` に `addPropertyOverride` しても `Unsupported property` エラーになる。そのため、Runtime 作成後に `UpdateAgentRuntime` API を Custom Resource 経由で呼び出す必要がある。

参考: [Amazon Bedrock AgentCore Runtime にSession Storage が追加されました](https://dev.classmethod.jp/articles/bedrock-agentcore-runtime-session-storage/)

**`AwsCustomResource` を使う際のハマりポイント**:

| 問題 | 原因と対処 |
|------|-----------|
| `Package @aws-sdk/client-bedrockagentcorecontrol does not exist` | `service` に PascalCase (`BedrockAgentCoreControl`) を指定すると、CDK が `bedrockagentcorecontrol`（ハイフンなし）に変換してしまう。**ケバブケース `bedrock-agentcore-control` で指定**すれば正しいパッケージ `@aws-sdk/client-bedrock-agentcore-control` が解決される |
| 上記パッケージが CDK Lambda の SDK バージョンに含まれない | `installLatestAwsSdk: true` を指定し、デプロイ時に最新 SDK をインストールさせる。JS SDK v3 には v3.1021.0 以降で含まれている（[リリースノート](https://github.com/aws/aws-sdk-js-v3/releases/tag/v3.1021.0)） |
| `iam:PassRole` エラー | `UpdateAgentRuntime` に `roleArn` を渡すため、Custom Resource の Lambda にも `iam:PassRole` 権限が必要 |
| `UpdateAgentRuntime` の必須パラメータ | `filesystemConfigurations` だけでなく `agentRuntimeArtifact`, `roleArn`, `networkConfiguration` も**必須**。省略すると API エラーになる |

##### (5) Lambda: S3 同期ハンドラー

```typescript
const syncFn = new lambda.Function(this, "SyncHandler", {
  functionName: "agentcore-rag-sync",
  runtime: lambda.Runtime.PYTHON_3_13,
  handler: "sync_handler.handler",
  code: lambda.Code.fromAsset(path.join(__dirname, "..", "lambda")),
  timeout: cdk.Duration.minutes(5),
  memorySize: 256,
  logGroup: syncLogGroup,
  environment: { ... },
});
```

**なぜ `logRetention` ではなく `logGroup` を使うのか**: `aws-cdk-lib` v2.245+ で `FunctionOptions#logRetention` は deprecated になった。代わりに明示的に `logs.LogGroup` を作成し `logGroup` プロパティで指定する。

**なぜ Lambda に最新 boto3 をバンドルするのか**: Lambda Python 3.13 ランタイムに標準搭載されている boto3 は `invoke_agent_runtime_command` API をサポートしていない。`InvokeAgentRuntimeCommand` は比較的新しい API であり、**boto3 >= 1.42.80** が必要。`uv pip install boto3 --target infra/lambda/` でバンドルしている。

参考: [boto3 bedrock-agentcore InvokeAgentRuntimeCommand](https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore-control/client/update_agent_runtime.html)

##### (6) Lambda の IAM ポリシー

```typescript
syncFn.addToRolePolicy(
  new iam.PolicyStatement({
    actions: [
      "bedrock-agentcore:InvokeAgentRuntime",
      "bedrock-agentcore:InvokeAgentRuntimeCommand",
    ],
    resources: [
      runtime.agentRuntimeArn,
      `${runtime.agentRuntimeArn}/*`,
    ],
  }),
);
```

**なぜワイルドカード `/*` が必要か**: `InvokeAgentRuntime` API は Runtime ARN のサブリソース `/runtime-endpoint/DEFAULT` に対して呼び出される。Runtime ARN のみを指定すると `AccessDeniedException` になるため、`arn:...:runtime/xxx/*` のワイルドカードも必要。

##### (7) EventBridge ルール

```typescript
const rule = new events.Rule(this, "S3SyncRule", {
  eventPattern: {
    source: ["aws.s3"],
    detailType: ["Object Created", "Object Deleted"],
    detail: {
      bucket: { name: [kbBucket.bucketName] },
      object: { key: [{ prefix: s3Prefix }] },
    },
  },
});

rule.addTarget(new targets.CloudWatchLogGroup(eventLog));
rule.addTarget(new targets.LambdaFunction(syncFn, { retryAttempts: 2 }));
```

**なぜ CloudWatch Logs ターゲットも追加しているか**: デバッグ用。S3 イベントの生データを `/agentcore-rag/s3-events` ロググループに記録し、Lambda が期待通りのイベントを受信しているか確認できる。

---

## 7. デプロイ手順

### 前提条件

- AWS CLI 設定済み (credentials, region)
- Node.js >= 18
- uv (Python パッケージマネージャ)

### 手順

```bash
# 1. uv.lock の生成（初回のみ）
uv lock

# 2. Lambda に最新 boto3 をバンドル
uv pip install "boto3>=1.42.80" --target infra/lambda/

# 3. CDK Bootstrap（アカウント・リージョンにつき初回のみ）
cd infra
npm install
npx cdk bootstrap

# 4. デプロイ
npx cdk deploy

# 5. ナレッジベースを S3 にアップロード
aws s3 sync ../knowledge_base/ s3://agentcore-rag-kb-<ACCOUNT_ID>/knowledge_base/

# 6. 動作確認
# Session Storage の中身を確認
python3 -c "
import boto3
client = boto3.client('bedrock-agentcore', region_name='ap-northeast-1')
response = client.invoke_agent_runtime_command(
    agentRuntimeArn='<RUNTIME_ARN>',
    runtimeSessionId='agentcore-rag-shared-session-00000001',
    qualifier='DEFAULT',
    contentType='application/json',
    accept='application/vnd.amazon.eventstream',
    body={'command': '/bin/bash -c \"ls -laR /mnt/session/knowledge_base/\"', 'timeout': 30},
)
for event in response.get('stream', []):
    if 'chunk' in event:
        chunk = event['chunk']
        if 'contentDelta' in chunk:
            delta = chunk['contentDelta']
            if delta.get('stdout'):
                print(delta['stdout'], end='')
"
```

参考: [Bedrock AgentCore Runtime で直接コマンドを実行する](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-execute-command.html)

---

## 8. 設計上の注意事項

| 項目 | 詳細 |
|------|------|
| セッション ID の統一 | Lambda とユーザークエリで同じ固定セッション ID（33文字以上）を使用すること。異なる ID を使うと別セッション（別 Session Storage）となり、同期したファイルが見えない |
| Session Storage 上限 | 1GB/session。大量ドキュメントの場合は S3 prefix のフィルタリングや分割を検討 |
| Session Storage 保持期間 | アイドル 14日間。14日以上アクセスがない場合はデータ消失。定期的な sync で回避可能 |
| セキュリティ | `tools` で Read/Grep/Glob のみ許可、Write/Bash は無効。`cwd` でスコープを制限 |
| `InvokeAgentRuntimeCommand` の前提 | 既存セッションに対してのみ動作。Lambda は必ず `invoke_agent_runtime` でセッション確保を先に行う |
| セッションの再起動 | コンテナイメージ更新後、既存セッションは旧イメージで稼働し続ける。`stop_runtime_session` API で明示停止し、次回呼出し時に新イメージで再起動させる必要がある |
| `.venv` の混入防止 | `.dockerignore` + CDK `exclude` で二重防御。ローカルの `.venv` がコンテナに混入すると `Exec format error` で起動不能になる |
| モデル ID の形式 | cross-region inference profile の ID は `global.anthropic.claude-sonnet-4-6` のように指定する。末尾に `-v1:0` を付けると無効になるケースがある。`aws bedrock list-inference-profiles` で正確な ID を確認すること |

---

## 9. 参考リンク

### AWS ドキュメント
- [Amazon Bedrock AgentCore Runtime](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime.html)
- [AgentCore Runtime でコマンドを実行する (InvokeAgentRuntimeCommand)](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-execute-command.html)
- [Bedrock cross-region inference](https://docs.aws.amazon.com/bedrock/latest/userguide/cross-region-inference.html)
- [boto3 bedrock-agentcore-control リファレンス](https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore-control/client/update_agent_runtime.html)
- [AWS SDK for JavaScript v3 リリースノート (v3.1021.0)](https://github.com/aws/aws-sdk-js-v3/releases/tag/v3.1021.0)

### CDK / IaC
- [@aws-cdk/aws-bedrock-agentcore-alpha (npm)](https://www.npmjs.com/package/@aws-cdk/aws-bedrock-agentcore-alpha)
- [@cdklabs/deploy-time-build (npm)](https://www.npmjs.com/package/@cdklabs/deploy-time-build)
- [参考 CDK スタック: har1101/claude-agent-handson](https://github.com/har1101/claude-agent-handson/blob/main/claude-agent-cdk/lib/ambient-agent-stack.ts)

### 技術記事（日本語）
- [Amazon Bedrock AgentCore Runtime に Session Storage が追加されました - DevelopersIO](https://dev.classmethod.jp/articles/bedrock-agentcore-runtime-session-storage/)
- [Claude Code が RAG を捨てた理由 - Zenn](https://zenn.dev/acntechjp/articles/c1296f425baf03)
- [RAGを構築して満足してた私へ。Claude Code開発者はもう捨てていた - Zenn](https://zenn.dev/kenimo49/articles/rag-vs-agentic-search)
- [ClaudeはなぜRAGを捨てたのか？ - Zenn](https://zenn.dev/manntera/articles/f3017ecba9c9c1)
- [RAG パイプラインを捨てて claude -p に Grep させた - Zenn](https://zenn.dev/kok1eeeee/articles/knowledge-chatbot-agentic-search)

### 技術記事（英語）
- [Why Grep Beat Embeddings in Our SWE-Bench Agent](https://jxnl.co/writing/2025/09/11/why-grep-beat-embeddings-in-our-swe-bench-agent-lessons-from-augment/)
- [Keyword Search is All You Need](https://signals.aktagon.com/articles/2026/02/keyword-search-is-all-you-need-achieving-rag-level-performance-without-vector-databases-using-agentic-tool-use/)
- [Claude Code Doesn't Index Your Codebase. Here's What It Does Instead](https://vadim.blog/claude-code-no-indexing)

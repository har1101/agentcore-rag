# Amazon Bedrock AgentCore Runtime

## 概要

AgentCore Runtime は、AI エージェントをデプロイ・実行するためのサーバーレス実行環境。microVM ベースで、セキュリティ・スケーリング・インフラ管理を自動化する。

## 主な特徴

- **フレームワーク非依存**: LangGraph, CrewAI, Claude Agent SDK など任意のフレームワークをサポート
- **モデル非依存**: Claude, GPT, Gemini, Llama など任意のモデルを使用可能
- **長時間実行**: 最大8時間の連続実行をサポート
- **高速コールドスタート**: microVM の最適化により素早い起動
- **セッション分離**: 各実行が完全に隔離

## Session Storage

2026年3月にパブリックプレビューとして追加された機能。

### 特徴
- セッション停止・再開をまたいでファイルシステムの状態を永続化
- マウントパスを指定するだけで利用可能
- 標準的な Linux ファイルシステム操作をサポート

### 制限
- セッションあたり最大 1GB
- アイドル状態で 14日間のデータ保持
- 各セッションのデータは完全に分離

### 設定方法

```bash
aws bedrock-agentcore-control update-agent-runtime \
  --agent-runtime-id <id> \
  --filesystem-configurations '[{
    "sessionStorage": { "mountPath": "/mnt/session" }
  }]'
```

## デプロイ方法

### agentcore starter toolkit (Python)

```bash
pip install bedrock-agentcore-starter-toolkit
agentcore configure -e app.py
agentcore deploy
```

### AgentCore CLI (Node.js)

```bash
npm install -g @aws/agentcore
agentcore create my-agent
agentcore deploy
```

# Claude 会話履歴引き継ぎの検討メモ

## 結論

AgentCore Runtime 上で Claude の会話履歴を invocation をまたいで引き継ぎたい場合、最優先で採るべき案は `query() + resume`。

理由:

- 現在の構成は `InvokeAgentRuntime` ごとに `app.py` が 1 回走り、Claude を 1 回呼んで終わる
- この構造では `ClaudeSDKClient` の「接続を開いたまま複数回やり取りする」強みが活きにくい
- Claude SDK 側には `resume` / `continue_conversation` があり、会話再開を比較的素直に実装できる
- 外部で `session_id` を持つことを許容するなら、実装はかなり単純になる

---

## 現状整理

今の実装では、AgentCore の `runtimeSessionId` は固定していても、Claude SDK 側の会話履歴は毎回新規。

つまり、以下は別物:

- **AgentCore session**
  - Session Storage や runtime セッション共有のための ID
- **Claude session**
  - Claude SDK の会話履歴再開用 ID

この 2 つを混同しないことが重要。

---

## Claude SDK の観点

Claude SDK の docs / 型定義上、以下が確認できる:

- `query()` は基本的に stateless 寄り
- interactive / stateful な用途には `ClaudeSDKClient` が案内されている
- 一方で `ClaudeAgentOptions` には以下がある
  - `continue_conversation`
  - `resume`
  - `session_id`

このため、invocation をまたぐ会話継続という要件に対しては、まず `query() + resume` が最小の解になる。

参考:

- <https://platform.claude.com/docs/en/agent-sdk/python>
- <https://platform.claude.com/docs/en/agent-sdk/streaming-output>

---

## 案A: `query() + resume`

### 概要

各リクエストで `query()` を呼ぶ方式は維持しつつ、前回の Claude `session_id` を次回リクエストで渡して再開する。

### イメージ

- 初回リクエスト
  - Claude 新規会話開始
  - `result.session_id` を返す
- 次回リクエスト
  - `claude_session_id` を payload に含める
  - `ClaudeAgentOptions(resume=..., continue_conversation=True)` を使う

### メリット

- 現在の AgentCore 実装に最も自然
- コード量が少ない
- 実現可能性が高い
- invocation 境界との相性が良い

### デメリット

- クライアントまたは API 層で `claude_session_id` を保持する必要がある
- 1 invocation 内で複数 follow-up を処理する用途にはあまり向かない

### 使うべきケース

- まず会話継続を確実に実現したい
- バックエンドをシンプルに保ちたい
- follow-up は次の HTTP リクエストで十分

---

## 案B: `ClaudeSDKClient`

### 概要

`query()` ではなく `ClaudeSDKClient` を使い、接続を張った状態で対話的にメッセージをやり取りする。

### メリット

- 同一 invocation 内で複数回 `query()` 的な送信をしやすい
- interrupt やより双方向な制御に向いている
- 将来的にチャットサーバー的な構造へ伸ばしやすい

### デメリット

- invocation をまたいで `ClaudeSDKClient` インスタンスを保持できるわけではない
- 結局、会話履歴継続には外部 session 管理が必要
- 今の要件だと構造が重い
- `query()+resume` より複雑

### 使うべきケース

- 1 invocation 内で複数 follow-up を処理したい
- interactive な会話制御を強くしたい
- 将来 WebSocket / SSE ベースの会話エンジンにしたい

---

## Web アプリ化した場合の整理

CLI ベースの確認段階と、`API Gateway + Lambda + AgentCore` の本格アプリ構成では話が少し変わる。

本格アプリでは API 層が state を持てるため、`claude_session_id` の管理が自然になる。

### 状態の分離

Web アプリでは、状態を以下のように分けて持つのが自然:

- **UI session**
  - ブラウザ上のチャット ID
- **アプリ会話状態**
  - `chat_id` ごとのメタデータ
- **AgentCore session**
  - Session Storage 用
- **Claude session**
  - Claude 会話履歴再開用

### 推奨構成

- フロント
  - `chat_id` を持つ
- API Gateway / Lambda
  - `chat_id` から DB を見て `claude_session_id` を取得
  - AgentCore に `prompt` と `claude_session_id` を渡す
  - 返ってきた `session_id` を保存
- AgentCore
  - `query()+resume` で Claude を再開

### この構成の意味

Web アプリになると、CLI のように手で session ID を渡さなくてよくなる。
ただし、会話継続の本質は依然として「外部で Claude session ID を管理すること」であり、`ClaudeSDKClient` に変えれば自動解決するわけではない。

参考:

- <https://speakerdeck.com/takuyay0ne/20260228-jaws-beginner-kansai>
- <https://speakerdeck.com/iidaxs/aibuildersday-track-a-iidaxs>

---

## 比較まとめ

### 実現優先なら

`query()+resume`

- シンプル
- 実装コストが低い
- 今の AgentCore 構造に合う
- Web アプリ化しても継続しやすい

### 将来の対話制御まで重視するなら

`ClaudeSDKClient`

- 1 invocation 内の柔軟な会話制御に向く
- ただし会話履歴継続の主手段にはならない
- まずは不要

---

## 最終推奨

現時点では以下が最適:

1. まず `query()+resume` で会話履歴継続を実現する
2. `claude_session_id` は外部で保持する
3. Web アプリ化する場合は `chat_id -> claude_session_id` を API 層 / DB で管理する
4. `ClaudeSDKClient` は将来的に、同一 invocation 内の複雑な対話制御が必要になったときに再検討する

---

## 補足

参考資料:

- Claude Agent SDK Python
  - <https://platform.claude.com/docs/en/agent-sdk/python>
- Claude Agent SDK Streaming Output
  - <https://platform.claude.com/docs/en/agent-sdk/streaming-output>
- AgentCore を Web アプリに組み込む構成の参考
  - <https://speakerdeck.com/takuyay0ne/20260228-jaws-beginner-kansai>
  - <https://speakerdeck.com/iidaxs/aibuildersday-track-a-iidaxs>

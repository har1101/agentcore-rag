# Agentic Search: grep/glob ベースの検索アプローチ

## 概要

Agentic Search は、ベクトル検索（RAG）の代替として、LLM エージェントが grep や glob などの基本的なファイル検索ツールを使って情報を探索するアプローチである。

## 背景

Claude Code の開発者 Boris Cherny によると、初期の Claude Code は RAG とローカルベクトル DB を使用していたが、grep/glob による Agentic Search が「すべてを大幅に上回った」とのこと。

## 従来の RAG との比較

### ベクトル検索 RAG の課題
- エンベディングモデルのコストが発生
- インデックスの構築・更新が必要（コードベースは頻繁に変更される）
- セマンティック検索はコード検索に最適化されていない
- インデックスが古くなると検索精度が低下

### Agentic Search の利点
- **コスト削減**: エンベディング不要
- **常に最新**: ファイルシステムを直接検索するため、インデックスの陳腐化がない
- **透明性**: 検索過程が完全に可視化される
- **シンプルなインフラ**: ベクトル DB が不要
- **精度**: 正確なキーワードマッチにより、false positive が少ない

## 検索戦略

効果的な Agentic Search は以下の戦略を採用する:

1. **構造把握**: `glob` でファイル構造を理解
2. **キーワード検索**: `grep` で関連するファイルと行を特定
3. **詳細読取**: `read` で特定のファイルの内容を確認
4. **反復的な絞り込み**: 必要に応じて検索パターンを変えて再検索

## 参考文献

- "Why Grep Beat Embeddings in Our SWE-Bench Agent" (Jason Liu)
- "Keyword Search is All You Need" (Aktagon)
- "Claude Code Doesn't Index Your Codebase" (Vadim)

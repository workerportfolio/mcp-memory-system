"""
設定・ルール定義
"""

from pathlib import Path

# ===== パス設定 =====
DB_PATH = "data/memory.db"
LOG_DIR = "data/logs"

# ===== 自動提案設定 =====
MAX_AUTO_SUGGESTIONS = 5
AUTO_SUGGEST_MIN_CONFIDENCE = 'HIGH'
MISC_WARNING_THRESHOLD = 20  # misc_operationが20件超えたら警告

# ===== type優先順位 =====
TYPE_PRIORITY = {
    'decision': 1,
    'config': 2,
    'procedure': 3,
    'design_note': 4
}

# ===== 類似検索の制約 =====
# search_similar_key() は英数字トークンがある場合のみ発動
# 日本語のみのタイトルは KEYWORD_MAP に寄せる設計
# → KEYWORD_MAP の充実が重要

# ===== 憲法キー用キーワードマップ =====
KEYWORD_MAP_CONSTITUTION = {
    "architecture_overview": ["アーキテクチャ", "全体構成", "構成", "レイヤ", "三層", "モノリス", "マイクロサービス"],
    "component_responsibility": ["責務", "役割", "分担", "境界", "コンポーネント", "サービス分割"],
    "data_flow_overview": ["データフロー", "処理フロー", "同期", "非同期", "イベント", "キュー"],
    "integration_overview": ["連携", "外部API", "Webhook", "バッチ連携", "ETL"],
    "security_baseline": ["セキュリティ", "最小権限", "暗号化", "TLS", "監査", "コンプライアンス"],
    "availability_slo": ["可用性", "冗長化", "HA", "SLO", "SLA", "RTO", "RPO", "DR"],
    "scalability_strategy": ["スケール", "水平", "垂直", "オートスケール", "負荷分散"],
    "observability_strategy": ["可観測性", "監視", "ログ", "メトリクス", "トレース", "APM", "相関ID"],
    "data_retention_policy": ["保持", "retention", "保存期間", "年数"],
    "release_governance": ["リリース", "変更管理", "レビュー", "承認", "ロールバック", "段階リリース"]
}

# ===== 運用キー用キーワードマップ =====
KEYWORD_MAP_OPERATION = {
    # インフラ/基盤
    "os_baseline": ["OS", "Linux", "Windows", "パッチ", "アップデート", "EOL", "LTS"],
    "server_sizing_policy": ["サーバ", "スペック", "CPU", "vCPU", "メモリ", "RAM", "ディスク", "サイジング"],
    "storage_layout_policy": ["パーティション", "LVM", "RAID", "マウント", "IOPS", "暗号化ディスク"],
    "capacity_management": ["容量管理", "使用率", "逼迫", "閾値", "増設", "リサイズ"],
    "virtualization_platform": ["仮想化", "VM", "ハイパーバイザ", "コンテナ", "Docker", "Kubernetes"],
    "network_topology": ["ネットワーク", "VPC", "サブネット", "セグメント", "NAT", "プロキシ"],
    "port_and_protocol_policy": ["ポート", "protocol", "TCP", "UDP", "公開", "内部通信", "mTLS", "443", "80"],
    "dns_and_routing_policy": ["DNS", "名前解決", "FQDN", "CNAME", "ルート", "route"],
    "load_balancing_policy": ["ロードバランサ", "LB", "負荷分散", "L7", "L4", "ヘルスチェック"],
    "firewall_policy": ["ファイアウォール", "FW", "WAF", "ACL", "allow", "deny", "ホワイトリスト"],
    "access_control_policy": ["アクセス制御", "踏み台", "bastion", "管理経路", "IP制限"],
    "backup_and_restore_procedure": ["バックアップ", "リストア", "復元", "スナップショット", "復旧訓練"],
    
    # 認証・秘密情報
    "authentication_method": ["認証", "authentication", "SSO", "OIDC", "SAML", "MFA"],
    "authorization_model": ["認可", "authorization", "権限", "RBAC", "ABAC", "ロール"],
    "secret_management": ["シークレット", "secret", "APIキー", "鍵", "KMS", "Vault", "ローテーション"],
    "certificate_policy": ["証明書", "certificate", "TLS", "mTLS", "CA", "期限", "更新"],
    
    # アプリ/API/設定
    "configuration_management": ["設定", "config", "環境変数", ".env", "設定ファイル", "パラメータ"],
    "api_contract_policy": ["API", "契約", "OpenAPI", "スキーマ", "バージョン", "互換性"],
    "timeout_and_retry_policy": ["タイムアウト", "timeout", "リトライ", "retry", "バックオフ"],
    "rate_limit_policy": ["レート制限", "rate limit", "スロットリング", "429", "クォータ"],
    "error_handling_policy": ["エラー処理", "例外", "exception", "失敗時", "フォールバック"],
    "logging_policy": ["ログ", "ログレベル", "相関ID", "PII", "マスキング", "監査ログ"],
    
    # データ/DB
    "database_schema_policy": ["スキーマ", "テーブル", "DDL", "主キー", "外部キー", "マイグレーション"],
    "indexing_strategy": ["インデックス", "index", "実行計画", "チューニング"],
    "transaction_policy": ["トランザクション", "ACID", "分離レベル", "ロック", "SAGA"],
    "data_archival_procedure": ["削除", "アーカイブ", "ジョブ", "手順"],
    
    # テスト/運用
    "test_strategy": ["テスト", "単体", "結合", "E2E", "回帰", "負荷試験"],
    "ci_cd_pipeline_policy": ["CI", "CD", "パイプライン", "ビルド", "自動化", "署名"],
    "deployment_procedure": ["デプロイ", "Blue/Green", "カナリア", "ローリング", "ロールバック"],
    "monitoring_and_alerting_rules": ["監視", "メトリクス", "アラート", "閾値", "ダッシュボード"],
    "incident_response_runbook": ["障害", "インシデント", "一次対応", "エスカレーション", "ポストモーテム"]
}

# ===== type別キーワード =====
# 性質分類
# decision:決定（以下を内包）
# (決定内訳) config:設定  procedure:手順  design_note:設計メモ・思想
TYPE_KEYWORDS = {
    'decision': ['決定', '判断', '選択', '採用', '廃止', 'に統一', 'を使用'],
    'config': ['設定', 'パラメータ', '値', 'configure', 'タイムアウト', 'ポート'],
    'procedure': ['手順', 'プロセス', 'フロー', 'やり方', '実施', '実行'],
    'design_note': ['原則', '方針', '哲学', '考え方', '禁止', '必ず']
}


def generate_key_by_map(title: str, content: str) -> tuple[str, str] | None:
    """
    KEYWORD_MAPによるマッチングのみ（DB不要）
    Args:
        title: タイトル
        content: 内容
    Returns:
        (key, key_layer) or None
    """
    text = (title + " " + content).lower()
    
    # 1. 憲法キーマッチ
    for key, patterns in KEYWORD_MAP_CONSTITUTION.items():
        if any(p.lower() in text for p in patterns):
            return (key, "constitution")
    
    # 2. 運用キーマッチ
    for key, patterns in KEYWORD_MAP_OPERATION.items():
        if any(p.lower() in text for p in patterns):
            return (key, "operation")
    
    # 3. マッチせず
    return None

def judge_confidence(title: str, content: str, type_: str) -> str:
    """
    confidence判定
    Args:
        title: タイトル
        content: 内容
        type_: 分類
    Returns:
        'HIGH' | 'MED' | 'LOW'
    """
    text = (title + " " + content).lower()
    
    # 断定表現
    decisive_patterns = ['に統一', 'を使用', '必ず', '禁止', 'してはならない']
    has_decisive = any(p in text for p in decisive_patterns)
    
    # 具体値（数字や固有名詞）
    import re
    has_concrete = bool(re.search(r'\d+|[A-Z][a-z]+', title + content))
    
    # HIGH判定
    if has_decisive and has_concrete and type_ in ['decision', 'config']:
        return 'HIGH'
    
    # MED判定
    if '推奨' in text or type_ == 'procedure':
        return 'MED'
    
    # LOW判定
    return 'LOW'
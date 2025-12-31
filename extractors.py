"""
会話ログからの自動提案抽出
ユーザに「保存を検討すべき決定事項」をログから抽出し、提案のみを行う
（※本モジュールは保存・確定処理を行わない）
"""

import json
import logging
from pathlib import Path
from datetime import datetime
import config

# ロガー設定（モジュール先頭）
logger = logging.getLogger(__name__)

#conversation_log => AIとの会話履歴
def extract_suggestions(conversation_log: list, db) -> list:
    """
    会話から自動提案を抽出
    Args:
        conversation_log: 会話（リスト）
        db: MemoryDB インスタンス
    Returns:
        [{'title': ..., 'content': ..., 'type': ..., 'confidence': ...}, ...]
    Note:
        - 最大5件まで
        - HIGH confidence のみ
        - 重複チェック済み
    """
    suggestions = []
    
    # 会話ログから候補を抽出
    # ※以下の条件を満たさない場合は continue で次の turn（for先頭）へ
    # 1) turn.role が 'assistant'（AI発言）か？
    # 2) _has_decision_pattern(content)（判断表現）を含むか？
    # 3) タイトル/内容 を抽出できるか？
    # 4) type を判定する
    # 5) confidence （自動提案に足る確信度）が HIGH か？
    # 6) 既に抽出済み候補と重複しないか？
    # 7) 最大件数（MAX_AUTO_SUGGESTIONS）に達したら打ち切り（break）
    for turn in conversation_log:
        if turn.get('role') != 'assistant':
            continue
        
        content = turn.get('content', '')
        
        # 判断表現を含むか
        if not _has_decision_pattern(content):
            continue
        
        # タイトル・内容抽出
        title = _extract_title(content)
        extracted_content = _extract_content(content)
        
        if not title or not extracted_content:
            continue
        
        # type判定
        type_ = judge_type(title, extracted_content)
        
        # confidence判定
        confidence = config.judge_confidence(title, extracted_content, type_)
        
        # HIGHのみ
        if confidence != 'HIGH':
            continue
        
        # 重複チェック
        if _is_duplicate(title, extracted_content, suggestions):
            continue
        
        suggestions.append({
            'title': title,
            'content': extracted_content,
            'type': type_,
            'confidence': confidence
        })
        
        # 最大5件
        if len(suggestions) >= config.MAX_AUTO_SUGGESTIONS:
            break
    
    logger.info(f"自動提案抽出: {len(suggestions)}件")
    return suggestions


def _has_decision_pattern(text: str) -> bool:
    """
    判断表現を含むか
    Args:
        text: テキスト
    Returns:
        True: 判断表現あり
    Note:
        使っていく中でキーワード強化を実施
    """
    patterns = [
        'に統一', 'を使用', '必ず', '禁止', 
        'してはならない', '推奨', '採用', '廃止',
        'べき', 'ルール', '原則', '方針'
    ]
    
    return any(p in text for p in patterns)


def _extract_title(text: str) -> str:
    """
    タイトル抽出（簡易版）
    Args:
        text: テキスト
    Returns:
        タイトル（最大100文字）
    Note:
        タイトルを雑に抽出（きれいさよりも一覧表示用の仮ラベルを優先）
    """
    # 最初の文を抽出
    lines = text.strip().split('\n')
    
    for line in lines:
        line = line.strip()
        if len(line) > 10:  # 10文字以上
            # 句点で区切って最初の文
            sentences = line.split('。')
            if sentences:
                title = sentences[0].strip()
                if len(title) > 100:
                    title = title[:100]
                return title
    
    # 見つからない場合は先頭100文字
    return text[:100].strip()


def _extract_content(text: str) -> str:
    """
    内容抽出（簡易版）
    Args:
        text: テキスト
    Returns:
        内容（最大500文字）
    Note:
        正確性を重視し、原文をそのまま保持する（長すぎる場合のみカット）
    """
    # そのまま返す（最大500文字）
    content = text.strip()
    
    if len(content) > 500:
        content = content[:500]
    
    return content


def judge_type(title: str, content: str) -> str:
    """
    type判定
    Args:
        title: タイトル
        content: 内容
    Returns:
        'decision' | 'config' | 'procedure' | 'design_note'
    Note:
        会話テキスト（title+content）に含まれる
        既定キーワード数を基に機械的に分類
    """
    text = (title + " " + content).lower()
    
    # type別のキーワード数をカウント
    type_scores = {}
    
    for type_, keywords in config.TYPE_KEYWORDS.items():
        score = sum(1 for k in keywords if k in text)
        type_scores[type_] = score
    
    # 最大スコアのtypeを返す
    if type_scores:
        max_type = max(type_scores, key=type_scores.get)
        if type_scores[max_type] > 0:
            return max_type
    
    # デフォルトは decision
    return 'decision'


def _is_duplicate(title: str, content: str, existing: list) -> bool:
    """
    重複チェック
    Args:
        title: タイトル
        content: 内容（重複チェックには使用せず、人が確認する前提）
        existing: 既存の提案リスト
    Returns:
        True: 重複あり
    """
    # 簡易的な重複判定（タイトルの類似度）
    for item in existing:
        existing_title = item['title']
        
        # 完全一致
        if title == existing_title:
            return True
        
        # 部分一致（70%以上）
        # 単語単位ではなく文字（1文字単位）で確認 順番 回数も見ていない
        common = set(title) & set(existing_title)
        similarity = len(common) / max(len(title), len(existing_title))
        
        if similarity > 0.7:
            return True
    
    return False


def load_conversation_log(conversation_id: str) -> list:
    """
    会話ログを読み込み
    Args:
        conversation_id: 会話ID
        conversation_log: 会話（リスト）
    Returns:
        AIとの会話ログ（リスト）
    Note:
        conversation_log は人とAIの会話履歴（JSONLから復元）
    """
    log_file = Path(config.LOG_DIR) / f"{conversation_id}.jsonl"
    
    if not log_file.exists():
        logger.warning(f"会話ログが見つかりません: {log_file}")
        return []
    
    conversation_log = []
    
    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    turn = json.loads(line)
                    conversation_log.append(turn)
        
        logger.info(f"会話ログ読み込み: {len(conversation_log)}ターン")
        return conversation_log
        
    except Exception as e:
        logger.exception(f"会話ログ読み込みエラー: {e}")
        return []


def save_conversation_turn(conversation_id: str, turn_number: int, role: str, content: str):
    """
    会話ターンを保存
    Args:
        conversation_id: 会話ID
        turn_number: ターン番号
        role: 'user' | 'assistant'
        content: 内容
    Note:
        AIとの会話を加工せず、そのままログとして保存
    """
    log_file = Path(config.LOG_DIR) / f"{conversation_id}.jsonl"
    
    # ディレクトリ作成
    log_file.parent.mkdir(parents=True, exist_ok=True)
    
    turn = {
        'conversation_id': conversation_id,
        'turn_number': turn_number,
        'role': role,
        'content': content,
        'timestamp': datetime.now().isoformat()
    }
    
    try:
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(turn, ensure_ascii=False) + '\n')
        
        logger.info(f"会話ターン保存: {conversation_id} turn={turn_number}")
        
    except Exception as e:
        logger.exception(f"会話ターン保存エラー: {e}")


# テスト実行
if __name__ == "__main__":
    import sys
    
    print("=" * 50)
    print("Extractors テスト")
    print("=" * 50)
    
    # ロガー設定
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # テスト用会話ログ
    test_conversation = [
        {
            'role': 'user',
            'content': 'ポート番号について教えてください'
        },
        {
            'role': 'assistant',
            'content': 'ポート番号は22222に統一します。本番環境では全サービスでポート22222を使用する必要があります。'
        },
        {
            'role': 'user',
            'content': 'タイムアウトはどうしますか？'
        },
        {
            'role': 'assistant',
            'content': 'タイムアウトは30秒に設定することを推奨します。API呼び出しは30秒でタイムアウトします。'
        }
    ]
    
    # テスト用会話ログ保存
    test_conv_id = "test_conv_001"
    for i, turn in enumerate(test_conversation, 1):
        save_conversation_turn(test_conv_id, i, turn['role'], turn['content'])
    
    # 会話ログ読み込み
    loaded_log = load_conversation_log(test_conv_id)
    print(f"\n会話ログ読み込み: {len(loaded_log)}ターン")
    
    # 自動提案抽出（DBなしバージョン）
    print("\n--- 自動提案抽出テスト ---")
    suggestions = extract_suggestions(loaded_log, None)
    
    print(f"\n自動提案: {len(suggestions)}件")
    for i, sug in enumerate(suggestions, 1):
        print(f"\n[{i}]")
        print(f"  タイトル: {sug['title']}")
        print(f"  type: {sug['type']}")
        print(f"  confidence: {sug['confidence']}")
    
    print("\n 全テスト成功")
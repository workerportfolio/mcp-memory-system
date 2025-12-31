"""
SQLiteデータベース操作
DBへの処理を管理
"""

import sqlite3
import logging
import re
from datetime import datetime
from pathlib import Path
import config

# ロガー設定（モジュール先頭）
logger = logging.getLogger(__name__)

class MemoryDB:
    """メモリデータベース管理クラス"""
    def __init__(self, db_path=None):
        """初期化"""
        self.db_path = db_path or config.DB_PATH
        # 無ければディレクトリ作成処理
        # 親ディレクトリを作成
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        
        # ログディレクトリも作成
        Path(config.LOG_DIR).mkdir(parents=True, exist_ok=True)
    
    def _get_connection(self):
        """
        データベース接続取得（メソッド内ローカル用）
        Returns:
            sqlite3.Connection
        Note:
            - SQLite特有の概念・制約制御（MCPでSqliteを安全(並列)利用するため）
            - isolation_level=None で完全手動トランザクション 
                => 自動トランザクションの無効化
            - BEGIN/COMMIT/ROLLBACKを明示的に制御
                => BEGIN IMMEDIATE(即座に書き込みロック取得)実行のため
            - WAL（Write-Ahead Logging） は init_db() で設定済み（毎回は不要）
                => 読み取りと書き込みを同時に成立させやすくする（read/write concurrency 向上）
        """
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        
        # 毎回必要な設定のみ
        # DBロック待ち時間：ロックされている間の排他をエラーとして扱う時間 
        # 外部キー制約：テーブル間の整合性チェック機能ON
        conn.execute("PRAGMA busy_timeout=3000;")
        conn.execute("PRAGMA foreign_keys=ON;")
        
        return conn
    
    def _safe_rollback(self, conn):
        """
        エラー発生時のトランザクション破棄（やり直し）
        Args:
            conn: データベース接続
        Note:
            エラー発生時に仕掛かり中の変更をすべて取り消し、整合性を保ちます。
            トランザクションが開始されていない状態で呼び出しても例外を投げないよう、
            内部で例外を握り潰しています（安全な終了を保証）。
        """
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
    
    def init_db(self):
        """テーブル作成・初期化"""
        logger.info("=" * 50)
        logger.info("データベース初期化")
        logger.info("=" * 50)
        
        # 親ディレクトリ作成
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        
        # 規約に統一: isolation_level=None
        # Sqlite思想に沿い、分かりやすくするため_get_connectionと分けて明記
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        
        try:
            # WAL設定＋確認ログ
            mode = conn.execute("PRAGMA journal_mode=WAL;").fetchone()[0]
            logger.info(f" journal_mode={mode}")
            
            if mode.lower() != 'wal':
                logger.warning(f" WAL設定失敗: {mode} (環境制約の可能性)")
            
            conn.execute("PRAGMA busy_timeout=3000;")
            conn.execute("PRAGMA foreign_keys=ON;")
            
            # 規約に統一: BEGIN/COMMIT
            conn.execute("BEGIN")
            
            cursor = conn.cursor()
            
            # [1] memory_items テーブル
            # 決定事項テーブル
            logger.info("[1] memory_items テーブル作成中...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS memory_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT NOT NULL,
                    key_layer TEXT NOT NULL CHECK(key_layer IN ('constitution', 'operation')),
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    type TEXT NOT NULL CHECK(type IN ('decision', 'config', 'procedure', 'design_note')),
                    status TEXT NOT NULL CHECK(status IN ('draft', 'final', 'obsolete')),
                    is_canonical BOOLEAN NOT NULL DEFAULT FALSE,
                    supersedes INTEGER,
                    confidence TEXT NOT NULL CHECK(confidence IN ('HIGH', 'MED', 'LOW')),
                    conversation_id TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (supersedes) REFERENCES memory_items(id)
                );
            """)
            
            # [2] UNIQUE制約（key, key_layer）
            # canonical final における (key, key_layer) 二重登録防止制約
            logger.info("[2] UNIQUE制約作成中...")
            cursor.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_canonical_key 
                ON memory_items(key, key_layer) 
                WHERE is_canonical=TRUE AND status='final';
            """)
            
            # [3] その他インデックス
            logger.info("[3] インデックス作成中...")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_memory_key ON memory_items(key);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_memory_key_layer ON memory_items(key_layer);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_memory_type ON memory_items(type);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_memory_status ON memory_items(status);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_memory_conversation ON memory_items(conversation_id);")
            
            # [4] memory_sources テーブル
            # 決定事項の根拠テーブル
            logger.info("[4] memory_sources テーブル作成中...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS memory_sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_id INTEGER NOT NULL,
                    conversation_id TEXT NOT NULL,
                    turn_number INTEGER NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (memory_id) REFERENCES memory_items(id) ON DELETE CASCADE
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_sources_memory ON memory_sources(memory_id);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_sources_conversation ON memory_sources(conversation_id);")
            
            # [5] FTS5テーブル
            logger.info("[5] FTS5テーブル作成中...")
            cursor.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_items_fts USING fts5(
                    title,
                    content,
                    content=memory_items,
                    content_rowid=id,
                    tokenize='porter unicode61'
                );
            """)
            
            # [6] トリガー（FTS5同期のみ、updated_atは除外 IF NOT EXISTSで既存DB保護）
            # ==========================================================
            # メモ：全文検索 (FTS5) 同期設定
            # ==========================================================
            # [特性1: 高速全文検索]
            #   SQLite標準の LIKE 検索とは異なり、転置インデックス（索引）を作成することで
            #   大量のテキストデータから特定の単語を高速で検索可能にする。
            #
            # [特性2: 仮想テーブルと実テーブルの分離]
            #   FTS5は仮想テーブル(_fts)として独立しているため、
            #   本体テーブル(memory_items)とのデータ整合性を保つための同期処理が必要。
            #
            # [特性3: 抽出・同期の自動化]
            #   以下のトリガーにより、DML(INSERT/UPDATE/DELETE)発生時に
            #   検索用インデックスを自動更新する。　削除→INSERTの方が安全
            #   （external content方式では直接UPDATEは不整合・エラーの原因になりやすいため）
            # ==========================================================
            logger.info("[6] FTS5同期トリガー作成中...")
            
            # 安全な同期トリガー（削除→再INSERT方式）
            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS memory_fts_insert AFTER INSERT ON memory_items BEGIN
                    INSERT INTO memory_items_fts(rowid, title, content)
                    VALUES (new.id, new.title, new.content);
                END;
            """)
            
            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS memory_fts_delete AFTER DELETE ON memory_items BEGIN
                    INSERT INTO memory_items_fts(memory_items_fts, rowid, title, content)
                    VALUES('delete', old.id, old.title, old.content);
                END;
            """)
            
            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS memory_fts_update AFTER UPDATE ON memory_items BEGIN
                    INSERT INTO memory_items_fts(memory_items_fts, rowid, title, content)
                    VALUES('delete', old.id, old.title, old.content);
                    
                    INSERT INTO memory_items_fts(rowid, title, content)
                    VALUES (new.id, new.title, new.content);
                END;
            """)
            
            # 規約に統一: conn.execute("COMMIT")
            conn.execute("COMMIT")
            
            logger.info("データベース初期化完了")
            
        except Exception:
            # 規約に統一: _safe_rollback()
            self._safe_rollback(conn)
            logger.exception("DB初期化エラー")
            raise
        
        finally:
            conn.close()
    
    def suggest_key(self, title: str, content: str, type_: str) -> tuple[str, str]:
        """
        key提案（確実な判定 → 補助的推定 → 最終退避  key未決状態の回避）
        Args:
            title: タイトル
            content: 内容
            type_: 分類
        Returns:
            (key, key_layer)
        """
        # 1. KEYWORD_MAPマッチ（確実な判定）
        result = config.generate_key_by_map(title, content)
        if result:
            logger.info(f"key判定: KEYWORD_MAP一致 → {result}")
            return result
        
        # 2. 類似検索（情報量チェック＋補助的推定）
        if self._has_enough_info(title):
            similar = self.search_similar_key(title, limit=3)
            if similar:
                logger.info(f"key判定: 類似検索一致 → ({similar[0]['key']}, {similar[0]['key_layer']})")
                return (similar[0]['key'], similar[0]['key_layer'])
        
        # 3. misc_operation（最終退避）
        logger.info("key判定: fallback → misc_operation")
        return ("misc_operation", "operation")
    
    def _has_enough_info(self, text: str) -> bool:
        """
        情報量チェック（類似検索に値するか）
        Returns:
            True: 英数字トークンが2個以上ある（FTS5で検索可能）
            False: 英数字トークンが不足 → misc直行
        Note:
            ★修正: search_similar_key()と条件を統一（英数字のみ判定）
            カタカナ判定を削除し、設計の一貫性を確保
        """
        # 英数字トークン抽出
        tokens = re.findall(r"[A-Za-z0-9_]+", text)
        
        # 短すぎるトークン除外（2文字以上）
        tokens = [t for t in tokens if len(t) >= 2]
        
        # 英数字トークンが2個以上 → OK
        return len(tokens) >= 2
    
    def search_similar_key(self, text: str, limit: int) -> list:
        """
        既存keyとの類似検索
        Args:
            text: 検索テキスト
            limit: 取得件数
        Returns:
            [{'key': ..., 'key_layer': ..., 'score': ...}, ...]
        Note:
            英数字トークンがない場合は空リストを返す（日本語のみは類似検索スキップ）
        """
        # 英数字のみ抽出
        tokens = re.findall(r"[A-Za-z0-9_]+", text)
        
        # 短すぎるトークン除外（2文字以上）
        tokens = [t for t in tokens if len(t) >= 2]
        
        if not tokens:
            logger.info(f"類似検索スキップ: 英数字トークンなし（text={text}）")
            return []
        
        # プレフィックス検索（最大3トークン）
        query = ' OR '.join([f"{t}*" for t in tokens[:3]])
        
        conn = self._get_connection()
        
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT m.key, m.key_layer,
                       MIN(bm25(memory_items_fts)) AS score
                FROM memory_items m
                JOIN memory_items_fts ON m.id = memory_items_fts.rowid
                WHERE memory_items_fts MATCH ?
                  AND m.is_canonical = TRUE
                  AND m.status = 'final'
                GROUP BY m.key, m.key_layer
                ORDER BY score ASC
                LIMIT ?
            """, (query, limit))
            
            results = [
                {'key': row[0], 'key_layer': row[1], 'score': row[2]}
                for row in cursor.fetchall()
            ]
            
            logger.info(f"類似検索結果: {len(results)}件（query={query}）")
            return results
            
        except Exception:
            logger.exception(f"FTS MATCH error (query={query})")
            return []
        
        finally:
            conn.close()
    
    def insert_draft(self, title: str, content: str, type_: str, conversation_id: str = None):
        """
        draft保存
        Args:
            title: タイトル
            content: 内容
            type_: 分類
            conversation_id: 会話識別子
        Returns:
            draft_id
        Note:
            自動判定されたkeyを、承認前のドラフト（仮）として保存する
        """
        # key, key_layer 自動生成
        key, key_layer = self.suggest_key(title, content, type_)
        
        # confidence自動判定
        confidence = config.judge_confidence(title, content, type_)
        
        conn = self._get_connection()
        
        try:
            conn.execute("BEGIN")
            
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO memory_items (key, key_layer, title, content, type, status, confidence, conversation_id)
                VALUES (?, ?, ?, ?, ?, 'draft', ?, ?)
            """, (key, key_layer, title, content, type_, confidence, conversation_id))
            
            draft_id = cursor.lastrowid
            conn.execute("COMMIT")
            
            logger.info(f"draft保存完了: ID={draft_id}, key={key}, key_layer={key_layer}")
            return draft_id
            
        except Exception:
            self._safe_rollback(conn)
            logger.exception("draft保存エラー")
            raise
        
        finally:
            conn.close()
    
    def finalize_item(self, draft_id: int):
        """
        draft → final（canonical化）
        Args:
            draft_id: draftのID
        Returns:
            draft_id
        Note:
            canonical を更新するため BEGIN IMMEDIATE を使用し、先に書き込みロックを確保して競合（レース）を回避する
            更新対象は実テーブル（memory_items）。FTS5（memory_items_fts）は 
            external content + 同期トリガにより delete→insert 方式で追従更新される
        """
        conn = self._get_connection()
        
        try:
            conn.execute("BEGIN IMMEDIATE")
            
            cursor = conn.cursor()
            
            # draftの情報取得
            cursor.execute("SELECT * FROM memory_items WHERE id=?", (draft_id,))
            draft = cursor.fetchone()
            
            if not draft:
                raise ValueError(f"draft_id={draft_id} が見つかりません")
            
            if draft['status'] != 'draft':
                raise ValueError(f"ID={draft_id} は既にfinal化されています")
            
            # 既存canonicalを無効化
            cursor.execute("""
                SELECT id FROM memory_items 
                WHERE key=? AND key_layer=? AND is_canonical=TRUE AND status='final'
            """, (draft['key'], draft['key_layer']))
            
            existing = cursor.fetchone()
            
            if existing:
                cursor.execute("""
                    UPDATE memory_items 
                    SET status='obsolete', is_canonical=FALSE, updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                """, (existing['id'],))
            
            # draftをfinal化
            cursor.execute("""
                UPDATE memory_items 
                SET status='final', is_canonical=TRUE, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
            """, (draft_id,))
            
            conn.execute("COMMIT")
            
            logger.info(f"final化完了: ID={draft_id}, key={draft['key']}")
            return draft_id
            
        except Exception:
            self._safe_rollback(conn)
            logger.exception("final化エラー")
            raise
        
        finally:
            conn.close()
    
    def supersede_item(self, old_id: int, new_title: str, new_content: str, conversation_id: str):
        """
        既存判断を更新（同一key維持）
        Args:
            old_id: 更新元のID
            new_title: 新しいタイトル
            new_content: 新しい内容
            conversation_id: 更新した会話のID（必須）
        Returns:
            new_id: 新版のID
        Note:
            keyとkey_layerは旧版から引き継ぐ
            conversation_idは新しい会話IDを使用
            contentに元の会話IDを自動追記
            BEGIN IMMEDIATEを最初に実行してレース回避
        """
        conn = self._get_connection()
        
        try:
            # 最初に BEGIN IMMEDIATE（ロック確保）
            conn.execute("BEGIN IMMEDIATE")
            
            cursor = conn.cursor()
            
            # ロック下で最新状態を取得
            cursor.execute("SELECT * FROM memory_items WHERE id=?", (old_id,))
            old_item = cursor.fetchone()
            
            if not old_item:
                raise ValueError(f"old_id={old_id} が見つかりません")
            
            if not old_item['is_canonical'] or old_item['status'] != 'final':
                raise ValueError(f"ID={old_id} は canonical final ではありません")
            
            # keyとkey_layerは旧版から引き継ぐ
            key = old_item['key']
            key_layer = old_item['key_layer']
            
            # confidenceは再判定
            new_confidence = config.judge_confidence(new_title, new_content, old_item['type'])
            
            # ★修正: 更新履歴を正確な表現に
            update_date = datetime.now().strftime('%Y-%m-%d')
            old_conv_id = old_item['conversation_id'] or '不明'
            
            update_note = f"\n\n---\n（更新履歴）\n{update_date}: 元会話ID={old_conv_id} を会話ID={conversation_id} で更新"
            final_content = new_content + update_note
            
            # 1. 旧版を obsolete
            cursor.execute("""
                UPDATE memory_items 
                SET status='obsolete', is_canonical=FALSE, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
            """, (old_id,))
            
            # 2. 新版を final（conversation_idは新しい会話ID）
            cursor.execute("""
                INSERT INTO memory_items 
                (key, key_layer, title, content, type, status, is_canonical, supersedes, confidence, conversation_id, updated_at)
                VALUES (?, ?, ?, ?, ?, 'final', TRUE, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (key, key_layer, new_title, final_content, old_item['type'], old_id, new_confidence, conversation_id))
            
            new_id = cursor.lastrowid
            
            conn.execute("COMMIT")
            
            logger.info(f"更新完了: old_id={old_id} → new_id={new_id}, key={key}（維持）, 元会話ID={old_conv_id}")
            return new_id
            
        except Exception:
            self._safe_rollback(conn)
            logger.exception("更新エラー")
            raise
        
        finally:
            conn.close()


# テスト実行
if __name__ == "__main__":
    import sys
    
    print("=" * 50)
    print("MemoryDB テスト")
    print("=" * 50)
    
    # ロガー設定
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    try:
        # 初期化
        db = MemoryDB()
        db.init_db()
        
        print("\n 全テスト成功")
        
    except Exception as e:
        print(f"\n テストエラー: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
"""
MCPツールサーバ実装
※APIサーバ
"""

import asyncio    #非同期処理ライブラリ
import json
import logging
from datetime import datetime

# MCPサーバーの本体  ツールやリソースの管理、AIクライアントとの通信を制御
from mcp.server import Server
# 通信経路の確立  標準入出力（stdin/stdout）を介して、Claude Desktop等のクライアントと接続するための仕組み
from mcp.server.stdio import stdio_server
# データ型の定義。AIに提供する「ツールの定義（Tool）」や
# AIに返す「テキスト回答の形式（TextContent）」を指定するために使用。
from mcp.types import Tool, TextContent

import config
from memory_db import MemoryDB
from extractors import extract_suggestions, load_conversation_log, save_conversation_turn

# MCPサーバ起動時初期化処理
# ロガー設定
# MCPサーバー（stdio方式）=> print()はNG 標準出力はstdout => loggingで統一する
logging.basicConfig(
    level=logging.INFO,
    # 日時 - モジュール名 - レベル - メッセージ
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('data/logs/mcp_server.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# MCPサーバーインスタンス
# Server は MCP の「本体」オブジェクト
#  - AI（Claude）が最初に接続してくる窓口
#  - MCP が提供できる機能（ツール）一覧をここに集約する
#  - AI はこの Server に登録されたツールだけを認識・利用できる
#
# 処理の流れ（概要）:
#  1. AI が MCP サーバの存在を認識
#  2. Server インスタンスに登録されたツール一覧を取得
#  3. AI が「使いたい」と判断したツールを指定して呼び出す
#  4. 対応する Python 関数が実行される
#     - 通信が発生する場合、await を使って「待ち時間」を効率化
app = Server("memory-mcp")

# データベース初期化
db = MemoryDB()
db.init_db()

# 現在の会話ID（グローバル変数）
# 会話が始まっていないことの明示化
current_conversation_id = None

# デコレータで記載
# MCPサーバーがAIに「利用可能なツール一覧」を通知するための定義
@app.list_tools()
async def list_tools() -> list[Tool]:
    """利用可能なツール一覧"""
    # AIへのツール仕様書
    # JSON Schemaに準拠
    # description => AIへのプロンプトに近い概念（各プロパティ精度を上げたい場合はチューニング）
    return [
        Tool(
            name="memory_save_draft",
            description="記録候補をdraftとして保存（承認前の仮保存）",
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "タイトル"},
                    "content": {"type": "string", "description": "内容"},
                    "type": {
                        "type": "string",
                        "enum": ["decision", "config", "procedure", "design_note"],
                        "description": "分類"
                    }
                },
                "required": ["title", "content", "type"]
            }
        ),
        Tool(
            name="memory_finalize",
            description="draftをfinalに確定（canonical化）",
            inputSchema={
                "type": "object",
                "properties": {
                    "draft_id": {"type": "integer", "description": "draftのID"}
                },
                "required": ["draft_id"]
            }
        ),
        Tool(
            name="memory_supersede",
            description="既存判断を更新（同一key維持）",
            inputSchema={
                "type": "object",
                "properties": {
                    "old_id": {"type": "integer", "description": "更新元のID"},
                    "new_title": {"type": "string", "description": "新しいタイトル"},
                    "new_content": {"type": "string", "description": "新しい内容"}
                },
                "required": ["old_id", "new_title", "new_content"]
            }
        ),
        Tool(
            name="memory_search",
            description="メモリ検索（canonical finalのみ）",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "検索クエリ"},
                    "type_filter": {
                        "type": "string",
                        "enum": ["decision", "config", "procedure", "design_note"],
                        "description": "type絞り込み（任意）"
                    },
                    "limit": {"type": "integer", "description": "取得件数（デフォルト10）"}
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="memory_get_recent_context",
            description="最近の文脈取得（新チャット開始時に使用）",
            inputSchema={
                "type": "object",
                "properties": {
                    "layer": {
                        "type": "string",
                        "enum": ["constitution", "operation", "all"],
                        "description": "取得するレイヤ（デフォルト: operation）"
                    },
                    "limit": {"type": "integer", "description": "取得件数（デフォルト10）"}
                },
                "required": []
            }
        ),
        Tool(
            name="memory_list_suggestions",
            description="自動提案リスト取得",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="memory_list_drafts",
            description="draft一覧取得",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="memory_open_source",
            description="記録のソース確認（会話ログ参照）",
            inputSchema={
                "type": "object",
                "properties": {
                    "memory_id": {"type": "integer", "description": "メモリID"}
                },
                "required": ["memory_id"]
            }
        )
    ]

# =========================
# MCPプロトコル規約メモ（重要）
# =========================
#  MCPとして成立させるための必須インタフェース
# - list_tools() でAIに“使えるツールの仕様”を提示する（ツール一覧=仕様書）
# - AIはその仕様に基づき、実行したいツール名(name)と引数(arguments)を決める
# - call_tool() は「AIが選んだ name」を受け取り、対応する関数を呼ぶ
# - arguments は JSON object で渡され、Python側では dict として受け取る
# - 戻り値は MCP規約上 list[TextContent] にする（単体TextContentではなく必ずリスト）
# - async は MCP SDK/サーバ実行モデルに合わせた形（await可能な設計にしておく）
# - AI にはJSON形式で返さないといけない（result）
@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """ツール呼び出し"""
    logger.info(f"ツール呼び出し: {name}")
    logger.info(f"引数: {arguments}")
    
    try:
        if name == "memory_save_draft":
            return await save_draft(arguments)
        
        elif name == "memory_finalize":
            return await finalize(arguments)
        
        elif name == "memory_supersede":
            return await supersede(arguments)
        
        elif name == "memory_search":
            return await search(arguments)
        
        elif name == "memory_get_recent_context":
            return await get_recent_context(arguments)
        
        elif name == "memory_list_suggestions":
            return await list_suggestions(arguments)
        
        elif name == "memory_list_drafts":
            return await list_drafts(arguments)
        
        elif name == "memory_open_source":
            return await open_source(arguments)
        
        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
    
    except Exception as e:
        logger.exception(f"ツール実行エラー: {name}")
        return [TextContent(type="text", text=f"エラー: {str(e)}")]


async def save_draft(args: dict) -> list[TextContent]:
    """draft保存"""
    title = args["title"]
    content = args["content"]
    type_ = args["type"]
    
    draft_id = db.insert_draft(title, content, type_, current_conversation_id)
    
    result = {
        "status": "success",
        "draft_id": draft_id,
        "message": f"draft保存完了（ID: {draft_id}）"
    }
    
    return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]


async def finalize(args: dict) -> list[TextContent]:
    """final化"""
    draft_id = args["draft_id"]
    
    db.finalize_item(draft_id)
    
    result = {
        "status": "success",
        "message": f"final化完了（ID: {draft_id}）"
    }
    
    return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]


async def supersede(args: dict) -> list[TextContent]:
    """更新"""
    old_id = args["old_id"]
    new_title = args["new_title"]
    new_content = args["new_content"]
    
    # conversation_id を渡す
    new_id = db.supersede_item(old_id, new_title, new_content, current_conversation_id)
    
    result = {
        "status": "success",
        "old_id": old_id,
        "new_id": new_id,
        "message": f"更新完了（{old_id} → {new_id}）"
    }
    
    return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]


async def search(args: dict) -> list[TextContent]:
    """検索"""
    query = args["query"]
    type_filter = args.get("type_filter")
    limit = args.get("limit", 10)
    
    conn = db._get_connection()
    
    try:
        cursor = conn.cursor()
        
        # 基本クエリ
        sql = """
            SELECT id, key, key_layer, title, content, type, confidence, updated_at
            FROM memory_items
            WHERE is_canonical = TRUE
              AND status = 'final'
        """
        
        params = []
        
        # type絞り込み
        if type_filter:
            sql += " AND type = ?"
            params.append(type_filter)
        
        # タイトル・内容で絞り込み（簡易版）
        sql += " AND (title LIKE ? OR content LIKE ?)"
        params.extend([f"%{query}%", f"%{query}%"])
        
        # 優先順位順にソート
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        
        cursor.execute(sql, params)
        results = cursor.fetchall()
        
        items = []
        for row in results:
            items.append({
                "id": row[0],
                "key": row[1],
                "key_layer": row[2],
                "title": row[3],
                "content": row[4][:200] + "..." if len(row[4]) > 200 else row[4],
                "type": row[5],
                "confidence": row[6],
                "updated_at": row[7]
            })
        
        result = {
            "status": "success",
            "count": len(items),
            "items": items
        }
        
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]
        
    finally:
        conn.close()


async def get_recent_context(args: dict) -> list[TextContent]:
    """最近の文脈取得"""
    layer = args.get("layer", "operation")
    limit = args.get("limit", 10)
    
    conn = db._get_connection()
    
    try:
        cursor = conn.cursor()
        
        # layer絞り込み
        if layer == "all":
            sql = """
                SELECT id, key, key_layer, title, content, type, confidence, updated_at
                FROM memory_items
                WHERE is_canonical = TRUE
                  AND status = 'final'
                ORDER BY updated_at DESC
                LIMIT ?
            """
            params = [limit]
        else:
            sql = """
                SELECT id, key, key_layer, title, content, type, confidence, updated_at
                FROM memory_items
                WHERE is_canonical = TRUE
                  AND status = 'final'
                  AND key_layer = ?
                ORDER BY updated_at DESC
                LIMIT ?
            """
            params = [layer, limit]
        
        cursor.execute(sql, params)
        results = cursor.fetchall()
        
        items = []
        for row in results:
            items.append({
                "id": row[0],
                "key": row[1],
                "key_layer": row[2],
                "title": row[3],
                "content": row[4][:200] + "..." if len(row[4]) > 200 else row[4],
                "type": row[5],
                "confidence": row[6],
                "updated_at": row[7]
            })
        
        result = {
            "status": "success",
            "layer": layer,
            "count": len(items),
            "items": items
        }
        
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]
        
    finally:
        conn.close()


async def list_suggestions(args: dict) -> list[TextContent]:
    """自動提案リスト"""
    if not current_conversation_id:
        result = {
            "status": "success",
            "count": 0,
            "suggestions": [],
            "message": "会話IDが設定されていません"
        }
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]
    
    # 会話ログ読み込み
    conversation_log = load_conversation_log(current_conversation_id)
    
    # 自動提案抽出
    suggestions = extract_suggestions(conversation_log, db)
    
    result = {
        "status": "success",
        "count": len(suggestions),
        "suggestions": suggestions
    }
    
    return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]


async def list_drafts(args: dict) -> list[TextContent]:
    """draft一覧"""
    conn = db._get_connection()
    
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, key, key_layer, title, content, type, confidence, created_at
            FROM memory_items
            WHERE status = 'draft'
            ORDER BY created_at DESC
        """)
        
        results = cursor.fetchall()
        
        items = []
        for row in results:
            items.append({
                "id": row[0],
                "key": row[1],
                "key_layer": row[2],
                "title": row[3],
                "content": row[4][:200] + "..." if len(row[4]) > 200 else row[4],
                "type": row[5],
                "confidence": row[6],
                "created_at": row[7]
            })
        
        result = {
            "status": "success",
            "count": len(items),
            "drafts": items
        }
        
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]
        
    finally:
        conn.close()


async def open_source(args: dict) -> list[TextContent]:
    """ソース確認"""
    memory_id = args["memory_id"]
    
    conn = db._get_connection()
    
    try:
        cursor = conn.cursor()
        
        # メモリ情報取得
        cursor.execute("""
            SELECT key, title, conversation_id
            FROM memory_items
            WHERE id = ?
        """, (memory_id,))
        
        memory = cursor.fetchone()
        
        if not memory:
            result = {
                "status": "error",
                "message": f"ID={memory_id} が見つかりません"
            }
            return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]
        
        # ソース取得
        cursor.execute("""
            SELECT turn_number, role, content, timestamp
            FROM memory_sources
            WHERE memory_id = ?
            ORDER BY turn_number
        """, (memory_id,))
        
        sources = cursor.fetchall()
        
        source_list = []
        for row in sources:
            source_list.append({
                "turn": row[0],
                "role": row[1],
                "content": row[2],
                "timestamp": row[3]
            })
        
        result = {
            "status": "success",
            "memory_id": memory_id,
            "key": memory[0],
            "title": memory[1],
            "conversation_id": memory[2],
            "source_count": len(source_list),
            "sources": source_list
        }
        
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]
        
    finally:
        conn.close()


async def main():
    """MCPサーバー起動"""
    logger.info("=" * 50)
    logger.info("MCPサーバー起動")
    logger.info("=" * 50)
    
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
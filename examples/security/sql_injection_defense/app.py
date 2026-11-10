# -*- coding: utf-8 -*-
"""SQL 注入防御实战示例（Flask + SQLite）。

这个示例只演示“正确的防御写法”，不暴露任何 vulnerable 路由；
`unsafe_query_for_teaching()` 仅用于测试里做对照，说明为什么不能拼接。

运行：
    python app.py
    curl "http://127.0.0.1:5000/users/search?name=alice"
    curl "http://127.0.0.1:5000/users?sort=username&order=asc"
    curl -X POST http://127.0.0.1:5000/login \
         -H "Content-Type: application/json" \
         -d "{\"username\":\"alice\",\"password\":\"alice-pass\"}"

安全点：
1. 所有用户输入都走参数化查询，SQL 结构与数据值分离。
2. 查询使用只读连接，写操作只发生在初始化。
3. ORDER BY 只允许代码内白名单，绝不拼接用户原文。
4. LIKE 查询转义 % 和 _，避免通配符放大。
5. 登录使用参数化查询 + 哈希比较，不返回数据库错误细节。
6. Flask debug=False，避免调试器泄露。
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from flask import Flask, jsonify, request

DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo.db")

# 白名单：列名和排序方向都映射到固定 SQL 片段，用户输入只用于查表
SORT_COLUMNS = {
    "id": "id",
    "username": "username",
    "role": "role",
}
SORT_ORDERS = {
    "asc": "ASC",
    "desc": "DESC",
}


def hash_password(password: str) -> str:
    """示例用 SHA-256；生产环境请使用 bcrypt/argon2 并加盐。"""
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def connect(db_path: str, readonly: bool = False) -> sqlite3.Connection:
    """建立连接；查询默认只读，体现最小权限。"""
    if readonly:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def db_connection(db_path: str, readonly: bool = False):
    """连接上下文：提交并确保关闭（Windows 下不关闭会锁住临时 DB 文件）。"""
    conn = connect(db_path, readonly)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: str) -> None:
    """建表并写入演示数据；只在初始化时使用写连接。"""
    with db_connection(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                role TEXT NOT NULL,
                password_hash TEXT NOT NULL
            )
            """
        )
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if count == 0:
            conn.executemany(
                "INSERT INTO users (username, role, password_hash) "
                "VALUES (?, ?, ?)",
                [
                    ("alice", "user", hash_password("alice-pass")),
                    ("bob", "user", hash_password("bob-pass")),
                    ("admin", "admin", hash_password("admin-pass")),
                ],
            )


def _rows(rows: List[sqlite3.Row]) -> List[Dict[str, Any]]:
    return [dict(row) for row in rows]


def safe_search_users(db_path: str, username: str) -> List[Dict[str, Any]]:
    """安全写法：参数化查询，用户输入只作为数据绑定。"""
    sql = "SELECT id, username, role FROM users WHERE username = ?"
    with db_connection(db_path, readonly=True) as conn:
        rows = conn.execute(sql, (username,)).fetchall()
    return _rows(rows)


def safe_search_like(db_path: str, keyword: str) -> List[Dict[str, Any]]:
    """安全写法：LIKE 仍然参数化，并转义 % / _ 通配符。"""
    escaped = (
        keyword.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
    sql = (
        "SELECT id, username, role FROM users "
        "WHERE username LIKE ? ESCAPE '\\' ORDER BY username"
    )
    with db_connection(db_path, readonly=True) as conn:
        rows = conn.execute(sql, (f"%{escaped}%",)).fetchall()
    return _rows(rows)


def safe_login(db_path: str, username: str,
               password: str) -> Optional[Dict[str, Any]]:
    """安全写法：用户名和密码哈希都作为参数绑定。"""
    sql = (
        "SELECT id, username, role FROM users "
        "WHERE username = ? AND password_hash = ?"
    )
    with db_connection(db_path, readonly=True) as conn:
        row = conn.execute(
            sql, (username, hash_password(password))
        ).fetchone()
    return dict(row) if row else None


def safe_list_users(db_path: str, sort: str, order: str,
                    limit: int, offset: int) -> List[Dict[str, Any]]:
    """安全写法：动态 ORDER BY 只允许白名单映射，绝不拼接用户原文。"""
    column = SORT_COLUMNS.get(sort)
    direction = SORT_ORDERS.get(order)
    if column is None or direction is None:
        raise ValueError("sort/order 不在白名单内")

    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    # 这里唯一的 f-string 使用的是白名单映射后的固定片段，不是用户输入。
    sql = (
        f"SELECT id, username, role FROM users "
        f"ORDER BY {column} {direction} LIMIT ? OFFSET ?"
    )
    with db_connection(db_path, readonly=True) as conn:
        rows = conn.execute(sql, (limit, offset)).fetchall()
    return _rows(rows)


def unsafe_query_for_teaching(db_path: str, username: str) -> List[Dict[str, Any]]:
    """仅用于测试对照：把输入拼进 SQL，展示为什么危险。

    警告：不要把这个函数接到任何 HTTP 路由或生产代码。
    """
    sql = (
        "SELECT id, username, role FROM users "
        f"WHERE username = '{username}'"
    )
    with db_connection(db_path, readonly=True) as conn:
        rows = conn.execute(sql).fetchall()
    return _rows(rows)


def create_app(db_path: Optional[str] = None) -> Flask:
    app = Flask(__name__)
    app.config["DB_PATH"] = db_path or DEFAULT_DB
    app.config["DEBUG"] = False
    init_db(app.config["DB_PATH"])

    @app.errorhandler(sqlite3.Error)
    def handle_db_error(exc: sqlite3.Error):
        # 只记服务端日志，不把 SQL/表名/堆栈返回给客户端
        app.logger.exception("database error: %s", exc)
        return jsonify({"error": "query failed"}), 500

    @app.get("/health")
    def health():
        return jsonify({"ok": True})

    @app.get("/users/search")
    def users_search():
        name = (request.args.get("name") or "").strip()
        if not name:
            return jsonify({"error": "name is required"}), 400
        return jsonify({"users": safe_search_users(
            app.config["DB_PATH"], name)})

    @app.get("/users/search_like")
    def users_search_like():
        keyword = (request.args.get("q") or "").strip()
        if not keyword:
            return jsonify({"error": "q is required"}), 400
        return jsonify({"users": safe_search_like(
            app.config["DB_PATH"], keyword)})

    @app.post("/login")
    def login():
        data = request.get_json(silent=True) or request.form
        username = str(data.get("username") or "").strip()
        password = str(data.get("password") or "")
        if not username or not password:
            return jsonify({"error": "username/password required"}), 400
        user = safe_login(app.config["DB_PATH"], username, password)
        if user is None:
            return jsonify({"error": "invalid credentials"}), 401
        return jsonify({"user": user})

    @app.get("/users")
    def users_list():
        sort = (request.args.get("sort") or "username").strip()
        order = (request.args.get("order") or "asc").strip().lower()
        try:
            limit = int(request.args.get("limit") or 20)
            offset = int(request.args.get("offset") or 0)
        except ValueError:
            return jsonify({"error": "limit/offset must be integers"}), 400
        try:
            users = safe_list_users(
                app.config["DB_PATH"], sort, order, limit, offset)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"users": users})

    return app


if __name__ == "__main__":
    application = create_app()
    print("SQL 注入防御示例：http://127.0.0.1:5000")
    print("  GET  /users/search?name=alice")
    print("  GET  /users/search_like?q=ali")
    print("  GET  /users?sort=username&order=asc")
    print("  POST /login  {\"username\":\"alice\",\"password\":\"alice-pass\"}")
    application.run(host="127.0.0.1", port=5000, debug=False)

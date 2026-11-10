# -*- coding: utf-8 -*-
"""SQL 注入防御示例的自动化验收。

运行：
    python test_sql_injection_defense.py

测试覆盖：
1. 正常查询
2. 经典注入 payload 只被当作数据
3. 对照用的不安全拼接确实会中招（仅测试内使用）
4. 登录绕过失败
5. ORDER BY 白名单拒收非法字段
6. LIKE 通配符被转义
7. DROP TABLE payload 不生效
8. 数据库错误不泄露 SQL 细节
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import (  # noqa: E402
    create_app,
    init_db,
    safe_search_like,
    safe_search_users,
    unsafe_query_for_teaching,
)


class SQLInjectionDefenseTest(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        init_db(self.db_path)
        self.app = create_app(self.db_path)
        self.client = self.app.test_client()

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except FileNotFoundError:
            pass

    def test_normal_search(self):
        users = safe_search_users(self.db_path, "alice")
        self.assertEqual(len(users), 1)
        self.assertEqual(users[0]["username"], "alice")

    def test_sql_injection_payload_is_data(self):
        users = safe_search_users(self.db_path, "' OR '1'='1")
        self.assertEqual(users, [])

        resp = self.client.get("/users/search", query_string={
            "name": "' OR '1'='1"
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["users"], [])

    def test_unsafe_teaching_query_is_vulnerable(self):
        # 只用于对照：不安全拼接会返回全部用户
        users = unsafe_query_for_teaching(self.db_path, "' OR '1'='1")
        self.assertEqual(len(users), 3)

    def test_login_payload_rejected(self):
        payload = {"username": "' OR '1'='1", "password": "anything"}
        resp = self.client.post(
            "/login", data=json.dumps(payload),
            content_type="application/json")
        self.assertEqual(resp.status_code, 401)

        ok = self.client.post(
            "/login",
            data=json.dumps({"username": "alice", "password": "alice-pass"}),
            content_type="application/json")
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.get_json()["user"]["username"], "alice")

    def test_order_by_whitelist(self):
        resp = self.client.get("/users", query_string={
            "sort": "password", "order": "asc"
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("白名单", resp.get_json()["error"])

        ok = self.client.get("/users", query_string={
            "sort": "username", "order": "desc"
        })
        self.assertEqual(ok.status_code, 200)
        names = [u["username"] for u in ok.get_json()["users"]]
        self.assertEqual(names, sorted(names, reverse=True))

    def test_like_wildcards_escaped(self):
        # 未转义时 "%" 会匹配全部；转义后应返回空
        self.assertEqual(safe_search_like(self.db_path, "%"), [])
        self.assertEqual(
            [u["username"] for u in safe_search_like(self.db_path, "ali")],
            ["alice"],
        )

    def test_drop_table_payload_does_not_drop(self):
        safe_search_users(self.db_path, "'; DROP TABLE users--")
        # 表还在，数据还是 3 条
        users = safe_search_users(self.db_path, "admin")
        self.assertEqual(len(users), 1)

    def test_db_error_does_not_leak_sql(self):
        os.unlink(self.db_path)
        resp = self.client.get("/users/search", query_string={"name": "alice"})
        self.assertEqual(resp.status_code, 500)
        body = resp.get_data(as_text=True).lower()
        self.assertIn("query failed", body)
        self.assertNotIn("select", body)
        self.assertNotIn("sqlite", body)
        self.assertNotIn("users", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)

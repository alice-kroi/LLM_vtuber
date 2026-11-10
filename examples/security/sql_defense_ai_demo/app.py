
from flask import Flask, request, jsonify
import sqlite3
from sqlite3 import connect, OperationalError, ProgrammingError
import re

app = Flask(__name__)

# 数据库初始化
def init_db():
    with connect('demo.db') as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users
                     (id INTEGER PRIMARY KEY, name TEXT, email TEXT, age INTEGER)''')
        # 插入一些测试数据
        sample_data = [
            (1, 'Alice', 'alice@example.com', 25),
            (2, 'Bob', 'bob@example.com', 30),
            (3, 'Charlie', 'charlie@example.com', 35),
            (4, 'David', 'david@example.com', 28),
            (5, 'Eve', 'eve@example.com', 22)
        ]
        c.executemany('INSERT OR IGNORE INTO users VALUES (?,?,?,?)', sample_data)
        conn.commit()

# 获取只读连接
def get_readonly_conn():
    # 使用 SQLite URI 模式打开只读连接
    conn = connect('file:demo.db?mode=ro', uri=True)
    return conn

# 获取可写连接
def get_writable_conn():
    conn = connect('demo.db')
    return conn

@app.route('/')
def index():
    return "SQL Defense AI Demo"

@app.route('/users')
def get_users():
    conn = get_readonly_conn()
    c = conn.cursor()
    c.execute('SELECT * FROM users')
    users = c.fetchall()
    conn.close()
    return jsonify([{'id': u[0], 'name': u[1], 'email': u[2], 'age': u[3]} for u in users])

@app.route('/users/search')
def search_users():
    q = request.args.get('q', '')
    # 转义 LIKE 特殊字符：先转义反斜杠，再转义 % 和 _
    q_escaped = q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    conn = get_readonly_conn()
    c = conn.cursor()
    # 使用参数化查询防止 SQL 注入，并指定 ESCAPE 字符：告诉数据库反斜杠后的字符是普通字符而非通配符
    c.execute('SELECT * FROM users WHERE name LIKE ? ESCAPE \'\\\' OR email LIKE ? ESCAPE \'\\\'', 
              (f'%{q_escaped}%', f'%{q_escaped}%'))
    users = c.fetchall()
    conn.close()
    return jsonify([{'id': u[0], 'name': u[1], 'email': u[2], 'age': u[3]} for u in users])

@app.route('/users/<int:id>')
def get_user(id):
    conn = get_readonly_conn()
    c = conn.cursor()
    c.execute('SELECT * FROM users WHERE id = ?', (id,))
    user = c.fetchone()
    conn.close()
    if user:
        return jsonify({'id': user[0], 'name': user[1], 'email': user[2], 'age': user[3]})
    return jsonify({'error': 'User not found'}), 404

@app.route('/users/sorted')
def get_users_sorted():
    # 白名单验证
    allowed_sort_fields = ['id', 'name', 'email', 'age']
    sort_by = request.args.get('sort_by', 'id')
    
    # 非白名单值回退到默认字段 id
    if sort_by not in allowed_sort_fields:
        sort_by = 'id'
    
    # 构建查询时直接使用白名单验证后的字段名，避免 SQL 拼接
    conn = get_readonly_conn()
    c = conn.cursor()
    
    # 直接使用白名单验证后的字段，而不是拼接用户输入
    # 我们可以用一个字典映射来实现安全的排序
    if sort_by == 'id':
        c.execute('SELECT * FROM users ORDER BY id')
    elif sort_by == 'name':
        c.execute('SELECT * FROM users ORDER BY name')
    elif sort_by == 'email':
        c.execute('SELECT * FROM users ORDER BY email')
    elif sort_by == 'age':
        c.execute('SELECT * FROM users ORDER BY age')
    
    users = c.fetchall()
    conn.close()
    return jsonify([{'id': u[0], 'name': u[1], 'email': u[2], 'age': u[3]} for u in users])

if __name__ == '__main__':
    init_db()
    app.run(debug=False)

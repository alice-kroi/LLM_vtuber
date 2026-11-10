# SQL 注入与 XSS 防御检查清单

> 用途：代码审查、开发自检、AI 安全经验召回
> 来源：OWASP SQL Injection Prevention Cheat Sheet、OWASP XSS Prevention Cheat Sheet
> 原则：默认不信任任何外部输入；结构、数据、权限、输出上下文分开处理

## 一、SQL 注入防御检查清单

### 1. 查询构造

- [ ] 所有 SQL 使用参数化查询/预编译语句，SQL 结构与数据值彻底分离。
- [ ] 禁止把用户输入、URL 参数、Header、Cookie、第三方 API 返回值拼进 SQL 字符串。
- [ ] ORM/MyBatis 等框架必须使用绑定参数；MyBatis 用 `#{}`，禁止用 `${}` 拼接用户输入。
- [ ] 动态表名、列名、排序字段、`ASC/DESC` 只能来自代码内写死的白名单，不能来自外部输入。
- [ ] `LIKE` 查询要处理 `%`、`_` 通配符；不要把用户输入直接拼进 `LIKE '%...%'`。
- [ ] 批量插入、存储过程、动态报表、二阶注入（数据先入库、后被拼接）单独检查。
- [ ] 预编译顺序正确：先构造带占位符的纯骨架 SQL，再绑定数据；不能在预编译前拼接数据。

### 2. 权限与运行

- [ ] 应用数据库账号最小权限：只授予业务必需的 SELECT/INSERT/UPDATE，禁止 DDL、删库、读文件、执行系统命令。
- [ ] 生产库与开发库隔离；应用账号不能访问无关库表。
- [ ] 数据库错误信息不回显给终端用户：不暴露 SQL、表名、列名、堆栈、数据库版本。
- [ ] 日志脱敏：不记录完整 SQL 参数中的密码、Token、身份证号等敏感数据。
- [ ] 关闭不必要的数据库危险功能，例如 `xp_cmdshell`、`LOAD_FILE`、`INTO OUTFILE`。

### 3. 检测与兜底

- [ ] WAF 只能作为补充，不能替代参数化查询和最小权限。
- [ ] SAST/DAST/依赖扫描覆盖 SQL 拼接、ORM 误用、旧框架漏洞。
- [ ] 代码审查时搜索危险模式：`f"SELECT`、`+ "SELECT"`、`${}`、`execute("...%s" %`。
- [ ] 关键查询有自动化测试，恶意输入只被当作普通字符串。

### 4. 安全示例

```python
# Python sqlite3：结构固定 + 数据绑定
import sqlite3

def find_user(username: str):
    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE users (name TEXT, role TEXT)")
    cursor.execute("INSERT INTO users VALUES (?, ?)", ("admin", "admin"))
    cursor.execute("SELECT name, role FROM users WHERE name = ?", (username,))
    row = cursor.fetchone()
    conn.close()
    return row
```

```java
// Java：PreparedStatement，禁止字符串拼接
String sql = "SELECT name, role FROM users WHERE name = ?";
try (PreparedStatement ps = conn.prepareStatement(sql)) {
    ps.setString(1, username);
    try (ResultSet rs = ps.executeQuery()) {
        // 处理结果
    }
}
```

### 5. 验收用例

- [ ] 输入 `' OR '1'='1`：只作为普通用户名查询，不返回全部数据。
- [ ] 输入 `'; DROP TABLE users--`：不执行删表，不产生语法错误泄露。
- [ ] 输入 `%`、`_`：LIKE 查询不会变成全表扫描或异常匹配。
- [ ] 动态排序参数传入 `password` 等非白名单字段：被拒绝。

## 二、XSS 防御检查清单

### 1. 先分类

- [ ] 反射型 XSS：恶意输入经 URL/表单进入，立即回显。
- [ ] 存储型 XSS：恶意内容入库，其他用户访问时执行。
- [ ] DOM 型 XSS：前端 JS 直接从 `location`、`innerHTML`、`eval` 等 sink 读取不可信数据。

### 2. 输出编码（按上下文）

- [ ] HTML 正文：做 HTML 实体编码，`<` → `&lt;`，`>` → `&gt;`，`&` → `&amp;`。
- [ ] HTML 属性：属性值加引号并做属性编码，禁止把用户输入放进事件属性（`onclick` 等）。
- [ ] JavaScript 上下文：用 JSON 序列化/JS 字符串编码，禁止直接拼进 `<script>`。
- [ ] URL 上下文：使用 URL 编码，并校验协议，禁止 `javascript:`、`data:` 等危险协议。
- [ ] CSS 上下文：避免用户输入进入 `style`，必须进入时做严格白名单。
- [ ] 富文本/Markdown：服务端和客户端都要白名单净化，不能只靠前端。

### 3. 危险 API 与框架边界

- [ ] 纯文本展示用 `textContent`，禁止 `innerHTML`、`outerHTML`、`document.write`。
- [ ] 必须渲染 HTML 时使用 DOMPurify 等白名单净化库，禁止黑名单替换。
- [ ] React 禁止未净化的 `dangerouslySetInnerHTML`；Vue 禁止未净化的 `v-html`。
- [ ] 模板引擎保持自动转义开启；禁止 `|safe`、`{{{ }}}` 等绕过转义。
- [ ] 不要用 `eval`、`new Function`、`setTimeout(string)` 执行用户输入。

### 4. 浏览器安全头与 Cookie

- [ ] 开启 `Content-Security-Policy`，限制 `script-src`、`object-src`、`base-uri`。
- [ ] 会话 Cookie 设置 `HttpOnly`、`Secure`、`SameSite=Lax/Strict`。
- [ ] 设置 `X-Content-Type-Options: nosniff`。
- [ ] 高安全场景考虑 Trusted Types，限制 DOM XSS sink。

### 5. 安全示例

```python
# Flask：输出前做 HTML 实体编码
from flask import Flask, request
import html

app = Flask(__name__)

@app.get("/hello")
def hello():
    name = request.args.get("name", "")
    return f"<p>你好，{html.escape(name, quote=True)}</p>"
```

```javascript
// 纯文本：textContent 不解析 HTML
element.textContent = userInput;

// 必须渲染 HTML：先白名单净化
import DOMPurify from "dompurify";
element.innerHTML = DOMPurify.sanitize(userInput, {
  ALLOWED_TAGS: ["b", "i", "em", "strong", "a"],
  ALLOWED_ATTR: ["href"]
});
```

### 6. 验收用例

- [ ] 输入 `<img src=x onerror=alert(1)>`：页面只显示文本，不执行脚本。
- [ ] 输入 `<script>alert(1)</script>`：不执行，源码中已转义。
- [ ] 输入 `javascript:alert(1)` 作为链接：协议被拒绝。
- [ ] 富文本输入 `<b>正常</b><script>...</script>`：只保留白名单标签。

## 三、通用安全基线

- [ ] 输入校验采用白名单，不用黑名单。
- [ ] 认证、授权、会话管理不依赖前端隐藏。
- [ ] 密钥、Token 放环境变量，不入库、不进日志、不进代码仓库。
- [ ] 依赖定期扫描，框架和数据库驱动及时升级。
- [ ] 错误页面统一，不泄露堆栈、路径、SQL、内部 IP。
- [ ] 安全日志记录时间、用户、动作、结果，敏感字段脱敏。

## 四、快速审查流程

1. 找所有外部输入入口：URL、表单、Header、Cookie、上传文件、第三方 API。
2. 找所有危险 sink：SQL 拼接、`innerHTML`、模板 `safe`、`eval`、命令执行。
3. 判断数据是否经过参数化、白名单、上下文编码或净化。
4. 检查权限、错误信息、日志、CSP/Cookie 等纵深措施。
5. 用验收用例跑一遍，确认恶意输入只被当作数据。

## 五、参考

- OWASP SQL Injection Prevention Cheat Sheet
- OWASP Cross Site Scripting Prevention Cheat Sheet
- OWASP Top 10: Injection / Cross-Site Scripting

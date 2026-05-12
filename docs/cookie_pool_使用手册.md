# Cookie 池使用手册

## 功能概述

Cookie 池用于**无人值守**场景：预存多组已登录 Cookie，程序运行时直接注入 Cookie 调用平台 API，跳过浏览器扫码登录流程。支持：

- **多平台**：抖音、B站、快手、小红书、微博、今日头条
- **多账号**：每个平台可存多组 Cookie，自动轮换
- **自动切换**：单个 Cookie 连续失败达阈值后自动标记失效并切换下一个
- **扫码收集**：通过内置工具一键扫码，自动写入池文件

---

## 快速开始

### 1. 收集 Cookie（扫码登录）

运行内置的 Cookie 收集工具，扫码后自动存入 `config/cookie_pool.json`：

```bash
# 全平台批量收集（交互模式，逐个平台提示扫码）
python tools/cookie_collector.py

# 仅收集指定平台
python tools/cookie_collector.py -p dy bili xhs

# 单平台单账号（非交互，适合脚本调用）
python tools/cookie_collector.py -p dy --single

# 自定义输出文件和超时
python tools/cookie_collector.py -p dy -f config/my_cookies.json -t 180
```

**操作流程：**

1. 工具启动后会弹出浏览器窗口
2. 在浏览器中使用手机 App 扫码登录
3. 登录成功后工具自动检测并保存 Cookie
4. 提示是否继续为该平台添加更多账号

### 2. 手动添加 Cookie

如果已有 Cookie 字符串（如从浏览器 DevTools 复制），可直接编辑 `config/cookie_pool.json`：

```json
{
  "dy": [
    {
      "id": "dy_01",
      "cookie": "sessionid=xxx; passport_csrf_token=yyy;",
      "note": "抖音账号1-小明"
    },
    {
      "id": "dy_02",
      "cookie": "sessionid=zzz; passport_csrf_token=www;",
      "note": "抖音账号2-小红"
    }
  ],
  "bili": [
    {
      "id": "bili_01",
      "cookie": "SESSDATA=xxx; bili_jct=yyy; DedeUserID=zzz;",
      "note": "B站账号1"
    }
  ]
}
```

**格式说明：**


| 字段     | 类型     | 说明                           |
| ------ | ------ | ---------------------------- |
| id     | string | 唯一标识，建议格式：`平台_序号`            |
| cookie | string | 完整 Cookie 字符串（key=value;...） |
| note   | string | 备注信息（方便辨识）                   |


### 3. 启用 Cookie 池

在 `config/base_config.py` 中设置：

```python
# 启用 Cookie 池（跳过浏览器扫码）
ENABLE_COOKIE_POOL = True

# Cookie 池来源
COOKIE_POOL_SOURCE = "file"          # "file" 或 "db"

# Cookie 池文件路径
COOKIE_POOL_FILE = "config/cookie_pool.json"

# 登录方式设为 cookie_pool
LOGIN_TYPE = "cookie_pool"

# 扫码存 Cookie 到数据库（默认）
python tools/cookie_collector.py --platforms dy --storage db --single

# 批量多平台
python tools/cookie_collector.py --platforms dy bili ks wb toutiao --storage db

# 存到本地 JSON（旧模式）
python tools/cookie_collector.py --platforms dy --storage file
```

---

## 配置参数详解


| 参数                    | 默认值                         | 说明                              |
| --------------------- | --------------------------- | ------------------------------- |
| `ENABLE_COOKIE_POOL`  | `False`                     | 总开关，启用后跳过浏览器登录                  |
| `COOKIE_POOL_SOURCE`  | `"file"`                    | 来源：`file`(本地JSON) / `db`(MySQL) |
| `COOKIE_POOL_FILE`    | `"config/cookie_pool.json"` | 本地 Cookie 池文件路径                 |
| `COOKIE_AUTO_SWITCH`  | `True`                      | Cookie 失效后是否自动切换下一个             |
| `COOKIE_MAX_FAILURES` | `3`                         | 连续失败几次后标记 Cookie 失效             |


---

## 运行机制

```
程序启动
  │
  ├─ ENABLE_COOKIE_POOL = True ?
  │     │
  │     ├─ Yes → 加载 Cookie 池 → 按平台注入 Cookie → 直接调 API
  │     │         │
  │     │         ├─ 请求成功 → 正常处理
  │     │         └─ 请求失败 → fail_count += 1
  │     │                        │
  │     │                        ├─ < COOKIE_MAX_FAILURES → 继续用当前 Cookie
  │     │                        └─ >= COOKIE_MAX_FAILURES → 标记失效 → 切换下一个
  │     │
  │     └─ No → 走浏览器扫码登录流程
  │
  └─ 所有 Cookie 失效 → 日志告警，该平台跳过
```

---

## 数据库模式（可选）

如果 `COOKIE_POOL_SOURCE = "db"`，需要在外部 MySQL 中建表：

```sql
CREATE TABLE cookie_pool (
    id INT AUTO_INCREMENT PRIMARY KEY,
    platform VARCHAR(20) NOT NULL COMMENT '平台标识: dy/bili/ks/xhs/wb/toutiao',
    cookie_id VARCHAR(50) NOT NULL COMMENT 'Cookie唯一ID',
    cookie_str TEXT NOT NULL COMMENT 'Cookie字符串',
    note VARCHAR(200) DEFAULT '' COMMENT '备注',
    is_valid TINYINT DEFAULT 1 COMMENT '是否有效: 1有效 0失效',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_platform_cookie_id (platform, cookie_id),
    INDEX idx_platform_valid (platform, is_valid)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Cookie池';
```

**切换为 DB 模式的步骤：**

1. 在 MySQL 中执行上述建表 SQL
2. 修改 `config/base_config.py`：
  ```python
   COOKIE_POOL_SOURCE = "db"
  ```
3. 确保 `.env` 中配置了外部数据库连接：
  ```bash
   EXT_MYSQL_HOST=127.0.0.1
   EXT_MYSQL_PORT=3306
   EXT_MYSQL_USER=root
   EXT_MYSQL_PWD=your_password
   EXT_MYSQL_DB=your_database
  ```
4. 通过 API 接口 `POST /api/v1/cookies/add` 添加 Cookie，会自动写入数据库
5. Cookie 失效时程序自动更新 `is_valid = 0`

**DB 模式优势：**

- 多实例共享同一 Cookie 池
- 可通过数据库管理界面直接查看/编辑
- 失效状态实时持久化，重启不丢失

---

## 常见问题

### Q: Cookie 多久会过期？

各平台 Cookie 有效期不同（通常 7~30 天）。建议：

- 定期（每周）重新扫码更新
- 每个平台存 2~3 组 Cookie，一组失效时自动切换

### Q: 如何判断 Cookie 是否还有效？

程序运行时如果接口返回未登录/无权限，会自动计入失败次数。达到 `COOKIE_MAX_FAILURES`（默认3次）后自动标记失效。

### Q: 扫码登录超时怎么办？

- 默认超时 120 秒，可通过 `-t` 参数延长
- 确保手机 App 和电脑在同一网络
- 部分平台可能需要先手动关闭已有的登录弹窗

### Q: 支持哪些平台？


| 平台标识    | 名称   | 关键Cookie    |
| ------- | ---- | ----------- |
| dy      | 抖音   | sessionid   |
| bili    | B站   | SESSDATA    |
| ks      | 快手   | did         |
| xhs     | 小红书  | web_session |
| wb      | 微博   | SUB         |
| toutiao | 今日头条 | ttwid       |



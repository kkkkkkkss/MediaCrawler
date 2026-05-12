# AutoGen vs Playwright 脚本 — 自动化复杂行为方案对比

## 背景

后续需求：通过 Cookie 注入后，对指定作品链接执行以下操作：
1. **投诉举报**：自动对违规作品提交举报
2. **评论留言**：以官方账号身份，在特定链接下回复预设内容（如"我们已知悉此事，已全力抢修"）

这些行为涉及多步页面交互（导航、点击按钮、填写表单、确认提交），且每个平台的 UI 和流程差异较大。

---

## 方案 A：使用 AutoGen 多 Agent 协作

### 什么是 AutoGen

微软开源的多 Agent 框架，允许定义多个「角色」（如 Planner、Browser Agent、Verifier）协作完成任务。

### 流程

```
用户下发任务："对链接 X 进行举报，理由是虚假信息"
         ↓
   Planner Agent
   (分析平台类型，生成操作步骤)
         ↓
   Browser Agent (内置 Playwright)
   (按步骤执行页面操作：导航→点击举报→选择理由→提交)
         ↓
   Verifier Agent
   (截图验证是否提交成功)
         ↓
   返回执行结果
```

### 落实步骤

1. **安装框架**：`pip install pyautogen`
2. **定义 Agent 角色**：
   - `PlannerAgent`：接收任务，输出操作步骤的 JSON 列表
   - `BrowserAgent`：调用 Playwright 执行每一步操作
   - `VerifierAgent`：对执行后的截图做 OCR/VLM 判断是否成功
3. **编写 Skill/Tool**：
   - 封装 Playwright 操作为 Tool（`click_element`, `fill_input`, `screenshot` 等）
   - 注册到 Agent 的 tool_use 列表中
4. **平台适配层**：
   - 每个平台的举报/评论流程不同，需要维护一份「操作 Playbook」
   - 例如：抖音举报 = 点击"…" → 点击"举报" → 选择"虚假信息" → 点击"提交"
5. **会话编排**：AutoGen 的 GroupChat 管理 Agent 之间的对话流转

### 优点

- **灵活应变**：LLM 能理解非结构化的页面变化，平台 UI 改版后无需立即修改代码
- **可扩展**：新增平台或新增操作类型时，只需增加 Playbook，不改底层框架
- **错误恢复**：Agent 可以根据截图判断操作是否失败，并尝试不同路径
- **天然支持复杂对话**：多轮交互（如验证码弹窗、二次确认）可以由 Agent 动态处理

### 缺点

- **速度慢**：每一步都需要 LLM 推理（~1-3 秒/步），100 条链接处理耗时较长
- **Token 成本高**：每次操作都要发送页面截图或 HTML 给 LLM 分析
- **可靠性不稳定**：LLM 可能"幻觉"出不存在的按钮或错误的操作序列
- **调试困难**：多 Agent 协作时的错误定位比单脚本复杂得多
- **依赖外部 AI 服务**：网络抖动或 API 限流会导致整个流程中断

---

## 方案 B：直接编写 Playwright 自动化脚本

### 流程

```
用户下发任务："对链接 X 进行举报"
         ↓
   平台路由层
   (识别平台类型：dy/ks/wb/toutiao)
         ↓
   调用对应平台的硬编码脚本
   (playwright_douyin_report.py)
         ↓
   顺序执行：导航 → 等待加载 → 定位举报按钮 → 点击 → 选择理由 → 提交
         ↓
   截图存档 + 返回成功/失败
```

### 落实步骤

1. **创建操作脚本目录**：
   ```
   media_platform/actions/
   ├── __init__.py
   ├── base_action.py        # 基类：Cookie注入、截图、错误处理
   ├── douyin_report.py       # 抖音举报
   ├── douyin_comment.py      # 抖音评论
   ├── kuaishou_report.py     # 快手举报
   ├── kuaishou_comment.py    # 快手评论
   ├── weibo_report.py        # 微博举报
   ├── weibo_comment.py       # 微博评论
   └── toutiao_report.py      # 头条举报
   ```
2. **基类设计**：
   ```python
   class BaseAction:
       async def execute(self, url, cookie, params):
           page = await self._create_page(cookie)
           await page.goto(url)
           await self._do_action(page, params)    # 子类实现
           screenshot = await page.screenshot()
           return ActionResult(success=True, screenshot=screenshot)
   ```
3. **选择器维护**：
   - 使用 `data-testid`、`aria-label` 等稳定属性做定位
   - 每个平台的选择器集中配置在 JSON/YAML 文件中，方便更新
4. **错误处理**：
   - 超时重试（最多 3 次）
   - 截图存档（无论成功失败）
   - 日志记录每一步操作
5. **API 接口**：
   ```
   POST /api/v1/action/report  → 提交举报
   POST /api/v1/action/comment → 发布评论
   ```

### 优点

- **速度极快**：纯 Playwright 操作，无 LLM 延迟，单条操作通常 5-10 秒
- **零 Token 成本**：不调用任何 AI 服务
- **可靠性高**：固定路径执行，不存在"幻觉"问题，行为完全可预测
- **调试简单**：单文件单流程，出错时直接看截图和日志即可定位
- **批量性能好**：并发处理 10+ 个链接无压力

### 缺点

- **维护成本**：平台 UI 改版后，需要人工更新选择器和操作流程
- **扩展性差**：每增加一个平台或一种操作类型，都要写一套新脚本
- **无法处理意外**：遇到验证码、弹窗等非预期场景时，脚本直接失败
- **初始开发量大**：每个平台 × 每种操作 = N 个脚本文件

---

## 综合对比

| 维度 | AutoGen (方案A) | Playwright 脚本 (方案B) |
|------|----------------|----------------------|
| 执行速度 | 慢（每步需 LLM 推理） | 快（毫秒级定位+点击） |
| Token 成本 | 高（每次操作都需 AI） | 无 |
| 可靠性 | 中（LLM 可能出错） | 高（固定路径） |
| UI 改版适应 | 好（LLM 可动态理解） | 差（需人工更新选择器） |
| 新平台扩展 | 容易（加 Playbook） | 较难（写新脚本） |
| 异常处理 | 好（Agent 可推理应对） | 差（需预设所有分支） |
| 调试难度 | 高 | 低 |
| 初始开发量 | 中（框架搭建） | 大（逐平台逐操作） |
| 适合场景 | 操作类型多变、平台频繁改版 | 操作固定、追求速度和稳定 |

---

## 推荐方案

**推荐「方案 B 为主，方案 A 为后期增强」**

### 理由

1. **举报和评论是确定性操作**：操作路径固定（点固定按钮、填固定文本），不需要 LLM 的"理解能力"
2. **批量场景对速度敏感**：100 条链接批量举报，Playwright 脚本 ~10 分钟，AutoGen 可能需要 1 小时以上
3. **成本可控**：举报/评论是高频操作，每次都消耗 AI token 不划算
4. **可靠性优先**：官方账号的自动回复不能出错，硬编码脚本比 LLM 更可预测

### 建议实施路径

| 阶段 | 内容 | 预计工时 |
|------|------|---------|
| 第一阶段 | 实现 Playwright 脚本框架 + 抖音举报/评论 | 2-3 天 |
| 第二阶段 | 扩展到快手、微博、头条 | 2-3 天 |
| 第三阶段 | API 接口封装 + 前端操作面板 | 1-2 天 |
| 第四阶段（可选） | 接入 AutoGen 处理异常场景 | 3-5 天 |

### 第四阶段说明

当 Playwright 脚本遇到无法处理的情况（如新增的验证码、改版的 UI）时，可以降级调用 AutoGen Agent 来尝试完成操作。这样形成 **"Playwright 为主 → AutoGen 兜底"** 的双层架构，兼顾速度和灵活性。

### 关键代码结构

```
media_platform/actions/
├── __init__.py
├── base_action.py           # 基类：Cookie注入、Playwright上下文管理
├── action_config.yaml       # 各平台选择器配置（方便更新维护）
├── douyin/
│   ├── report.py            # 抖音举报
│   └── comment.py           # 抖音评论
├── kuaishou/
│   ├── report.py
│   └── comment.py
├── weibo/
│   ├── report.py
│   └── comment.py
└── toutiao/
    ├── report.py
    └── comment.py

api/routes_action.py         # 新增：举报/评论 API 路由
api/schemas_action.py        # 新增：请求/响应模型
```

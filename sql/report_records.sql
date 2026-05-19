-- 举报投诉记录表
-- 每次举报操作（一个Cookie对一条URL举报一次）对应一行记录

CREATE TABLE IF NOT EXISTS report_records (
  id           BIGINT       AUTO_INCREMENT PRIMARY KEY,
  task_id      VARCHAR(64)  NOT NULL          COMMENT '任务ID，关联前端任务管理',
  url          VARCHAR(1024) NOT NULL         COMMENT '被举报的作品链接',
  platform     VARCHAR(20)                    COMMENT '平台代码: dy/wb/ks/toutiao',
  cookie_id    VARCHAR(64)                    COMMENT '使用的Cookie ID，游客模式为 guest',
  reason       VARCHAR(100)                   COMMENT '举报理由（前端选择的中文理由）',
  description  TEXT                           COMMENT '补充说明（可选）',
  success      TINYINT      DEFAULT 0         COMMENT '是否成功: 1=成功, 0=失败',
  error_msg    TEXT                           COMMENT '失败原因',
  screenshot_path VARCHAR(512)               COMMENT '截图文件路径（相对于项目根目录）',
  created_at   DATETIME     DEFAULT CURRENT_TIMESTAMP COMMENT '记录创建时间',
  INDEX idx_task     (task_id),
  INDEX idx_platform (platform),
  INDEX idx_created  (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='举报投诉操作记录表';

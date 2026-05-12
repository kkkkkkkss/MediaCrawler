-- 多平台作品评论表（落库在 db_sdga_report）
-- 执行前请确保已连接到 db_sdga_report 库

CREATE TABLE IF NOT EXISTS db_sdga_report.bigscreen_content_comments (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  source_platform VARCHAR(32) NOT NULL COMMENT '平台标识: douyin/bilibili/kuaishou/toutiao/xhs/weibo',
  content_url VARCHAR(500) NOT NULL COMMENT '作品原始URL',
  content_id VARCHAR(128) NULL COMMENT '平台侧作品ID',
  comment_id VARCHAR(128) NOT NULL COMMENT '平台侧评论ID',
  parent_comment_id VARCHAR(128) NULL COMMENT '父评论ID（二级评论时使用）',
  author_id VARCHAR(256) NULL COMMENT '评论者用户ID',
  author_name VARCHAR(256) NULL COMMENT '评论者昵称',
  comment_text TEXT NULL COMMENT '评论内容',
  comment_like_count INT NULL COMMENT '评论点赞数',
  comment_reply_count INT NULL COMMENT '子评论数',
  comment_time DATETIME NULL COMMENT '评论时间',
  crawl_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '爬取时间',
  raw_json JSON NULL COMMENT '原始JSON数据（兜底/扩展用）',
  UNIQUE KEY uk_platform_comment (source_platform, comment_id),
  KEY idx_content (source_platform, content_id),
  KEY idx_url (content_url(191)),
  KEY idx_time (comment_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='多平台作品评论（url_check模式产出）';

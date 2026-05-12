名称	类型	长度	小数点	Not Null	虚拟	键	虚拟类型	表达式	Enum Value	默认值	注释	存储	列格式	字符集	排序规则	键长度	键排序	永远生成	根据当前时间戳更新	二进制	自动递增	无符号	填充零
id	bigint	20		true	false	true					主键					0	ASC	false	false	false	true	false	false
type	smallint	2		true	false	false					1为图文，2为视频							false	false	false	false	false	false
wtype	varchar	4		false	false	false				NULL	文章发表类型，1：原创、2：转发、7 评论			utf8	utf8_general_ci			false	false	false	false	false	false
micro_blog_type	varchar	255		false	false	false				NULL	微博类型			utf8	utf8_general_ci			false	false	false	false	false	false
channel	varchar	255		false	false	false				NULL	频道			utf8mb4	utf8mb4_general_ci			false	false	false	false	false	false
web_name	varchar	255		false	false	false				NULL	网站名称			utf8	utf8_general_ci			false	false	false	false	false	false
data_id	varchar	255		false	false	false				NULL	数据信息ID			utf8	utf8_general_ci			false	false	false	false	false	false
pid	varchar	32		false	false	false				NULL	转发、评论等上一级信息 I			utf8	utf8_general_ci			false	false	false	false	false	false
rootid	varchar	32		false	false	false				NULL	用于标记多级评论信息的根信息 			utf8	utf8_general_ci			false	false	false	false	false	false
publish_time	datetime			false	false	false				NULL	发布时间							false	false	false	false	false	false
publish_location	varchar	4000		false	false	false				NULL	发布地			utf8mb4	utf8mb4_general_ci			false	false	false	false	false	false
media_type	varchar	50		false	false	false				NULL	媒体类型, 媒体类型，'01'：新闻大类, '0101'：新闻, '0105'：平媒, 0109'：APP, '02论坛, 0201:百度贴吧', 03':博客, '04'：微博, '0401':新浪微博, '0408':新浪长微博, '06'：微信，'07':视频, '11'：小视频, '1101'：抖音, '1102'：快手, '99搜索			utf8	utf8_general_ci			false	false	false	false	false	false
media_type_merge	varchar	255		false	false	false				NULL	媒体类型合并			utf8	utf8_general_ci			false	false	false	false	false	false
author_auth_type	varchar	4		false	false	false				NULL	作者认证类型			utf8	utf8_general_ci			false	false	false	false	false	false
author	varchar	50		false	false	false				NULL	作者			utf8mb4	utf8mb4_general_ci			false	false	false	false	false	false
author_id	varchar	500		false	false	false				NULL	作者Id			utf8	utf8_general_ci			false	false	false	false	false	false
author_avatar	varchar	500		false	false	false				NULL	作者头像地址			utf8	utf8_general_ci			false	false	false	false	false	false
attitude	varchar	255		false	false	false				NULL	倾向性 -6:确定负面(高敏感)，-5:确定负面(敏感)，-2:确定负面，-1:疑似负面0:中性，1:疑似正面，2:确定正面，9:争议			utf8	utf8_general_ci			false	false	false	false	false	false
attitude_merge	varchar	255		false	false	false				NULL	倾向性合并			utf8	utf8_general_ci			false	false	false	false	false	false
hashcode	varchar	255		false	false	false				NULL	相似值			utf8	utf8_general_ci			false	false	false	false	false	false
impor_tance_weight	varchar	255		false	false	false				NULL	视频匹配距离			utf8	utf8_general_ci			false	false	false	false	false	false
forward_count	varchar	50		false	false	false				NULL	转发数			utf8	utf8_general_ci			false	false	false	false	false	false
praise_count	int	10		false	false	false				0	点赞数							false	false	false	false	false	false
reply_count	int	10		false	false	false				0	评论数							false	false	false	false	false	false
visit_count	int	10		false	false	false				0	访问数							false	false	false	false	false	false
share_count	int	10		false	false	false				0	分享数							false	false	false	false	false	false
repost_source	varchar	500		false	false	false				NULL	转载来源			utf8mb4	utf8mb4_general_ci			false	false	false	false	false	false
follow_state	int	10		false	false	false				NULL	粉丝数							false	false	false	false	false	false
is_valid	smallint	5		false	false	false				NULL	作品链接是否有效 0或null=未知，1=有效，2=无效							false	false	false	false	false	false
is_abroad	varchar	50		false	false	false				NULL	是否境外信息			utf8	utf8_general_ci			false	false	false	false	false	false
cover	varchar	500		false	false	false				NULL	封面地址			utf8	utf8_general_ci			false	false	false	false	false	false
content	longtext			false	false	false				NULL	正文			utf8mb4	utf8mb4_general_ci			false	false	false	false	false	false
title	text			false	false	false				NULL	标题			utf8mb4	utf8mb4_general_ci			false	false	false	false	false	false
collect_time	datetime			false	false	false				NULL	采集时间							false	false	false	false	false	false
column	varchar	255		false	false	false				NULL	发布媒体			utf8mb4	utf8mb4_general_ci			false	false	false	false	false	false
is_comment_data	varchar	50		false	false	false				NULL	是否评论信息			utf8	utf8_general_ci			false	false	false	false	false	false
is_ocr	varchar	50		false	false	false				NULL	是否图片识别			utf8	utf8_general_ci			false	false	false	false	false	false
ocr_data	longtext			false	false	false				NULL	图片识别结果			utf8mb4	utf8mb4_general_ci			false	false	false	false	false	false
url	varchar	500		false	false	false				NULL	原文地址			utf8	utf8_general_ci			false	false	false	false	false	false
video_url	text			false	false	false				NULL	视频地址			utf8	utf8_general_ci			false	false	false	false	false	false
ai_key	text			false	false	false				NULL	识别标记			utf8	utf8_general_ci			false	false	false	false	false	false
ip_region	varchar	25		false	false	false				NULL	IP 地址			utf8	utf8_general_ci			false	false	false	false	false	false
is_mqdb	smallint	2		false	false	false				0	是否插入墨奇，0未插，1已插							false	false	false	false	false	false
is_synchro	varchar	2		false	false	false				'0'	0:未同步  1：已同步			utf8	utf8_general_ci			false	false	false	false	false	false
create_time	datetime			false	false	false				CURRENT_TIMESTAMP	创建时间							false	false	false	false	false	false
local_day	varchar	10		false	false	false				NULL	采集时间string 格式 yyyy-mm-dd			utf8	utf8_general_ci			false	false	false	false	false	false
body_of_text	longtext			false	false	false				NULL	正文150字			utf16	utf16_general_ci			false	false	false	false	false	false
bigscreen_version	int	10		false	false	false				0	数据版本号							false	false	false	false	false	false
check_date	date			false	false	false				NULL								false	false	false	false	false	false
check_status	varchar	1		false	false	false				NULL	0:正常  1:异常（无权限，已删除）			utf8	utf8_general_ci			false	false	false	false	false	false
subject_id	varchar	500		false	false	false				NULL	主题id			utf8	utf8_general_ci			false	false	false	false	false	false
all_content	longtext			false	false	false				NULL	组合查询字段			utf16	utf16_general_ci			false	false	false	false	false	false
retweeted	longtext			false	false	false				NULL	原发数据			utf16	utf16_general_ci			false	false	false	false	false	false
is_ad	smallint	2		false	false	false				NULL	是否广告0（不是）1（是）							false	false	false	false	false	false
province	varchar	255		false	false	false				NULL	省份			utf8	utf8_general_ci			false	false	false	false	false	false
city	varchar	255		false	false	false				NULL	城市			utf8	utf8_general_ci			false	false	false	false	false	false
district	varchar	255		false	false	false				NULL	地区			utf8	utf8_general_ci			false	false	false	false	false	false
area_words	varchar	500		false	false	false				NULL	地区组合			utf8	utf8_general_ci			false	false	false	false	false	false
categories	varchar	255		false	false	false				NULL	nlp分类			utf8	utf8_general_ci			false	false	false	false	false	false
police_categories	varchar	50		false	false	false				NULL	涉警分类			utf8	utf8_general_ci			false	false	false	false	false	false
is_police	varchar	20		false	false	false				NULL	是否涉警			utf8	utf8_general_ci			false	false	false	false	false	false
is_asian_police	smallint	2		false	false	false				0	是否涉亚运							false	false	false	false	false	false
attention_color	smallint	2		false	false	false				NULL	预警4色							false	false	false	false	false	false
opinion_extraction	mediumtext			false	false	false				NULL	实体观点抽取			utf8mb4	utf8mb4_general_ci			false	false	false	false	false	false
media_id	varchar	255		false	false	false				NULL	媒体ID			utf8	utf8_general_ci			false	false	false	false	false	false
laudio	mediumtext			false	false	false				NULL	文档音频地址 ["URL1","URL2" ,"URL3"]（互联网地址）			utf8	utf8_general_ci			false	false	false	false	false	false
video_cont	mediumtext			false	false	false				NULL	视频抽帧图片识别内容结果			utf8	utf8_general_ci			false	false	false	false	false	false
video_cont_timeline	mediumtext			false	false	false				NULL	视频抽帧图片识别时间线，如[{"time":"00:00","text":"成绩合格,"},{"time":"00:02","text":"请回中心打印"}]			utf8	utf8_general_ci			false	false	false	false	false	false
status	int	11		false	false	false				NULL								false	false	false	false	false	false
content_xml	longtext			false	false	false				NULL	富文本正文			utf8mb4	utf8mb4_general_ci			false	false	false	false	false	false
positive_probs	varchar	50		false	false	false				NULL	不敏感比率			utf8	utf8_general_ci			false	false	false	false	false	false
negative_probs	varchar	50		false	false	false				NULL	敏感比率			utf8	utf8_general_ci			false	false	false	false	false	false
audio_trans_cont	mediumtext			false	false	false				NULL	视频音频转写内容结果			utf8mb4	utf8mb4_general_ci			false	false	false	false	false	false
audio_trans_cont_timeline	mediumtext			false	false	false				NULL	视频音频转写时间线结果，如[{"time":"00:00","text":"成绩合格,"},{"time":"00:02","text":"请回中心打印."}]			utf8mb4	utf8mb4_general_ci			false	false	false	false	false	false
is_zy	varchar	50		false	false	false				NULL	是否噪音，噪音、非噪音			utf8	utf8_general_ci			false	false	false	false	false	false
zy_score	varchar	50		false	false	false				NULL	噪音分数			utf8	utf8_general_ci			false	false	false	false	false	false
zy_fenlei	varchar	50		false	false	false				NULL	噪音分类			utf8	utf8_general_ci			false	false	false	false	false	false
poi_location	varchar	1024		false	false	false				NULL	poi地域拼接信息			utf8	utf8_general_ci			false	false	false	false	false	false
data_create_time	datetime			false	false	false				NULL	数据创建时间							false	false	false	false	false	false
data_update_time	datetime			false	false	false				NULL	数据更新时间							false	false	false	false	false	false
report_time	datetime			false	false	false				NULL	上报时间							false	false	false	false	false	false
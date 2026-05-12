# -*- coding: utf-8 -*-
# AI 字段映射模块
#
# 提取策略：硬编码优先 → 指标不足时自动调 AI 补充
#   1. 先执行硬编码路径映射提取（零延迟、零成本）
#   2. 当 praise_count、reply_count、visit_count 均为 0 或 null 时，
#      自动调用 AI 接口对完整 JSON 做二次提取
#   3. 可通过 config.URLCHECK_EXTRACT_MODE 强制指定模式：
#      - "hardcode_first" (默认) → 上述策略
#      - "ai_only"               → 跳过硬编码，直接调 AI
#      - "hardcode_only"         → 仅硬编码，不调 AI

import os
import json
import time
from typing import Any, Dict, Optional

import config
from openai import AsyncOpenAI

from tools import utils
from tools.fallback_field_map import fallback_extract

TARGET_FIELDS = ["praise_count", "reply_count", "visit_count", "share_count", "author", "title"]

_SYSTEM_PROMPT = """你是一个 JSON 数据分析助手。你的任务是从给定的平台接口 JSON 中提取作品的互动指标、作者信息和标题。
严格按照以下规则：
1. 只返回一个 JSON 对象，不要包含任何其它文字、解释或 markdown 标记
2. JSON 对象必须包含以下六个字段：praise_count（点赞数）、reply_count（评论数）、visit_count（播放/浏览数）、share_count（分享数）、author（作者名）、title（作品标题）
3. 数值字段的值必须是整数或 null（如果在 JSON 中确实找不到对应数据）
4. author 和 title 字段的值必须是字符串或 null
5. 不要编造数据，只从给定的 JSON 中提取
6. 常见字段名映射参考：digg_count/like_count/attitudes_count→praise_count, comment_count/comments_count/reply→reply_count, play_count/view_count/read_count→visit_count, share_count/reposts_count/forward_count→share_count, nickname/screen_name/userName/name→author
7. title 提取规则：优先找 title/desc 字段；微博(wb)平台如果没有专用标题字段，从 text_raw 中提取——有【xxx】取括号内文字，否则截取前25字"""

_USER_PROMPT_TEMPLATE = """平台：{platform}
请从以下接口 JSON 中提取 praise_count、reply_count、visit_count、share_count：

{json_fragment}"""


def _metrics_insufficient(metrics: Dict[str, Any]) -> bool:
    """判断核心指标是否全部为空/零"""
    praise = metrics.get("praise_count")
    reply = metrics.get("reply_count")
    visit = metrics.get("visit_count")
    return (
        (praise is None or praise == 0)
        and (reply is None or reply == 0)
        and (visit is None or visit == 0)
    )


# 第三层 AI 诊断时的附加 prompt
_DIAGNOSTIC_PROMPT_SUFFIX = """

额外任务：请同时分析这份 JSON 的结构，判断以下情况：
1. 这份 JSON 是否包含作品的互动数据（点赞/评论/播放等）？如果有，字段名叫什么？路径是什么？
2. 如果所有互动指标都是0或不存在，可能的原因是什么？（如：接口字段路径变更、数据确实为0、接口返回了错误数据等）
3. 给出修复建议（需要修改哪些硬编码路径）

请在返回 JSON 对象之后，另起一行输出 "---DIAGNOSIS---"，然后输出上述分析（纯文本，不要JSON格式）。"""


class AIFieldMapper:
    """
    指标提取器（硬编码优先策略）。

    默认流程：
      1. 硬编码路径提取 → 快速、零成本
      2. 若 praise_count / reply_count / visit_count 全为 0 或 null，
         自动调用 AI 补充提取
    """

    def __init__(self):
        # api_key = os.getenv("DOUBAO_API_KEY", "")
        # base_url = os.getenv("DOUBAO_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
        # model = os.getenv("DOUBAO_MODEL", "doubao-seed-2-0-pro-260215")

        # 优先使用阿里云兼容 OpenAI 的配置，同时兼容历史 DOUBAO_* 变量
        # api_key = os.getenv("ALIYUN_API_KEY") or os.getenv("DOUBAO_API_KEY", "")
        # base_url = os.getenv("ALIYUN_BASE_URL") or os.getenv(
        #     "DOUBAO_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        # )
        # model = os.getenv("ALIYUN_MODEL") or os.getenv("DOUBAO_MODEL", "qwen3.6-max-preview")

        # 纯阿里云配置，彻底删除了所有 DOUBAO_* 兼容代码
        api_key = os.getenv("ALIYUN_API_KEY", "")
        base_url = os.getenv("ALIYUN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        model = os.getenv("ALIYUN_MODEL", "qwen3.6-max-preview")

        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model = model
        self._total_tokens_used = 0
        self._call_count = 0
        self.last_method: Optional[str] = None  # 上一次提取使用的方法: "硬编码" / "AI"

    async def extract_metrics(
        self,
        platform: str,
        raw_json: Any,
        health_checker=None,
        task_info=None,
    ) -> Dict[str, Optional[int]]:
        """
        提取策略入口（含三层兜底优化）。

        三层兜底：当指标全为 0/null 时——
          1. 检查 author/title 是否非空 → 非空说明帖子确实没互动，跳过 AI
          2. 调用 health_checker 检测基准帖子 → 基准正常说明帖子本身无数据
          3. 基准也异常 → 调 AI 提取并生成诊断报告

        Args:
            health_checker: 可选的异步回调，签名 async (platform) -> bool
            task_info: 可选的 TaskInfo，用于写入诊断日志
        """
        raw_dict = raw_json if isinstance(raw_json, dict) else {}
        mode = getattr(config, "URLCHECK_EXTRACT_MODE", "hardcode_first")

        # ── 模式1：仅硬编码 ──
        if mode == "hardcode_only" or mode == "hardcode":
            result = fallback_extract(platform, raw_dict)
            utils.logger.info(f"[AIFieldMapper] 硬编码提取(hardcode_only): {result}")
            self.last_method = "硬编码"
            return result

        # ── 模式2：仅 AI ──
        if mode == "ai_only" or mode == "ai":
            self.last_method = "AI"
            return await self._ai_extract_with_fallback(platform, raw_dict)

        # ── 模式3（默认）：硬编码优先 + 三层兜底 ──
        hardcode_result = fallback_extract(platform, raw_dict)
        utils.logger.info(f"[AIFieldMapper] 硬编码优先提取: {hardcode_result}")

        if not _metrics_insufficient(hardcode_result):
            utils.logger.info("[AIFieldMapper] 硬编码指标充足，跳过 AI 调用")
            self.last_method = "硬编码"
            return hardcode_result

        # ── 指标全为 0/null，进入三层兜底 ──

        # 第一层：检查 author/title 是否存在
        from tools.platform_health_checker import has_content_fields
        if has_content_fields(hardcode_result):
            utils.logger.info(
                "[AIFieldMapper] 第一层兜底: author/title 非空，帖子确实无互动，跳过 AI"
            )
            self.last_method = "硬编码(零互动)"
            return hardcode_result

        # 第二层：基准帖子检测
        if health_checker is not None:
            try:
                platform_healthy = await health_checker(platform)
                if platform_healthy:
                    utils.logger.info(
                        "[AIFieldMapper] 第二层兜底: 基准帖子正常，帖子本身无数据，跳过 AI"
                    )
                    self.last_method = "硬编码(零互动-基准验证)"
                    if task_info:
                        task_info.add_log("基准帖子验证通过，该帖子确实无互动数据，跳过 AI")
                    return hardcode_result
                else:
                    utils.logger.warning(
                        "[AIFieldMapper] 第二层兜底: 基准帖子也异常，平台接口可能变动"
                    )
                    if task_info:
                        task_info.add_log("⚠ 基准帖子检测异常，平台接口可能变动，启动 AI 诊断")
            except Exception as e:
                utils.logger.warning(f"[AIFieldMapper] 基准帖子检测异常: {e}")

        # 第三层：AI 诊断提取
        self.last_method = "AI(诊断)"
        utils.logger.info(
            "[AIFieldMapper] 第三层兜底: 启动 AI 诊断提取"
        )
        if task_info:
            task_info.add_log("正在调用 AI 进行诊断提取...")

        ai_result, diagnosis = await self._ai_diagnostic_extract(platform, raw_dict)

        # 生成诊断报告
        if diagnosis:
            from tools.platform_health_checker import generate_diagnostic_report
            report_path = generate_diagnostic_report(
                platform, raw_dict, hardcode_result, ai_result, diagnosis
            )
            if task_info:
                task_info.add_log(f"⚠ AI 诊断报告已生成: {report_path}")
                task_info.add_log(f"诊断摘要: {diagnosis[:200]}")

        if ai_result is None:
            return hardcode_result

        # 合并：AI 结果补充硬编码中为空/零的字段
        merged = dict(hardcode_result)
        for key, val in ai_result.items():
            if val is not None and val != 0:
                if merged.get(key) is None or merged.get(key) == 0:
                    merged[key] = val
        utils.logger.info(f"[AIFieldMapper] 合并后结果: {merged}")
        return merged

    async def _ai_extract_with_fallback(
        self, platform: str, raw_dict: Dict
    ) -> Dict[str, Optional[int]]:
        """AI 模式提取，失败时回退硬编码"""
        json_str = json.dumps(raw_dict, ensure_ascii=False)
        utils.logger.info(
            f"[AIFieldMapper] AI 模式(ai_only)，传入 JSON 长度: {len(json_str)} 字符"
        )
        try:
            ai_result = await self._call_ai(platform, json_str)
            if ai_result is not None:
                validated = self._validate_result(ai_result)
                if validated is not None:
                    utils.logger.info(f"[AIFieldMapper] AI 提取成功: {validated}")
                    return validated
                else:
                    utils.logger.warning("[AIFieldMapper] AI 返回值校验失败，回退硬编码")
        except Exception as e:
            utils.logger.warning(f"[AIFieldMapper] AI 调用异常: {e}，回退硬编码")

        fallback_result = fallback_extract(platform, raw_dict)
        utils.logger.info(f"[AIFieldMapper] 硬编码回退结果: {fallback_result}")
        return fallback_result

    async def _ai_diagnostic_extract(
        self, platform: str, raw_dict: Dict
    ) -> tuple:
        """第三层兜底：AI 提取指标 + 诊断分析，返回 (metrics_dict|None, diagnosis_text)"""
        json_str = json.dumps(raw_dict, ensure_ascii=False)
        utils.logger.info(
            f"[AIFieldMapper] AI 诊断提取，JSON 长度: {len(json_str)} 字符"
        )
        try:
            user_msg = _USER_PROMPT_TEMPLATE.format(
                platform=platform, json_fragment=json_str
            ) + _DIAGNOSTIC_PROMPT_SUFFIX

            start_time = time.time()
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.1,
                max_tokens=1024,
                timeout=90,
            )
            elapsed = time.time() - start_time

            usage = response.usage
            if usage:
                self._total_tokens_used += usage.total_tokens
                self._call_count += 1
                utils.logger.info(
                    f"[AIFieldMapper] 诊断消耗 {usage.total_tokens} tokens, "
                    f"耗时 {elapsed:.2f}s"
                )

            content = response.choices[0].message.content.strip()

            # 分离 JSON 结果和诊断文本
            diagnosis = ""
            if "---DIAGNOSIS---" in content:
                parts = content.split("---DIAGNOSIS---", 1)
                json_part = parts[0].strip()
                diagnosis = parts[1].strip() if len(parts) > 1 else ""
            else:
                json_part = content

            if json_part.startswith("```"):
                lines = json_part.split("\n")
                lines = [l for l in lines if not l.startswith("```")]
                json_part = "\n".join(lines).strip()

            metrics = json.loads(json_part)
            validated = self._validate_result(metrics)
            if validated:
                utils.logger.info(f"[AIFieldMapper] AI 诊断提取成功: {validated}")
                return validated, diagnosis
            else:
                utils.logger.warning("[AIFieldMapper] AI 诊断返回值校验失败")
                return None, diagnosis

        except Exception as e:
            utils.logger.warning(f"[AIFieldMapper] AI 诊断提取异常: {e}")
            return None, f"AI 调用异常: {e}"

    async def _ai_extract_raw(
        self, platform: str, raw_dict: Dict
    ) -> Optional[Dict[str, Any]]:
        """调 AI 提取指标，返回验证后的 dict 或 None"""
        json_str = json.dumps(raw_dict, ensure_ascii=False)
        utils.logger.info(
            f"[AIFieldMapper] AI 补充提取，JSON 长度: {len(json_str)} 字符"
        )
        try:
            ai_result = await self._call_ai(platform, json_str)
            if ai_result is not None:
                validated = self._validate_result(ai_result)
                if validated is not None:
                    utils.logger.info(f"[AIFieldMapper] AI 补充提取成功: {validated}")
                    return validated
                else:
                    utils.logger.warning("[AIFieldMapper] AI 返回值校验失败")
        except Exception as e:
            utils.logger.warning(f"[AIFieldMapper] AI 补充调用异常: {e}")
        return None

    async def _call_ai(
        self, platform: str, json_fragment: str
    ) -> Optional[Dict]:
        """调用 Doubao AI，返回解析后的 dict，失败返回 None"""
        user_msg = _USER_PROMPT_TEMPLATE.format(
            platform=platform, json_fragment=json_fragment
        )

        start_time = time.time()
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.1,
            max_tokens=256,
            timeout=60,
        )
        elapsed = time.time() - start_time

        usage = response.usage
        if usage:
            self._total_tokens_used += usage.total_tokens
            self._call_count += 1
            utils.logger.info(
                f"[AIFieldMapper] 本次消耗 {usage.total_tokens} tokens, "
                f"累计 {self._total_tokens_used} tokens / {self._call_count} 次调用, "
                f"耗时 {elapsed:.2f}s"
            )

        content = response.choices[0].message.content.strip()

        if content.startswith("```"):
            lines = content.split("\n")
            lines = [l for l in lines if not l.startswith("```")]
            content = "\n".join(lines).strip()

        return json.loads(content)

    @staticmethod
    def _validate_result(result: Any) -> Optional[Dict]:
        if not isinstance(result, dict):
            return None

        validated = {}
        for field in TARGET_FIELDS:
            val = result.get(field)
            if field in ("author", "title"):
                validated[field] = str(val) if val else None
                continue
            if val is None:
                validated[field] = None
            elif isinstance(val, int):
                validated[field] = val if val >= 0 else None
            elif isinstance(val, (float, str)):
                try:
                    int_val = int(float(val))
                    validated[field] = int_val if int_val >= 0 else None
                except (ValueError, TypeError):
                    validated[field] = None
            else:
                validated[field] = None

        return validated

    def get_stats(self) -> Dict[str, int]:
        return {
            "total_tokens_used": self._total_tokens_used,
            "call_count": self._call_count,
        }


ai_mapper = AIFieldMapper()

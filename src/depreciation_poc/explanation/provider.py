from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Protocol


class ExplanationProvider(Protocol):
    provider_name: str

    def explain(self, context: dict[str, Any]) -> dict[str, Any]:
        ...


class TemplateExplanationProvider:
    provider_name = "template"

    def explain(self, context: dict[str, Any]) -> dict[str, Any]:
        drivers = context.get("drivers", [])
        contributors = context.get("contributors", [])
        anomalies = context.get("anomalies", [])
        scope = context.get("scope", {})
        top_driver = drivers[0] if drivers else {}
        top_asset = contributors[0] if contributors else {}
        policy_context = context.get("policy_context") or {}
        available_actions = context.get("available_actions", [])
        department = scope.get("department") or "全部部门"
        year = scope.get("year") or "全部年度"
        total = context.get("dashboard", {}).get("kpis", {}).get("total_depreciation", "0.00")
        narrative = (
            f"{department}在{year}范围内的折旧预测需要重点关注。"
            f"当前场景未来期间折旧合计为{total}。"
            f"主要驱动来自{_driver_label(top_driver.get('driver'))}"
            f"{_source_suffix(top_driver.get('asset_source_type'))}，金额为{top_driver.get('depreciation', '0.00')}。"
            f"贡献最大的对象是{top_asset.get('asset_ref', '无')}，"
            f"对应类别为{_category_label(top_asset.get('asset_category'))}，"
            f"适用政策为{policy_context.get('depreciation_policy_label_cn') or top_asset.get('depreciation_policy', '无')}。"
        )
        risk = (
            f"当前仍有{len(anomalies)}条异常需要在预算提交前处理；"
            "阻断异常对应对象不会进入折旧预测明细。"
            if anomalies
            else "当前筛选范围内未发现阻断异常。"
        )
        next_step = (
            "建议先处理折旧码错配和缺少投产日期的异常，再对金额较大的计划资产做 What-if 测算。"
        )
        action_hint = _action_hint(available_actions)
        return {
            "provider": self.provider_name,
            "summary": narrative,
            "key_reasons": [
                f"主要驱动：{_driver_label(item.get('driver'))}{_source_suffix(item.get('asset_source_type'))}，金额 {item.get('depreciation', '0.00')}"
                for item in drivers[:4]
            ],
            "risks": [risk],
            "next_steps": [next_step, action_hint] if action_hint else [next_step],
        }


class DeepSeekExplanationProvider:
    provider_name = "deepseek"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout_seconds: int = 20,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def explain(self, context: dict[str, Any]) -> dict[str, Any]:
        prompt = {
            "role": "user",
            "content": (
                "你是企业预算财务分析助手。请基于以下结构化事实生成中文业务解释，"
                "不要重新计算金额，不要编造事实。输出 JSON，字段为 summary, key_reasons, risks, next_steps。\n"
                + json.dumps(context, ensure_ascii=False)
            ),
        }
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "你只解释已给出的折旧预测事实，不做金额计算。",
                },
                prompt,
            ],
            "temperature": 0.2,
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            data = json.loads(response.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        parsed = _extract_json(content)
        parsed["provider"] = self.provider_name
        return parsed


class FallbackExplanationProvider:
    provider_name = "fallback"

    def __init__(self) -> None:
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        self.template = TemplateExplanationProvider()
        self.deepseek = None
        if api_key:
            self.deepseek = DeepSeekExplanationProvider(
                api_key=api_key,
                model=os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash"),
                base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
            )

    def explain(self, context: dict[str, Any]) -> dict[str, Any]:
        if self.deepseek is not None:
            try:
                return self.deepseek.explain(context)
            except (KeyError, json.JSONDecodeError, urllib.error.URLError, TimeoutError, ValueError) as exc:
                result = self.template.explain(context)
                result["provider"] = "template_fallback"
                result["fallback_reason"] = str(exc)
                return result
        return self.template.explain(context)


def _extract_json(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text)


def _driver_label(value: str | None) -> str:
    return {
        "BASE": "存量/常规折旧",
        "ADDITION": "新增资产影响",
        "DISPOSAL": "减少资产影响",
        "IMPAIRMENT": "减值影响",
    }.get(value or "", value or "未知驱动")


def _source_suffix(value: str | None) -> str:
    if value == "PLANNED":
        return "（计划资产）"
    if value == "CURRENT":
        return "（存量资产）"
    return ""


def _category_label(value: str | None) -> str:
    return {
        "INJECTION_EQUIPMENT": "注塑设备",
        "MACHINE_EQUIPMENT": "机器设备",
        "PRODUCTION_EQUIPMENT": "生产设备",
        "FIXED_ASSET": "固定资产",
        "ELECTRONIC_EQUIPMENT": "电子设备",
        "BUILDING": "房屋建筑物",
    }.get(value or "", value or "无")


def _action_hint(actions: list[dict[str, Any]]) -> str:
    labels = [
        str(item.get("label_cn"))
        for item in actions
        if item.get("type_id") in {
            "changePlannedAssetAmount",
            "changeInServiceDate",
            "addDisposalEvent",
            "addImpairmentEvent",
            "changePolicyParameters",
            "resolveAnomaly",
        }
    ]
    if not labels:
        return ""
    return "可通过受控动作继续分析：" + "、".join(labels[:4]) + "。"

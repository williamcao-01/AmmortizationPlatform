from __future__ import annotations

import ast
import json
import os
import re
import urllib.error
import urllib.request
import uuid
from decimal import Decimal
from typing import Any, Callable, Iterator


ToolExecutor = Callable[[str, dict[str, Any], str], dict[str, Any]]
ToolCatalog = Callable[[], list[dict[str, Any]]]
ContextLoader = Callable[[str], dict[str, Any]]


class KnowledgeChatService:
    """Evidence-grounded multi-turn chat with streaming model output."""

    def __init__(
        self,
        *,
        tool_executor: ToolExecutor,
        tool_catalog: ToolCatalog,
        context_loader: ContextLoader,
    ) -> None:
        self.tool_executor = tool_executor
        self.tool_catalog = tool_catalog
        self.context_loader = context_loader
        self.external_allowed = os.environ.get("KNOWLEDGE_CHAT_ALLOW_EXTERNAL", "false").lower() in {"1", "true", "yes"}
        self.available_api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        self.api_key = self.available_api_key if self.external_allowed else ""
        self.model = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
        self.base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")

    def status(self) -> dict[str, Any]:
        return {
            "configured": bool(self.available_api_key),
            "provider": "deepseek" if self.available_api_key else "deterministic_fallback",
            "model": self.model if self.available_api_key else None,
            "streaming": True,
            "grounding": "database_and_ontology",
            "agentic_tool_use": True,
            "protocol_version": "knowledge-agent-v2",
            "ontology_evidence_gateway": {
                "required": True,
                "protocol": "ontology-evidence-gateway-v1",
                "access": "python_neo4j_driver_controlled_cypher",
            },
            "external_model_allowed": self.external_allowed,
            "external_model_available": bool(self.available_api_key),
            "external_model_requires_consent": bool(self.available_api_key) and not self.external_allowed,
        }

    def stream(self, payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
        scenario_id = str(payload.get("scenario_id") or "BASELINE")
        messages = self._messages(payload.get("messages"))
        question = str(payload.get("question") or (messages[-1]["content"] if messages else "")).strip()
        if not question:
            raise ValueError("请输入要咨询的问题。")
        if not messages or messages[-1]["role"] != "user" or messages[-1]["content"] != question:
            messages.append({"role": "user", "content": question})
        messages = messages[-12:]
        conversation_id = str(payload.get("conversation_id") or f"CHAT-{uuid.uuid4().hex[:12].upper()}")
        request_api_key = self.available_api_key if (
            self.external_allowed or payload.get("external_model_consent") is True
        ) else ""
        yield {
            "type": "meta",
            "conversation_id": conversation_id,
            "provider": "deepseek" if request_api_key else "deterministic_fallback",
            "model": self.model if request_api_key else None,
            "agentic_tool_use": bool(request_api_key),
            "protocol_version": "knowledge-agent-v2",
        }

        evidence: dict[str, Any] = {"scenario_id": scenario_id, "question": question, "items": [], "sources": []}
        tool_trace: list[dict[str, Any]] = []
        if not request_api_key:
            yield {
                "type": "error",
                "code": "DEEPSEEK_NOT_AVAILABLE",
                "error": "DeepSeek未配置或本会话未授权，知识问答不会使用模板答案代替模型回答。",
            }
            return
        try:
            yield {"type": "progress", "stage": "understanding", "text": "DeepSeek正在理解问题并规划查询。"}
            evidence, tool_trace = self._run_agent(
                messages=messages,
                scenario_id=scenario_id,
                conversation_id=conversation_id,
                api_key=request_api_key,
            )
            for item in tool_trace:
                yield {
                    "type": "progress",
                    "stage": "tool",
                    "text": f"已调用 {item['tool']} 获取核验证据。",
                    "tool": item["tool"],
                }
            for item in evidence.get("items") or []:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "action_draft":
                    yield {"type": "action_draft", "draft": item.get("draft")}
                elif item.get("type") == "reverse_plan":
                    yield {"type": "reverse_plan", "draft_id": item.get("draft_id"), "recommendations": item.get("recommendations") or []}
                elif item.get("type") == "comparison_result":
                    yield {"type": "comparison_result", "comparison": item.get("comparison")}
            yield {
                "type": "progress",
                "stage": "answering",
                "text": f"已完成{len(tool_trace)}次工具调用，正在组织回答。",
            }
            for text in self._stream_model(messages=messages, evidence=evidence, api_key=request_api_key):
                if text:
                    yield {"type": "delta", "text": text}
        except (RuntimeError, urllib.error.URLError, TimeoutError, ValueError, KeyError, json.JSONDecodeError) as exc:
            yield {
                "type": "error",
                "code": "DEEPSEEK_AGENT_FAILED",
                "error": f"DeepSeek Agent执行失败：{exc}",
                "tool_trace": tool_trace,
            }
            return

        sources = list(evidence.get("sources") or [])
        yield {"type": "sources", "sources": sources}
        yield {
            "type": "done",
            "conversation_id": conversation_id,
            "used_llm": True,
            "provider": "deepseek",
            "model": self.model,
            "tool_trace": tool_trace,
            "protocol_version": "knowledge-agent-v2",
        }

    def _run_agent(
        self,
        *,
        messages: list[dict[str, str]],
        scenario_id: str,
        conversation_id: str,
        api_key: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        observations: list[dict[str, Any]] = []
        trace: list[dict[str, Any]] = []
        executed_calls: set[str] = set()
        for round_number in range(1, 5):
            mandatory_plan = self._mandatory_impairment_impact_plan(messages[-1]["content"]) if not observations else None
            mandatory_plan = mandatory_plan or (self._mandatory_variance_plan(messages[-1]["content"]) if not observations else None)
            plan = mandatory_plan or self._plan_tools(
                messages=messages,
                scenario_id=scenario_id,
                observations=observations,
                api_key=api_key,
            )
            calls = plan.get("tool_calls") or []
            if str(plan.get("action") or "") == "ready" or not calls:
                break
            if not isinstance(calls, list):
                raise ValueError("DeepSeek工具规划格式错误")
            executed_this_round = 0
            for call in calls[:4]:
                if not isinstance(call, dict):
                    continue
                name = str(call.get("name") or "")
                arguments = call.get("arguments") or {}
                if not isinstance(arguments, dict):
                    arguments = {}
                arguments = {**arguments, "_conversation_id": conversation_id}
                call_key = json.dumps({"name": name, "arguments": arguments}, ensure_ascii=False, sort_keys=True, default=str)
                if call_key in executed_calls:
                    continue
                executed_calls.add(call_key)
                result = self.tool_executor(name, arguments, scenario_id)
                gateway = (result.get("summary") or {}).get("ontology_gateway") if isinstance(result, dict) else None
                if not (
                    isinstance(gateway, dict)
                    and gateway.get("query_executed") is True
                    and gateway.get("status") in {"verified", "missing_after_query"}
                ):
                    raise ValueError("工具结果未通过Ontology Evidence Gateway确认，不能用于知识回答")
                executed_this_round += 1
                observations.append({"tool": name, "arguments": arguments, "result": result})
                trace.append({
                    "round": round_number,
                    "tool": name,
                    "arguments": arguments,
                    "summary": result.get("summary") or {},
                })
                for follow_up_name, follow_up_arguments in self._required_asset_trace_calls(
                    tool_name=name,
                    arguments=arguments,
                    result=result,
                ):
                    follow_up_key = json.dumps({"name": follow_up_name, "arguments": follow_up_arguments}, ensure_ascii=False, sort_keys=True, default=str)
                    if follow_up_key in executed_calls:
                        continue
                    executed_calls.add(follow_up_key)
                    follow_up_result = self.tool_executor(follow_up_name, follow_up_arguments, scenario_id)
                    follow_up_gateway = (follow_up_result.get("summary") or {}).get("ontology_gateway") if isinstance(follow_up_result, dict) else None
                    if not (
                        isinstance(follow_up_gateway, dict)
                        and follow_up_gateway.get("query_executed") is True
                        and follow_up_gateway.get("status") in {"verified", "missing_after_query"}
                    ):
                        raise ValueError("自动补充的资产规则证据未通过Ontology Evidence Gateway确认")
                    observations.append({"tool": follow_up_name, "arguments": follow_up_arguments, "result": follow_up_result})
                    trace.append({"round": round_number, "tool": follow_up_name, "arguments": follow_up_arguments, "summary": follow_up_result.get("summary") or {}, "auto_follow_up": True})
            if executed_this_round == 0:
                break
        if not observations:
            raise ValueError("DeepSeek未调用数据库或Ontology工具，无法形成可核验回答")
        return self._merge_tool_evidence(observations, scenario_id, messages[-1]["content"]), trace

    @staticmethod
    def _required_asset_trace_calls(
        *,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
    ) -> list[tuple[str, dict[str, Any]]]:
        """A material asset ranking is not explanatory evidence until its rule is read."""
        if tool_name not in {"get_monthly_summary", "explain_monthly_change"}:
            return []
        summary = result.get("summary") or {}
        asset_refs = [str(item) for item in summary.get("trace_asset_refs") or []][:4]
        period = str(summary.get("period") or arguments.get("period") or "")
        if not asset_refs or not re.fullmatch(r"20\d{2}-(?:0[1-9]|1[0-2])", period):
            return []
        year, month = map(int, period.split("-"))
        previous = f"{year - 1:04d}-12" if month == 1 else f"{year:04d}-{month - 1:02d}"
        calls: list[tuple[str, dict[str, Any]]] = []
        for asset_ref in asset_refs:
            calls.extend([
                ("get_asset_detail", {"asset_ref": asset_ref, "periods": [previous, period], "_conversation_id": arguments.get("_conversation_id")}),
                ("get_rule_execution", {"asset_ref": asset_ref, "period": period, "_conversation_id": arguments.get("_conversation_id")}),
            ])
        return calls

    @staticmethod
    def _mandatory_impairment_impact_plan(question: str) -> dict[str, Any] | None:
        """Route exact, single-asset impairment comparisons to the rule engine."""
        compact = question.replace(" ", "")
        if "减值" not in compact or not any(word in compact for word in ("折旧", "差多少", "差额", "相比", "比较")):
            return None
        asset_match = re.search(r"(?<!\d)(\d{6,}(?:-\d+)?)(?!\d)", compact)
        period_match = re.search(r"(?:(20\d{2})|(\d{2}))年(\d{1,2})月", compact)
        amount_match = re.search(r"减值(?:金额)?(?:为|是|=|约)?(\d+(?:\.\d+)?)\s*(万元|万|元)", compact)
        if asset_match is None or period_match is None or amount_match is None:
            return None
        year = int(period_match.group(1) or f"20{period_match.group(2)}")
        month = int(period_match.group(3))
        if not 1 <= month <= 12:
            return None
        amount = Decimal(amount_match.group(1)) * (Decimal("10000") if amount_match.group(2) in {"万", "万元"} else Decimal("1"))
        return {
            "action": "tool_calls", "understanding": "单资产减值影响必须由规则引擎复算后比较",
            "tool_calls": [{"name": "simulate_asset_impairment_impact", "arguments": {
                "asset_ref": asset_match.group(1), "period": f"{year:04d}-{month:02d}", "amount": format(amount, "f"),
            }}],
        }

    @staticmethod
    def _mandatory_variance_plan(question: str) -> dict[str, Any] | None:
        compact = question.replace(" ", "")
        if not any(word in compact for word in ("为什么", "原因", "上升", "下降", "突增", "变化")):
            return None
        match = re.search(r"(?:(20\d{2})|(\d{2}))年(\d{1,2})月", compact)
        if match is None:
            return None
        year = int(match.group(1) or f"20{match.group(2)}")
        month = int(match.group(3))
        if not 1 <= month <= 12:
            return None
        return {
            "action": "tool_calls", "understanding": "期间变化原因必须先取相邻月份与关键资产规则证据",
            "tool_calls": [{"name": "explain_monthly_change", "arguments": {"period": f"{year:04d}-{month:02d}", "top_n": 5}}],
        }

    def _plan_tools(
        self,
        *,
        messages: list[dict[str, str]],
        scenario_id: str,
        observations: list[dict[str, Any]],
        api_key: str,
    ) -> dict[str, Any]:
        catalog = self.tool_catalog()
        system = (
            "你是资产折旧平台的Question Understanding与Tool Planning Agent。"
            "你必须理解当前问题及多轮指代，并自主选择工具查询数据库或Ontology。"
            "不得直接回答事实问题，不得自行计算金额，不得编造工具。"
            "如果已有工具结果足以回答，action输出ready；否则action输出tool_calls。"
            "输出单个JSON对象：action、understanding、tool_calls。"
            "tool_calls是数组，每项包含name和arguments；name只能来自工具目录。"
            "月份未带年份时，必须使用platform_context.available_periods解析，禁止猜测其他年份。"
            "资产折旧方法必须以工具返回的depreciation_method或规则执行为准，不能根据常识猜测。"
            "所属单位、成本中心、利润中心是不同字段；询问其中任一字段时，必须读取并引用同名字段，禁止相互替代。"
            "用户使用平台中未确认的业务类别词时，先调用resolve_business_term；没有匹配时应停止继续猜测，"
            "最终请用户确认映射到哪个真实资产类别。只有类别已确认后才能调用get_category_policy回答折旧方法。"
            "如果resolve_business_term精确匹配到AssetCategory，且用户询问怎么折旧，必须继续调用get_category_policy；"
            "取得类别政策中的method后，再调用get_calculation_rule补充正式计算公式，然后才能action=ready。"
            "如果resolve_business_term匹配到CostCenter、ProfitCenter、Department、Block或其他Ontology对象，"
            "且用户询问属性或关系，必须继续调用get_ontology_node读取完整属性和相邻关系。"
            "用户询问单项资产指定月份的折旧时，必须在get_asset_detail的periods中传入全部被询问月份。"
            "该工具会返回实际命中的规则输入及资产到月度驱动的Ontology路径；证据中已有规则输入或月度驱动时，最终回答不得声称产量、储量或其他参数未提供。"
            "每次工具调用都必须通过Ontology Evidence Gateway执行受控图查询；只有网关状态为verified或missing_after_query的结果可以作为回答依据。"
            "若状态为missing_after_query，只能说明已查询后确认缺失，不能猜测或补写数据。"
            "用户要求反向推演时调用plan_reverse_depreciation；它只试算，不会创建场景。"
            "用户询问某项资产在指定月份计提减值后与当前折旧相差多少时，必须调用simulate_asset_impairment_impact；"
            "该工具会执行规则引擎试算，不创建草稿或场景，最终回答必须引用它返回的基准、试算和差额。"
            "用户明确提出规则场景假设时调用draft_what_if_scenario；它只生成待确认草稿，绝不创建场景。"
            "用户要求比较场景金额时调用compare_scenarios。模型绝不能调用确认、取消、删除或任何写入接口。"
            "当get_monthly_summary按资产返回关键资产时，系统会自动补充这些资产的相邻月份明细和当月规则执行；最终不得把这种未主动阅读过的证据误称为数据缺失。"
            "任何‘某年某月为何上升、下降、突增或变化’问题，系统会先调用explain_monthly_change并自动补关键资产规则；不得跳过该证据直接回答。"
        )
        planner_context = {
            "scenario_id": scenario_id,
            "platform_context": self.context_loader(scenario_id),
            "tools": catalog,
            "previous_tool_results": observations[-8:],
            "instruction": "优先用最少工具取得完整证据；涉及金额时调用汇总或资产明细工具，涉及规则时调用规则工具。",
        }
        request_messages = [
            {"role": "system", "content": system},
            *messages,
            {"role": "user", "content": json.dumps(planner_context, ensure_ascii=False, default=str)},
        ]
        payload = {
            "model": self.model,
            "messages": request_messages,
            "temperature": 0,
            "max_tokens": 900,
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=45) as response:
            data = json.loads(response.read().decode("utf-8"))
        content = str(data["choices"][0]["message"].get("content") or "").strip()
        try:
            return self._decode_tool_plan(content)
        except ValueError:
            # Compatible model endpoints occasionally emit prose despite
            # response_format. Retry once with a minimal corrective request;
            # never use that prose as business evidence.
            retry_payload = {
                **payload,
                "messages": [
                    {"role": "system", "content": "上一轮工具计划无法解析。只输出一个JSON对象，不要思考过程、解释、Markdown或代码围栏。"},
                    *request_messages,
                ],
            }
            retry_request = urllib.request.Request(
                f"{self.base_url}/chat/completions",
                data=json.dumps(retry_payload, ensure_ascii=False).encode("utf-8"),
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(retry_request, timeout=45) as response:
                retry_data = json.loads(response.read().decode("utf-8"))
            retry_content = str(retry_data["choices"][0]["message"].get("content") or "").strip()
            try:
                return self._decode_tool_plan(retry_content)
            except ValueError:
                return self._fallback_tool_plan(messages=messages, scenario_id=scenario_id)

    @staticmethod
    def _decode_tool_plan(content: str) -> dict[str, Any]:
        """Accept common model wrappers but never relax the later tool allow-list checks."""
        candidates = [content]
        if "```" in content:
            parts = content.split("```")
            candidates.extend(part.removeprefix("json").strip() for part in parts[1::2])
        first_object = content.find("{")
        if first_object >= 0:
            candidates.append(content[first_object:])
        for candidate in candidates:
            if not candidate:
                continue
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                try:
                    parsed, _ = json.JSONDecoder().raw_decode(candidate)
                except json.JSONDecodeError:
                    # Some compatible endpoints ignore json_object and return a
                    # Python-style dict. literal_eval is data-only, then the
                    # normal registered-tool validation still applies.
                    try:
                        parsed = ast.literal_eval(candidate)
                    except (SyntaxError, ValueError):
                        continue
            if isinstance(parsed, dict):
                return parsed
            if isinstance(parsed, str):
                try:
                    nested = json.loads(parsed)
                except json.JSONDecodeError:
                    try:
                        nested = ast.literal_eval(parsed)
                    except (SyntaxError, ValueError):
                        continue
                if isinstance(nested, dict):
                    return nested
        raise ValueError("DeepSeek工具规划未返回可解析的JSON对象")

    @staticmethod
    def _fallback_tool_plan(*, messages: list[dict[str, str]], scenario_id: str) -> dict[str, Any]:
        """Conservative deterministic planner used only after two malformed model replies."""
        question = messages[-1]["content"] if messages else ""
        compact = question.replace(" ", "")
        scenario_ids = list(dict.fromkeys(re.findall(r"(?:BASELINE|SCN-\d+)", question, flags=re.IGNORECASE)))
        impairment_plan = KnowledgeChatService._mandatory_impairment_impact_plan(question)
        if impairment_plan is not None:
            return impairment_plan
        if any(word in compact for word in ("反向推演", "降低", "减少", "增加", "提高")) and any(word in compact for word in ("折旧", "金额", "目标")):
            return {"action": "tool_calls", "understanding": "回退为受控反向推演", "tool_calls": [{"name": "plan_reverse_depreciation", "arguments": {"question": question}}]}
        if any(word in compact for word in ("对比", "比较", "差异")) and scenario_ids:
            baseline = next((item for item in scenario_ids if item.upper() == "BASELINE"), "BASELINE")
            compared = [item for item in scenario_ids if item != baseline]
            if compared:
                return {"action": "tool_calls", "understanding": "回退为受控场景对比", "tool_calls": [{"name": "compare_scenarios", "arguments": {"baseline_scenario_id": baseline, "scenario_ids": compared}}]}
        asset_match = re.search(r"(?<!\d)(\d{6,}(?:-\d+)?)(?!\d)", question)
        if asset_match:
            asset_ref = asset_match.group(1)
            periods = re.findall(r"20\d{2}-(?:0[1-9]|1[0-2])", question)
            return {"action": "tool_calls", "understanding": "回退为受控资产查询", "tool_calls": [{"name": "get_asset_detail", "arguments": {"asset_ref": asset_ref, "periods": periods}}]}
        return {"action": "tool_calls", "understanding": "回退为数据快照核验", "tool_calls": [{"name": "get_source_snapshot", "arguments": {}}]}

    @staticmethod
    def _merge_tool_evidence(
        observations: list[dict[str, Any]],
        scenario_id: str,
        question: str,
    ) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        sources: list[dict[str, Any]] = []
        seen: set[str] = set()
        for observation in observations:
            result = observation.get("result") or {}
            result_items = result.get("items") or []
            result_sources = result.get("sources") or []
            if not result_items:
                result_items = [{
                    "type": "empty_tool_result",
                    "title": f"{observation.get('tool')}查询无匹配",
                    "text": (
                        f"工具参数：{json.dumps(observation.get('arguments') or {}, ensure_ascii=False)}；"
                        f"查询结果摘要：{json.dumps(result.get('summary') or {}, ensure_ascii=False)}。"
                    ),
                }]
            for index, item in enumerate(result_items):
                if not isinstance(item, dict):
                    continue
                fingerprint = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                evidence_number = len(items) + 1
                items.append({"evidence_number": evidence_number, **item})
                source = result_sources[index] if index < len(result_sources) and isinstance(result_sources[index], dict) else {}
                sources.append({
                    "id": f"evidence-{evidence_number}",
                    "label": source.get("label") or item.get("title") or f"证据{evidence_number}",
                    "kind": source.get("kind") or item.get("type") or "tool_result",
                    **{key: value for key, value in source.items() if key not in {"id", "label", "kind"}},
                })
                if len(items) >= 24:
                    break
            if len(items) >= 24:
                break
        return {
            "scenario_id": scenario_id,
            "question": question,
            "items": items,
            "sources": sources,
            "summary": {"evidence_count": len(items), "tool_result_count": len(observations)},
        }

    def _stream_model(self, *, messages: list[dict[str, str]], evidence: dict[str, Any], api_key: str) -> Iterator[str]:
        history = messages[:-1]
        question = messages[-1]["content"]
        system = (
            "你是企业资产折旧知识助手。你只能依据本次提供的数据库和Ontology证据回答，"
            "不得编造资产、金额、规则、字段或关系，不得自行替换业务口径。"
            "证据不足时明确说明缺少什么。回答使用简洁中文Markdown；引用证据时使用[证据N]标记。"
            "多轮对话中的指代可参考历史消息，但事实必须以当前证据包为准。"
            "若折旧明细证据包含规则输入或Ontology月度驱动，必须据实说明其中的参数与关系路径；不得声称这些参数未提供。"
            "所属单位、成本中心、利润中心是不同业务字段，必须按证据中的同名字段回答，不能互相替代。"
            "每条证据都含Ontology网关回执。只有回执状态verified或missing_after_query才可引用；后者必须明确为已查询后确认缺失。"
            "不得把‘本轮原始汇总工具没有展开’表述成‘数据缺失’；如证据包有自动补充的资产明细或规则执行，必须据此解释。"
        )
        evidence_text = json.dumps(evidence.get("items") or [], ensure_ascii=False, default=str)
        request_messages = [
            {"role": "system", "content": system},
            *history,
            {
                "role": "user",
                "content": f"用户问题：{question}\n\n当前场景：{evidence.get('scenario_id')}\n核验证据：{evidence_text}",
            },
        ]
        payload = {
            "model": self.model,
            "messages": request_messages,
            "temperature": 0.1,
            "max_tokens": 1_600,
            "stream": True,
            "thinking": {"type": "disabled"},
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=45) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                if not data:
                    continue
                event = json.loads(data)
                delta = event.get("choices", [{}])[0].get("delta", {})
                text = delta.get("content") or ""
                if text:
                    yield str(text)

    @staticmethod
    def _messages(raw_messages: Any) -> list[dict[str, str]]:
        if not isinstance(raw_messages, list):
            return []
        result: list[dict[str, str]] = []
        for item in raw_messages:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "")
            content = str(item.get("content") or "").strip()
            if role in {"user", "assistant"} and content:
                result.append({"role": role, "content": content[:8_000]})
        return result

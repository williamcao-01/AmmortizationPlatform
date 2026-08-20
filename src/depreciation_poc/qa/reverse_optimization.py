from __future__ import annotations

import copy
import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Callable

from ortools.sat.python import cp_model


CENT = Decimal("0.01")


def _cents(value: Decimal | str | int | float) -> int:
    return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _money(value: int) -> Decimal:
    return (Decimal(value) / 100).quantize(CENT)


@dataclass(frozen=True)
class ReverseActionOption:
    option_id: str
    capability_id: str
    template_id: str
    target_object: str
    label_cn: str
    solution_kind: str
    risk_weight: int
    affected_object_ids: tuple[str, ...]
    assumptions: tuple[dict[str, Any], ...]
    adjustable_field: str | None = None
    minimum_value: Decimal = Decimal("0")
    maximum_value: Decimal = Decimal("0")
    # Full action can be the lower numeric bound (for example production -> 0).
    full_value: Decimal | None = None
    value_effect_direction: int = 1
    value_scale: int = 100
    notice_cn: str = ""

    @property
    def is_continuous(self) -> bool:
        return self.adjustable_field is not None

    def assumptions_at(self, value: Decimal | None = None) -> list[dict[str, Any]]:
        assumptions = copy.deepcopy(list(self.assumptions))
        if self.adjustable_field is not None and value is not None:
            assumptions[0][self.adjustable_field] = format(value, "f")
        return assumptions

    def full_assumptions(self) -> list[dict[str, Any]]:
        return self.assumptions_at(self.full_value if self.is_continuous else None)


@dataclass(frozen=True)
class PreparedOption:
    option: ReverseActionOption
    signed_max_effect_cents: int
    usable_effect_cents: int
    effect_direction: int


class ReverseOptimizationEngine:
    """Finds auditable reverse-depreciation recommendations and verifies every result."""

    def __init__(
        self,
        *,
        simulate: Callable[[list[dict[str, Any]], dict[str, Any]], dict[str, Any]],
        timeout_seconds: float = 15.0,
        max_actions: int = 3,
    ) -> None:
        self.simulate = simulate
        self.timeout_seconds = timeout_seconds
        self.max_actions = max_actions

    def optimize(
        self,
        *,
        plan: dict[str, Any],
        baseline_amount: Decimal,
        options: list[ReverseActionOption],
    ) -> dict[str, Any]:
        started = time.monotonic()
        target_amount = Decimal(str(plan["target_amount"]))
        requested_effect = _cents(target_amount - baseline_amount)
        accounting = [item for item in options if item.solution_kind == "accounting"]
        operational = [item for item in options if item.solution_kind == "operational_fallback"]
        # Business policy: accounting changes are the primary strategy.
        # Operating parameters are only a clearly labelled fallback when the
        # approved accounting actions cannot meet the target exactly.
        # Candidate validation calls the full depreciation engine, so its time
        # is deliberately outside the bounded CP-SAT search window.
        accounting_result = self._solve_group(
            plan=plan,
            baseline_amount=baseline_amount,
            target_amount=target_amount,
            requested_effect=requested_effect,
            options=accounting,
        )
        exact_accounting = bool(accounting_result["exact"])
        operational_result: dict[str, Any] | None = None
        if not exact_accounting and operational:
            operational_result = self._solve_group(
                plan=plan,
                baseline_amount=baseline_amount,
                target_amount=target_amount,
                requested_effect=requested_effect,
                options=operational,
                max_recommendations=1,
            )
        exact_operational = bool(operational_result and operational_result["exact"])
        elapsed_ms = int((time.monotonic() - started) * 1000)
        recommendations = accounting_result["recommendations"]
        fallback_recommendations = operational_result["recommendations"] if operational_result else []
        overall_exact = exact_accounting or exact_operational
        best_error = min(
            Decimal(str(accounting_result["best_error"])),
            Decimal(str(operational_result["best_error"])) if operational_result else Decimal(str(accounting_result["best_error"])),
        )
        feasibility_cn = accounting_result["feasibility_cn"]
        if not exact_accounting and exact_operational:
            feasibility_cn = "会计类动作未能精确达标；已找到并经折旧规则引擎复算的经营驱动备选方案。"
        return {
            "recommendations": recommendations,
            "operational_fallback_recommendations": fallback_recommendations,
            "optimization": {
                "solver": "OR-Tools CP-SAT",
                "timeout_seconds": self.timeout_seconds,
                "elapsed_ms": elapsed_ms,
                "target_error": f"{best_error:.2f}",
                "is_exact": overall_exact,
                "accounting_is_exact": exact_accounting,
                "solver_status": accounting_result["solver_status"],
                "candidate_space": {
                    "accounting_action_options": accounting_result["prepared_count"],
                    "operational_action_options": len(operational),
                    "rejected_action_options": accounting_result["rejected_count"],
                    "verified_recommendation_count": len(recommendations),
                    "verified_operational_fallback_count": len(fallback_recommendations),
                },
                "coverage_cn": (
                    "已从当前范围全部可作用对象生成会计类动作变量，并使用 OR-Tools 按目标误差、业务风险、动作数、"
                    "影响对象数和调整金额依次求解；每套结果均已由折旧规则引擎复算。"
                ),
                "operational_fallback_used": bool(operational_result),
                "operational_fallback_status": operational_result["solver_status"] if operational_result else None,
                "accounting_search_skipped": False,
            },
            "candidate_evaluation": {
                "generated_count": len(options),
                "executed_count": accounting_result["simulation_count"] + (operational_result["simulation_count"] if operational_result else 0),
                "valid_count": accounting_result["prepared_count"] + (operational_result["prepared_count"] if operational_result else 0),
                "rejected_count": accounting_result["rejected_count"] + (operational_result["rejected_count"] if operational_result else 0),
                "feasible_exact": overall_exact,
                "closest_gap": f"{best_error:.2f}",
                "maximum_directional_change": accounting_result["maximum_effect"],
                "coverage_cn": "不再使用前 16 项资产、前 2 个区块或前 6 项组合截断；所有符合 Ontology 动作能力和规则边界的对象均进入优化变量集合。",
                "feasibility_cn": feasibility_cn,
            },
        }

    def _solve_group(
        self,
        *,
        plan: dict[str, Any],
        baseline_amount: Decimal,
        target_amount: Decimal,
        requested_effect: int,
        options: list[ReverseActionOption],
        max_recommendations: int = 3,
    ) -> dict[str, Any]:
        direction = 1 if requested_effect >= 0 else -1
        prepared: list[PreparedOption] = []
        rejected_count = 0
        simulation_count = 0
        for option in options:
            try:
                outcome = self.simulate(option.full_assumptions(), plan)
                simulation_count += 1
            except (ValueError, ArithmeticError):
                rejected_count += 1
                continue
            effect = _cents(Decimal(str(outcome["target_amount"])) - baseline_amount)
            if effect == 0 or effect * direction <= 0:
                rejected_count += 1
                continue
            prepared.append(PreparedOption(option, effect, abs(effect), direction))

        if not prepared or requested_effect == 0:
            return {
                "recommendations": [], "prepared_count": len(prepared), "rejected_count": rejected_count,
                "simulation_count": simulation_count, "best_error": f"{abs(_money(requested_effect)):.2f}",
                "maximum_effect": "0.00", "exact": requested_effect == 0,
                "solver_status": "NO_USABLE_ACTIONS", "feasibility_cn": "当前范围内没有能按目标方向有效改变折旧的已注册动作。",
            }

        # Start the bounded optimization window only after all action effects
        # have been measured with the rule engine.  The measurements are
        # evidence work, not CP-SAT search work.
        deadline = time.monotonic() + self.timeout_seconds
        recommendations: list[dict[str, Any]] = []
        patterns: list[list[int]] = []
        statuses: list[str] = []
        best_error: int | None = None
        for rank in range(1, max_recommendations + 1):
            result = self._solve_one(prepared, abs(requested_effect), patterns, deadline)
            if result is None:
                break
            selected, expected_error, status = result
            statuses.append(status)
            selected_indices = [index for index, _effect in selected]
            patterns.append([1 if index in selected_indices else 0 for index in range(len(prepared))])
            recommendation = self._materialize_solution(
                selected=[(prepared[index], effect_cents) for index, effect_cents in selected],
                plan=plan,
                baseline_amount=baseline_amount,
                target_amount=target_amount,
                rank=rank,
            )
            if recommendation is None:
                continue
            simulation_count += int(recommendation.pop("_simulation_count", 0))
            recommendations.append(recommendation)
            final_error = abs(_cents(Decimal(str(recommendation["target_amount"])) - target_amount))
            best_error = final_error if best_error is None else min(best_error, final_error)

        maximum_effect = max((item.usable_effect_cents for item in prepared), default=0)
        exact = any(abs(_cents(Decimal(str(item["gap"])))) <= 1 for item in recommendations)
        timed_out = any(status == "FEASIBLE" for status in statuses)
        solver_status = "OPTIMAL" if statuses and not timed_out else "FEASIBLE_TIMEOUT" if recommendations else "NO_FEASIBLE_SOLUTION"
        if exact:
            feasibility_cn = "已找到并经折旧规则引擎复算的精确达标会计类方案。"
        elif recommendations:
            feasibility_cn = "当前规则边界内未找到精确达标的会计类方案，已返回最接近且经复算验证的方案。"
        else:
            feasibility_cn = "当前会计类动作在规则边界内无法形成可行方案。"
        return {
            "recommendations": recommendations,
            "prepared_count": len(prepared), "rejected_count": rejected_count,
            "simulation_count": simulation_count,
            "best_error": f"{_money(best_error if best_error is not None else abs(requested_effect)):.2f}",
            "maximum_effect": f"{_money(maximum_effect):.2f}",
            "exact": exact,
            "solver_status": solver_status,
            "feasibility_cn": feasibility_cn,
        }

    def _solve_one(
        self,
        prepared: list[PreparedOption],
        needed_effect: int,
        patterns: list[list[int]],
        deadline: float,
    ) -> tuple[list[tuple[int, int]], int, str] | None:
        model = cp_model.CpModel()
        use = [model.NewBoolVar(f"use_{index}") for index in range(len(prepared))]
        effects: list[cp_model.IntVar] = []
        for index, item in enumerate(prepared):
            if item.option.is_continuous:
                value = model.NewIntVar(0, item.usable_effect_cents, f"effect_{index}")
                model.Add(value <= item.usable_effect_cents * use[index])
                model.Add(value >= use[index])
            else:
                value = model.NewIntVar(0, item.usable_effect_cents, f"effect_{index}")
                model.Add(value == item.usable_effect_cents * use[index])
            effects.append(value)
        model.Add(sum(use) <= self.max_actions)
        by_target: dict[str, list[int]] = {}
        for index, item in enumerate(prepared):
            if item.option.target_object:
                by_target.setdefault(item.option.target_object, []).append(index)
        for indices in by_target.values():
            if len(indices) > 1:
                model.Add(sum(use[index] for index in indices) <= 1)
        for pattern in patterns:
            model.Add(sum(use[index] if selected else 1 - use[index] for index, selected in enumerate(pattern)) <= len(use) - 1)

        slack_low = model.NewIntVar(0, max(needed_effect, sum(item.usable_effect_cents for item in prepared)), "slack_low")
        slack_high = model.NewIntVar(0, max(needed_effect, sum(item.usable_effect_cents for item in prepared)), "slack_high")
        model.Add(sum(effects) + slack_low - slack_high == needed_effect)
        absolute_error = slack_low + slack_high
        risk = sum(item.option.risk_weight * use[index] for index, item in enumerate(prepared))
        action_count = sum(use)
        impacted_count = sum(len(item.option.affected_object_ids) * use[index] for index, item in enumerate(prepared))
        adjustment = sum(effects)
        last_solver: cp_model.CpSolver | None = None
        statuses: list[int] = []
        for objective in (absolute_error, risk, action_count, impacted_count, adjustment):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            model.Minimize(objective)
            solver = cp_model.CpSolver()
            solver.parameters.max_time_in_seconds = max(0.1, remaining)
            solver.parameters.num_search_workers = 8
            status = solver.Solve(model)
            statuses.append(status)
            if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                break
            value = int(solver.Value(objective))
            model.Add(objective == value)
            last_solver = solver
        if last_solver is None:
            return None
        selected = [
            (index, int(last_solver.Value(effects[index])))
            for index, item in enumerate(prepared)
            if last_solver.Value(use[index])
        ]
        if not selected:
            return None
        error = int(last_solver.Value(absolute_error))
        label = "OPTIMAL" if all(status == cp_model.OPTIMAL for status in statuses) else "FEASIBLE"
        return selected, error, label

    def _materialize_solution(
        self,
        *,
        selected: list[tuple[PreparedOption, int]],
        plan: dict[str, Any],
        baseline_amount: Decimal,
        target_amount: Decimal,
        rank: int,
    ) -> dict[str, Any] | None:
        actions: list[dict[str, Any]] = []
        all_assumptions: list[dict[str, Any]] = []
        continuous: list[tuple[PreparedOption, Decimal]] = []
        simulations = 0
        for item, expected_effect_cents in selected:
            desired_effect = Decimal(expected_effect_cents) / 100
            if item.option.is_continuous:
                amount, attempts = self._calibrate_option(item, desired_effect, baseline_amount, plan)
                simulations += attempts
                assumptions = item.option.assumptions_at(amount)
                continuous.append((item, amount))
            else:
                assumptions = item.option.assumptions_at()
            all_assumptions.extend(assumptions)
            action = {
                "label_cn": item.option.label_cn,
                "template_id": item.option.template_id,
                "target_object": item.option.target_object,
                "notice_cn": item.option.notice_cn,
                "capability_id": item.option.capability_id,
                "risk_weight": item.option.risk_weight,
            }
            if item.option.is_continuous:
                parameter_label = "建议减值金额" if item.option.template_id == "straight_impairment" else f"建议{self._field_label(item.option.adjustable_field)}"
                action["recommended_parameters"] = [{
                    "field": item.option.adjustable_field,
                    "label_cn": f"{parameter_label}：{amount:,.2f}",
                }]
            actions.append(action)
        try:
            outcome = self.simulate(all_assumptions, plan)
            simulations += 1
        except (ValueError, ArithmeticError):
            return None
        actual = Decimal(str(outcome["target_amount"]))
        gap = actual - target_amount
        if abs(gap) > CENT and continuous:
            corrected, correction_attempts = self._correct_combination(
                continuous=continuous,
                all_assumptions=all_assumptions,
                plan=plan,
                target_amount=target_amount,
            )
            simulations += correction_attempts
            if corrected is not None:
                all_assumptions, outcome = corrected
                actual = Decimal(str(outcome["target_amount"]))
                gap = actual - target_amount
        kind = selected[0][0].option.solution_kind
        return {
            "recommendation_id": f"{kind.upper()}-{rank:02d}",
            "recommendation_number": rank,
            "rank": rank,
            "solution_kind": kind,
            "selection_label_cn": "综合最优方案" if rank == 1 else "综合备选方案",
            "selection_reason_cn": "按目标偏差、业务干预风险、动作数、影响对象数和调整金额依次求解，并经规则引擎复算。",
            "strategy_key": "+".join(sorted({str(action["template_id"]) for action in actions})),
            "strategy_label_cn": " + ".join(self._template_label(str(action["template_id"])) for action in actions),
            "actions": actions,
            "assumptions": all_assumptions,
            "affected_object_count": len({asset for item, _ in selected for asset in item.option.affected_object_ids}),
            "target_amount": f"{actual:.2f}",
            "gap": f"{gap:.2f}",
            "effect": f"{actual - baseline_amount:.2f}",
            "score": {
                "target_error": f"{abs(gap):.2f}",
                "business_risk": sum(item.option.risk_weight for item, _ in selected),
                "action_count": len(actions),
            },
            "rule_execution_trace": outcome.get("rule_execution_trace", []),
            "scenario_written": False,
            "assumption_notice_cn": "本方案仅为临时业务假设，尚未创建或保存 What-if 场景。",
            "_simulation_count": simulations,
        }

    def _calibrate_option(
        self,
        item: PreparedOption,
        target_effect: Decimal,
        baseline_amount: Decimal,
        plan: dict[str, Any],
    ) -> tuple[Decimal, int]:
        option = item.option
        low, high = option.minimum_value, option.maximum_value
        maximum_effect = Decimal(item.usable_effect_cents) / 100
        ratio = min(Decimal("1"), max(Decimal("0"), target_effect / maximum_effect)) if maximum_effect else Decimal("0")
        # The full effect has already been measured by the rule engine.  For
        # the registered driver actions the target-period response is linear,
        # so interpolate from the no-effect endpoint and verify once before
        # falling back to binary correction.  This avoids 28 full-horizon
        # forecasts for a simple company-level production adjustment.
        full_value = option.full_value if option.full_value is not None else option.maximum_value
        neutral_value = option.maximum_value if full_value <= option.minimum_value else option.minimum_value
        initial_value = neutral_value + (full_value - neutral_value) * ratio
        initial_value = min(high, max(low, initial_value))
        precision = Decimal("1") / Decimal(option.value_scale)
        initial_value = initial_value.quantize(precision)
        outcome = self.simulate(option.assumptions_at(initial_value), plan)
        attempts = 1
        initial_effect = (Decimal(str(outcome["target_amount"])) - baseline_amount) * item.effect_direction
        if abs(initial_effect - target_effect) <= CENT:
            return initial_value.quantize(precision), attempts

        best_value, best_error = initial_value, abs(initial_effect - target_effect)
        # A single secant-style correction handles rounding and small rule
        # discontinuities while retaining the fast path for continuous drivers.
        corrected_value = initial_value + (full_value - neutral_value) * (target_effect - initial_effect) / maximum_effect
        corrected_value = min(high, max(low, corrected_value)).quantize(precision)
        if corrected_value != initial_value:
            outcome = self.simulate(option.assumptions_at(corrected_value), plan)
            attempts += 1
            corrected_effect = (Decimal(str(outcome["target_amount"])) - baseline_amount) * item.effect_direction
            corrected_error = abs(corrected_effect - target_effect)
            if corrected_error <= CENT:
                return corrected_value, attempts
            if corrected_error < best_error:
                best_value, best_error = corrected_value, corrected_error
        for _ in range(16):
            value = (low + high) / 2
            outcome = self.simulate(option.assumptions_at(value), plan)
            attempts += 1
            effect = (Decimal(str(outcome["target_amount"])) - baseline_amount) * item.effect_direction
            error = abs(effect - target_effect)
            if error < best_error:
                best_value, best_error = value, error
            if effect < target_effect:
                if option.value_effect_direction > 0:
                    low = value
                else:
                    high = value
            else:
                if option.value_effect_direction > 0:
                    high = value
                else:
                    low = value
        return best_value.quantize(precision), attempts

    def _correct_combination(
        self,
        *,
        continuous: list[tuple[PreparedOption, Decimal]],
        all_assumptions: list[dict[str, Any]],
        plan: dict[str, Any],
        target_amount: Decimal,
    ) -> tuple[tuple[list[dict[str, Any]], dict[str, Any]], int] | tuple[None, int]:
        item, _value = continuous[0]
        option = item.option
        index = next((idx for idx, assumption in enumerate(all_assumptions) if assumption.get("template_id") == option.template_id and str(assumption.get("asset_id") or assumption.get("reference_asset_id") or assumption.get("block_id") or assumption.get("company") or "") == option.target_object), None)
        if index is None:
            return None, 0
        low, high = option.minimum_value, option.maximum_value
        best: tuple[list[dict[str, Any]], dict[str, Any]] | None = None
        best_error = Decimal("Infinity")
        attempts = 0
        for _ in range(28):
            value = (low + high) / 2
            assumptions = copy.deepcopy(all_assumptions)
            assumptions[index][str(option.adjustable_field)] = format(value, "f")
            outcome = self.simulate(assumptions, plan)
            attempts += 1
            amount = Decimal(str(outcome["target_amount"]))
            error = abs(amount - target_amount)
            if error < best_error:
                best, best_error = (assumptions, outcome), error
            if (amount - target_amount) * item.effect_direction < 0:
                if option.value_effect_direction > 0:
                    low = value
                else:
                    high = value
            else:
                if option.value_effect_direction > 0:
                    high = value
                else:
                    low = value
        return best, attempts

    @staticmethod
    def _field_label(field: str | None) -> str:
        return {
            "amount": "调整金额",
            "production": "目标月产量",
            "reserves": "目标月剩余储量",
            "total_amortization": "目标月总摊销额",
        }.get(str(field), str(field or "参数"))

    @staticmethod
    def _template_label(template_id: str) -> str:
        return {
            "straight_new_asset": "新增资产",
            "straight_impairment": "减值后重算",
            "straight_accelerated": "加速折旧",
            "straight_start_rule": "开始计提规则调整",
            "production_driver": "产量/储量调整",
            "workload_driver": "工作量法摊销调整",
        }.get(template_id, template_id)

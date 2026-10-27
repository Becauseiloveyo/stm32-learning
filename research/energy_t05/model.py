"""Second-grid scheduler calling the accepted T04 controller and raw-observation model.

Environment is read only at the time it can affect service. No dry truth, future
harvest, or future connection outcome is supplied to the admission policy.
"""
from dataclasses import asdict
import math

from research.state_machine.machine import StateMachine, OBSERVATION_KEYS
from theory.model import Parameters, simulate as observe
from research.energy_t05.contracts import CATEGORIES, EPS, LABEL, required_battery_wh


class Scheduler:
    def __init__(self, scenario, policy):
        self.config = scenario.validate()
        if policy not in ("fixed", "demand"):
            raise ValueError("unknown scheduling policy")
        self.scenario, self.policy, self.e = scenario, policy, scenario.energy
        self.machine = StateMachine(self.config)
        self.config_sha = self.config.sha256
        self.store = self.e.initial_wh
        self.powered, self.mode = False, "stopped"
        self.boot_count, self.boot_origin = 0, 0
        self.last_event_wall = None
        self.reserved_end_wall = None
        self.active = None
        self.pending, self.requests = {}, []
        self.actions, self.events, self.ledger, self.controls, self.modes = [], [], [], [], []
        self.opportunities = []
        self.install_queue = []
        self.wifi_used = {}
        self.id_counter = 0

    def can_emit(self, t):
        return self.last_event_wall is None or t > self.last_event_wall

    def emit(self, kind, t, **fields):
        if not self.can_emit(t):
            raise AssertionError("two state-machine events at the same wall second")
        self.id_counter += 1
        event = {"event_version": "1.0", "kind": kind, "event_id": f"e{self.id_counter}",
                 "boot_id": f"boot-{self.boot_count}", "monotonic_s": t - self.boot_origin,
                 "config_sha256": self.config_sha, **fields}
        before = self.machine.snapshot()
        output = self.machine.handle(event)
        self.last_event_wall = t
        idle_expired = (before["weak_cycles"] > 0 and output["weak_cycles"] == 0
                        and before["pending_cycle"] is None and before["last_evidence_end_s"] is not None
                        and event["monotonic_s"] - before["last_evidence_end_s"] > self.config.policy.max_evidence_gap_s
                        and kind in ("tick", "start_cycle"))
        self.events.append({"wall_s": t, "event": event, "output": output, "idle_evidence_expired": idle_expired})
        if output["collection_authorized"] or output["config_sha256"] != self.config_sha:
            raise AssertionError("research configuration or authorization changed")
        if output["assessment"] and output["assessment"]["collection_authorized"]:
            raise AssertionError("assessment authorized collection")
        return output

    def identity(self):
        return {"batch_id": self.machine.batch_id, "installation_id": self.machine.installation_id}

    def change_mode(self, mode, t, reason):
        if mode != self.mode:
            self.modes.append({"wall_s": t, "from": self.mode, "to": mode, "reason": reason, "store_wh": self.store})
            self.mode = mode

    def account(self, start, end, requested, solar_wh=0.0, tag="", phase=""):
        """Load-priority balance; zero-duration actuator/boot impulses cannot borrow solar."""
        before = self.store
        available = before + solar_wh
        served = {}
        for key in CATEGORIES:
            amount = requested.get(key, 0.0)
            if amount:
                paid = min(amount, max(0, available) * self.e.eta)
                served[key] = paid
                available -= paid / self.e.eta
        spill = max(0, available - self.e.capacity_wh)
        self.store = max(0, min(self.e.capacity_wh, available))
        demand = sum(requested.values())
        paid = sum(served.values())
        unmet = max(0, demand - paid)
        dt = end - start
        signature = (self.mode, tag, phase, tuple(requested.items()), solar_wh, spill > EPS, unmet > EPS)
        row = {"start_s": start, "end_s": end, "mode": self.mode, "action_id": tag, "phase": phase,
               "store_start_wh": before, "solar_battery_wh": solar_wh,
               "requested_load_wh": dict(requested), "served_load_wh": served,
               "unmet_load_wh": unmet, "conversion_loss_wh": paid / self.e.eta - paid,
               "spill_wh": spill, "store_end_wh": self.store}
        if (dt and self.ledger and self.ledger[-1].get("_signature") == signature
                and self.ledger[-1]["end_s"] == start and self.ledger[-1]["start_s"] < start):
            old = self.ledger[-1]
            old["end_s"], old["store_end_wh"] = end, self.store
            for key in ("solar_battery_wh", "unmet_load_wh", "conversion_loss_wh", "spill_wh"):
                old[key] += row[key]
            for key in ("requested_load_wh", "served_load_wh"):
                for name, amount in row[key].items():
                    old[key][name] = old[key].get(name, 0) + amount
        else:
            row["_signature"] = signature if dt else None
            self.ledger.append(row)
        if self.active and tag == self.active["id"]:
            for key, amount in served.items():
                if key not in ("background_run", "background_stop", "misc"):
                    self.active["paid_components_wh"][key] = self.active["paid_components_wh"].get(key, 0) + amount
        return unmet

    def terminal(self, request, status, t):
        request["status"], request["finished_s"] = status, t
        request["wait_s"] = t - request["at_s"]
        self.pending.pop(request["id"], None)

    def close_vent(self, t):
        if self.active and self.active["kind"] == "measurement" and self.active["vent_open"]:
            gap = self.account(t, t, {"vent_close": self.e.vent_close_j / 3600}, tag=self.active["id"], phase="vent_close")
            self.active["vent_open"] = False
            if gap > EPS:
                raise AssertionError("reserved safe-exit energy missing")

    def abort(self, t, reason):
        if self.active:
            self.close_vent(t)
            self.active["outcome"], self.active["ended_s"] = reason, t
            request = self.pending.get(self.active["request_id"])
            if request:
                self.terminal(request, reason, t)
            self.active = None

    def invalidate(self, t, reason):
        self.abort(t, reason)
        for request in list(self.pending.values()):
            self.terminal(request, reason, t)

    def boot_or_stop(self, t):
        if self.powered and self.store <= self.e.stop_wh + EPS and self.can_emit(t):
            self.invalidate(t, "cancelled_power_stop")
            if self.machine.installation_id is not None and self.can_emit(t):
                self.emit("clip", t, **self.identity())
            self.powered = False
            self.install_queue.clear()
            self.change_mode("stopped", t, "low_energy_stop")
        elif not self.powered and self.store >= self.e.restart_wh - EPS:
            # Reserve bootstrap background too? No invented boot duration: this is an impulse.
            self.account(t, t, {"boot": self.e.boot_wh}, phase="boot")
            self.boot_count += 1
            self.boot_origin = t
            self.last_event_wall = None
            self.powered = True
            self.emit("boot", t)
            self.change_mode("normal" if self.store >= self.e.low_exit_wh else "low", t, "paid_boot")
        if self.powered:
            if self.mode == "normal" and self.store <= self.e.low_enter_wh + EPS:
                self.change_mode("low", t, "low_enter")
            elif self.mode == "low" and self.store >= self.e.low_exit_wh - EPS:
                self.change_mode("normal", t, "low_exit")

    def install(self, t, installations):
        for item in installations:
            record = {**asdict(item), "observed_s": t, "status": "queued" if self.powered else "missed_offline"}
            self.controls.append(record)
            if self.powered:
                self.invalidate(t, "cancelled_installation")
                self.install_queue.append(record)
        if self.install_queue and self.powered and self.can_emit(t):
            item = self.install_queue.pop(0)
            output = self.emit("install", t, batch_id=item["batch_id"], installation_id=item["installation_id"])
            item.update(status="applied" if output["accepted"] else "rejected", applied_s=t, reason=output["reason"])
            if not output["accepted"]:
                raise ValueError("trusted installation rejected: " + output["reason"])

    def arrive(self, t, arrivals):
        for item in arrivals:
            request = {**asdict(item), "status": "pending", "attempts": 0, "next_attempt_s": t,
                       "budget_completed": False, "quality_valid": False, "assessment_state": None,
                       "blocked_reasons": [], "finished_s": None, "wait_s": None,
                       "collection_authorized": False}
            self.requests.append(request)
            self.pending[item.id] = request
            if self.policy == "demand" and item.kind == "measurement":
                self.opportunities.append({"wall_s": t, "policy": self.policy,
                                           "request_id": item.id, "result": "external_trigger"})
            if not self.powered:
                self.terminal(request, "missed_offline", t)
            elif self.machine.installation_id is None or item.batch_id != self.machine.batch_id:
                self.terminal(request, "unavailable_installation", t)

    def finish(self, t):
        if not self.active:
            return
        a = self.active
        request = self.pending.get(a["request_id"])
        if a["kind"] == "measurement" and t == a["planned_end_s"]:
            if not self.can_emit(t):
                raise AssertionError("complete cycle lost its state-machine event slot")
            self.close_vent(t)
            parameters = self.scenario.default_parameters
            for interval in self.scenario.observations:
                if interval.start_s <= a["started_s"] < interval.end_s:
                    parameters = interval.parameters
                    break
            # Acquisition is created for THIS completed cycle; no cache/import of old observations.
            rows = observe(Parameters(**parameters), seed=self.scenario.seed, protocol=self.config.protocol)
            raw = [{key: row[key] for key in OBSERVATION_KEYS} for row in rows]
            output = self.emit("finish_cycle", t, **self.identity(), cycle_id=a["cycle_id"],
                               start_s=a["start_monotonic_s"], end_s=a["end_monotonic_s"], observations=raw)
            a.update(outcome="budget_completed", ended_s=t, assessment=output["assessment"],
                     state_machine_accepted=output["accepted"], state_machine_reason=output["reason"],
                     quality_valid=bool(output["assessment"] and output["assessment"]["state"] != "UNKNOWN"),
                     weak_cycles_after=output["weak_cycles"], independent_review_due=output["independent_review_due"])
            if request:
                request.update(budget_completed=True, quality_valid=a["quality_valid"],
                               assessment_state=output["assessment"]["state"] if output["assessment"] else None)
                self.terminal(request, "completed" if a["quality_valid"] else "quality_invalid", t)
            self.active = None
        elif a["kind"] == "wifi":
            elapsed = t - a["started_s"]
            success = elapsed == self.e.wifi_success_s and a["network_ok"]
            if not success and elapsed < self.e.wifi_timeout_s:
                return
            a.update(outcome="network_confirmed" if success else "network_timeout", ended_s=t)
            if request and success:
                request["budget_completed"] = True
                self.terminal(request, "network_confirmed", t)
            elif request:
                if request["attempts"] >= self.e.wifi_attempt_limit:
                    self.terminal(request, "retry_exhausted", t)
                else:
                    request["status"] = "pending"
                    request["next_attempt_s"] = t + self.e.backoff_s[min(request["attempts"] - 1, len(self.e.backoff_s) - 1)]
            self.active = None

    def wifi_budget_ok(self, t):
        # Reserve worst-case energy in each calendar day actually spanned by this attempt.
        portions = {}
        for second in range(t, t + self.e.wifi_timeout_s):
            day = second // 86400
            portions[day] = portions.get(day, 0) + self.e.wifi_w / 3600 / self.e.eta
        return all(self.wifi_used.get(day, 0) + amount <= self.e.wifi_daily_wh + EPS for day, amount in portions.items())

    def eligibility(self, t, kind, request):
        if not self.powered:
            return "stopped"
        if self.install_queue or self.machine.installation_id is None:
            return "awaiting_installation"
        if request and request["batch_id"] != self.machine.batch_id:
            return "stale_batch"
        if self.mode == "low" and request and not request["critical"]:
            return "low_priority_shed"
        duration = self.config.protocol.total_s if kind == "measurement" else self.e.wifi_timeout_s
        if t + duration > self.scenario.duration_s:
            return "horizon_too_short"
        if request and t + duration > request["deadline_s"]:
            return "deadline_too_short"
        if kind == "measurement":
            state = self.machine.state
            if state == "STABILIZING" and t - self.boot_origin >= self.machine.stable_after_s:
                state = "READY"
            if not self.can_emit(t) or state not in ("READY", "AWAITING_RECHECK", "SOURCE_PRESENT"):
                return "state_machine_not_ready"
            if self.reserved_end_wall is not None and t < self.reserved_end_wall + self.config.policy.min_cycle_gap_s:
                return "reserved_window_or_min_gap"
        if self.store - self.e.stop_wh + EPS < required_battery_wh(self.e, self.config.protocol, kind):
            return "insufficient_energy"
        if kind == "wifi" and not self.wifi_budget_ok(t):
            return "wifi_daily_budget"
        return ""

    def schedule(self, t):
        fixed = self.policy == "fixed" and t >= self.e.fixed_phase_s and (t - self.e.fixed_phase_s) % self.e.fixed_period_s == 0
        candidates = sorted((r for r in self.pending.values() if r["status"] == "pending"),
                            key=lambda r: (not r["critical"], r["at_s"], r["id"]))
        measure = next((r for r in candidates if r["kind"] == "measurement"), None)
        opportunity = None
        if fixed:
            opportunity = {"wall_s": t, "policy": self.policy, "request_id": measure["id"] if measure else "", "result": "busy"}
            self.opportunities.append(opportunity)
        if self.active:
            return
        if fixed or (self.policy == "demand" and measure):
            kind, request = "measurement", measure
        else:
            request = next((r for r in candidates if r["kind"] == "wifi" and t >= r["next_attempt_s"]), None)
            if request is None:
                return
            kind = "wifi"
        reason = self.eligibility(t, kind, request)
        if opportunity:
            opportunity["result"] = reason or "admitted"
        if reason:
            if request and reason not in request["blocked_reasons"]:
                request["blocked_reasons"].append(reason)
            return
        a = {"id": f"a{len(self.actions) + 1}", "kind": kind,
             "request_id": request["id"] if request else "", "started_s": t,
             "batch_id": self.machine.batch_id, "installation_id": self.machine.installation_id,
             "boot_id": self.machine.boot_id, "configuration_sha256": self.config_sha,
             "reserved_battery_wh": required_battery_wh(self.e, self.config.protocol, kind),
             "available_above_stop_wh": self.store - self.e.stop_wh, "paid_components_wh": {},
             "outcome": "running", "ended_s": None, "quality_valid": False, "collection_authorized": False}
        if kind == "measurement":
            a["cycle_id"] = f"cycle-{len(self.actions) + 1}"
            output = self.emit("start_cycle", t, **self.identity(), cycle_id=a["cycle_id"])
            if not output["accepted"]:
                raise AssertionError("scheduler violated T04 readiness: " + output["reason"])
            a.update(start_monotonic_s=output["pending_cycle"]["start_s"],
                     end_monotonic_s=output["pending_cycle"]["end_s"],
                     planned_end_s=t + int(self.config.protocol.total_s), vent_open=False)
            self.reserved_end_wall = a["planned_end_s"]
            if self.policy == "demand":
                opportunity = next(o for o in self.opportunities if o["request_id"] == request["id"])
                opportunity.update(result="admitted", admitted_s=t)
        else:
            a.update(planned_end_s=t + self.e.wifi_timeout_s, network_ok=True)
        self.active = a
        self.actions.append(a)
        if request:
            request["attempts"] += 1
            request["status"] = "running"

    def maintain_clock(self, t):
        if not self.powered or not self.can_emit(t):
            return
        if self.machine.state == "FAULT" and self.machine.installation_id is not None:
            self.emit("recover", t, **self.identity())
            return
        evidence = self.machine.last_evidence_end_s
        idle_due = evidence is not None and self.machine.pending is None and t - self.boot_origin - evidence > self.config.policy.max_evidence_gap_s
        stable_due = self.machine.state == "STABILIZING" and t - self.boot_origin >= self.machine.stable_after_s
        if stable_due or idle_due or t - self.last_event_wall >= self.e.tick_s:
            self.emit("tick", t)

    def spend_second(self, t):
        if self.powered:
            requested = {"background_run": self.e.base_w / 3600, "misc": self.e.misc_w / 3600}
        else:
            requested = {"background_stop": self.e.stopped_w / 3600}
        tag, phase = "", ""
        if self.active:
            a = self.active
            tag = a["id"]
            if a["kind"] == "measurement":
                elapsed = t - a["started_s"]
                protocol = self.config.protocol
                if elapsed == protocol.baseline_s:
                    unmet = self.account(t, t, {"vent_open": self.e.vent_open_j / 3600}, tag=tag, phase="vent_open")
                    if unmet > EPS:
                        raise AssertionError("reserved opening energy missing")
                    a["vent_open"] = True
                if elapsed == protocol.recovery_start_s:
                    self.close_vent(t)
                phase = protocol.phase_at(elapsed)
                requested[f"measurement_{phase}"] = getattr(self.e, f"{phase}_w") / 3600
                if phase == "flush":
                    requested["fan"] = self.e.fan_w / 3600
            else:
                phase = "wifi_attempt"
                requested["wifi"] = self.e.wifi_w / 3600
                available = all(i.available for i in self.scenario.network if i.start_s <= t < i.end_s)
                a["network_ok"] = a["network_ok"] and available
        solar = sum(i.battery_w / 3600 for i in self.scenario.solar if i.start_s <= t < i.end_s)
        unmet = self.account(t, t + 1, requested, solar, tag, phase)
        if self.active and self.active["kind"] == "wifi":
            day = t // 86400
            self.wifi_used[day] = self.wifi_used.get(day, 0) + requested["wifi"] / self.e.eta
        if unmet > EPS and self.active:
            # This branch is forbidden by admission invariants under the modeled supply.
            # Never issue finish_cycle if those assumptions are broken.
            self.abort(t + 1, "interrupted_energy")

    def run(self):
        arrivals, installations = {}, {}
        for row in self.scenario.requests:
            arrivals.setdefault(row.at_s, []).append(row)
        for row in self.scenario.installations:
            installations.setdefault(row.at_s, []).append(row)
        for t in range(self.scenario.duration_s + 1):
            if t < self.scenario.duration_s:
                if not self.powered:
                    self.boot_or_stop(t)
                self.install(t, installations.get(t, ()))
            self.finish(t)  # A new installation at this second cancels even a just-ending old cycle.
            if t < self.scenario.duration_s and self.powered:
                self.boot_or_stop(t)
            for request in list(self.pending.values()):
                if request["status"] == "pending" and t >= request["deadline_s"]:
                    self.terminal(request, "expired", t)
            if t == self.scenario.duration_s:
                self.maintain_clock(t)
                break
            self.arrive(t, arrivals.get(t, ()))
            self.schedule(t)
            self.maintain_clock(t)
            self.spend_second(t)
        for request in list(self.pending.values()):
            self.terminal(request, "horizon_censored", self.scenario.duration_s)
        if self.active:
            raise AssertionError("active action disappeared at horizon")
        for row in self.ledger:
            row.pop("_signature", None)
            row["balance_residual_wh"] = (row["store_start_wh"] + row["solar_battery_wh"]
                                          - sum(row["served_load_wh"].values()) / self.e.eta
                                          - row["spill_wh"] - row["store_end_wh"])
        result = {"label": LABEL, "scenario": self.scenario.name, "policy": self.policy,
                  "configuration_sha256": self.config_sha, "ledger": self.ledger, "actions": self.actions,
                  "events": self.events, "requests": self.requests, "installations": self.controls,
                  "modes": self.modes, "opportunities": self.opportunities,
                  "wifi_daily_battery_wh": self.wifi_used, "final_machine": self.machine.snapshot(),
                  "final_powered": self.powered}
        result["summary"] = summarize(self.scenario, result)
        return result


def simulate(scenario, policy):
    return Scheduler(scenario, policy).run()


def summarize(scenario, result):
    totals = {key: sum(row["served_load_wh"].get(key, 0) for row in result["ledger"]) for key in CATEGORIES}
    s = {"scenario": scenario.name, "policy": result["policy"], "label": LABEL,
         "initial_wh": scenario.energy.initial_wh, "final_wh": result["ledger"][-1]["store_end_wh"],
         "solar_wh": sum(r["solar_battery_wh"] for r in result["ledger"]),
         "battery_consumed_wh": sum(totals.values()) / scenario.energy.eta,
         "spill_wh": sum(r["spill_wh"] for r in result["ledger"]),
         "actual_unmet_load_wh": sum(r["unmet_load_wh"] for r in result["ledger"]),
         "max_interval_residual_wh": max(abs(r["balance_residual_wh"]) for r in result["ledger"]),
         **{f"load_{key}_wh": value for key, value in totals.items()},
         "measurement_opportunities": len(result["opportunities"]),
         "measurement_started": sum(a["kind"] == "measurement" for a in result["actions"]),
         "measurement_budget_completed": sum(a["outcome"] == "budget_completed" for a in result["actions"]),
         "measurement_quality_valid": sum(a["kind"] == "measurement" and a["quality_valid"] for a in result["actions"]),
         "wifi_attempts": sum(a["kind"] == "wifi" for a in result["actions"]),
         "wifi_confirmed": sum(a["outcome"] == "network_confirmed" for a in result["actions"]),
         "wifi_timeouts": sum(a["outcome"] == "network_timeout" for a in result["actions"]),
         "wifi_success_load_wh": sum(sum(a["paid_components_wh"].values()) for a in result["actions"] if a["outcome"] == "network_confirmed"),
         "wifi_timeout_load_wh": sum(sum(a["paid_components_wh"].values()) for a in result["actions"] if a["outcome"] == "network_timeout"),
         "max_weak_chain": max((e["output"]["weak_cycles"] for e in result["events"]), default=0),
         "independent_review_due_finishes": sum(e["event"]["kind"] == "finish_cycle" and e["output"]["independent_review_due"] for e in result["events"]),
         "idle_evidence_expirations": sum(e["idle_evidence_expired"] for e in result["events"]),
         "stopped_s": sum(r["end_s"] - r["start_s"] for r in result["ledger"] if r["mode"] == "stopped"),
         "paid_boots": sum(e["event"]["kind"] == "boot" for e in result["events"]),
         "collection_authorized": False}
    s["balance_residual_wh"] = s["initial_wh"] + s["solar_wh"] - s["battery_consumed_wh"] - s["spill_wh"] - s["final_wh"]
    for kind in ("measurement", "wifi"):
        group = [r for r in result["requests"] if r["kind"] == kind]
        complete = [r for r in group if r["quality_valid"]] if kind == "measurement" else [r for r in group if r["status"] == "network_confirmed"]
        waits = sorted(r["wait_s"] for r in complete)
        unsuccessful = [r for r in group if r not in complete]
        s.update({f"{kind}_requests": len(group), f"{kind}_served_requests": len(complete),
                  f"{kind}_no_result_fraction": len(unsuccessful) / len(group) if group else None,
                  f"{kind}_completed_wait_p95_s": waits[math.ceil(len(waits) * .95) - 1] if waits else None,
                  f"{kind}_all_wait_sum_s": sum(r["wait_s"] for r in group),
                  f"{kind}_unfinished_wait_sum_s": sum(r["wait_s"] for r in unsuccessful),
                  f"{kind}_unfinished_followup_sum_s": sum(scenario.duration_s - r["at_s"] for r in unsuccessful),
                  f"{kind}_horizon_censored": sum(r["status"] == "horizon_censored" for r in group),
                  f"{kind}_missed_offline": sum(r["status"] == "missed_offline" for r in group)})
    return s

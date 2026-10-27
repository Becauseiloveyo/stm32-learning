#include "hanger_energy.h"

#include <math.h>
#include <string.h>

static int finite_nonnegative(double value)
{
    return isfinite(value) && value >= 0.0;
}

static int bit(uint8_t value) { return value <= 1u; }

he_status he_time_add(uint64_t time_s, uint64_t duration_s, uint64_t *result)
{
    if (result == NULL) return HE_ERR_ARGUMENT;
    if (duration_s > UINT64_MAX - time_s) return HE_ERR_OVERFLOW;
    *result = time_s + duration_s;
    return HE_OK;
}

he_status he_config_validate(const he_config *c)
{
    size_t i;
    double values[18];
    if (c == NULL) return HE_ERR_ARGUMENT;
    values[0] = c->capacity_wh; values[1] = c->eta;
    values[2] = c->base_w; values[3] = c->misc_w; values[4] = c->stopped_w;
    values[5] = c->baseline_w; values[6] = c->flush_w; values[7] = c->recovery_w;
    values[8] = c->fan_w; values[9] = c->vent_open_j; values[10] = c->vent_close_j;
    values[11] = c->wifi_w; values[12] = c->boot_wh;
    values[13] = c->stop_wh; values[14] = c->restart_wh;
    values[15] = c->low_enter_wh; values[16] = c->low_exit_wh;
    values[17] = c->wifi_daily_wh;
    for (i = 0; i < 18u; ++i) {
        if (!finite_nonnegative(values[i])) return HE_ERR_CONFIG;
    }
    if (!(c->eta > 0.0 && c->eta <= 1.0)) return HE_ERR_CONFIG;
    if (!isfinite(c->base_w + c->misc_w) || !isfinite(c->boot_wh / c->eta))
        return HE_ERR_OVERFLOW;
    if (!(c->stop_wh < c->restart_wh && c->restart_wh <= c->low_enter_wh &&
          c->low_enter_wh < c->low_exit_wh && c->low_exit_wh <= c->capacity_wh))
        return HE_ERR_CONFIG;
    if (c->stopped_w > c->base_w + c->misc_w ||
        c->restart_wh - c->boot_wh / c->eta <= c->stop_wh)
        return HE_ERR_CONFIG;
    if (c->wifi_success_s == 0u || c->wifi_success_s > c->wifi_timeout_s ||
        c->wifi_timeout_s > HE_DAY_S || c->wifi_attempt_limit == 0u)
        return HE_ERR_CONFIG;
    for (i = 0; i < HE_BACKOFF_COUNT; ++i) {
        if (c->backoff_s[i] == 0u) return HE_ERR_CONFIG;
    }
    return HE_OK;
}

he_status he_protocol_validate(const he_protocol *p)
{
    if (p == NULL) return HE_ERR_ARGUMENT;
    if (p->baseline_s == 0u || p->flush_s == 0u || p->recovery_s == 0u)
        return HE_ERR_CONFIG;
    return HE_OK;
}

he_status he_budget(const he_config *c, const he_protocol *p,
                    he_kind kind, he_budget_result *out)
{
    he_budget_result r;
    he_status status;
    size_t i;
    if (out == NULL) return HE_ERR_ARGUMENT;
    status = he_config_validate(c); if (status != HE_OK) return status;
    status = he_protocol_validate(p); if (status != HE_OK) return status;
    if (kind != HE_MEASUREMENT && kind != HE_WIRELESS) return HE_ERR_RANGE;
    memset(&r, 0, sizeof(r));
    if (kind == HE_MEASUREMENT) {
        /* Three uint32_t durations cannot overflow a uint64_t sum. */
        r.duration_s = (uint64_t)p->baseline_s + p->flush_s + p->recovery_s;
        r.components_wh[HE_MEASUREMENT_BASELINE] = c->baseline_w * p->baseline_s / 3600.0;
        r.components_wh[HE_MEASUREMENT_FLUSH] = c->flush_w * p->flush_s / 3600.0;
        r.components_wh[HE_MEASUREMENT_RECOVERY] = c->recovery_w * p->recovery_s / 3600.0;
        r.components_wh[HE_FAN] = c->fan_w * p->flush_s / 3600.0;
        r.components_wh[HE_VENT_OPEN] = c->vent_open_j / 3600.0;
        r.components_wh[HE_VENT_CLOSE] = c->vent_close_j / 3600.0;
    } else {
        r.duration_s = c->wifi_timeout_s;
        r.components_wh[HE_WIFI] = c->wifi_w * (double)r.duration_s / 3600.0;
    }
    for (i = 0; i < HE_CATEGORY_COUNT; ++i) r.action_load_wh += r.components_wh[i];
    r.background_load_wh = (c->base_w + c->misc_w) * (double)r.duration_s / 3600.0;
    r.required_battery_wh = (r.action_load_wh + r.background_load_wh) / c->eta;
    if (!isfinite(r.action_load_wh) || !isfinite(r.background_load_wh) ||
        !isfinite(r.required_battery_wh)) return HE_ERR_OVERFLOW;
    *out = r;
    return HE_OK;
}

he_status he_account(const he_config *c, he_step_kind kind, double store,
                     const double requested[HE_CATEGORY_COUNT],
                     double solar, he_balance *out)
{
    he_balance r;
    he_status status;
    double available, demand = 0.0, paid = 0.0;
    size_t i;
    if (requested == NULL || out == NULL) return HE_ERR_ARGUMENT;
    status = he_config_validate(c); if (status != HE_OK) return status;
    if (kind != HE_INTERVAL && kind != HE_IMPULSE) return HE_ERR_RANGE;
    if (!finite_nonnegative(store) || store > c->capacity_wh || !finite_nonnegative(solar))
        return HE_ERR_RANGE;
    if (kind == HE_IMPULSE && solar != 0.0) return HE_ERR_RANGE;
    for (i = 0; i < HE_CATEGORY_COUNT; ++i) {
        if (!finite_nonnegative(requested[i])) return HE_ERR_RANGE;
        demand += requested[i];
    }
    available = store + solar;
    if (!isfinite(demand) || !isfinite(available)) return HE_ERR_OVERFLOW;
    memset(&r, 0, sizeof(r));
    r.store_start_wh = store; r.solar_battery_wh = solar;
    for (i = 0; i < HE_CATEGORY_COUNT; ++i) {
        r.requested_load_wh[i] = requested[i];
        r.served_load_wh[i] = fmin(requested[i], fmax(0.0, available) * c->eta);
        available -= r.served_load_wh[i] / c->eta;
        paid += r.served_load_wh[i];
    }
    r.unmet_load_wh = fmax(0.0, demand - paid);
    r.conversion_loss_wh = paid / c->eta - paid;
    r.spill_wh = fmax(0.0, available - c->capacity_wh);
    r.store_end_wh = fmax(0.0, fmin(c->capacity_wh, available));
    r.balance_residual_wh = store + solar - paid / c->eta - r.spill_wh - r.store_end_wh;
    if (!isfinite(paid / c->eta) || !isfinite(r.balance_residual_wh)) return HE_ERR_OVERFLOW;
    *out = r;
    return HE_OK;
}

he_status he_power_update(const he_config *c, const he_power_state *input,
                          uint8_t can_emit, he_power_result *out)
{
    he_power_result r;
    he_status status;
    if (input == NULL || out == NULL) return HE_ERR_ARGUMENT;
    status = he_config_validate(c); if (status != HE_OK) return status;
    if (!bit(input->powered) || !bit(can_emit) || !finite_nonnegative(input->store_wh) ||
        input->store_wh > c->capacity_wh) return HE_ERR_RANGE;
    if ((!input->powered && input->mode != HE_STOPPED) ||
        (input->powered && input->mode != HE_LOW && input->mode != HE_NORMAL))
        return HE_ERR_STATE;
    memset(&r, 0, sizeof(r));
    r.state = *input;
    if (input->powered && input->store_wh <= c->stop_wh + HE_EPS_WH) {
        if (can_emit) {
            r.state.powered = 0u; r.state.mode = HE_STOPPED; r.stopped = 1u;
        } else r.stop_deferred = 1u;
    } else if (!input->powered && input->store_wh >= c->restart_wh - HE_EPS_WH) {
        double requested[HE_CATEGORY_COUNT] = {0.0};
        he_balance balance;
        requested[HE_BOOT] = c->boot_wh;
        status = he_account(c, HE_IMPULSE, input->store_wh, requested, 0.0, &balance);
        if (status != HE_OK) return status;
        /* Do not claim a paid boot on pathological sub-epsilon reserves. */
        if (balance.served_load_wh[HE_BOOT] != c->boot_wh) return HE_ERR_STATE;
        r.booted = 1u; r.boot_load_wh = c->boot_wh;
        r.state.store_wh = balance.store_end_wh; r.state.powered = 1u;
        r.state.mode = r.state.store_wh >= c->low_exit_wh ? HE_NORMAL : HE_LOW;
    }
    if (r.state.powered) {
        if (r.state.mode == HE_NORMAL && r.state.store_wh <= c->low_enter_wh + HE_EPS_WH)
            r.state.mode = HE_LOW;
        else if (r.state.mode == HE_LOW && r.state.store_wh >= c->low_exit_wh - HE_EPS_WH)
            r.state.mode = HE_NORMAL;
    }
    *out = r;
    return HE_OK;
}

static he_status quota_validate(const he_quota *q)
{
    if (q == NULL) return HE_ERR_ARGUMENT;
    if (q->initialized == 0u) return HE_ERR_UNINITIALIZED;
    if (q->initialized != 1u || q->day != q->last_time_s / HE_DAY_S ||
        !finite_nonnegative(q->used_battery_wh[0]) || !finite_nonnegative(q->used_battery_wh[1]))
        return HE_ERR_STATE;
    return HE_OK;
}

he_status he_quota_init(he_quota *q, uint64_t now, double current, double next)
{
    he_quota r;
    if (q == NULL) return HE_ERR_ARGUMENT;
    if (!finite_nonnegative(current) || !finite_nonnegative(next)) return HE_ERR_RANGE;
    memset(&r, 0, sizeof(r));
    r.last_time_s = now; r.day = now / HE_DAY_S;
    r.used_battery_wh[0] = current; r.used_battery_wh[1] = next; r.initialized = 1u;
    *q = r;
    return HE_OK;
}

he_status he_quota_advance(he_quota *q, uint64_t now)
{
    he_status status = quota_validate(q);
    uint64_t day;
    if (status != HE_OK) return status;
    if (now < q->last_time_s) return HE_ERR_TIME;
    day = now / HE_DAY_S;
    if (day > q->day) {
        q->used_battery_wh[0] = day - q->day == 1u ? q->used_battery_wh[1] : 0.0;
        q->used_battery_wh[1] = 0.0;
    }
    q->last_time_s = now; q->day = day;
    return HE_OK;
}

static he_status wifi_portions(const he_config *c, uint64_t start, uint64_t duration,
                               he_wifi_result *r, uint64_t *end)
{
    uint64_t second;
    double per_second;
    he_status status;
    if (duration == 0u || duration > c->wifi_timeout_s) return HE_ERR_RANGE;
    status = he_time_add(start, duration, end); if (status != HE_OK) return status;
    per_second = c->wifi_w / 3600.0 / c->eta;
    if (!isfinite(per_second)) return HE_ERR_OVERFLOW;
    memset(r, 0, sizeof(*r)); r->first_day = start / HE_DAY_S;
    /* Deliberately use the same ordered additions as T05's per-second budget
     * for threshold parity. Bound <= 86400 iterations, independent of uptime. */
    for (second = start; second < *end; ++second) {
        size_t slot = (size_t)(second / HE_DAY_S - r->first_day);
        r->battery_wh[slot] += per_second;
        if (!isfinite(r->battery_wh[slot])) return HE_ERR_OVERFLOW;
    }
    return HE_OK;
}

he_status he_wifi_check(const he_config *c, he_quota *q,
                        uint64_t start, he_wifi_result *out)
{
    he_quota local;
    he_wifi_result r;
    uint64_t end;
    he_status status;
    size_t i;
    if (out == NULL || q == NULL) return HE_ERR_ARGUMENT;
    status = he_config_validate(c); if (status != HE_OK) return status;
    local = *q;
    status = he_quota_advance(&local, start); if (status != HE_OK) return status;
    status = wifi_portions(c, start, c->wifi_timeout_s, &r, &end);
    if (status != HE_OK) return status;
    r.allowed = 1u;
    for (i = 0; i < 2u; ++i) {
        double proposed = local.used_battery_wh[i] + r.battery_wh[i];
        if (!isfinite(proposed)) return HE_ERR_OVERFLOW;
        /* T05 checks only days actually spanned, including zero-W attempts. */
        if ((i == 0u || (end - 1u) / HE_DAY_S > r.first_day) &&
            proposed > c->wifi_daily_wh + HE_EPS_WH) r.allowed = 0u;
    }
    *q = local; *out = r;
    return HE_OK;
}

he_status he_wifi_charge(const he_config *c, he_quota *q,
                         uint64_t start, uint64_t elapsed, he_wifi_result *out)
{
    he_quota local;
    he_wifi_result r;
    he_status status;
    uint64_t end, second;
    double per_second;
    if (out == NULL || q == NULL) return HE_ERR_ARGUMENT;
    status = he_config_validate(c); if (status != HE_OK) return status;
    local = *q;
    status = he_quota_advance(&local, start); if (status != HE_OK) return status;
    status = wifi_portions(c, start, elapsed, &r, &end); if (status != HE_OK) return status;
    per_second = c->wifi_w / 3600.0 / c->eta;
    for (second = start; second < end; ++second) {
        size_t slot = (size_t)(second / HE_DAY_S - local.day);
        local.used_battery_wh[slot] += per_second;
        if (!isfinite(local.used_battery_wh[slot])) return HE_ERR_OVERFLOW;
    }
    r.allowed = (uint8_t)(local.used_battery_wh[0] <= c->wifi_daily_wh + HE_EPS_WH &&
                          local.used_battery_wh[1] <= c->wifi_daily_wh + HE_EPS_WH);
    status = he_quota_advance(&local, end); if (status != HE_OK) return status;
    *q = local; *out = r;
    return HE_OK;
}

he_status he_admit(const he_config *c, const he_protocol *p, const he_admission *a,
                   he_quota *quota, he_admission_result *out)
{
    he_budget_result budget;
    he_admission_result r;
    he_status status;
    uint64_t gap_end = 0u;
    he_readiness readiness;
    if (a == NULL || out == NULL) return HE_ERR_ARGUMENT;
    status = he_budget(c, p, a->kind, &budget); if (status != HE_OK) return status;
    if (!bit(a->powered) || !bit(a->installation_queued) || !bit(a->installed) ||
        !bit(a->batch_matches) || !bit(a->has_request) || !bit(a->critical) ||
        !bit(a->low_mode) || !bit(a->can_emit) || !bit(a->has_reserved_window) ||
        a->readiness < HE_NOT_READY || a->readiness > HE_SOURCE_PRESENT ||
        !finite_nonnegative(a->store_wh) || a->store_wh > c->capacity_wh)
        return HE_ERR_RANGE;
    if (a->min_cycle_gap_s == 0u) return HE_ERR_CONFIG;
    if (a->horizon_s < a->now_s) return HE_ERR_TIME;
    memset(&r, 0, sizeof(r)); r.required_battery_wh = budget.required_battery_wh;
    status = he_time_add(a->now_s, budget.duration_s, &r.complete_at_s);
    if (status != HE_OK) return status;
    if (a->kind == HE_MEASUREMENT && a->has_reserved_window) {
        status = he_time_add(a->reserved_end_s, a->min_cycle_gap_s, &gap_end);
        if (status != HE_OK) return status;
    }
    readiness = a->readiness;
    if (readiness == HE_STABILIZING && a->now_s >= a->stable_after_s) readiness = HE_READY;
    if (!a->powered) r.reason = HE_DENY_STOPPED;
    else if (a->installation_queued || !a->installed) r.reason = HE_DENY_INSTALLATION;
    else if (a->has_request && !a->batch_matches) r.reason = HE_DENY_BATCH;
    else if (a->low_mode && a->has_request && !a->critical) r.reason = HE_DENY_LOW_PRIORITY;
    else if (r.complete_at_s > a->horizon_s) r.reason = HE_DENY_HORIZON;
    else if (a->has_request && r.complete_at_s > a->deadline_s) r.reason = HE_DENY_DEADLINE;
    else if (a->kind == HE_MEASUREMENT && (!a->can_emit ||
             (readiness != HE_READY && readiness != HE_AWAITING_RECHECK && readiness != HE_SOURCE_PRESENT)))
        r.reason = HE_DENY_STATE;
    else if (a->kind == HE_MEASUREMENT && a->has_reserved_window && a->now_s < gap_end)
        r.reason = HE_DENY_WINDOW;
    else if (a->store_wh - c->stop_wh + HE_EPS_WH < budget.required_battery_wh)
        r.reason = HE_DENY_ENERGY;
    else if (a->kind == HE_WIRELESS) {
        he_wifi_result wifi;
        status = he_wifi_check(c, quota, a->now_s, &wifi); if (status != HE_OK) return status;
        if (!wifi.allowed) r.reason = HE_DENY_WIFI_BUDGET;
    }
    *out = r;
    return HE_OK;
}

he_status he_retry(const he_config *c, uint32_t attempts, uint64_t failed_at, he_retry_result *out)
{
    he_retry_result r;
    he_status status;
    uint32_t slot;
    if (out == NULL) return HE_ERR_ARGUMENT;
    status = he_config_validate(c); if (status != HE_OK) return status;
    if (attempts == 0u) return HE_ERR_RANGE;
    memset(&r, 0, sizeof(r));
    r.exhausted = (uint8_t)(attempts >= c->wifi_attempt_limit);
    if (!r.exhausted) {
        slot = attempts - 1u;
        if (slot >= HE_BACKOFF_COUNT) slot = HE_BACKOFF_COUNT - 1u;
        status = he_time_add(failed_at, c->backoff_s[slot], &r.next_attempt_s);
        if (status != HE_OK) return status;
    }
    *out = r;
    return HE_OK;
}

const char *he_reason_name(he_reason reason)
{
    static const char *const names[] = {
        "", "stopped", "awaiting_installation", "stale_batch", "low_priority_shed",
        "horizon_too_short", "deadline_too_short", "state_machine_not_ready",
        "reserved_window_or_min_gap", "insufficient_energy", "wifi_daily_budget"
    };
    if (reason < HE_ADMITTED || reason > HE_DENY_WIFI_BUDGET) return "invalid_reason";
    return names[(size_t)reason];
}

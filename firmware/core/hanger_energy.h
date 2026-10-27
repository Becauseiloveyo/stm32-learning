#ifndef HANGER_ENERGY_H
#define HANGER_ENERGY_H

/* C99, caller-owned fixed storage, no allocation/I/O/hardware/global state.
 * Energies: Wh (load-side unless named store/battery/solar/spill).
 * Power: W; actuator impulses: J; times: uint64_t elapsed whole seconds.
 * Parse/range-check signed or textual times BEFORE conversion to these unsigned
 * types. min_cycle_gap_s must be positive (accepted T04 Policy requirement).
 * Supply accepted T04 protocol/configuration identity externally; this module
 * neither assesses observations nor authorizes collection.
 * All errors leave caller outputs and mutable state unchanged. A normal
 * admission denial is HE_OK plus a reason. Quota checks advance trusted time
 * even on a normal budget denial, but never charge a hypothetical attempt.
 */
#include <stddef.h>
#include <stdint.h>

#define HE_CATEGORY_COUNT 11u
#define HE_BACKOFF_COUNT 4u
#define HE_DAY_S UINT64_C(86400)
#define HE_EPS_WH 1e-12

typedef enum {
    HE_OK = 0, HE_ERR_ARGUMENT, HE_ERR_RANGE, HE_ERR_CONFIG,
    HE_ERR_OVERFLOW, HE_ERR_TIME, HE_ERR_STATE, HE_ERR_UNINITIALIZED
} he_status;

typedef enum {
    HE_BACKGROUND_RUN = 0, HE_BACKGROUND_STOP, HE_MISC,
    HE_MEASUREMENT_BASELINE, HE_MEASUREMENT_FLUSH, HE_MEASUREMENT_RECOVERY,
    HE_FAN, HE_VENT_OPEN, HE_VENT_CLOSE, HE_WIFI, HE_BOOT
} he_category;

typedef enum { HE_MEASUREMENT = 0, HE_WIRELESS = 1 } he_kind;
typedef enum { HE_INTERVAL = 0, HE_IMPULSE = 1 } he_step_kind;
typedef enum { HE_STOPPED = 0, HE_LOW = 1, HE_NORMAL = 2 } he_mode;
/* Only the readiness classes consumed by T05 eligibility, NOT a T04 port. */
typedef enum {
    HE_NOT_READY = 0, HE_STABILIZING, HE_READY,
    HE_AWAITING_RECHECK, HE_SOURCE_PRESENT
} he_readiness;
typedef enum {
    HE_ADMITTED = 0, HE_DENY_STOPPED, HE_DENY_INSTALLATION, HE_DENY_BATCH,
    HE_DENY_LOW_PRIORITY, HE_DENY_HORIZON, HE_DENY_DEADLINE,
    HE_DENY_STATE, HE_DENY_WINDOW, HE_DENY_ENERGY, HE_DENY_WIFI_BUDGET
} he_reason;

typedef struct {
    double capacity_wh, eta, base_w, misc_w, stopped_w;
    double baseline_w, flush_w, recovery_w, fan_w;
    double vent_open_j, vent_close_j, wifi_w, boot_wh;
    double stop_wh, restart_wh, low_enter_wh, low_exit_wh, wifi_daily_wh;
    uint64_t wifi_success_s, wifi_timeout_s;
    uint32_t wifi_attempt_limit;
    uint64_t backoff_s[HE_BACKOFF_COUNT];
} he_config;

typedef struct { uint32_t baseline_s, flush_s, recovery_s; } he_protocol;

typedef struct {
    uint64_t duration_s;
    double components_wh[HE_CATEGORY_COUNT];
    double action_load_wh, background_load_wh, required_battery_wh;
} he_budget_result;

typedef struct {
    double store_start_wh, solar_battery_wh;
    double requested_load_wh[HE_CATEGORY_COUNT], served_load_wh[HE_CATEGORY_COUNT];
    double unmet_load_wh, conversion_loss_wh, spill_wh, store_end_wh;
    double balance_residual_wh;
} he_balance;

typedef struct {
    uint8_t powered;
    he_mode mode;
    double store_wh;
} he_power_state;
typedef struct {
    he_power_state state;
    uint8_t booted, stopped, stop_deferred;
    double boot_load_wh;
} he_power_result;

/* Only current+next day are retained, with a monotonic trusted time floor.
 * Wi-Fi timeout <= one day, so each attempt spans at most two budget days.
 * Do NOT zero-initialize this ledger on a physical reboot. Without a trusted
 * restored ledger and clock, leave initialized=0 and block wireless actions.
 * Calendar advance discards expired days, rejects backward time, and does not
 * resurrect their allowances. No nonvolatile persistence is implemented.
 */
typedef struct {
    uint64_t last_time_s, day;
    double used_battery_wh[2];
    uint8_t initialized;
} he_quota;
typedef struct {
    uint64_t first_day;
    double battery_wh[2];
    uint8_t allowed;
} he_wifi_result;

typedef struct {
    he_kind kind;
    uint8_t powered, installation_queued, installed, batch_matches;
    uint8_t has_request, critical, low_mode, can_emit, has_reserved_window;
    he_readiness readiness;
    uint64_t now_s, horizon_s, deadline_s, stable_after_s;
    uint64_t reserved_end_s, min_cycle_gap_s;
    double store_wh;
} he_admission;
typedef struct {
    he_reason reason;
    uint64_t complete_at_s;
    double required_battery_wh;
} he_admission_result;
typedef struct { uint8_t exhausted; uint64_t next_attempt_s; } he_retry_result;

he_status he_config_validate(const he_config *config);
he_status he_protocol_validate(const he_protocol *protocol);
he_status he_time_add(uint64_t time_s, uint64_t duration_s, uint64_t *result);
he_status he_budget(const he_config *config, const he_protocol *protocol,
                    he_kind kind, he_budget_result *result);
/* An interval must describe a single constant-input step. Do not merge
 * intervals across action/control/capacity/exhaustion boundaries. An impulse
 * MUST have zero solar; admission never receives future-harvest inputs. */
he_status he_account(const he_config *config, he_step_kind kind, double store_wh,
                     const double requested_load_wh[HE_CATEGORY_COUNT],
                     double solar_battery_wh, he_balance *result);
/* Controller processes due finish BEFORE calling this function, and new
 * installation BEFORE that finish. can_emit=0 preserves T04's occupied event
 * second and defers orderly stop, possibly paying one extra second background.
 * Caller applies clip/cancellation/boot/installation reset using the flags. */
he_status he_power_update(const he_config *config, const he_power_state *state,
                          uint8_t can_emit, he_power_result *result);
he_status he_quota_init(he_quota *quota, uint64_t trusted_now_s,
                        double current_battery_wh, double next_battery_wh);
he_status he_quota_advance(he_quota *quota, uint64_t trusted_now_s);
he_status he_wifi_check(const he_config *config, he_quota *quota,
                        uint64_t start_s, he_wifi_result *result);
/* Charge only actual observed/paid seconds (success, timeout or interruption),
 * not the worst-case reservation. end is exclusive; the clock floor advances
 * to end, preventing overlapping/backdated charges. Over-budget actual costs
 * remain recorded, so later admission fails rather than hiding an overrun.
 * A single active attempt is enforced by the EXTERNAL controller. */
he_status he_wifi_charge(const he_config *config, he_quota *quota,
                         uint64_t start_s, uint64_t elapsed_s,
                         he_wifi_result *result);
he_status he_admit(const he_config *config, const he_protocol *protocol,
                   const he_admission *input, he_quota *quota,
                   he_admission_result *result);
he_status he_retry(const he_config *config, uint32_t attempts_done,
                   uint64_t failed_at_s, he_retry_result *result);
const char *he_reason_name(he_reason reason);

#endif

#ifndef HANGER_STATE_H
#define HANGER_STATE_H
#include "hanger_measurement.h"

/* Host-independent C99 research controller. No persistence, allocation or I/O.
 * All durations are seconds, all IDs are 1..32 printable ASCII bytes, with no
 * leading/trailing space. Empty IDs represent absence. All strings MUST have
 * storage for HS_ID_BYTES bytes; no pointer input is retained after a call.
 * Caller supplies exclusive access, valid buffer extents and nonoverlapping
 * input/output/state objects. Initialize structures fully before use. Do not serialize
 * this ABI by memcpy across builds; use an explicit validated transport. */
#define HS_ID_BYTES 33U
#define HS_MAX_MESSAGES 64U
#define HS_MAX_CYCLES 16U
#define HS_MAX_BOOTS 8U
#define HS_MAX_INSTALLATIONS 32U
#define HS_MAX_BATCHES 16U
#define HS_MAX_TIME_S 1000000000.0

typedef struct {
    double stabilization_wait_s, min_cycle_gap_s, max_evidence_gap_s;
    double max_event_gap_s, completion_grace_s, timestamp_tolerance_s;
    uint32_t review_after_weak_cycles;
} hs_policy;
typedef struct { int32_t version; hm_protocol protocol; hm_gates gates; hs_policy policy; } hs_config;
enum { HS_BOOT, HS_INSTALL, HS_CONFIGURE, HS_CLIP, HS_TICK, HS_RECOVER, HS_START, HS_FINISH };
enum { HS_AWAITING_INSTALLATION, HS_STABILIZING, HS_READY, HS_MEASURING,
       HS_AWAITING_RECHECK, HS_SOURCE_PRESENT, HS_FAULT };
enum { HS_REJECTED, HS_APPLIED, HS_IGNORED_DUPLICATE };
enum {
    HS_MALFORMED_EVENT, HS_STALE_BOOT, HS_RETIRED_BOOT, HS_CONFIG_MISMATCH,
    HS_SCOPE_MISMATCH, HS_DUPLICATE_EVENT, HS_EVENT_CONFLICT, HS_BOOT_REUSED,
    HS_INVALID_INSTALLATION, HS_RETIRED_INSTALLATION, HS_INVALID_TIME,
    HS_BOOT_INSTALL, HS_INVALID_CYCLE, HS_CYCLE_REUSED, HS_NONINCREASING_TIME,
    HS_TIME_JUMP, HS_TIMEOUT, HS_INSTALL_WAIT, HS_INVALID_CONFIG,
    HS_CONFIG_UNCHANGED, HS_CONFIG_CHANGED, HS_CLIP_INSTALL, HS_RECOVERY_REQUIRES,
    HS_RECOVERY_WAIT, HS_CLOCK, HS_ALREADY_ACTIVE, HS_NOT_READY, HS_SHORT_GAP,
    HS_BAD_DEADLINE, HS_CYCLE_STARTED, HS_NO_START, HS_INTERVAL_MISMATCH,
    HS_OBSERVED_CLIP, HS_INVALID_OBSERVATIONS, HS_SOURCE, HS_WEAK,
    HS_CAPACITY_EXHAUSTED, HS_UNINITIALIZED
};
/* present mirrors EVENT_KEYS plus observations: 13 bits: version,kind,event_id,
 * boot_id,time,context,batch,installation,cycle,start,end,new_config,observations.
 * Its purposes are required-field checks and receipt identity, including
 * absent-vs-present optional fields. Missing dynamic values must also be
 * translated to invalid values if missing. context binds ACTUAL configuration,
 * not a caller-chosen hash. Transport resolves hashes to immutable snapshots.
 * Numeric invalid dynamic types use NaN; invalid flags use values other than 0/1.
 * extra diagnostic/truth fields never enter this ABI or receipt identity. */
typedef struct {
    uint32_t present;
    int32_t version, kind;
    char event_id[HS_ID_BYTES], boot_id[HS_ID_BYTES];
    char batch_id[HS_ID_BYTES], installation_id[HS_ID_BYTES], cycle_id[HS_ID_BYTES];
    double monotonic_s, start_s, end_s;
    const hs_config *context, *new_config;
    const hm_observation *observations;
    size_t observation_count;
} hs_event;
typedef struct {
    int32_t state, has_time, has_stable, has_window, has_evidence_end, has_pending;
    double last_monotonic_s, stable_after_s, last_window_end_s, last_evidence_end_s;
    double pending_start_s, pending_end_s, pending_deadline_s;
    char boot_id[HS_ID_BYTES], batch_id[HS_ID_BYTES], installation_id[HS_ID_BYTES];
    char pending_cycle_id[HS_ID_BYTES], evidence_cycle_ids[HS_MAX_CYCLES][HS_ID_BYTES];
    uint32_t weak_cycles;
} hs_snapshot;
typedef struct {
    int32_t accepted, disposition, reason, assessment_present, independent_review_due;
    int32_t collection_authorized, capacity_latched;
    hm_result assessment;
    hs_snapshot snapshot;
    hs_config config;
} hs_output;
typedef struct { char boot_id[HS_ID_BYTES], event_id[HS_ID_BYTES]; uint8_t digest[32]; } hs_receipt;
typedef struct {
    uint32_t initialized, capacity_latched;
    hs_config config;
    hs_snapshot snapshot;
    uint32_t message_count, cycle_count, boot_count, installation_count, batch_count;
    hs_receipt messages[HS_MAX_MESSAGES];
    char cycles[HS_MAX_CYCLES][HS_ID_BYTES], boots[HS_MAX_BOOTS][HS_ID_BYTES];
    char installations[HS_MAX_INSTALLATIONS][HS_ID_BYTES], batches[HS_MAX_BATCHES][HS_ID_BYTES];
} hs_machine;

int hs_config_valid(const hs_config *config);
int hs_config_equal(const hs_config *a, const hs_config *b);
/* init is a TRUE cold initialization, not a boot event. It erases all receipts
 * and weak evidence. Caller must guarantee a fresh trusted boot identity after
 * init; uniqueness/persistent epoch storage are NOT implemented here. Invalid
 * config initializes an unusable fail-closed object and returns 0. */
int hs_init(hs_machine *machine, const hs_config *config);
/* All history capacities are exact; next NEW record beyond a capacity latches
 * FAULT, clears evidence/pending, and rejects even boot/recover/configure until
 * true init. Duplicate receipts at full capacity remain idempotent. No eviction.
 * Return 1 means output populated (accepted is separate), 0 means NULL API arg. */
int hs_handle(hs_machine *machine, const hs_event *event, hs_output *output);
#endif

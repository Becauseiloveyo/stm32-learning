#ifndef HANGER_MEASUREMENT_H
#define HANGER_MEASUREMENT_H

#include <stddef.h>
#include <stdint.h>

/* C99 ABI v1: IEEE binary64 double seconds / degC / %RH / Pa; round-to-nearest,
 * no fast-math, FMA contraction disabled for reference comparison. No calibration.
 * Callers own buffers and immutable configuration for the duration of a call.
 * No pointers or observation buffers are retained after hm_assess returns.
 * Output and input buffers must not overlap; the caller guarantees extents.
 * IEEE binary64 arithmetic/exp must be requalified on each target toolchain. */
#define HM_API_VERSION 1
#define HM_MAX_SAMPLES 512U
#define HM_MAX_DURATION_S 86400.0
#define HM_MIN_PERIOD_S 0.001
#define HM_REQUIRED_FIELDS 2047U

typedef struct {
    int32_t version;
    double baseline_s, flush_s, recovery_s, sample_period_s, max_gap_factor;
    double head_window_s, tail_window_s, time_tolerance_s;
    uint32_t min_phase_samples, min_window_samples;
} hm_protocol;

typedef struct {
    double high_ambient_rh_pct, saturation_rh_pct, ambient_change_rh_pct;
    double temp_mismatch_c, strong_delta_pa, weak_delta_pa;
} hm_gates;

/* Bits 0..10 are the required observation keys in the Python contract, ordered
 * version, units, protocol, time, phase, ambient T/RH, local T/RH, valid, clip.
 * A missing key clears its bit. A present nonnumeric measurement becomes NaN.
 * Flags must be 0 or 1; other integers explicitly denote invalid dynamic types.
 * Protocol identity is the actual immutable parameter snapshot, not a caller
 * string or unchecked hash. The transport must resolve versioned identities. */
typedef struct {
    uint32_t present;
    int32_t observation_version, units; /* units=1 means s_degC_pctRH_Pa */
    const hm_protocol *protocol;
    int32_t phase; /* 0 baseline, 1 flush, 2 recovery; other values invalid */
    int32_t sensor_valid, clip_event;
    double time_s, ambient_T_C, ambient_RH_pct, local_T_C, local_RH_pct;
} hm_observation;

enum { HM_UNKNOWN=0, HM_VAPOR_SOURCE_PRESENT=1, HM_LOW_SIGNAL_UNCONFIRMED=2 };
enum { HM_OK=0, HM_CONFIG_ERROR=1, HM_CAPACITY_ERROR=2 };
enum {
    HM_MISSING_DATA=1U<<0, HM_MISSING_FIELD=1U<<1, HM_VERSION=1U<<2,
    HM_UNITS=1U<<3, HM_PROTOCOL=1U<<4, HM_INVALID_FLAG=1U<<5,
    HM_INVALID_SENSOR=1U<<6, HM_DOMAIN=1U<<7, HM_NON_MONOTONIC=1U<<8,
    HM_SAMPLE_GAP=1U<<9, HM_MISSING_PHASE=1U<<10, HM_PHASE_TIME=1U<<11,
    HM_INCOMPLETE=1U<<12, HM_PHASE_SAMPLES=1U<<13, HM_FEATURE_SAMPLES=1U<<14,
    HM_HIGH_HUMIDITY=1U<<15, HM_SATURATION=1U<<16, HM_AMBIENT_CHANGE=1U<<17,
    HM_TEMP_MISMATCH=1U<<18, HM_CLIP=1U<<19, HM_AMBIGUOUS=1U<<20,
    HM_LOW_NOT_DRY=1U<<21, HM_BAD_CONFIG=1U<<22, HM_CAPACITY=1U<<23
};

typedef struct {
    int32_t state, features_valid, collection_authorized;
    uint32_t reasons;
    double recovery_rise_Pa, tail_delta_Pa;
} hm_result;

int hm_protocol_valid(const hm_protocol *p);
int hm_gates_valid(const hm_gates *g);
int hm_protocol_equal(const hm_protocol *a, const hm_protocol *b);
int hm_gates_equal(const hm_gates *a, const hm_gates *b);
double hm_total_s(const hm_protocol *p);
int hm_assess(const hm_observation *rows, size_t count, const hm_protocol *p,
              const hm_gates *g, hm_result *out);

#endif

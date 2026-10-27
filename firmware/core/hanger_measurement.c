#include "hanger_measurement.h"
#include "hanger_mean_internal.h"
#include <math.h>
#include <fenv.h>
#include <string.h>

double hm_total_s(const hm_protocol *p) { return (p->baseline_s+p->flush_s)+p->recovery_s; }
static int positive(double x) { return isfinite(x) && x>0.0; }
static int aligned(double t, const hm_protocol *p) {
    double ratio=t/p->sample_period_s;
    return isfinite(ratio) && fabs(t-round(ratio)*p->sample_period_s)<=p->time_tolerance_s;
}
int hm_protocol_valid(const hm_protocol *p) {
    double total, n;
    if (!p || p->version!=HM_API_VERSION || fegetround()!=FE_TONEAREST) return 0;
    if (!positive(p->baseline_s) || !positive(p->flush_s) || !positive(p->recovery_s) ||
        !positive(p->sample_period_s) || !positive(p->max_gap_factor) ||
        !positive(p->head_window_s) || !positive(p->tail_window_s) || !positive(p->time_tolerance_s)) return 0;
    total=hm_total_s(p); n=total/p->sample_period_s;
    if (!isfinite(total) || total>HM_MAX_DURATION_S || p->sample_period_s<HM_MIN_PERIOD_S ||
        !isfinite(n) || round(n)>HM_MAX_SAMPLES-1U || p->max_gap_factor<1.0 ||
        !isfinite(p->sample_period_s*p->max_gap_factor) ||
        p->time_tolerance_s>=p->sample_period_s/1000.0 ||
        p->min_phase_samples<2U || p->min_phase_samples>HM_MAX_SAMPLES ||
        p->min_window_samples<2U || p->min_window_samples>HM_MAX_SAMPLES) return 0;
    if (!aligned(p->baseline_s,p) || !aligned(p->baseline_s+p->flush_s,p) || !aligned(total,p)) return 0;
    if (round(p->baseline_s/p->sample_period_s)<p->min_phase_samples ||
        round(p->flush_s/p->sample_period_s)<p->min_phase_samples ||
        round(p->recovery_s/p->sample_period_s)+1.0<p->min_phase_samples ||
        p->head_window_s+p->tail_window_s>=p->recovery_s ||
        floor((p->head_window_s+p->time_tolerance_s)/p->sample_period_s)+1.0<p->min_window_samples ||
        floor((p->tail_window_s+p->time_tolerance_s)/p->sample_period_s)+1.0<p->min_window_samples) return 0;
    return 1;
}
int hm_gates_valid(const hm_gates *g) {
    if (!g) return 0;
    return isfinite(g->high_ambient_rh_pct) && g->high_ambient_rh_pct>=0.0 && g->high_ambient_rh_pct<=100.0 &&
        isfinite(g->saturation_rh_pct) && g->saturation_rh_pct>=0.0 && g->saturation_rh_pct<=100.0 &&
        isfinite(g->ambient_change_rh_pct) && g->ambient_change_rh_pct>=0.0 && g->ambient_change_rh_pct<=100.0 &&
        isfinite(g->temp_mismatch_c) && g->temp_mismatch_c>=0.0 && g->temp_mismatch_c<=50.0 &&
        isfinite(g->strong_delta_pa) && isfinite(g->weak_delta_pa) &&
        g->weak_delta_pa>=0.0 && g->strong_delta_pa>g->weak_delta_pa;
}
int hm_protocol_equal(const hm_protocol *a, const hm_protocol *b) {
    if (!a || !b) return 0;
    return a->version==b->version && a->baseline_s==b->baseline_s && a->flush_s==b->flush_s &&
        a->recovery_s==b->recovery_s && a->sample_period_s==b->sample_period_s && a->max_gap_factor==b->max_gap_factor &&
        a->head_window_s==b->head_window_s && a->tail_window_s==b->tail_window_s &&
        a->time_tolerance_s==b->time_tolerance_s && a->min_phase_samples==b->min_phase_samples &&
        a->min_window_samples==b->min_window_samples;
}
int hm_gates_equal(const hm_gates *a, const hm_gates *b) {
    if (!a || !b) return 0;
    return a->high_ambient_rh_pct==b->high_ambient_rh_pct && a->saturation_rh_pct==b->saturation_rh_pct &&
        a->ambient_change_rh_pct==b->ambient_change_rh_pct && a->temp_mismatch_c==b->temp_mismatch_c &&
        a->strong_delta_pa==b->strong_delta_pa && a->weak_delta_pa==b->weak_delta_pa;
}
static int phase_at(double t, const hm_protocol *p) {
    return t<p->baseline_s-p->time_tolerance_s ? 0 : (t<p->baseline_s+p->flush_s-p->time_tolerance_s ? 1 : 2);
}
static double vapor(double t, double rh) { return (610.94*exp(17.625*t/(t+243.04)))*rh/100.0; }
int hm_assess(const hm_observation *rows, size_t count, const hm_protocol *p,
              const hm_gates *g, hm_result *out) {
    size_t i, phases[3]={0,0,0}, heads=0, tails=0;
    double start,total,tol,amin=100.0,amax=0.0,cmax=0.0,tmax=0.0,head;
    hm_sum hsum={{0},{0}},tsum={{0},{0}};
    if (!out) return HM_CONFIG_ERROR;
    memset(out,0,sizeof(*out));
    if (!hm_protocol_valid(p) || !hm_gates_valid(g)) { out->reasons=HM_BAD_CONFIG; return HM_CONFIG_ERROR; }
    if (count>HM_MAX_SAMPLES) { out->reasons=HM_CAPACITY; return HM_CAPACITY_ERROR; }
    if (!rows || !count) { out->reasons=HM_MISSING_DATA; return HM_OK; }
    for (i=0;i<count;i++) if ((rows[i].present&HM_REQUIRED_FIELDS)!=HM_REQUIRED_FIELDS) {
        out->reasons=HM_MISSING_FIELD; return HM_OK;
    }
    for (i=0;i<count;i++) {
        if (rows[i].observation_version!=1) out->reasons|=HM_VERSION;
        if (rows[i].units!=1) out->reasons|=HM_UNITS;
        if (!hm_protocol_equal(rows[i].protocol,p)) out->reasons|=HM_PROTOCOL;
    }
    if (out->reasons) return HM_OK;
    for (i=0;i<count;i++) if ((rows[i].sensor_valid!=0 && rows[i].sensor_valid!=1) ||
                              (rows[i].clip_event!=0 && rows[i].clip_event!=1)) {
        out->reasons=HM_INVALID_FLAG; return HM_OK;
    }
    for (i=0;i<count;i++) if (!rows[i].sensor_valid || !isfinite(rows[i].time_s) ||
        !isfinite(rows[i].ambient_T_C) || !isfinite(rows[i].ambient_RH_pct) ||
        !isfinite(rows[i].local_T_C) || !isfinite(rows[i].local_RH_pct)) {
        out->reasons=HM_INVALID_SENSOR; return HM_OK;
    }
    for (i=0;i<count;i++) if (rows[i].ambient_T_C<0.0 || rows[i].ambient_T_C>50.0 ||
        rows[i].local_T_C<0.0 || rows[i].local_T_C>50.0 || rows[i].ambient_RH_pct<0.0 ||
        rows[i].ambient_RH_pct>100.0 || rows[i].local_RH_pct<0.0 || rows[i].local_RH_pct>100.0) {
        out->reasons=HM_DOMAIN; return HM_OK;
    }
    start=p->baseline_s+p->flush_s; total=hm_total_s(p); tol=p->time_tolerance_s;
    for (i=0;i<count;i++) {
        const hm_observation *r=&rows[i];
        if (i && r->time_s<=rows[i-1].time_s) out->reasons|=HM_NON_MONOTONIC;
        if (i && r->time_s-rows[i-1].time_s>p->sample_period_s*p->max_gap_factor+tol) out->reasons|=HM_SAMPLE_GAP;
        if (r->phase>=0 && r->phase<3) phases[r->phase]++;
        if (r->phase!=phase_at(r->time_s,p)) out->reasons|=HM_PHASE_TIME;
        if (r->time_s < -tol || r->time_s>total+tol) out->reasons|=HM_INCOMPLETE;
        if (r->phase==2 && r->time_s<=start+p->head_window_s+tol) heads++;
        if (r->phase==2 && r->time_s>=total-p->tail_window_s-tol) tails++;
    }
    if (fabs(rows[0].time_s)>tol || fabs(rows[count-1].time_s-total)>tol) out->reasons|=HM_INCOMPLETE;
    for (i=0;i<3;i++) {
        if (!phases[i]) out->reasons|=HM_MISSING_PHASE;
        if (phases[i]<p->min_phase_samples) out->reasons|=HM_PHASE_SAMPLES;
    }
    if (heads<p->min_window_samples || tails<p->min_window_samples) out->reasons|=HM_FEATURE_SAMPLES;
    if (out->reasons) return HM_OK;
    for (i=0;i<count;i++) {
        const hm_observation *r=&rows[i];
        double delta=vapor(r->local_T_C,r->local_RH_pct)-vapor(r->ambient_T_C,r->ambient_RH_pct);
        if (r->ambient_RH_pct<amin) amin=r->ambient_RH_pct;
        if (r->ambient_RH_pct>amax) amax=r->ambient_RH_pct;
        if (r->local_RH_pct>cmax) cmax=r->local_RH_pct;
        if (fabs(r->local_T_C-r->ambient_T_C)>tmax) tmax=fabs(r->local_T_C-r->ambient_T_C);
        if (r->clip_event) out->reasons|=HM_CLIP;
        if (r->phase==2 && r->time_s<=start+p->head_window_s+tol) hm_sum_add(&hsum,delta);
        if (r->phase==2 && r->time_s>=total-p->tail_window_s-tol) hm_sum_add(&tsum,delta);
    }
    if (amax>=g->high_ambient_rh_pct) out->reasons|=HM_HIGH_HUMIDITY;
    if (cmax>=g->saturation_rh_pct) out->reasons|=HM_SATURATION;
    if (amax-amin>g->ambient_change_rh_pct) out->reasons|=HM_AMBIENT_CHANGE;
    if (tmax>g->temp_mismatch_c) out->reasons|=HM_TEMP_MISMATCH;
    head=hm_mean(&hsum,heads); out->tail_delta_Pa=hm_mean(&tsum,tails);
    out->recovery_rise_Pa=out->tail_delta_Pa-head; out->features_valid=1;
    if (out->reasons) return HM_OK;
    if (out->recovery_rise_Pa>=g->strong_delta_pa && out->tail_delta_Pa>=g->strong_delta_pa) out->state=HM_VAPOR_SOURCE_PRESENT;
    else if (fabs(out->recovery_rise_Pa)<=g->weak_delta_pa && fabs(out->tail_delta_Pa)<=g->weak_delta_pa) {
        out->state=HM_LOW_SIGNAL_UNCONFIRMED; out->reasons=HM_LOW_NOT_DRY;
    } else out->reasons=HM_AMBIGUOUS;
    return HM_OK;
}

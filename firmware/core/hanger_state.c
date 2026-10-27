#include "hanger_state.h"
#include "hanger_sha256_internal.h"
#include <math.h>
#include <fenv.h>
#include <string.h>

#define HS_MAGIC 0x485331U
#define HAS(e,bit) (((e)->present&(1U<<(bit)))!=0)
static int pos(double x) { return isfinite(x) && x>0.0 && x<=HS_MAX_TIME_S; }
int hs_config_valid(const hs_config *c) {
    const hs_policy *p;
    if(!c || c->version!=1 || !hm_protocol_valid(&c->protocol) || !hm_gates_valid(&c->gates)) return 0;
    p=&c->policy;
    return pos(p->stabilization_wait_s) && pos(p->min_cycle_gap_s) && pos(p->max_evidence_gap_s) &&
        pos(p->max_event_gap_s) && pos(p->completion_grace_s) && pos(p->timestamp_tolerance_s) &&
        p->review_after_weak_cycles>=1 && p->review_after_weak_cycles<=HS_MAX_CYCLES &&
        p->max_evidence_gap_s>=p->min_cycle_gap_s &&
        p->timestamp_tolerance_s<p->stabilization_wait_s/1000.0 &&
        p->timestamp_tolerance_s<p->min_cycle_gap_s/1000.0 &&
        p->timestamp_tolerance_s<p->max_event_gap_s/1000.0 &&
        p->timestamp_tolerance_s<p->completion_grace_s/1000.0 &&
        p->timestamp_tolerance_s<c->protocol.sample_period_s/1000.0;
}
int hs_config_equal(const hs_config *a, const hs_config *b) {
    if(!a || !b) return 0;
    return a->version==b->version && hm_protocol_equal(&a->protocol,&b->protocol) && hm_gates_equal(&a->gates,&b->gates) &&
        a->policy.stabilization_wait_s==b->policy.stabilization_wait_s && a->policy.min_cycle_gap_s==b->policy.min_cycle_gap_s &&
        a->policy.max_evidence_gap_s==b->policy.max_evidence_gap_s && a->policy.max_event_gap_s==b->policy.max_event_gap_s &&
        a->policy.completion_grace_s==b->policy.completion_grace_s && a->policy.timestamp_tolerance_s==b->policy.timestamp_tolerance_s &&
        a->policy.review_after_weak_cycles==b->policy.review_after_weak_cycles;
}
static int valid_id(const char id[HS_ID_BYTES]) {
    size_t i;
    for(i=0;i<HS_ID_BYTES;i++) {
        unsigned char ch=(unsigned char)id[i];
        if(!ch) return i>0 && id[0]!=' ' && id[i-1]!=' ';
        if(ch<32 || ch>126) return 0;
    }
    return 0;
}
static int same(const char *a,const char *b) { return strncmp(a,b,HS_ID_BYTES)==0; }
static void copy_id(char *to,const char *from) { memcpy(to,from,HS_ID_BYTES); }
static int contains(const char ids[][HS_ID_BYTES],uint32_t n,const char *id) {
    uint32_t i; for(i=0;i<n;i++) if(same(ids[i],id)) return 1; return 0;
}
static void clear_evidence(hs_machine *m) {
    m->snapshot.weak_cycles=0; m->snapshot.has_evidence_end=0;
    memset(m->snapshot.evidence_cycle_ids,0,sizeof(m->snapshot.evidence_cycle_ids));
}
static void fault(hs_machine *m) {
    clear_evidence(m); m->snapshot.has_pending=0; m->snapshot.has_stable=0; m->snapshot.state=HS_FAULT;
}
static void detach(hs_machine *m) {
    clear_evidence(m); m->snapshot.has_pending=0; m->snapshot.has_stable=0;
    memset(m->snapshot.installation_id,0,HS_ID_BYTES); m->snapshot.state=HS_AWAITING_INSTALLATION;
}
static int reply(hs_machine *m,hs_output *o,int accepted,int reason,int disposition) {
    o->accepted=accepted; o->reason=reason; o->disposition=disposition;
    o->snapshot=m->snapshot; o->config=m->config; o->capacity_latched=(int32_t)m->capacity_latched;
    o->independent_review_due=m->snapshot.weak_cycles>=m->config.policy.review_after_weak_cycles && m->initialized==HS_MAGIC;
    o->collection_authorized=0; return 1;
}
static int failure(hs_machine *m,hs_output *o,int reason) { fault(m); return reply(m,o,0,reason,HS_REJECTED); }
static int capacity(hs_machine *m,hs_output *o) { m->capacity_latched=1; return failure(m,o,HS_CAPACITY_EXHAUSTED); }
static int deadline_ok(double a,double b) { return isfinite(b) && b>a && b<=HS_MAX_TIME_S; }
static int stabilize(hs_machine *m,double now) {
    double deadline=now+m->config.policy.stabilization_wait_s;
    if(!deadline_ok(now,deadline)) return 0;
    clear_evidence(m); m->snapshot.has_pending=0; m->snapshot.has_stable=1;
    m->snapshot.stable_after_s=deadline; m->snapshot.state=HS_STABILIZING; return 1;
}
int hs_init(hs_machine *m,const hs_config *c) {
    if(!m) return 0;
    memset(m,0,sizeof(*m));
    if(!hs_config_valid(c)) { m->snapshot.state=HS_FAULT; return 0; }
    m->config=*c; m->initialized=HS_MAGIC; return 1;
}

/* Digest individual scalar fields; never hash padding, pointer addresses, or
 * unconsumed diagnostics. Zero/NaN use canonical representations. This typed
 * receipt treats 1 and 1.0 alike; raw JSON spelling identity belongs to transport. */
static void hd(hs_sha *s,double v) { if(v==0.0) v=0.0; if(isnan(v)) v=NAN; hs_sha_add(s,&v,sizeof(v)); }
static void hi(hs_sha *s,uint32_t v) { hs_sha_add(s,&v,sizeof(v)); }
static void hid(hs_sha *s,const char *id) {
    size_t i; for(i=0;i<HS_ID_BYTES;i++) { hs_sha_add(s,id+i,1); if(id[i]==0) break; }
}
static void hp(hs_sha *s,const hm_protocol *p) {
    hi(s,p!=NULL); if(!p) return;
    hi(s,(uint32_t)p->version); hd(s,p->baseline_s); hd(s,p->flush_s); hd(s,p->recovery_s);
    hd(s,p->sample_period_s); hd(s,p->max_gap_factor); hd(s,p->head_window_s); hd(s,p->tail_window_s);
    hd(s,p->time_tolerance_s); hi(s,p->min_phase_samples); hi(s,p->min_window_samples);
}
static void hc(hs_sha *s,const hs_config *c) {
    hi(s,c!=NULL); if(!c) return;
    hi(s,(uint32_t)c->version); hp(s,&c->protocol);
    hd(s,c->gates.high_ambient_rh_pct); hd(s,c->gates.saturation_rh_pct); hd(s,c->gates.ambient_change_rh_pct);
    hd(s,c->gates.temp_mismatch_c); hd(s,c->gates.strong_delta_pa); hd(s,c->gates.weak_delta_pa);
    hd(s,c->policy.stabilization_wait_s); hd(s,c->policy.min_cycle_gap_s); hd(s,c->policy.max_evidence_gap_s);
    hd(s,c->policy.max_event_gap_s); hd(s,c->policy.completion_grace_s); hd(s,c->policy.timestamp_tolerance_s);
    hi(s,c->policy.review_after_weak_cycles);
}
static void signature(const hs_event *e,uint8_t digest[32]) {
    hs_sha s; size_t i; hs_sha_init(&s);
    hi(&s,e->present); hi(&s,(uint32_t)e->version); hi(&s,(uint32_t)e->kind);
    hid(&s,e->event_id); hid(&s,e->boot_id); hid(&s,e->batch_id); hid(&s,e->installation_id); hid(&s,e->cycle_id);
    hd(&s,e->monotonic_s); hd(&s,e->start_s); hd(&s,e->end_s); hc(&s,e->context); hc(&s,e->new_config);
    hi(&s,(uint32_t)e->observation_count); hi(&s,e->observations!=NULL);
    if(e->observations) for(i=0;i<e->observation_count;i++) {
        const hm_observation *r=&e->observations[i];
        hi(&s,r->present); hi(&s,(uint32_t)r->observation_version); hi(&s,(uint32_t)r->units); hp(&s,r->protocol);
        hi(&s,(uint32_t)r->phase); hi(&s,(uint32_t)r->sensor_valid); hi(&s,(uint32_t)r->clip_event);
        hd(&s,r->time_s); hd(&s,r->ambient_T_C); hd(&s,r->ambient_RH_pct); hd(&s,r->local_T_C); hd(&s,r->local_RH_pct);
    }
    hs_sha_end(&s,digest);
}

int hs_handle(hs_machine *m,const hs_event *e,hs_output *o) {
    hs_snapshot *s; const hs_policy *p; double now,gap,end,deadline,tol; uint8_t digest[32]; uint32_t i; int k;
    if(!m || !o) return 0;
    memset(o,0,sizeof(*o));
    if(m->initialized!=HS_MAGIC) return failure(m,o,HS_UNINITIALIZED);
    if(m->capacity_latched) return failure(m,o,HS_CAPACITY_EXHAUSTED);
    if(fegetround()!=FE_TONEAREST) return failure(m,o,HS_INVALID_CONFIG);
    if(!e || (e->present&15U)!=15U || e->version!=1 || e->kind<HS_BOOT || e->kind>HS_FINISH || !valid_id(e->event_id) || !valid_id(e->boot_id))
        return failure(m,o,HS_MALFORMED_EVENT);
    s=&m->snapshot; k=e->kind;
    if(k!=HS_BOOT && !same(e->boot_id,s->boot_id)) return reply(m,o,0,HS_STALE_BOOT,HS_REJECTED);
    if(k==HS_BOOT && contains((const char (*)[HS_ID_BYTES])m->boots,m->boot_count,e->boot_id) && !same(e->boot_id,s->boot_id))
        return reply(m,o,0,HS_RETIRED_BOOT,HS_REJECTED);
    if(!HAS(e,5) || !hs_config_equal(e->context,&m->config)) return reply(m,o,0,HS_CONFIG_MISMATCH,HS_REJECTED);
    if((k==HS_CLIP || k==HS_RECOVER || k==HS_START || k==HS_FINISH) &&
       (!same(e->batch_id,s->batch_id) || !same(e->installation_id,s->installation_id))) return reply(m,o,0,HS_SCOPE_MISMATCH,HS_REJECTED);
    if(e->observation_count>HM_MAX_SAMPLES) return capacity(m,o);
    signature(e,digest);
    for(i=0;i<m->message_count;i++) if(same(e->boot_id,m->messages[i].boot_id) && same(e->event_id,m->messages[i].event_id)) {
        if(memcmp(digest,m->messages[i].digest,32)==0) return reply(m,o,0,HS_DUPLICATE_EVENT,HS_IGNORED_DUPLICATE);
        return failure(m,o,HS_EVENT_CONFLICT);
    }
    if(m->message_count==HS_MAX_MESSAGES) return capacity(m,o);
    copy_id(m->messages[m->message_count].boot_id,e->boot_id); copy_id(m->messages[m->message_count].event_id,e->event_id);
    memcpy(m->messages[m->message_count++].digest,digest,32);
    if(k==HS_BOOT && contains((const char (*)[HS_ID_BYTES])m->boots,m->boot_count,e->boot_id)) return reply(m,o,0,HS_BOOT_REUSED,HS_REJECTED);
    if(k==HS_INSTALL) {
        if(!HAS(e,6) || !HAS(e,7) || !valid_id(e->batch_id) || !valid_id(e->installation_id)) return failure(m,o,HS_INVALID_INSTALLATION);
        if(contains((const char (*)[HS_ID_BYTES])m->installations,m->installation_count,e->installation_id) ||
           contains((const char (*)[HS_ID_BYTES])m->batches,m->batch_count,e->batch_id)) return reply(m,o,0,HS_RETIRED_INSTALLATION,HS_REJECTED);
    }
    now=e->monotonic_s;
    if(!HAS(e,4) || !isfinite(now) || now<0.0 || now>HS_MAX_TIME_S) return failure(m,o,HS_INVALID_TIME);
    if(k==HS_BOOT) {
        if(m->boot_count==HS_MAX_BOOTS) return capacity(m,o);
        copy_id(m->boots[m->boot_count++],e->boot_id); copy_id(s->boot_id,e->boot_id); memset(s->batch_id,0,HS_ID_BYTES);
        m->installation_count=0; m->batch_count=0; m->cycle_count=0;
        s->has_window=0; s->has_time=1; s->last_monotonic_s=now; detach(m);
        return reply(m,o,1,HS_BOOT_INSTALL,HS_APPLIED);
    }
    if(k==HS_START || k==HS_FINISH) {
        if(!HAS(e,8) || !valid_id(e->cycle_id)) return failure(m,o,HS_INVALID_CYCLE);
        if(contains((const char (*)[HS_ID_BYTES])m->cycles,m->cycle_count,e->cycle_id) &&
           (k==HS_START || !s->has_pending || !same(s->pending_cycle_id,e->cycle_id))) return reply(m,o,0,HS_CYCLE_REUSED,HS_IGNORED_DUPLICATE);
    }
    if(now<=s->last_monotonic_s) return failure(m,o,HS_NONINCREASING_TIME);
    gap=now-s->last_monotonic_s; s->last_monotonic_s=now; p=&m->config.policy; tol=p->timestamp_tolerance_s;
    if(gap>p->max_event_gap_s) return failure(m,o,HS_TIME_JUMP);
    if(k!=HS_INSTALL && k!=HS_CONFIGURE && k!=HS_CLIP && k!=HS_RECOVER && s->has_pending && now>s->pending_deadline_s+tol)
        return failure(m,o,HS_TIMEOUT);
    if(s->has_evidence_end && !s->has_pending && now-s->last_evidence_end_s>p->max_evidence_gap_s) {
        clear_evidence(m); if(s->state==HS_AWAITING_RECHECK) s->state=HS_READY;
    }
    if(k==HS_INSTALL) {
        int retire=s->batch_id[0] && !same(s->batch_id,e->batch_id);
        if(m->installation_count==HS_MAX_INSTALLATIONS || (retire && m->batch_count==HS_MAX_BATCHES)) return capacity(m,o);
        if(retire) copy_id(m->batches[m->batch_count++],s->batch_id);
        copy_id(s->batch_id,e->batch_id); copy_id(s->installation_id,e->installation_id);
        copy_id(m->installations[m->installation_count++],e->installation_id);
        if(!stabilize(m,now)) return failure(m,o,HS_BAD_DEADLINE);
        return reply(m,o,1,HS_INSTALL_WAIT,HS_APPLIED);
    }
    if(k==HS_CONFIGURE) {
        if(!HAS(e,11) || !hs_config_valid(e->new_config)) return failure(m,o,HS_INVALID_CONFIG);
        if(hs_config_equal(&m->config,e->new_config)) return reply(m,o,1,HS_CONFIG_UNCHANGED,HS_APPLIED);
        m->config=*e->new_config; detach(m); return reply(m,o,1,HS_CONFIG_CHANGED,HS_APPLIED);
    }
    if(k==HS_CLIP) { detach(m); return reply(m,o,1,HS_CLIP_INSTALL,HS_APPLIED); }
    if(k==HS_RECOVER) {
        if(s->state!=HS_FAULT || !s->installation_id[0]) return reply(m,o,0,HS_RECOVERY_REQUIRES,HS_REJECTED);
        if(!stabilize(m,now)) return failure(m,o,HS_BAD_DEADLINE);
        return reply(m,o,1,HS_RECOVERY_WAIT,HS_APPLIED);
    }
    if(s->state==HS_STABILIZING && now>=s->stable_after_s) { s->state=HS_READY; s->has_stable=0; }
    if(k==HS_TICK) return reply(m,o,1,HS_CLOCK,HS_APPLIED);
    if(k==HS_START) {
        if(s->state==HS_MEASURING) return failure(m,o,HS_ALREADY_ACTIVE);
        if(s->state!=HS_READY && s->state!=HS_AWAITING_RECHECK && s->state!=HS_SOURCE_PRESENT) return reply(m,o,0,HS_NOT_READY,HS_REJECTED);
        if(s->has_window && now<s->last_window_end_s+p->min_cycle_gap_s) return reply(m,o,0,HS_SHORT_GAP,HS_REJECTED);
        end=now+hm_total_s(&m->config.protocol); deadline=end+p->completion_grace_s;
        if(!deadline_ok(now,end) || !deadline_ok(end,deadline)) return failure(m,o,HS_BAD_DEADLINE);
        if(m->cycle_count==HS_MAX_CYCLES) return capacity(m,o);
        copy_id(m->cycles[m->cycle_count++],e->cycle_id); s->has_window=1; s->last_window_end_s=end;
        s->has_pending=1; copy_id(s->pending_cycle_id,e->cycle_id); s->pending_start_s=now; s->pending_end_s=end; s->pending_deadline_s=deadline;
        s->state=HS_MEASURING; return reply(m,o,1,HS_CYCLE_STARTED,HS_APPLIED);
    }
    if(!s->has_pending || !same(s->pending_cycle_id,e->cycle_id)) return failure(m,o,HS_NO_START);
    if(!HAS(e,9) || !HAS(e,10) || !isfinite(e->start_s) || !isfinite(e->end_s) || e->end_s<=e->start_s ||
       fabs(e->start_s-s->pending_start_s)>tol || fabs(e->end_s-s->pending_end_s)>tol || now+tol<s->pending_end_s)
        return failure(m,o,HS_INTERVAL_MISMATCH);
    (void)hm_assess(HAS(e,12)?e->observations:NULL,HAS(e,12)?e->observation_count:0,&m->config.protocol,&m->config.gates,&o->assessment);
    o->assessment_present=1; s->has_pending=0;
    if(HAS(e,12) && e->observations) for(i=0;i<e->observation_count;i++) if((e->observations[i].present&(1U<<10)) && e->observations[i].clip_event==1) {
        detach(m); return reply(m,o,0,HS_OBSERVED_CLIP,HS_REJECTED);
    }
    if(o->assessment.state==HM_UNKNOWN) return failure(m,o,HS_INVALID_OBSERVATIONS);
    if(o->assessment.state==HM_VAPOR_SOURCE_PRESENT) { clear_evidence(m); s->state=HS_SOURCE_PRESENT; return reply(m,o,1,HS_SOURCE,HS_APPLIED); }
    if(s->weak_cycles==HS_MAX_CYCLES) return capacity(m,o);
    copy_id(s->evidence_cycle_ids[s->weak_cycles++],e->cycle_id); s->has_evidence_end=1; s->last_evidence_end_s=e->end_s;
    s->state=HS_AWAITING_RECHECK; return reply(m,o,1,HS_WEAK,HS_APPLIED);
}

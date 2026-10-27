/* Coordinator-authored public API cases, independent of worker fixtures.
 * Constants here deliberately form a small analytic test fixture, not firmware
 * production defaults. No actual sensor, battery, radio or garment involved. */
#include "hanger_measurement.h"
#include "hanger_energy.h"
#include "hanger_state.h"
#include "hanger_sha256_internal.h"
#include "hanger_core.h"
#include <math.h>
#include <stdio.h>
#include <string.h>

static unsigned checks;
static hm_observation observations[HM_MAX_SAMPLES];
static hs_machine machine;
#define REQUIRE(x) do { ++checks; if (!(x)) { \
    fprintf(stderr,"check failed at %s:%d: %s\n",__FILE__,__LINE__,#x); return 1; } } while (0)

static hm_protocol protocol(void)
{
    hm_protocol p;
    memset(&p,0,sizeof(p));
    p.version=1; p.baseline_s=30; p.flush_s=20; p.recovery_s=90;
    p.sample_period_s=1; p.max_gap_factor=2.5; p.head_window_s=5;
    p.tail_window_s=10; p.time_tolerance_s=1e-8;
    p.min_phase_samples=2; p.min_window_samples=2;
    return p;
}

static hm_gates gates(void)
{
    hm_gates g={90,98,3,1,100,40};
    return g;
}

static he_config energy(void)
{
    he_config c;
    memset(&c,0,sizeof(c));
    c.capacity_wh=3.885; c.eta=.85; c.base_w=.001; c.misc_w=.004/24;
    c.stopped_w=.00005; c.baseline_w=.002; c.flush_w=.002; c.recovery_w=.002;
    c.fan_w=.2; c.vent_open_j=.05; c.vent_close_j=.05; c.wifi_w=.6;
    c.boot_wh=.002; c.stop_wh=.05; c.restart_wh=.15; c.low_enter_wh=.30;
    c.low_exit_wh=.45; c.wifi_daily_wh=.08; c.wifi_success_s=6;
    c.wifi_timeout_s=20; c.wifi_attempt_limit=5;
    c.backoff_s[0]=60; c.backoff_s[1]=300; c.backoff_s[2]=900; c.backoff_s[3]=3600;
    return c;
}

static void raw_rows(const hm_protocol *p)
{
    size_t i;
    memset(observations,0,sizeof(observations));
    for(i=0;i<=140;i++) {
        hm_observation *r=&observations[i];
        r->present=HM_REQUIRED_FIELDS; r->observation_version=1; r->units=1;
        r->protocol=p; r->phase=i<30?0:(i<50?1:2); r->sensor_valid=1;
        r->time_s=(double)i; r->ambient_T_C=25; r->local_T_C=25;
        r->ambient_RH_pct=50; r->local_RH_pct=50;
    }
}

static hs_config state_config(void)
{
    hs_config c;
    memset(&c,0,sizeof(c)); c.version=1; c.protocol=protocol(); c.gates=gates();
    c.policy.stabilization_wait_s=30; c.policy.min_cycle_gap_s=30;
    c.policy.max_evidence_gap_s=1800; c.policy.max_event_gap_s=3600;
    c.policy.completion_grace_s=10; c.policy.timestamp_tolerance_s=1e-6;
    c.policy.review_after_weak_cycles=3;
    return c;
}

static hs_event event(const hs_config *config,int kind,unsigned number,double now)
{
    hs_event e;
    memset(&e,0,sizeof(e));
    e.present=8191U; e.version=1; e.kind=kind; e.context=config; e.monotonic_s=now;
    (void)snprintf(e.event_id,HS_ID_BYTES,"coordinator-%u",number);
    strcpy(e.boot_id,"boot-a"); strcpy(e.batch_id,"batch-a");
    strcpy(e.installation_id,"installation-a"); strcpy(e.cycle_id,"cycle-a");
    return e;
}

static int state_boundaries(void)
{
    hs_config c=state_config(), updated;
    hs_event e, duplicate;
    hs_output o;
    unsigned i;
    hs_sha sha;
    uint8_t digest[32];
    const uint8_t abc[32]={0xba,0x78,0x16,0xbf,0x8f,0x01,0xcf,0xea,0x41,0x41,0x40,0xde,0x5d,0xae,0x22,0x23,
        0xb0,0x03,0x61,0xa3,0x96,0x17,0x7a,0x9c,0xb4,0x10,0xff,0x61,0xf2,0x00,0x15,0xad};
    /* hashlib.sha256(b'a'*1000), to exercise multiple compression blocks. */
    const uint8_t many[32]={0x41,0xed,0xec,0xe4,0x2d,0x63,0xe8,0xd9,0xbf,0x51,0x5a,0x9b,0xa6,0x93,0x2e,0x1c,
        0x20,0xcb,0xc9,0xf5,0xa5,0xd1,0x34,0x64,0x5a,0xdb,0x5d,0xb1,0xb9,0x73,0x7e,0xa3};
    hs_sha_init(&sha); hs_sha_add(&sha,"abc",3); hs_sha_end(&sha,digest);
    REQUIRE(memcmp(digest,abc,32)==0);
    hs_sha_init(&sha); for(i=0;i<1000;i++) hs_sha_add(&sha,"a",1); hs_sha_end(&sha,digest);
    REQUIRE(memcmp(digest,many,32)==0);
    REQUIRE(hs_init(&machine,&c));
    e=event(&c,HS_BOOT,1,0);
    REQUIRE(hs_handle(&machine,&e,&o) && o.accepted && o.snapshot.state==HS_AWAITING_INSTALLATION);
    e=event(&c,HS_INSTALL,2,1);
    REQUIRE(hs_handle(&machine,&e,&o) && o.accepted && o.snapshot.stable_after_s==31);
    e=event(&c,HS_START,3,31);
    REQUIRE(hs_handle(&machine,&e,&o) && o.accepted && o.snapshot.pending_end_s==171);
    raw_rows(&c.protocol);
    e=event(&c,HS_FINISH,4,171); e.start_s=31; e.end_s=171;
    e.observations=observations; e.observation_count=141;
    REQUIRE(hs_handle(&machine,&e,&o) && o.accepted && o.snapshot.weak_cycles==1 && !o.collection_authorized);
    duplicate=e;
    REQUIRE(hs_handle(&machine,&duplicate,&o) && o.disposition==HS_IGNORED_DUPLICATE && o.snapshot.weak_cycles==1);
    observations[70].local_RH_pct=51;
    REQUIRE(hs_handle(&machine,&duplicate,&o) && o.reason==HS_EVENT_CONFLICT && o.snapshot.weak_cycles==0 && o.snapshot.state==HS_FAULT);
    REQUIRE(o.snapshot.last_monotonic_s==171 && o.snapshot.last_window_end_s==171);
    e=event(&c,HS_RECOVER,5,172);
    REQUIRE(hs_handle(&machine,&e,&o) && o.accepted && o.snapshot.stable_after_s==202);
    e=event(&c,HS_START,6,202); strcpy(e.cycle_id,"cycle-b");
    REQUIRE(hs_handle(&machine,&e,&o) && o.accepted);
    e=event(&c,HS_INSTALL,7,220); strcpy(e.installation_id,"installation-b");
    REQUIRE(hs_handle(&machine,&e,&o) && o.accepted && !o.snapshot.has_pending && o.snapshot.last_window_end_s==342);
    e=event(&c,HS_START,8,250); strcpy(e.installation_id,"installation-b"); strcpy(e.cycle_id,"cycle-c");
    REQUIRE(hs_handle(&machine,&e,&o) && o.reason==HS_SHORT_GAP && !o.snapshot.has_pending);
    updated=c; updated.gates.weak_delta_pa=30;
    e=event(&c,HS_CONFIGURE,9,251); e.new_config=&updated;
    REQUIRE(hs_handle(&machine,&e,&o) && o.accepted && o.snapshot.state==HS_AWAITING_INSTALLATION);
    e=event(&c,HS_TICK,10,252);
    REQUIRE(hs_handle(&machine,&e,&o) && o.reason==HS_CONFIG_MISMATCH && o.snapshot.last_monotonic_s==251);
    REQUIRE(hs_init(&machine,&c));
    e=event(&c,HS_BOOT,1,0); (void)hs_handle(&machine,&e,&o);
    for(i=2;i<=HS_MAX_MESSAGES;i++) { e=event(&c,HS_TICK,i,(double)i); (void)hs_handle(&machine,&e,&o); }
    REQUIRE(machine.message_count==HS_MAX_MESSAGES && !o.capacity_latched && o.accepted);
    duplicate=e;
    REQUIRE(hs_handle(&machine,&duplicate,&o) && o.reason==HS_DUPLICATE_EVENT && !o.capacity_latched);
    e=event(&c,HS_TICK,HS_MAX_MESSAGES+1U,HS_MAX_MESSAGES+1.0);
    REQUIRE(hs_handle(&machine,&e,&o) && o.reason==HS_CAPACITY_EXHAUSTED && o.capacity_latched && o.snapshot.weak_cycles==0);
    e=event(&c,HS_BOOT,HS_MAX_MESSAGES+2U,0); strcpy(e.boot_id,"boot-b");
    REQUIRE(hs_handle(&machine,&e,&o) && o.reason==HS_CAPACITY_EXHAUSTED && !o.accepted);
    REQUIRE(hs_init(&machine,&c));
    e=event(&c,HS_BOOT,1,HS_MAX_TIME_S);
    REQUIRE(hs_handle(&machine,&e,&o) && o.accepted);
    e=event(&c,HS_TICK,2,HS_MAX_TIME_S+1);
    REQUIRE(hs_handle(&machine,&e,&o) && o.reason==HS_INVALID_TIME && !o.collection_authorized);
    return 0;
}

int main(void)
{
    hm_protocol p=protocol(), altered;
    hm_gates g=gates();
    hm_result measured;
    he_config c=energy();
    he_protocol ep={30,20,90};
    he_protocol projected;
    he_budget_result budget;
    he_admission a;
    he_admission_result admission, untouched;
    he_balance balance;
    he_power_state power;
    he_power_result transition;
    he_quota quota, quota_before;
    he_wifi_result wifi;
    he_retry_result retry;
    double loads[HE_CATEGORY_COUNT]={0};
    uint64_t added=123;
    size_t i;
    raw_rows(&p);
    REQUIRE(hc_energy_protocol(&p,&projected)==HE_OK && projected.baseline_s==30 && projected.flush_s==20 && projected.recovery_s==90);
    altered=p; altered.sample_period_s=.5; altered.baseline_s=30.5; altered.recovery_s=89.5;
    REQUIRE(hm_protocol_valid(&altered));
    REQUIRE(hc_energy_protocol(&altered,&projected)==HE_ERR_RANGE && projected.baseline_s==30 && projected.recovery_s==90);
    ep=projected;
    REQUIRE(hm_assess(observations,141,&p,&g,&measured)==HM_OK);
    REQUIRE(measured.state==HM_LOW_SIGNAL_UNCONFIRMED && measured.features_valid &&
            measured.tail_delta_Pa==0 && measured.recovery_rise_Pa==0 && !measured.collection_authorized);
    REQUIRE(he_budget(&c,&ep,HE_MEASUREMENT,&budget)==HE_OK);
    REQUIRE(budget.duration_s==140 && fabs(budget.action_load_wh*3600-4.38)<1e-12);
    memset(&a,0,sizeof(a));
    a.kind=HE_MEASUREMENT; a.powered=1; a.installed=1; a.batch_matches=1;
    a.has_request=1; a.critical=1; a.can_emit=1; a.readiness=HE_READY;
    a.now_s=40; a.horizon_s=180; a.deadline_s=180; a.min_cycle_gap_s=30;
    a.store_wh=c.stop_wh+budget.required_battery_wh;
    REQUIRE(he_admit(&c,&ep,&a,NULL,&admission)==HE_OK && admission.reason==HE_ADMITTED && admission.complete_at_s==180);
    observations[70].sensor_valid=0;
    REQUIRE(hm_assess(observations,141,&p,&g,&measured)==HM_OK && measured.state==HM_UNKNOWN && !measured.features_valid);
    REQUIRE(!measured.collection_authorized); /* paid admission cannot make invalid data valid */
    raw_rows(&p);
    observations[70].local_RH_pct=NAN;
    REQUIRE(hm_assess(observations,141,&p,&g,&measured)==HM_OK && measured.reasons==HM_INVALID_SENSOR);
    raw_rows(&p); observations[70].present &= ~(1U<<3);
    REQUIRE(hm_assess(observations,141,&p,&g,&measured)==HM_OK && measured.reasons==HM_MISSING_FIELD);
    raw_rows(&p); altered=p; altered.head_window_s=6; observations[70].protocol=&altered;
    REQUIRE(hm_assess(observations,141,&p,&g,&measured)==HM_OK && measured.reasons==HM_PROTOCOL);
    raw_rows(&p); observations[70].time_s=observations[69].time_s;
    REQUIRE(hm_assess(observations,141,&p,&g,&measured)==HM_OK && (measured.reasons&HM_NON_MONOTONIC));
    raw_rows(&p); observations[70].clip_event=1;
    REQUIRE(hm_assess(observations,141,&p,&g,&measured)==HM_OK && measured.state==HM_UNKNOWN && (measured.reasons&HM_CLIP));
    REQUIRE(hm_assess(observations,HM_MAX_SAMPLES+1U,&p,&g,&measured)==HM_CAPACITY_ERROR && !measured.collection_authorized);
    raw_rows(&p);
    for(i=0;i<=140;i++) observations[i].ambient_RH_pct=observations[i].local_RH_pct=90;
    REQUIRE(hm_assess(observations,141,&p,&g,&measured)==HM_OK && (measured.reasons&HM_HIGH_HUMIDITY));
    raw_rows(&p);
    for(i=50;i<=140;i++) observations[i].local_RH_pct=50+10.0*(double)(i-50)/90.0;
    REQUIRE(hm_assess(observations,141,&p,&g,&measured)==HM_OK && measured.state==HM_VAPOR_SOURCE_PRESENT && !measured.collection_authorized);
    a.store_wh-=1e-7;
    REQUIRE(he_admit(&c,&ep,&a,NULL,&admission)==HE_OK && admission.reason==HE_DENY_ENERGY);
    a.store_wh=c.capacity_wh; a.deadline_s=179;
    REQUIRE(he_admit(&c,&ep,&a,NULL,&admission)==HE_OK && admission.reason==HE_DENY_DEADLINE);
    a.deadline_s=180; a.has_reserved_window=1; a.reserved_end_s=10; a.min_cycle_gap_s=30;
    REQUIRE(he_admit(&c,&ep,&a,NULL,&admission)==HE_OK && admission.reason==HE_ADMITTED);
    a.reserved_end_s=11;
    REQUIRE(he_admit(&c,&ep,&a,NULL,&admission)==HE_OK && admission.reason==HE_DENY_WINDOW);
    a.reserved_end_s=UINT64_MAX; untouched=admission;
    REQUIRE(he_admit(&c,&ep,&a,NULL,&admission)==HE_ERR_OVERFLOW && memcmp(&untouched,&admission,sizeof(admission))==0);
    REQUIRE(he_time_add(UINT64_MAX-1,1,&added)==HE_OK && added==UINT64_MAX);
    REQUIRE(he_time_add(UINT64_MAX,1,&added)==HE_ERR_OVERFLOW && added==UINT64_MAX);
    loads[HE_BACKGROUND_RUN]=.1; loads[HE_WIFI]=.2;
    REQUIRE(he_account(&c,HE_INTERVAL,.1,loads,0,&balance)==HE_OK);
    REQUIRE(fabs(balance.served_load_wh[HE_BACKGROUND_RUN]-.085)<1e-15 && balance.served_load_wh[HE_WIFI]==0 && fabs(balance.unmet_load_wh-.215)<1e-15);
    REQUIRE(fabs(balance.balance_residual_wh)<1e-14 && balance.store_end_wh==0);
    REQUIRE(he_account(&c,HE_IMPULSE,.1,loads,.1,&balance)==HE_ERR_RANGE);
    memset(loads,0,sizeof(loads));
    REQUIRE(he_account(&c,HE_INTERVAL,c.capacity_wh,loads,.1,&balance)==HE_OK && fabs(balance.spill_wh-.1)<1e-14);
    power.powered=0; power.mode=HE_STOPPED; power.store_wh=c.restart_wh;
    REQUIRE(he_power_update(&c,&power,1,&transition)==HE_OK && transition.booted && transition.state.powered && transition.state.mode==HE_LOW);
    REQUIRE(fabs(transition.state.store_wh-(c.restart_wh-c.boot_wh/c.eta))<1e-14);
    power=transition.state; power.store_wh=c.stop_wh;
    REQUIRE(he_power_update(&c,&power,0,&transition)==HE_OK && transition.stop_deferred && transition.state.powered);
    REQUIRE(he_power_update(&c,&power,1,&transition)==HE_OK && transition.stopped && !transition.state.powered);
    memset(&quota,0,sizeof(quota));
    REQUIRE(he_wifi_check(&c,&quota,86400-5,&wifi)==HE_ERR_UNINITIALIZED);
    REQUIRE(he_quota_init(&quota,86400-5,0,0)==HE_OK);
    REQUIRE(he_wifi_check(&c,&quota,86400-5,&wifi)==HE_OK && wifi.allowed);
    REQUIRE(fabs(wifi.battery_wh[0]-c.wifi_w*5/3600/c.eta)<1e-14 && fabs(wifi.battery_wh[1]-c.wifi_w*15/3600/c.eta)<1e-14);
    REQUIRE(quota.used_battery_wh[0]==0 && quota.used_battery_wh[1]==0); /* checking is not charging */
    REQUIRE(he_wifi_charge(&c,&quota,86400-5,20,&wifi)==HE_OK && quota.day==1 && fabs(quota.used_battery_wh[0]-wifi.battery_wh[1])<1e-14);
    quota_before=quota;
    REQUIRE(he_quota_advance(&quota,86400-5)==HE_ERR_TIME && memcmp(&quota,&quota_before,sizeof(quota))==0);
    REQUIRE(he_quota_advance(&quota,3*86400)==HE_OK && quota.used_battery_wh[0]==0);
    REQUIRE(he_retry(&c,1,100,&retry)==HE_OK && !retry.exhausted && retry.next_attempt_s==160);
    REQUIRE(he_retry(&c,5,100,&retry)==HE_OK && retry.exhausted);
    REQUIRE(he_retry(&c,1,UINT64_MAX,&retry)==HE_ERR_OVERFLOW);
    if(state_boundaries()) return 1;
    printf("{\"passed\":true,\"check_count\":%u,\"hm_observation_bytes\":%lu,\"he_config_bytes\":%lu,\"he_quota_bytes\":%lu,\"hs_machine_bytes\":%lu}\n",
           checks,(unsigned long)sizeof(hm_observation),(unsigned long)sizeof(he_config),(unsigned long)sizeof(he_quota),(unsigned long)sizeof(hs_machine));
    return 0;
}

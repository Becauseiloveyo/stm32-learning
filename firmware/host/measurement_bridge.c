/* Test-only export bridge. Core state is caller owned, no hidden global machine. */
#include "hanger_state.h"
#include "hanger_sha256_internal.h"
#include "hanger_mean_internal.h"
#include <fenv.h>
#ifdef _WIN32
#define MB_EXPORT __declspec(dllexport)
#else
#define MB_EXPORT
#endif
MB_EXPORT int mb_init(hs_machine *m,const hs_config *c) { return hs_init(m,c); }
MB_EXPORT int mb_handle(hs_machine *m,const hs_event *e,hs_output *o) { return hs_handle(m,e,o); }
MB_EXPORT int mb_assess(const hm_observation *r,size_t n,const hm_protocol *p,const hm_gates *g,hm_result *o) { return hm_assess(r,n,p,g,o); }
MB_EXPORT size_t mb_size(unsigned type) {
    switch(type) {
        case 0:return sizeof(hs_machine); case 1:return sizeof(hm_observation); case 2:return sizeof(hs_event);
        case 3:return sizeof(hs_output); case 4:return sizeof(hs_config); case 5:return sizeof(hm_protocol);
        case 6:return sizeof(hm_result); case 7:return sizeof(hs_snapshot); case 8:return sizeof(hs_receipt);
        case 9:return sizeof(hs_sha); case 10:return sizeof(long double); default:return 0;
    }
}
MB_EXPORT void mb_counts(const hs_machine *m,uint32_t out[5]) {
    out[0]=m->message_count; out[1]=m->cycle_count; out[2]=m->boot_count; out[3]=m->installation_count; out[4]=m->batch_count;
}
MB_EXPORT void mb_sha(const void *p,size_t n,uint8_t out[32]) { hs_sha s; hs_sha_init(&s); hs_sha_add(&s,p,n); hs_sha_end(&s,out); }
MB_EXPORT int mb_mean(const double *values,size_t n,double *out) {
    hm_sum sum={{0},{0}}; size_t i;
    if(!values || !out || !n || n>HM_MAX_SAMPLES) return 0;
    for(i=0;i<n;i++) {
        if(!isfinite(values[i]) || fabs(values[i])>12360.0) return 0;
        hm_sum_add(&sum,values[i]);
    }
    *out=hm_mean(&sum,n); return 1;
}
MB_EXPORT int mb_rounding_guard(const hs_config *c) {
    int original=fegetround(), rejected;
    if(original==-1 || fesetround(FE_UPWARD)!=0) return 0;
    rejected=!hm_protocol_valid(&c->protocol) && !hs_config_valid(c);
    if(fesetround(original)!=0) return 0;
    return rejected;
}

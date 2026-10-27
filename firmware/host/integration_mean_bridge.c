/* Independent host test access to the internal fixed integer accumulator. */
#include "hanger_mean_internal.h"
#if defined(_WIN32)
#define EXPORT __declspec(dllexport)
#else
#define EXPORT __attribute__((visibility("default")))
#endif
EXPORT int integration_mean(const double *values,size_t count,double *out)
{
    hm_sum sum={{0},{0}};
    size_t i;
    if(!values || !out || count==0 || count>512) return 0;
    for(i=0;i<count;i++) if(!isfinite(values[i]) || fabs(values[i])>16384) return 0;
    for(i=0;i<count;i++) hm_sum_add(&sum,values[i]);
    *out=hm_mean(&sum,count);
    return 1;
}

#include "hanger_core.h"
#include <math.h>

he_status hc_energy_protocol(const hm_protocol *p, he_protocol *out)
{
    he_protocol value;
    if (p == NULL || out == NULL) return HE_ERR_ARGUMENT;
    if (!hm_protocol_valid(p)) return HE_ERR_CONFIG;
    if (p->baseline_s > UINT32_MAX || p->flush_s > UINT32_MAX || p->recovery_s > UINT32_MAX ||
        floor(p->baseline_s) != p->baseline_s || floor(p->flush_s) != p->flush_s ||
        floor(p->recovery_s) != p->recovery_s) return HE_ERR_RANGE;
    value.baseline_s = (uint32_t)p->baseline_s;
    value.flush_s = (uint32_t)p->flush_s;
    value.recovery_s = (uint32_t)p->recovery_s;
    *out = value;
    return HE_OK;
}

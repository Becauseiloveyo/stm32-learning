/* Native ABI bridge only. Every decision is made by hanger_energy.c. */
#include "../core/hanger_energy.h"

#if defined(_WIN32)
#define EXPORT __declspec(dllexport)
#else
#define EXPORT __attribute__((visibility("default")))
#endif

EXPORT size_t energy_layout(size_t *sizes, size_t capacity)
{
    const size_t layout[] = {
        sizeof(he_config), sizeof(he_protocol), sizeof(he_budget_result),
        sizeof(he_balance), sizeof(he_power_state), sizeof(he_power_result),
        sizeof(he_quota), sizeof(he_wifi_result), sizeof(he_admission),
        sizeof(he_admission_result), sizeof(he_retry_result),
        sizeof(double), sizeof(uint64_t), sizeof(he_status)
    };
    size_t i, count = sizeof(layout) / sizeof(layout[0]);
    if (sizes != NULL && capacity >= count) {
        for (i = 0; i < count; ++i) sizes[i] = layout[i];
    }
    return count;
}
EXPORT he_status energy_config(const he_config *c) { return he_config_validate(c); }
EXPORT he_status energy_protocol(const he_protocol *p) { return he_protocol_validate(p); }
EXPORT he_status energy_add(uint64_t t, uint64_t d, uint64_t *o) { return he_time_add(t, d, o); }
EXPORT he_status energy_budget(const he_config *c, const he_protocol *p, he_kind k, he_budget_result *o)
{ return he_budget(c, p, k, o); }
EXPORT he_status energy_account(const he_config *c, he_step_kind k, double b, const double *r, double s, he_balance *o)
{ return he_account(c, k, b, r, s, o); }
EXPORT he_status energy_power(const he_config *c, const he_power_state *s, uint8_t slot, he_power_result *o)
{ return he_power_update(c, s, slot, o); }
EXPORT he_status energy_quota_init(he_quota *q, uint64_t t, double a, double b)
{ return he_quota_init(q, t, a, b); }
EXPORT he_status energy_quota_advance(he_quota *q, uint64_t t) { return he_quota_advance(q, t); }
EXPORT he_status energy_wifi_check(const he_config *c, he_quota *q, uint64_t t, he_wifi_result *o)
{ return he_wifi_check(c, q, t, o); }
EXPORT he_status energy_wifi_charge(const he_config *c, he_quota *q, uint64_t t, uint64_t d, he_wifi_result *o)
{ return he_wifi_charge(c, q, t, d, o); }
EXPORT he_status energy_admit(const he_config *c, const he_protocol *p, const he_admission *a, he_quota *q, he_admission_result *o)
{ return he_admit(c, p, a, q, o); }
EXPORT he_status energy_retry(const he_config *c, uint32_t a, uint64_t t, he_retry_result *o)
{ return he_retry(c, a, t, o); }
EXPORT const char *energy_reason(he_reason r) { return he_reason_name(r); }

#ifndef HANGER_CORE_H
#define HANGER_CORE_H

#include "hanger_measurement.h"
#include "hanger_state.h"
#include "hanger_energy.h"

/* Explicit bounded host-validated profile, not a hardware firmware version. */
#define HC_PROFILE "T06-C99-BOUNDED-1"

/* Derive energy timing from the exact validated measurement configuration.
 * Never cast fractional phase seconds to integers or silently use defaults.
 * Call on every configuration change, before enabling action admission.
 * Error leaves result untouched. This does not validate a power source or
 * associate independent caller event clocks/identities. */
he_status hc_energy_protocol(const hm_protocol *measurement, he_protocol *result);

#endif

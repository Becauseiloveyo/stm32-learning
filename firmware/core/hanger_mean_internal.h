/* Exact sum and correctly rounded mean of <=512 bounded binary64 deltas.
 * Store separate positive/negative unsigned integers in units of 2^-1074;
 * 36 limbs cover the bound sum <512*2^14 Pa. No dependence on x87 precision control,
 * extended floating point or order-sensitive summation. Fixed local storage. */
#ifndef HANGER_MEAN_INTERNAL_H
#define HANGER_MEAN_INTERNAL_H
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <math.h>
#include <float.h>
#if DBL_MANT_DIG != 53 || DBL_MAX_EXP != 1024 || FLT_RADIX != 2
#error "hanger measurement requires IEEE 754 binary64 double"
#endif
#define HM_LIMBS 36U
typedef char hm_requires_eight_byte_double[(sizeof(double)==sizeof(uint64_t))?1:-1];
typedef struct { uint32_t positive[HM_LIMBS], negative[HM_LIMBS]; } hm_sum;
static void hm_sum_add(hm_sum *s,double v) {
    uint64_t bits,mantissa,carry; unsigned exponent,shift,index,offset; uint32_t *a;
    memcpy(&bits,&v,sizeof(bits)); a=(bits>>63)?s->negative:s->positive;
    exponent=(unsigned)((bits>>52)&2047U); mantissa=bits&UINT64_C(0xfffffffffffff);
    if(exponent) mantissa|=UINT64_C(0x10000000000000);
    shift=exponent?exponent-1:0; index=shift/32; offset=shift%32;
    /* Insert the two mantissa words separately, propagating unsigned carry. */
    carry=(mantissa&UINT64_C(0xffffffff))<<offset;
    while(carry) { carry+=(uint64_t)a[index]; a[index++]=(uint32_t)carry; carry>>=32; }
    index=shift/32+1; carry=(mantissa>>32)<<offset;
    while(carry) { carry+=(uint64_t)a[index]; a[index++]=(uint32_t)carry; carry>>=32; }
}
static int hm_bit(const uint32_t *a,unsigned bit) { return (int)((a[bit/32]>>(bit%32))&1U); }
static double hm_mean(const hm_sum *s,size_t count) {
    uint32_t q[HM_LIMBS]; const uint32_t *larger=s->positive,*smaller=s->negative;
    uint64_t borrow=0,remain=0,value,mantissa=0; int sign=1,highest=-1; unsigned i,j,shift,guard,sticky=0;
    for(i=HM_LIMBS;i>0;i--) if(s->positive[i-1]!=s->negative[i-1]) {
        if(s->positive[i-1]<s->negative[i-1]) { larger=s->negative; smaller=s->positive; sign=-1; }
        break;
    }
    for(i=0;i<HM_LIMBS;i++) {
        uint64_t sub=(uint64_t)smaller[i]+borrow;
        q[i]=(uint32_t)((uint64_t)larger[i]-sub); borrow=(uint64_t)larger[i]<sub;
    }
    for(i=HM_LIMBS;i>0;i--) {
        value=(remain<<32)|q[i-1]; q[i-1]=(uint32_t)(value/count); remain=value%count;
    }
    for(i=HM_LIMBS;i>0;i--) if(q[i-1]) {
        for(j=32;j>0;j--) if((q[i-1]>>(j-1))&1U) { highest=(int)((i-1)*32+j-1); break; }
        break;
    }
    shift=highest>52?(unsigned)(highest-52):0;
    for(i=0;i<53;i++) if(hm_bit(q,shift+i)) mantissa|=UINT64_C(1)<<i;
    if(shift) {
        guard=(unsigned)hm_bit(q,shift-1); sticky=remain!=0;
        for(i=0;i<shift-1;i++) if(hm_bit(q,i)) { sticky=1; break; }
        if(guard && (sticky || (mantissa&1U))) mantissa++;
    } else if(2*remain>count || (2*remain==count && (mantissa&1U))) mantissa++;
    return sign*ldexp((double)mantissa,(int)shift-1074);
}
#endif

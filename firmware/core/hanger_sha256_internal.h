/* Internal streaming SHA-256, FIPS 180-4 equations. Receipt integrity only;
 * not authentication. Local numeric byte encoding is not a portable wire hash. */
#ifndef HANGER_SHA256_INTERNAL_H
#define HANGER_SHA256_INTERNAL_H
#include <stdint.h>
#include <stddef.h>
#include <string.h>
typedef struct { uint32_t h[8]; uint64_t bytes; size_t used; uint8_t block[64]; } hs_sha;
static uint32_t hs_rotr(uint32_t x, unsigned n) { return (x>>n)|(x<<(32U-n)); }
static void hs_sha_block(hs_sha *s) {
    static const uint32_t k[64]={
        0x428a2f98U,0x71374491U,0xb5c0fbcfU,0xe9b5dba5U,0x3956c25bU,0x59f111f1U,0x923f82a4U,0xab1c5ed5U,
        0xd807aa98U,0x12835b01U,0x243185beU,0x550c7dc3U,0x72be5d74U,0x80deb1feU,0x9bdc06a7U,0xc19bf174U,
        0xe49b69c1U,0xefbe4786U,0x0fc19dc6U,0x240ca1ccU,0x2de92c6fU,0x4a7484aaU,0x5cb0a9dcU,0x76f988daU,
        0x983e5152U,0xa831c66dU,0xb00327c8U,0xbf597fc7U,0xc6e00bf3U,0xd5a79147U,0x06ca6351U,0x14292967U,
        0x27b70a85U,0x2e1b2138U,0x4d2c6dfcU,0x53380d13U,0x650a7354U,0x766a0abbU,0x81c2c92eU,0x92722c85U,
        0xa2bfe8a1U,0xa81a664bU,0xc24b8b70U,0xc76c51a3U,0xd192e819U,0xd6990624U,0xf40e3585U,0x106aa070U,
        0x19a4c116U,0x1e376c08U,0x2748774cU,0x34b0bcb5U,0x391c0cb3U,0x4ed8aa4aU,0x5b9cca4fU,0x682e6ff3U,
        0x748f82eeU,0x78a5636fU,0x84c87814U,0x8cc70208U,0x90befffaU,0xa4506cebU,0xbef9a3f7U,0xc67178f2U};
    uint32_t w[64],a,b,c,d,e,f,g,h,t1,t2; size_t i;
    for(i=0;i<16;i++) w[i]=((uint32_t)s->block[4*i]<<24)|((uint32_t)s->block[4*i+1]<<16)|
        ((uint32_t)s->block[4*i+2]<<8)|s->block[4*i+3];
    for(i=16;i<64;i++) w[i]=w[i-16]+(hs_rotr(w[i-15],7)^hs_rotr(w[i-15],18)^(w[i-15]>>3))+
        w[i-7]+(hs_rotr(w[i-2],17)^hs_rotr(w[i-2],19)^(w[i-2]>>10));
    a=s->h[0]; b=s->h[1]; c=s->h[2]; d=s->h[3]; e=s->h[4]; f=s->h[5]; g=s->h[6]; h=s->h[7];
    for(i=0;i<64;i++) {
        t1=h+(hs_rotr(e,6)^hs_rotr(e,11)^hs_rotr(e,25))+((e&f)^((~e)&g))+k[i]+w[i];
        t2=(hs_rotr(a,2)^hs_rotr(a,13)^hs_rotr(a,22))+((a&b)^(a&c)^(b&c));
        h=g; g=f; f=e; e=d+t1; d=c; c=b; b=a; a=t1+t2;
    }
    s->h[0]+=a; s->h[1]+=b; s->h[2]+=c; s->h[3]+=d; s->h[4]+=e; s->h[5]+=f; s->h[6]+=g; s->h[7]+=h;
}
static void hs_sha_init(hs_sha *s) {
    static const uint32_t initial[8]={0x6a09e667U,0xbb67ae85U,0x3c6ef372U,0xa54ff53aU,0x510e527fU,0x9b05688cU,0x1f83d9abU,0x5be0cd19U};
    memcpy(s->h,initial,sizeof(initial)); s->bytes=0; s->used=0;
}
static void hs_sha_add(hs_sha *s, const void *data, size_t n) {
    const uint8_t *p=(const uint8_t *)data; size_t take;
    s->bytes+=(uint64_t)n;
    while(n) { take=64-s->used; if(take>n) take=n;
        memcpy(s->block+s->used,p,take); s->used+=take; p+=take; n-=take;
        if(s->used==64) { hs_sha_block(s); s->used=0; }
    }
}
static void hs_sha_end(hs_sha *s, uint8_t out[32]) {
    uint64_t bits=s->bytes*8U; uint8_t one=0x80,zero=0,tail[8]; size_t i;
    hs_sha_add(s,&one,1);
    while(s->used!=56) hs_sha_add(s,&zero,1);
    for(i=0;i<8;i++) tail[7-i]=(uint8_t)(bits>>(8*i));
    hs_sha_add(s,tail,8);
    for(i=0;i<32;i++) out[i]=(uint8_t)(s->h[i/4]>>(24-8*(i%4)));
}
#endif

#ifndef EID_CRYPTO_H
#define EID_CRYPTO_H

#include <stdint.h>
#include <stddef.h>

#define EID_LEN       20
#define EIK_LEN       32
#define AES_BLOCK_LEN 32

/**
 * Generate FMDN EID from identity key and timestamp.
 *
 * Algorithm (matches GoogleFindMyTools eid_generator.py):
 * 1. Mask timestamp (zero lower K=10 bits)
 * 2. Build 32-byte data block
 * 3. AES-256-ECB encrypt -> 32-byte r'
 * 4. r = r' mod SECP160R1.order
 * 5. R = r * G (point multiply)
 * 6. EID = R.x (20 bytes, big-endian)
 *
 * @param eik        Identity key (32 bytes)
 * @param timestamp  Unix timestamp (will be masked to 1024s boundary)
 * @param eid_out    Output buffer (20 bytes)
 * @return 0 on success, -1 on error
 */
int generate_eid(const uint8_t eik[EIK_LEN],
                 uint32_t timestamp,
                 uint8_t eid_out[EID_LEN]);

#endif

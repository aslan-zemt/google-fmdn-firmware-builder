#ifndef GATT_SERVICE_H
#define GATT_SERVICE_H

#include <stdint.h>

#define TRACKER_SERIAL_LEN 16
#define GATT_WINDOW_SEC    60  /* Connectable window after boot */

/**
 * Initialize the activation GATT service.
 *
 * Registers characteristics:
 * - tracker_serial (read, 16 bytes)
 * - current_eid    (read, 20 bytes)
 * - boot_timestamp (read, 4 bytes, big-endian)
 *
 * @param serial      Tracker serial number (16 bytes)
 * @param eid         Current EID slot 0 (20 bytes)
 * @param boot_ts     Boot timestamp (seconds since epoch)
 */
void gatt_activation_init(const uint8_t serial[TRACKER_SERIAL_LEN],
                          const uint8_t *eid,
                          uint32_t boot_ts);

#endif

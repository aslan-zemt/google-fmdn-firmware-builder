/*
 * GATT Activation Service
 *
 * Exposes tracker identity data during the connectable window (first 60s).
 * Mobile app reads these characteristics to activate the tracker on the backend.
 *
 * Service UUID: 0xFEAB (custom, avoids collision with 0xFEAA FMDN)
 * Characteristics:
 *   - tracker_serial  (0x2A00-ish) : 16 bytes, read-only
 *   - current_eid     (0x2A01-ish) : 20 bytes, read-only
 *   - boot_timestamp  (0x2A02-ish) : 4 bytes big-endian, read-only
 */

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/uuid.h>
#include <zephyr/logging/log.h>
#include <string.h>

#include "gatt_service.h"
#include "eid_crypto.h"

LOG_MODULE_REGISTER(gatt_act, CONFIG_LOG_DEFAULT_LEVEL);

/* Activation service UUID: 0xFEAB */
#define BT_UUID_ACT_SVC    BT_UUID_DECLARE_16(0xFEAB)
#define BT_UUID_ACT_SERIAL BT_UUID_DECLARE_16(0x2B00)
#define BT_UUID_ACT_EID    BT_UUID_DECLARE_16(0x2B01)
#define BT_UUID_ACT_BOOT   BT_UUID_DECLARE_16(0x2B02)

static uint8_t gatt_serial[TRACKER_SERIAL_LEN];
static uint8_t gatt_eid[EID_LEN];
static uint8_t gatt_boot_ts[4];  /* Big-endian */

static ssize_t read_serial(struct bt_conn *conn,
                           const struct bt_gatt_attr *attr,
                           void *buf, uint16_t len, uint16_t offset)
{
    return bt_gatt_attr_read(conn, attr, buf, len, offset,
                             gatt_serial, TRACKER_SERIAL_LEN);
}

static ssize_t read_eid(struct bt_conn *conn,
                        const struct bt_gatt_attr *attr,
                        void *buf, uint16_t len, uint16_t offset)
{
    return bt_gatt_attr_read(conn, attr, buf, len, offset,
                             gatt_eid, EID_LEN);
}

static ssize_t read_boot_ts(struct bt_conn *conn,
                            const struct bt_gatt_attr *attr,
                            void *buf, uint16_t len, uint16_t offset)
{
    return bt_gatt_attr_read(conn, attr, buf, len, offset,
                             gatt_boot_ts, sizeof(gatt_boot_ts));
}

/* GATT service definition */
BT_GATT_SERVICE_DEFINE(act_svc,
    BT_GATT_PRIMARY_SERVICE(BT_UUID_ACT_SVC),
    BT_GATT_CHARACTERISTIC(BT_UUID_ACT_SERIAL,
                           BT_GATT_CHRC_READ,
                           BT_GATT_PERM_READ,
                           read_serial, NULL, NULL),
    BT_GATT_CHARACTERISTIC(BT_UUID_ACT_EID,
                           BT_GATT_CHRC_READ,
                           BT_GATT_PERM_READ,
                           read_eid, NULL, NULL),
    BT_GATT_CHARACTERISTIC(BT_UUID_ACT_BOOT,
                           BT_GATT_CHRC_READ,
                           BT_GATT_PERM_READ,
                           read_boot_ts, NULL, NULL),
);

void gatt_activation_init(const uint8_t serial[TRACKER_SERIAL_LEN],
                          const uint8_t *eid,
                          uint32_t boot_ts)
{
    memcpy(gatt_serial, serial, TRACKER_SERIAL_LEN);
    memcpy(gatt_eid, eid, EID_LEN);

    gatt_boot_ts[0] = (boot_ts >> 24) & 0xFF;
    gatt_boot_ts[1] = (boot_ts >> 16) & 0xFF;
    gatt_boot_ts[2] = (boot_ts >>  8) & 0xFF;
    gatt_boot_ts[3] = (boot_ts      ) & 0xFF;

    LOG_INF("GATT activation service initialized (serial: %02x%02x...%02x%02x)",
            gatt_serial[0], gatt_serial[1],
            gatt_serial[14], gatt_serial[15]);
}

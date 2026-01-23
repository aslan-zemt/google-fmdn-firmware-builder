/*
 * Google FMDN Tracker - Ultra Low Power
 *
 * Features:
 * - Static EIDs (time=0)
 * - Entity rotation every 15 min (UTP evasion)
 * - TX power +4 dBm (balanced range/power)
 * - Low power advertising (1-2 sec interval)
 * - DC/DC regulator enabled
 *
 * Supported: nRF52840, nRF52832
 */

#include <zephyr/kernel.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <string.h>

#include "entity_pool.h"

/* Advertising interval: 5000ms (power efficient) */
#define ADV_INTERVAL_MIN  8000  /* 5000ms in 0.625ms units */
#define ADV_INTERVAL_MAX  8000  /* 5000ms in 0.625ms units */

/* FMDN Frame Constants */
#define FMDN_UUID_LOW   0xAA
#define FMDN_UUID_HIGH  0xFE
#define FMDN_FRAME_TYPE 0x41

/* Service Data Structure */
struct __attribute__((packed)) fmdn_service_data {
    uint8_t uuid_low;
    uint8_t uuid_high;
    uint8_t frame_type;
    uint8_t eid[20];
    uint8_t hashed_flags;
};

static struct fmdn_service_data fmdn_data = {
    .uuid_low = FMDN_UUID_LOW,
    .uuid_high = FMDN_UUID_HIGH,
    .frame_type = FMDN_FRAME_TYPE,
    .eid = {0},
    .hashed_flags = 0x80
};

static uint8_t current_entity_index = 0;

/* Advertising data */
static const struct bt_data ad[] = {
    BT_DATA_BYTES(BT_DATA_FLAGS, BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR),
    BT_DATA(BT_DATA_SVC_DATA16, &fmdn_data, sizeof(fmdn_data)),
};

/* RPA advertising parameters */
static struct bt_le_adv_param adv_param = BT_LE_ADV_PARAM_INIT(
    BT_LE_ADV_OPT_NONE,
    ADV_INTERVAL_MIN,
    ADV_INTERVAL_MAX,
    NULL
);

/* Load entity EID */
static void load_entity(uint8_t idx)
{
    if (idx >= ENTITY_POOL_SIZE) {
        idx = 0;
    }
    memcpy(fmdn_data.eid, eid_pool[idx], 20);
    fmdn_data.hashed_flags = hashed_flags_pool[idx];
    current_entity_index = idx;
}

/* Start advertising */
static int start_advertising(void)
{
    bt_le_adv_stop();
    return bt_le_adv_start(&adv_param, ad, ARRAY_SIZE(ad), NULL, 0);
}

/* Rotation work handler */
static void rotation_handler(struct k_work *work);
K_WORK_DELAYABLE_DEFINE(rotation_work, rotation_handler);

static void rotation_handler(struct k_work *work)
{
    uint8_t next = (current_entity_index + 1) % ENTITY_POOL_SIZE;

    /* Stop, switch entity, restart */
    bt_le_adv_stop();
    load_entity(next);
    start_advertising();

    /* Schedule next rotation */
    k_work_schedule(&rotation_work, K_SECONDS(ROTATION_PERIOD_SEC));
}

int main(void)
{
    /* Init Bluetooth */
    if (bt_enable(NULL)) {
        return 0;
    }

    /* Load first entity and start */
    load_entity(0);
    start_advertising();

    /* Schedule first rotation */
    k_work_schedule(&rotation_work, K_SECONDS(ROTATION_PERIOD_SEC));

    /* No main loop needed - Zephyr idle thread handles power management */
    return 0;
}

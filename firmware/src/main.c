/*
 * Google FMDN Tracker - Dynamic EID
 *
 * v3: On-device EID computation from single EIK.
 *
 * Boot sequence:
 * 1. Compute 20 EIDs from EIK (virtual timestamps 0, 1024, ..., 19*1024)
 * 2. First 60s: connectable mode + GATT activation service
 * 3. After 60s: non-connectable FMDN advertising
 * 4. Rotation every ROTATION_PERIOD_SEC: stop adv -> rotate MAC -> next EID -> start adv
 *
 * ADV interval: 2s (more packets in short window)
 * Crypto: tiny-aes-c (AES-256-ECB) + micro-ecc (SECP160R1)
 * Overhead: ~7 KB flash, ~200 B RAM. Boot EID computation: ~3-4 sec.
 */

#include <zephyr/kernel.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/sys/util.h>
#include <zephyr/logging/log.h>
#include <string.h>

#include "config.h"
#include "eid_crypto.h"
#include "gatt_service.h"

LOG_MODULE_REGISTER(fmdn, CONFIG_LOG_DEFAULT_LEVEL);

/* Advertising interval: 2000ms (was 5000ms — more packets per rotation window) */
#define ADV_INTERVAL_MIN  3200  /* 2000ms in 0.625ms units */
#define ADV_INTERVAL_MAX  3200

/* FMDN Frame Constants */
#define FMDN_UUID_LOW   0xAA
#define FMDN_UUID_HIGH  0xFE
#define FMDN_FRAME_TYPE 0x41

/* Retry config */
#define ADV_START_MAX_RETRIES 5
#define ADV_START_RETRY_DELAY_MS 50

/* EID pool — computed at boot from EIK */
static uint8_t eid_pool[SLOT_COUNT][EID_LEN];

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

static uint8_t current_slot = 0;

/* Advertising data */
static const struct bt_data ad[] = {
    BT_DATA_BYTES(BT_DATA_FLAGS, BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR),
    BT_DATA(BT_DATA_SVC_DATA16, &fmdn_data, sizeof(fmdn_data)),
};

/* Non-connectable advertising parameters */
static struct bt_le_adv_param adv_param_nonconn = BT_LE_ADV_PARAM_INIT(
    BT_LE_ADV_OPT_NONE,
    ADV_INTERVAL_MIN,
    ADV_INTERVAL_MAX,
    NULL
);

/* Connectable advertising parameters (for GATT activation window) */
static struct bt_le_adv_param adv_param_conn = BT_LE_ADV_PARAM_INIT(
    BT_LE_ADV_OPT_CONNECTABLE,
    ADV_INTERVAL_MIN,
    ADV_INTERVAL_MAX,
    NULL
);

static bool connectable_mode = true;

/* ---- EID Pool Computation ---- */

static int compute_eid_pool(void)
{
    LOG_INF("Computing %u EIDs from EIK...", SLOT_COUNT);

    for (uint8_t i = 0; i < SLOT_COUNT; i++) {
        /* Virtual timestamp: i * 1024 (each slot = different 1024s window) */
        uint32_t virt_ts = (uint32_t)i * 1024U;
        int rc = generate_eid(TRACKER_EIK, virt_ts, eid_pool[i]);
        if (rc != 0) {
            LOG_ERR("EID computation failed for slot %u", i);
            return -1;
        }
    }

    LOG_INF("EID pool computed. Slot 0: %02x%02x%02x%02x...",
            eid_pool[0][0], eid_pool[0][1],
            eid_pool[0][2], eid_pool[0][3]);
    return 0;
}

/* ---- Advertising ---- */

static void load_slot(uint8_t idx)
{
    if (idx >= SLOT_COUNT) {
        idx = 0;
    }
    memcpy(fmdn_data.eid, eid_pool[idx], EID_LEN);
    current_slot = idx;
    LOG_INF("Slot %u loaded, EID: %02x%02x%02x%02x...", idx,
            fmdn_data.eid[0], fmdn_data.eid[1],
            fmdn_data.eid[2], fmdn_data.eid[3]);
}

static int start_advertising(void)
{
    struct bt_le_adv_param *param = connectable_mode
        ? &adv_param_conn
        : &adv_param_nonconn;
    int err;

    for (int i = 0; i < ADV_START_MAX_RETRIES; i++) {
        k_msleep(ADV_START_RETRY_DELAY_MS);
        err = bt_le_adv_start(param, ad, ARRAY_SIZE(ad), NULL, 0);
        if (err == 0) {
            LOG_INF("Advertising started (%s, attempt %d)",
                    connectable_mode ? "connectable" : "non-connectable",
                    i + 1);
            return 0;
        }
        LOG_WRN("Adv start failed (attempt %d): err %d", i + 1, err);
        k_msleep(ADV_START_RETRY_DELAY_MS * (i + 1));
    }

    LOG_ERR("Advertising failed after %d retries: err %d",
            ADV_START_MAX_RETRIES, err);
    return err;
}

static void rotate_mac_address(void)
{
    int err = bt_id_reset(0, NULL, NULL);
    if (err) {
        LOG_WRN("MAC rotation failed: err %d", err);
    } else {
        LOG_INF("MAC rotated");
    }
}

/* ---- GATT Window Transition ---- */

static void gatt_window_handler(struct k_work *work);
K_WORK_DELAYABLE_DEFINE(gatt_window_work, gatt_window_handler);

static void gatt_window_handler(struct k_work *work)
{
    LOG_INF("=== GATT window closed, switching to non-connectable ===");

    bt_le_adv_stop();
    connectable_mode = false;
    rotate_mac_address();
    start_advertising();
}

/* ---- Slot Rotation ---- */

static void rotation_handler(struct k_work *work);
K_WORK_DELAYABLE_DEFINE(rotation_work, rotation_handler);

static void rotation_handler(struct k_work *work)
{
    uint8_t next = (current_slot + 1) % SLOT_COUNT;

    LOG_INF("=== Rotation %u -> %u ===", current_slot, next);

    bt_le_adv_stop();
    rotate_mac_address();
    load_slot(next);
    start_advertising();

    k_work_schedule(&rotation_work, K_SECONDS(ROTATION_PERIOD_SEC));
}

/* ---- Main ---- */

int main(void)
{
    LOG_INF("FMDN Tracker v3 (dynamic EID) starting...");
    LOG_INF("Slots: %u, rotation: %us, GATT window: %us",
            SLOT_COUNT, ROTATION_PERIOD_SEC, GATT_WINDOW_SEC);

    /* Step 1: Compute EID pool from EIK */
    if (compute_eid_pool() != 0) {
        LOG_ERR("Failed to compute EID pool — halting");
        return 0;
    }

    /* Step 2: Initialize Bluetooth */
    int err = bt_enable(NULL);
    if (err) {
        LOG_ERR("BT init failed: err %d", err);
        return 0;
    }
    LOG_INF("Bluetooth initialized");

    /* Step 3: Initialize GATT activation service */
    gatt_activation_init(TRACKER_SERIAL, eid_pool[0], BOOT_TIMESTAMP);

    /* Step 4: Start connectable advertising (GATT window) */
    connectable_mode = true;
    load_slot(0);
    start_advertising();

    /* Step 5: Schedule GATT window close */
    k_work_schedule(&gatt_window_work, K_SECONDS(GATT_WINDOW_SEC));
    LOG_INF("GATT connectable window: %u seconds", GATT_WINDOW_SEC);

    /* Step 6: Schedule first slot rotation */
    k_work_schedule(&rotation_work, K_SECONDS(ROTATION_PERIOD_SEC));
    LOG_INF("Running. First rotation in %us", ROTATION_PERIOD_SEC);

    return 0;
}

# SPDX-License-Identifier: Apache-2.0
# Google FMDN Tracker Firmware (v3 - Dynamic EID)

cmake_minimum_required(VERSION 3.20.0)

# Add custom board definitions
list(APPEND BOARD_ROOT ${CMAKE_CURRENT_SOURCE_DIR})

find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})

project(google_fmdn_tracker)

# Application sources
target_sources(app PRIVATE
    src/main.c
    src/eid_crypto.c
    src/gatt_service.c
)

# tiny-aes-c (AES-256-ECB, ~2 KB flash)
target_sources(app PRIVATE lib/tiny-aes/aes.c)
target_include_directories(app PRIVATE lib/tiny-aes)
target_compile_definitions(app PRIVATE AES256=1)

# micro-ecc (SECP160R1 point multiply, ~4-6 KB flash)
target_sources(app PRIVATE lib/micro-ecc/uECC.c)
target_include_directories(app PRIVATE lib/micro-ecc)
target_compile_definitions(app PRIVATE
    uECC_SUPPORTS_secp160r1=1
    uECC_SUPPORT_COMPRESSED_POINT=0
    uECC_OPTIMIZATION_LEVEL=2
)

target_include_directories(app PRIVATE
    include
    src
)

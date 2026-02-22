"""
Google FMDN Firmware Builder Service (v2 - Dynamic EID)

Builds nRF52 firmware using Zephyr RTOS for Google Find My Device Network.
v2: Generates config.h with EIK + serial instead of entity_pool.h with static EIDs.
The firmware computes EIDs on-device at boot.
"""

import asyncio
import os
import json
import secrets
import shutil
from pathlib import Path
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .eid_crypto import generate_eid, compute_hashed_flags

app = FastAPI(
    title="Google FMDN Firmware Builder",
    description="Builds Google Find My Device Network firmware for nRF52 trackers",
    version="2.0.0"
)

# Configuration
ZEPHYR_BASE = Path(os.environ.get("ZEPHYR_BASE", "/opt/zephyrproject/zephyr"))
ZEPHYR_PROJECT = ZEPHYR_BASE.parent
FIRMWARE_SRC = Path("/app/firmware")
OUTPUT_DIR = Path("/app/output")
BUILD_DIR = ZEPHYR_PROJECT / "build"

MAX_SLOTS = 20
EIK_SIZE = 32
SERIAL_SIZE = 16

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# --- Data Models ---

class EntityData(BaseModel):
    """Individual entity data (legacy, for backward compatibility)"""
    name: str
    eik: str  # Hex encoded 32-byte Entity Identity Key


class BuildRequest(BaseModel):
    """Request to build firmware"""
    tracker_id: str = Field(..., description="Unique tracker identifier")
    hardware: str = Field(default="nrf52840", description="Hardware: nrf52840 or nrf52832")
    # Legacy: entity list (one EIK per entity)
    entities: Optional[List[EntityData]] = Field(default=None, description="Legacy: entity list")
    # v2: Single EIK + serial (dynamic EID on device)
    eik: Optional[str] = Field(default=None, description="Single EIK (hex, 32 bytes)")
    tracker_serial: Optional[str] = Field(default=None, description="Tracker serial (hex, 16 bytes)")
    slot_count: int = Field(default=20, ge=1, le=MAX_SLOTS, description="Number of EID slots")
    rotation_period: int = Field(default=180, description="Rotation period in seconds")


class EIDSlotInfo(BaseModel):
    """Pre-computed EID info for one slot"""
    slot_index: int
    virtual_timestamp: int
    eid_hex: str


class BuildResponse(BaseModel):
    """Build response"""
    tracker_id: str
    hardware: str
    firmware_size: int
    entity_count: int
    rotation_period: int
    build_date: str
    download_url: str
    # v2: pre-computed EIDs for backend registration
    tracker_serial: Optional[str] = None
    eik: Optional[str] = None
    eid_slots: List[EIDSlotInfo] = []


class HealthResponse(BaseModel):
    """Health check response"""
    status: str
    service: str
    version: str
    zephyr_available: bool


def get_board_name(hardware: str) -> str:
    """Get Zephyr board name"""
    boards = {
        "nrf52840": "nrf52840dk/nrf52840",
        "nrf52832": "nrf52dk/nrf52832",
    }
    if hardware not in boards:
        raise ValueError(f"Unsupported hardware: {hardware}")
    return boards[hardware]


def generate_config_h(eik_hex: str, serial_hex: str,
                      slot_count: int, rotation_period: int) -> str:
    """Generate config.h with EIK, serial, and firmware constants."""
    eik_bytes = bytes.fromhex(eik_hex)
    serial_bytes = bytes.fromhex(serial_hex)

    eik_c = ', '.join([f'0x{b:02X}' for b in eik_bytes])
    serial_c = ', '.join([f'0x{b:02X}' for b in serial_bytes])

    # Boot timestamp aligned to 1024s boundary
    import time
    boot_ts = int(time.time()) & ~0x3FF

    return f"""/*
 * Auto-generated Tracker Configuration
 * Generated: {datetime.utcnow().isoformat()}Z
 * Slots: {slot_count}, Rotation: {rotation_period}s
 */

#ifndef CONFIG_H
#define CONFIG_H

#include <stdint.h>

/* Tracker Identity Key (32 bytes) */
static const uint8_t TRACKER_EIK[32] = {{
    {eik_c}
}};

/* Tracker Serial (16 bytes) */
static const uint8_t TRACKER_SERIAL[16] = {{
    {serial_c}
}};

/* Boot timestamp (aligned to 1024s) */
#define BOOT_TIMESTAMP {boot_ts}U

/* Slot and rotation config */
#define SLOT_COUNT          {slot_count}U
#define ROTATION_PERIOD_SEC {rotation_period}U

#endif /* CONFIG_H */
"""


def generate_entity_pool_h(entities: List[EntityData], rotation_period: int) -> str:
    """Generate legacy entity_pool.h content from entities."""
    lines = [
        "/*",
        " * Auto-generated Entity Pool (legacy mode)",
        f" * Generated: {datetime.utcnow().isoformat()}Z",
        f" * Entities: {len(entities)}",
        f" * Rotation: {rotation_period}s",
        " */",
        "",
        "#ifndef ENTITY_POOL_H",
        "#define ENTITY_POOL_H",
        "",
        "#include <stdint.h>",
        "",
        f"#define ENTITY_POOL_SIZE {len(entities)}U",
        f"#define ROTATION_PERIOD_SEC {rotation_period}U",
        "",
        "/* Static EID pool - computed at time=0 for each EIK */",
        f"static const uint8_t eid_pool[ENTITY_POOL_SIZE][20] = {{",
    ]

    for i, entity in enumerate(entities):
        eik = bytes.fromhex(entity.eik)
        eid = generate_eid(eik, timestamp=0)
        eid_hex = ', '.join([f"0x{b:02X}" for b in eid])
        lines.append(f"    /* Entity {i}: {entity.name} */")
        lines.append(f"    {{ {eid_hex} }},")
        lines.append("")

    lines.append("};")
    lines.append("")
    lines.append("/* Hashed flags (0x80 for UTP mode) */")
    lines.append(f"static const uint8_t hashed_flags_pool[ENTITY_POOL_SIZE] = {{")

    flags = []
    for entity in entities:
        eik = bytes.fromhex(entity.eik)
        flag = compute_hashed_flags(eik)
        flags.append(f"0x{flag:02X}")

    for i in range(0, len(flags), 10):
        chunk = flags[i:i+10]
        lines.append(f"    {', '.join(chunk)},")

    lines.append("};")
    lines.append("")
    lines.append("#endif /* ENTITY_POOL_H */")

    return '\n'.join(lines)


def precompute_eids(eik_hex: str, slot_count: int) -> List[EIDSlotInfo]:
    """Pre-compute EIDs for all slots (for backend registration)."""
    eik = bytes.fromhex(eik_hex)
    slots = []
    for i in range(slot_count):
        virt_ts = i * 1024
        eid = generate_eid(eik, timestamp=virt_ts)
        slots.append(EIDSlotInfo(
            slot_index=i,
            virtual_timestamp=virt_ts,
            eid_hex=eid.hex(),
        ))
    return slots


async def run_west_build(board: str, firmware_src: Path) -> tuple[bool, str]:
    """Run west build using subprocess exec (no shell)."""
    cmd = ["west", "build", "-p", "always", "-b", board, str(firmware_src)]

    env = os.environ.copy()
    env["ZEPHYR_BASE"] = str(ZEPHYR_BASE)

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(ZEPHYR_PROJECT),
        env=env
    )

    stdout, stderr = await process.communicate()
    output = stdout.decode() + stderr.decode()

    return process.returncode == 0, output


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Check service health"""
    zephyr_ok = ZEPHYR_BASE.exists() and (ZEPHYR_BASE / "VERSION").exists()

    return HealthResponse(
        status="healthy" if zephyr_ok else "degraded",
        service="Google FMDN Firmware Builder",
        version="2.0.0",
        zephyr_available=zephyr_ok
    )


@app.post("/build", response_model=BuildResponse)
async def build_firmware(request: BuildRequest):
    """Build firmware with provided entities or single EIK."""

    use_dynamic = request.eik is not None
    eid_slots: List[EIDSlotInfo] = []
    tracker_serial = request.tracker_serial

    if use_dynamic:
        # v2 mode: single EIK, dynamic EID on device
        if len(request.eik) != EIK_SIZE * 2:
            raise HTTPException(status_code=400, detail="EIK must be 32 bytes (64 hex chars)")

        if not tracker_serial:
            tracker_serial = secrets.token_hex(SERIAL_SIZE)

        if len(tracker_serial) != SERIAL_SIZE * 2:
            raise HTTPException(status_code=400, detail="Serial must be 16 bytes (32 hex chars)")

        # Generate config.h
        content = generate_config_h(
            request.eik, tracker_serial,
            request.slot_count, request.rotation_period,
        )
        config_path = FIRMWARE_SRC / "src" / "config.h"
        with open(config_path, 'w') as f:
            f.write(content)

        # Pre-compute EIDs for backend registration
        eid_slots = precompute_eids(request.eik, request.slot_count)

        entity_count = request.slot_count

    elif request.entities:
        # Legacy mode: multiple entities with static EIDs
        if len(request.entities) > MAX_SLOTS:
            raise HTTPException(status_code=400, detail=f"Max {MAX_SLOTS} entities")

        for entity in request.entities:
            if len(entity.eik) != EIK_SIZE * 2:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid EIK for {entity.name}"
                )

        content = generate_entity_pool_h(request.entities, request.rotation_period)
        pool_path = FIRMWARE_SRC / "include" / "entity_pool.h"
        pool_path.parent.mkdir(parents=True, exist_ok=True)
        with open(pool_path, 'w') as f:
            f.write(content)

        entity_count = len(request.entities)
    else:
        raise HTTPException(status_code=400, detail="Provide either 'eik' or 'entities'")

    try:
        board = get_board_name(request.hardware)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Build
    success, output = await run_west_build(board, FIRMWARE_SRC)

    if not success:
        raise HTTPException(status_code=500, detail=f"Build failed:\n{output[-2000:]}")

    hex_file = BUILD_DIR / "zephyr" / "zephyr.hex"
    bin_file = BUILD_DIR / "zephyr" / "zephyr.bin"

    if not hex_file.exists():
        raise HTTPException(status_code=500, detail="HEX not found after build")

    # Copy output
    tracker_dir = OUTPUT_DIR / request.tracker_id
    tracker_dir.mkdir(parents=True, exist_ok=True)

    out_hex = tracker_dir / f"{request.tracker_id}_fmdn.hex"
    out_bin = tracker_dir / f"{request.tracker_id}_fmdn.bin"

    shutil.copy(hex_file, out_hex)
    if bin_file.exists():
        shutil.copy(bin_file, out_bin)

    firmware_size = out_hex.stat().st_size

    # Save metadata
    build_date = datetime.utcnow().isoformat() + "Z"

    if use_dynamic:
        entities_data = {
            "tracker_id": request.tracker_id,
            "hardware": request.hardware,
            "mode": "dynamic_eid",
            "eik": request.eik,
            "tracker_serial": tracker_serial,
            "slot_count": request.slot_count,
            "rotation_period": request.rotation_period,
            "eid_slots": [s.model_dump() for s in eid_slots],
        }
    else:
        entities_data = {
            "tracker_id": request.tracker_id,
            "hardware": request.hardware,
            "mode": "static_pool",
            "entity_count": entity_count,
            "rotation_period": request.rotation_period,
            "entities": [
                {"name": e.name, "eik": e.eik, "eid_time0": generate_eid(bytes.fromhex(e.eik), 0).hex()}
                for e in request.entities
            ]
        }

    with open(tracker_dir / "entities.json", 'w') as f:
        json.dump(entities_data, f, indent=2)

    build_info = {
        "tracker_id": request.tracker_id,
        "hardware": request.hardware,
        "firmware_type": "google-fmdn",
        "version": "2.0.0",
        "build_date": build_date,
        "entity_count": entity_count,
        "rotation_period": request.rotation_period,
        "firmware_size": firmware_size,
        "mode": "dynamic_eid" if use_dynamic else "static_pool",
    }
    with open(tracker_dir / "firmware_info.json", 'w') as f:
        json.dump(build_info, f, indent=2)

    return BuildResponse(
        tracker_id=request.tracker_id,
        hardware=request.hardware,
        firmware_size=firmware_size,
        entity_count=entity_count,
        rotation_period=request.rotation_period,
        build_date=build_date,
        download_url=f"/download/{request.tracker_id}/firmware.hex",
        tracker_serial=tracker_serial,
        eik=request.eik if use_dynamic else None,
        eid_slots=eid_slots,
    )


@app.get("/download/{tracker_id}/firmware.hex")
async def download_hex(tracker_id: str):
    hex_path = OUTPUT_DIR / tracker_id / f"{tracker_id}_fmdn.hex"
    if not hex_path.exists():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(hex_path, filename=hex_path.name)


@app.get("/download/{tracker_id}/firmware.bin")
async def download_bin(tracker_id: str):
    bin_path = OUTPUT_DIR / tracker_id / f"{tracker_id}_fmdn.bin"
    if not bin_path.exists():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(bin_path, filename=bin_path.name)


@app.get("/download/{tracker_id}/entities.json")
async def download_entities(tracker_id: str):
    path = OUTPUT_DIR / tracker_id / "entities.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path, filename="entities.json")


@app.get("/builds")
async def list_builds():
    builds = []
    for d in OUTPUT_DIR.iterdir():
        if d.is_dir():
            info = d / "firmware_info.json"
            if info.exists():
                with open(info) as f:
                    builds.append(json.load(f))
    return {"builds": builds}


@app.delete("/builds/{tracker_id}")
async def delete_build(tracker_id: str):
    d = OUTPUT_DIR / tracker_id
    if not d.exists():
        raise HTTPException(status_code=404, detail="Not found")
    shutil.rmtree(d)
    return {"status": "deleted", "tracker_id": tracker_id}

"""
Google FMDN Firmware Builder Service
Builds nRF52 firmware using Zephyr RTOS for Google Find My Device Network
"""

import asyncio
import os
import json
import shutil
from pathlib import Path
from datetime import datetime
from typing import List

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .eid_crypto import generate_eid, compute_hashed_flags

app = FastAPI(
    title="Google FMDN Firmware Builder",
    description="Builds Google Find My Device Network firmware for nRF52 trackers",
    version="1.0.0"
)

# Configuration
ZEPHYR_BASE = Path(os.environ.get("ZEPHYR_BASE", "/opt/zephyrproject/zephyr"))
ZEPHYR_PROJECT = ZEPHYR_BASE.parent
FIRMWARE_SRC = Path("/app/firmware")
OUTPUT_DIR = Path("/app/output")
BUILD_DIR = ZEPHYR_PROJECT / "build"

MAX_ENTITIES = 20
EIK_SIZE = 32

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


class EntityData(BaseModel):
    """Individual entity data"""
    name: str
    eik: str  # Hex encoded 32-byte Entity Identity Key


class BuildRequest(BaseModel):
    """Request to build firmware"""
    tracker_id: str = Field(..., description="Unique tracker identifier")
    hardware: str = Field(default="nrf52840", description="Hardware: nrf52840 or nrf52832")
    entities: List[EntityData] = Field(..., description="List of entities with EIKs")
    rotation_period: int = Field(default=900, description="Rotation period in seconds")


class BuildResponse(BaseModel):
    """Build response"""
    tracker_id: str
    hardware: str
    firmware_size: int
    entity_count: int
    rotation_period: int
    build_date: str
    download_url: str


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


def generate_entity_pool_h(entities: List[EntityData], rotation_period: int) -> str:
    """Generate entity_pool.h content from entities"""
    lines = [
        "/*",
        " * Auto-generated Entity Pool",
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


async def run_west_build(board: str, firmware_src: Path) -> tuple[bool, str]:
    """Run west build - uses exec array form (safe, no shell injection)"""
    cmd = ["west", "build", "-p", "always", "-b", board, str(firmware_src)]

    env = os.environ.copy()
    env["ZEPHYR_BASE"] = str(ZEPHYR_BASE)

    # Using create_subprocess_exec (not shell) - safe from injection
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
        version="1.0.0",
        zephyr_available=zephyr_ok
    )


@app.post("/build", response_model=BuildResponse)
async def build_firmware(request: BuildRequest):
    """Build firmware with provided entities"""

    if not request.entities:
        raise HTTPException(status_code=400, detail="No entities provided")
    if len(request.entities) > MAX_ENTITIES:
        raise HTTPException(status_code=400, detail=f"Max {MAX_ENTITIES} entities")

    for entity in request.entities:
        if len(entity.eik) != EIK_SIZE * 2:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid EIK for {entity.name}"
            )

    try:
        board = get_board_name(request.hardware)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Generate entity_pool.h
    content = generate_entity_pool_h(request.entities, request.rotation_period)
    pool_path = FIRMWARE_SRC / "include" / "entity_pool.h"
    pool_path.parent.mkdir(parents=True, exist_ok=True)
    with open(pool_path, 'w') as f:
        f.write(content)

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
    entities_data = {
        "tracker_id": request.tracker_id,
        "hardware": request.hardware,
        "entity_count": len(request.entities),
        "rotation_period": request.rotation_period,
        "entities": [
            {"name": e.name, "eik": e.eik, "eid_time0": generate_eid(bytes.fromhex(e.eik), 0).hex()}
            for e in request.entities
        ]
    }
    with open(tracker_dir / "entities.json", 'w') as f:
        json.dump(entities_data, f, indent=2)

    build_date = datetime.utcnow().isoformat() + "Z"
    build_info = {
        "tracker_id": request.tracker_id,
        "hardware": request.hardware,
        "firmware_type": "google-fmdn",
        "version": "1.0.0",
        "build_date": build_date,
        "entity_count": len(request.entities),
        "rotation_period": request.rotation_period,
        "firmware_size": firmware_size
    }
    with open(tracker_dir / "firmware_info.json", 'w') as f:
        json.dump(build_info, f, indent=2)

    return BuildResponse(
        tracker_id=request.tracker_id,
        hardware=request.hardware,
        firmware_size=firmware_size,
        entity_count=len(request.entities),
        rotation_period=request.rotation_period,
        build_date=build_date,
        download_url=f"/download/{request.tracker_id}/firmware.hex"
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

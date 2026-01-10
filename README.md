# Google FMDN Firmware Builder

Сервис для сборки прошивок Google Find My Device Network (FMDN) трекеров.

## Архитектура

```
Backend (airtag-military)
    │
    ▼ POST /build {entities: [...], hardware: "nrf52840"}
┌─────────────────────────────┐
│  FMDN Firmware Builder      │
│  ─────────────────────────  │
│  1. Генерирует entity_pool.h│
│  2. Запускает west build    │
│  3. Возвращает .hex/.bin    │
└─────────────────────────────┘
    │
    ▼ GET /download/{id}/firmware.hex
Backend сохраняет прошивку
```

## API

### POST /build

```json
{
  "tracker_id": "tracker_001",
  "hardware": "nrf52840",
  "rotation_period": 900,
  "entities": [
    {"name": "entity-00", "eik": "0123456789abcdef..."},
    {"name": "entity-01", "eik": "fedcba9876543210..."}
  ]
}
```

**Response:**
```json
{
  "tracker_id": "tracker_001",
  "hardware": "nrf52840",
  "firmware_size": 145678,
  "entity_count": 2,
  "rotation_period": 900,
  "build_date": "2024-01-11T12:00:00Z",
  "download_url": "/download/tracker_001/firmware.hex"
}
```

### GET /download/{tracker_id}/firmware.hex

Скачать скомпилированную прошивку в формате Intel HEX.

### GET /download/{tracker_id}/firmware.bin

Скачать прошивку в бинарном формате.

### GET /download/{tracker_id}/entities.json

Скачать метаданные entities с вычисленными EID.

### GET /health

Проверка состояния сервиса.

## Поддерживаемое железо

- `nrf52840` - nRF52840 (HolyIoT YJ-18010, nRF52840-DK)
- `nrf52832` - nRF52832 (nRF52-DK)

## Локальный запуск

```bash
docker compose up --build
```

Сервис будет доступен на http://localhost:8081

## Интеграция с airtag-military

Добавить в `docker-compose.yml` бекенда:

```yaml
services:
  fmdn-firmware-builder:
    image: ghcr.io/aslan-zemt/google-fmdn-firmware-builder:latest
    container_name: fmdn-firmware-builder
    ports:
      - "8081:8081"
    volumes:
      - fmdn-output:/app/output
    networks:
      - airtag-network

  backend:
    environment:
      - FMDN_FIRMWARE_BUILDER_URL=http://fmdn-firmware-builder:8081
```

## Переменные окружения

| Variable | Default | Description |
|----------|---------|-------------|
| ZEPHYR_BASE | /opt/zephyrproject/zephyr | Путь к Zephyr |
| LOG_LEVEL | INFO | Уровень логирования |

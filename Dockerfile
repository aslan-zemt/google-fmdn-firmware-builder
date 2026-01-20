# Google FMDN Firmware Builder
# Builds nRF52 firmware with Zephyr RTOS for Google Find My Device Network
#
# This container includes full Zephyr SDK for on-demand firmware compilation

FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV ZEPHYR_BASE=/opt/zephyrproject/zephyr
ENV ZEPHYR_TOOLCHAIN_VARIANT=gnuarmemb
ENV GNUARMEMB_TOOLCHAIN_PATH=/opt/gcc-arm

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    cmake \
    ninja-build \
    gperf \
    ccache \
    dfu-util \
    device-tree-compiler \
    wget \
    curl \
    git \
    python3 \
    python3-dev \
    python3-pip \
    python3-venv \
    xz-utils \
    file \
    make \
    gcc \
    libsdl2-dev \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

# Install ARM GCC Toolchain
RUN wget -q https://developer.arm.com/-/media/Files/downloads/gnu-rm/10.3-2021.10/gcc-arm-none-eabi-10.3-2021.10-x86_64-linux.tar.bz2 \
    && tar -xf gcc-arm-none-eabi-10.3-2021.10-x86_64-linux.tar.bz2 -C /opt \
    && mv /opt/gcc-arm-none-eabi-10.3-2021.10 /opt/gcc-arm \
    && rm gcc-arm-none-eabi-10.3-2021.10-x86_64-linux.tar.bz2

ENV PATH="/opt/gcc-arm/bin:${PATH}"

# Install west and Python dependencies
RUN pip3 install --no-cache-dir \
    west \
    pyelftools \
    pycryptodome \
    ecdsa \
    fastapi \
    uvicorn \
    httpx \
    python-multipart

# Initialize Zephyr
WORKDIR /opt
RUN west init zephyrproject && \
    cd zephyrproject && \
    west update --narrow -o=--depth=1 && \
    west zephyr-export

# Install Zephyr requirements with legacy resolver to avoid ResolutionTooDeep error
# Pin problematic packages first
RUN pip3 install --no-cache-dir "ruamel.yaml>=0.17" "ruamel.yaml.clib>=0.2.7" && \
    pip3 install --no-cache-dir -r /opt/zephyrproject/zephyr/scripts/requirements.txt || \
    pip3 install --no-cache-dir --resolver=legacy -r /opt/zephyrproject/zephyr/scripts/requirements.txt

# Copy firmware source
WORKDIR /app
COPY firmware /app/firmware

# Copy application code
COPY app /app/app
COPY requirements.txt /app/

# Install additional Python deps
RUN pip3 install --no-cache-dir -r requirements.txt || true

# Create output directory
RUN mkdir -p /app/output

# Pre-build a test firmware to cache Zephyr objects (optional, speeds up first build)
# RUN cd /opt/zephyrproject && west build -p always -b nrf52840dk/nrf52840 /app/firmware -- -DOVERLAY_CONFIG=""

WORKDIR /app

EXPOSE 8081

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:8081/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8081"]

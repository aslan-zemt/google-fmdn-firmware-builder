"""
EID Crypto for Google FMDN
Generates Ephemeral Identifiers from Entity Identity Keys
"""

# pycryptodome can install as Crypto or Cryptodome depending on version
try:
    from Cryptodome.Cipher import AES
except ImportError:
    from Crypto.Cipher import AES

from ecdsa import SECP160r1

# Constants
K = 10  # Rotation exponent
ROTATION_PERIOD = 1024  # 2^K seconds


def generate_eid(identity_key: bytes, timestamp: int) -> bytes:
    """
    Generate EID from identity key and timestamp.

    Args:
        identity_key: 32-byte Entity Identity Key (EIK)
        timestamp: Unix timestamp (will be masked to rotation period)

    Returns:
        20-byte Ephemeral Identifier (EID)
    """
    # Calculate r
    r = calculate_r(identity_key, timestamp)

    # Compute R = r * G on SECP160r1
    curve = SECP160r1
    R = r * curve.generator

    # Return x coordinate as EID
    return R.x().to_bytes(20, 'big')


def calculate_r(identity_key: bytes, timestamp: int) -> int:
    """Calculate r value for EID generation"""
    # Mask timestamp to rotation period
    ts_bytes = get_masked_timestamp(timestamp, K)

    # Build data structure for AES encryption
    data = bytearray(32)
    data[0:11] = b'\xFF' * 11
    data[11] = K
    data[12:16] = ts_bytes
    data[16:27] = b'\x00' * 11
    data[27] = K
    data[28:32] = ts_bytes

    # AES-ECB-256 encryption
    cipher = AES.new(identity_key, AES.MODE_ECB)
    r_dash = cipher.encrypt(bytes(data))

    # Convert to integer
    r_dash_int = int.from_bytes(r_dash, byteorder='big', signed=False)

    # Project to finite field
    n = SECP160r1.order
    return r_dash_int % n


def get_masked_timestamp(timestamp: int, k: int) -> bytes:
    """Mask timestamp to rotation period boundary"""
    mask = ~((1 << k) - 1)
    timestamp &= mask
    return timestamp.to_bytes(4, byteorder='big')


def compute_hashed_flags(identity_key: bytes) -> int:
    """
    Compute hashed flags byte for UTP mode.
    For now, always returns 0x80 (UTP enabled).
    """
    # TODO: Implement actual hashed flags computation if needed
    return 0x80

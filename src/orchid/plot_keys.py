"""Plot ID generation, memo construction, and post-processing for pos2-chip plots.

Implements the same logic as chia-blockchain's create_plots.py:
- calculate_plot_id_v2: SHA256-based plot ID from keys and parameters
- memo: pool_key/puzzle_hash + farmer_pk + master_sk
- inject_memo: overwrite zero memo in .bin file after plotting
- finalize_plot: inject memo + rename .bin → .plot2
"""

import hashlib
import logging
import secrets
import struct
from pathlib import Path

log = logging.getLogger("orchid")

# pos2 .bin file header layout:
# 4 bytes: "pos2" magic
# 1 byte:  format version
# 32 bytes: plot_id
# 1 byte:  k
# 1 byte:  strength
# 2 bytes: index (uint16 LE)
# 1 byte:  meta_group
# 1 byte:  memo_length
# N bytes: memo data
HEADER_MEMO_OFFSET = 4 + 1 + 32 + 1 + 1 + 2 + 1  # = 42 (memo_length byte)


def std_hash(data: bytes) -> bytes:
    """SHA256 hash, same as chia's std_hash."""
    return hashlib.sha256(data).digest()


# ── Plot ID ────────────────────────────────────────────────────

def calculate_plot_id_v2(
    k: int,
    index: int,
    meta_group: int,
    pool_pk_or_ph: bytes,
    plot_public_key: bytes,
    strength: int,
) -> bytes:
    """Calculate plot ID for v2 plots.

    Mirrors chia.types.blockchain_format.proof_of_space.calculate_plot_id_v2:
        plot_group_id = sha256(k(1) + version(1) + strength(1) + plot_public_key + pool_pk_or_ph)
        plot_id = sha256(plot_group_id + index(2 big-endian) + meta_group(1))
    """
    version = 2
    plot_group_id = std_hash(
        k.to_bytes(1, "big")
        + version.to_bytes(1, "big")
        + strength.to_bytes(1, "big")
        + plot_public_key
        + pool_pk_or_ph
    )
    plot_id = std_hash(
        plot_group_id
        + index.to_bytes(2, "big")
        + meta_group.to_bytes(1, "big")
    )
    return plot_id


def generate_plot_id_testnet() -> str:
    """Random 32-byte plot ID for testnet (no keys needed)."""
    return secrets.token_hex(32)


# ── Key helpers ────────────────────────────────────────────────

def _try_import_blspy():
    """Try to import BLS library for key operations."""
    try:
        from blspy import AugSchemeMPL, G1Element, PrivateKey
        return AugSchemeMPL, G1Element, PrivateKey
    except ImportError:
        pass
    try:
        from chia_rs import AugSchemeMPL, G1Element, PrivateKey
        return AugSchemeMPL, G1Element, PrivateKey
    except ImportError:
        pass
    return None, None, None


def _master_sk_to_local_sk(master_sk):
    """Derive local secret key from master, same as chia's derive_keys."""
    # EIP-2333 derivation path: m/12381/8444/3/0
    from blspy import AugSchemeMPL
    return AugSchemeMPL.derive_child_sk(
        AugSchemeMPL.derive_child_sk(
            AugSchemeMPL.derive_child_sk(
                AugSchemeMPL.derive_child_sk(master_sk, 12381),
                8444,
            ),
            3,
        ),
        0,
    )


def _generate_plot_public_key(local_pk, farmer_pk, include_taproot: bool = False):
    """Generate plot public key from local + farmer keys.

    Without taproot: plot_pk = local_pk + farmer_pk
    With taproot: plot_pk = local_pk + farmer_pk + taproot_pk (for NFT plots)
    """
    if not include_taproot:
        return local_pk + farmer_pk

    # Taproot: hash(local_pk + farmer_pk) → taproot_sk → taproot_pk
    from blspy import AugSchemeMPL, G1Element, PrivateKey
    taproot_message = bytes(local_pk + farmer_pk)
    taproot_hash = std_hash(taproot_message)
    taproot_sk = PrivateKey.from_bytes(taproot_hash)
    return local_pk + farmer_pk + taproot_sk.get_g1()


def generate_keys_and_plot_id(
    k: int,
    strength: int,
    plot_index: int,
    meta_group: int,
    farmer_pk_hex: str,
    pool_pk_hex: str = "",
    contract_address_hex: str = "",
) -> tuple[str, bytes, bytes] | None:
    """Generate plot_id and memo using BLS keys.

    Returns (plot_id_hex, memo_bytes, master_sk_bytes) or None if BLS not available.
    """
    AugSchemeMPL, G1Element, PrivateKey = _try_import_blspy()
    if AugSchemeMPL is None:
        log.warning("blspy/chia_rs not installed, cannot generate proper plot_id")
        return None

    # Generate random master secret key for this plot
    master_sk = AugSchemeMPL.key_gen(secrets.token_bytes(32))

    # Derive local secret key and public key
    local_sk = _master_sk_to_local_sk(master_sk)
    local_pk = local_sk.get_g1()

    # Parse farmer public key
    farmer_pk = G1Element.from_bytes(bytes.fromhex(farmer_pk_hex))

    # Determine pool key type
    if contract_address_hex:
        # NFT plot (pool contract puzzle hash)
        pool_ph = bytes.fromhex(contract_address_hex)
        include_taproot = True
        plot_public_key = _generate_plot_public_key(local_pk, farmer_pk, include_taproot)
        plot_id = calculate_plot_id_v2(
            k, plot_index, meta_group, pool_ph, bytes(plot_public_key), strength
        )
        memo = pool_ph + bytes(farmer_pk) + bytes(master_sk)
        assert len(memo) == 112  # 32 + 48 + 32
    elif pool_pk_hex:
        # OG plot (pool public key)
        pool_pk = G1Element.from_bytes(bytes.fromhex(pool_pk_hex))
        include_taproot = False
        plot_public_key = _generate_plot_public_key(local_pk, farmer_pk, include_taproot)
        plot_id = calculate_plot_id_v2(
            k, plot_index, meta_group, bytes(pool_pk), bytes(plot_public_key), strength
        )
        memo = bytes(pool_pk) + bytes(farmer_pk) + bytes(master_sk)
        assert len(memo) == 128  # 48 + 48 + 32
    else:
        log.warning("No pool key or contract address, using random plot_id")
        return None

    return plot_id.hex(), memo, bytes(master_sk)


# ── Memo injection ─────────────────────────────────────────────

def inject_memo(plot_file: Path, memo: bytes) -> None:
    """Overwrite memo in a pos2 .bin file header.

    The file format stores memo at a fixed offset:
      byte 42: memo_length (uint8)
      byte 43+: memo data

    The plotter writes 112 bytes of zeros. We overwrite with actual key data.
    """
    if not plot_file.exists():
        raise FileNotFoundError(f"Plot file not found: {plot_file}")

    if len(memo) > 255:
        raise ValueError(f"Memo too large: {len(memo)} bytes (max 255)")

    with open(plot_file, "r+b") as f:
        # Verify magic
        magic = f.read(4)
        if magic != b"pos2":
            raise ValueError(f"Not a pos2 plot file: {plot_file} (magic: {magic!r})")

        # Seek to memo_length position
        f.seek(HEADER_MEMO_OFFSET)

        # Read current memo length
        current_memo_len = struct.unpack("B", f.read(1))[0]

        if len(memo) != current_memo_len:
            raise ValueError(
                f"Memo size mismatch: file has {current_memo_len} bytes, "
                f"trying to write {len(memo)} bytes. "
                f"Cannot change memo size (would shift chunk data)."
            )

        # Overwrite memo data (position is now at memo_length + 1 = memo data start)
        f.write(memo)

    log.info("Injected %d-byte memo into %s", len(memo), plot_file.name)


# ── Plot file finalization ─────────────────────────────────────

def get_plot_bin_filename(k: int, strength: int, plot_index: int, meta_group: int,
                          plot_id_hex: str, testnet: bool = False) -> str:
    """Expected .bin filename from pos2-chip plotter."""
    parts = [f"plot_{k}_{strength}_{plot_index}_{meta_group}"]
    if testnet:
        parts.append("testnet")
    parts.append(f"{plot_id_hex}.bin")
    return "_".join(parts)


def get_plot2_filename(k: int, plot_id_hex: str) -> str:
    """Target .plot2 filename for chia harvester."""
    return f"plot-k{k}-{plot_id_hex}.plot2"


def finalize_plot(
    plot_dir: Path,
    k: int,
    strength: int,
    plot_index: int,
    meta_group: int,
    plot_id_hex: str,
    testnet: bool = False,
    memo: bytes | None = None,
) -> Path | None:
    """Post-process a completed plot: inject memo and rename .bin → .plot2.

    Returns the final .plot2 path, or None if .bin file not found.
    """
    bin_name = get_plot_bin_filename(k, strength, plot_index, meta_group, plot_id_hex, testnet)
    bin_path = plot_dir / bin_name

    if not bin_path.exists():
        # Try to find any .bin file with this plot_id
        candidates = list(plot_dir.glob(f"*{plot_id_hex}*.bin"))
        if candidates:
            bin_path = candidates[0]
            log.info("Found plot file by plot_id: %s", bin_path.name)
        else:
            log.error("Plot file not found: %s", bin_name)
            return None

    # Inject memo if provided
    if memo:
        try:
            inject_memo(bin_path, memo)
        except Exception as e:
            log.error("Failed to inject memo: %s", e)
            return None

    # Rename to .plot2
    plot2_name = get_plot2_filename(k, plot_id_hex)
    plot2_path = plot_dir / plot2_name

    bin_path.rename(plot2_path)
    log.info("Renamed %s -> %s", bin_path.name, plot2_name)

    return plot2_path

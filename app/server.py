#!/usr/bin/env python3
"""Neutralis Pools: acompanhamento local de LPs com sincronização da Byreal."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen


PORT = int(os.environ.get("PORT", "8788"))
DATA_DIR = Path(os.environ.get("NEUTRALIS_POOLS_DATA_DIR", "/data"))
DATA_FILE = DATA_DIR / "pools.json"
STATIC_DIR = Path(os.environ.get("STATIC_DIR", Path(__file__).resolve().parents[1] / "dist"))
BYREAL_URL = "https://api2.byreal.io/byreal/api/dex/v2/position/list"
BYREAL_MINT_LIST_URL = "https://api2.byreal.io/byreal/api/dex/v2/mint/list"
SOLANA_RPC_URL = os.environ.get("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
RAYDIUM_MINT_URL = "https://api-v3.raydium.io/mint/ids"
ORCA_TOKEN_URL = "https://api.orca.so/v2/solana/tokens"
RAYDIUM_CLMM_PROGRAM = "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK"
ORCA_WHIRLPOOL_PROGRAM = "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc"
ORCA_IMMUTABLE_WHIRLPOOL_PROGRAM = "iwhrLHdsgrvmnwU8GF2FSmyabSMjfHwFGJAX2ufJ3ZN"
ORCA_WHIRLPOOL_PROGRAMS = (ORCA_WHIRLPOOL_PROGRAM, ORCA_IMMUTABLE_WHIRLPOOL_PROGRAM)
SPL_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
ORCA_POSITION_DISCRIMINATOR = bytes.fromhex("aabc8fe47a40f7d0")
ORCA_POSITION_BUNDLE_DISCRIMINATOR = bytes([129, 169, 175, 65, 185, 95, 32, 100])
SOLANA_PATTERN = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
STABLE_SYMBOLS = {"USD", "USDC", "USDT", "USDS", "PYUSD"}
KNOWN_MINTS = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
    "XsueG8BtpquVJX9LVLLEGuViXUungE6WmK5YZ3p3bd1": "CRCLX",
    "XsoCS1TfEyfFhfvj8EtZ528L3CaKBDBRqRapnBbDF2W": "SPYX",
    "A7bdiYdS5GjqGFtxf17ppRHtDKPkkRqbKtR27dxvQXaS": "ZEC",
    "SKRbvo6Gf7GondiT3BbTfuRDPqLWei4j2Qy2NPGZhW3": "SKR",
}
BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
MAX_BODY = 2 * 1024 * 1024


class AppError(Exception):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


def next_daily_time(anchor: str, reference: datetime | None = None) -> str:
    """Mantém a cadência de 24h ancorada no horário de cadastro."""
    reference = reference or datetime.now(timezone.utc)
    candidate = parse_time(anchor)
    while candidate <= reference:
        candidate += timedelta(hours=24)
    return candidate.isoformat()


def optional_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def timestamp_iso(value: Any) -> str | None:
    stamp = optional_float(value)
    if stamp is None or stamp <= 0:
        return None
    if stamp > 10_000_000_000:
        stamp /= 1000
    try:
        return datetime.fromtimestamp(stamp, timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def json_request(url: str, payload: dict[str, Any] | None = None) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(url, data=data, headers={"accept": "application/json", "content-type": "application/json", "user-agent": "Neutralis-Pools/1.1"}, method="GET" if payload is None else "POST")
    try:
        with urlopen(request, timeout=15) as response:
            return json.load(response)
    except Exception as error:
        raise AppError(f"Falha de rede ao consultar {urlparse(url).hostname}.") from error


def solana_request(payload: dict[str, Any]) -> Any:
    return json_request(SOLANA_RPC_URL, payload)


def token_metadata(pool: dict[str, Any], side: str) -> dict[str, Any]:
    direct = pool.get(f"mint{side}")
    candidates = [direct, pool.get(f"mint{side}Info"), pool.get(f"token{side}"), pool.get(f"token{side}Info")]
    value = next((item for item in candidates if isinstance(item, dict)), {})
    return {
        "address": direct if isinstance(direct, str) else str(value.get("address") or value.get("mintAddress") or value.get("mint") or ""),
        "symbol": str(value.get("symbol") or value.get("ticker") or "").upper(),
        "decimals": optional_float(value.get("decimals", value.get("decimal"))),
        "priceUsd": optional_float(value.get("priceUsd", value.get("usdPrice"))),
    }


def normalize_position(position: dict[str, Any], pool: dict[str, Any]) -> dict[str, Any]:
    token_a = token_metadata(pool, "A")
    token_b = token_metadata(pool, "B")
    stable_a = token_a["symbol"] in STABLE_SYMBOLS
    stable_b = token_b["symbol"] in STABLE_SYMBOLS
    asset = token_b if stable_a and not stable_b else token_a
    quote = token_b if stable_b and not stable_a else token_a
    lower_tick = optional_float(position.get("lowerTick", position.get("tickLower")))
    upper_tick = optional_float(position.get("upperTick", position.get("tickUpper")))
    value_usd = optional_float(position.get("liquidityUsd", position.get("positionValueUsd", position.get("valueUsd"))))
    lower_price = optional_float(position.get("lowerPrice", position.get("priceLower")))
    upper_price = optional_float(position.get("upperPrice", position.get("priceUpper")))
    current_price = optional_float(position.get("currentPrice", position.get("price")))
    if (
        lower_price is None and upper_price is None and stable_a != stable_b
        and token_a["decimals"] is not None and token_b["decimals"] is not None
        and lower_tick is not None and upper_tick is not None
    ):
        scale = 10 ** (token_a["decimals"] - token_b["decimals"])
        tick_lower_price = (1.0001 ** lower_tick) * scale
        tick_upper_price = (1.0001 ** upper_tick) * scale
        current_tick = optional_float(pool.get("tickCurrent", pool.get("currentTick")))
        tick_current_price = (1.0001 ** current_tick) * scale if current_tick is not None else None
        if stable_b:
            lower_price, upper_price, current_price = tick_lower_price, tick_upper_price, current_price or tick_current_price
        else:
            lower_price, upper_price = 1 / tick_upper_price, 1 / tick_lower_price
            current_price = current_price or (1 / tick_current_price if tick_current_price else None)
    if current_price is None and asset.get("priceUsd") is not None:
        quote_usd = quote.get("priceUsd") or 1.0
        if quote_usd > 0:
            current_price = asset["priceUsd"] / quote_usd
    return {
        "externalId": str(position.get("positionAddress") or position.get("address") or ""),
        "poolAddress": str(position.get("poolAddress") or pool.get("poolAddress") or pool.get("address") or ""),
        "name": f'{asset["symbol"]}/{quote["symbol"]}' if asset["symbol"] and quote["symbol"] else "Pool Byreal",
        "token0": asset["symbol"] or "TOKEN",
        "token1": quote["symbol"] or "USDC",
        "currentValue": value_usd,
        "rangeMin": lower_price,
        "rangeMax": upper_price,
        "currentPrice": current_price,
        "fees": optional_float(position.get("earnedUsd", position.get("feesUsd", position.get("feeUsd")))),
        "initialValue": optional_float(position.get("totalDeposit")),
        "openedAt": timestamp_iso(position.get("openTime")),
        "positionAgeMs": optional_float(position.get("positionAgeMs")),
        "pnl": optional_float(position.get("pnlUsd")),
        "pnlPercent": optional_float(position.get("pnlUsdPercent")),
        "reportedApr": optional_float(position.get("apr")),
    }


def byreal_mint_price(mint: str) -> float | None:
    if not SOLANA_PATTERN.fullmatch(mint):
        return None
    root = json_request(BYREAL_MINT_LIST_URL + "?" + urlencode({"page": 1, "pageSize": 10, "search": mint}))

    def visit(value: Any) -> float | None:
        if isinstance(value, dict):
            address = str(value.get("mintAddress") or value.get("address") or value.get("mint") or "")
            if address == mint:
                price = optional_float(value.get("priceUsd", value.get("usdPrice")))
                if price is not None and price > 0:
                    return price
            for nested in value.values():
                found = visit(nested)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for nested in value:
                found = visit(nested)
                if found is not None:
                    return found
        return None

    return visit(root)


def byreal_positions(wallet: str) -> list[dict[str, Any]]:
    if not SOLANA_PATTERN.fullmatch(wallet):
        raise AppError("Carteira Solana inválida.")
    query = "?" + urlencode({"userAddress": wallet, "status": 0, "page": 1, "pageSize": 100})
    root = json_request(BYREAL_URL + query)
    data = root.get("result", {}).get("data") if isinstance(root, dict) and isinstance(root.get("result"), dict) else None
    if data is None and isinstance(root, dict):
        data = root.get("data", root.get("result", root))
    data = data if isinstance(data, dict) else {}
    rows = data.get("positions", data.get("records", []))
    rows = rows if isinstance(rows, list) else []
    pool_map = data.get("poolMap", {})
    pools = pool_map if isinstance(pool_map, list) else list(pool_map.values()) if isinstance(pool_map, dict) else []
    result: list[dict[str, Any]] = []
    for position in rows:
        if not isinstance(position, dict):
            continue
        address = str(position.get("poolAddress") or "")
        if isinstance(pool_map, dict) and isinstance(pool_map.get(address), dict):
            pool = pool_map[address]
        else:
            pool = next((item for item in pools if isinstance(item, dict) and str(item.get("poolAddress") or item.get("address") or "") == address), {})
        normalized = normalize_position(position, pool)
        if normalized["currentPrice"] is None:
            asset = token_metadata(pool, "A")
            quote = token_metadata(pool, "B")
            try:
                asset_usd = byreal_mint_price(asset["address"])
                quote_usd = 1.0 if quote["symbol"] in STABLE_SYMBOLS else byreal_mint_price(quote["address"])
                if asset_usd is not None and quote_usd and quote_usd > 0:
                    normalized["currentPrice"] = asset_usd / quote_usd
            except AppError:
                pass
        if normalized["externalId"]:
            result.append(normalized)
    return result


def base58_decode(value: str) -> bytes:
    number = 0
    for character in value:
        try:
            number = number * 58 + BASE58_ALPHABET.index(character)
        except ValueError as error:
            raise AppError("Endereço Solana inválido.") from error
    payload = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    return b"\0" * (len(value) - len(value.lstrip("1"))) + payload


def base58_encode(value: bytes) -> str:
    number = int.from_bytes(value, "big")
    encoded = ""
    while number:
        number, remainder = divmod(number, 58)
        encoded = BASE58_ALPHABET[remainder] + encoded
    return "1" * (len(value) - len(value.lstrip(b"\0"))) + (encoded or "")


def is_ed25519_point(value: bytes) -> bool:
    if len(value) != 32:
        return False
    prime = 2**255 - 19
    y = int.from_bytes(value, "little") & ((1 << 255) - 1)
    if y >= prime:
        return False
    y_squared = y * y % prime
    d = -121665 * pow(121666, prime - 2, prime) % prime
    denominator = (d * y_squared + 1) % prime
    if denominator == 0:
        return False
    x_squared = (y_squared - 1) * pow(denominator, prime - 2, prime) % prime
    return x_squared == 0 or pow(x_squared, (prime - 1) // 2, prime) == 1


def program_pda(seeds: list[bytes], program_id: str) -> str:
    program = base58_decode(program_id)
    if len(program) != 32 or any(len(seed) > 32 for seed in seeds):
        raise AppError("Não foi possível identificar a posição.")
    for bump in range(255, -1, -1):
        digest = hashlib.sha256(b"".join(seeds) + bytes([bump]) + program + b"ProgramDerivedAddress").digest()
        if not is_ed25519_point(digest):
            return base58_encode(digest)
    raise AppError("Não foi possível identificar a posição.")


def position_pda(nft_mint: str, program_id: str) -> str:
    mint = base58_decode(nft_mint)
    if len(mint) != 32:
        raise AppError("NFT da posição inválido.")
    return program_pda([b"position", mint], program_id)


def orca_position_bundle_pda(nft_mint: str, program_id: str) -> str:
    mint = base58_decode(nft_mint)
    return program_pda([b"position_bundle", mint], program_id)


def orca_bundled_position_pda(bundle: str, index: int, program_id: str) -> str:
    return program_pda([b"bundled_position", base58_decode(bundle), str(index).encode("ascii")], program_id)


def solana_account(address: str, expected_owner: str | None = None) -> bytes:
    response = solana_request({"jsonrpc": "2.0", "id": 1, "method": "getAccountInfo", "params": [address, {"encoding": "base64", "commitment": "confirmed"}]})
    value = response.get("result", {}).get("value") if isinstance(response, dict) else None
    if not isinstance(value, dict) or not isinstance(value.get("data"), list):
        raise AppError("Conta Solana não encontrada.")
    if expected_owner and value.get("owner") != expected_owner:
        raise AppError("Conta Solana pertence a outro programa.")
    try:
        return base64.b64decode(value["data"][0], validate=True)
    except Exception as error:
        raise AppError("Resposta inválida da rede Solana.") from error


def solana_accounts(addresses: list[str], expected_owner: str) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for start in range(0, len(addresses), 100):
        chunk = addresses[start:start + 100]
        response = solana_request({"jsonrpc": "2.0", "id": 1, "method": "getMultipleAccounts", "params": [chunk, {"encoding": "base64", "commitment": "confirmed"}]})
        values = response.get("result", {}).get("value") if isinstance(response, dict) else None
        if not isinstance(values, list) or len(values) != len(chunk):
            raise AppError("Resposta incompleta da rede Solana.")
        for address, value in zip(chunk, values):
            if not isinstance(value, dict) or value.get("owner") != expected_owner or not isinstance(value.get("data"), list):
                continue
            result[address] = base64.b64decode(value["data"][0], validate=True)
    return result


def public_key_at(data: bytes, offset: int) -> str:
    value = data[offset:offset + 32]
    if len(value) != 32:
        raise AppError("Conta Solana incompleta.")
    return base58_encode(value)


def dex_symbols(mints: list[str], source: str) -> dict[str, str]:
    symbols = {mint: KNOWN_MINTS[mint] for mint in mints if mint in KNOWN_MINTS}
    missing = [mint for mint in mints if mint not in symbols]
    if not missing:
        return symbols
    try:
        if source == "orca":
            root = json_request(ORCA_TOKEN_URL + "?" + urlencode({"tokens": ",".join(missing), "size": len(missing)}))
            rows = root.get("data", []) if isinstance(root, dict) else []
        else:
            root = json_request(RAYDIUM_MINT_URL + "?" + urlencode({"mints": ",".join(missing)}))
            rows = root.get("data", root) if isinstance(root, dict) else root
            rows = list(rows.values()) if isinstance(rows, dict) else rows
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict):
                address = str(row.get("address") or row.get("mint") or "")
                symbol = str(row.get("symbol") or "").upper()
                if address in missing and symbol:
                    symbols[address] = symbol
    except AppError:
        pass
    if source == "orca" and any(mint not in symbols for mint in mints):
        symbols.update(dex_symbols([mint for mint in mints if mint not in symbols], "raydium"))
    return symbols


def concentrated_position(source: str, nft_mint: str, position_data: bytes | None = None, program_id: str | None = None, position_address: str | None = None) -> dict[str, Any]:
    if not SOLANA_PATTERN.fullmatch(nft_mint):
        raise AppError(f"NFT da posição {source.title()} inválido.")
    if source == "raydium":
        program_id = RAYDIUM_CLMM_PROGRAM
        position_address = position_pda(nft_mint, program_id)
        data = position_data or solana_account(position_address)
        if len(data) < 145:
            raise AppError("Conta da posição Raydium incompleta.")
        pool_address, stored_nft = public_key_at(data, 41), public_key_at(data, 9)
        tick_lower = int.from_bytes(data[73:77], "little", signed=True)
        tick_upper = int.from_bytes(data[77:81], "little", signed=True)
        raw_liquidity = int.from_bytes(data[81:97], "little")
        fee_a_raw = int.from_bytes(data[129:137], "little")
        fee_b_raw = int.from_bytes(data[137:145], "little")
        pool_data = solana_account(pool_address)
        mint_a, mint_b = public_key_at(pool_data, 73), public_key_at(pool_data, 105)
        decimals_a, decimals_b = pool_data[233], pool_data[234]
        sqrt_price_x64 = int.from_bytes(pool_data[253:269], "little")
    else:
        program_id = program_id or ORCA_WHIRLPOOL_PROGRAM
        position_address = position_address or position_pda(nft_mint, program_id)
        data = position_data or solana_account(position_address, program_id)
        if len(data) < 144 or data[:8] != ORCA_POSITION_DISCRIMINATOR:
            raise AppError("Conta da posição Orca inválida.")
        pool_address, stored_nft = public_key_at(data, 8), public_key_at(data, 40)
        raw_liquidity = int.from_bytes(data[72:88], "little")
        tick_lower = int.from_bytes(data[88:92], "little", signed=True)
        tick_upper = int.from_bytes(data[92:96], "little", signed=True)
        fee_a_raw = int.from_bytes(data[112:120], "little")
        fee_b_raw = int.from_bytes(data[136:144], "little")
        pool_data = solana_account(pool_address, program_id)
        sqrt_price_x64 = int.from_bytes(pool_data[65:81], "little")
        mint_a, mint_b = public_key_at(pool_data, 101), public_key_at(pool_data, 181)
        mint_a_data, mint_b_data = solana_account(mint_a), solana_account(mint_b)
        decimals_a, decimals_b = mint_a_data[44], mint_b_data[44]
    if stored_nft != nft_mint or raw_liquidity <= 0 or sqrt_price_x64 <= 0:
        raise AppError(f"A posição {source.title()} não está ativa.")
    symbols = dex_symbols([mint_a, mint_b], source)
    symbol_a, symbol_b = symbols.get(mint_a, ""), symbols.get(mint_b, "")
    stable_a, stable_b = symbol_a in STABLE_SYMBOLS, symbol_b in STABLE_SYMBOLS
    if stable_a == stable_b:
        raise AppError(f"A pool {source.title()} precisa ter uma cotação estável reconhecida.")
    scale = 10 ** (decimals_a - decimals_b)
    raw_price = (sqrt_price_x64 / 2**64) ** 2
    price_b_per_a = raw_price * scale
    tick_lower_price, tick_upper_price = (1.0001**tick_lower) * scale, (1.0001**tick_upper) * scale
    sqrt_current, sqrt_lower, sqrt_upper = math.sqrt(raw_price), math.sqrt(1.0001**tick_lower), math.sqrt(1.0001**tick_upper)
    if sqrt_current <= sqrt_lower:
        amount_a_raw, amount_b_raw = raw_liquidity * (sqrt_upper - sqrt_lower) / (sqrt_lower * sqrt_upper), 0.0
    elif sqrt_current >= sqrt_upper:
        amount_a_raw, amount_b_raw = 0.0, raw_liquidity * (sqrt_upper - sqrt_lower)
    else:
        amount_a_raw = raw_liquidity * (sqrt_upper - sqrt_current) / (sqrt_current * sqrt_upper)
        amount_b_raw = raw_liquidity * (sqrt_current - sqrt_lower)
    amount_a, amount_b = amount_a_raw / 10**decimals_a, amount_b_raw / 10**decimals_b
    fee_a, fee_b = fee_a_raw / 10**decimals_a, fee_b_raw / 10**decimals_b
    if stable_b:
        asset, quote, lower, upper, price = symbol_a, symbol_b, tick_lower_price, tick_upper_price, price_b_per_a
        liquidity_usd, fees_usd = amount_a * price + amount_b, fee_a * price + fee_b
    else:
        asset, quote, lower, upper, price = symbol_b, symbol_a, 1 / tick_upper_price, 1 / tick_lower_price, 1 / price_b_per_a
        liquidity_usd, fees_usd = amount_b * price + amount_a, fee_b * price + fee_a
    return {"externalId": position_address, "poolAddress": pool_address, "name": f"{asset}/{quote}", "token0": asset, "token1": quote, "currentValue": liquidity_usd, "rangeMin": lower, "rangeMax": upper, "currentPrice": price, "feesRaw": fees_usd, "openedAt": None, "reportedApr": None, "pnl": None, "pnlPercent": None}


def solana_nft_mints(wallet: str) -> list[str]:
    if not SOLANA_PATTERN.fullmatch(wallet):
        raise AppError("Carteira Solana inválida.")
    mints: set[str] = set()
    successful = 0
    for program_id in (SPL_TOKEN_PROGRAM, TOKEN_2022_PROGRAM):
        try:
            response = solana_request({"jsonrpc": "2.0", "id": 1, "method": "getTokenAccountsByOwner", "params": [wallet, {"programId": program_id}, {"encoding": "base64", "commitment": "confirmed"}]})
        except AppError:
            continue
        successful += 1
        rows = response.get("result", {}).get("value", []) if isinstance(response, dict) else []
        for row in rows if isinstance(rows, list) else []:
            encoded = row.get("account", {}).get("data") if isinstance(row, dict) else None
            if not isinstance(encoded, list) or not encoded:
                continue
            try:
                account = base64.b64decode(encoded[0], validate=True)
            except Exception:
                continue
            if len(account) >= 72 and int.from_bytes(account[64:72], "little") == 1:
                mints.add(base58_encode(account[:32]))
    if not successful:
        raise AppError("Falha ao consultar os NFTs da carteira Solana.")
    return sorted(mints)


def raydium_positions(nft_mint: str) -> list[dict[str, Any]]:
    return [concentrated_position("raydium", nft_mint)]


def orca_positions(wallet: str) -> list[dict[str, Any]]:
    positions: list[dict[str, Any]] = []
    nfts = solana_nft_mints(wallet)
    for program_id in ORCA_WHIRLPOOL_PROGRAMS:
        regular = {position_pda(mint, program_id): mint for mint in nfts}
        bundles = {orca_position_bundle_pda(mint, program_id): mint for mint in nfts}
        accounts = solana_accounts(list(regular) + list(bundles), program_id)
        for address, mint in regular.items():
            data = accounts.get(address)
            if data and data[:8] == ORCA_POSITION_DISCRIMINATOR:
                try:
                    positions.append(concentrated_position("orca", mint, data, program_id, address))
                except AppError:
                    pass
        bundled: dict[str, tuple[str, str]] = {}
        for bundle_address, mint in bundles.items():
            data = accounts.get(bundle_address)
            if not data or len(data) < 72 or data[:8] != ORCA_POSITION_BUNDLE_DISCRIMINATOR:
                continue
            for index in range(256):
                if data[40 + index // 8] & (1 << (index % 8)):
                    bundled[orca_bundled_position_pda(bundle_address, index, program_id)] = (mint, bundle_address)
        for address, data in solana_accounts(list(bundled), program_id).items() if bundled else []:
            mint, _ = bundled[address]
            try:
                positions.append(concentrated_position("orca", mint, data, program_id, address))
            except AppError:
                pass
    if not positions:
        raise AppError("Nenhuma posição Orca ativa foi encontrada nesta carteira.")
    unique = {item["externalId"]: item for item in positions}
    return list(unique.values())


class Store:
    def __init__(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.data = self._load()

    def _load(self) -> dict[str, Any]:
        defaults = {"version": 2, "settings": {"wallet": "", "autoSync": True, "connections": {}}, "pools": [], "lastSync": None, "lastError": None}
        try:
            loaded = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                defaults.update(loaded)
        except (OSError, json.JSONDecodeError):
            pass
        settings = defaults.setdefault("settings", {})
        settings.setdefault("autoSync", True)
        settings.setdefault("wallet", "")
        connections = settings.setdefault("connections", {})
        if settings["wallet"] and "byreal" not in connections:
            connections["byreal"] = settings["wallet"]
        return defaults

    def save(self) -> None:
        temp = DATA_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(temp, 0o600)
        temp.replace(DATA_FILE)

    def state(self) -> dict[str, Any]:
        with self.lock:
            return json.loads(json.dumps(self.data))

    def set_settings(self, incoming: dict[str, Any]) -> dict[str, Any]:
        wallet = str(incoming.get("wallet", self.data["settings"].get("wallet", ""))).strip()
        if wallet and not SOLANA_PATTERN.fullmatch(wallet):
            raise AppError("Carteira Solana inválida.")
        auto_sync = bool(incoming.get("autoSync", self.data["settings"].get("autoSync", True)))
        with self.lock:
            connections = dict(self.data["settings"].get("connections", {}))
            if wallet:
                connections["byreal"] = wallet
            self.data["settings"] = {"wallet": wallet, "autoSync": auto_sync, "connections": connections}
            self.save()
            return dict(self.data["settings"])

    def add_manual(self, incoming: dict[str, Any]) -> dict[str, Any]:
        initial = optional_float(incoming.get("initialValue"))
        if initial is None or initial <= 0:
            raise AppError("Informe uma liquidez inicial maior que zero.")
        current = optional_float(incoming.get("currentValue")) or initial
        fees = optional_float(incoming.get("fees")) or 0.0
        created_at = now_iso()
        started_at = str(incoming.get("startedAt") or created_at)
        pool = {
            "id": uuid.uuid4().hex,
            "source": str(incoming.get("source") or "manual"),
            "network": str(incoming.get("network") or "Solana"),
            "exchange": str(incoming.get("exchange") or "Outra DEX"),
            "name": str(incoming.get("name") or f'{incoming.get("token0", "TOKEN")}/{incoming.get("token1", "USDC")}'),
            "token0": str(incoming.get("token0") or "TOKEN").upper(),
            "token1": str(incoming.get("token1") or "USDC").upper(),
            "externalId": str(incoming.get("externalId") or ""),
            "poolAddress": str(incoming.get("poolAddress") or ""),
            "initialValue": initial,
            "startedAt": started_at,
            "rangeMin": optional_float(incoming.get("rangeMin")),
            "rangeMax": optional_float(incoming.get("rangeMax")),
            "status": "active",
            "createdAt": created_at,
            "updatedAt": created_at,
            "nextSnapshotAt": None,
            "feesBaseline": 0.0,
            "snapshots": [{"date": created_at[:10], "capturedAt": created_at, "value": current, "fees": fees, "price": optional_float(incoming.get("currentPrice")), "apr": optional_float(incoming.get("reportedApr")), "source": "manual"}],
        }
        with self.lock:
            self.data["pools"].append(pool)
            self.save()
        return pool

    def update_pool(self, pool_id: str, incoming: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            pool = next((item for item in self.data["pools"] if item["id"] == pool_id), None)
            if not pool:
                raise AppError("Pool não encontrada.")
            for key in ("name", "network", "exchange", "token0", "token1", "startedAt"):
                if key in incoming:
                    pool[key] = str(incoming[key])
            for key in ("initialValue", "rangeMin", "rangeMax"):
                if key in incoming:
                    pool[key] = optional_float(incoming[key])
            pool["updatedAt"] = now_iso()
            self.save()
            return pool

    def snapshot(self, pool_id: str, incoming: dict[str, Any], source: str = "manual") -> dict[str, Any]:
        value = optional_float(incoming.get("currentValue", incoming.get("value")))
        fees = optional_float(incoming.get("fees"))
        if value is None or value < 0 or fees is None or fees < 0:
            raise AppError("Valor atual e taxas devem ser números positivos.")
        captured_at = str(incoming.get("capturedAt") or now_iso())
        date = str(incoming.get("date") or captured_at[:10])
        row = {"date": date, "capturedAt": captured_at, "value": value, "fees": fees, "price": optional_float(incoming.get("currentPrice", incoming.get("price"))), "apr": optional_float(incoming.get("reportedApr", incoming.get("apr"))), "source": source}
        with self.lock:
            pool = next((item for item in self.data["pools"] if item["id"] == pool_id), None)
            if not pool:
                raise AppError("Pool não encontrada.")
            pool["snapshots"].append(row)
            pool["snapshots"].sort(key=lambda item: item.get("capturedAt", item["date"]))
            pool["updatedAt"] = now_iso()
            self.save()
            return row

    def sync_byreal(self, wallet: str | None = None, automatic: bool = False) -> dict[str, Any]:
        wallet = (wallet or self.data["settings"].get("wallet") or "").strip()
        discovered = byreal_positions(wallet)
        created = 0
        updated = 0
        captured_at = now_iso()
        captured_time = parse_time(captured_at)
        with self.lock:
            for item in discovered:
                pool = next((p for p in self.data["pools"] if p.get("source") == "byreal" and p.get("externalId") == item["externalId"]), None)
                is_new = pool is None
                due_at = captured_time
                if pool is None:
                    if automatic:
                        continue
                    current = item["currentValue"] or 0.0
                    initial = item["initialValue"] if item["initialValue"] is not None and item["initialValue"] > 0 else current
                    pool = {
                        "id": uuid.uuid4().hex,
                        "source": "byreal",
                        "network": "Solana",
                        "exchange": "Byreal",
                        "name": item["name"],
                        "token0": item["token0"],
                        "token1": item["token1"],
                        "externalId": item["externalId"],
                        "poolAddress": item["poolAddress"],
                        "initialValue": initial,
                        "initialValueLocked": True,
                        "startedAt": item["openedAt"] or captured_at,
                        "rangeMin": item["rangeMin"],
                        "rangeMax": item["rangeMax"],
                        "status": "active",
                        "createdAt": captured_at,
                        "updatedAt": captured_at,
                        "nextSnapshotAt": (captured_time + timedelta(hours=24)).isoformat(),
                        "feesBaseline": 0.0,
                        "historyScope": "byreal-lifetime",
                        "snapshots": [],
                    }
                    self.data["pools"].append(pool)
                    created += 1
                else:
                    due_at = parse_time(str(pool.get("nextSnapshotAt") or pool.get("createdAt") or captured_at))
                    if automatic and due_at > captured_time:
                        continue
                    pool.update({key: item[key] for key in ("name", "token0", "token1", "poolAddress", "rangeMin", "rangeMax")})
                    if not pool.get("initialValueLocked"):
                        if item["initialValue"] is not None and item["initialValue"] > 0:
                            pool["initialValue"] = item["initialValue"]
                        if item["openedAt"]:
                            pool["startedAt"] = item["openedAt"]
                        pool["initialValueLocked"] = True
                    pool["feesBaseline"] = 0.0
                    pool["historyScope"] = "byreal-lifetime"
                    pool["status"] = "active"
                    updated += 1
                last_fees = pool["snapshots"][-1]["fees"] if pool["snapshots"] else 0.0
                row = {
                    "date": captured_at[:10],
                    "capturedAt": captured_at,
                    "value": item["currentValue"] if item["currentValue"] is not None else (pool["snapshots"][-1]["value"] if pool["snapshots"] else pool["initialValue"]),
                    "fees": item["fees"] if item["fees"] is not None else last_fees,
                    "price": item["currentPrice"],
                    "apr": item["reportedApr"],
                    "pnl": item["pnl"],
                    "pnlPercent": item["pnlPercent"],
                    "source": "byreal-auto" if automatic else "byreal",
                }
                pool["live"] = row
                if is_new or due_at <= captured_time:
                    pool["snapshots"].append(row)
                    pool["snapshots"].sort(key=lambda snap: snap.get("capturedAt", snap["date"]))
                elif not any(snap.get("source") == "byreal-auto" for snap in pool["snapshots"]):
                    # Sincronizações manuais antes da primeira janela de 24h atualizam
                    # apenas o valor ao vivo; o histórico conserva um único ponto inicial.
                    pool["snapshots"] = pool["snapshots"][:1]
                pool["updatedAt"] = captured_at
                if not is_new and due_at <= captured_time:
                    pool["nextSnapshotAt"] = next_daily_time(str(pool.get("nextSnapshotAt") or captured_at), captured_time)
            active_ids = {item["externalId"] for item in discovered}
            for pool in self.data["pools"]:
                if pool.get("source") == "byreal" and pool.get("externalId") not in active_ids:
                    pool["status"] = "closed"
                    pool["closedAt"] = captured_at
                    pool["nextSnapshotAt"] = None
            self.data["settings"]["wallet"] = wallet
            self.data["lastSync"] = captured_at
            self.data["lastError"] = None
            self.save()
        return {"found": len(discovered), "created": created, "updated": updated, "syncedAt": self.data["lastSync"]}

    def sync_dex(self, source: str, address: str | None = None, automatic: bool = False) -> dict[str, Any]:
        if source == "byreal":
            return self.sync_byreal(address, automatic)
        if source not in {"raydium", "orca"}:
            raise AppError("DEX não reconhecida.")
        settings = self.data.get("settings", {})
        address = str(address or settings.get("connections", {}).get(source) or "").strip()
        if not SOLANA_PATTERN.fullmatch(address):
            raise AppError("Informe uma carteira ou NFT Solana válido.")
        discovered = raydium_positions(address) if source == "raydium" else orca_positions(address)
        captured_at = now_iso()
        captured_time = parse_time(captured_at)
        created = updated = 0
        with self.lock:
            for item in discovered:
                pool = next((p for p in self.data["pools"] if p.get("source") == source and p.get("externalId") == item["externalId"]), None)
                is_new = pool is None
                due_at = captured_time
                if is_new:
                    if automatic:
                        continue
                    current = item["currentValue"] or 0.0
                    pool = {
                        "id": uuid.uuid4().hex, "source": source, "network": "Solana",
                        "exchange": source.title(), "name": item["name"], "token0": item["token0"], "token1": item["token1"],
                        "externalId": item["externalId"], "poolAddress": item["poolAddress"], "initialValue": current,
                        "syncAddress": address,
                        "initialValueLocked": True, "startedAt": captured_at, "rangeMin": item["rangeMin"], "rangeMax": item["rangeMax"],
                        "status": "active", "createdAt": captured_at, "updatedAt": captured_at,
                        "nextSnapshotAt": (captured_time + timedelta(hours=24)).isoformat(), "feesBaseline": 0.0,
                        "historyScope": "tracked", "feeRaw": item.get("feesRaw") or 0.0, "snapshots": [],
                    }
                    self.data["pools"].append(pool)
                    created += 1
                else:
                    due_at = parse_time(str(pool.get("nextSnapshotAt") or pool.get("createdAt") or captured_at))
                    if automatic and due_at > captured_time:
                        continue
                    pool.update({key: item[key] for key in ("name", "token0", "token1", "poolAddress", "rangeMin", "rangeMax")})
                    pool["status"] = "active"
                    updated += 1
                previous_raw = optional_float(pool.get("feeRaw")) or 0.0
                current_raw = optional_float(item.get("feesRaw")) or 0.0
                previous_total = optional_float(pool.get("live", {}).get("fees")) if isinstance(pool.get("live"), dict) else (optional_float(pool["snapshots"][-1].get("fees")) if pool["snapshots"] else 0.0)
                cumulative_fees = (previous_total or 0.0) + max(0.0, current_raw - previous_raw)
                row = {"date": captured_at[:10], "capturedAt": captured_at, "value": item["currentValue"], "fees": cumulative_fees,
                       "price": item["currentPrice"], "apr": None, "pnl": None, "pnlPercent": None,
                       "source": f"{source}-auto" if automatic else source}
                pool["feeRaw"] = current_raw
                pool["live"] = row
                if is_new or due_at <= captured_time:
                    pool["snapshots"].append(row)
                    pool["snapshots"].sort(key=lambda snap: snap.get("capturedAt", snap["date"]))
                pool["updatedAt"] = captured_at
                if not is_new and due_at <= captured_time:
                    pool["nextSnapshotAt"] = next_daily_time(str(pool.get("nextSnapshotAt") or captured_at), captured_time)
            connections = self.data["settings"].setdefault("connections", {})
            connections[source] = address
            self.data["lastSync"] = captured_at
            self.data["lastError"] = None
            self.save()
        return {"found": len(discovered), "created": created, "updated": updated, "syncedAt": captured_at}

    def close(self, pool_id: str) -> None:
        with self.lock:
            pool = next((item for item in self.data["pools"] if item["id"] == pool_id), None)
            if not pool:
                raise AppError("Pool não encontrada.")
            pool["status"] = "closed"
            pool["closedAt"] = now_iso()
            pool["nextSnapshotAt"] = None
            pool["updatedAt"] = now_iso()
            self.save()

    def delete(self, pool_id: str) -> None:
        with self.lock:
            before = len(self.data["pools"])
            self.data["pools"] = [item for item in self.data["pools"] if item["id"] != pool_id]
            if len(self.data["pools"]) == before:
                raise AppError("Pool não encontrada.")
            self.save()


STORE = Store()


class AutoSync(threading.Thread):
    def __init__(self) -> None:
        super().__init__(daemon=True, name="dex-daily-sync")
        self.interval = max(15, int(os.environ.get("SYNC_CHECK_SECONDS", "60")))

    def run(self) -> None:
        while True:
            state = STORE.state()
            settings = state.get("settings", {})
            now = datetime.now(timezone.utc)
            due_connections = {
                (str(pool.get("source")), str(pool.get("syncAddress") or "")) for pool in state.get("pools", [])
                if pool.get("source") in {"byreal", "raydium", "orca"} and pool.get("status") == "active"
                and pool.get("nextSnapshotAt") and parse_time(str(pool["nextSnapshotAt"])) <= now
            }
            connections = settings.get("connections", {}) if isinstance(settings.get("connections"), dict) else {}
            if settings.get("wallet") and "byreal" not in connections:
                connections["byreal"] = settings["wallet"]
            if settings.get("autoSync"):
                for source, pool_address in due_connections:
                    address = pool_address or connections.get(source)
                    if not address:
                        continue
                    try:
                        STORE.sync_dex(source, address, automatic=True)
                    except AppError as error:
                        with STORE.lock:
                            STORE.data["lastError"] = f"{source.title()}: {error}"
                            STORE.save()
            # Dorme no máximo até a próxima coleta. Assim uma posição
            # cadastrada entre duas verificações não precisa esperar uma hora.
            refreshed = STORE.state()
            next_times = [
                parse_time(str(pool["nextSnapshotAt"]))
                for pool in refreshed.get("pools", [])
                if pool.get("source") in {"byreal", "raydium", "orca"} and pool.get("status") == "active"
                and pool.get("nextSnapshotAt")
            ]
            wait_seconds = self.interval
            if next_times:
                wait_seconds = max(1, min(self.interval, (min(next_times) - datetime.now(timezone.utc)).total_seconds()))
            time.sleep(wait_seconds)


class Handler(SimpleHTTPRequestHandler):
    server_version = "NeutralisPools/1.0"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_BODY:
            raise AppError("Arquivo muito grande.")
        try:
            value = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as error:
            raise AppError("JSON inválido.") from error
        if not isinstance(value, dict):
            raise AppError("Conteúdo inválido.")
        return value

    def api(self, method: str) -> bool:
        path = urlparse(self.path).path
        parts = [part for part in path.split("/") if part]
        if not parts or parts[0] != "api":
            return False
        try:
            if method == "GET" and path == "/api/health":
                self.send_json(200, {"ok": True, "time": now_iso()})
            elif method == "GET" and path == "/api/state":
                self.send_json(200, STORE.state())
            elif method == "GET" and path == "/api/export":
                self.send_json(200, STORE.state())
            elif method == "GET" and path == "/api/byreal/discover":
                wallet = parse_qs(urlparse(self.path).query).get("wallet", [""])[0]
                self.send_json(200, {"positions": byreal_positions(wallet)})
            elif method == "GET" and path == "/api/dex/discover":
                query = parse_qs(urlparse(self.path).query)
                source = str(query.get("source", [""])[0])
                address = str(query.get("address", [""])[0])
                positions = byreal_positions(address) if source == "byreal" else raydium_positions(address) if source == "raydium" else orca_positions(address) if source == "orca" else []
                self.send_json(200, {"positions": positions})
            elif method == "PUT" and path == "/api/settings":
                self.send_json(200, STORE.set_settings(self.body()))
            elif method == "POST" and path == "/api/pools":
                self.send_json(HTTPStatus.CREATED, STORE.add_manual(self.body()))
            elif method == "POST" and path == "/api/byreal/sync":
                incoming = self.body()
                self.send_json(200, STORE.sync_byreal(str(incoming.get("wallet") or "") or None))
            elif method == "POST" and path == "/api/dex/sync":
                incoming = self.body()
                self.send_json(200, STORE.sync_dex(str(incoming.get("source") or ""), str(incoming.get("address") or "") or None))
            elif len(parts) == 3 and parts[1] == "pools" and method == "PATCH":
                self.send_json(200, STORE.update_pool(parts[2], self.body()))
            elif len(parts) == 4 and parts[1] == "pools" and parts[3] == "snapshots" and method == "POST":
                self.send_json(HTTPStatus.CREATED, STORE.snapshot(parts[2], self.body()))
            elif len(parts) == 4 and parts[1] == "pools" and parts[3] == "close" and method == "POST":
                STORE.close(parts[2])
                self.send_json(200, {"ok": True})
            elif len(parts) == 3 and parts[1] == "pools" and method == "DELETE":
                STORE.delete(parts[2])
                self.send_json(200, {"ok": True})
            else:
                self.send_json(404, {"error": "Rota não encontrada."})
        except AppError as error:
            self.send_json(400, {"error": str(error)})
        except Exception as error:
            print(f"Erro inesperado: {error}")
            self.send_json(500, {"error": "Falha interna ao processar a solicitação."})
        return True

    def do_GET(self) -> None:
        if self.api("GET"):
            return
        static_path = urlparse(self.path).path
        if static_path not in ("/", "/index.html", "/style.css", "/app.js", "/icon.svg"):
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self) -> None:
        self.api("POST")

    def do_PUT(self) -> None:
        self.api("PUT")

    def do_PATCH(self) -> None:
        self.api("PATCH")

    def do_DELETE(self) -> None:
        self.api("DELETE")


if __name__ == "__main__":
    AutoSync().start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Neutralis Pools em http://0.0.0.0:{PORT}")
    server.serve_forever()

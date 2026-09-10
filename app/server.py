#!/usr/bin/env python3
"""Neutralis Pools: acompanhamento local de LPs com sincronização da Byreal."""

from __future__ import annotations

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
SOLANA_PATTERN = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
STABLE_SYMBOLS = {"USD", "USDC", "USDT", "USDS", "PYUSD"}
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


def json_request(url: str) -> Any:
    request = Request(url, headers={"accept": "application/json", "user-agent": "Neutralis-Pools/1.0"})
    try:
        with urlopen(request, timeout=15) as response:
            return json.load(response)
    except Exception as error:
        raise AppError("A Byreal não respondeu. Tente novamente em alguns minutos.") from error


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


class Store:
    def __init__(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.data = self._load()

    def _load(self) -> dict[str, Any]:
        defaults = {"version": 1, "settings": {"wallet": "", "autoSync": True}, "pools": [], "lastSync": None, "lastError": None}
        try:
            loaded = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                defaults.update(loaded)
        except (OSError, json.JSONDecodeError):
            pass
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
            self.data["settings"] = {"wallet": wallet, "autoSync": auto_sync}
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
        super().__init__(daemon=True, name="byreal-daily-sync")
        self.interval = max(300, int(os.environ.get("SYNC_CHECK_SECONDS", "3600")))

    def run(self) -> None:
        while True:
            state = STORE.state()
            settings = state.get("settings", {})
            now = datetime.now(timezone.utc)
            due = any(
                pool.get("source") == "byreal" and pool.get("status") == "active"
                and pool.get("nextSnapshotAt") and parse_time(str(pool["nextSnapshotAt"])) <= now
                for pool in state.get("pools", [])
            )
            if settings.get("autoSync") and settings.get("wallet") and due:
                try:
                    STORE.sync_byreal(automatic=True)
                except AppError as error:
                    with STORE.lock:
                        STORE.data["lastError"] = str(error)
                        STORE.save()
            time.sleep(self.interval)


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
            elif method == "PUT" and path == "/api/settings":
                self.send_json(200, STORE.set_settings(self.body()))
            elif method == "POST" and path == "/api/pools":
                self.send_json(HTTPStatus.CREATED, STORE.add_manual(self.body()))
            elif method == "POST" and path == "/api/byreal/sync":
                incoming = self.body()
                self.send_json(200, STORE.sync_byreal(str(incoming.get("wallet") or "") or None))
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

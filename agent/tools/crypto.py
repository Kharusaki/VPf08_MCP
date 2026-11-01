"""Инструмент «Курс криптовалюты» с несколькими источниками и метаданными."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict

from .http_utils import get_json, request
from .time_utils import format_times

logger = logging.getLogger(__name__)

COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"
COINBASE_URL = "https://api.coinbase.com/v2/prices/{pair}/spot"
BLOCKCHAIN_URL = "https://blockchain.info/ticker"

# Частые тикеры -> id в CoinGecko
COIN_ALIASES: Dict[str, str] = {
    "btc": "bitcoin",
    "eth": "ethereum",
    "usdt": "tether",
    "usdc": "usd-coin",
    "bnb": "binancecoin",
    "sol": "solana",
    "ton": "the-open-network",
    "xrp": "ripple",
    "ada": "cardano",
    "doge": "dogecoin",
    "trx": "tron",
    "dot": "polkadot",
    "ltc": "litecoin",
    "bch": "bitcoin-cash",
    "link": "chainlink",
    "matic": "matic-network",
    "avax": "avalanche-2",
    "shib": "shiba-inu",
    "atom": "cosmos",
    "xmr": "monero",
}

COIN_SYMBOLS: Dict[str, str] = {
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "tether": "USDT",
    "usd-coin": "USDC",
    "binancecoin": "BNB",
    "solana": "SOL",
    "the-open-network": "TON",
    "ripple": "XRP",
    "cardano": "ADA",
    "dogecoin": "DOGE",
    "tron": "TRX",
    "polkadot": "DOT",
    "litecoin": "LTC",
    "bitcoin-cash": "BCH",
    "chainlink": "LINK",
    "matic-network": "MATIC",
    "avalanche-2": "AVAX",
    "shiba-inu": "SHIB",
    "cosmos": "ATOM",
    "monero": "XMR",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _validate(coin: str, currency: str) -> tuple[str, str]:
    coin = (coin or "").strip().lower()
    currency = (currency or "usd").strip().lower()
    if not coin:
        raise ValueError("Не указана криптовалюта.")
    if not currency.isalpha() or not (2 <= len(currency) <= 5):
        raise ValueError("Валюта должна быть кодом, например usd, eur или rub.")
    return COIN_ALIASES.get(coin, coin), currency


def _quote(
    coin_id: str,
    currency: str,
    price: Any,
    source: str,
    source_url: str,
    updated_at: str | None = None,
) -> Dict[str, Any]:
    try:
        value = float(price)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Некорректная цена от {source}: {price!r}") from exc
    if value <= 0:
        raise ValueError(f"{source} вернул некорректную цену: {value}")
    fetched_at = _now_iso()
    fetched_times = format_times(fetched_at)
    updated_times = format_times(updated_at)
    return {
        "coin": coin_id,
        "currency": currency.upper(),
        "price": value,
        "source": source,
        "source_url": source_url,
        "updated_at": updated_at,
        "updated_at_utc": updated_times["utc"],
        "updated_at_msk": updated_times["msk"],
        "fetched_at": fetched_at,
        "fetched_at_utc": fetched_times["utc"],
        "fetched_at_msk": fetched_times["msk"],
    }


def _coingecko(coin_id: str, currency: str) -> Dict[str, Any]:
    data = get_json(
        COINGECKO_URL,
        params={
            "ids": coin_id,
            "vs_currencies": currency,
            "include_last_updated_at": "true",
        },
        timeout=20,
    )
    prices = data.get(coin_id) or {}
    if currency not in prices:
        raise ValueError(f"CoinGecko не вернул цену {coin_id}/{currency}")
    timestamp = prices.get("last_updated_at")
    updated_at = None
    if timestamp:
        updated_at = datetime.fromtimestamp(
            int(timestamp), tz=timezone.utc
        ).isoformat(timespec="seconds")
    return _quote(
        coin_id,
        currency,
        prices[currency],
        "CoinGecko",
        f"https://www.coingecko.com/en/coins/{coin_id}",
        updated_at,
    )


def _coinbase(coin_id: str, currency: str) -> Dict[str, Any]:
    symbol = COIN_SYMBOLS.get(coin_id, coin_id.upper())
    response = request(
        "GET",
        COINBASE_URL.format(pair=f"{symbol}-{currency.upper()}"),
        timeout=20,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Coinbase вернул HTTP {response.status_code}")
    payload = response.json().get("data") or {}
    return _quote(
        coin_id,
        currency,
        payload.get("amount"),
        "Coinbase",
        f"https://www.coinbase.com/price/{coin_id}",
    )


def _blockchain_info(coin_id: str, currency: str) -> Dict[str, Any]:
    if coin_id != "bitcoin":
        raise ValueError("Blockchain.com поддерживает только Bitcoin")
    response = request("GET", BLOCKCHAIN_URL, timeout=20)
    if response.status_code >= 400:
        raise RuntimeError(f"Blockchain.com вернул HTTP {response.status_code}")
    item = (response.json() or {}).get(currency.upper()) or {}
    return _quote(
        coin_id,
        currency,
        item.get("last"),
        "Blockchain.com",
        "https://www.blockchain.com/explorer",
    )


def get_crypto_quote(coin: str, currency: str = "usd") -> Dict[str, Any]:
    """Вернуть цену, источник и время получения."""
    coin_id, currency = _validate(coin, currency)
    errors = []
    for provider in (_coingecko, _coinbase, _blockchain_info):
        try:
            return provider(coin_id, currency)
        except Exception as exc:
            errors.append(f"{provider.__name__.lstrip('_')}: {exc}")
            logger.warning("Источник %s не сработал: %s", provider.__name__, exc)
    raise RuntimeError(
        f"Не удалось получить цену {coin_id}/{currency.upper()}: " + "; ".join(errors)
    )


def get_crypto_price(coin: str, currency: str = "usd") -> float:
    """Вернуть только числовой курс (совместимость со старым кодом)."""
    return float(get_crypto_quote(coin, currency)["price"])

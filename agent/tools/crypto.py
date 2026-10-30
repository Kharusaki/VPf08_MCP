"""Инструмент «Курс криптовалюты» через бесплатный CoinGecko API."""

from __future__ import annotations

import logging
from typing import Dict

from .http_utils import get_json

logger = logging.getLogger(__name__)

COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"

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


def get_crypto_price(coin: str, currency: str = "usd") -> float:
    """Вернуть цену монеты в указанной валюте.

    Пример: get_crypto_price("bitcoin", "usd") -> 76652.0
    """
    coin = (coin or "").strip().lower()
    currency = (currency or "usd").strip().lower()

    if not coin:
        raise ValueError("Не указана криптовалюта.")
    if not currency.isalpha() or not (2 <= len(currency) <= 5):
        raise ValueError("Валюта должна быть кодом, например usd, eur или rub.")

    coin_id = COIN_ALIASES.get(coin, coin)
    data = get_json(
        COINGECKO_URL,
        params={"ids": coin_id, "vs_currencies": currency},
        timeout=20,
    )

    prices = data.get(coin_id) or {}
    price = prices.get(currency)
    if price is None:
        raise ValueError(
            f"Не удалось получить цену «{coin}» в {currency.upper()}. "
            "Проверьте название монеты (например, bitcoin или btc)."
        )
    return float(price)

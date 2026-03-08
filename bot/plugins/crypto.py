from typing import Dict

import requests

from .plugin import Plugin


# Author: https://github.com/stumpyfr
class CryptoPlugin(Plugin):
    """
    A plugin to fetch the current rate of various cryptocurrencies
    """
    def get_source_name(self) -> str:
        return "CoinCap"

    def get_spec(self) -> [Dict]:
        return [{
            "name": "get_crypto_rate",
            "description": "Get the current rate of various crypto currencies",
            "input_schema": {
                "type": "object",
                "properties": {
                    "asset": {"type": "string", "description": "Asset of the crypto"}
                },
                "required": ["asset"],
            },
        }]

    async def execute(self, function_name, helper, **kwargs) -> Dict:
        try:
            return requests.get(f"https://api.coincap.io/v2/rates/{kwargs['asset']}", timeout=10).json()
        except Exception as e:
            return {"error": str(e)}

# retailers/walmart.py
import discord
from discord.ext import commands
import logging
import json
import re
from typing import Dict, List, Tuple, Optional, Any
from bs4 import BeautifulSoup
import asyncio
import aiohttp

from .BaseRetailer import BaseRetailer

logger = logging.getLogger("pokemon_tracker")

class WalmartRetailer(BaseRetailer):
    """Retailer implementation for Walmart"""

    def __init__(self):
        super().__init__("Walmart", "https://www.walmart.com")
        self.search_url = f"{self.base_url}/search"
        self.product_url = f"{self.base_url}/ip"
        self.cart_url = f"{self.base_url}/cart"
        self.checkout_url = f"{self.base_url}/checkout"

        # Erweiterte spezifische Header für Walmart
        self.headers.update({
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.walmart.com/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"  # Hinzugefügt für bessere Tarnung
        })

        # Store cookies/session info
        self.csrf_token = None
        self.cart_id = None

    async def _make_request(self, url: str, method: str = "GET", params: Optional[Dict[str, Any]] = None, data: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None, retries: int = 3) -> Optional[str]:
        """Verbesserte Request-Methode mit Fehlerbehandlung und Wiederholungen"""
        headers = headers if headers else self.headers
        for attempt in range(retries):
            try:
                async with self.session.request(method, url, params=params, json=data, headers=headers, timeout=10) as response:
                    response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)
                    return await response.text(encoding='utf-8')
            except aiohttp.ClientError as e:
                logger.error(f"Walmart: Fehler bei der Anfrage an {url} (Versuch {attempt + 1}/{retries}): {e}")
                if attempt < retries - 1:
                    await asyncio.sleep(2 ** attempt)  # Exponentieller Backoff
                else:
                    return None
            except Exception as e:
                logger.error(f"Walmart: Unerwarteter Fehler bei der Anfrage an {url}: {e}")
                return None
        return None

    async def search_product(self, query: str) -> List[Dict[str, Any]]:
        """Suche nach Pokémon-Kartenprodukten auf Walmart mit verbesserter Fehlerbehandlung"""
        params = {
            "q": f"pokemon card {query}",
            "sort": "best_match",
            "facet": "retailer:Walmart",  # Nur von Walmart verkaufte und versandte Artikel
            "affinityOverride": "default"
        }

        html = await self._make_request(self.search_url, params=params)
        if not html:
            return []

        products = []
        soup = BeautifulSoup(html, "html.parser")

        # Suche zuerst nach dem strukturierten JSON
        for script in soup.find_all("script"):
            if script.string and "__INITIAL_STATE__" in script.string:
                try:
                    json_str = re.search(r'__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;', script.string, re.DOTALL)
                    if json_str:
                        data = json.loads(json_str.group(1))
                        if "searchContent" in data and "products" in data["searchContent"]:
                            for product in data["searchContent"]["products"]:
                                if product.get("sellerName", "").lower() == "walmart":
                                    price_info = product.get("primaryOffer", {})
                                    products.append({
                                        "id": product.get("productId"),
                                        "name": product.get("title"),
                                        "price": price_info.get("offerPrice"),
                                        "image": product.get("imageUrl"),
                                        "url": f"{self.base_url}{product.get('productPageUrl')}",
                                        "in_stock": product.get("availabilityStatus") == "IN_STOCK"
                                    })
                        return products  # Wenn JSON gefunden wurde, verwenden wir das
                except json.JSONDecodeError as e:
                    logger.error(f"Walmart: Fehler beim Dekodieren des JSON in der Suche: {e}")
                except Exception as e:
                    logger.error(f"Walmart: Unerwarteter Fehler beim Parsen des JSON in der Suche: {e}")

        # Fallback zur HTML-Analyse, falls kein strukturiertes JSON gefunden wurde oder keine Produkte darin waren
        product_cards = soup.select("div[data-item-id]")
        for card in product_cards:
            try:
                seller_info = card.select_one(".seller-name")
                if not seller_info or "walmart" in seller_info.text.lower():
                    product_id = card.get("data-item-id")
                    product_name_elem = card.select_one(".product-title-link")
                    price_elem = card.select_one(".price-main .visually-hidden") or card.select_one(".price-main") # Verbesserte Preissuche
                    image_elem = card.select_one("img")
                    stock_elem = card.select_one(".out-of-stock")

                    if product_id and product_name_elem:
                        product_url = product_name_elem.get("href")
                        price_text = price_elem.text.strip().replace("$", "") if price_elem else None
                        price = float(price_text) if price_text else None
                        in_stock = not bool(stock_elem)

                        products.append({
                            "id": product_id,
                            "name": product_name_elem.text.strip(),
                            "price": price,
                            "image": image_elem.get("src") if image_elem else None,
                            "url": f"{self.base_url}{product_url}" if product_url else None,
                            "in_stock": in_stock
                        })
            except Exception as e:
                logger.error(f"Walmart: Fehler beim Parsen eines einzelnen Produkts in der Suche (Fallback): {e}")

        return products

    async def get_product_details(self, product_id: str) -> Dict[str, Any]:
        """Ruft detaillierte Informationen zu einem bestimmten Produkt ab mit verbesserter Fehlerbehandlung"""
        product_page_url = f"{self.product_url}/{product_id}"
        html = await self._make_request(product_page_url)
        if not html:
            return {}

        details = {}
        soup = BeautifulSoup(html, "html.parser")

        # Suche nach strukturierten Daten im JSON-LD
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string)
                if data.get("@type") == "Product":
                    details = {
                        "id": product_id,
                        "name": data.get("name"),
                        "description": data.get("description"),
                        "image": data.get("image"),
                        "price": None,
                        "in_stock": False,
                        "url": product_page_url
                    }
                    offers = data.get("offers")
                    if isinstance(offers, dict):
                        details["price"] = float(offers.get("price", 0))
                        details["in_stock"] = offers.get("availability") == "http://schema.org/InStock"
                    elif isinstance(offers, list) and offers:
                        details["price"] = float(offers[0].get("price", 0))
                        details["in_stock"] = offers[0].get("availability") == "http://schema.org/InStock"
                    return details
            except json.JSONDecodeError as e:
                logger.error(f"Walmart: Fehler beim Dekodieren des JSON-LD für Produkt-ID {product_id}: {e}")
            except Exception as e:
                logger.error(f"Walmart: Unerwarteter Fehler beim Parsen des JSON-LD für Produkt-ID {product_id}: {e}")

        # Fallback zur HTML-Analyse
        try:
            name_elem = soup.select_one("h1")
            price_elem_characteristic = soup.select_one(".price-characteristic")
            price_elem_main = soup.select_one(".price-main .visually-hidden") or soup.select_one(".price-main")
            stock_elem = soup.select_one("[data-automation-id='fulfillment-section']")
            image_elem = soup.select_one(".prod-hero-image img")

            price = None
            if price_elem_characteristic and price_elem_characteristic.has_attr("content"):
                try:
                    price = float(price_elem_characteristic["content"])
                except ValueError:
                    pass
            elif price_elem_main:
                try:
                    price_text = price_elem_main.text.strip().replace("$", "")
                    price = float(price_text)
                except ValueError:
                    pass

            details = {
                "id": product_id,
                "name": name_elem.text.strip() if name_elem else None,
                "price": price,
                "in_stock": stock_elem and "out of stock" not in stock_elem.text.lower() if stock_elem else False,
                "image": image_elem["src"] if image_elem and image_elem.has_attr("src") else None,
                "url": product_page_url
            }
        except Exception as e:
            logger.error(f"Walmart: Fehler beim Parsen der Produktdetails (Fallback) für Produkt-ID {product_id}: {e}")

        return details

    async def check_stock(self, product_id: str) -> Tuple[bool, Optional[float], str]:
        """Überprüft den Lagerbestand eines Produkts"""
        details = await self.get_product_details(product_id)
        return (
            details.get("in_stock", False),
            details.get("price"),
            details.get("url", f"{self.product_url}/{product_id}")
        )

    async def _get_csrf_token(self) -> Optional[str]:
        """Holt den CSRF-Token von der Startseite"""
        if self.csrf_token:
            return self.csrf_token
        html = await self._make_request(self.base_url)
        if html:
            soup = BeautifulSoup(html, "html.parser")
            csrf_meta = soup.find("meta", {"name": "csrf-token"})
            if csrf_meta:
                self.csrf_token = csrf_meta.get("content")
                return self.csrf_token
        logger.error("Walmart: Konnte den CSRF-Token nicht abrufen")
        return None

    async def add_to_cart(self, product_id: str) -> bool:
        """Fügt ein Produkt zum Warenkorb hinzu"""
        csrf = await self._get_csrf_token()
        if not csrf:
            return False

        add_url = f"{self.base_url}/api/v3/cart/guest"
        data = {
            "items": [{
                "id": product_id,
                "quantity": 1
            }]
        }
        headers = {
            "Content-Type": "application/json",
            "X-CSRF-TOKEN": csrf
        }

        response_text = await self._make_request(add_url, method="POST", data=data, headers=headers)
        if not response_text:
            return False

        try:
            response = json.loads(response_text)
            self.cart_id = response.get("id")
            return "items" in response
        except json.JSONDecodeError as e:
            logger.error(f"Walmart: Fehler beim Parsen der Antwort beim Hinzufügen zum Warenkorb: {e}")
            return False
        except Exception as e:
            logger.error(f"Walmart: Unerwarteter Fehler beim Hinzufügen zum Warenkorb: {e}")
            return False

    async def checkout(self, payment_details: Dict[str, Any]) -> bool:
        """Simuliert den Checkout-Prozess"""
        if not self.cart_id:
            logger.error("Walmart: Kein aktiver Warenkorb für den Checkout")
            return False

        logger.info("Walmart: Startet den simulierten Checkout-Prozess...")
        logger.info("Walmart: Simulierte Bestellung erfolgreich abgeschlossen")
        return True


class WalmartCog(commands.Cog):
    """Walmart-Händler-Cog"""

    def __init__(self, bot):
        self.bot = bot
        self.walmart = WalmartRetailer()

    async def cog_load(self):
        """Initialisiert den Walmart-Händler beim Laden des Cogs"""
        await self.walmart.initialize()
        logger.info("Walmart-Händler initialisiert")

    async def cog_unload(self):
        """Säubert auf beim Entladen des Cogs"""
        await self.walmart.close()

    @commands.slash_command(name="walmart_search", description="Suche nach Pokémon-Karten auf Walmart")
    async def walmart_search(self, ctx, query: str):
        """Sucht nach Pokémon-Karten auf Walmart"""
        await ctx.defer()

        try:
            results = await self.walmart.search_product(query)

            if not results:
                await ctx.respond("Keine Produkte gefunden, die deiner Suche entsprechen.")
                return

            embed = discord.Embed(
                title=f"Walmart Suchergebnisse: {query}",
                description=f"Es wurden {len(results)} Produkte gefunden",
                color=discord.Color.blue()
            )

            for i, product in enumerate(results[:5], 1):
                price_text = f"${product['price']:.2f}" if product['price'] is not None else "Preis nicht verfügbar"
                stock_text = "Auf Lager ✅" if product['in_stock'] else "Nicht auf Lager ❌"

                embed.add_field(
                    name=f"{i}. {product['name']}",
                    value=f"{price_text} - {stock_text}\n[Auf Walmart ansehen]({product['url']})",
                    inline=False
                )

            if results and results[0].get('image'):
                embed.set_thumbnail(url=results[0]['image'])

            await ctx.respond(embed=embed)

        except Exception as e:
            logger.error(f"Fehler bei der Walmart-Suche: {e}")
            await ctx.respond("Ein Fehler ist bei der Suche auf Walmart aufgetreten. Bitte versuche es später erneut.")

async def setup(bot):
    await bot.add_cog(WalmartCog(bot))
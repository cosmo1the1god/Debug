# retailers/target.py
import discord
from discord.ext import commands

import urllib
import logging
import json
import re
from typing import Dict, List, Tuple, Optional, Any
from bs4 import BeautifulSoup
import aiohttp
from urllib.parse import urljoin
import urllib.parse

from .BaseRetailer import BaseRetailer, extract_price, is_in_stock_keyword, is_out_of_stock_keyword, clean_product_name

logger = logging.getLogger("pokemon_tracker.retailers.target")


class TargetRetailer(BaseRetailer):
    """Retailer implementation for Target"""

    def __init__(self):
        super().__init__("Target", "https://www.target.com")
        self.search_url = f"{self.base_url}/s"
        self.product_url = f"{self.base_url}/p"
        self.cart_url = f"{self.base_url}/co-cart"
        self.checkout_url = f"{self.base_url}/co-checkout"
        self.api_key = "9f36aeafbe60771e321a7cc95a78140772ab3e96"  # Public API key found in Target's JS

        # Update headers for Target
        self.headers.update({
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.target.com/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            "Target-Client-Id": "TARGETCOM"
        })

        # Target is more aggressive with rate limiting, increase delay
        self.request_delay = 2.0

    async def search_product(self, query: str) -> List[Dict[str, Any]]:
        """Search for products on Target"""
        # Construct the search URL
        params = {
            "searchTerm": f"pokemon tcg {query}",
            "category": "5xtg6",  # Target's category ID for trading cards
            "sortBy": "relevance"
        }

        search_url = f"{self.search_url}/{urllib.parse.quote(f'pokemon tcg {query}')}"
        html = await self._make_request(search_url, params=params)
        if not html:
            return []

        products = []
        soup = BeautifulSoup(html, "html.parser")

        # Look for the JSON data containing product information
        for script in soup.find_all("script", type="application/json"):
            try:
                data = json.loads(script.string)
                if "__PRELOADED_QUERIES__" in data:
                    # Extract product data from Target's PRELOADED_QUERIES
                    query_data = data["__PRELOADED_QUERIES__"]
                    for key in query_data:
                        if "search" in key.lower():
                            search_results = query_data[key]["data"]["search"]
                            if "products" in search_results:
                                for product in search_results["products"]:
                                    try:
                                        product_id = product.get("tcin")
                                        title = product.get("title")
                                        price = None

                                        # Extract price information
                                        price_data = product.get("price", {})
                                        if "current_retail" in price_data:
                                            price = float(price_data["current_retail"])
                                        elif "formatted_current_price" in price_data:
                                            price = extract_price(price_data["formatted_current_price"])

                                        # Check availability
                                        in_stock = False
                                        if "availability" in product:
                                            availability = product["availability"]
                                            in_stock = availability.get("availability_status") == "IN_STOCK"

                                        # Get product URL
                                        url = f"{self.base_url}/p/{product_id}"
                                        if "url" in product:
                                            url = urljoin(self.base_url, product["url"])

                                        # Get primary image
                                        image = None
                                        if "images" in product and product["images"]:
                                            image = product["images"][0].get("base_url")

                                        # Ensure it's Target sold & shipped
                                        is_target_sold = True  # Default to True for simplicity
                                        if "fulfillment" in product:
                                            fulfillment = product["fulfillment"]
                                            if "is_marketplace" in fulfillment:
                                                is_target_sold = not fulfillment["is_marketplace"]

                                        if is_target_sold:  # Only include Target sold items
                                            products.append({
                                                "id": product_id,
                                                "name": title,
                                                "price": price,
                                                "image": image,
                                                "url": url,
                                                "in_stock": in_stock
                                            })
                                    except Exception as e:
                                        logger.error(f"Target: Error processing product data: {e}")
            except json.JSONDecodeError as e:
                logger.error(f"Target: Error parsing JSON in search: {e}")
            except Exception as e:
                logger.error(f"Target: Unexpected error in search: {e}")

        # If no products were found via JSON, fall back to HTML parsing
        if not products:
            product_cards = soup.select("[data-test='product-card']")
            for card in product_cards:
                try:
                    # Get product link and ID
                    product_link = card.select_one("a[href^='/p/']")
                    if not product_link or not product_link.has_attr("href"):
                        continue

                    href = product_link["href"]
                    product_id_match = re.search(r'/p/([A-Z0-9-]+)', href)
                    product_id = product_id_match.group(1) if product_id_match else None

                    if not product_id:
                        continue

                    # Get product name
                    name_elem = card.select_one("[data-test='product-title']")
                    product_name = name_elem.text.strip() if name_elem else None

                    # Get price
                    price_elem = card.select_one("[data-test='product-price']")
                    price = None
                    if price_elem:
                        price = extract_price(price_elem.text)

                    # Check stock status - looking for "Out of Stock" indicator
                    stock_elem = card.select_one("[data-test='outOfStock']")
                    in_stock = not bool(stock_elem)

                    # Check if sold by Target
                    third_party_elem = card.select_one("[data-test='thirdPartySellerName']")
                    is_target_sold = not bool(third_party_elem)

                    # Only include if sold by Target
                    if is_target_sold:
                        # Construct URL
                        url = f"{self.base_url}{href}" if href.startswith("/") else href

                        # Get image
                        img_elem = card.select_one("img")
                        image_url = img_elem["src"] if img_elem and img_elem.has_attr("src") else None

                        products.append({
                            "id": product_id,
                            "name": product_name,
                            "price": price,
                            "image": image_url,
                            "url": url,
                            "in_stock": in_stock
                        })
                except Exception as e:
                    logger.error(f"Target: Error parsing product card: {e}")

        return products

    async def get_product_details(self, product_id: str) -> Dict[str, Any]:
        """Get detailed information for a specific product"""
        product_url = f"{self.product_url}/{product_id}"
        html = await self._make_request(product_url)
        if not html:
            return {}

        details = {
            "id": product_id,
            "name": None,
            "price": None,
            "description": None,
            "in_stock": False,
            "url": product_url,
            "image": None
        }

        soup = BeautifulSoup(html, "html.parser")

        # Try to find product data in JSON
        for script in soup.find_all("script", type="application/json"):
            try:
                data = json.loads(script.string)
                if "__PRELOADED_QUERIES__" in data:
                    query_data = data["__PRELOADED_QUERIES__"]
                    for key in query_data:
                        if "product" in key.lower() and "pdp" in key.lower():
                            product_data = query_data[key]["data"]["product"]
                            if product_data:
                                # Extract product details
                                details["name"] = product_data.get("title")

                                # Get description
                                if "description" in product_data:
                                    details["description"] = product_data["description"]
                                elif "bullet_descriptions" in product_data:
                                    details["description"] = " ".join(product_data["bullet_descriptions"])

                                # Get price
                                if "price" in product_data:
                                    price_data = product_data["price"]
                                    if "current_retail" in price_data:
                                        details["price"] = float(price_data["current_retail"])
                                    elif "formatted_current_price" in price_data:
                                        details["price"] = extract_price(price_data["formatted_current_price"])

                                # Check stock status
                                if "availability" in product_data:
                                    availability = product_data["availability"]
                                    details["in_stock"] = availability.get("availability_status") == "IN_STOCK"

                                # Get primary image
                                if "images" in product_data and product_data["images"]:
                                    details["image"] = product_data["images"][0].get("base_url")
            except json.JSONDecodeError as e:
                logger.error(f"Target: Error parsing JSON in product details: {e}")
            except Exception as e:
                logger.error(f"Target: Unexpected error in product details: {e}")

        # Fallback to HTML parsing if needed
        if not details["name"]:
            try:
                # Get product name
                name_elem = soup.select_one("[data-test='product-title']")
                if name_elem:
                    details["name"] = name_elem.text.strip()

                # Get price
                price_elem = soup.select_one("[data-test='product-price']")
                if price_elem:
                    details["price"] = extract_price(price_elem.text)

                # Check stock status
                stock_elem = soup.select_one("[data-test='outOfStock']")
                details["in_stock"] = not bool(stock_elem)

                # Get description
                desc_elem = soup.select_one("[data-test='detailsTab'] div")
                if desc_elem:
                    details["description"] = desc_elem.text.strip()

                # Get image
                img_elem = soup.select_one("[data-test='product-image'] img")
                if img_elem and img_elem.has_attr("src"):
                    details["image"] = img_elem["src"]
            except Exception as e:
                logger.error(f"Target: Error parsing HTML in product details: {e}")

        return details

    async def check_stock(self, product_id: str) -> Tuple[bool, Optional[float], str]:
        """Check if a product is in stock"""
        details = await self.get_product_details(product_id)
        return details.get("in_stock", False), details.get("price"), details.get("url",
                                                                                 f"{self.product_url}/{product_id}")

    async def add_to_cart(self, product_id: str) -> bool:
        """Add product to cart (simulated)"""
        logger.info(f"Target: Simulating adding product {product_id} to cart")

        # In a real implementation, this would make a POST request to Target's cart API
        # For simulation purposes, we'll just check if the product is in stock
        in_stock, _, _ = await self.check_stock(product_id)

        # If in stock, simulate success
        if in_stock:
            logger.info(f"Target: Successfully added {product_id} to cart (simulated)")
            return True
        else:
            logger.warning(f"Target: Failed to add {product_id} to cart - out of stock")
            return False

    async def checkout(self, payment_details: Dict[str, Any]) -> bool:
        """Perform checkout (simulated)"""
        logger.info("Target: Simulating checkout process")

        # This would be a multi-step process in a real implementation
        # 1. Navigate to cart
        # 2. Proceed to checkout
        # 3. Fill shipping information
        # 4. Fill payment information
        # 5. Submit order

        # For simulation, we'll just return success
        logger.info("Target: Checkout simulation completed successfully")
        return True


class TargetCog(commands.Cog):
    """Target retailer cog"""

    def __init__(self, bot):
        self.bot = bot
        self.target = TargetRetailer()

    async def cog_load(self):
        """Initialize Target retailer when cog is loaded"""
        await self.target.initialize()
        logger.info("Target retailer initialized")

    async def cog_unload(self):
        """Clean up when cog is unloaded"""
        await self.target.close()

    @commands.slash_command(name="target_search", description="Search for Pokémon cards on Target")
    async def target_search(self, ctx, query: str):
        """Search for Pokémon cards on Target"""
        await ctx.defer()

        try:
            results = await self.target.search_product(query)

            if not results:
                await ctx.respond("No products found matching your search.")
                return

            embed = discord.Embed(
                title=f"Target Search Results: {query}",
                description=f"Found {len(results)} products",
                color=discord.Color.red()
            )

            for i, product in enumerate(results[:5], 1):
                price_text = f"${product['price']:.2f}" if product['price'] is not None else "Price unavailable"
                stock_text = "In Stock ✅" if product['in_stock'] else "Out of Stock ❌"

                embed.add_field(
                    name=f"{i}. {product['name']}",
                    value=f"{price_text} - {stock_text}\n[View on Target]({product['url']})",
                    inline=False
                )

            if results and results[0].get('image'):
                embed.set_thumbnail(url=results[0]['image'])

            await ctx.respond(embed=embed)

        except Exception as e:
            logger.error(f"Error during Target search: {e}")
            await ctx.respond("An error occurred while searching Target. Please try again later.")


async def setup(bot):
    await bot.add_cog(TargetCog(bot))
from abc import ABC, abstractmethod
from bs4 import BeautifulSoup
from typing import List, Dict, Any, Tuple, Optional
import logging
import aiohttp
import asyncio
import discord
from discord.ext import commands
import json
import os
import random
import time
from urllib.parse import urljoin

logger = logging.getLogger("pokemon_tracker")
logger.setLevel(logging.INFO)

# --- Utility Functions ---

def extract_price(text: Optional[str]) -> Optional[float]:
    """Extracts a float price from a string."""
    if text:
        cleaned_text = text.replace("$", "").replace("€", "").replace("£", "").replace(",", "").strip()
        try:
            return float(cleaned_text)
        except ValueError:
            return None
    return None

def is_in_stock_keyword(text: Optional[str]) -> bool:
    """Checks if a text indicates an item is in stock."""
    return bool(text and any(keyword in text.lower() for keyword in ["in stock", "available"]))

def is_out_of_stock_keyword(text: Optional[str]) -> bool:
    """Checks if a text indicates an item is out of stock."""
    return bool(text and any(keyword in text.lower() for keyword in ["out of stock", "unavailable"]))

def clean_product_name(name: Optional[str]) -> Optional[str]:
    """Cleans up a product name by removing extra whitespace."""
    return name.strip() if name else None

# --- Base Retailer ---

class BaseRetailer(ABC):
    """Abstract base class for all retailer implementations"""

    def __init__(self, name: str, base_url: str):
        self.name = name
        self.base_url = base_url
        self.session: Optional[aiohttp.ClientSession] = None
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Connection": "keep-alive",
        }
        self._session_initialized = False
        self.request_delay = 0.5  # Default delay between requests

    async def initialize(self):
        """Initialize the HTTP session."""
        if not self._session_initialized:
            self.session = aiohttp.ClientSession(headers=self.headers)
            self._session_initialized = True
            logger.info(f"{self.name}: HTTP session initialized")

    async def close(self):
        """Close the HTTP session."""
        if self.session:
            await self.session.close()
            self.session = None
            self._session_initialized = False
            logger.info(f"{self.name}: HTTP session closed")

    async def _make_request(self, url: str, method: str = "GET", data: Optional[Dict] = None,
                            params: Optional[Dict] = None, headers: Optional[Dict] = None,
                            retries: int = 3, delay: float = 1) -> Optional[str]:
        """Make an HTTP request with retries and exponential backoff."""
        await self.initialize()
        _headers = self.headers.copy()
        if headers:
            _headers.update(headers)

        for attempt in range(retries):
            try:
                if method.upper() == "GET":
                    async with self.session.get(url, params=params, headers=_headers, timeout=15) as response:
                        if response.status == 200:
                            return await response.text(encoding='utf-8')
                        elif response.status == 404:
                            logger.warning(f"{self.name}: Resource not found at {url}")
                            return None
                        elif response.status == 429:  # Rate limited
                            wait_time = delay * (2 ** attempt) + random.uniform(0, 1)
                            logger.warning(f"{self.name}: Rate limited. Retrying in {wait_time:.2f} seconds...")
                            await asyncio.sleep(wait_time)
                        elif response.status >= 500:  # Server errors
                            wait_time = delay * (2 ** attempt) + random.uniform(1, 3)
                            logger.error(f"{self.name}: Server error ({response.status}) at {url}. Retrying in {wait_time:.2f} seconds...")
                            await asyncio.sleep(wait_time)
                        else:
                            logger.error(f"{self.name}: GET request failed with status {response.status} at {url}")
                            return None
                elif method.upper() == "POST":
                    async with self.session.post(url, json=data, params=params, headers=_headers, timeout=15) as response:
                        if response.status in (200, 201):
                            return await response.text(encoding='utf-8')
                        elif response.status >= 500:
                            wait_time = delay * (2 ** attempt) + random.uniform(1, 3)
                            logger.error(f"{self.name}: Server error ({response.status}) during POST to {url}. Retrying in {wait_time:.2f} seconds...")
                            await asyncio.sleep(wait_time)
                        else:
                            logger.error(f"{self.name}: POST request failed with status {response.status} at {url}")
                            return None
            except aiohttp.ClientError as e:
                logger.error(f"{self.name}: Client error during request to {url}: {e}")
                await asyncio.sleep(delay)
            except asyncio.TimeoutError:
                logger.error(f"{self.name}: Request to {url} timed out (attempt {attempt + 1}/{retries})")
                await asyncio.sleep(delay)
            except Exception as e:
                logger.error(f"{self.name}: An unexpected error occurred during request to {url}: {e}")
                await asyncio.sleep(delay)
            finally:
                await asyncio.sleep(self.request_delay) # Respect a basic delay

        logger.error(f"{self.name}: All {retries} attempts to request {url} failed")
        return None

    @abstractmethod
    async def search_product(self, query: str) -> List[Dict[str, Any]]:
        """Abstract method to search for products."""
        raise NotImplementedError

    @abstractmethod
    async def get_product_details(self, product_id: str) -> Dict[str, Any]:
        """Abstract method to get product details."""
        raise NotImplementedError

    @abstractmethod
    async def check_stock(self, product_id: str) -> Tuple[bool, Optional[float], str]:
        """Abstract method to check product stock."""
        raise NotImplementedError

    @abstractmethod
    async def add_to_cart(self, product_id: str) -> bool:
        """Abstract method to add product to cart."""
        raise NotImplementedError

    @abstractmethod
    async def checkout(self, payment_details: Dict[str, Any]) -> bool:
        """Abstract method to perform checkout."""
        raise NotImplementedError

# --- Retailer Cog ---

class RetailerCog(commands.Cog):
    """Cog for managing multiple retailer instances"""

    def __init__(self, bot):
        self.bot = bot
        self.retailers: Dict[str, BaseRetailer] = {}
        self.load_retailers()
        self.notification_queue: asyncio.Queue[Tuple[int, str]] = asyncio.Queue() # (user_id, message)
        self.bot.loop.create_task(self._process_notifications())

    def load_retailers(self):
        """Load retailer implementations dynamically"""
        import importlib
        retailers_dir = os.path.dirname(os.path.abspath(__file__))
        for filename in os.listdir(retailers_dir):
            if filename.endswith(".py") and filename != "BaseRetailer.py" and filename != "__init__.py":
                module_name = f"retailers.{filename[:-3]}"
                try:
                    module = importlib.import_module(module_name)
                    for name, obj in module.__dict__.items():
                        if isinstance(obj, type) and issubclass(obj, BaseRetailer) and obj != BaseRetailer:
                            try:
                                retailer_instance = obj()
                                self.retailers[retailer_instance.name.lower()] = retailer_instance
                                logger.info(f"Loaded retailer: {retailer_instance.name} from {module_name}.{name}")
                            except Exception as e:
                                logger.error(f"Error instantiating retailer {name} from {module_name}: {e}")
                except ImportError as e:
                    logger.error(f"Error importing module {module_name}: {e}")
                except Exception as e:
                    logger.error(f"Error loading retailer from {module_name}: {e}")
        logger.info(f"Loaded {len(self.retailers)} retailers.")

    async def cog_load(self):
        """Initialize retailers when the cog is loaded"""
        init_tasks = [retailer.initialize() for retailer in self.retailers.values()]
        await asyncio.gather(*init_tasks)
        logger.info("All retailers initialized on cog load.")

    async def cog_unload(self):
        """Close retailer sessions when the cog is unloaded"""
        close_tasks = [retailer.close() for retailer in self.retailers.values()]
        await asyncio.gather(*close_tasks)
        logger.info("All retailer sessions closed on cog unload.")

    async def _process_notifications(self):
        """Processes notifications to Discord users."""
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            user_id, message = await self.notification_queue.get()
            user = self.bot.get_user(user_id)
            if user:
                try:
                    await user.send(message)
                    logger.info(f"Notification sent to user {user_id}: {message[:50]}...")
                except discord.DiscordServerError as e:
                    logger.error(f"Discord server error sending notification to {user_id}: {e}")
                except discord.Forbidden as e:
                    logger.error(f"Could not send notification to {user_id} (forbidden): {e}")
                except Exception as e:
                    logger.error(f"Error sending notification to {user_id}: {e}")
            else:
                logger.warning(f"User {user_id} not found, cannot send notification: {message[:50]}...")
            self.notification_queue.task_done()
            await asyncio.sleep(1) # Simple rate limit for notifications

    async def get_retailer(self, name: str) -> Optional[BaseRetailer]:
        """Retrieve a retailer by its name."""
        return self.retailers.get(name.lower())

    @commands.slash_command(name="search", description="Search for a product across multiple retailers.")
    async def search_command(self, ctx: discord.ApplicationContext, query: str):
        """Discord command to search across retailers."""
        await ctx.defer()
        results = {}
        search_tasks = {name: retailer.search_product(query) for name, retailer in self.retailers.items()}
        completed, pending = await asyncio.wait(search_tasks.values(), return_when=asyncio.ALL_COMPLETED)

        for name, task in search_tasks.items():
            try:
                retailer_results = task.result()
                if retailer_results:
                    results[name] = retailer_results
            except Exception as e:
                logger.error(f"Error during search on {name}: {e}")

        if not results:
            await ctx.respond(f"No products found for '{query}' across any retailers.")
            return

        embed = discord.Embed(title=f"Search Results for '{query}'", color=discord.Color.blue())
        for retailer_name, product_list in results.items():
            if product_list:
                product_strings = [f"[{clean_product_name(p.get('name', 'N/A'))}]({p.get('url', '#')}) - ${p.get('price', 'N/A')}" for p in product_list[:5]] # Limit to top 5
                if product_strings:
                    embed.add_field(name=retailer_name, value="\n".join(product_strings), inline=False)
                else:
                    embed.add_field(name=retailer_name, value="No products found.", inline=False)

        await ctx.respond(embed=embed)

    @commands.slash_command(name="stock", description="Check stock for a product at specific retailers.")
    async def stock_command(self, ctx: discord.ApplicationContext, retailers: str, product_id: str):
        """Discord command to check stock."""
        await ctx.defer()
        retailer_list = [r.strip().lower() for r in retailers.split(',')]
        results = {}
        stock_tasks = {}
        for name, retailer in self.retailers.items():
            if name in retailer_list:
                stock_tasks[name] = retailer.check_stock(product_id)

        completed, pending = await asyncio.wait(stock_tasks.values(), return_when=asyncio.ALL_COMPLETED)

        for name, task in stock_tasks.items():
            try:
                in_stock, price, url = task.result()
                results[name] = (in_stock, price, url)
            except Exception as e:
                logger.error(f"Error checking stock at {name} for {product_id}: {e}")
                results[name] = (False, None, None)

        embed = discord.Embed(title=f"Stock Check for Product ID '{product_id}'", color=discord.Color.green())
        for name, (in_stock, price, url) in results.items():
            stock_status = "✅ In Stock" if in_stock else "❌ Out of Stock"
            price_str = f"${price:.2f}" if price is not None else "N/A"
            embed.add_field(name=name.capitalize(), value=f"Status: {stock_status}\nPrice: {price_str}\n[View Product]({url})", inline=False)

        await ctx.respond(embed=embed)

    @commands.slash_command(name="buy", description="Attempt to purchase a product from a specific retailer (simulated).")
    async def buy_command(self, ctx: discord.ApplicationContext, retailer: str, product_id: str, payment_info: str):
        """Discord command to trigger a simulated purchase."""
        await ctx.defer(ephemeral=True)
        retailer_instance = await self.get_retailer(retailer)
        if not retailer_instance:
            await ctx.respond(f"Retailer '{retailer}' not found.", ephemeral=True)
            return

        payment_details = {"info": payment_info} # Replace with secure handling in real app

        try:
            in_stock, price, _ = await retailer_instance.check_stock(product_id)
            if not in_stock:
                await ctx.respond(f"Product '{product_id}' is out of stock at {retailer}.", ephemeral=True)
                return

            await ctx.respond(f"Attempting to purchase '{product_id}' at {retailer}...", ephemeral=True)

            if not await retailer_instance.add_to_cart(product_id):
                await ctx.followup.send(f"Failed to add '{product_id}' to cart at {retailer}.", ephemeral=True)
                return

            success = await retailer_instance.checkout(payment_details)
            if success:
                await ctx.followup.send(f"Simulated purchase successful for '{product_id}' at {retailer}!", ephemeral=True)
            else:
                await ctx.followup.send(f"Simulated checkout failed for '{product_id}' at {retailer}.", ephemeral=True)

        except Exception as e:
            logger.error(f"Error during simulated purchase at {retailer} for {product_id}: {e}")
            await ctx.followup.send(f"An error occurred during the simulated purchase: {e}", ephemeral=True)

    @commands.slash_command(name="track", description="Track a product for price or stock changes.")
    async def track_command(self, ctx: discord.ApplicationContext, retailer: str, product_id: str, track_price: Optional[float] = None, track_stock: Optional[bool] = True):
        """Discord command to track a product."""
        await ctx.respond("Tracking functionality is under development.", ephemeral=True) # Placeholder

async def setup(bot):
    await bot.add_cog(RetailerCog(bot))
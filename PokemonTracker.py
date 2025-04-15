import discord
from discord.ext import commands, tasks
from discord import SlashCommandGroup, Option, ApplicationContext
from typing import List, Dict, Optional
from datetime import datetime
import asyncio
import logging
import yaml
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("pokemon_tracker.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("pokemon_tracker")


class RetailerDatabase:
    """Simulated database for retailers and product tracking"""

    def __init__(self):
        # Available retailers
        self.retailers = [
            "Walmart", "Target", "Pokémon Center", "Amazon",
            "GameStop", "Best Buy", "PaladinCards", "ThePokeCave"
        ]

        # Product categories
        self.product_categories = [
            "Booster Box", "Elite Trainer Box", "Booster Pack",
            "Special Collection", "Tin", "Promo Card", "Singles"
        ]

        # Sample products for each retailer
        self.products_by_retailer = {
            "Walmart": ["Scarlet & Violet ETB", "Paldean Fates Booster Box", "Charizard ex Box"],
            "Target": ["Brilliant Stars ETB", "Temporal Forces Booster Pack", "Celebrations Tin"],
            "Pokémon Center": ["Tera Charizard ex Premium Collection", "Paldean Fates ETB Plus",
                               "Scarlet & Violet 151 Booster Box"],
            "Amazon": ["Paradox Rift Booster Box", "Obsidian Flames ETB", "Pokémon TCG: Trick or Trade BOOster Bundle"],
            "GameStop": ["Temporal Forces ETB", "Scarlet & Violet 151 Mini Tin", "Paradox Rift 3-Pack Blister"],
            "Best Buy": ["Paldean Fates Tin", "Scarlet & Violet Crown Zenith ETB", "Pokémon TCG Battle Academy"],
            "PaladinCards": ["Neo Genesis Singles", "Temporal Forces Singles", "Charizard VMAX Rainbow Rare"],
            "ThePokeCave": ["Classic Collection Box", "Pikachu ex Box", "Mimikyu V Box"]
        }

        # Track user preferences
        self.user_tracking = {}  # User ID -> list of products they're tracking
        self.user_buylists = {}  # User ID -> dict of products with price thresholds
        self.user_channels = {}  # User ID -> channel ID for notifications
        self.user_settings = {}  # User ID -> dict of settings

        # Simulated stock status
        self.stock_status = {}  # Product -> (in_stock, price, url)

        # Initialize sample stock data
        self._initialize_sample_stock()

    def _initialize_sample_stock(self):
        """Initialize sample stock data"""
        for retailer, products in self.products_by_retailer.items():
            for product in products:
                product_key = f"{retailer}:{product}"
                # Set random stock status (70% out of stock for realism)
                import random
                in_stock = random.random() > 0.7
                price = round(random.uniform(20.0, 120.0), 2) if in_stock else None
                url = f"https://example.com/{retailer.lower()}/{product.lower().replace(' ', '-')}"
                self.stock_status[product_key] = (in_stock, price, url)

    def get_products_by_retailer(self, retailer):
        """Get products for a specific retailer"""
        return self.products_by_retailer.get(retailer, [])

    def add_to_tracking(self, user_id, product):
        """Add product to user's tracking list"""
        if user_id not in self.user_tracking:
            self.user_tracking[user_id] = []

        if product not in self.user_tracking[user_id]:
            self.user_tracking[user_id].append(product)
            return True
        return False

    def remove_from_tracking(self, user_id, product):
        """Remove product from user's tracking list"""
        if user_id in self.user_tracking and product in self.user_tracking[user_id]:
            self.user_tracking[user_id].remove(product)
            return True
        return False

    def get_tracking_list(self, user_id):
        """Get user's tracking list"""
        return self.user_tracking.get(user_id, [])

    def add_to_buylist(self, user_id, product, price_threshold):
        """Add product to user's buylist with price threshold"""
        if user_id not in self.user_buylists:
            self.user_buylists[user_id] = {}

        self.user_buylists[user_id][product] = price_threshold
        return True

    def remove_from_buylist(self, user_id, product):
        """Remove product from user's buylist"""
        if user_id in self.user_buylists and product in self.user_buylists[user_id]:
            del self.user_buylists[user_id][product]
            return True
        return False

    def get_buylist(self, user_id):
        """Get user's buylist with price thresholds"""
        return self.user_buylists.get(user_id, {})

    def set_notification_channel(self, user_id, channel_id):
        """Set notification channel for user"""
        self.user_channels[user_id] = channel_id
        return True

    def get_notification_channel(self, user_id):
        """Get notification channel for user"""
        return self.user_channels.get(user_id)

    def check_stock(self, product):
        """Check if product is in stock"""
        return self.stock_status.get(product, (False, None, None))

    def simulate_stock_change(self):
        """Simulate stock status change for testing"""
        import random
        products = list(self.stock_status.keys())
        if products:
            product = random.choice(products)
            in_stock = not self.stock_status[product][0]  # Flip current status
            price = round(random.uniform(20.0, 120.0), 2) if in_stock else None
            url = self.stock_status[product][2]
            self.stock_status[product] = (in_stock, price, url)
            return product, in_stock, price, url
        return None, None, None, None


class PokemonTracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = RetailerDatabase()  # Initialize our database
        self.last_backup = None
        self.check_stock_task.start()  # Start the stock checking task

    def cog_unload(self):
        self.check_stock_task.cancel()  # Ensure we stop the task when unloading the cog

    # Create command groups
    track = SlashCommandGroup(name="track", description="Manage product tracking")
    buylist = SlashCommandGroup(name="buylist", description="Manage product buylist")
    settings = SlashCommandGroup(name="settings", description="Manage user settings")

    # Autocomplete helper functions
    async def retailer_autocomplete(self, ctx: discord.AutocompleteContext):
        """Returns retailers that match what the user has typed so far."""
        return [retailer for retailer in self.db.retailers
                if retailer.lower().startswith(ctx.value.lower())]

    async def product_autocomplete(self, ctx: discord.AutocompleteContext):
        """Returns products for a specific retailer."""
        retailer = ctx.options.get("retailer")
        if not retailer or retailer not in self.db.retailers:
            return []
        return [product for product in self.db.get_products_by_retailer(retailer)
                if product.lower().startswith(ctx.value.lower())]

    async def tracked_product_autocomplete(self, ctx: discord.AutocompleteContext):
        """Returns user's tracked products that match what they've typed."""
        user_id = ctx.interaction.user.id
        tracking_list = self.db.get_tracking_list(user_id)
        return [product for product in tracking_list
                if product.lower().startswith(ctx.value.lower())]

    async def buylist_product_autocomplete(self, ctx: discord.AutocompleteContext):
        """Returns user's buylist products that match what they've typed."""
        user_id = ctx.interaction.user.id
        buylist = self.db.get_buylist(user_id)
        return [product for product in buylist.keys()
                if product.lower().startswith(ctx.value.lower())]

    # Track command implementations
    @track.command(name="add", description="Add a product to your tracking list")
    async def track_add(
            self,
            ctx: ApplicationContext,
            retailer: Option(str, "Select a retailer",
                             autocomplete=retailer_autocomplete,
                             required=True),
            product: Option(str, "Select a product",
                            autocomplete=product_autocomplete,
                            required=True)
    ):
        """Add a product to your tracking list"""
        full_product = f"{retailer}:{product}"
        success = self.db.add_to_tracking(ctx.author.id, full_product)

        if success:
            # Check stock immediately
            in_stock, price, url = self.db.check_stock(full_product)
            status = "in stock" if in_stock else "out of stock"
            price_text = f"Price: ${price}" if price else ""

            embed = discord.Embed(
                title="Product Added to Tracking",
                description=f"Added {product} from {retailer} to your tracking list.",
                color=discord.Color.green()
            )
            embed.add_field(name="Status", value=f"Currently {status} {price_text}", inline=False)
            if url:
                embed.add_field(name="Link", value=url, inline=False)

            await ctx.respond(embed=embed)
        else:
            await ctx.respond(f"You're already tracking {product} from {retailer}.", ephemeral=True)

    @track.command(name="remove", description="Remove a product from your tracking list")
    async def track_remove(
            self,
            ctx: ApplicationContext,
            product: Option(str, "Select a product to remove",
                            autocomplete=tracked_product_autocomplete,
                            required=True)
    ):
        """Remove a product from your tracking list"""
        success = self.db.remove_from_tracking(ctx.author.id, product)

        if success:
            retailer, item = product.split(":", 1)
            embed = discord.Embed(
                title="Product Removed",
                description=f"Removed {item} from {retailer} from your tracking list.",
                color=discord.Color.red()
            )
            await ctx.respond(embed=embed)
        else:
            await ctx.respond("You're not tracking this product.", ephemeral=True)

    @track.command(name="list", description="List all products you're tracking")
    async def track_list(self, ctx: ApplicationContext):
        """List all products you're tracking"""
        tracking_list = self.db.get_tracking_list(ctx.author.id)

        if not tracking_list:
            await ctx.respond("You're not tracking any products currently.", ephemeral=True)
            return

        embed = discord.Embed(
            title="Your Tracked Products",
            description="Here are the products you're currently tracking:",
            color=discord.Color.blue()
        )

        # Group products by retailer
        by_retailer = {}
        for product in tracking_list:
            retailer, item = product.split(":", 1)
            if retailer not in by_retailer:
                by_retailer[retailer] = []
            by_retailer[retailer].append(item)

        # Add fields for each retailer
        for retailer, products in by_retailer.items():
            products_list = "\n".join([f"• {item}" for item in products])
            embed.add_field(name=retailer, value=products_list, inline=False)

        await ctx.respond(embed=embed)

    # Buylist command implementations
    @buylist.command(name="add", description="Add a product to your buylist with a price threshold")
    async def buylist_add(
            self,
            ctx: ApplicationContext,
            retailer: Option(str, "Select a retailer",
                             autocomplete=retailer_autocomplete,
                             required=True),
            product: Option(str, "Select a product",
                            autocomplete=product_autocomplete,
                            required=True),
            price: Option(float, "Maximum price to automatically purchase",
                          required=True, min_value=0.01)
    ):
        """Add a product to your buylist with a price threshold"""
        full_product = f"{retailer}:{product}"
        success = self.db.add_to_buylist(ctx.author.id, full_product, price)

        if success:
            embed = discord.Embed(
                title="Product Added to Buylist",
                description=f"Added {product} from {retailer} to your auto-purchase list.",
                color=discord.Color.green()
            )
            embed.add_field(name="Price Threshold", value=f"Will auto-buy if price is at or below ${price:.2f}",
                            inline=False)
            await ctx.respond(embed=embed)
        else:
            await ctx.respond("Failed to add product to buylist.", ephemeral=True)

    @buylist.command(name="remove", description="Remove a product from your buylist")
    async def buylist_remove(
            self,
            ctx: ApplicationContext,
            product: Option(str, "Select a product to remove",
                            autocomplete=buylist_product_autocomplete,
                            required=True)
    ):
        """Remove a product from your buylist"""
        success = self.db.remove_from_buylist(ctx.author.id, product)

        if success:
            retailer, item = product.split(":", 1)
            embed = discord.Embed(
                title="Product Removed",
                description=f"Removed {item} from {retailer} from your buylist.",
                color=discord.Color.red()
            )
            await ctx.respond(embed=embed)
        else:
            await ctx.respond("This product is not in your buylist.", ephemeral=True)

    @buylist.command(name="list", description="List all products in your buylist")
    async def buylist_list(self, ctx: ApplicationContext):
        """List all products in your buylist"""
        buylist = self.db.get_buylist(ctx.author.id)

        if not buylist:
            await ctx.respond("You don't have any products in your buylist.", ephemeral=True)
            return

        embed = discord.Embed(
            title="Your Buylist",
            description="Here are the products you've set up for auto-purchase:",
            color=discord.Color.gold()
        )

        # Group by retailer
        by_retailer = {}
        for product, price in buylist.items():
            retailer, item = product.split(":", 1)
            if retailer not in by_retailer:
                by_retailer[retailer] = []
            by_retailer[retailer].append((item, price))

        # Add fields for each retailer
        for retailer, products in by_retailer.items():
            products_list = "\n".join([f"• {item} - Max: ${price:.2f}" for item, price in products])
            embed.add_field(name=retailer, value=products_list, inline=False)

        await ctx.respond(embed=embed)

    # Settings commands
    @settings.command(name="channel", description="Set a channel for notifications")
    async def settings_channel(
            self,
            ctx: ApplicationContext,
            channel: Option(discord.TextChannel, "Select notification channel", required=True)
    ):
        """Set a channel for notifications"""
        success = self.db.set_notification_channel(ctx.author.id, channel.id)

        if success:
            embed = discord.Embed(
                title="Notification Channel Set",
                description=f"Your notifications will now be sent to {channel.mention}.",
                color=discord.Color.green()
            )
            await ctx.respond(embed=embed)
        else:
            await ctx.respond("Failed to set notification channel.", ephemeral=True)

    @commands.slash_command(name="simulate", description="Simulate a stock change (for testing)")
    async def simulate_stock_change(self, ctx: ApplicationContext):
        """Simulate a stock change (for testing)"""
        product, in_stock, price, url = self.db.simulate_stock_change()

        if product:
            retailer, item = product.split(":", 1)
            status = "in stock" if in_stock else "out of stock"
            price_text = f" at ${price:.2f}" if price else ""

            embed = discord.Embed(
                title="Stock Status Changed (Simulated)",
                description=f"{item} from {retailer} is now {status}{price_text}.",
                color=discord.Color.green() if in_stock else discord.Color.red()
            )
            if url:
                embed.add_field(name="Link", value=url, inline=False)

            await ctx.respond(embed=embed)
        else:
            await ctx.respond("No products available to simulate stock change.", ephemeral=True)

    @tasks.loop(seconds=10)  # In production, you'd want 1-2 seconds
    async def check_stock_task(self):
        """Background task to check stock status"""
        try:
            # In production, this would check actual websites
            # For demo purposes, we'll just simulate a random change occasionally
            import random
            if random.random() < 0.2:  # 20% chance to simulate a change
                product, in_stock, price, url = self.db.simulate_stock_change()

                if product:
                    retailer, item = product.split(":", 1)
                    status = "in stock" if in_stock else "out of stock"
                    price_text = f" at ${price:.2f}" if price else ""

                    # Find users tracking this product
                    for user_id, tracking_list in self.db.user_tracking.items():
                        if product in tracking_list:
                            # Create notification embed
                            embed = discord.Embed(
                                title="🚨 STOCK ALERT 🚨",
                                description=f"{item} from {retailer} is now {status}{price_text}!",
                                color=discord.Color.green() if in_stock else discord.Color.red(),
                                timestamp=datetime.now()
                            )
                            if url:
                                embed.add_field(name="Link", value=url, inline=False)

                            # Determine where to send the notification
                            channel_id = self.db.get_notification_channel(user_id)
                            if channel_id:
                                channel = self.bot.get_channel(channel_id)
                                if channel:
                                    await channel.send(f"<@{user_id}>", embed=embed)

                            # Check if this is a buylist item and it's in stock at a good price
                            buylist = self.db.get_buylist(user_id)
                            if in_stock and product in buylist and price and price <= buylist[product]:
                                # Simulate auto-purchase
                                purchase_embed = discord.Embed(
                                    title="🎉 AUTO-PURCHASE SUCCESSFUL 🎉",
                                    description=f"Successfully purchased {item} from {retailer} for ${price:.2f}!",
                                    color=discord.Color.gold(),
                                    timestamp=datetime.now()
                                )
                                purchase_embed.add_field(name="Status", value="Order confirmed", inline=True)
                                purchase_embed.add_field(name="Payment", value="Complete", inline=True)

                                if channel_id:
                                    channel = self.bot.get_channel(channel_id)
                                    if channel:
                                        await channel.send(f"<@{user_id}>", embed=purchase_embed)

        except Exception as e:
            logger.error(f"Error in stock checking task: {e}")

    @check_stock_task.before_loop
    async def before_check_stock(self):
        """Wait until the bot is ready before starting the task"""
        await self.bot.wait_until_ready()


def setup(bot):
    bot.add_cog(PokemonTracker(bot))
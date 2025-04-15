# retailers/PokemonTCGAPI.py
import discord
from discord.ext import commands

import logging
import json
import aiohttp
from typing import Dict, List, Tuple, Optional, Any

from .BaseRetailer import BaseRetailer

logger = logging.getLogger("pokemon_tracker.retailers.pokemontcg_api")


class PokemonTCGAPI:
    """Interface for the Pokémon TCG API"""

    def __init__(self, api_key):
        self.base_url = "https://api.pokemontcg.io/v2"
        self.api_key = api_key
        self.session = None
        self.headers = {
            "X-Api-Key": api_key,
            "Content-Type": "application/json"
        }

    async def initialize(self):
        """Initialize the API client"""
        if self.session is None:
            self.session = aiohttp.ClientSession(headers=self.headers)
        logger.info("PokemonTCG API client initialized")

    async def close(self):
        """Close the API client session"""
        if self.session:
            await self.session.close()
            self.session = None
        logger.info("PokemonTCG API client closed")

    async def _make_request(self, endpoint, params=None):
        """Make a request to the Pokémon TCG API"""
        url = f"{self.base_url}/{endpoint}"
        try:
            async with self.session.get(url, params=params) as response:
                if response.status == 200:
                    return await response.json()
                elif response.status == 429:
                    logger.warning(f"Rate limit exceeded for PokemonTCG API: {await response.text()}")
                    return None
                else:
                    logger.error(f"Error {response.status} from PokemonTCG API: {await response.text()}")
                    return None
        except Exception as e:
            logger.error(f"Exception making request to PokemonTCG API: {e}")
            return None

    async def search_cards(self, query, page=1, page_size=10):
        """Search for cards based on query"""
        params = {
            'q': query,
            'page': page,
            'pageSize': page_size
        }
        return await self._make_request('cards', params)

    async def get_card(self, card_id):
        """Get a specific card by ID"""
        return await self._make_request(f'cards/{card_id}')

    async def get_set(self, set_id):
        """Get a specific set by ID"""
        return await self._make_request(f'sets/{set_id}')

    async def search_sets(self, query=None, page=1, page_size=10):
        """Search for sets based on query"""
        params = {
            'page': page,
            'pageSize': page_size
        }
        if query:
            params['q'] = query
        return await self._make_request('sets', params)

    async def get_card_pricing(self, card_id):
        """Get TCGPlayer pricing for a specific card"""
        card_data = await self.get_card(card_id)
        if card_data and 'data' in card_data:
            return card_data['data'].get('tcgplayer', {}).get('prices', {})
        return {}


class PokemonTCGAPICog(commands.Cog):
    """PokemonTCG API integration cog"""

    def __init__(self, bot):
        self.bot = bot
        self.tcg_api = PokemonTCGAPI(api_key="16479fee-e147-4852-87f4-191fbe34b786")

    async def cog_load(self):
        """Initialize PokemonTCG API when cog is loaded"""
        await self.tcg_api.initialize()
        logger.info("PokemonTCG API initialized")

    async def cog_unload(self):
        """Clean up when cog is unloaded"""
        await self.tcg_api.close()

    @commands.slash_command(name="card_search", description="Search for a Pokémon card")
    async def card_search(self, ctx, query: str):
        """Search for Pokémon cards by name"""
        await ctx.defer()

        try:
            # Formulate a search query that looks for cards with the given name
            search_query = f'name:"{query}"'
            results = await self.tcg_api.search_cards(search_query)

            if not results or 'data' not in results or not results['data']:
                await ctx.respond("No cards found matching your search.")
                return

            cards = results['data']
            embed = discord.Embed(
                title=f"Card Search Results: {query}",
                description=f"Found {len(cards)} cards",
                color=discord.Color.blue()
            )

            for i, card in enumerate(cards[:5], 1):
                # Get pricing if available
                prices = {}
                if 'tcgplayer' in card and 'prices' in card['tcgplayer']:
                    prices = card['tcgplayer']['prices']

                price_text = "No pricing available"
                if 'normal' in prices and 'market' in prices['normal']:
                    price_text = f"${prices['normal']['market']:.2f} (Market)"
                elif 'holofoil' in prices and 'market' in prices['holofoil']:
                    price_text = f"${prices['holofoil']['market']:.2f} (Holo Market)"

                # Get card legality
                legality_text = ""
                if 'legalities' in card:
                    legalities = []
                    for format_name, status in card['legalities'].items():
                        legalities.append(f"{format_name.capitalize()}: {status}")
                    legality_text = " | ".join(legalities)

                # Get set information
                set_info = f"Set: {card['set']['name']} ({card['set']['id'].upper()})"

                embed.add_field(
                    name=f"{i}. {card['name']} - {card['id']}",
                    value=f"{price_text}\n{set_info}\n{legality_text}",
                    inline=False
                )

            # Set thumbnail to the first card's image
            if cards and 'images' in cards[0] and 'small' in cards[0]['images']:
                embed.set_thumbnail(url=cards[0]['images']['small'])

            await ctx.respond(embed=embed)

        except Exception as e:
            logger.error(f"Error during card search: {e}")
            await ctx.respond("An error occurred while searching for cards. Please try again later.")

    @commands.slash_command(name="card_price", description="Get pricing for a specific Pokémon card")
    async def card_price(self, ctx, card_id: str):
        """Get pricing information for a specific card by ID"""
        await ctx.defer()

        try:
            card_data = await self.tcg_api.get_card(card_id)

            if not card_data or 'data' not in card_data:
                await ctx.respond(f"Card with ID '{card_id}' not found.")
                return

            card = card_data['data']
            embed = discord.Embed(
                title=f"{card['name']} ({card_id})",
                description=f"Set: {card['set']['name']} ({card['set']['id'].upper()})",
                color=discord.Color.gold()
            )

            # Add card image
            if 'images' in card and 'large' in card['images']:
                embed.set_image(url=card['images']['large'])

            # Add pricing information
            if 'tcgplayer' in card and 'prices' in card['tcgplayer']:
                prices = card['tcgplayer']['prices']

                for variant, price_data in prices.items():
                    price_details = []

                    if 'low' in price_data:
                        price_details.append(f"Low: ${price_data['low']:.2f}")
                    if 'mid' in price_data:
                        price_details.append(f"Mid: ${price_data['mid']:.2f}")
                    if 'high' in price_data:
                        price_details.append(f"High: ${price_data['high']:.2f}")
                    if 'market' in price_data:
                        price_details.append(f"Market: ${price_data['market']:.2f}")

                    # Format variant name for display
                    variant_name = variant.replace('_', ' ').title()

                    embed.add_field(
                        name=f"{variant_name} Prices",
                        value=" | ".join(price_details),
                        inline=False
                    )

                # Add TCGPlayer URL if available
                if 'url' in card['tcgplayer']:
                    embed.add_field(
                        name="Purchase",
                        value=f"[Buy on TCGPlayer]({card['tcgplayer']['url']})",
                        inline=False
                    )
            else:
                embed.add_field(
                    name="Pricing",
                    value="No pricing information available for this card.",
                    inline=False
                )

            await ctx.respond(embed=embed)

        except Exception as e:
            logger.error(f"Error getting card price: {e}")
            await ctx.respond(
                f"An error occurred while getting pricing for card ID '{card_id}'. Please try again later.")

    @commands.slash_command(name="set_search", description="Search for Pokémon card sets")
    async def set_search(self, ctx, query: str = None):
        """Search for Pokémon card sets"""
        await ctx.defer()

        try:
            # If query is provided, search for sets matching the query
            # Otherwise, get the most recent sets
            search_query = f'name:"{query}"' if query else None
            results = await self.tcg_api.search_sets(search_query)

            if not results or 'data' not in results or not results['data']:
                await ctx.respond("No sets found matching your search.")
                return

            sets = results['data']
            embed = discord.Embed(
                title=f"Set Search Results: {query}" if query else "Recent Pokémon Card Sets",
                description=f"Found {len(sets)} sets",
                color=discord.Color.blue()
            )

            for i, set_data in enumerate(sets[:10], 1):
                # Format release date
                release_date = set_data.get('releaseDate', 'Unknown')

                # Get legality information
                legality_text = ""
                if 'legalities' in set_data:
                    legalities = []
                    for format_name, status in set_data['legalities'].items():
                        legalities.append(f"{format_name.capitalize()}: {status}")
                    legality_text = " | ".join(legalities)

                embed.add_field(
                    name=f"{i}. {set_data['name']} ({set_data['id'].upper()})",
                    value=f"Series: {set_data['series']}\n"
                          f"Cards: {set_data['printedTotal']}/{set_data['total']}\n"
                          f"Released: {release_date}\n"
                          f"{legality_text}",
                    inline=False
                )

            # Set thumbnail to the first set's logo
            if sets and 'images' in sets[0] and 'logo' in sets[0]['images']:
                embed.set_thumbnail(url=sets[0]['images']['logo'])

            await ctx.respond(embed=embed)

        except Exception as e:
            logger.error(f"Error during set search: {e}")
            await ctx.respond("An error occurred while searching for sets. Please try again later.")


async def setup(bot):
    await bot.add_cog(PokemonTCGAPICog(bot))
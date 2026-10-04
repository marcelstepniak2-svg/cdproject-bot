import os
import discord
from discord.ext import commands
import yfinance as yf

# Konfiguracja uprawnień bota
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
  print(f"Zalogowano jako {bot.user}")


@bot.command()
async def kurs(ctx):
  await ctx.send("⏳ Sprawdzam dane z giełdy...")
  try:
    cdr = yf.Ticker("CDR.WA")
    data = cdr.history(period="1d")

    if data.empty:
      await ctx.send(
          "❌ Brak danych. Giełda może być zamknięta lub API nie odpowiada."
      )
      return

    cena = data["Close"].iloc[0]
    wolumen = data["Volume"].iloc[0]

    wiadomosc = (
        f"**CD Projekt S.A. (CDR)**\n"
        f"💰 Obecna cena: **{cena:.2f} PLN**\n"
        f"📊 Wolumen dzisiejszy: {int(wolumen):,} sztuk\n"
        f"*(Dane opóźnione o ok. 15 minut)*"
    )
    await ctx.send(wiadomosc)
  except Exception as e:
    await ctx.send("❌ Wystąpił błąd podczas pobierania danych.")
    print(e)


# Pobieranie tokenu ze zmiennych środowiskowych
token = os.environ.get("DISCORD_TOKEN")
if token:
  bot.run(token)
else:
  print("BŁĄD: Brak tokenu Discorda.")

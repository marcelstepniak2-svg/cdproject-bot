import os
import json
import threading
from flask import Flask
import discord
from discord.ext import commands, tasks
import yfinance as yf
import requests

# --- 1. PROSTY SERWER HTTP DLA RENDER (WEB SERVICE) ---
app = Flask('')

@app.route('/')
def home():
    return "Bot Discord działa poprawnie!"

def run_http():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- 2. KONFIGURACJA BOTA DISCORD ---
TOKEN = os.getenv("DISCORD_TOKEN")
PROG_PERCENT = 3.0 
DATA_FILE = "portfolio.json"

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

alert_high_sent = False
alert_low_sent = False

def get_cdr_data():
    """Pobiera dane CDR.WA omijając blokady Yahoo Finance"""
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    })
    ticker = yf.Ticker("CDR.WA", session=session)
    return ticker.history(period="1mo")

def load_portfolio():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_portfolio(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

portfolio = load_portfolio()

def generuj_analize_pozycji(current_price, user_data):
    if not user_data or user_data.get("shares", 0) <= 0:
        return ""
    
    shares = user_data["shares"]
    avg_price = user_data["avg_price"]
    profit_per_share = current_price - avg_price
    total_profit = profit_per_share * shares
    profit_pct = (profit_per_share / avg_price) * 100

    msg = f"\n\n👤 **TWÓJ PORTFEL:** Posiadasz **{shares:.4f}** akcji (Średnia: **{avg_price:.2f} PLN**)\n"
    msg += f"• Wynik na pozycji: **{total_profit:+.2f} PLN ({profit_pct:+.2f}%)**\n"

    if profit_pct >= 5.0:
        sell_shares = round(shares * 0.35, 4)
        sell_profit = profit_per_share * sell_shares
        msg += (
            f"🟢 **OPŁACA SIĘ CZĘŚCIOWO SPRZEDAĆ!**\n"
            f"💡 *Sugeruję realizację zysku z części pakietu (np. sprzedaż **{sell_shares} szt.** po **{current_price:.2f} PLN**).\n"
            f"Zgarniasz wtedy **+{sell_profit:.2f} PLN czystego zysku**, a pozostałe {shares - sell_shares:.4f} akcji nadal pracuje!*"
        )
    elif profit_pct <= -5.0:
        msg += "📉 *Twój pakiet jest na minusie. Dobry moment na uśrednienie ceny na XTB.*"
    else:
        msg += "🟡 *Wynik w normie. Dobre miejsce na trzymanie pozycji.*"

    return msg

@bot.event
async def on_ready():
    print(f"Zalogowano jako {bot.user}")
    check_price_alerts.start()

@bot.command()
async def kup(ctx, ilosc: float, cena: float):
    user_id = str(ctx.author.id)
    user_data = portfolio.get(user_id, {"shares": 0.0, "avg_price": 0.0})
    
    old_shares = user_data["shares"]
    old_avg = user_data["avg_price"]
    
    new_shares = old_shares + ilosc
    new_avg = ((old_shares * old_avg) + (ilosc * cena)) / new_shares
    
    portfolio[user_id] = {
        "shares": round(new_shares, 4),
        "avg_price": round(new_avg, 2)
    }
    save_portfolio(portfolio)
    
    await ctx.send(
        f"✅ **Zarejestrowano zakup na XTB!**\n"
        f"• Kupiono: **{ilosc} akcji** po **{cena:.2f} PLN**\n"
        f"• Łączny stan: **{new_shares:.4f} akcji**\n"
        f"• Nowa średnia cena zakupu: **{new_avg:.2f} PLN**"
    )

@bot.command()
async def sprzedaj(ctx, ilosc: float, cena: float):
    user_id = str(ctx.author.id)
    if user_id not in portfolio or portfolio[user_id]["shares"] <= 0:
        await ctx.send("❌ Nie masz jeszcze zarejestrowanych akcji! Użyj najpierw `!kup`.")
        return
        
    user_data = portfolio[user_id]
    current_shares = user_data["shares"]
    avg_price = user_data["avg_price"]
    
    if ilosc > current_shares:
        await ctx.send(f"❌ Próbujesz sprzedać {ilosc} akcji, a posiadasz tylko {current_shares:.4f}!")
        return
        
    realized_profit = (cena - avg_price) * ilosc
    remaining_shares = current_shares - ilosc
    
    if remaining_shares > 0:
        portfolio[user_id]["shares"] = round(remaining_shares, 4)
    else:
        del portfolio[user_id]
        
    save_portfolio(portfolio)
    
    emoji = "💰" if realized_profit >= 0 else "🔻"
    await ctx.send(
        f"✅ **Zarejestrowano sprzedaż częściową!**\n"
        f"• Sprzedano: **{ilosc} akcji** po **{cena:.2f} PLN**\n"
        f"{emoji} Zrealizowany czysty wynik: **{realized_profit:+.2f} PLN**\n"
        f"• Pozostało Ci w portfelu: **{remaining_shares:.4f} akcji**"
    )

@bot.command()
async def portfel(ctx):
    user_id = str(ctx.author.id)
    if user_id not in portfolio or portfolio[user_id]["shares"] <= 0:
        await ctx.send("ℹ️ Twój portfel jest pusty. Wpisz np. `!kup 0.5 150` aby dodać akcje.")
        return
        
    user_data = portfolio[user_id]
    shares = user_data["shares"]
    avg_price = user_data["avg_price"]
    
    try:
        df = get_cdr_data()
        current_price = df['Close'].iloc[-1] if not df.empty else avg_price
    except Exception:
        current_price = avg_price

    total_cost = shares * avg_price
    current_value = shares * current_price
    profit = current_value - total_cost
    profit_pct = ((current_price - avg_price) / avg_price) * 100

    await ctx.send(
        f"📊 **TWÓJ PORTFEL CD PROJEKT (XTB)**\n"
        f"• Posiadane akcje: **{shares:.4f} szt.**\n"
        f"• Średnia cena zakupu: **{avg_price:.2f} PLN**\n"
        f"• Zainwestowano: **{total_cost:.2f} PLN**\n"
        f"• Aktualna wartość: **{current_value:.2f} PLN**\n"
        f"• Niezrealizowany wynik: **{profit:+.2f} PLN ({profit_pct:+.2f}%)**"
    )

@bot.command()
async def kurs(ctx):
    try:
        await ctx.send("⏳ Sprawdzam dane z giełdy...")
        df = get_cdr_data()
        
        if df.empty:
            await ctx.send("❌ Brak danych z giełdy.")
            return

        current_price = df['Close'].iloc[-1]
        prev_close = df['Close'].iloc[-2] if len(df) >= 2 else current_price
        change_pct = ((current_price - prev_close) / prev_close) * 100
        emoji = "📈" if change_pct >= 0 else "📉"
        
        user_id = str(ctx.author.id)
        user_data = portfolio.get(user_id)
        analiza_portfela = generuj_analize_pozycji(current_price, user_data)
        
        await ctx.send(
            f"{emoji} **CD Projekt S.A. (CDR.WA)**\n"
            f"• Kurs: **{current_price:.2f} PLN**\n"
            f"• Zmiana dzienna: **{change_pct:+.2f}%**"
            f"{analiza_portfela}"
        )
    except Exception as e:
        await ctx.send(f"❌ Błąd: {e}")

@tasks.loop(minutes=15)
async def check_price_alerts():
    global alert_high_sent, alert_low_sent
    try:
        df = get_cdr_data()
        if df.empty or len(df) < 2:
            return

        current_price = df['Close'].iloc[-1]
        prev_close = df['Close'].iloc[-2]
        change_pct = ((current_price - prev_close) / prev_close) * 100

        target_channel = None
        for guild in bot.guilds:
            for channel in guild.text_channels:
                if channel.permissions_for(guild.me).send_messages:
                    target_channel = channel
                    break
            if target_channel:
                break

        if not target_channel:
            return

        if change_pct >= PROG_PERCENT and not alert_high_sent:
            await target_channel.send(
                f"🚀 **ALERT GIEŁDOWY: CD Projekt mocno rośnie!**\n"
                f"Aktualny kurs: **{current_price:.2f} PLN** ({change_pct:+.2f}% dzisiaj)"
            )
            alert_high_sent = True

        elif change_pct <= -PROG_PERCENT and not alert_low_sent:
            await target_channel.send(
                f"📉 **ALERT GIEŁDOWY: CD Projekt mocno spada!**\n"
                f"Aktualny kurs: **{current_price:.2f} PLN** ({change_pct:+.2f}% dzisiaj)"
            )
            alert_low_sent = True

    except Exception as e:
        print(f"Błąd automatycznego sprawdzania: {e}")

# Uruchomienie serwera HTTP w osobnym wątku dla Rendera
t = threading.Thread(target=run_http)
t.daemon = True
t.start()

bot.run(TOKEN)

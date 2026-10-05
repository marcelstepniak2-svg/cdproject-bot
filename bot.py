import os
import json
import threading
import requests
import yfinance as yf
import discord
from discord.ext import commands, tasks
from flask import Flask

# ==========================================
# KONFIGURACJA
# ==========================================
TICKER = "AMD"
NAME = "AMD"
CURRENCY = "PLN"
PORTFOLIO_FILE = "portfolio.json"

# ID kanału powiadomień dla alertów (opcjonalnie).
# Jeśli zostawisz 0, bot wyśle alert na pierwszy dostępny kanał tekstowy.
ALERT_CHANNEL_ID = 0  

# Tworzenie sesji HTTP z nagłówkiem User-Agent (ochrona przed blokadą Yahoo Finance)
session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
})

# ==========================================
# SERWER FLASK (DLA RENDER.COM & KEEP-ALIVE)
# ==========================================
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot AMD jest uruchomiony i działa 24/7!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# ==========================================
# ZARZĄDZANIE PORTFELEM (JSON)
# ==========================================
def load_portfolio():
    if not os.path.exists(PORTFOLIO_FILE):
        return {}
    try:
        with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_portfolio(data):
    with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

# ==========================================
# POBIERANIE DANYCH GIEŁDOWYCH (USD -> PLN)
# ==========================================
def get_amd_data():
    """
    Pobiera dane dla AMD w USD oraz aktualny kurs USD/PLN,
    zwracając przeliczoną cenę w PLN, cenę w USD oraz zmianę procentową.
    """
    try:
        # 1. Pobranie danych dla akcji AMD
        amd = yf.Ticker(TICKER, session=session)
        hist_amd = amd.history(period="1mo")
        if hist_amd.empty:
            return None, None, None, None

        cena_usd = hist_amd['Close'].iloc[-1]
        
        # Obliczenie zmiany procentowej z ostatniej znanej sesji
        if len(hist_amd) >= 2:
            poprzednia_cena_usd = hist_amd['Close'].iloc[-2]
            zmiana_pct = ((cena_usd - poprzednia_cena_usd) / poprzednia_cena_usd) * 100
        else:
            zmiana_pct = 0.0

        # 2. Pobranie kursu walutowego USD/PLN
        usd_pln_ticker = yf.Ticker("USDPLN=X", session=session)
        hist_usd_pln = usd_pln_ticker.history(period="1mo")
        if not hist_usd_pln.empty:
            kurs_usd = hist_usd_pln['Close'].iloc[-1]
        else:
            # Rezerwowy ticker kursu dolara
            usd_pln_alt = yf.Ticker("PLN=X", session=session)
            hist_alt = usd_pln_alt.history(period="1mo")
            kurs_usd = hist_alt['Close'].iloc[-1] if not hist_alt.empty else 4.0

        # 3. Przeliczenie ceny na PLN
        cena_pln = cena_usd * kurs_usd
        return cena_pln, cena_usd, kurs_usd, zmiana_pct

    except Exception as e:
        print(f"Błąd pobierania danych giełdowych: {e}")
        return None, None, None, None

# ==========================================
# DISCORD BOT SETUP
# ==========================================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

last_alert_pct = 0.0

@bot.event
async def on_ready():
    print(f"Bot zalogowany jako: {bot.user.name}")
    if not check_alerts.is_running():
        check_alerts.start()

# ==========================================
# KOMENDY DISCORD
# ==========================================

@bot.command(name="kurs")
async def kurs(ctx):
    """Wyświetla aktualny kurs AMD przeliczony na PLN oraz rekomendację."""
    cena_pln, cena_usd, kurs_usd, zmiana_pct = get_amd_data()

    if cena_pln is None:
        await ctx.send("❌ Błąd podczas pobierania danych z giełdy. Spróbuj ponownie za chwilę.")
        return

    emoji = "🟢" if zmiana_pct >= 0 else "🔴"
    
    msg = f"**• Kurs {NAME} ({TICKER}):**\n"
    msg += f"💵 Cena: **{cena_pln:.2f} PLN** ({cena_usd:.2f} USD | kurs USD: {kurs_usd:.2f} PLN)\n"
    msg += f"{emoji} Zmiana dzienna: **{zmiana_pct:+.2f}%**\n\n"

    # Sprawdzenie indywidualnego portfela użytkownika
    portfolio = load_portfolio()
    user_id = str(ctx.author.id)
    
    if user_id in portfolio and portfolio[user_id].get("ilosc", 0) > 0:
        u_data = portfolio[user_id]
        sr_cena = u_data["srednia_cena"]
        zysk_pct = ((cena_pln - sr_cena) / sr_cena) * 100
        
        msg += f"**TWÓJ PORTFEL:**\n"
        msg += f"📦 Posiadasz: **{u_data['ilosc']:.4f}** akcji\n"
        msg += f"📊 Średnia cena zakupu: **{sr_cena:.2f} PLN**\n"
        msg += f"📈 Twój zysk/strata: **{zysk_pct:+.2f}%**\n\n"

        if zysk_pct >= 5.0:
            msg += "💡 *Rekomendacja:* Masz ładny zysk (+5%)! Rozważ realizację części zysków (np. sprzedaż 20-30% akcji)."
        elif zysk_pct <= -5.0:
            msg += "💡 *Rekomendacja:* Kurs spadł poniżej Twojej średniej. Dobry moment na uśrednienie ceny zakupu na XTB."
        else:
            msg += "💡 *Rekomendacja:* Pozycja stabilna. Trzymaj akcje i obserwuj rynek."
    else:
        msg += "💡 *Brak akcji w portfelu:* Wpisz `!kup <ilość> <cena_w_PLN>`, aby zacząć śledzić swoje inwestycje."

    await ctx.send(msg)

@bot.command(name="kup")
async def kup(ctx, ilosc: float, cena_pln: float):
    """Rejestruje zakup akcji w PLN (np. !kup 0.5 620.50)."""
    if ilosc <= 0 or cena_pln <= 0:
        await ctx.send("❌ Ilość oraz cena muszą być większe od zera.")
        return

    portfolio = load_portfolio()
    user_id = str(ctx.author.id)

    if user_id not in portfolio:
        portfolio[user_id] = {"ilosc": 0.0, "srednia_cena": 0.0, "zrealizowany_zysk": 0.0}

    u = portfolio[user_id]
    stara_ilosc = u.get("ilosc", 0.0)
    stara_srednia = u.get("srednia_cena", 0.0)

    nowa_ilosc = stara_ilosc + ilosc
    nowa_srednia = ((stara_ilosc * stara_srednia) + (ilosc * cena_pln)) / nowa_ilosc

    u["ilosc"] = nowa_ilosc
    u["srednia_cena"] = nowa_srednia
    save_portfolio(portfolio)

    await ctx.send(
        f"✅ **Zarejestrowano zakup {NAME}:**\n"
        f"• Kupiłeś: **{ilosc:.4f}** akcji po **{cena_pln:.2f} PLN**\n"
        f"• Razem posiadasz: **{nowa_ilosc:.4f}** akcji\n"
        f"• Nowa średnia cena zakupu: **{nowa_srednia:.2f} PLN**"
    )

@bot.command(name="sprzedaj")
async def sprzedaj(ctx, ilosc: float, cena_pln: float):
    """Rejestruje sprzedaż akcji w PLN (np. !sprzedaj 0.2 680.00)."""
    portfolio = load_portfolio()
    user_id = str(ctx.author.id)

    if user_id not in portfolio or portfolio[user_id].get("ilosc", 0.0) <= 0:
        await ctx.send("❌ Nie posiadasz żadnych akcji do sprzedania!")
        return

    u = portfolio[user_id]
    posiadane = u["ilosc"]

    if ilosc <= 0 or ilosc > posiadane:
        await ctx.send(f"❌ Nieprawidłowa ilość! Posiadasz obecnie **{posiadane:.4f}** akcji.")
        return

    sr_cena = u["srednia_cena"]
    zysk_pln = (cena_pln - sr_cena) * ilosc
    zysk_pct = ((cena_pln - sr_cena) / sr_cena) * 100

    u["ilosc"] -= ilosc
    u["zrealizowany_zysk"] = u.get("zrealizowany_zysk", 0.0) + zysk_pln

    if u["ilosc"] <= 0.00001:
        u["ilosc"] = 0.0
        u["srednia_cena"] = 0.0

    save_portfolio(portfolio)

    emoji = "🟢" if zysk_pln >= 0 else "🔴"
    await ctx.send(
        f"✅ **Zarejestrowano sprzedaż {NAME}:**\n"
        f"• Sprzedałeś: **{ilosc:.4f}** akcji po **{cena_pln:.2f} PLN**\n"
        f"• Pozostało w portfelu: **{u['ilosc']:.4f}** akcji\n"
        f"• Zrealizowany wynik: {emoji} **{zysk_pln:+.2f} PLN** ({zysk_pct:+.2f}%)"
    )

@bot.command(name="portfel")
async def portfel(ctx):
    """Wyświetla pełne podsumowanie Twojego portfela AMD."""
    portfolio = load_portfolio()
    user_id = str(ctx.author.id)

    if user_id not in portfolio or portfolio[user_id].get("ilosc", 0.0) <= 0:
        await ctx.send("💼 Twój portfel jest pusty! Użyj `!kup <ilość> <cena_w_PLN>`, aby dodać pozycję.")
        return

    u = portfolio[user_id]
    posiadane = u["ilosc"]
    sr_cena = u["srednia_cena"]
    zrealizowany = u.get("zrealizowany_zysk", 0.0)

    cena_pln, cena_usd, kurs_usd, zmiana_pct = get_amd_data()

    if cena_pln is None:
        await ctx.send("❌ Błąd podczas odczytu aktualnego kursu giełdowego.")
        return

    wartosc_poczatkowa = posiadane * sr_cena
    aktualna_wartosc = posiadane * cena_pln
    niezrealizowany_zysk = aktualna_wartosc - wartosc_poczatkowa
    zysk_pct = (niezrealizowany_zysk / wartosc_poczatkowa) * 100 if wartosc_poczatkowa > 0 else 0.0

    emoji = "🟢" if niezrealizowany_zysk >= 0 else "🔴"

    msg = f"💼 **TWÓJ PORTFEL {NAME}:**\n\n"
    msg += f"📦 Posiadane akcje: **{posiadane:.4f}** szt.\n"
    msg += f"🏷️ Średnia cena zakupu: **{sr_cena:.2f} PLN**\n"
    msg += f"💵 Aktualny kurs: **{cena_pln:.2f} PLN**\n\n"
    msg += f"💰 Zainwestowany kapitał: **{wartosc_poczatkowa:.2f} PLN**\n"
    msg += f"📊 Aktualna wartość: **{aktualna_wartosc:.2f} PLN**\n"
    msg += f"📈 Niezrealizowany wynik: {emoji} **{niezrealizowany_zysk:+.2f} PLN** ({zysk_pct:+.2f}%)\n"
    msg += f"🏦 Zrealizowany zysk/strata: **{zrealizowany:+.2f} PLN**"

    await ctx.send(msg)

# ==========================================
# AUTOMATYCZNE ALERTY CENOWE (CO 15 MINUT)
# ==========================================
@tasks.loop(minutes=15)
async def check_alerts():
    global last_alert_pct
    cena_pln, cena_usd, kurs_usd, zmiana_pct = get_amd_data()

    if cena_pln is None:
        return

    # Sprawdzanie progu +/- 3%
    if abs(zmiana_pct) >= 3.0 and abs(zmiana_pct - last_alert_pct) >= 1.0:
        last_alert_pct = zmiana_pct
        emoji = "🚀" if zmiana_pct > 0 else "⚠️"
        
        alert_msg = (
            f"{emoji} **ALERT CENOWY {NAME}!**\n"
            f"Aktualny kurs uległ znacznej zmianie: **{cena_pln:.2f} PLN** ({zmiana_pct:+.2f}% dzisiaj).\n"
            f"Sprawdź swój portfel wpisując `!portfel`!"
        )

        if ALERT_CHANNEL_ID != 0:
            channel = bot.get_channel(ALERT_CHANNEL_ID)
            if channel:
                await channel.send(alert_msg)
        else:
            for guild in bot.guilds:
                for channel in guild.text_channels:
                    if channel.permissions_for(guild.me).send_messages:
                        await channel.send(alert_msg)
                        break

# ==========================================
# URUCHOMIENIE BOTA
# ==========================================
if __name__ == "__main__":
    # Serwer Flask w tle dla Render.com
    threading.Thread(target=run_flask, daemon=True).start()

    # Logowanie tokenem Discorda
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        print("❌ BŁĄD: Brak zmiennej środowiskowej DISCORD_TOKEN w panelu Render.com!")
    else:
        bot.run(token)

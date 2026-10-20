import os
import json
import threading
from curl_cffi import requests as curl_requests
import yfinance as yf
import discord
from discord.ext import commands, tasks
from flask import Flask

TICKER = "AMD"
NAME = "AMD"
CURRENCY = "PLN"
PORTFOLIO_FILE = "portfolio.json"

ALERT_CHANNEL_ID = 0

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot AMD jest uruchomiony i działa 24/7!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

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

def get_market_data():
    try:
        session = curl_requests.Session(impersonate="chrome")
        stock = yf.Ticker(TICKER, session=session)
        hist_stock = stock.history(period="1mo")
        if hist_stock.empty:
            return None, None, None, None

        cena_usd = float(hist_stock['Close'].iloc[-1])
        
        if len(hist_stock) >= 2:
            poprzednia_cena_usd = float(hist_stock['Close'].iloc[-2])
            zmiana_pct = ((cena_usd - poprzednia_cena_usd) / poprzednia_cena_usd) * 100
        else:
            zmiana_pct = 0.0

        fx_ticker = yf.Ticker("USDPLN=X", session=session)
        hist_fx = fx_ticker.history(period="1mo")
        if not hist_fx.empty:
            kurs_usd = float(hist_fx['Close'].iloc[-1])
        else:
            kurs_usd = 4.00

        cena_pln = cena_usd * kurs_usd
        return cena_pln, cena_usd, kurs_usd, zmiana_pct

    except Exception as e:
        print(f"Błąd pobierania danych giełdowych: {e}")
        return None, None, None, None

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

last_alert_pct = 0.0

async def broadcast_message(message_text):
    if ALERT_CHANNEL_ID != 0:
        channel = bot.get_channel(ALERT_CHANNEL_ID)
        if channel:
            await channel.send(message_text)
    else:
        for guild in bot.guilds:
            for channel in guild.text_channels:
                if channel.permissions_for(guild.me).send_messages:
                    await channel.send(message_text)
                    break

@bot.event
async def on_ready():
    print(f"Bot zalogowany jako: {bot.user.name}")
    if not check_alerts.is_running():
        check_alerts.start()

@bot.command(name="kurs")
async def kurs(ctx):
    cena_pln, cena_usd, kurs_usd, zmiana_pct = get_market_data()

    if cena_pln is None:
        await ctx.send("❌ Błąd podczas pobierania danych z giełdy. Spróbuj ponownie za chwilę.")
        return

    emoji = "🟢" if zmiana_pct >= 0 else "🔴"
    
    msg = f"**• Kurs {NAME} ({TICKER}):**\n"
    msg += f"💵 Cena: **{cena_pln:.2f} PLN** ({cena_usd:.2f} USD | USD/PLN: {kurs_usd:.2f})\n"
    msg += f"{emoji} Zmiana dzienna giełdy: **{zmiana_pct:+.2f}%**\n\n"

    portfolio = load_portfolio()
    user_id = str(ctx.author.id)
    
    if user_id in portfolio and portfolio[user_id].get("ilosc", 0) > 0:
        u_data = portfolio[user_id]
        sr_cena = u_data["srednia_cena"]
        zysk_pct = ((cena_pln - sr_cena) / sr_cena) * 100
        ilosc = u_data["ilosc"]
        
        msg += f"**TWÓJ PORTFEL:**\n"
        msg += f"📦 Posiadasz: **{ilosc:.4f}** akcji\n"
        msg += f"📊 Średnia cena zakupu: **{sr_cena:.2f} PLN**\n"
        msg += f"📈 Wynik od zakupu: **{zysk_pct:+.2f}%**\n\n"

        if zysk_pct >= 12.0:
            sztuki = ilosc * 0.50
            msg += f"🔥 **TERAZ OPŁACA SIĘ SPRZEDAĆ! (+12%)**\nSprzedaj 50% pozostałej pozycji (**{sztuki:.4f}** szt.):\n`!sprzedaj {sztuki:.4f} {cena_pln:.2f}`"
        elif zysk_pct >= 7.0:
            sztuki = ilosc * 0.30
            msg += f"🚀 **TERAZ OPŁACA SIĘ SPRZEDAĆ! (+7%)**\nSprzedaj kolejne 30% pozycji (**{sztuki:.4f}** szt.):\n`!sprzedaj {sztuki:.4f} {cena_pln:.2f}`"
        elif zysk_pct >= 3.0:
            sztuki = ilosc * 0.25
            msg += f"🟢 **TERAZ OPŁACA SIĘ SPRZEDAĆ! (+3%)**\nZabezpiecz zysk sprzedając 25% akcji (**{sztuki:.4f}** szt.):\n`!sprzedaj {sztuki:.4f} {cena_pln:.2f}`"
        elif zysk_pct <= -4.0:
            msg += f"📉 **TERAZ OPŁACA SIĘ KUPIĆ! (-4%)**\nCena spadła od zakupu. Dobry moment na dokupienie akcji na XTB, żeby obniżyć średnią cenę."
        else:
            msg += "💡 **CZEKAJ:** Pozycja stabilna. Nie opłaca się teraz kupować ani sprzedawać."
    else:
        msg += "💡 *Brak akcji w portfelu:* Wpisz `!kup <ilość> <cena_w_PLN>`, aby zacząć śledzić pozycję."

    await ctx.send(msg)

@bot.command(name="kup")
async def kup(ctx, ilosc: float, cena_pln: float):
    if ilosc <= 0 or cena_pln <= 0:
        await ctx.send("❌ Ilość oraz cena muszą być większe od zera.")
        return

    portfolio = load_portfolio()
    user_id = str(ctx.author.id)

    if user_id not in portfolio:
        portfolio[user_id] = {
            "ilosc": 0.0, 
            "srednia_cena": 0.0, 
            "zrealizowany_zysk": 0.0, 
            "profit_stage": 0,
            "dip_alert_sent": False
        }

    u = portfolio[user_id]
    stara_ilosc = u.get("ilosc", 0.0)
    stara_srednia = u.get("srednia_cena", 0.0)

    nowa_ilosc = stara_ilosc + ilosc
    nowa_srednia = ((stara_ilosc * stara_srednia) + (ilosc * cena_pln)) / nowa_ilosc

    u["ilosc"] = nowa_ilosc
    u["srednia_cena"] = nowa_srednia
    u["profit_stage"] = 0
    u["dip_alert_sent"] = False

    save_portfolio(portfolio)

    await ctx.send(
        f"✅ **Zarejestrowano zakup {NAME}:**\n"
        f"• Kupiłeś: **{ilosc:.4f}** akcji po **{cena_pln:.2f} PLN**\n"
        f"• Razem posiadasz: **{nowa_ilosc:.4f}** akcji\n"
        f"• Nowa średnia cena zakupu: **{nowa_srednia:.2f} PLN**"
    )

@bot.command(name="sprzedaj")
async def sprzedaj(ctx, ilosc: float, cena_pln: float):
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
        u["profit_stage"] = 0
        u["dip_alert_sent"] = False

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
    portfolio = load_portfolio()
    user_id = str(ctx.author.id)

    if user_id not in portfolio or portfolio[user_id].get("ilosc", 0.0) <= 0:
        await ctx.send("💼 Twój portfel jest pusty! Użyj `!kup <ilość> <cena_w_PLN>`, aby dodać pozycję.")
        return

    u = portfolio[user_id]
    posiadane = u["ilosc"]
    sr_cena = u["srednia_cena"]
    zrealizowany = u.get("zrealizowany_zysk", 0.0)

    cena_pln, cena_usd, kurs_usd, zmiana_pct = get_market_data()

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

@tasks.loop(minutes=15)
async def check_alerts():
    global last_alert_pct
    cena_pln, cena_usd, kurs_usd, zmiana_pct = get_market_data()

    if cena_pln is None:
        return

    if abs(zmiana_pct) >= 3.0 and abs(zmiana_pct - last_alert_pct) >= 1.0:
        last_alert_pct = zmiana_pct
        emoji = "🚀" if zmiana_pct > 0 else "⚠️"
        alert_msg = f"{emoji} **ALERT GIEŁDOWY {NAME}!**\nAktualny kurs uległ znacznej zmianie dzisiaj: **{cena_pln:.2f} PLN** ({zmiana_pct:+.2f}%)."
        await broadcast_message(alert_msg)

    portfolio = load_portfolio()
    updated_portfolio = False

    for user_id, u_data in portfolio.items():
        ilosc = u_data.get("ilosc", 0.0)
        sr_cena = u_data.get("srednia_cena", 0.0)

        if ilosc > 0 and sr_cena > 0:
            zysk_pct = ((cena_pln - sr_cena) / sr_cena) * 100
            stage = u_data.get("profit_stage", 0)
            dip_sent = u_data.get("dip_alert_sent", False)

            if zysk_pct >= 3.0 and stage < 1:
                sprzedaj_sztuk = round(ilosc * 0.25, 4)
                if sprzedaj_sztuk > 0:
                    msg = (
                        f"🟢 <@{user_id}> **TERAZ OPŁACA SIĘ SPRZEDAĆ! (ZYSK +3%)**\n"
                        f"Twoje akcje {NAME} wzrosły o **{zysk_pct:+.2f}%** od zakupu!\n"
                        f"💡 **Rekomendacja:** Sprzedaj **25%** akcji (**{sprzedaj_sztuk:.4f}** szt.):\n"
                        f"`!sprzedaj {sprzedaj_sztuk:.4f} {cena_pln:.2f}`"
                    )
                    await broadcast_message(msg)
                    u_data["profit_stage"] = 1
                    updated_portfolio = True

            elif zysk_pct >= 7.0 and stage < 2:
                sprzedaj_sztuk = round(ilosc * 0.30, 4)
                if sprzedaj_sztuk > 0:
                    msg = (
                        f"🚀 <@{user_id}> **TERAZ OPŁACA SIĘ SPRZEDAĆ! (ZYSK +7%)**\n"
                        f"Kurs rośnie dalej! Zysk wynosi **{zysk_pct:+.2f}%**!\n"
                        f"💡 **Rekomendacja:** Sprzedaj kolejne **30%** z pozostałych akcji (**{sprzedaj_sztuk:.4f}** szt.):\n"
                        f"`!sprzedaj {sprzedaj_sztuk:.4f} {cena_pln:.2f}`"
                    )
                    await broadcast_message(msg)
                    u_data["profit_stage"] = 2
                    updated_portfolio = True

            elif zysk_pct >= 12.0 and stage < 3:
                sprzedaj_sztuk = round(ilosc * 0.50, 4)
                if sprzedaj_sztuk > 0:
                    msg = (
                        f"🔥 <@{user_id}> **TERAZ OPŁACA SIĘ SPRZEDAĆ! (ZYSK +12%)**\n"
                        f"Mocny rajd! Zysk przekroczył **{zysk_pct:+.2f}%**!\n"
                        f"💡 **Rekomendacja:** Zrealizuj połowę z reszty akcji (**{sprzedaj_sztuk:.4f}** szt.):\n"
                        f"`!sprzedaj {sprzedaj_sztuk:.4f} {cena_pln:.2f}`"
                    )
                    await broadcast_message(msg)
                    u_data["profit_stage"] = 3
                    updated_portfolio = True

            elif zysk_pct <= -4.0 and not dip_sent:
                msg = (
                    f"📉 <@{user_id}> **TERAZ OPŁACA SIĘ KUPIĆ! (SPADEK -4%)**\n"
                    f"Cena (**{cena_pln:.2f} PLN**) jest o **{zysk_pct:.2f}%** niższa od Twojej średniej zakupu (**{sr_cena:.2f} PLN**).\n"
                    f"💡 **Rekomendacja:** Dokup małą paczkę akcji na XTB, aby obniżyć średnią cenę!\n"
                    f"Po zakupie wpisz: `!kup <ilość> {cena_pln:.2f}`"
                )
                await broadcast_message(msg)
                u_data["dip_alert_sent"] = True
                updated_portfolio = True

            elif zysk_pct > -2.0 and dip_sent:
                u_data["dip_alert_sent"] = False
                updated_portfolio = True

    if updated_portfolio:
        save_portfolio(portfolio)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()

    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        print("❌ BŁĄD: Brak zmiennej środowiskowej DISCORD_TOKEN!")
    else:
        bot.run(token)

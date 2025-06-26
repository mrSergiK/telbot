import logging
import pandas as pd
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
import finnhub
import os
import yfinance as yf
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

# Load tickers from CSV
tickers = set(pd.read_csv('tickers.csv')['Ticker'].str.upper())

# Finnhub client setup (token from env variable)
FINNHUB_API_KEY = os.getenv('d1278qpr01qmhi3gs9egd1278qpr01qmhi3gs9f0')
finnhub_client = finnhub.Client(api_key=FINNHUB_API_KEY)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('Send me a ticker!')

async def fetch_yfinance_data(ticker):
    stock = yf.Ticker(ticker)
    hist = stock.history(period="2d")
    today = datetime.now().date()
    last_quote = hist.iloc[-1]
    prev_quote = hist.iloc[-2] if len(hist) > 1 else last_quote
    market_open = today == last_quote.name.date()
    price_change = (last_quote['Close'] - prev_quote['Close']) if market_open else (prev_quote['Close'] - hist.iloc[-3]['Close'] if len(hist) > 2 else 0)
    price_change_pct = (price_change / (prev_quote['Close'] if not market_open else hist.iloc[-3]['Close'])) * 100 if (prev_quote['Close'] if not market_open else hist.iloc[-3]['Close']) != 0 else 0
    volume = int(last_quote['Volume'])
    avg_volume_30d = int(hist['Volume'][-30:].mean()) if len(hist) >= 30 else int(hist['Volume'].mean())
    info = stock.info
    float_shares = info.get('floatShares')
    shares_outstanding = info.get('sharesOutstanding')
    float_pct = (float_shares / shares_outstanding * 100) if float_shares and shares_outstanding else None
    return {
        'price_change': price_change,
        'price_change_pct': price_change_pct,
        'volume': volume,
        'avg_volume_30d': avg_volume_30d,
        'float_pct': float_pct,
        'shares_outstanding': shares_outstanding,
        'float_shares': float_shares
    }

async def fetch_finnhub_news(ticker):
    now = datetime.now()
    three_days_ago = now - timedelta(days=3)
    news = finnhub_client.company_news(ticker, _from=three_days_ago.strftime('%Y-%m-%d'), to=now.strftime('%Y-%m-%d'))
    news = sorted(news, key=lambda x: x['datetime'], reverse=True)[:3]
    return [f"{n['headline']} ({n['datetime']:%Y-%m-%d}): {n['url']}" for n in news]

async def fetch_finviz_insider(ticker):
    url = f'https://finviz.com/quote.ashx?t={ticker}'
    headers = {'User-Agent': 'Mozilla/5.0'}
    r = requests.get(url, headers=headers)
    soup = BeautifulSoup(r.text, 'html.parser')
    insider_own = None
    insider_activity = None
    for row in soup.find_all('tr'):
        cells = row.find_all('td')
        if len(cells) == 2:
            if 'Insider Own' in cells[0].text:
                insider_own = cells[1].text.strip()
            if 'Insider Trans' in cells[0].text:
                insider_activity = cells[1].text.strip()
    return insider_own, insider_activity

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().upper()
    if text in tickers:
        await update.message.reply_text('Processing...')
        ticker = text
        try:
            ydata = await fetch_yfinance_data(ticker)
            news = await fetch_finnhub_news(ticker)
            insider_own, insider_activity = await fetch_finviz_insider(ticker)
            reply = f"""
Ticker: {ticker}
Price Change: {ydata['price_change']:.2f} ({ydata['price_change_pct']:.2f}%)
Volume: {ydata['volume']:,}
30d Avg Volume: {ydata['avg_volume_30d']:,}
Float %: {ydata['float_pct']:.2f}%
Insider Ownership: {insider_own or 'N/A'}
Insider Activity: {insider_activity or 'N/A'}
Shares Outstanding: {ydata['shares_outstanding']:,} ({ydata['float_shares']:,} float)
\nNews (last 3 days):\n"""
            if news:
                reply += '\n'.join(news)
            else:
                reply += 'No recent news.'
            await update.message.reply_text(reply)
        except Exception as e:
            await update.message.reply_text(f"Error fetching data: {e}")
    else:
        await update.message.reply_text('Not in our scope bro.')

def main():
    TOKEN = os.getenv('7973319165:AAGtfzhc9W5xg4pbRpAdlCjT9PMlSqOAvxk')
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()

if __name__ == '__main__':
    main() 
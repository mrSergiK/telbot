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
FINNHUB_API_KEY = os.getenv('FINNHUB_API_KEY')
finnhub_client = finnhub.Client(api_key=FINNHUB_API_KEY)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('Send me a ticker!')

async def fetch_yfinance_data(ticker):
    stock = yf.Ticker(ticker)
    hist = stock.history(period="35d")  # get up to 30d avg
    if hist.empty or len(hist) < 1:
        return {
            'price_change': 'N/A',
            'price_change_pct': 'N/A',
            'volume': 'N/A',
            'avg_volume_30d': 'N/A',
            'today_vs_30d': 'N/A',
            'float_pct': 'N/A',
            'shares_outstanding': 'N/A',
            'float_shares': 'N/A'
        }
    today = datetime.now().date()
    last_quote = hist.iloc[-1]
    prev_quote = hist.iloc[-2] if len(hist) > 1 else last_quote
    market_open = today == last_quote.name.date()
    # Price change logic with safe checks
    if market_open and len(hist) > 1:
        price_change = last_quote['Close'] - prev_quote['Close']
        price_change_pct = (price_change / prev_quote['Close'] * 100) if prev_quote['Close'] else 0
    elif not market_open and len(hist) > 2:
        price_change = prev_quote['Close'] - hist.iloc[-3]['Close']
        price_change_pct = (price_change / hist.iloc[-3]['Close'] * 100) if hist.iloc[-3]['Close'] else 0
    else:
        price_change = 'N/A'
        price_change_pct = 'N/A'
    # Volume extraction
    volume = last_quote['Volume']
    if pd.isna(volume):
        volume = 0
    else:
        volume = int(volume)
    # 30-day average volume
    last_30_vol = hist['Volume'].dropna()[-30:]
    avg_volume_30d = int(last_30_vol.mean()) if not last_30_vol.empty else 'N/A'
    # Today/30-day Vol, x
    today_vs_30d = round(volume / avg_volume_30d, 2) if isinstance(avg_volume_30d, int) and avg_volume_30d > 0 else 'N/A'
    info = stock.info
    float_shares = info.get('floatShares', 'N/A')
    shares_outstanding = info.get('sharesOutstanding', 'N/A')
    float_pct = (float_shares / shares_outstanding * 100) if isinstance(float_shares, (int, float)) and isinstance(shares_outstanding, (int, float)) and float_shares and shares_outstanding else 'N/A'
    return {
        'price_change': price_change,
        'price_change_pct': price_change_pct,
        'volume': volume,
        'avg_volume_30d': avg_volume_30d,
        'today_vs_30d': today_vs_30d,
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

def fmt_num(val, fmt):
    if isinstance(val, (int, float)):
        return format(val, fmt)
    return str(val)

# Helper: Try Finnhub for price/volume/float/shares
async def fetch_finnhub_metrics(ticker):
    try:
        quote = finnhub_client.quote(ticker)
        metric = finnhub_client.company_basic_financials(ticker, 'all').get('metric', {})
        price = quote.get('c')
        prev_close = quote.get('pc')
        price_change = price - prev_close if price is not None and prev_close is not None else 'N/A'
        price_change_pct = (price_change / prev_close * 100) if price_change != 'N/A' and prev_close else 'N/A'
        volume = quote.get('v', 'N/A')
        avg_volume_30d = metric.get('10DayAverageTradingVolume') or metric.get('52WeekAverageTradingVolume') or 'N/A'
        today_vs_30d = round(volume / avg_volume_30d, 2) if isinstance(volume, (int, float)) and isinstance(avg_volume_30d, (int, float)) and avg_volume_30d > 0 else 'N/A'
        float_shares = metric.get('floatShares', 'N/A')
        shares_outstanding = metric.get('sharesOutstanding', 'N/A')
        float_pct = (float_shares / shares_outstanding * 100) if isinstance(float_shares, (int, float)) and isinstance(shares_outstanding, (int, float)) and float_shares and shares_outstanding else 'N/A'
        return {
            'price_change': price_change,
            'price_change_pct': price_change_pct,
            'volume': volume,
            'avg_volume_30d': avg_volume_30d,
            'today_vs_30d': today_vs_30d,
            'float_pct': float_pct,
            'shares_outstanding': shares_outstanding,
            'float_shares': float_shares
        }
    except Exception:
        return None

async def fetch_yfinance_data_with_fallback(ticker):
    ydata = await fetch_yfinance_data(ticker)
    # If any key is 'N/A', try Finnhub
    if any(ydata[k] == 'N/A' for k in ydata):
        fdata = await fetch_finnhub_metrics(ticker)
        if fdata:
            # Use Finnhub values only for missing ones
            for k in ydata:
                if ydata[k] == 'N/A' and fdata.get(k) != 'N/A':
                    ydata[k] = fdata[k]
    return ydata

async def fetch_finnhub_insider(ticker):
    try:
        # Insider transactions
        tx = finnhub_client.stock_insider_transactions(ticker)
        activity = f"{len(tx.get('data', []))} recent transactions" if tx.get('data') else 'N/A'
        # Insider ownership
        own = finnhub_client.ownership(ticker)
        if own and own.get('ownership'):  # List of dicts
            pct = own['ownership'][0].get('percent')
            pct_str = f"{pct:.2f}%" if pct is not None else 'N/A'
        else:
            pct_str = 'N/A'
        return pct_str, activity
    except Exception:
        return None, None

async def fetch_finnhub_news_with_fallback(ticker):
    now = datetime.now()
    three_days_ago = now - timedelta(days=3)
    try:
        news = finnhub_client.company_news(ticker, _from=three_days_ago.strftime('%Y-%m-%d'), to=now.strftime('%Y-%m-%d'))
        news = sorted(news, key=lambda x: x['datetime'], reverse=True)[:3]
        if news:
            return [f"{n['headline']} ({datetime.fromtimestamp(n['datetime']).strftime('%Y-%m-%d')}): {n['url']}" for n in news]
    except Exception:
        pass
    # Fallback to yfinance news
    try:
        stock = yf.Ticker(ticker)
        ynews = stock.news[:3]
        return [f"{n['title']} ({datetime.fromtimestamp(n['providerPublishTime']).strftime('%Y-%m-%d')}): {n['link']}" for n in ynews]
    except Exception:
        return []

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().upper()
    if text in tickers:
        await update.message.reply_text('Processing...')
        ticker = text
        try:
            ydata = await fetch_yfinance_data_with_fallback(ticker)
            insider_own, insider_activity = await fetch_finnhub_insider(ticker)
            if not insider_own or insider_own == 'N/A':
                # fallback to Finviz
                insider_own, insider_activity = await fetch_finviz_insider(ticker)
            news = await fetch_finnhub_news_with_fallback(ticker)
            reply = f"""
Ticker: {ticker}
Price Change: {fmt_num(ydata['price_change'], '.2f')} ({fmt_num(ydata['price_change_pct'], '.2f')}%)
Volume: {fmt_num(ydata['volume'], ',')}
30d Avg Volume: {fmt_num(ydata['avg_volume_30d'], ',')}
Today/30-day Vol, x: {fmt_num(ydata['today_vs_30d'], '.2f')}
Float %: {fmt_num(ydata['float_pct'], '.2f')}%
Insider Ownership: {insider_own or 'N/A'}
Insider Activity: {insider_activity or 'N/A'}
Shares Outstanding: {fmt_num(ydata['shares_outstanding'], ',')} ({fmt_num(ydata['float_shares'], ',')} float)
\nNews (last 3 days):\n"""
            if news:
                reply += '\n'.join(news)
            else:
                reply += 'No recent news.'
            await update.message.reply_text(reply)
        except Exception as e:
            await update.message.reply_text(f"Error fetching data: {e}\nPlease try again later or with a different ticker.")
    else:
        await update.message.reply_text('Not in our scope bro.')

def main():
    TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()

if __name__ == '__main__':
    main() 
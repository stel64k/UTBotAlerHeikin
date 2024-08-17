import ccxt
import pandas as pd
import ta
import time
from datetime import datetime
import logging
import telegram
# Настройка логгера
logging.basicConfig(
    filename='trading_bot.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    filemode='w',
    encoding='utf-8'
)
logger = logging.getLogger()

telegram_token = '7290631229:AAFcC2IGpE-A7p2xY0pQNjb4EX8KcElrkcg'
telegram_chat_id = '488941196'
telegram_bot = telegram.Bot(token=telegram_token)


def send_message(message):
    telegram_bot.send_message(chat_id=telegram_chat_id, text=message)

# Инициализация API Binance Futures
binance = ccxt.binance({
    'enableRateLimit': True,
    'options': {'defaultType': 'future'}
})

# Флаг для использования свечей Heikin Ashi
use_heikin_ashi = True

# Функция для получения топ 10 торговых пар по объему
def get_top_10_pairs():
    tickers = binance.fetch_tickers()
    futures_tickers = {symbol: data for symbol, data in tickers.items() if 'USDT' in symbol and data['quoteVolume'] is not None}
    sorted_tickers = sorted(futures_tickers.items(), key=lambda x: x[1]['quoteVolume'], reverse=True)
    top_10 = sorted_tickers[:30]
    return [ticker[0] for ticker in top_10]

# Функция для получения данных OHLCV
def fetch_ohlcv(symbol, timeframe='3m', limit=500):
    return binance.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)

# Функция для расчёта свечей Heikin Ashi
def calculate_heikin_ashi(df):
    df['HA_Close'] = (df['open'] + df['high'] + df['low'] + df['close']) / 4
    df['HA_Open'] = (df['open'].shift(1) + df['close'].shift(1)) / 2
    df['HA_High'] = df[['high', 'HA_Open', 'HA_Close']].max(axis=1)
    df['HA_Low'] = df[['low', 'HA_Open', 'HA_Close']].min(axis=1)
    return df

# Функция для анализа рынка на основе стратегии
def analyze_market(symbol, df):
    # Если флаг use_heikin_ashi установлен, используем свечи Heikin Ashi
    if use_heikin_ashi:
        df = calculate_heikin_ashi(df)
        src = df['HA_Close']
    else:
        src = df['close']

    # Расчёт ATR
    df['ATR'] = ta.volatility.AverageTrueRange(df['high'], df['low'], df['close'], window=10).average_true_range()
    df['nLoss'] = 3 * df['ATR']

    # Добавление расчёта EMA
    df['ema'] = ta.trend.ema_indicator(src, window=1)

    # Инициализация переменной xATRTrailingStop
    df['xATRTrailingStop'] = 0.0

    # Вычисление xATRTrailingStop
    for i in range(1, len(df)):
        prev_stop = df['xATRTrailingStop'][i-1]
        if src[i] > prev_stop and src[i-1] > prev_stop:
            df.at[i, 'xATRTrailingStop'] = max(prev_stop, src[i] - df['nLoss'][i])
        elif src[i] < prev_stop and src[i-1] < prev_stop:
            df.at[i, 'xATRTrailingStop'] = min(prev_stop, src[i] + df['nLoss'][i])
        else:
            if src[i] > prev_stop:
                df.at[i, 'xATRTrailingStop'] = src[i] - df['nLoss'][i]
            else:
                df.at[i, 'xATRTrailingStop'] = src[i] + df['nLoss'][i]

    # Вычисление позиции
    df['pos'] = 0
    for i in range(1, len(df)):
        prev_stop = df['xATRTrailingStop'][i-1]
        prev_pos = df['pos'][i-1]
        if src[i-1] < prev_stop and src[i] > prev_stop:
            df.at[i, 'pos'] = 1
        elif src[i-1] > prev_stop and src[i] < prev_stop:
            df.at[i, 'pos'] = -1
        else:
            df.at[i, 'pos'] = prev_pos

    # Логика сигналов покупки/продажи
    df['buy'] = (src > df['xATRTrailingStop']) & (df['ema'] > df['xATRTrailingStop']) & (df['ema'].shift(1) <= df['xATRTrailingStop'].shift(1))
    df['sell'] = (src < df['xATRTrailingStop']) & (df['xATRTrailingStop'] > df['ema']) & (df['xATRTrailingStop'].shift(1) <= df['ema'].shift(1))

    return df


# Основной цикл для мониторинга рынков
def monitor_markets():
    while True:
        try:
            top_10_pairs = get_top_10_pairs()
            logger.info(f"Топ 10 пар: {top_10_pairs}")
            for symbol in top_10_pairs:
                ohlcv = fetch_ohlcv(symbol)
                df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                latest = df.iloc[-1]
                # Анализ рынка
                analyzed_df = analyze_market(symbol, df)
                symbol = symbol.replace(':USDT', '').replace('/', '')  # Clean symbol
                tradingview_link = f"https://www.tradingview.com/chart/?symbol=BINANCE:{symbol}PERP"
                # Проверка на сигналы покупки/продажи
                if analyzed_df['buy'].iloc[-1]:
                    logger.info(f"Сигнал на покупку для {symbol} с ценой {latest['close']} ")
                    print(f"Сигнал на покупку для {symbol} с ценой {latest['close']}")
                    send_message(f"Сигнал на покупку для {symbol} с ценой {latest['close']} {tradingview_link}")
                if analyzed_df['sell'].iloc[-1]:
                    print(f"Сигнал на продажу для {symbol} с ценой {latest['close']}")
                    logger.info(f"Сигнал на продажу для {symbol} с ценой {latest['close']}")
                    send_message(f"Сигнал на продажу для {symbol} с ценой {latest['close']} {tradingview_link}")

            # Пауза на 3 минуты перед повтором
            time.sleep(90)
        except Exception as e:
            logger.error(f"Произошла ошибка: {e}")
            time.sleep(180)  # Продолжаем после задержки даже если произошла ошибка

# Запуск мониторинга
monitor_markets()

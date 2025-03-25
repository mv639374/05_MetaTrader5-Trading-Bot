import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from ta.volatility import BollingerBands, AverageTrueRange
from ta.momentum import RSIIndicator, StochasticOscillator
from ta.trend import EMAIndicator, ADXIndicator, MACD
import time
from datetime import datetime, timedelta

# Initialize MT5 connection
print("Attempting to initialize MT5...")
if not mt5.initialize():
    print("MT5 initialization failed:", mt5.last_error())
    mt5.shutdown()
    exit()

# Account credentials
account = 240065549
password = "135790Mv*"
server = "Exness-MT5Trial6"
print("Attempting to login...")
if not mt5.login(account, password, server):
    print("Login failed:", mt5.last_error())
    mt5.shutdown()
    exit()
print("MT5 connected successfully!")

# Define forex pairs and their strategies
securities = {
    "EURUSD": {"timeframe": mt5.TIMEFRAME_M5, "strategy": "mean_reversion", "active_hours": range(8, 17)},  # London/EU session
    "USDJPY": {"timeframe": mt5.TIMEFRAME_M1, "strategy": "scalping", "active_hours": range(0, 8)},  # Asian session
    "GBPUSD": {"timeframe": mt5.TIMEFRAME_M15, "strategy": "momentum", "active_hours": range(8, 17)},  # London session
    "USDCHF": {"timeframe": mt5.TIMEFRAME_M5, "strategy": "breakout", "active_hours": range(8, 17)},  # EU session
    "USDCAD": {"timeframe": mt5.TIMEFRAME_H1, "strategy": "trend_following", "active_hours": range(13, 21)},  # NY session
    "AUDUSD": {"timeframe": mt5.TIMEFRAME_M5, "strategy": "rsi_mean_reversion", "active_hours": range(0, 8)},  # Asian session
    "NZDUSD": {"timeframe": mt5.TIMEFRAME_M15, "strategy": "volatility_breakout", "active_hours": range(0, 8)},  # Asian session
    "GBPJPY": {"timeframe": mt5.TIMEFRAME_M1, "strategy": "hft_scalping", "active_hours": range(8, 17)},  # London session
    "USDINR": {"timeframe": mt5.TIMEFRAME_H1, "strategy": "stat_arb", "active_hours": range(3, 11)},  # Indian session
}

# Initialize symbols
for symbol in securities.keys():
    found = False
    for s in mt5.symbols_get():
        if symbol in s.name:
            securities[symbol]["symbol"] = s.name
            found = True
            print(f"Found {symbol} variant: {s.name}")
            break
    if not found:
        print(f"No {symbol} variant found. Exiting...")
        mt5.shutdown()
        exit()
    if not mt5.symbol_select(securities[symbol]["symbol"], True):
        print(f"Symbol {symbol} not found:", mt5.last_error())
        mt5.shutdown()
        exit()

# Strategy parameters
lot_size = 0.01  # Default lot size if calculation fails
cooldown_seconds = 5 * 60  # 5-minute cooldown per security
max_trades_per_day = 50  # Max 50 trades per day per security
max_open_positions = 10  # Max 10 open positions at a time
rrr = 1.5  # Risk-Reward Ratio of 1.5
leverage = 200  # Leverage is 1:200
max_trade_duration = 2 * 24 * 60 * 60  # Max 2 days for a trade
max_margin_per_trade = 25000  # Max margin per trade is 25,000 USD

# Track trades and cooldowns
last_trade_times = {symbol: 0 for symbol in securities.keys()}
daily_trade_counts = {symbol: 0 for symbol in securities.keys()}
last_reset_date = datetime.utcnow().date()

def calculate_margin(symbol, lot, price):
    """Calculate the margin required for a position."""
    notional_value = lot * 100000 * price  # 100,000 units per lot
    margin = notional_value / leverage
    return margin

def get_total_margin_used():
    """Calculate total margin used by all open positions."""
    positions = mt5.positions_get()
    total_margin = 0.0
    for pos in positions:
        margin = calculate_margin(pos.symbol, pos.volume, pos.price_open)
        total_margin += margin
    return total_margin

def calculate_lot_size(symbol, current_price):
    """Calculate lot size to ensure margin does not exceed 25,000 USD."""
    # Margin = (lot * 100,000 * price) / leverage
    # 25,000 = (lot * 100,000 * price) / 200
    # lot = (25,000 * 200) / (100,000 * price)
    lot = (max_margin_per_trade * leverage) / (100000 * current_price)
    return max(round(lot, 2), 0.01)

def get_indicators(symbol, timeframe):
    """Fetch OHLC data and calculate technical indicators."""
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, 200)
    if rates is None or len(rates) < 200:
        print(f"Failed to fetch rates for {symbol}:", mt5.last_error())
        return None
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    
    # Calculate indicators
    df['ema10'] = EMAIndicator(close=df['close'], window=10).ema_indicator()
    df['ema50'] = EMAIndicator(close=df['close'], window=50).ema_indicator()
    df['ema200'] = EMAIndicator(close=df['close'], window=200).ema_indicator()
    bb = BollingerBands(close=df['close'], window=20, window_dev=2.0)
    df['bb_upper'] = bb.bollinger_hband()
    df['bb_lower'] = bb.bollinger_lband()
    rsi = RSIIndicator(close=df['close'], window=14)
    df['rsi'] = rsi.rsi()
    stoch = StochasticOscillator(high=df['high'], low=df['low'], close=df['close'], window=14, smooth_window=3)
    df['stoch'] = stoch.stoch()
    atr = AverageTrueRange(high=df['high'], low=df['low'], close=df['close'], window=14)
    df['atr'] = atr.average_true_range()
    adx = ADXIndicator(high=df['high'], low=df['low'], close=df['close'], window=14)
    df['adx'] = adx.adx()
    macd = MACD(close=df['close'], window_fast=12, window_slow=26, window_sign=9)
    df['macd'] = macd.macd()
    df['macd_signal'] = macd.macd_signal()
    df['high_20'] = df['high'].rolling(window=20).max()
    df['low_20'] = df['low'].rolling(window=20).min()
    return df

def place_order(symbol, order_type, price, sl, tp, lot):
    """Place a trade order with retry mechanism."""
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lot,
        "type": order_type,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": 20,
        "magic": 123456,
        "comment": "Multi-Strategy",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    max_retries = 3
    for attempt in range(max_retries):
        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            print(f"Order failed for {symbol}: {result.retcode} - {result.comment}")
            if result.retcode == 10027:
                print("AutoTrading disabled. Please enable it in MT5 terminal.")
                time.sleep(5)
                continue
            elif result.retcode == 10013:
                print("Invalid request. Retrying...")
                time.sleep(2)
                continue
            elif result.retcode == 10019:
                print("Insufficient funds to place order.")
                return None
            return result
        else:
            print(f"Order placed for {symbol}: {result.order}")
            return result
    print(f"Max retries reached for {symbol}. Order not placed.")
    return None

def modify_trailing_stop(position, atr_value):
    """Update trailing stop based on ATR."""
    if position.type == mt5.ORDER_TYPE_BUY:
        new_sl = position.price_current - atr_value
        if new_sl > position.sl:
            request = {
                "action": mt5.TRADE_ACTION_SLTP,
                "position": position.ticket,
                "sl": new_sl,
                "tp": position.tp,
            }
            result = mt5.order_send(request)
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                print(f"Trailing stop updated for position {position.ticket}: New SL={new_sl}")
    elif position.type == mt5.ORDER_TYPE_SELL:
        new_sl = position.price_current + atr_value
        if new_sl < position.sl:
            request = {
                "action": mt5.TRADE_ACTION_SLTP,
                "position": position.ticket,
                "sl": new_sl,
                "tp": position.tp,
            }
            result = mt5.order_send(request)
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                print(f"Trailing stop updated for position {position.ticket}: New SL={new_sl}")

def check_correlation_filter(symbol):
    """Avoid overexposure by checking correlated pairs."""
    correlated_pairs = {
        "EURUSD": ["USDCHF"],
        "USDCHF": ["EURUSD"],
        "GBPUSD": ["GBPJPY"],
        "GBPJPY": ["GBPUSD"],
    }
    positions = mt5.positions_get()
    if symbol not in correlated_pairs:
        return True
    for pos in positions:
        if pos.symbol in correlated_pairs[symbol]:
            print(f"Skipping trade for {symbol} due to open position in correlated pair {pos.symbol}")
            return False
    return True

# Main trading loop
while True:
    current_time = time.time()
    current_datetime = datetime.utcnow()
    current_date = current_datetime.date()
    current_hour = current_datetime.hour

    # Reset daily trade counts at 00:00 GMT
    if current_date != last_reset_date:
        for symbol in securities.keys():
            daily_trade_counts[symbol] = 0
        last_reset_date = current_date
        print("Daily trade counts reset.")

    # Check total open positions
    positions = mt5.positions_get()
    if len(positions) >= max_open_positions:
        print(f"Max open positions ({max_open_positions}) reached. Waiting...")
        time.sleep(1)
        continue

    for symbol, config in securities.items():
        # Check if within active hours (prioritize but allow trading outside for very good opportunities)
        is_active_hour = current_hour in config["active_hours"]

        # Check max trades per day
        if daily_trade_counts[symbol] >= max_trades_per_day:
            print(f"Max trades per day reached for {symbol}. Waiting for next day...")
            continue

        print(f"\nProcessing {symbol} at {pd.Timestamp.now()}")
        account_info = mt5.account_info()
        if not account_info:
            print("Failed to get account info:", mt5.last_error())
            time.sleep(1)
            continue
        balance = account_info.balance
        equity = account_info.equity
        print(f"Account balance: {balance}, Equity: {equity}")

        # Fetch current price
        tick = mt5.symbol_info_tick(config["symbol"])
        if not tick or tick.ask == 0.0:
            print(f"Failed to get valid tick data for {symbol}: Price is 0.0. Retrying...")
            time.sleep(1)
            continue
        current_price = tick.ask
        print(f"Current price for {symbol}: {current_price}")

        # Fetch indicators
        df = get_indicators(config["symbol"], config["timeframe"])
        if df is None:
            print(f"Failed to get indicators for {symbol}")
            time.sleep(1)
            continue
        latest = df.iloc[-1]
        print(f"{symbol} - BB Upper: {latest['bb_upper']}, BB Lower: {latest['bb_lower']}, RSI: {latest['rsi']}, ATR: {latest['atr']}")

        # Volatility filter: Skip if ATR is too low
        if latest['atr'] < 0.0002:  # Adjust threshold based on pair
            print(f"Volatility too low for {symbol}. Skipping...")
            continue

        # Calculate lot size to ensure margin does not exceed 25,000 USD
        lot = calculate_lot_size(config["symbol"], current_price)
        new_margin = calculate_margin(config["symbol"], lot, current_price)
        total_margin_used = get_total_margin_used()
        print(f"Total margin used: {total_margin_used}, New margin for {symbol}: {new_margin}")

        # Manage existing positions
        positions = mt5.positions_get(symbol=config["symbol"])
        for pos in positions:
            open_time = pd.to_datetime(pos.time, unit='s')
            if (pd.Timestamp.now() - open_time).total_seconds() > max_trade_duration:
                mt5.Close(config["symbol"], ticket=pos.ticket)
                print(f"Closed position {pos.ticket} for {symbol} due to time limit")
                continue
            modify_trailing_stop(pos, latest['atr'])

        # Check cooldown period
        if current_time - last_trade_times[symbol] < cooldown_seconds:
            print(f"Cooldown active for {symbol}. Waiting {cooldown_seconds - (current_time - last_trade_times[symbol]):.1f} seconds.")
            continue

        # Check correlation filter
        if not check_correlation_filter(symbol):
            continue

        # Calculate SL and TP
        stop_loss_pips = latest['atr'] * 100
        take_profit_pips = stop_loss_pips * rrr
        pip_multiplier = 0.01 if "JPY" in symbol else 0.0001

        # Strategy-specific logic with adjusted conditions for non-active hours
        if config["strategy"] == "mean_reversion":  # EURUSD
            if current_price < latest['bb_lower'] and current_price > latest['ema200']:
                if is_active_hour or (not is_active_hour and current_price < latest['bb_lower'] * 0.999):  # Stronger signal outside active hours
                    sl = current_price - stop_loss_pips * pip_multiplier
                    tp = current_price + take_profit_pips * pip_multiplier
                    result = place_order(config["symbol"], mt5.ORDER_TYPE_BUY, current_price, sl, tp, lot)
                    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                        last_trade_times[symbol] = time.time()
                        daily_trade_counts[symbol] += 1
                    print(f"Buy attempted for {symbol}: Price={current_price}, SL={sl}, TP={tp}, Lot={lot}")

        elif config["strategy"] == "scalping":  # USDJPY
            if latest['rsi'] < 30 and latest['stoch'] < 20:
                if is_active_hour or (not is_active_hour and latest['rsi'] < 20 and latest['stoch'] < 10):  # Stronger signal outside active hours
                    sl = current_price - (latest['atr'] * 0.5 * pip_multiplier)  # Tight SL for scalping
                    tp = current_price + (latest['atr'] * 1.0 * pip_multiplier)  # Tight TP
                    result = place_order(config["symbol"], mt5.ORDER_TYPE_BUY, current_price, sl, tp, lot)
                    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                        last_trade_times[symbol] = time.time()
                        daily_trade_counts[symbol] += 1
                    print(f"Buy attempted for {symbol}: Price={current_price}, SL={sl}, TP={tp}, Lot={lot}")
            elif latest['rsi'] > 70 and latest['stoch'] > 80:
                if is_active_hour or (not is_active_hour and latest['rsi'] > 80 and latest['stoch'] > 90):  # Stronger signal outside active hours
                    sl = current_price + (latest['atr'] * 0.5 * pip_multiplier)
                    tp = current_price - (latest['atr'] * 1.0 * pip_multiplier)
                    result = place_order(config["symbol"], mt5.ORDER_TYPE_SELL, current_price, sl, tp, lot)
                    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                        last_trade_times[symbol] = time.time()
                        daily_trade_counts[symbol] += 1
                    print(f"Sell attempted for {symbol}: Price={current_price}, SL={sl}, TP={tp}, Lot={lot}")

        elif config["strategy"] == "momentum":  # GBPUSD
            if latest['macd'] > latest['macd_signal'] and df['macd'].iloc[-2] <= df['macd_signal'].iloc[-2]:
                if is_active_hour or (not is_active_hour and (latest['macd'] - latest['macd_signal']) > 0.0005):  # Stronger signal outside active hours
                    sl = current_price - stop_loss_pips * pip_multiplier
                    tp = current_price + take_profit_pips * pip_multiplier
                    result = place_order(config["symbol"], mt5.ORDER_TYPE_BUY, current_price, sl, tp, lot)
                    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                        last_trade_times[symbol] = time.time()
                        daily_trade_counts[symbol] += 1
                    print(f"Buy attempted for {symbol}: Price={current_price}, SL={sl}, TP={tp}, Lot={lot}")

        elif config["strategy"] == "breakout":  # USDCHF
            if current_price > latest['high_20']:
                if is_active_hour or (not is_active_hour and current_price > latest['high_20'] * 1.001):  # Stronger signal outside active hours
                    breakout_range = latest['high_20'] - latest['low_20']
                    sl = current_price - (breakout_range * 0.5)
                    tp = current_price + (breakout_range * 0.5 * rrr)
                    result = place_order(config["symbol"], mt5.ORDER_TYPE_BUY, current_price, sl, tp, lot)
                    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                        last_trade_times[symbol] = time.time()
                        daily_trade_counts[symbol] += 1
                    print(f"Buy attempted for {symbol}: Price={current_price}, SL={sl}, TP={tp}, Lot={lot}")

        elif config["strategy"] == "trend_following":  # USDCAD
            if latest['ema10'] > latest['ema50'] and latest['adx'] > 20:
                if is_active_hour or (not is_active_hour and latest['adx'] > 30):  # Stronger signal outside active hours
                    sl = current_price - stop_loss_pips * pip_multiplier
                    tp = current_price + take_profit_pips * pip_multiplier
                    result = place_order(config["symbol"], mt5.ORDER_TYPE_BUY, current_price, sl, tp, lot)
                    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                        last_trade_times[symbol] = time.time()
                        daily_trade_counts[symbol] += 1
                    print(f"Buy attempted for {symbol}: Price={current_price}, SL={sl}, TP={tp}, Lot={lot}")

        elif config["strategy"] == "rsi_mean_reversion":  # AUDUSD
            if latest['rsi'] < 30 and current_price > latest['ema200']:
                if is_active_hour or (not is_active_hour and latest['rsi'] < 20):  # Stronger signal outside active hours
                    sl = current_price - stop_loss_pips * pip_multiplier
                    tp = current_price + take_profit_pips * pip_multiplier
                    result = place_order(config["symbol"], mt5.ORDER_TYPE_BUY, current_price, sl, tp, lot)
                    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                        last_trade_times[symbol] = time.time()
                        daily_trade_counts[symbol] += 1
                    print(f"Buy attempted for {symbol}: Price={current_price}, SL={sl}, TP={tp}, Lot={lot}")

        elif config["strategy"] == "volatility_breakout":  # NZDUSD
            if current_price > latest['high_20'] and latest['atr'] > df['atr'].mean():
                if is_active_hour or (not is_active_hour and latest['atr'] > df['atr'].mean() * 1.5):  # Stronger signal outside active hours
                    sl = current_price - stop_loss_pips * pip_multiplier
                    tp = current_price + take_profit_pips * pip_multiplier
                    result = place_order(config["symbol"], mt5.ORDER_TYPE_BUY, current_price, sl, tp, lot)
                    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                        last_trade_times[symbol] = time.time()
                        daily_trade_counts[symbol] += 1
                    print(f"Buy attempted for {symbol}: Price={current_price}, SL={sl}, TP={tp}, Lot={lot}")

        elif config["strategy"] == "hft_scalping":  # GBPJPY
            if latest['rsi'] < 30 and latest['stoch'] < 20:
                if is_active_hour or (not is_active_hour and latest['rsi'] < 20 and latest['stoch'] < 10):  # Stronger signal outside active hours
                    sl = current_price - (latest['atr'] * 0.3 * pip_multiplier)  # Very tight SL for HFT
                    tp = current_price + (latest['atr'] * 0.6 * pip_multiplier)  # Very tight TP
                    result = place_order(config["symbol"], mt5.ORDER_TYPE_BUY, current_price, sl, tp, lot)
                    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                        last_trade_times[symbol] = time.time()
                        daily_trade_counts[symbol] += 1
                    print(f"Buy attempted for {symbol}: Price={current_price}, SL={sl}, TP={tp}, Lot={lot}")

        elif config["strategy"] == "stat_arb":  # USDINR
            z_score = (current_price - df['close'].rolling(window=50).mean()) / df['close'].rolling(window=50).std()
            if z_score.iloc[-1] < -2:  # Buy if price is 2 std devs below mean
                if is_active_hour or (not is_active_hour and z_score.iloc[-1] < -3):  # Stronger signal outside active hours
                    sl = current_price - stop_loss_pips * pip_multiplier
                    tp = current_price + take_profit_pips * pip_multiplier
                    result = place_order(config["symbol"], mt5.ORDER_TYPE_BUY, current_price, sl, tp, lot)
                    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                        last_trade_times[symbol] = time.time()
                        daily_trade_counts[symbol] += 1
                    print(f"Buy attempted for {symbol}: Price={current_price}, SL={sl}, TP={tp}, Lot={lot}")

    time.sleep(1)  # Check every second
# Author: Brent F.
# This is a script that uses the Binance python connector and technical analysis library to actively analyze and execute trades through your
# Binance spot account either on the testnet or live. I do not use or maintain this system anymore. Experiment at your own discretion.
import time
import pandas as pd
import os
import ta
from dotenv import load_dotenv
from binance.spot import Spot 
from binance.websocket.spot.websocket_client import SpotWebsocketClient as Client

class Trade:
    def __init__(self):
        # Load API keys and secrets from local .env file
        load_dotenv()
        LIVE_KEY=os.getenv('LIVE_KEY')
        LIVE_SECRET=os.getenv('LIVE_SECRET')
        TEST_KEY=os.getenv('TEST_KEY')
        TEST_SECRET=os.getenv('TEST_SECRET')

        # Config variables
        self.live=True
        self.symbol="MANA"
        self.base_currency="USDT"
        self.time_frame="5m" # An extremely volatile timeframe may produce worse results
        self.risk_tolerance = 0.05 # Always greater than 0
        self.take_profit = 0.05 # 0 for no profit taking
        self.trailing_stop = 0.04 # 0 for no trailing stop
        self.max_df_length = 100 # This can be changed but will have an impact on some technical indicators (i.e. N-period sma's)

        # Program variables
        self.pair=f"{self.symbol}{self.base_currency}"
        self.holding=False
        self.buy = {}
        self.sell = {}
        self.stop = {}
        self.bullish = True
        self.capital = 0
        self.start_capital = self.capital
        self.client={}
        self.latest_time=0
        self.min_notional = 0
        self.buy_price = 0
        self.take_price = 0
        self.capital = 0
        self.executing = False
        self.new_candle = False

        if not self.live:
            # TESTNET
            self.client = Spot(base_url='https://testnet.binance.vision', key=TEST_KEY, secret=TEST_SECRET)
        else:    
            # LIVE
            self.client = Spot(key=LIVE_KEY, secret=LIVE_SECRET)
        print(f"UNIX Timestamp: {self.client.time()['serverTime']}")

        # Set exchange limits for the pair being traded
        self.set_exchange_limits()

        # Main program loop
        while True:
            try:
                self.init_trade()
            except:
                print("Something went wrong in the main loop, retrying in 5 seconds...")
                if not self.live:
                    # TESTNET
                    self.client = Spot(base_url='https://testnet.binance.vision', key=TEST_KEY, secret=TEST_SECRET)
                else:    
                    # LIVE
                    self.client = Spot(key=LIVE_KEY, secret=LIVE_SECRET)
                time.sleep(5)
                self.init_trade()
    
    # Set variables, prepare data, execute the strategy
    def init_trade(self):
        latest_klines = self.client.klines(self.pair, self.time_frame, limit=2)
        latest = latest_klines[-1]
        previous = latest_klines[0]
        time_check = float(latest[0])
        break_low = float(previous[3]) > float(latest[3])
        trailing_stop = self.trailing_stop > 0 and self.holding and (float(latest[2]) - float(latest[4]))/float(latest[2]) > self.trailing_stop
        take_profit = self.take_profit > 0 and self.holding and self.take_price < float(latest[4])
        
        # Execute the trade strategy only if the latest candle checked is a new (unchecked) candle or if the candle is breaking previous lows
        if (time_check > self.latest_time or break_low or take_profit or not self.holding or trailing_stop) and not self.executing:
            self.executing = True
            
            # Set the latest candle time and whether or not the latest candle is a new (unchecked) candle
            if time_check > self.latest_time:
                self.new_candle = True
                self.latest_time=time_check
                if self.capital > self.min_notional and not self.holding:
                    print(f"[{int(time_check)}] Balance: {self.capital}")
            else:
                self.new_candle = False
            
            # Prepare candlestick chart data for trading strategy (add technical indicator values, headers, etc..)          
            raw_kline = self.client.klines(self.pair, self.time_frame, limit=self.max_df_length)
            kline_data = pd.DataFrame(raw_kline, columns=['Date','Open','High','Low','Close','Volume','CloseTime','QAV','Trades','bVolBase','bVolQuote','Ignore'])
            kline_data['MACD']=self.macd(kline_data['Close'].astype(float), window_slow = 21, window_fast = 8, window_sign = 9, fillna=True)
            kline_data['SMA']=self.sma(kline_data['Close'].astype(float), 5, fillna=True)

            # Execute the strategy
            res = self.macd_strategy(kline_data, len(kline_data)-1, kline_data.iloc[-1])
            if len(res)>0:
                print(res)

            self.executing = False
        time.sleep(1)
    
    def macd(self, series=[], window_slow = 26, window_fast = 12, window_sign = 9, fillna=True):
        return ta.trend.MACD(series, window_slow, window_fast, window_sign, fillna)

    def sma(self, series, window=26, fillna=True):
        return ta.trend.sma_indicator(series, window, fillna)
    
    def market_buy(self, pair, quantity):
        buy = {
            'symbol': pair,
            'side': 'BUY',
            'type': 'MARKET',
            'quoteOrderQty': quantity # qty in USDT
        }
        response = self.client.new_order(**buy)
        self.holding = True
        return response

    def market_sell(self, pair, quantity):
        buy = {
            'symbol': pair,
            'side': 'SELL',
            'type': 'MARKET',
            'quantity': quantity # qty in self.symbol
        }
        response = self.client.new_order(**buy)
        self.holding = False
        return response
    
    def stop_limit_sell(self, pair, quantity, price, riskTolerance, spreadTolerance):
        stop = {
            'symbol': pair,
            'side': 'SELL',
            'type': 'STOP_LOSS_LIMIT',
            'price': round(float(price) - float(price)*spreadTolerance, 4),
            'stopPrice': round(float(price) - float(price)*riskTolerance, 4),
            'timeInForce':'GTC',
            'quantity': quantity
        }
        response = self.client.new_order(**stop)
        return response
    
    def set_exchange_limits(self):
        # floor the risk tolerance percentage based on exchange limits
        info = self.client.exchange_info(self.pair)
        for filter in info['symbols'][0]['filters']:
            if filter['filterType']=='PERCENT_PRICE':
                multiplier_normal = float(filter['multiplierDown'])/100
                if multiplier_normal>self.risk_tolerance:
                    self.risk_tolerance=multiplier_normal
                    print(f'NEW RISK_TOLERANCE: {multiplier_normal}')
            elif filter['filterType']=='MIN_NOTIONAL':
                self.min_notional = round(float(filter['minNotional']), 4)
                print(f"[{int(self.latest_time)}] MIN_NOTIONAL: {self.min_notional}")

    def macd_strategy(self, series, index, row):

        macd = row['MACD'].macd_diff()
        macd_1 = float(macd.iloc[index-1])
        macd_2 = float(macd.iloc[index-2])
        macd_current  = float(macd.iloc[index])
        index_floor = int(index-2)
        close_1 = float(series.iloc[index-1]['Close'])
        low_1 = float(series.iloc[index-1]['Low'])
        open_1 = float(series.iloc[index-1]['Open'])
        open_2 = float(series.iloc[index-2]['Open'])
        current = float(row['Close'])

        # Check held amount of symbol and base currency
        balances = self.client.account()['balances']
        self.holding = False
        for balance in balances:
            if balance['asset'] == self.symbol:
                if float(balance['locked'])*current >= self.min_notional*1.005: # add room for price movements and comissions
                    if self.new_candle:
                        print(f"[{int(self.latest_time)}] Position: {float(balance['locked'])}")
                    self.holding = True
            elif balance['asset'] == self.base_currency:
                self.capital = float(balance['free'])
                    
        # Check momentum using MACD, and make sure its above 
        if macd_2 <= macd_1 and macd_1 <= macd_current and current > float(row['SMA']):
            self.bullish=True
        else: 
            self.bullish=False

        # Buy logic
        if index_floor>0 and not self.holding:
            # MACD + Candle Close + Market & Stop Loss Limit Orders
            if self.bullish and open_1 < current and low_1 < float(row['Low']) and not (self.trailing_stop > 0 and (float(row['High']) - float(row['Low']))/float(row['High']) > self.trailing_stop):
                # Clear any open orders on the pair being traded (cancels any leftovers stop loss limit orders)
                try:
                    self.client.cancel_open_orders(self.pair)
                except:
                    print(f"[{int(self.latest_time)}] No open orders found.")
                
                # Buy at market price
                self.buy = self.market_buy(self.pair, self.capital)
                
                # Set fill price variable (use average fill price)
                fill_count = 0
                price_total = 0
                for fill in self.buy['fills']:
                    fill_count+=float(fill['qty'])
                    price_total+=float(fill['price'])*float(fill['qty'])
                self.buy_price=price_total/fill_count
                if self.take_profit > 0:
                    self.take_price = self.buy_price*(1+self.take_profit)
                    print(f"[{int(self.latest_time)}] Profit taking at: {self.take_price}")

                # Send stop loss limit orders based on user-defined risk tolerance
                try:
                    self.stop = self.stop_limit_sell(self.pair, float(self.buy['executedQty']), self.buy_price, self.risk_tolerance, self.risk_tolerance*2)
                except:
                    # Catch if held quantity is different from executed buy quantity (fees)
                    held = 0
                    print(f"[{int(self.latest_time)}] ERROR CAUGHT: {float(self.buy['executedQty'])}")
                    balances = self.client.account()['balances']
                    for balance in balances:
                        if balance['asset'] == self.symbol:
                            held = int(float(balance['free']))
                    self.stop = self.stop_limit_sell(self.pair, held, self.buy_price, self.risk_tolerance, self.risk_tolerance*2)

                return f"[{int(self.latest_time)}] Approx. Buy Price: {self.buy_price}"
        # Sell logic
        elif index_floor>0 and self.holding:
            # MACD + Candle Close + Lower Low
            if (not self.bullish and open_2>=close_1) or low_1 > current or (self.take_profit > 0 and current > self.take_price) or (self.trailing_stop > 0 and (float(row['High']) - current)/float(row['High']) > self.trailing_stop):
                # Clear any open orders on the pair being traded (cancels any leftovers stop loss limit orders)
                try:
                    self.client.cancel_open_orders(self.pair)
                except:
                    print(f"[{int(self.latest_time)}] No open orders.")
                
                # Sell at market price
                try:
                    self.sell = self.market_sell(self.pair, float(self.buy['executedQty']))

                    return f"[{int(self.latest_time)}] Approx. Sell Price: {float(self.client.ticker_price(self.pair)['price'])}"
                except:
                    held = 0
                    print(f"[{int(self.latest_time)}] ERROR CAUGHT: MARKET SELL")
                    balances = self.client.account()['balances']
                    for balance in balances:
                        if balance['asset'] == self.symbol:
                            held = int(float(balance['free']))
                    self.sell = self.market_sell(self.pair, held)

                    return f"[{int(self.latest_time)}] Approx. Sell Price: {float(self.client.ticker_price(self.pair)['price'])}"
        return ""

trade = Trade()
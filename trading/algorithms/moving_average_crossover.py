import datetime
import math
from decimal import Decimal

from bson import ObjectId

from trading.algorithms.base import Strategy
from trading.broker import MarketOrder, ORDER_MARKET, SIDE_BUY, SIDE_SELL, SIDE_STAY, PRICE_ASK_CLOSE, PRICE_ASK
from trading.indicators import INTERVAL_TEN_CANDLES, INTERVAL_TWENTY_CANDLES
from trading.indicators.overlap_studies import calc_moving_average
from trading.util.transformations import normalize_price_data, normalize_current_price_data


class MAC(Strategy):
    name = 'Moving Average Crossover'

    crossover_threshold = 0.001

    def __init__(self, config):
        strategy_id = config.get('strategy_id')

        if strategy_id is None:
            strategy_id = ObjectId()
        else:
            config = self.load_strategy(strategy_id)

        super(MAC, self).__init__(config)
        self.strategy_id = strategy_id
        self.invested = False

    def calc_units_to_buy(self, current_price):
        base_pair_tradeable = self.portfolio.base_pair.tradeable_units
        num_units = math.floor(base_pair_tradeable / current_price)
        return int(num_units)

    def calc_units_to_sell(self, current_price):
        quote_pair_tradeable = self.portfolio.quote_pair.tradeable_units
        return int(quote_pair_tradeable)

    def allocate_tradeable_amount(self):
        base_pair = self.portfolio.base_pair
        profit = self.portfolio.profit
        if profit > 0:
            base_pair['tradeable_units'] = base_pair['initial_units']

    def analyze_data(self, market_data):
        current_market_data = market_data['current']
        historical_market_data = market_data['historical']

        historical_candle_data = historical_market_data['candles']

        closing_market_data = normalize_price_data(historical_candle_data, PRICE_ASK_CLOSE)
        asking_price = normalize_current_price_data(current_market_data, PRICE_ASK)

        # Construct the upper and lower Bollinger Bands
        short_ma = Decimal(calc_moving_average(closing_market_data, INTERVAL_TEN_CANDLES))
        long_ma = Decimal(calc_moving_average(closing_market_data, INTERVAL_TWENTY_CANDLES))

        if math.isnan(long_ma):
            self.logger.error('JJDEBUG: Closing Market Data', data=closing_market_data)

        self.strategy_data['asking_price'] = asking_price
        self.strategy_data['short_term_ma'] = short_ma
        self.strategy_data['long_term_ma'] = long_ma

        self.log_strategy_data()

    def make_decision(self):
        asking_price = self.strategy_data['asking_price']
        short_term = self.strategy_data['short_term_ma']
        long_term = self.strategy_data['long_term_ma']

        decision = SIDE_STAY
        order = None

        try:
            diff = 100 * (short_term - long_term) / ((short_term + long_term) / 2)
            self.logger.info('Diff {diff}'.format(diff=diff))

            if diff >= self.crossover_threshold and not self.invested:
                decision = SIDE_BUY
                order = self.make_order(asking_price, decision)

            elif diff <= -self.crossover_threshold and self.invested:
                decision = SIDE_SELL
                order = self.make_order(asking_price, decision)

            else:
                return decision, None
        except Exception as e:
            self.logger.error(e)
            return decision, order

        if order.units <=0:
            decision = SIDE_STAY
            order = None

        return decision, order

    def make_order(self, asking_price, order_side=SIDE_BUY):
        trade_expire = datetime.datetime.utcnow() + datetime.timedelta(days=1)
        trade_expire = trade_expire.isoformat("T") + "Z"

        if order_side == SIDE_BUY:
            units = self.calc_units_to_buy(asking_price)
        else:
            units = self.calc_units_to_sell(asking_price)

        self.logger.info('Calculated units {units} and side {side}'.format(units=units, side=order_side))

        instrument = self.portfolio.instrument
        side = order_side
        order_type = ORDER_MARKET
        price = asking_price
        expiry = trade_expire

        return MarketOrder(instrument, units, side, order_type, price, expiry)

    def shutdown(self, started_at, ended_at, num_ticks, num_orders, shutdown_cause):
        session_info = self.make_trading_session_info(started_at, ended_at, num_ticks, num_orders, shutdown_cause)

        base_pair = self.portfolio.base_pair
        quote_pair = self.portfolio.quote_pair

        config = {
            'instrument': self.portfolio.instrument,
            'base_pair': {'currency': base_pair.currency, 'starting_units': base_pair.starting_units,
                       'tradeable_units': base_pair.tradeable_units},
            'quote_pair': {'currency': quote_pair.currency, 'starting_units': quote_pair.starting_units,
                       'tradeable_units': quote_pair.tradeable_units}
        }

        strategy = {
            'name': self.name,
            'config': config,
            'profit': self.portfolio.profit,
            'data_window': self.data_window,
            'interval': self.interval,
            'indicators': self.strategy_data.keys(),
            'instrument': self.instrument,
        }

        query = {'_id': ObjectId(self.strategy_id)}
        update = {'$set': {'strategy_data': strategy}, '$push': {'sessions': session_info}}
        self.db.strategies.update(query, update, upsert=True)




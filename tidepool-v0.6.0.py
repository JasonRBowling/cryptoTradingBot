
import pickle
import os.path as path
import datetime
import time
import pandas as pd
import math
import os
import syslog
import robin_stocks as r
import tideconfig as cfg
import talib

class coin:
    purchasedPrice = 0.0
    numHeld = 0.0
    numBought = 0.0
    name = ""
    lastBuyOrder = ""
    timeBought = ""

    def __init__(self, name=""):
        print("Creating coin " + name)
        self.name = name
        self.purchasedPrice = 0.0
        self.numHeld = 0.0
        self.numBought = 0.0
        self.lastBuyOrderID = ""
        self.timeBought = ""

class moneyBot:

    #set these in tideconfig.py

    tradesEnabled = False
    buyBelowMA = 0.00
    sellAboveBuyPrice = .00
    movingAverageWindows = 0
    rsiWindow = 0
    runMinute = []
    coinList = []

    coinState = []
    minIncrements = {}  #the smallest increment of a coin you can buy/sell
    minPriceIncrements = {}   #the smallest fraction of a dollar you can buy/sell a coin with

    data = pd.DataFrame()
    boughtIn = False

    # used to determine if we have had a break in our incoming price data and hold buys if so
    minutesBetweenUpdates = 10
    buysLockedCounter = 0
    lastHeartbeat = datetime.datetime.now()
    minConsecutiveSamples = 0
    pricesGood = False

    def __init__(self):

        self.loadConfig()

        if path.exists("state.pickle"):
            # load state
            print("Saved state found, loading.")
            with open('state.pickle', 'rb') as f:
                self.coinState = pickle.load(f)

            with open('boughtIn.pickle', 'rb') as f:
                self.boughtIn = pickle.load(f)

        else:

            # create state storage object
            print("No state saved, starting from scratch.")
            self.coinState = []
            for c in self.coinList:
                print(c)
                self.coinState.append(coin(c))

        self.data = self.loadDataframe()
        Result = r.login(self.rh_user, self.rh_pw)
        #print("\n")
        #print(loginResult)
        #print("\n")
        self.getIncrements()

        return

    def loadConfig(self):
        print("\nLoading Config...")
        self.rh_user = cfg.rh["username"]
        self.rh_pw = cfg.rh["password"]
        self.statusEmailAddress = cfg.config["statusEmailAddress"]
        print("Status Email: " + self.statusEmailAddress)
        self.buyBelowMA = float(cfg.config["buyLimit"])
        print("Buy Limit " + str(self.buyBelowMA * 100) + "%")
        self.sellAboveBuyPrice = float(cfg.config["sellLimit"])
        print("Sell Limit " + str(self.sellAboveBuyPrice * 100) + "%")
        self.movingAverageWindows = cfg.config["movingAverageWindows"]
        self.runMinute = cfg.config["runMinute"]
        self.coinList = cfg.config["coinList"]
        self.tradesEnabled = cfg.config["tradesEnabled"]
        print("Trades enabled: " + str(self.tradesEnabled))
        self.rsiWindow = cfg.config["rsiWindow"]
        print("RSI Window: " + str(self.rsiWindow))
        print("MA Window: " + str(self.movingAverageWindows))

        if self.rsiWindow > self.movingAverageWindows:
            self.minConsecutiveSamples = self.rsiWindow
        else:
            self.minConsecutiveSamples = self.movingAverageWindows
        print("Consecutive prices required: " + str(self.minConsecutiveSamples))

        print("\n")

    def output(self, msg):
        # send email
        cmd = "echo \"" + msg + "\" | mail -s \"Bot\" " + self.statusEmailAddress
        os.system(cmd)
        print(msg)
        # write to syslog
        syslog.syslog(syslog.LOG_NOTICE, msg)

    def getPrices(self):
        #try:
        #  print(x)
        #except:
        #  print("An exception occurred")

        prices = {}
        emptyDict = {}

        for c in self.coinList:
            try:
                result = r.get_crypto_quote(c)
                price = result['mark_price']
            except:
                print("An exception occurred retrieving prices.")
                return emptyDict

            prices.update({c: float(price)})

        return prices

    def saveState(self):
        # save state
        with open('state.pickle', 'wb') as f:
            pickle.dump(self.coinState, f)

        with open('boughtIn.pickle', 'wb') as f:
            pickle.dump(self.boughtIn, f)

        self.data.to_pickle('dataframe.pickle')

    def getHoldings(self, ticker):
        # this contains the amount we currently own by id and code
        # quantity_available may be better
        quantity = 0.0
        try:
            result = r.get_crypto_positions()
            for t in range(0, len(result)):
                symbol = result[t]['currency']['code']
                if (symbol == ticker):
                    quantity = result[t]['quantity']
        except:
            print("Got exception while getting holdings from RobinHood.")
            quantity = -1.0

        return float(quantity)

    def getCash(self):
        # if you only want to trade part of your money, set this to (Available Cash - Amount to Trade)

        reserve = 0.00
        try:
            me = r.account.load_phoenix_account(info=None)
            cash = float(me['crypto_buying_power']['amount'])
        except:
            print("An exception occurred getting cash amount.")
            return -1.0

        if cash - reserve < 0.0:
            return 0.0
        else:
            return cash - reserve

    def sell(self, c, price):

        #if boughtIn == false but coinHeld > 0 an order has not completed

        if self.boughtIn == False:
            #a previous sell has not completed. We are marked as not in the market but still holding coin, abort sale.
            print("Previous sale incomplete.")
            return


        coinHeld = self.getHoldings(self.coinState[c].name)
        if coinHeld == -1:
            print("Got exception trying to get holdings in sell(), cancelling.")
            return

        print("RobinHood says you have " + str(coinHeld) + " of " + str(self.coinState[c].name))

        if(coinHeld > 0.0):
            #price needs to be specified to no more precision than listed in minPriceIncrement. Truncate to 7 decimal places to avoid floating point problems way out at the precision limit
            minPriceIncrement = self.minPriceIncrements[self.coinState[c].name]
            price = round(self.roundDown(price, minPriceIncrement), 7)
            profit = (coinHeld * price) - (coinHeld * self.coinState[c].purchasedPrice)

            print("Selling " + str(coinHeld) + " of " + str(self.coinList[c]) + " at price " + str(price) + " profit " + str(round(profit, 2)))

            if self.tradesEnabled == True:

                try:
                    sellResult = r.order_sell_crypto_limit(str(self.coinList[c]), coinHeld, price)
                    self.coinState[c].lastSellOrder = sellResult['id']
                    self.output(str(sellResult))
                except:
                    print("Got exception trying to sell, cancelling.")
                    return

                msg = "LiveBot: Sold " + str(coinHeld) + " of " + str(self.coinList[c]) + " at price " + str(price) + " profit " + str(round(profit, 2))
                self.output(msg)

                self.coinState[c].purchasedPrice = 0.0
                self.coinState[c].numHeld = 0.0
                self.coinState[c].numBought = 0.0
                self.coinState[c].lastBuyOrderID = ""
                self.coinState[c].timeBought = ""
                self.boughtIn = False

        return

    def buy(self, c, price):

        #we are already in the process of a buy, don't submit another
        if self.boughtIn == True:
            print("Previous buy incomplete.")
            return

        availableCash = self.getCash()
        if availableCash == -1:
            print("Got an exception checking for available cash, canceling buy.")
            return

        print("RobinHood says you have " + str(availableCash) + " in cash")

        if (availableCash > 1.0):
            minPriceIncrement = self.minPriceIncrements[self.coinState[c].name]
            #price needs to be specified to no more precision than listed in minPriceIncrement. Truncate to 7 decimal places to avoid floating point problems way out at the precision limit
            price = round(self.roundDown(price, minPriceIncrement), 7)
            shares = (availableCash - .25)/price
            minShareIncrement = self.minIncrements[self.coinState[c].name]
            shares = round(self.roundDown(shares, minShareIncrement), 8)
            sellAt = price + (price * self.sellAboveBuyPrice)
            print("Buying " + str(shares) + " shares of " + self.coinList[c] + " at " + str(price) + " selling at " + str(round(sellAt, 2)))

            if self.tradesEnabled == True:
                try:
                    buyResult = r.order_buy_crypto_limit(str(self.coinList[c]), shares, price)
                    self.coinState[c].lastBuyOrderID = buyResult['id']
                    self.output(str(buyResult))
                except:
                    print("Got exception trying to buy, cancelling.")
                    return

                msg = "LiveBot: Bought " + str(shares) + " shares of " + self.coinList[c] + " at " + str(price) + " selling at " + str(round(sellAt, 2))
                self.output(msg)
                self.coinState[c].purchasedPrice = price
                self.coinState[c].numHeld = shares
                self.coinState[c].timeBought = str(datetime.datetime.now())
                self.coinState[c].numBought = shares
                self.boughtIn = True

        return

    def checkBuyCondition(self, c):

        if self.buysLockedCounter > 0:
            print(str(self.coinList[c]) + ": Buys locked due to break in price update stream")
            return False

        # look at values in last row only
        price = self.data.iloc[-1][self.coinList[c]]
        movingAverage = self.data.iloc[-1][str(self.coinList[c]) + "-MA"]
        RSI = self.data.iloc[-1][str(self.coinList[c]) + "-RSI"]
        #threshold = movingAverage - (movingAverage * self.buyBelowMA)
        if math.isnan(movingAverage) == False and math.isnan(RSI) == False:
            #if price < movingAverage - (movingAverage * self.buyBelowMA) and self.coinState[c].numHeld == 0.0:
            if price < movingAverage - (movingAverage * self.buyBelowMA) and self.coinState[c].numHeld == 0.0 and (RSI <= 39.5):
                print("Conditions met to buy " + str(self.coinList[c]))
                return True

        return False

    def checkSellCondition(self, c):

    # look at values in last row only
        price = self.data.iloc[-1][self.coinList[c]]
        movingAverage = self.data.iloc[-1][str(self.coinList[c]) + "-MA"]
        RSI = self.data.iloc[-1][str(self.coinList[c]) + "-RSI"]

        if  math.isnan(movingAverage) == False and math.isnan(RSI) == False:
            #if price > self.coinState[c].purchasedPrice and RSI > 70.0 and self.coinState[c].numHeld > 0.0:
            if price > self.coinState[c].purchasedPrice + (self.coinState[c].purchasedPrice * self.sellAboveBuyPrice) and self.coinState[c].numHeld > 0.0:
                return True

        return False

    def checkConsecutive(self, now):

        print("\nChecking for consecutive times..")

        #check for break between now and last sample
        lastDateStamp = self.data.iloc[-1]['exec_time']
        timeDelta = now - lastDateStamp
        minutes = (timeDelta.seconds/60)
        if minutes > self.minutesBetweenUpdates:
            print("It has been too long since last price point gathered, holding buys.\n")
            return False

        if self.data.shape[0] <= 1:
            return True

        #check for break in sequence of samples to minimum consecutive sample number
        position = len(self.data) - 1
        for x in range(0, self.minConsecutiveSamples):
            t1 = self.data.iloc[position - x]['exec_time']
            t2 = self.data.iloc[position - (x + 1)]['exec_time']
            #print(str(t1) + "," + str(t2)) 
            timeDelta = t1 - t2
            minutes = (timeDelta.seconds/60)
            if minutes > self.minutesBetweenUpdates:
                print("Interruption found in price data, holding buys until sufficient samples are collected.\n")
                return False

        return True

    def updateDataframe(self, now):

        #we check this each time, so we don't need to lock for more than two cycles. It will set back to two if it fails on the next pass.
        if self.data.shape[0] > 0:
            if self.checkConsecutive(now) == False:
                self.buysLockedCounter = 2

        # tick down towards being able to buy again, if not there already.
        if self.buysLockedCounter > 0:
            self.buysLockedCounter = self.buysLockedCounter - 1

        rowdata = {}

        currentPrices = self.getPrices()
        if len(currentPrices) == 0:
            print("Exception received getting prices, not adding data, locking buys")
            self.buysLockedCounter = 2
            self.pricesGood = False
            return self.data

        self.pricesGood = True
        rowdata.update({'exec_time': now})

        for c in self.coinList:
            rowdata.update({c: currentPrices[c]})

        self.data = self.data.append(rowdata, ignore_index=True)

        # generate moving averages
        for c in self.coinList:
            colName = c + "-MA"
            self.data[colName] = self.data[c].shift(1).rolling(window=self.movingAverageWindows).mean()
            colName = c + "-RSI"
            self.data[colName] = talib.RSI(self.data[c].values, timeperiod = self.rsiWindow)

        print(self.data.tail())

        return self.data

    def loadDataframe(self):

        if path.exists("dataframe.pickle"):

            print("Restoring state...")

            self.data = pd.read_pickle('dataframe.pickle')
            print(self.data.tail())

        else:

            column_names = ['exec_time']

            for c in self.coinList:
                column_names.append(c)

            self.data = pd.DataFrame(columns=column_names)

        return self.data

    def getIncrements(self):

        self.minIncrements = {}
        self.minPriceIncrements = {}

        #also need min_order_price_increment

        for c in range(0, len(self.coinList)):
            code = self.coinList[c]

            try:
                result = r.get_crypto_info(code)
                inc = result['min_order_quantity_increment']
                p_inc = result['min_order_price_increment']
            except:
                print("Failed to get increments from RobinHood. Are you connected to the internet? Exiting.")
                exit()

            self.minIncrements.update({code: float(inc)})
            self.minPriceIncrements.update({code: float(p_inc)})

        print("Minimum sale increments:")
        print(self.minIncrements)

        print("Minimum price increments:")
        print(self.minPriceIncrements)

    def roundDown(self, x, a):
        # rounds down x to the nearest multiple of a
        return math.floor(x/a) * a

    def printState(self):

        print("\n")
        print("Bought In:" + str(self.boughtIn))

        for c in self.coinState:
            if c.numHeld > 0.0:

                print("Coin: " + str(c.name))
                print("Held: " + str(c.numHeld))
                print("Amount bought: " + str(c.numBought))
                print("Time bought: " + str(c.timeBought))
                print("Order ID: " + str(c.lastBuyOrderID))
                print("Bought at: $" + str(c.purchasedPrice))
                price = self.data.iloc[-1][c.name]
                currentValue = price * c.numHeld
                print("Current value: $" + str(round(currentValue, 2)))
        print("\n")

    def cancelOrder(self, orderID):
        self.output("Swing and miss, cancelling order " + orderID)
        try:
            cancelResult = r.cancel_crypto_order(orderID)
            print(str(cancelResult))
        except:
            self.output("Got exception canceling order, will try again.")
            return False
        return True

    def runBot(self):

        self.printState()

        while (True):

            now = datetime.datetime.now()

            timeDiffHeartbeat = now - self.lastHeartbeat

            #report in not being dead every 12 hours
            if (timeDiffHeartbeat.total_seconds() > (60 * 60 * 12)):
                msg = "Bot: Heartbeat"
                self.output(msg)
                self.lastHeartbeat = now

            #is it time to pull prices?
            if (now.minute in self.runMinute):
                self.data = self.updateDataframe(now)

                #check for swing/miss on each coin here
                if self.boughtIn == True:
                    for c in self.coinState:
                        if (c.timeBought != ''):
                            dt_timeBought = datetime.datetime.strptime(c.timeBought, '%Y-%m-%d %H:%M:%S.%f')
                            timeDiffBuyOrder = now - dt_timeBought
                            coinHeld = self.getHoldings(c.name)
                            if coinHeld == -1:
                                print("Got exception trying to get holdings while checking for swing/miss, cancelling.")
                                return

                            if (timeDiffBuyOrder.total_seconds() > (60 * 60 * 1) and coinHeld == 0.0):
                                cancelled = self.cancelOrder(c.lastBuyOrderID)
                                if cancelled == True:
                                    c.purchasedPrice = 0.0
                                    c.numHeld = 0.0
                                    c.numBought = 0.0
                                    c.lastBuyOrderID = ""
                                    c.timeBought = ""
                                    self.boughtIn = False

                for c in range(0, len(self.coinList)):

                    if self.checkBuyCondition(c) and self.pricesGood == True:
                        price = self.data.iloc[-1][self.coinList[c]]
                        self.buy(c, price)

                    if self.checkSellCondition(c) and self.pricesGood == True:
                        price = self.data.iloc[-1][self.coinList[c]]
                        self.sell(c, price)

                self.printState()
                self.saveState()
                time.sleep(60)

        time.sleep(30)

def main():
    m = moneyBot()
    m.runBot()


if __name__ == "__main__":
    main()

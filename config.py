CONFIG = {
    "risk_per_trade": 0.005,
    "max_daily_trades": 2,
    "min_rr": 2.0,
    "account_equity_default": 10000.0,
    "stop_atr_mult": 1.5,
    "target_atr_mult": 3.0,
    "instruments": ["XAUUSD", "EURUSD", "GBPUSD"],
    "timeframes": ["H4", "H1", "M15", "M5"],
    # H1 fits a "hold hours to a few days" holding period better than M15 —
    # the morning briefing is macro bias, not a scalping entry trigger.
    "briefing_timeframe": "H1",
}

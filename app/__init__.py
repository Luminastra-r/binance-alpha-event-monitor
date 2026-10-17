"""Binance Alpha Event Monitor — lightweight MVP (Phase 3, multi-channel).

Monitors the official Binance Wallet Telegram channel (P0), the official
Binance announcements Telegram channel (P1) and — as an adapter retained in
BLOCKED mode — the official Binance Square account for Binance Alpha airdrop
announcements. Sends an immediate notification on discovery and a second
"T-10 minutes before start" notification when a precise start time is known.
All official channels are merged into one canonical event set; notifications
fan out to every enabled channel (PushPlus WeChat, Feishu custom bot webhook).

No LLM, no Binance API key, no auto-claim, no browser automation.
"""

__version__ = "2.1.0"

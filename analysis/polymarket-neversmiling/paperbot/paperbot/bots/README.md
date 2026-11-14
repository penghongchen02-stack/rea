# Drop-in bots

Put a Python file here that defines `REGISTRY = {"my_bot": factory}` where `factory(account)` returns a
strategy object with `name` and `on_tick(market, ctx, now)` (subclass `paperbot.strategies.Base` or any of
the existing bots). On the next engine restart (the supervisor restarts it within a minute if you kill it)
the bot gets a $100 account in every universe and shows up on every dashboard.

Example:

```python
from paperbot.strategies import LastCallHold

class MyBot(LastCallHold):
    name = "my_bot"
    MAX_PRICE = 0.85

REGISTRY = {"my_bot": lambda acct: MyBot(acct)}
```

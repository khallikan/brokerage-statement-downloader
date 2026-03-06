from .schwab import SchwabBrokerage
from .fidelity import FidelityBrokerage
from .robinhood import RobinhoodBrokerage
from .etrade import ETradeBrokerage
from .vanguard import VanguardBrokerage
from .webull import WebullBrokerage
from .m1finance import M1FinanceBrokerage
from .ibkr import IBKRBrokerage

ALL_BROKERAGES = {
    "schwab": SchwabBrokerage,
    "fidelity": FidelityBrokerage,
    "robinhood": RobinhoodBrokerage,
    "etrade": ETradeBrokerage,
    "vanguard": VanguardBrokerage,
    "webull": WebullBrokerage,
    "m1finance": M1FinanceBrokerage,
    "ibkr": IBKRBrokerage,
}

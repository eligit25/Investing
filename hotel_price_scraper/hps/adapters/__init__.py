from .capture import HyattAdapter, MarriottAdapter, TajAdapter
from .reztrip import ReztripAdapter
from .synxis import SynxisAdapter

ADAPTERS = {a.source: a for a in (SynxisAdapter, ReztripAdapter, TajAdapter, MarriottAdapter, HyattAdapter)}

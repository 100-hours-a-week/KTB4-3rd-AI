# repos 묶음: 에이전트·파이프라인은 이것만 쓴다(SQL 문자열을 직접 쓰지 않는다).
from .adjust import Adjust
from .chat import Chat
from .coord import Coord
from .drills import Drills
from .incidents import Incidents
from .knowledge import Knowledge
from .metrics import Metrics
from .records import Records


class Repos:
    def __init__(self, db, vec=None, embed=None, policy_keys=()):
        self.db = db
        self.records = Records(db)
        self.metrics = Metrics(db)
        self.coord = Coord(db)
        self.incidents = Incidents(db)
        self.adjust = Adjust(db)
        self.knowledge = Knowledge(db, vec, embed, policy_keys)
        self.chat = Chat(db, vec, embed)
        self.drills = Drills(db)

from distribook.db.errors import DEADLOCK_DETECTED, constraint_name, sqlstate


class FakeDriverError(Exception):
    sqlstate = DEADLOCK_DETECTED
    constraint_name = "some_index"


class FakeWrapper(Exception):
    def __init__(self, orig: BaseException) -> None:
        super().__init__("wrapped")
        self.orig = orig


def test_sqlstate_found_through_orig_and_cause() -> None:
    inner = FakeDriverError()
    adapted = Exception("adapter")
    adapted.__cause__ = inner
    assert sqlstate(FakeWrapper(adapted)) == DEADLOCK_DETECTED
    assert constraint_name(FakeWrapper(adapted)) == "some_index"


def test_sqlstate_absent() -> None:
    assert sqlstate(ValueError("no db")) is None
    assert constraint_name(ValueError("no db")) is None


def test_cycles_terminate() -> None:
    a, b = Exception("a"), Exception("b")
    a.__cause__, b.__cause__ = b, a
    assert sqlstate(a) is None

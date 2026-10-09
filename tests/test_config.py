from reabasto.config import load_assumptions


def test_horizon_is_lead_time_plus_review():
    a = load_assumptions()
    assert a.horizon_days(perishable=True) == 4
    assert a.horizon_days(perishable=False) == 8


def test_splits_do_not_overlap():
    s = load_assumptions().split
    assert s.train_end < s.valid_start <= s.valid_end < s.sim_start <= s.sim_end


def test_service_level_matches_p90():
    assert load_assumptions().service_level == 0.90

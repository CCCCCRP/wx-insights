from worker.crawl.biz_search import pick_best_match


def test_pick_best_match_exact_nickname():
    results = [
        {"fakeid": "A", "nickname": "返朴", "alias": ""},
        {"fakeid": "B", "nickname": "返朴精选", "alias": ""},
    ]
    m = pick_best_match("返朴", results)
    assert m["fakeid"] == "A"


def test_pick_best_match_single_result():
    results = [{"fakeid": "A", "nickname": "某号", "alias": ""}]
    m = pick_best_match("某号", results)
    assert m["fakeid"] == "A"


def test_pick_best_match_ambiguous():
    results = [
        {"fakeid": "A", "nickname": "AI前沿", "alias": ""},
        {"fakeid": "B", "nickname": "AI前沿观察", "alias": ""},
    ]
    assert pick_best_match("AI", results) is None

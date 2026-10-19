"""Which laps count towards the stint averages (classify_laps)."""

from fs_telemetry.stint import LapFilter, LapMetrics, classify_laps


def lap(number, kind="timed", time=100.0, invalid=False):
    return LapMetrics(
        number=number, kind=kind, invalid=invalid, lap_time_s=time, splits_s=[], top_speed_kph=0, g_abs_avg=None,
        g_abs_p95=None, fuel_used=None, fuel_left=None, vkm={}, grip_end={}, grip_lost={}, core_temp_avg={},
        pressure_avg={}, kers_deployed_kj=None, kers_charge_end=None, full_throttle_pct=0, braking_pct=0,
        coasting_pct=0, overlap_pct=0,
    )


def run():
    # out lap, a slow first lap, three good laps, an invalid one, a cool-down lap, then the unfinished in lap
    return [lap(1, "out", 150), lap(2, time=108), lap(3, time=100.4), lap(4, time=100.0), lap(5, time=100.9),
            lap(6, time=100.2, invalid=True), lap(7, time=125), lap(8, "incomplete", 40)]


def by_number(laps):
    return {l.number: l for l in laps}


def test_out_and_unfinished_laps_are_left_out_by_default():
    laps = by_number(classify_laps(run(), LapFilter()))
    assert laps[1].excluded == "out lap" and not laps[1].in_averages and not laps[1].in_pace
    assert laps[8].excluded == "unfinished lap" and not laps[8].in_averages and not laps[8].in_pace


def test_slow_laps_are_greyed_out_by_percent_of_best():
    laps = by_number(classify_laps(run(), LapFilter()))  # best valid lap is 100.0, limit 110.0
    assert laps[7].excluded == "slow +25%" and not laps[7].in_averages
    assert laps[2].excluded is None and laps[2].in_pace  # 8% slower: still fine
    assert laps[2].in_averages


def test_the_threshold_can_be_changed():
    laps = by_number(classify_laps(run(), LapFilter(slow_pct=5)))
    assert laps[2].excluded == "slow +8%"
    assert laps[3].excluded is None


def test_invalid_laps_burn_fuel_but_have_no_pace():
    laps = by_number(classify_laps(run(), LapFilter()))
    assert laps[6].in_averages and not laps[6].in_pace and laps[6].excluded is None


def test_the_best_lap_is_the_yardstick_not_an_invalid_fast_one():
    laps = [lap(1, "out", 150), lap(2, time=90, invalid=True), lap(3, time=100), lap(4, time=105)]
    done = by_number(classify_laps(laps + [lap(5, "incomplete", 30)], LapFilter()))
    assert done[3].excluded is None and done[4].excluded is None  # judged against 100, not the invalid 90


def test_including_partial_laps_only_affects_the_averages():
    laps = by_number(classify_laps(run(), LapFilter(include_partial=True)))
    assert laps[1].in_averages and laps[8].in_averages
    assert not laps[1].in_pace and not laps[8].in_pace  # a partial lap has no lap time to compare
    assert laps[1].excluded == "out lap"  # still says what it is


def test_first_and_last_mode_drops_the_first_and_last_timed_lap():
    laps = by_number(classify_laps(run(), LapFilter(slow_filter="first_last")))
    assert laps[2].excluded == "first lap" and laps[7].excluded == "last lap"
    assert laps[3].in_pace and laps[4].in_pace and laps[5].in_pace


def test_first_and_last_mode_never_empties_a_short_run():
    short = [lap(1, "out", 150), lap(2, time=100), lap(3, time=101), lap(4, "incomplete", 30)]
    laps = classify_laps(short, LapFilter(slow_filter="first_last"))
    assert [l.in_pace for l in laps] == [False, True, True, False]


def test_filter_off_keeps_every_timed_lap():
    laps = by_number(classify_laps(run(), LapFilter(slow_filter="off")))
    assert laps[7].excluded is None and laps[7].in_pace and laps[2].in_pace


def test_the_stint_endpoint_takes_the_filter_as_query_parameters():
    from fastapi.testclient import TestClient

    from fs_telemetry.main import app

    client = TestClient(app)
    url = "/api/sessions/no-such-run/stint"
    # good parameters get as far as looking up the run; bad ones are refused before that
    assert client.get(url, params={"include_partial": "true", "slow_filter": "first_last", "slow_pct": 5}).status_code == 404
    assert client.get(url, params={"slow_pct": 0}).status_code == 422
    assert client.get(url, params={"slow_filter": "sometimes"}).status_code == 422


# --- lap flags marked by the team (session notes) win over the filter -----------


def test_a_lap_marked_ignore_is_left_out_of_everything():
    laps = by_number(classify_laps(run(), LapFilter(), {4: "ignore"}))
    assert laps[4].excluded == "marked ignore" and laps[4].flag == "ignore"
    assert not laps[4].in_pace and not laps[4].in_averages
    # lap 4 was the yardstick for "slow"; now it's lap 3 (100.4), as lap 6 is invalid
    assert laps[3].in_pace and laps[2].in_pace  # 108 is still within 10% of 100.4


def test_a_lap_marked_use_counts_even_if_slow_or_invalid():
    laps = by_number(classify_laps(run(), LapFilter(), {7: "use", 6: "use"}))
    assert laps[7].excluded is None and laps[7].in_pace and laps[7].flag == "use"
    assert laps[6].in_pace  # invalid, but the team says it was fine


def test_an_out_lap_marked_use_counts_for_averages_but_never_lap_times():
    laps = by_number(classify_laps(run(), LapFilter(), {1: "use"}))
    assert laps[1].in_averages and not laps[1].in_pace


def test_an_ignored_partial_lap_stays_out_even_when_partial_laps_are_included():
    laps = by_number(classify_laps(run(), LapFilter(include_partial=True), {8: "ignore"}))
    assert not laps[8].in_averages and laps[1].in_averages

from logscope.anomaly.detector import AnomalyDetector

BUCKET_MS = 10_000


def run(det, series):
    """Feed (bucket_index, count) pairs and return every completed bucket.

    A bucket is only *finalized* (scored) when a later event crosses into a new
    bucket, so we append one trailing event to flush the final real bucket.
    """
    completed = []

    def feed(bucket_index, count):
        base = bucket_index * BUCKET_MS
        for i in range(count):
            b = det.add(base + i)
            if b is not None:
                completed.append(b)

    for idx, count in series:
        feed(idx, count)
    # Flush: one event in a bucket far enough ahead to finalize the last one.
    last_idx = series[-1][0]
    feed(last_idx + 1, 1)
    return completed


def test_flat_series_does_not_fire():
    det = AnomalyDetector(bucket_seconds=10, window=10, k=3.0, min_count=5)
    buckets = run(det, [(i, 10) for i in range(20)])  # steady 10/bucket
    assert not any(b.is_anomaly for b in buckets)


def test_injected_spike_fires():
    det = AnomalyDetector(bucket_seconds=10, window=10, k=3.0, min_count=5)
    series = [(i, 10) for i in range(12)] + [(12, 100)]  # baseline then spike
    buckets = run(det, series)
    spikes = [b for b in buckets if b.is_anomaly]
    assert any(b.count == 100 for b in spikes)
    spike = next(b for b in spikes if b.count == 100)
    assert spike.z_score >= 3.0


def test_small_absolute_counts_are_floored():
    # A jump to 3 is statistically large against a ~0 baseline but absolutely
    # tiny; the min_count floor must suppress it.
    det = AnomalyDetector(bucket_seconds=10, window=10, k=3.0, min_count=5)
    series = [(i, 0 if i % 2 else 1) for i in range(10)] + [(10, 3)]
    buckets = run(det, series)
    assert not any(b.is_anomaly for b in buckets)


def test_incremental_stats_match_naive():
    import statistics

    det = AnomalyDetector(bucket_seconds=10, window=5, k=3.0)
    counts = [4, 7, 2, 9, 5, 6, 3]
    run(det, list(enumerate(counts)))

    window = det.recent_counts()
    mean, stddev = det._stats()
    assert abs(mean - statistics.fmean(window)) < 1e-9
    expected_std = statistics.pstdev(window) if len(window) > 1 else 0.0
    assert abs(stddev - expected_std) < 1e-9

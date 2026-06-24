from logscope.metrics import Counter, Gauge, Histogram, Metrics, RateMeter


def test_counter():
    c = Counter()
    c.inc()
    c.inc(5)
    assert c.value == 6


def test_gauge():
    g = Gauge()
    g.set(3.5)
    assert g.value == 3.5
    g.set(1)
    assert g.value == 1.0


def test_histogram_percentiles():
    h = Histogram()
    for v in range(1, 101):  # 1..100
        h.observe(v)
    assert h.count == 100
    assert h.percentile(50) == 50.5    # interpolated median
    assert h.percentile(95) == 95.05
    assert h.percentile(0) == 1
    assert h.percentile(100) == 100


def test_histogram_empty_is_zero():
    assert Histogram().percentile(95) == 0.0


def test_rate_meter_sliding_window():
    rm = RateMeter(window_s=5)
    # 10 events at t=0, 10 at t=1
    for _ in range(10):
        rm.mark(now=0.0)
    for _ in range(10):
        rm.mark(now=1.0)
    assert rm.rate(now=1.0) == 20 / 5  # 20 events over a 5s window = 4.0/s
    # By t=10 the old buckets have aged out of the window.
    assert rm.rate(now=10.0) == 0.0


def test_metrics_status_line():
    m = Metrics()
    m.record_event(lag_ms=12, now=0.0)
    m.queue_depth.set(3)
    m.cluster_count.set(7)
    line = m.status_line(ai_hit_rate=0.5)
    assert "/s" in line                # ingest rate present
    assert "lag 12ms" in line
    assert "clusters 7" in line
    assert "total 1" in line
    assert "ai-cache 50%" in line

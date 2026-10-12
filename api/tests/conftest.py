# In-memory OpenTelemetry for the tests: real trace ids for traceId, and a metric reader for the problems counter.
# Set before main.py is imported; the API's tracer and meter bind to these through the global proxies.
from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider

METRICS = InMemoryMetricReader()
trace.set_tracer_provider(TracerProvider())
metrics.set_meter_provider(MeterProvider(metric_readers=[METRICS]))


def problem_counts() -> dict[tuple[str, str], int]:
    """coverdesigner.problems so far, by (type, reason)."""
    counts: dict[tuple[str, str], int] = {}
    data = METRICS.get_metrics_data()
    for rm in data.resource_metrics if data else []:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                if m.name == "coverdesigner.problems":
                    for point in m.data.data_points:
                        counts[(point.attributes["type"], point.attributes["reason"])] = point.value
    return counts

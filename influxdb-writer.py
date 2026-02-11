#!/home/ichardart/dev/infrastructure/metrics/assumption-validation/venv/bin/python3
"""
InfluxDB Writer for Assumption Validation Metrics
Writes metrics to InfluxDB time-series database for Grafana visualization
"""

import sys
import json
from datetime import datetime
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

# InfluxDB Configuration
INFLUXDB_URL = "http://localhost:8086"
INFLUXDB_TOKEN = "assumption-metrics-token-secure-2026"
INFLUXDB_ORG = "ai-governance"
INFLUXDB_BUCKET = "assumption-validation"

def write_metrics(metrics_json):
    """Write metrics to InfluxDB"""
    try:
        # Parse metrics
        metrics = json.loads(metrics_json)

        # Create InfluxDB client
        client = InfluxDBClient(
            url=INFLUXDB_URL,
            token=INFLUXDB_TOKEN,
            org=INFLUXDB_ORG
        )

        write_api = client.write_api(write_options=SYNCHRONOUS)

        # Parse timestamp
        timestamp = datetime.fromisoformat(metrics['timestamp'].replace('Z', '+00:00'))

        # Calculate override rate
        override_count = metrics['override_count']
        test_bypass_count = metrics['test_bypass_count']
        total_queries = override_count + test_bypass_count

        if total_queries > 0:
            override_rate = (override_count / total_queries) * 100
        else:
            override_rate = 0.0

        # Get validation rate
        validation_rate = float(metrics['assumptions']['validation_rate_pct'])

        # Create data point
        point = Point("assumption_metrics") \
            .tag("source", "monitoring_script") \
            .field("override_count", override_count) \
            .field("test_bypass_count", test_bypass_count) \
            .field("override_rate", override_rate) \
            .field("validation_rate", validation_rate) \
            .field("total_assumptions", metrics['assumptions']['total']) \
            .field("validated_count", metrics['assumptions']['validated']) \
            .field("invalid_count", metrics['assumptions']['invalid']) \
            .field("unvalidated_count", metrics['assumptions']['unvalidated']) \
            .time(timestamp)

        # Write to InfluxDB
        write_api.write(bucket=INFLUXDB_BUCKET, org=INFLUXDB_ORG, record=point)

        print(f"✓ Wrote metrics to InfluxDB: override_rate={override_rate:.1f}%, validation_rate={validation_rate:.1f}%", file=sys.stderr)

        client.close()
        return True

    except Exception as e:
        print(f"✗ Failed to write to InfluxDB: {e}", file=sys.stderr)
        return False

if __name__ == "__main__":
    if len(sys.argv) > 1:
        # Read from argument
        metrics_json = sys.argv[1]
    else:
        # Read from stdin
        metrics_json = sys.stdin.read()

    success = write_metrics(metrics_json)
    sys.exit(0 if success else 1)

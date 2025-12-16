import json
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import RunReportRequest


def get_sessions_for_property(property_id: str,
                              start_date: str = "2025-12-01",
                              end_date: str = "today") -> int:
    """
    Returns the total number of sessions for a given GA4 property id
    in the specified date range.

    :param property_id: GA4 property id as string, e.g. "182279779"
    :param start_date: start of date range, e.g. "2025-12-01" or "7daysAgo"
    :param end_date: end of date range, e.g. "today" or "2025-12-15"
    """

    client = BetaAnalyticsDataClient()

    request = RunReportRequest(
        property=f"properties/{property_id}",
        metrics=[{"name": "sessions"}],
        date_ranges=[{"start_date": start_date, "end_date": end_date}],
    )

    response = client.run_report(request)

    # When no dimension is requested, GA4 returns a single row with the total.
    if not response.rows:
        return 0

    total_sessions = int(response.rows[0].metric_values[0].value)
    return total_sessions


def main():
    # 👉 Put here the property id you want to test
    property_id = "182279779"  # GA4_web+contweb+shop+app

    # Example: sessions in the last 30 days
    start_date = "30daysAgo"
    end_date = "today"

    total_sessions = get_sessions_for_property(property_id, start_date, end_date)

    result = {
        "propertyId": property_id,
        "dateRange": {
            "start_date": start_date,
            "end_date": end_date,
        },
        "metrics": {
            "sessions": total_sessions,
        },
    }

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

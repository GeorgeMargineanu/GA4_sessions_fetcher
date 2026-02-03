import json
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import RunReportRequest, Dimension, Metric, DateRange


def ga4_campaign_breakdown(property_id: str,
                           start_date: str = "30daysAgo",
                           end_date: str = "today",
                           limit: int = 1000):
    client = BetaAnalyticsDataClient()

    request = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=[
            Dimension(name="sessionSource"),
            Dimension(name="sessionMedium"),
            Dimension(name="sessionCampaignName"),
        ],
        metrics=[
            Metric(name="sessions"),
            Metric(name="totalUsers"),
            Metric(name="conversions"),
        ],
        date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
        limit=limit,
    )

    response = client.run_report(request)

    rows = []
    for r in response.rows:
        rows.append({
            "source": r.dimension_values[0].value,
            "medium": r.dimension_values[1].value,
            "campaign": r.dimension_values[2].value,
            "sessions": int(r.metric_values[0].value),
            "users": int(r.metric_values[1].value),
            "conversions": float(r.metric_values[2].value),
        })

    return rows


def main():
    property_id = "182279779"
    data = ga4_campaign_breakdown(property_id, "30daysAgo", "today", limit=5000)
    print(json.dumps(data[:20], indent=2, ensure_ascii=False))  # preview first 20


if __name__ == "__main__":
    main()

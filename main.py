import json
from google.analytics.admin import AnalyticsAdminServiceClient
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import RunReportRequest


# ========== FUNCTION 1: LIST ACCOUNTS + PROPERTIES ==========

def ga4_list_accounts(request):
    """
    HTTP Cloud Function (GET) that returns all GA4 accounts and their properties
    available to the credentials used (service account in Cloud Functions).

    Response JSON:
    {
      "accounts": [
        {
          "account": "accounts/52835573",
          "displayName": "App Regina Maria",
          "properties": [
            {
              "property": "properties/182279779",
              "propertyId": "182279779",
              "displayName": "GA4_web+contweb+shop+app"
            },
            ...
          ]
        },
        ...
      ]
    }
    """
    client = AnalyticsAdminServiceClient()
    accounts_data = []

    for summary in client.list_account_summaries():
        account_entry = {
            "account": summary.account,
            "displayName": summary.display_name,
            "properties": [],
        }

        for prop_summary in summary.property_summaries:
            prop_id = prop_summary.property.split("/")[-1]
            account_entry["properties"].append(
                {
                    "property": prop_summary.property,
                    "propertyId": prop_id,
                    "displayName": prop_summary.display_name,
                }
            )

        accounts_data.append(account_entry)

    # Cloud Functions can return (body, status, headers) OR just a dict
    # For CORS / PPT calls, you might want to add headers later.
    return (
        json.dumps({"accounts": accounts_data}),
        200,
        {"Content-Type": "application/json"},
    )


# ========== FUNCTION 2: SESSIONS FOR A GIVEN PROPERTY ==========

def ga4_property_sessions(request):
    """
    HTTP Cloud Function (GET/POST) that returns sessions for a given property id.

    Query parameters (or JSON body):
      - property_id (required): e.g. "182279779"
      - start_date (optional): e.g. "30daysAgo" or "2025-12-01"
      - end_date   (optional): e.g. "today" or "2025-12-15"

    Example response:
    {
      "propertyId": "182279779",
      "dateRange": {
        "start_date": "30daysAgo",
        "end_date": "today"
      },
      "metrics": {
        "sessions": 5106379
      }
    }
    """

    # --- 1. Read parameters (from query or JSON body) ---

    # Query params (GET)
    property_id = request.args.get("property_id") if request.args else None
    start_date = request.args.get("start_date") if request.args else None
    end_date = request.args.get("end_date") if request.args else None

    # JSON body (POST) – optional support
    if not property_id:
        try:
            data = request.get_json(silent=True) or {}
        except Exception:
            data = {}
        property_id = property_id or data.get("property_id")
        start_date = start_date or data.get("start_date")
        end_date = end_date or data.get("end_date")

    if not property_id:
        return (
            json.dumps({"error": "Missing required parameter: property_id"}),
            400,
            {"Content-Type": "application/json"},
        )

    # Defaults if not provided
    if not start_date:
        start_date = "30daysAgo"
    if not end_date:
        end_date = "today"

    # --- 2. Call GA4 Data API ---

    client = BetaAnalyticsDataClient()

    request_body = RunReportRequest(
        property=f"properties/{property_id}",
        metrics=[{"name": "sessions"}],
        date_ranges=[{"start_date": start_date, "end_date": end_date}],
    )

    response = client.run_report(request_body)

    if not response.rows:
        total_sessions = 0
    else:
        total_sessions = int(response.rows[0].metric_values[0].value)

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

    return (
        json.dumps(result),
        200,
        {"Content-Type": "application/json"},
    )

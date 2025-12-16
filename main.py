import json
from typing import Tuple
import traceback

from google.analytics.admin import AnalyticsAdminServiceClient
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import RunReportRequest
from google.oauth2.credentials import Credentials


# ----------------------------
# CORS helpers
# ----------------------------
def _cors_headers():
    return {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Authorization, Content-Type",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    }



def _handle_preflight(request):
    # Browsers send an OPTIONS preflight for requests with Authorization header.
    if request.method == "OPTIONS":
        return ("", 204, _cors_headers())
    return None


# ----------------------------
# Auth helper
# ----------------------------
def _get_user_credentials_from_request(request) -> Tuple[Credentials, str]:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise ValueError("Missing/invalid Authorization header. Use: Bearer <ACCESS_TOKEN>")

    token = auth_header.split(" ", 1)[1].strip()
    if not token:
        raise ValueError("Empty bearer token.")

    # Important: pass scopes hint (helps some libs / debugging)
    creds = Credentials(
        token=token,
        scopes=["https://www.googleapis.com/auth/analytics.readonly"],
    )
    return creds, token


# ============================
# FUNCTION 1: LIST ACCOUNTS + PROPERTIES
# ============================
def ga4_list_accounts_oauth(request):
    # CORS preflight
    if request.method == "OPTIONS":
        return ("", 204, _cors_headers())

    try:
        user_creds, _ = _get_user_credentials_from_request(request)

        client = AnalyticsAdminServiceClient(credentials=user_creds)
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

        return (json.dumps({"accounts": accounts_data}), 200, _cors_headers())

    except Exception as e:
        # Return full debug information to the caller (temporary, for testing)
        err = {
            "error": str(e),
            "traceback": traceback.format_exc(),
        }
        return (json.dumps(err), 500, _cors_headers())


# ============================
# FUNCTION 2: SESSIONS FOR A GIVEN PROPERTY
# ============================
def ga4_property_sessions_oauth(request):
    """
    HTTP Cloud Function that returns sessions for a given property id.

    Required:
      Header:
        Authorization: Bearer <ACCESS_TOKEN>  (analytics.readonly)
      Query/body:
        property_id (required): e.g. "182279779"
        start_date (optional): e.g. "30daysAgo" or "2025-12-01"
        end_date   (optional): e.g. "today" or "2025-12-15"
    """
    pre = _handle_preflight(request)
    if pre:
        return pre

    try:
        user_creds, _ = _get_user_credentials_from_request(request)
    except ValueError as e:
        return (json.dumps({"error": str(e)}), 401, _cors_headers())

    # --- parameters: query string or JSON body ---
    property_id = request.args.get("property_id") if request.args else None
    start_date = request.args.get("start_date") if request.args else None
    end_date = request.args.get("end_date") if request.args else None

    if not property_id:
        data = request.get_json(silent=True) or {}
        property_id = property_id or data.get("property_id")
        start_date = start_date or data.get("start_date")
        end_date = end_date or data.get("end_date")

    if not property_id:
        return (json.dumps({"error": "Missing required parameter: property_id"}), 400, _cors_headers())

    start_date = start_date or "30daysAgo"
    end_date = end_date or "today"

    # --- GA4 Data API call ---
    data_client = BetaAnalyticsDataClient(credentials=user_creds)

    request_body = RunReportRequest(
        property=f"properties/{property_id}",
        metrics=[{"name": "sessions"}],
        date_ranges=[{"start_date": start_date, "end_date": end_date}],
    )

    response = data_client.run_report(request_body)

    total_sessions = 0
    if response.rows:
        total_sessions = int(response.rows[0].metric_values[0].value)

    result = {
        "propertyId": property_id,
        "dateRange": {"start_date": start_date, "end_date": end_date},
        "metrics": {"sessions": total_sessions},
    }

    return (json.dumps(result), 200, _cors_headers())
